"""Configured, wanted and running are three facts, and there were two.

The requirement, in the reporter's words: "I can for example save an email or AI
key but not use it and then this is reflected in the service provider card but
things are still saved."

That could not be expressed. Wanted-ness lived in the browser -- a
`Set<string>` initialised to every feature -- so unticking a capability hid part
of the setup guide, survived nothing, and switched nothing off. The only way to
stop a configured integration was to delete the credential it had just been
asked for.

These tests cover the switch itself and the promise around it: the credential
stays readable, and nothing dispatches through it.
"""

import asyncio

import pytest

pytest.importorskip("fastapi")

from app.services import capability_switches as cs


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def stored(monkeypatch):
    """The switches as if a town had set exactly these, and nothing else."""
    box: dict = {}

    async def _stored():
        return dict(box)

    monkeypatch.setattr(cs, "_stored", _stored)
    return box


@pytest.fixture
def legacy(monkeypatch):
    """What the old flags said, for the entries a town has never answered."""
    box = {"value": True}

    async def _legacy(capability):
        return box["value"]

    monkeypatch.setattr(cs, "_legacy", _legacy)
    return box


# ---------------------------------------------------------------------------
# The three facts
# ---------------------------------------------------------------------------

def test_a_stored_no_switches_the_capability_off(stored, legacy):
    stored["ai"] = False
    assert _run(cs.enabled("ai")) is False


def test_never_answered_is_not_the_same_as_no(stored, legacy):
    """An empty map means the town has not been asked, and must behave exactly
    as the code did before the switch existed. Reading it as "off" would switch
    every integration off on the deploy that added the column."""
    legacy["value"] = True
    assert _run(cs.enabled("translation")) is True
    legacy["value"] = False
    assert _run(cs.enabled("translation")) is False


def test_sign_in_maps_and_the_secret_store_cannot_be_switched_off(stored, legacy):
    """A town cannot take a report without staff sign-in and a map, and every
    credential either needs is kept by the secret store. An off switch for these
    would be an offer to break intake from the setup page."""
    legacy["value"] = False
    for capability in ("identity", "maps", "secrets"):
        stored[capability] = False
        assert _run(cs.enabled(capability)) is True, capability


def test_the_store_being_unreadable_does_not_switch_everything_off(monkeypatch, legacy):
    """`_stored` runs on dispatch paths -- the detector, the sender, the
    translator. A database hiccup must not decide that a town wants nothing, so
    an unreadable column reads as "never answered" and falls through to what the
    code did before the switch existed."""
    import app.db.session as session

    def explode():
        raise RuntimeError("no database")

    monkeypatch.setattr(session, "SessionLocal", explode, raising=False)
    assert _run(cs._stored()) == {}

    legacy["value"] = True
    assert _run(cs.enabled("ai")) is True


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """Just enough of AsyncSession for `set_enabled`."""

    def __init__(self, row):
        self.row = row
        self.committed = False

    async def execute(self, _stmt):
        return _FakeResult(self.row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    def add(self, row):
        self.row = row


@pytest.fixture
def writable(monkeypatch):
    """A settings row `set_enabled` can write to, without a database."""
    import sqlalchemy.orm.attributes as attributes

    from app.models import SystemSettings

    monkeypatch.setattr(attributes, "flag_modified", lambda *_a: None)
    row = SystemSettings()
    row.capability_switches = {}
    db = _FakeSession(row)

    async def all_enabled():
        return dict(row.capability_switches)

    monkeypatch.setattr(cs, "all_enabled", all_enabled)
    return db, row


def test_a_partial_write_leaves_the_other_answers_alone(writable):
    """The questionnaire posts the chip that was clicked. A town that has never
    been asked about photo redaction must not acquire an answer to it because
    somebody unticked backups."""
    db, row = writable
    _run(cs.set_enabled(db, {"ai": False}))
    _run(cs.set_enabled(db, {"backups": False}))

    assert row.capability_switches == {"ai": False, "backups": False}
    assert "redaction" not in row.capability_switches
    assert db.committed


def test_an_unknown_key_is_ignored_rather_than_stored(writable):
    """Otherwise the column becomes a place to write arbitrary JSON through an
    admin endpoint, and `all_enabled` starts reporting things that are not
    capabilities."""
    db, row = writable
    _run(cs.set_enabled(db, {"ai": False, "rm -rf": True, "identity": False}))

    # `identity` is accepted and ignored rather than refused: the questionnaire
    # never offers it, so a request carrying it is a stale client rather than an
    # attempt to break the town.
    assert row.capability_switches == {"ai": False}


# ---------------------------------------------------------------------------
# The reconciliation
# ---------------------------------------------------------------------------

def test_modules_no_longer_carries_a_provider_backed_flag():
    """Two overlapping off-switches is how this started. `modules` keeps the
    product features with nothing to configure; anything with a provider,
    credentials and a card is switched here."""
    from app.models import SystemSettings

    default = SystemSettings.__table__.c.modules.default.arg
    # The provider-free product features, and only those. `platform_feedback`
    # joined the list in 2026-08: one anonymous question asked by the app
    # itself, with no vendor, no credential and no card to configure.
    assert set(default) == {
        "unlisted_reports", "research_portal", "platform_feedback",
    }, default
    # The specific regression: none of the capability-backed switches may
    # reappear here, whatever else the list grows.
    assert not set(default) & {
        "ai_analysis", "sms_alerts", "email_notifications", "translation",
    }, default


def test_the_legacy_flags_are_still_consulted_for_an_unanswered_capability():
    """Both of them, and both have to agree.

    `configure_notifications` refused to build a sender without EMAIL_ENABLED
    and `send_notifications` refused to send without modules.email_notifications
    -- both were live at once. Taking either alone would switch email on for a
    town that had it off."""
    assert cs._LEGACY_MODULE_FLAG["email"] == ("email_notifications", True)
    assert cs._LEGACY_SECRET["email"] == ("EMAIL_ENABLED", "opt-in")
    # SMS was a kill switch, not an opt-in: it already had an off state in its
    # provider (`none`), so requiring a second yes would have stopped texts for
    # every town that configured Twilio and never heard of the key.
    assert cs._LEGACY_SECRET["sms"] == ("SMS_ENABLED", "kill-switch")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _switch(monkeypatch, **answers):
    async def enabled(capability):
        return answers.get(capability, True)

    monkeypatch.setattr(cs, "enabled", enabled)


def test_the_analyser_does_not_fire_when_ai_is_off(monkeypatch):
    """And the key is not deleted to achieve it -- `get_ai_provider` returns
    None with a perfectly good Vertex credential still stored."""
    from app.services.ai import registry

    _switch(monkeypatch, ai=False)
    reads = []

    async def get_secret(key):
        reads.append(key)
        return "something"

    monkeypatch.setattr("app.services.secret_manager.get_secret", get_secret)
    assert _run(registry.get_ai_provider()) is None
    assert reads == [], "it read credentials for a capability nobody is using"


def test_the_translator_does_not_fire_when_translation_is_off(monkeypatch):
    """Google is the default and needs no key of its own beyond the cloud
    account, so before the switch a town on Google Cloud got translation whether
    it wanted it or not, with nothing it could remove to stop it."""
    from app.services import translation_providers

    _switch(monkeypatch, translation=False)
    assert _run(translation_providers.get_translation_provider()) is None


def test_the_detector_does_not_fire_when_redaction_is_off(monkeypatch):
    """`resolve_provider` floors at on-server detection so a town cannot end up
    with no blurring by accident. That makes the switch the only way to say no
    on purpose."""
    from app.services import image_redaction

    _switch(monkeypatch, redaction=False)
    assert _run(image_redaction.settings()) == (None, False, False)


def test_the_sender_is_cleared_rather_than_left_running(monkeypatch):
    """A singleton outlives the call that configured it. Skipping the configure
    step left the previous sender in place, so a town that switched resident
    email off carried on emailing residents until the worker restarted."""
    import inspect

    from app.tasks import service_requests

    src = inspect.getsource(service_requests.configure_notifications)
    off_branch = src[src.index('enabled("email")'):]
    assert "_email_provider = None" in off_branch[:400]


@pytest.mark.parametrize("capability", ["ai", "translation", "email", "sms", "redaction", "kms"])
def test_a_switched_off_capability_is_not_reported_as_work_outstanding(monkeypatch, capability):
    """`capability_is_configured` decides what the daily sweep tests. A town
    that switched something off has not left work to do, and a red badge on it
    is the noise that teaches people to ignore badges."""
    from app.api import system

    _switch(monkeypatch, **{capability: False})
    assert _run(system.capability_is_configured(capability)) is False


def test_geocoding_is_the_documented_exception():
    """Maps is ALWAYS_ON, so `geocode_dispatch` has no switch to consult. Said
    in the code rather than left as an absence, so "was geocoding covered" has
    an answer."""
    import inspect

    from app.services import geocode_dispatch

    assert "maps" in cs.ALWAYS_ON
    assert "ALWAYS_ON" in inspect.getsource(geocode_dispatch._selected)


def test_every_switch_is_enforced_somewhere():
    """A switch nothing consults is a lie told in a nice font.

    `backups` and `errors` shipped that way: both were offered as ticks, both
    persisted, both showed on the card -- and `backup_database` never asked, so
    nightly dumps kept going to S3, while crash reporting was wired to a DSN read
    from the environment once at import. Unticking either changed nothing that
    left the building.

    So the rule is mechanical rather than remembered: every switchable capability
    is either always on, consulted at its point of use, or named in
    `ADVISORY_ONLY` with the reason written beside it. Discovering the call sites
    rather than listing them, because a list is the thing that goes stale.
    """
    from pathlib import Path

    app_dir = Path(cs.__file__).resolve().parent.parent
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in app_dir.rglob("*.py")
        if p.name != "capability_switches.py"
    )

    must_be_enforced = set(cs.SWITCHABLE) - cs.ALWAYS_ON - cs.ADVISORY_ONLY
    assert must_be_enforced, "the sets cannot swallow everything"

    for capability in sorted(must_be_enforced):
        # `wanted_sync` counts. It is how crash reporting is enforced -- Sentry's
        # `before_send` is synchronous and cannot await `enabled` -- and it is a
        # real gate, not a way around this assertion.
        assert any(
            f"{reader}({quote}{capability}{quote})" in sources
            for reader in ("enabled", "wanted_sync")
            for quote in ('"', "'")
        ), (
            f"nothing consults the {capability!r} switch, so switching it off "
            f"changes nothing a town can observe. Either gate it where it "
            f"dispatches, or add it to ADVISORY_ONLY with the reason."
        )


def test_advisory_only_is_not_a_place_to_hide_things():
    """It exists for one capability with one reason. Left unbounded it becomes
    the drawer everything unfinished gets swept into, and the test above stops
    meaning anything."""
    assert cs.ADVISORY_ONLY == frozenset({"kms"})
    assert not (cs.ADVISORY_ONLY & cs.ALWAYS_ON), "a capability is one or the other"
    assert cs.ADVISORY_ONLY <= set(cs.SWITCHABLE)
    # The reason, in the module, next to the set.
    import inspect

    src = inspect.getsource(cs)
    assert "pii_crypto" in src, "why kms stops short of the cipher has to be written down"


def test_crash_reporting_answers_without_touching_the_database():
    """`before_send` runs on the error path, and the error may be that the
    database is unreachable. It reads the snapshot, which is why the snapshot
    exists.

    Parsed rather than imported. `app.main` pulls in every router, and importing
    it from the test suite hits a pre-existing failure in `app.api.api_usage`
    that also breaks `test_security_headers` at collection -- unrelated to this,
    and not worth making this assertion depend on. Parsed rather than grepped,
    too: the docstring explains why the function must not open a session, so a
    substring search finds "SessionLocal" in the explanation and fails a function
    that is doing exactly the right thing.
    """
    import ast
    from pathlib import Path

    main_py = Path(cs.__file__).resolve().parent.parent / "main.py"
    tree = ast.parse(main_py.read_text(encoding="utf-8"))

    hook = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "_crash_reporting_wanted"),
        None,
    )
    assert hook is not None, "the before_send hook is gone, so the switch is inert again"
    assert not isinstance(hook, ast.AsyncFunctionDef), "before_send is called synchronously"

    body = ast.dump(ast.Module(body=hook.body, type_ignores=[]))
    assert "wanted_sync" in body
    assert "SessionLocal" not in body
    assert "Await" not in body, "there is no event loop on this path"

    # And that it is actually wired in, rather than defined and forgotten.
    assert "before_send=_crash_reporting_wanted" in main_py.read_text(encoding="utf-8")


def test_the_snapshot_survives_a_failed_read(monkeypatch):
    """A database hiccup is not evidence that anything was switched off. Clearing
    the snapshot on failure would turn one into "start reporting crashes again"
    -- or worse, into silence, depending which way the default fell."""
    monkeypatch.setattr(cs, "_snapshot", {"errors": False})

    class Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("no database")

    monkeypatch.setattr("app.db.session.SessionLocal", Boom())
    assert _run(cs._stored()) == {}
    assert cs.wanted_sync("errors") is False, "the last known answer still stands"


def test_a_process_that_has_read_nothing_still_reports_crashes(monkeypatch):
    """Swallowing the crash that stopped a process from reading its own settings
    is the worse of the two failures."""
    monkeypatch.setattr(cs, "_snapshot", {})
    assert cs.wanted_sync("errors") is True


# ---------------------------------------------------------------------------
# What the page is told
# ---------------------------------------------------------------------------

def test_status_reports_wanted_beside_configured(monkeypatch):
    """Not instead of it. The card has to be able to say "switched off, and your
    credentials are still there", which needs both facts in the same payload."""
    from app.api import system

    async def all_enabled():
        return {"sms": False, "ai": True, "backups": False}

    async def providers_for(capability):
        return [{"provider": "twilio", "credential_fields": []}]

    async def effective(capability):
        return "twilio"

    async def configured_map(providers):
        return {"twilio": True}

    monkeypatch.setattr(cs, "all_enabled", all_enabled)
    monkeypatch.setattr(system, "providers_for", providers_for)
    monkeypatch.setattr(system, "effective_provider_for", effective)
    monkeypatch.setattr(system, "_configured_map", configured_map)

    out = _run(system.get_provider_status(None))

    assert out["sms"]["enabled"] is False
    assert out["sms"]["configured"]["twilio"] is True, "the credential is still stored"
    assert out["sms"]["current_provider"] == "twilio", (
        "the provider has to survive being switched off, or the card cannot name it"
    )
    assert out["sms"]["ready"] is False, "off is not set up"
    assert out["ai"]["ready"] is True
    # The two switchable things with no provider catalog. They were held in the
    # browser and nowhere else, which is exactly the pair somebody is most
    # likely to untick and then wonder about.
    assert out["backups"] == {"enabled": False}
