"""Credentials the host supplies, and who owns each one.

A town on a state-hosted deployment often has no account of its own with a map
vendor, a translation API or an identity provider, and waiting for one is the
step that stalls onboarding. The host already has those accounts, so it can
hand the instance working credentials and the setup page stops dead-ending on
a box nobody can fill in.

That is only safe if ownership is written down, because the same box has two
possible authors and they must not be able to overwrite each other by
accident:

  * The host may replace or withdraw a key **it** provided, and nothing else.
    A key the town entered is refused, every time, however often it is pushed.
  * A town admin who types their own value into the box takes the key back
    permanently -- the next push is refused rather than silently reverting a
    credential a clerk chose on purpose.

`SystemSettings.host_provided_keys` is that record, and it holds key NAMES.
No value passes through this module into anything that is stored, logged,
audited or returned: values go to the ordinary secret path (`_persist_secret`)
and stay in the secret store plus the encrypted `system_secrets` copy, exactly
as a town-entered credential does. Everything this module reports back is a
sorted list of names.

The push is a FULL REPLACE. The payload is the complete current set of shared
credentials, so a key that is missing from it is one the host has stopped
sharing, and it is deleted rather than left behind: a credential nobody
intended to still be live is the thing a revocation exists to prevent.
"""

import logging
from typing import Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Crash reporting has no provider card and therefore no catalog entry, but it
# is a per-deployment credential a host plausibly owns. Named here rather than
# invented at the call site so there is one list to read.
EXTRA_SHAREABLE_KEYS = frozenset({"SENTRY_DSN"})

# Fields that sit in a provider catalog's `credential_fields` but are not
# credentials: they are the town's own choices about how the software behaves,
# and a host has no standing to set them from outside.
#
# REDACT_FACES and REDACT_PLATES decide whether faces and number plates in
# resident photographs are blurred before anybody sees them. They are declared
# alongside the redaction provider's key because the card needs them on screen,
# but pushing REDACT_FACES=false would turn off blurring of residents' faces
# from the host's console, silently, on a schedule -- which is a privacy
# decision belonging to the town and nobody else.
#
# SMTP_USE_TLS is transport policy for the town's own mail relay, and
# SMS_HTTP_TEST_URL points the SMS test at a URL of the pusher's choosing.
# Neither unlocks an account the host holds, which is the entire justification
# for this endpoint existing.
#
# Note this is a subtraction, not a switch to "secret fields only". The
# non-secret companion fields -- AUTH0_DOMAIN, the AZURE_*_ENDPOINT pair,
# VERTEX_AI_PROJECT and the rest -- have to stay shareable, because a key
# delivered without the endpoint or tenant it belongs to is a credential the
# town cannot use.
NON_CREDENTIAL_KEYS = frozenset({
    "REDACT_FACES",
    "REDACT_PLATES",
    "SMTP_USE_TLS",
    "SMS_HTTP_TEST_URL",
})

# Every capability with a provider catalog. The allowlist is derived from these
# rather than hand-listed, so a capability or a provider added later is covered
# without anyone remembering to come back here -- and, more importantly, a
# credential field REMOVED from a catalog stops being pushable at the same
# moment it stops being read.
_CAPABILITIES = (
    "ai", "translation", "identity", "maps",
    "email", "sms", "kms", "redaction", "secrets",
)


def shareable_keys() -> frozenset:
    """Every credential key a host is allowed to supply.

    The union of the credential fields every provider catalog declares, plus
    `EXTRA_SHAREABLE_KEYS`, minus anything the platform owns and minus
    `NON_CREDENTIAL_KEYS`.

    The platform filter is `is_platform_managed`, which is the same rule the
    town-facing write guard uses, and it removes two families at once: the
    keys that make the secret store and the KMS work (a host pushing those
    through this door would be repointing where the town's credentials live,
    from an endpoint whose whole promise is that it only writes provider
    credentials), and everything prefixed `BACKUP_`, which is the host's own
    backup arrangement and not a thing to copy into a town's vault.

    Note this filter is unconditional -- unlike `reject_platform_key_writes`,
    which only bites in managed mode. A key that is infrastructure on a hosted
    deployment is infrastructure everywhere, and the allowlist for a machine
    endpoint should not change shape depending on a mode flag.

    The second filter is `NON_CREDENTIAL_KEYS` -- the behaviour and privacy
    toggles that share a catalog with the credentials they belong to. Read its
    comment before adding to it: it is a deny-list precisely so that a new
    provider's non-secret companion field (the endpoint, the tenant, the
    project id) stays shareable by default, because a credential delivered
    without them is one the town cannot use.
    """
    from app.core.managed import is_platform_managed

    keys = set(EXTRA_SHAREABLE_KEYS)
    for capability in _CAPABILITIES:
        for spec in (_catalog(capability) or {}).values():
            for field in (spec.get("credential_fields") or []):
                key = (field or {}).get("key")
                if key:
                    keys.add(key)
    return frozenset(
        k for k in keys
        if not is_platform_managed(k) and k not in NON_CREDENTIAL_KEYS
    )


def _catalog(capability: str) -> Dict:
    """The raw catalog for a capability, or {} if it cannot be loaded.

    Never raises: an allowlist that fails closed for one capability is a host
    push that gets refused, which is recoverable and visible. An exception here
    would take out the whole endpoint.
    """
    from app.api.system import _capability_catalog

    try:
        return _capability_catalog(capability) or {}
    except Exception:  # pragma: no cover - a catalog that cannot import
        logger.warning("Could not read the %s catalog for the host allowlist", capability)
        return {}


def _normalise(raw) -> List[str]:
    """The stored column as a clean sorted list of key names.

    Defensive because the column is JSON: NULL on every row that predates the
    migration, and anything at all if something ever wrote the wrong shape.
    """
    if not isinstance(raw, (list, tuple, set)):
        return []
    return sorted({k for k in raw if isinstance(k, str) and k})


async def host_provided(db) -> List[str]:
    """Which keys are currently the host's, sorted. Never raises."""
    from app.services.system_settings import get_settings

    try:
        row = await get_settings(db)
    except Exception:
        logger.warning("Could not read host-provided credential ownership")
        return []
    return _normalise(getattr(row, "host_provided_keys", None))


async def _store(db, *, add: Iterable[str] = (), drop: Iterable[str] = ()) -> List[str]:
    """Adjust the ownership record in place. The caller commits.

    Reads the column FRESH and applies a delta, rather than writing back a set
    computed earlier. `apply` takes several seconds -- a vault write per key --
    and a town admin who saves their own credential during that window calls
    `forget`, which removes the key. Writing back a snapshot taken before the
    loop started put it straight back, and the next push then overwrote the
    value the clerk had just typed. The delta only ever says what THIS push
    decided; anything else the row says now is somebody else's decision and is
    left alone.

    `fresh=True` is what makes that read actually fresh, and it is the whole
    point of the call. An ordinary `get_settings` issues the SELECT but
    SQLAlchemy's identity map hands back the instance this session already
    holds, with the attribute values it was loaded with -- so the concurrent
    `forget` this re-read exists to notice is invisible, and the clerk's key
    goes straight back onto the host's books. `populate_existing` is what tells
    the ORM to overwrite the loaded attributes with what the database now says.
    (The tell that the old read was not fresh: the round-trip test in
    test_host_provided_credentials.py needs `session.expire_all()` before it
    can see its own committed write.)

    Flushed before returning, so the claim is durable inside the transaction
    before the caller goes off to do a multi-second vault write -- and so the
    NEXT fresh read sees it rather than discarding it as a pending change.

    Assigned as a new list rather than mutated: SQLAlchemy does not track
    in-place changes to a plain JSON column, so `keys.remove(...)` would be a
    change that never reached the database -- and a provenance record that
    silently does not persist is the failure mode where a town's own credential
    keeps being overwritten by the host.
    """
    from app.services.system_settings import get_settings

    row = await get_settings(db, create=True, fresh=True)
    fresh = set(_normalise(getattr(row, "host_provided_keys", None)))
    keys = sorted((fresh - set(drop)) | {k for k in add if k})
    row.host_provided_keys = keys
    try:
        await db.flush()
    except Exception:  # pragma: no cover - a session that cannot flush
        # The caller's commit is still ahead of us and will carry the change.
        logger.warning("Could not flush host provenance mid-push")
    return keys


async def forget(db, key_name: str) -> bool:
    """The town has entered its own value for this key, so it is theirs now.

    Called from the single write choke-point (`_persist_secret`) on every
    town-admin save, so there is no door into the secret table that leaves the
    ownership record stale. Returns whether anything changed; does not commit,
    because the caller's own commit is what makes the write and the ownership
    change land together.

    Never raises. This runs inside a save, and failing to update a provenance
    note must not fail the credential write that produced it -- the town still
    gets the value it typed, and the worst case is a later host push that is
    accepted when it should have been refused.
    """
    try:
        if key_name not in shareable_keys():
            # Selector keys, backup keys, bootstrap keys: never host-provided,
            # so there is nothing to look up. Keeps the ordinary save path from
            # taking an extra read for every key that could not be on the list.
            return False
        from app.services.system_settings import get_settings

        # Fresh for the same reason `_store` is: a push running right now has
        # been committing claims key by key, and the identity map would hand
        # this session the row as it looked before any of them. Deciding "the
        # host does not own this key" against a stale copy is how a clerk's
        # save fails to take the key back.
        row = await get_settings(db, fresh=True)
        current = _normalise(getattr(row, "host_provided_keys", None))
        if key_name not in current:
            return False
        row.host_provided_keys = [k for k in current if k != key_name]
        return True
    except Exception:
        logger.warning("Could not clear host provenance for a saved credential")
        return False


async def _is_configured(db, key_name: str) -> bool:
    """Is there a credential stored against this key right now.

    Two questions, because one of them is not enough. `get_secret` answers what
    can be READ, and it never raises: every backend falls through to the
    encrypted database copy and returns None when it has nothing. So a town
    credential that lives in a vault the instance cannot currently reach -- an
    expired service account, a network partition, a Key Vault the town rotated
    -- reads as None, which is indistinguishable from "nobody ever set this".
    Trusting that answer alone means the host's value is written over a clerk's
    credential precisely when nothing can see that it is there, which is the one
    outcome this endpoint may never produce.

    So the `system_secrets` row is asked too. `is_configured` on that row is the
    town's own record that a value was saved, and it survives the vault sweep
    that scrubs `key_value` to NULL. Either answer saying yes is yes.

    Raises rather than guessing if the row cannot be read at all: `apply` turns
    that into a refusal, which is the safe direction for the same reason.
    """
    from sqlalchemy import select

    from app.models import SystemSecret
    from app.services.secret_manager import get_secret

    if bool((await get_secret(key_name) or "").strip()):
        return True
    result = await db.execute(
        select(SystemSecret.is_configured).where(SystemSecret.key_name == key_name)
    )
    return bool(result.scalar_one_or_none())


async def _revoke(db, key_name: str) -> bool:
    """Take back a credential the host has stopped sharing.

    Deleted rather than blanked. The row and the vault entry both go, because
    "the host is no longer providing this" has to leave nothing behind that a
    later read could still resolve -- `get_secret` falls back to the database
    copy when the vault does not answer, so clearing only one of the two would
    leave the credential live.

    Returns whether the credential is actually gone, and that return value is
    the point of this function. `delete_secret` deletes from the vault AND from
    the database and returns True if EITHER worked, so an unreachable or
    unconfigured vault produces a successful-looking call that removed the
    database copy and left the real credential live in the vault. Reporting
    that as revoked and dropping the key from the ownership record would be the
    worst of the three possible outcomes: the credential still works, nobody
    knows it does, and the host can no longer withdraw it because the next push
    will refuse a key it no longer owns.

    So the key is looked up again afterwards, cache dropped first so the answer
    is not the one we are trying to invalidate. If it still resolves, the caller
    keeps ownership and reports it as failed, and the next push tries again.
    """
    from app.services.secret_manager import clear_cache, delete_secret

    try:
        await delete_secret(key_name)
    except Exception:
        logger.warning("Could not fully withdraw a host-provided credential")
    try:
        clear_cache(key_name=key_name)
        from app.api.system import _invalidate_process_caches

        _invalidate_process_caches(key_name)
    except Exception:
        pass
    try:
        return not await _is_configured(db, key_name)
    except Exception:
        # Cannot confirm it is gone. Same direction as everywhere else on this
        # page: assume the credential is still there, keep it on the host's
        # books, and say so.
        logger.warning("Could not confirm withdrawal of a host-provided credential")
        return False


async def apply(db, secrets: Optional[Dict[str, str]]) -> Dict[str, List[str]]:
    """Apply a host's complete current set of shared credentials.

    Full replace: what arrives is everything the host is sharing, so anything
    the host used to provide and did not send this time is withdrawn.

    Returns {"accepted", "refused", "revoked", "failed", "db_only"} -- sorted
    key names, and only names. Nothing here returns, logs or records a value.
    `failed` is the withdrawal that did not take: the credential is still live,
    it is still the host's, and the next push will try again. `db_only` is the
    write that landed in the encrypted database copy but not in the external
    secret store, which is a working credential today and a surprise the day
    the database copy is scrubbed.
    """
    from app.api.system import _persist_secret

    incoming = {
        str(k).strip(): v
        for k, v in (secrets or {}).items()
        if isinstance(k, str) and str(k).strip()
    }
    allowed = shareable_keys()
    previously: List[str] = await host_provided(db)
    owned = set(previously)

    accepted: List[str] = []
    refused: List[str] = []
    # Keys the host did NOT already own, claimed just before their write, whose
    # write then failed. Subtracted again at the end -- see the claim below for
    # why they are claimed first at all.
    #
    # Only keys that were new to the host go in here, and that restriction is
    # the whole of the fix for a bug that stranded credentials permanently. A
    # re-push of a key the host already owned used to land here too on any
    # transient vault error, and the key was then dropped from the ownership
    # record while the host's PREVIOUS value stayed live in the town's vault.
    # Every push after that saw a key the host does not own with a value
    # against it, refused it (the guard below), and the host could neither
    # replace nor withdraw it -- one blip, stranded forever. A key already on
    # the host's books stays on them when a re-write fails: the value that is
    # live is still the host's, so the record still says so, and the next push
    # simply tries the write again.
    unclaim: List[str] = []
    # Keys whose value reached the encrypted database copy but NOT the external
    # secret store -- `_persist_secret` returning False. Every other caller of
    # it surfaces this, because it is the ordering trap where a credential
    # saved before the store was reachable lives only in the database and
    # nobody finds out. Reported so the host can see it and re-push.
    db_only: List[str] = []

    for key in sorted(incoming):
        raw = incoming[key]
        if not isinstance(raw, str):
            # The request model types this Dict[str, str], so this is a
            # defensive second line rather than the expected path. It is here
            # because the first line used to be `dict`: a number or a nested
            # object reached `.strip()`, raised AttributeError halfway through
            # the loop, and returned 500 -- after some keys had already been
            # written and committed. Refused per key, like every other bad
            # input on this endpoint, so the rest of the batch still lands.
            refused.append(key)
            continue
        value = raw.strip()
        if key not in allowed:
            # Unknown, platform-owned or BACKUP_-prefixed. Refused in the body
            # rather than by a 4xx: one bad key in a batch must not throw away
            # the good ones, and the host needs to see which key it was.
            refused.append(key)
            continue
        if not value:
            # The contract says every shared key arrives with a value, so a
            # blank one is a mistake at the other end. Refusing is the
            # conservative reading: writing it would clear a live credential,
            # and revocation already has an unambiguous spelling (leave the key
            # out entirely).
            refused.append(key)
            continue
        if key not in owned:
            try:
                taken = await _is_configured(db, key)
            except Exception:
                # Cannot tell whether the town has one. Refuse: overwriting a
                # town's own credential is the one outcome this endpoint may
                # never produce by accident.
                logger.warning("Could not establish ownership of a pushed credential; refusing it")
                refused.append(key)
                continue
            if taken:
                refused.append(key)
                continue
        # Ownership BEFORE the value, in the transaction the value commits in.
        # `_persist_secret` commits each key as it writes it, and the ownership
        # record used to be written once at the end -- so a failure partway
        # through the batch left keys written into the town's vault with no
        # record that the host put them there. The host could then neither
        # replace nor withdraw them, because the next push sees a key it does
        # not own with a value against it and refuses it: permanently stuck
        # credentials, and the more keys in the batch the likelier it got.
        #
        # Claiming first is the safe half of the race. A claim whose write then
        # fails leaves a key marked host-provided with nothing stored, which
        # the next push simply writes; the reverse leaves a live credential
        # nobody can manage.
        await _store(db, add=[key])
        try:
            stored_externally = await _persist_secret(db, key, value, from_host=True)
        except Exception:
            logger.warning("Could not store a host-provided credential")
            refused.append(key)
            if key not in previously:
                # New key, nothing of the host's behind it: releasing the claim
                # leaves the row exactly as it was. A key the host ALREADY
                # owned keeps its claim -- see `unclaim` above for what
                # dropping it cost.
                unclaim.append(key)
            continue
        if not stored_externally:
            db_only.append(key)
        accepted.append(key)

    withdrawn: List[str] = []
    failed: List[str] = []
    # The same allowlist the write path uses, applied to the revoke path. It was
    # missing here, and the asymmetry is not academic: `allowed` is derived from
    # the provider catalogs, so a provider leaving a catalog or a key becoming
    # platform-managed shrinks it between one push and the next. A key stranded
    # on the wrong side of that change was deleted -- vault and database both --
    # because it looked like a withdrawal, when nothing had been withdrawn and
    # the town was simply running a build whose catalogs no longer name the key.
    # Deleting a live credential on the strength of a catalog edit is the one
    # direction that cannot be undone from here, so a key the host owns but may
    # no longer be given is left alone AND left on the host's books: the claim
    # is what lets the host withdraw it deliberately if the key ever comes back.
    #
    # Note what is deliberately NOT revoked on the other side of the same
    # asymmetry: a key that arrived in this payload and was REFUSED. Refusal
    # says the write could not happen -- the town owns the key, the value was
    # blank, the vault errored -- and none of those is the host saying "stop
    # sharing this". Revocation has exactly one spelling in a full replace, and
    # it is absence from the payload. A refused key is present, so it stays.
    for key in sorted(owned - set(incoming)):
        if key not in allowed:
            logger.warning(
                "Keeping a host-provided credential that is no longer shareable "
                "rather than deleting it"
            )
            continue
        if await _revoke(db, key):
            withdrawn.append(key)
        else:
            # Still resolves. Keep it on the host's books: an unmanageable live
            # credential is worse than one that is still listed as shared.
            failed.append(key)

    # Drops only. Every accepted key claimed itself at the moment it was
    # written, so re-adding the batch here added nothing except a race: a key a
    # town admin claimed with `forget` partway through this loop was handed
    # straight back to the host by this line, and the next push overwrote the
    # value the clerk had just typed. What this call is for is the two
    # subtractions, which nothing else has applied yet.
    await _store(db, drop=withdrawn + unclaim)
    await db.commit()

    return {
        "accepted": sorted(accepted),
        "refused": sorted(refused),
        "revoked": withdrawn,
        "failed": failed,
        # Written, but only to the encrypted database copy -- the external
        # store did not take it. Additive, like `failed` before it: a caller
        # reading only the older keys is unaffected.
        "db_only": sorted(db_only),
    }


async def field_provenance(db, stored_fields: Dict[str, bool]) -> Dict[str, bool]:
    """{credential key: did the host supply this one}, for the admin UI.

    True only when the key is the host's AND there is actually a value stored,
    so a box the host once filled and has since withdrawn does not keep
    claiming a provider it no longer has. Presence and ownership; no values.

    Empty on failure, which reads on screen as an ordinary town-entered
    credential -- the safe direction for a note that is only ever explanatory.
    """
    try:
        owned = set(await host_provided(db))
    except Exception:
        return {}
    return {key: (key in owned and bool(stored)) for key, stored in (stored_fields or {}).items()}


def sorted_names(keys: Sequence[str]) -> List[str]:
    """Names only, sorted -- the shape every response and audit entry uses."""
    return sorted({k for k in keys if k})
