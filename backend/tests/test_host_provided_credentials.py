"""Credentials the host supplies, and the town's right to overrule them.

A hosted town often has no account of its own with a map vendor or a
translation API, so its host can push working credentials in and the setup page
stops dead-ending on a box nobody can fill. That trade is only acceptable while
three properties hold, and each of them fails silently, which is why they are
pinned here rather than left to review:

  * A credential the TOWN entered is never overwritten. The failure mode is a
    clerk pasting a working key, a machine reverting it minutes later, and
    nobody able to say why the connector broke.
  * The push is a full replace, so a key the host stopped sharing is actually
    deleted -- vault and encrypted database copy both, because `get_secret`
    falls back to the second when the first does not answer. A half-revoked
    credential is a live credential nobody thinks is live.
  * The allowlist cannot reach the platform's own keys. `SECRETS_PROVIDER` or
    `AWS_KMS_KEY_ID` arriving through this door would repoint where the town's
    credentials live, from an endpoint whose entire promise is that it writes
    provider credentials only.

And the flat rule underneath all three: no key VALUE is returned, logged or
audited. The response and the audit entry are key names and counts.
"""

import asyncio
import json

import pytest

# A submodule, not the top-level package. `pytest.importorskip("fastapi")` can
# succeed against a partial install and then explode on the first real import;
# see the header of test_migrate.py for the version of this that hid a whole
# file from CI for months.
pytest.importorskip("fastapi.routing")
pytest.importorskip("sqlalchemy.orm")

from fastapi import HTTPException

from app.api import provisioning, system
from app.services import host_secrets


def _run(coro):
    return asyncio.run(coro)


# Captured before any fixture replaces it, so the end-to-end test can put the
# genuine write path back and exercise it.
_REAL_PERSIST_SECRET = system._persist_secret


# ---------------------------------------------------------------------------
# Doubles. The suite has no database, so the settings row and the secret store
# are stood in for -- but `apply` itself is the real thing under test, and so is
# every ownership decision it makes.
# ---------------------------------------------------------------------------

class FakeRow:
    """The singleton settings row, as far as this feature is concerned."""

    def __init__(self, keys=None):
        self.id = 1
        self.host_provided_keys = keys


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.flushes = 0

    async def commit(self):
        self.commits += 1

    async def flush(self):
        # `_store` flushes so a claim is durable inside the transaction before
        # the caller goes off to do a multi-second vault write.
        self.flushes += 1


class Store:
    """A secret store that records writes and deletions by key."""

    def __init__(self, existing=None):
        self.values = dict(existing or {})
        self.writes = []
        self.deleted = []
        self.host_writes = []
        # Keys whose vault delete does not take. `delete_secret` returns True
        # when EITHER half of the delete worked, so this is the shape of a real
        # failure: the database copy goes, the vault entry stays, and the
        # credential is still live.
        self.undeletable = set()
        # Called on every persist, so a test can act in the middle of a batch.
        self.on_persist = None

        # Keys whose write lands in the encrypted database copy but which the
        # external store will not take -- what `_persist_secret` reports by
        # returning False.
        self.db_only = set()

    async def persist(self, db, key_name, value, *, from_host=False):
        if self.on_persist:
            await self.on_persist(key_name)
        self.values[key_name] = value
        self.writes.append(key_name)
        if from_host:
            self.host_writes.append(key_name)
        return key_name not in self.db_only

    async def configured(self, _db, key_name):
        return bool((self.values.get(key_name) or "").strip())

    async def revoke(self, db, key_name):
        """Stands in for `_revoke`: delete, then answer whether it is gone."""
        self.deleted.append(key_name)
        if key_name in self.undeletable:
            return False
        self.values.pop(key_name, None)
        return True


@pytest.fixture
def town(monkeypatch):
    """A town whose settings row and secret store we can inspect."""
    row = FakeRow()
    store = Store()
    db = FakeDB()

    async def get_settings(_db, *, create=False, fresh=False):
        # `fresh` is the real helper's populate_existing re-read. There is one
        # row object here and every read already sees the latest state, so the
        # flag only has to be accepted -- a test that needs to simulate a
        # concurrent write mutates `row` directly.
        return row

    monkeypatch.setattr("app.services.system_settings.get_settings", get_settings)
    monkeypatch.setattr(system, "_persist_secret", store.persist)
    monkeypatch.setattr(host_secrets, "_is_configured", store.configured)
    monkeypatch.setattr(host_secrets, "_revoke", store.revoke)

    class Town:
        pass

    t = Town()
    t.row, t.store, t.db = row, store, db
    return t


A_KEY = "GOOGLE_MAPS_API_KEY"
ANOTHER = "AZURE_TRANSLATOR_KEY"
VALUE = "AIza-not-a-real-key-0001"


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

def test_the_allowlist_is_derived_from_the_catalogs():
    """Not hand-listed. A credential field removed from a catalog stops being
    pushable at the same moment it stops being read, and a provider added later
    is covered without anyone remembering to come back here."""
    keys = host_secrets.shareable_keys()
    assert A_KEY in keys
    assert "TWILIO_AUTH_TOKEN" in keys
    assert "AUTH0_CLIENT_SECRET" in keys
    # No provider card declares it, but it is still a per-deployment credential
    # a host plausibly owns.
    assert "SENTRY_DSN" in keys


@pytest.mark.parametrize("key", [
    # The storage arrangement. Pushing these would repoint where every other
    # credential lives, which is not a provider credential by any reading.
    "SECRETS_PROVIDER", "KMS_PROVIDER", "AWS_KMS_KEY_ID", "GCP_SERVICE_ACCOUNT_JSON",
    # The host's own backups, not something to copy into a town's vault.
    "BACKUP_ENCRYPTION_KEY", "BACKUP_S3_BUCKET",
    # The instance's identity and database.
    "SECRET_KEY", "DATABASE_URL", "PROVISIONING_TOKEN",
])
def test_the_platform_keys_are_not_shareable(key):
    assert key not in host_secrets.shareable_keys(), key


@pytest.mark.parametrize("key", sorted(host_secrets.NON_CREDENTIAL_KEYS))
def test_the_behaviour_toggles_are_not_shareable(key):
    """They sit in a provider catalog's credential_fields because the card has
    to show them, but they are not credentials and a host has no standing to
    set them.

    REDACT_FACES and REDACT_PLATES are the ones that matter: they decide
    whether faces and number plates in residents' photographs are blurred
    before anybody sees them. A host that could push REDACT_FACES=false would
    be turning off the blurring of residents' faces from its own console,
    silently, on whatever schedule it pushes on. That is the town's decision
    and there is no version of "the host has an account for it" that applies.
    """
    assert key not in host_secrets.shareable_keys(), key


def test_the_non_secret_companion_fields_stay_shareable():
    """The deny-list is a subtraction, not a switch to "secret fields only".

    A key arrives with the things it needs to be used: Auth0's domain, the
    endpoint an Azure key is scoped to, the project a Vertex credential names.
    Filtering on `secret: true` would have removed every one of them, and the
    town would have been handed credentials that cannot connect to anything --
    which is worse than not being handed them, because the card reports
    configured."""
    keys = host_secrets.shareable_keys()
    for companion in ("AUTH0_DOMAIN", "AZURE_TRANSLATOR_ENDPOINT", "VERTEX_AI_PROJECT"):
        assert companion in keys, companion


@pytest.mark.parametrize("key", [
    "SECRETS_PROVIDER", "BACKUP_ENCRYPTION_KEY", "NOT_A_REAL_SETTING", "",
])
def test_a_key_off_the_allowlist_is_refused_not_written(town, key):
    """Refused in the body rather than by a 4xx: one bad key in a batch must
    not throw away the good ones, and the host has to be told which key."""
    result = _run(host_secrets.apply(town.db, {key: "x", A_KEY: VALUE}))

    assert result["accepted"] == [A_KEY]
    assert town.store.writes == [A_KEY]
    if key:
        assert result["refused"] == [key]


# ---------------------------------------------------------------------------
# Accept, then revoke -- the full-replace round trip
# ---------------------------------------------------------------------------

def test_a_pushed_credential_is_stored_and_recorded_as_the_hosts(town):
    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result == {"accepted": [A_KEY], "refused": [], "revoked": [], "failed": [],
                      "db_only": []}
    assert town.store.values[A_KEY] == VALUE
    assert town.row.host_provided_keys == [A_KEY]
    # Written through the host door, so the write does not take ownership away
    # from the host it just gave it to.
    assert town.store.host_writes == [A_KEY]


def test_pushing_the_same_value_again_changes_nothing(town):
    _run(host_secrets.apply(town.db, {A_KEY: VALUE}))
    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result["accepted"] == [A_KEY]
    assert result["revoked"] == []
    assert town.row.host_provided_keys == [A_KEY]


def test_a_key_left_out_of_the_next_push_is_withdrawn(town):
    """Full replace. The host's console has no other way to say "stop sharing
    this", so absence has to mean it -- and it has to mean deleted, not merely
    unlisted, or the credential stays live in the town's vault."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result["revoked"] == [ANOTHER]
    assert town.store.deleted == [ANOTHER]
    assert ANOTHER not in town.store.values
    assert town.row.host_provided_keys == [A_KEY]


def test_an_empty_push_withdraws_everything(town):
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    result = _run(host_secrets.apply(town.db, {}))

    assert result["revoked"] == [ANOTHER, A_KEY]
    assert town.row.host_provided_keys == []


# ---------------------------------------------------------------------------
# The town wins
# ---------------------------------------------------------------------------

def test_a_credential_the_town_configured_is_refused(town):
    """The town's own key wins. Not a 4xx and not a partial write: the host is
    told which key it may not have, and the key keeps the value a clerk
    entered."""
    town.store.values[A_KEY] = "the-towns-own-key"

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result == {"accepted": [], "refused": [A_KEY], "revoked": [], "failed": [],
                      "db_only": []}
    assert town.store.values[A_KEY] == "the-towns-own-key"
    assert town.store.writes == []
    assert town.row.host_provided_keys == []


def test_a_town_save_flips_ownership_and_the_next_push_is_refused(town):
    """The whole override contract, end to end: the host supplies a key, a
    clerk types their own value over it, and the host can no longer touch it.

    The flip happens in `_persist_secret`, which every town-admin write goes
    through, so this is the property that makes it safe for the host to keep
    pushing on a schedule."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE}))
    assert town.row.host_provided_keys == [A_KEY]

    # What a town-admin save does: the value lands, and ownership moves.
    town.store.values[A_KEY] = "typed-by-the-clerk"
    _run(host_secrets.forget(town.db, A_KEY))
    assert town.row.host_provided_keys == []

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))
    assert result["refused"] == [A_KEY]
    assert town.store.values[A_KEY] == "typed-by-the-clerk"


def test_a_save_during_the_push_is_not_undone_when_it_finishes(town):
    """The race. A push is a vault write per key and takes seconds; a clerk who
    saves their own credential inside that window calls `forget`, which takes
    the key off the host.

    The ownership record used to be written back at the end from a snapshot
    taken before the loop started, which put the key straight back -- and the
    next scheduled push then overwrote the value the clerk had just typed, with
    nothing anywhere saying why. So the record is re-read fresh and only the
    keys this push actually decided about are changed."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    assert town.row.host_provided_keys == [ANOTHER, A_KEY]

    async def the_clerk_saves_their_own(key_name):
        if key_name == A_KEY:
            town.store.values[ANOTHER] = "typed-by-the-clerk"
            await host_secrets.forget(town.db, ANOTHER)

    town.store.on_persist = the_clerk_saves_their_own

    # ANOTHER is in the push but blank, so it is refused -- the case where the
    # old code kept it purely because the stale snapshot said it was the host's.
    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "   "}))

    assert result["refused"] == [ANOTHER]
    assert town.row.host_provided_keys == [A_KEY]
    assert town.store.values[ANOTHER] == "typed-by-the-clerk"


def test_ownership_is_not_taken_for_a_key_nobody_shared(town):
    assert _run(host_secrets.forget(town.db, A_KEY)) is False
    assert host_secrets._normalise(town.row.host_provided_keys) == []


def test_a_configured_but_unreadable_credential_is_refused(monkeypatch):
    """The one the fake store cannot show you, so it is tested against the real
    `_is_configured`.

    `get_secret` does not raise when the vault is unreachable. Every backend
    falls through to the encrypted database copy and returns None -- and the
    vault sweep has already scrubbed that copy to NULL for any key that
    migrated. So a town credential sitting in a Key Vault the instance cannot
    currently reach reads back as None, exactly like a key nobody ever set.

    The `system_secrets` row still says `is_configured = True`. That is the
    town's own record that a clerk saved something, and it is why the check
    asks the row as well as the store: reading the value is not the only way to
    know a credential is there, and overwriting one because we could not read
    it is the failure this endpoint exists to avoid."""
    class Result:
        """The system_secrets row: saved, and scrubbed by the vault sweep."""

        def scalar_one_or_none(self):
            return True

    class DB:
        def __init__(self):
            self.commits = 0

        async def execute(self, _stmt):
            return Result()

        async def commit(self):
            self.commits += 1

    row = FakeRow()
    store = Store()

    async def get_settings(_db, *, create=False, fresh=False):
        # `fresh` is the real helper's populate_existing re-read. There is one
        # row object here and every read already sees the latest state, so the
        # flag only has to be accepted -- a test that needs to simulate a
        # concurrent write mutates `row` directly.
        return row

    async def unreadable(_key):
        # What an unreachable vault actually does: None, and no exception.
        return None

    monkeypatch.setattr("app.services.system_settings.get_settings", get_settings)
    monkeypatch.setattr("app.services.secret_manager.get_secret", unreadable)
    monkeypatch.setattr(system, "_persist_secret", store.persist)

    result = _run(host_secrets.apply(DB(), {A_KEY: VALUE}))

    assert result["refused"] == [A_KEY]
    assert result["accepted"] == []
    assert store.writes == []
    assert host_secrets._normalise(row.host_provided_keys) == []


def test_a_store_that_cannot_be_asked_at_all_refuses(town, monkeypatch):
    """The pessimistic answer everywhere else on this page is "not configured",
    and here that would mean writing over a town credential nobody could read.
    So this one refuses instead of guessing."""
    async def boom(_db, _key):
        raise RuntimeError("settings row unreadable")

    monkeypatch.setattr(host_secrets, "_is_configured", boom)

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result["refused"] == [A_KEY]
    assert town.store.writes == []


def test_a_blank_value_is_refused_rather_than_clearing_a_credential(town):
    """Revocation has an unambiguous spelling -- leave the key out. A blank
    value is a mistake at the other end, and writing it would clear a live
    credential."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE}))
    result = _run(host_secrets.apply(town.db, {A_KEY: "   "}))

    assert result["refused"] == [A_KEY]
    assert result["revoked"] == []
    assert town.store.values[A_KEY] == VALUE


# ---------------------------------------------------------------------------
# A malformed push must not half-land
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [1234, None, {"nested": "object"}, ["a"], True])
def test_a_value_that_is_not_a_string_is_refused_not_raised(town, value):
    """It used to be an AttributeError on `.strip()`, which is a 500 -- and a
    500 halfway through a batch, after earlier keys had already been written
    and committed by `_persist_secret`. Refused per key, like every other bad
    input here, so one malformed entry costs that entry and the rest of the
    push still lands."""
    result = _run(host_secrets.apply(town.db, {ANOTHER: value, A_KEY: VALUE}))

    assert result["refused"] == [ANOTHER]
    assert result["accepted"] == [A_KEY]
    assert town.store.writes == [A_KEY]
    assert town.row.host_provided_keys == [A_KEY]


def test_the_request_model_rejects_a_non_string_value():
    """The first line of the same defence, at the door. `secrets: dict` took
    anything at all; `Dict[str, str]` is what makes the loop's check a second
    line rather than the only one."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        provisioning.HostSecretsRequest(secrets={A_KEY: {"not": "a string"}})


def test_a_write_that_fails_midway_leaves_no_unowned_credential(town):
    """The orphan. `_persist_secret` commits each key as it goes, and ownership
    used to be written once at the end -- so a failure partway through left
    keys sitting in the town's vault with nothing recording that the host put
    them there.

    That is not a cosmetic gap. The next push sees a key it does not own with a
    value against it, refuses it, and the host can then neither replace nor
    withdraw the credential it just installed: permanently stuck, and the more
    keys in the batch the likelier it got. So ownership is claimed in the same
    transaction as the write."""
    seen = []

    async def blow_up_on_the_second(key_name):
        seen.append(key_name)
        if len(seen) == 2:
            raise RuntimeError("vault write failed")

    town.store.on_persist = blow_up_on_the_second

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))

    # ANOTHER sorts first, so A_KEY is the one that blew up.
    assert result["accepted"] == [ANOTHER]
    assert result["refused"] == [A_KEY]
    # Everything written is owned, and nothing unwritten is.
    assert town.row.host_provided_keys == [ANOTHER]
    assert set(town.store.values) == {ANOTHER}


def test_every_committed_key_is_owned_at_the_moment_it_is_written(town):
    """The property underneath the test above, checked where it matters: at the
    instant `_persist_secret` commits a value, the ownership record already
    names that key. Anything weaker is a window in which a crash strands a
    credential."""
    owned_at_write = {}

    async def look(key_name):
        owned_at_write[key_name] = list(
            host_secrets._normalise(town.row.host_provided_keys)
        )

    town.store.on_persist = look

    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))

    assert A_KEY in owned_at_write[A_KEY]
    assert ANOTHER in owned_at_write[ANOTHER]


def test_a_failed_rewrite_does_not_strand_a_key_the_host_already_owned(town):
    """The permanent strand, and the reason the release of a claim is gated on
    whether the key was new.

    Releasing the claim is right for a key the host has never written: nothing
    of the host's is behind it, so the row goes back to how it was. It is
    catastrophic for a RE-push. The host's previous value is still live in the
    town's vault, and dropping the ownership record leaves a key that is not
    owned but does have a value -- which is exactly the shape the guard above
    refuses. Every push after that transient blip is refused, forever, and the
    host can neither replace the credential nor withdraw it. One failed vault
    write, and the key is unmanageable for the life of the deployment.

    Three pushes, which is the smallest sequence that shows it: accept, fail,
    and then the recovery that used to be impossible."""
    first = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))
    assert first["accepted"] == [A_KEY]
    assert town.row.host_provided_keys == [A_KEY]

    async def blow_up(_key_name):
        raise RuntimeError("vault write failed")

    town.store.on_persist = blow_up
    second = _run(host_secrets.apply(town.db, {A_KEY: "AIza-not-a-real-key-0002"}))

    # Refused, because the write did not happen -- but the key the host already
    # owned is still the host's, because the value behind it is still the
    # host's.
    assert second["refused"] == [A_KEY]
    assert second["accepted"] == []
    assert town.row.host_provided_keys == [A_KEY]
    assert town.store.values[A_KEY] == VALUE

    # And that is what makes the blip recoverable: the vault comes back, the
    # host re-pushes, and the key is replaced rather than refused.
    town.store.on_persist = None
    third = _run(host_secrets.apply(town.db, {A_KEY: "AIza-not-a-real-key-0003"}))

    assert third["accepted"] == [A_KEY]
    assert third["refused"] == []
    assert town.store.values[A_KEY] == "AIza-not-a-real-key-0003"

    # The withdrawal the host could no longer reach still works too.
    fourth = _run(host_secrets.apply(town.db, {}))
    assert fourth["revoked"] == [A_KEY]
    assert A_KEY not in town.store.values


def test_a_key_claimed_by_the_town_mid_push_is_not_handed_back(town):
    """`apply` takes seconds -- a vault write per key -- and a clerk who saves
    their own credential during that window calls `forget`, which takes the key
    off the host.

    The final ownership write used to re-add the whole accepted batch, which
    put the clerk's key straight back on the host's books and let the next push
    overwrite the value they had just typed. Every accepted key already claimed
    itself at the moment it was written, so the last write has nothing to add
    and only subtracts."""
    async def the_clerk_saves_their_own(key_name):
        if key_name == A_KEY:
            # Mid-write, after the host has claimed the key: what `forget` does
            # to the row from the clerk's own session.
            town.row.host_provided_keys = [
                k for k in host_secrets._normalise(town.row.host_provided_keys)
                if k != A_KEY
            ]

    town.store.on_persist = the_clerk_saves_their_own

    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))

    # The clerk's save is the last word on that key.
    assert A_KEY not in host_secrets._normalise(town.row.host_provided_keys)
    # And the key the host really did provide is untouched by the correction.
    assert ANOTHER in host_secrets._normalise(town.row.host_provided_keys)


def test_a_key_the_town_claims_before_its_turn_is_refused_not_overwritten(town):
    """The other ordering, and the one a single pre-loop snapshot cannot see.

    `apply` commits key by key, so the loop spans several committed
    transactions. A clerk who saves their own credential during the FIRST key's
    vault write has already committed `forget` by the time the loop reaches the
    second -- and against a snapshot taken before the push started, the second
    key still looks like the host's. The ownership guard is skipped on that
    basis, and the host's value is written over a credential a clerk typed
    seconds earlier.

    ANOTHER sorts first, so the clerk's save lands during ANOTHER's write and
    A_KEY's turn comes afterwards. That is the reviewer's reproduction, and it
    is why ownership is re-read for every key rather than once."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    assert town.row.host_provided_keys == [ANOTHER, A_KEY]

    async def the_clerk_saves_their_own(key_name):
        if key_name != ANOTHER:
            return
        # Exactly what a town-side save does, from the clerk's own session:
        # the value goes in, and `forget` takes the key off the host.
        town.store.values[A_KEY] = "the-towns-own-key"
        town.row.host_provided_keys = [
            k for k in host_secrets._normalise(town.row.host_provided_keys)
            if k != A_KEY
        ]

    town.store.on_persist = the_clerk_saves_their_own

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))

    # The town's own credential wins, however the timing falls.
    assert result["refused"] == [A_KEY]
    assert town.store.values[A_KEY] == "the-towns-own-key"
    assert A_KEY not in host_secrets._normalise(town.row.host_provided_keys)
    # And it is not revoked either: it is the town's now, not a withdrawal.
    assert result["revoked"] == []
    assert A_KEY not in town.store.deleted


def test_a_write_the_external_store_would_not_take_is_reported(town):
    """`_persist_secret` returns whether the EXTERNAL store took the value.
    False means it landed only in the encrypted database copy, which is a
    working credential right up until the sweep that scrubs those copies.

    Every other caller surfaces that. This one dropped it on the floor, so the
    host was told "accepted" for a credential that is not where it thinks it
    is. Accepted is still the right answer -- the town can use the key -- but
    not the only answer."""
    town.store.db_only.add(ANOTHER)

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))

    assert sorted(result["accepted"]) == sorted([A_KEY, ANOTHER])
    assert result["db_only"] == [ANOTHER]
    # Names only, here as everywhere else on this endpoint.
    assert VALUE not in json.dumps(result)


def test_a_key_that_is_no_longer_shareable_is_kept_rather_than_deleted(town, monkeypatch):
    """The revoke loop had no allowlist, and the write path does.

    That asymmetry deletes credentials nobody withdrew. `shareable_keys` is
    derived from the provider catalogs, so it shrinks whenever a provider
    leaves one or a key becomes platform-managed -- and a key stranded by that
    change looks exactly like a key the host stopped sending. Vault and
    database copy both go, on the strength of a catalog edit, and there is no
    way back from here.

    So a key the host owns but may no longer be given is left alone, and left
    on the host's books: the claim is what lets the host withdraw it on purpose
    if it ever becomes shareable again."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    assert town.row.host_provided_keys == [ANOTHER, A_KEY]

    # The catalogs stop naming A_KEY, exactly as a provider removal would.
    shrunk = host_secrets.shareable_keys() - {A_KEY}
    monkeypatch.setattr(host_secrets, "shareable_keys", lambda: shrunk)

    result = _run(host_secrets.apply(town.db, {ANOTHER: "translator-0002"}))

    assert result["revoked"] == []
    assert town.store.deleted == []
    # Still live, and still recorded as the host's so it can be withdrawn
    # deliberately later.
    assert town.store.values[A_KEY] == VALUE
    assert A_KEY in host_secrets._normalise(town.row.host_provided_keys)


def test_a_refused_key_in_the_payload_is_not_revoked(town):
    """The other half of the same decision, written down because it is a choice
    rather than an accident.

    A key that arrived and was refused is a key the host is still sharing --
    the write just could not happen. Revocation has exactly one spelling in a
    full replace, and it is absence from the payload. Treating refusal as
    withdrawal would delete a live credential every time a vault hiccuped."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))

    # Blank is refused, and the key is present in the payload.
    result = _run(host_secrets.apply(town.db, {A_KEY: "   ", ANOTHER: "translator-0002"}))

    assert result["refused"] == [A_KEY]
    assert result["revoked"] == []
    assert town.store.values[A_KEY] == VALUE
    assert A_KEY in host_secrets._normalise(town.row.host_provided_keys)


# ---------------------------------------------------------------------------
# A withdrawal that does not take
# ---------------------------------------------------------------------------

def test_a_failed_withdrawal_keeps_the_key_and_says_so(town):
    """`delete_secret` returns True when EITHER the vault delete or the database
    delete worked. An unreachable vault therefore produces a successful-looking
    call that removed the database copy and left the real credential live.

    Dropping the key from the ownership record on that basis is the worst
    available outcome: the credential still works, nothing on screen says it is
    the host's, and the host cannot withdraw it on a later push because it no
    longer owns it. So a withdrawal that cannot be confirmed keeps the key and
    is reported in `failed`."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    town.store.undeletable.add(ANOTHER)

    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result["revoked"] == []
    assert result["failed"] == [ANOTHER]
    # Still live, and still the host's to take back.
    assert town.store.values[ANOTHER] == "translator-0002"
    assert town.row.host_provided_keys == [ANOTHER, A_KEY]


def test_a_later_push_can_still_withdraw_what_failed_before(town):
    """Which is the point of keeping it. The retry is the next ordinary push --
    the host's console has nothing else to say, and nothing has to be
    reconciled by hand."""
    _run(host_secrets.apply(town.db, {A_KEY: VALUE, ANOTHER: "translator-0002"}))
    town.store.undeletable.add(ANOTHER)
    _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    town.store.undeletable.clear()
    result = _run(host_secrets.apply(town.db, {A_KEY: VALUE}))

    assert result["revoked"] == [ANOTHER]
    assert result["failed"] == []
    assert ANOTHER not in town.store.values
    assert town.row.host_provided_keys == [A_KEY]


def test_a_revoke_reports_failure_when_the_credential_still_resolves(monkeypatch):
    """The real `_revoke`, not the stand-in: the delete is called, it answers
    the cheerful True that a half-delete answers, and the key still reads back.
    """
    from app.services import secret_manager

    async def delete_secret(_key):
        return True  # The database copy went; the vault entry did not.

    monkeypatch.setattr(secret_manager, "delete_secret", delete_secret)
    monkeypatch.setattr(secret_manager, "clear_cache", lambda **kw: None)

    async def still_there(_key):
        return "the-value-that-would-not-die"

    monkeypatch.setattr(secret_manager, "get_secret", still_there)

    assert _run(host_secrets._revoke(FakeDB(), A_KEY)) is False


# ---------------------------------------------------------------------------
# The write choke-point
# ---------------------------------------------------------------------------

def test_the_town_reading_is_the_default_for_persist_secret():
    """`from_host` has to be opted into. A write path added later that forgets
    the flag takes ownership for the town, which is the safe direction: the
    host stops being able to overwrite something, rather than starting to."""
    import inspect

    sig = inspect.signature(system._persist_secret)
    assert sig.parameters["from_host"].default is False
    assert sig.parameters["from_host"].kind is inspect.Parameter.KEYWORD_ONLY


def test_every_town_write_path_clears_provenance():
    """Two doors into the secret table, and both have to move ownership. The
    plain-secrets endpoint does not go through `_persist_secret` -- it predates
    it and writes the row itself -- so a check that only looked at the choke
    point would miss the one way a town could enter its own credential and
    still be overwritten."""
    import inspect

    for fn in (system._persist_secret, system.create_or_update_secret):
        src = inspect.getsource(fn)
        assert "host_secrets.forget" in src, fn.__name__


def test_the_auth0_wizard_is_a_town_write_like_any_other():
    """A third door, and the one that was still open.

    `configure_auth0` builds the town's own Auth0 application and then stored
    AUTH0_DOMAIN, AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET by writing
    SystemSecret rows itself. All three are host-shareable, so a town that set
    up its OWN identity provider on that page kept them marked host-provided:
    the next push replaced the town's SSO with the host's, and the host
    un-sharing Auth0 deleted a credential the host never supplied. Losing sign
    -in for a whole town, from a wizard whose entire purpose is the town's own
    tenant.

    It goes through the choke point now, which is what moves ownership."""
    import inspect

    from app.api import setup

    src = inspect.getsource(setup.configure_auth0)
    assert "_persist_secret" in src
    # And no longer writes the rows by hand around that call.
    assert "SystemSecret(" not in src.split("AUTH0_CLIENT_SECRET")[-1]


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

def test_the_route_exists_on_the_provisioning_router():
    paths = {r.path for r in provisioning.router.routes}
    assert "/host-secrets" in paths


def test_the_endpoint_is_inert_until_a_provisioning_token_is_set(monkeypatch):
    """404, not 401. A standalone install exposes no provisioning surface at
    all -- the endpoint does not merely refuse, it does not exist."""
    class NoToken:
        provisioning_token = ""

    monkeypatch.setattr(provisioning, "get_settings", lambda: NoToken())
    with pytest.raises(HTTPException) as exc:
        provisioning.require_provisioning_token("anything")
    assert exc.value.status_code == 404


def test_a_wrong_token_is_rejected(monkeypatch):
    class Token:
        provisioning_token = "the-real-token"

    monkeypatch.setattr(provisioning, "get_settings", lambda: Token())
    with pytest.raises(HTTPException) as exc:
        provisioning.require_provisioning_token("not-the-token")
    assert exc.value.status_code == 401

    assert provisioning.require_provisioning_token("the-real-token") == "orchestrator"


def test_the_endpoint_answers_with_names_and_audits_no_values(town, monkeypatch):
    recorded = {}

    async def log_event(db, **kw):
        recorded.update(kw)

    monkeypatch.setattr(provisioning.AuditService, "log_event", log_event)

    body = provisioning.HostSecretsRequest(secrets={A_KEY: VALUE, "SECRETS_PROVIDER": "aws"})
    response = _run(provisioning.set_host_secrets(body, db=town.db, actor="orchestrator"))

    assert response == {
        "status": "ok",
        "accepted": [A_KEY],
        "refused": ["SECRETS_PROVIDER"],
        "revoked": [],
        "failed": [],
        "db_only": [],
    }
    assert recorded["event_type"] == "provisioning_host_secrets"
    assert recorded["details"]["counts"] == {
        "accepted": 1, "refused": 1, "revoked": 0, "failed": 0, "db_only": 0,
    }

    # The flat rule: a value never reaches the audit table, which is exported.
    assert VALUE not in json.dumps(recorded["details"])
    assert VALUE not in json.dumps(response)


# ---------------------------------------------------------------------------
# What the admin console is told
# ---------------------------------------------------------------------------

def test_provenance_is_reported_per_field_and_only_where_stored(town):
    """True needs both halves. A box the host once filled and has since
    withdrawn must not keep claiming a provider the town no longer has."""
    town.row.host_provided_keys = [A_KEY, ANOTHER]

    provenance = _run(host_secrets.field_provenance(
        town.db, {A_KEY: True, ANOTHER: False, "SMTP_PASSWORD": True}
    ))

    assert provenance == {A_KEY: True, ANOTHER: False, "SMTP_PASSWORD": False}


def test_provenance_reads_as_town_owned_when_it_cannot_be_read(town, monkeypatch):
    """The note is explanatory, so the safe failure is an ordinary "Saved"."""
    async def boom(_db, *, create=False):
        raise RuntimeError("no settings row")

    monkeypatch.setattr("app.services.system_settings.get_settings", boom)
    assert _run(host_secrets.field_provenance(town.db, {A_KEY: True})) == {A_KEY: False}


def test_every_catalog_route_reports_provenance():
    """All five of them. The generic route serves five capabilities and the four
    hand-written ones came first, so a change made in one place and not the
    others is the standing failure mode on this endpoint family."""
    import inspect

    routes = (
        system.get_identity_catalog,
        system.get_translation_catalog,
        system.get_maps_catalog,
        system.get_ai_catalog,
        system.get_capability_catalog,
    )
    for fn in routes:
        src = inspect.getsource(fn)
        assert '"host_provided"' in src, fn.__name__
        assert "_host_provided_fields" in src, fn.__name__


def test_the_secrets_list_says_who_supplied_each_key(town, monkeypatch):
    class FakeSecret:
        def __init__(self, key_name):
            self.id = 1
            self.key_name = key_name
            self.key_value = None
            self.description = None
            self.is_configured = True

    rows = [FakeSecret(A_KEY), FakeSecret("SMTP_PASSWORD")]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class DB:
        async def execute(self, _stmt):
            return Result()

    async def get_secret(_key):
        return "something"

    monkeypatch.setattr("app.services.secret_manager.get_secret", get_secret)

    async def host_provided(_db):
        return [A_KEY]

    monkeypatch.setattr(host_secrets, "host_provided", host_provided)

    listed = _run(system.list_secrets(db=DB(), _=None))

    by_key = {row.key_name: row for row in listed}
    assert by_key[A_KEY].host_provided is True
    assert by_key["SMTP_PASSWORD"].host_provided is False
    # Still no values, for any key.
    assert all(row.key_value is None for row in listed)


def test_the_secrets_list_drops_the_host_note_when_nothing_is_stored(town, monkeypatch):
    """Both halves, the same rule the catalog routes use. A key the host has
    since withdrawn kept saying "supplied by your host" next to an empty box,
    which reads as a credential the town has and does not -- and the row
    survives the withdrawal, because `is_configured` on the list is computed
    from what can actually be read."""
    class FakeSecret:
        def __init__(self, key_name):
            self.id = 1
            self.key_name = key_name
            self.key_value = None
            self.description = None
            self.is_configured = True

    rows = [FakeSecret(A_KEY)]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class DB:
        async def execute(self, _stmt):
            return Result()

    async def get_secret(_key):
        return None  # Withdrawn, or never readable.

    monkeypatch.setattr("app.services.secret_manager.get_secret", get_secret)

    async def host_provided(_db):
        return [A_KEY]

    monkeypatch.setattr(host_secrets, "host_provided", host_provided)

    listed = _run(system.list_secrets(db=DB(), _=None))

    assert listed[0].is_configured is False
    assert listed[0].host_provided is False


def test_clearing_a_host_provided_key_takes_it_off_the_host(town, monkeypatch):
    """A blank save on the plain secrets endpoint is a deliberate clear -- the
    box IS the credential there, unlike the provider cards where an untouched
    masked field posts empty and means "keep what is stored".

    The `forget` call used to be inside `if secret_data.key_value:`, so
    clearing a host-supplied key left it host-owned and the next scheduled push
    put it straight back. A town that deletes a credential has decided it does
    not want that credential, and a machine undoing that decision minutes later
    is indistinguishable from the clear not having worked."""
    from app.schemas import SecretCreate

    town.row.host_provided_keys = [A_KEY]

    class Result:
        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self):
            self.added = []

        async def execute(self, _stmt):
            return Result()

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            pass

        async def refresh(self, _obj):
            pass

    class Background:
        def add_task(self, *a, **kw):
            pass

    async def noop(*a, **kw):
        pass

    monkeypatch.setattr(system, "_require_a_secret_store", lambda: None)
    monkeypatch.setattr("app.services.secret_manager.set_secret", noop)
    monkeypatch.setattr("app.services.admin_audit.record_admin_action", noop)

    _run(system.create_or_update_secret(
        SecretCreate(key_name=A_KEY, key_value=""),
        background=Background(),
        db=DB(),
        _=None,
    ))

    assert town.row.host_provided_keys == []


# ---------------------------------------------------------------------------
# The real write path
# ---------------------------------------------------------------------------

def test_a_push_lands_before_the_town_has_chosen_a_secret_store(town, monkeypatch):
    """End to end through the REAL `_persist_secret`, with no store chosen.

    This is the state a town is in the moment it is provisioned, and it is the
    whole reason the endpoint has no store gate: the admin pages refuse a
    credential until somebody says where credentials go, which protects a
    person from an unmade decision, but refusing the host's push would leave a
    fresh town with no credentials and no way to be given any until an admin
    logged in.

    So: no 409, an encrypted row written to the database (never plaintext, even
    in this pre-decision window), and the key recorded as the host's."""
    from app.core.encryption import decrypt
    from app.services import secret_manager

    monkeypatch.delenv("SECRETS_PROVIDER", raising=False)
    # No project, no client: the "not available" answer, without asking the
    # network for it.
    monkeypatch.setitem(secret_manager._config, "use_gcp", False)

    class Result:
        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self):
            self.added = []
            self.commits = 0

        async def execute(self, _stmt):
            return Result()

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.commits += 1

    db = DB()
    # The real thing, not the fixture's recorder.
    monkeypatch.setattr(system, "_persist_secret", _REAL_PERSIST_SECRET)

    result = _run(host_secrets.apply(db, {A_KEY: VALUE}))

    assert result["accepted"] == [A_KEY]
    assert result["refused"] == []
    assert len(db.added) == 1
    written = db.added[0]
    assert written.key_name == A_KEY
    assert written.is_configured is True
    # Encrypted at rest, and it really is the value we pushed.
    assert written.key_value != VALUE
    assert decrypt(written.key_value) == VALUE
    assert town.row.host_provided_keys == [A_KEY]
    # And NOT flagged as a write that only reached the database. There is no
    # external store to have rejected it -- see the test below.
    assert result["db_only"] == []


@pytest.mark.parametrize("provider", ["database", ""])
def test_a_town_with_no_external_store_is_not_told_its_keys_are_db_only(
    town, monkeypatch, provider
):
    """`db_only` has to mean something, and on the default deployment it did not.

    `set_secret` returns False for every key when the chosen store IS the
    encrypted database, by design rather than by failure -- there is nothing
    outside the database to write to. Reported as db-only, that made an
    ordinary save on an ordinary town look like a credential about to vanish,
    on every key, forever; and the endpoint's own documentation told the host
    exactly that. An alarm that fires on the default configuration is one
    nobody can act on, which costs the real signal it was added to carry.

    Both external-less states are the same answer: "database" is a deliberate
    choice, "" is a town that has not chosen yet and whose values land in the
    database on purpose until it does."""
    from app.services import secret_manager

    if provider:
        monkeypatch.setenv("SECRETS_PROVIDER", provider)
    else:
        monkeypatch.delenv("SECRETS_PROVIDER", raising=False)
    monkeypatch.setitem(secret_manager._config, "use_gcp", False)

    class Result:
        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self):
            self.added = []

        async def execute(self, _stmt):
            return Result()

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            pass

    monkeypatch.setattr(system, "_persist_secret", _REAL_PERSIST_SECRET)

    result = _run(host_secrets.apply(DB(), {A_KEY: VALUE}))

    assert result["accepted"] == [A_KEY]
    assert result["db_only"] == []
    assert secret_manager.external_store_configured() is False


def test_an_external_store_that_refuses_the_write_is_still_reported(town, monkeypatch):
    """The other side of the same coin: the signal must survive the fix.

    With a real external store configured, a False from `set_secret` is what it
    always was -- the store would not take it, the credential is living in the
    database copy alone, and it goes away when that copy is swept. That is the
    ordering trap the flag exists for, and narrowing db_only must not have
    silenced it."""
    from app.services import secret_manager

    monkeypatch.setenv("SECRETS_PROVIDER", "azure")

    async def refuse(_key, _value):
        return False

    monkeypatch.setattr(secret_manager, "set_secret", refuse)
    monkeypatch.setattr("app.services.secret_manager.set_secret", refuse)

    class Result:
        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self):
            self.added = []

        async def execute(self, _stmt):
            return Result()

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            pass

    monkeypatch.setattr(system, "_persist_secret", _REAL_PERSIST_SECRET)

    result = _run(host_secrets.apply(DB(), {A_KEY: VALUE}))

    assert secret_manager.external_store_configured() is True
    assert result["accepted"] == [A_KEY]
    assert result["db_only"] == [A_KEY]


def test_the_column_has_the_belt_and_braces_guard_too():
    """Every recent column is added twice: once by its Alembic revision, and
    once by init_db's idempotent ADD COLUMN IF NOT EXISTS.

    That is not redundancy for its own sake. init_db is what catches an install
    whose migration history is ahead of its actual schema -- a restored dump, a
    stamped-not-migrated database, a town brought up from a snapshot. Without
    the guard, `host_provided_keys` is missing on exactly those deployments and
    every read of the settings row raises: not a degraded credential feature, a
    500 on any page that reads settings at all."""
    from pathlib import Path

    from app.db import init_db

    source = Path(init_db.__file__).read_text()
    assert (
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS host_provided_keys"
        in source
    )


def test_the_declared_schema_floor_matches_the_migration_it_ships():
    """MIN_DB_REVISION is the oldest schema this image can start against, and
    the orchestrator refuses an upgrade outside [min .. head].

    The consuming gate is SET MEMBERSHIP, not a range: centralizedhosting's
    rollout._precheck_compatibility refuses unless the town's observed revision
    is exactly `min_db_revision` or exactly `db_revision`. So there are only
    ever two legal declarations, and which one is correct depends on the head
    migration:

      * ADDITIVE (expand) -- the previous head. The new build still runs on the
        old schema, so a town can be started on the new image and migrated
        after, with no window where code and schema disagree.
      * DESTRUCTIVE (contract) -- head itself. The new build cannot run on the
        old schema, so the orchestrator has to see the town already migrated.

    This asserts the rule rather than one side of it. An earlier version
    demanded the previous head unconditionally, which is right for the
    migration shipping today and would have pressured whoever writes the next
    contract migration into declaring a floor their build cannot honour.

    The value had been left fourteen revisions back, across several contract
    migrations this build genuinely cannot start against -- the unsafe
    direction, since it lets an image reach a town whose schema it cannot
    serve, where naming a too-new revision merely blocks a rollout."""
    pytest.importorskip("alembic.script")
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]

    # The same parse the CI job does: comments out, whitespace stripped.
    declared = ""
    for line in (root / "MIN_DB_REVISION").read_text().splitlines():
        if line.strip() and not line.strip().startswith("#"):
            declared = line.strip()
    assert declared

    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one head, got {sorted(heads)}"

    head = heads[0]
    previous = script.get_revision(head).down_revision

    # The gate only ever accepts one of these two.
    assert declared in {head, previous}, (
        f"MIN_DB_REVISION says {declared}, which is neither head ({head}) nor "
        f"the revision before it ({previous}). The orchestrator's compatibility "
        f"check is set membership on exactly those two."
    )

    # And which of the two, by the head migration's own shape.
    from app.db.migrate import ADDITIVE, classify_source, revision_sources

    _path, head_source = revision_sources()[head]
    if classify_source(head_source) == ADDITIVE:
        assert declared == previous, (
            f"head {head} is additive, so the build runs on the old schema and "
            f"MIN_DB_REVISION should be {previous}, not {declared}"
        )
    else:
        assert declared == head, (
            f"head {head} is destructive, so the build cannot run on the old "
            f"schema and MIN_DB_REVISION must be {head}, not {declared}"
        )


def test_the_guard_read_asks_the_database_rather_than_the_identity_map():
    """The "fresh read" in `_store` has to actually be fresh.

    It exists to notice a `forget` that another session committed while this
    push was in its multi-second vault write. But an ordinary `get_settings`
    issues the SELECT and then throws the result away: SQLAlchemy's identity
    map already holds this row, so the attributes handed back are the ones it
    was first loaded with, and the clerk's save is invisible. The push then
    writes the key back onto the host's books and the next push overwrites the
    value they typed -- the exact bug the re-read was added to prevent.

    `populate_existing` is what makes the ORM overwrite the loaded attributes
    with what the database now says, so this pins that the flag is on the
    statement and that `_store` is the caller asking for it.
    """
    from app.services import system_settings

    seen = []

    class RecordingDB:
        async def execute(self, statement):
            seen.append(statement)

            class Result:
                def scalar_one_or_none(self_inner):
                    return FakeRow()

            return Result()

        async def flush(self):
            pass

    def populate_existing(statement) -> bool:
        return bool(statement.get_execution_options().get("populate_existing"))

    # The default stays cheap: almost every caller wants the identity map.
    _run(system_settings.get_settings(RecordingDB()))
    assert not populate_existing(seen[-1])

    _run(system_settings.get_settings(RecordingDB(), fresh=True))
    assert populate_existing(seen[-1])

    # And this is the caller that must not get a stale answer.
    _run(host_secrets._store(RecordingDB(), add=[A_KEY]))
    assert populate_existing(seen[-1])


def test_the_ownership_column_survives_a_real_round_trip():
    """A real SQLAlchemy JSON column, not a plain attribute on a stand-in.

    The suite has no async database (no aiosqlite), so this uses a synchronous
    SQLite session against the actual `SystemSettings` model -- which is enough,
    because what is being checked is ORM change tracking, not the driver.

    SQLAlchemy does not see in-place mutation of a plain JSON column: a
    `keys.remove(...)` or `keys.append(...)` is a change that flushes as
    nothing. `_store` therefore assigns a new list, and this is the test that
    fails if somebody ever tidies that into a mutation -- a provenance record
    that silently does not persist is exactly the bug where a town's own
    credential keeps being overwritten by the host."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import SystemSettings

    engine = create_engine("sqlite://")
    SystemSettings.__table__.create(engine)

    with Session(engine) as session:
        row = SystemSettings(host_provided_keys=[A_KEY, ANOTHER])
        session.add(row)
        session.commit()

        # What `_store` does: fresh set, assigned.
        row.host_provided_keys = sorted(
            set(host_secrets._normalise(row.host_provided_keys)) - {ANOTHER}
        )
        session.commit()
        session.expire_all()

        reloaded = session.query(SystemSettings).one()
        assert reloaded.host_provided_keys == [A_KEY]

        # And empty is stored as empty, not as NULL-that-reads-as-unknown.
        reloaded.host_provided_keys = []
        session.commit()
        session.expire_all()
        assert session.query(SystemSettings).one().host_provided_keys == []
