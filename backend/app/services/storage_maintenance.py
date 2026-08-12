"""Keeping stored data on the storage a town has chosen, without being asked.

Two maintenance jobs used to be buttons on the setup page: "Vault Local Secrets
to GCP Identity" and "Re-encrypt All PII Data (after key rotation)". Both are
real work. Neither is work a clerk can be expected to recognise the need for --
the second one is parenthetically conditioned on a key rotation, which is an
event nobody who reads that sentence has knowingly performed.

Both are also safe to run unprompted, which is the part that makes this
possible:

  * Vaulting writes every secret to the configured store, reads each one back to
    confirm it landed, and only then clears the database copy -- and never
    clears one of `DB_REQUIRED_KEYS`, whose readers cannot see the store. A run
    with nothing to move is a no-op; a run against an unreachable store stops
    before it deletes anything.

  * Re-wrapping is hygiene rather than a repair. A `pii2:` value carries the
    wrapped data key that produced it, and `pii_crypto._dek_for` unwraps by the
    tag in its first byte, so a row wrapped by last year's key still decrypts
    today. Rewriting it under the current key is worth doing -- it is what lets
    a town retire an old key, and what moves rows that silently fell back to the
    application key while a KMS was unreachable -- but nothing is broken while
    it is pending, so it can take as long as it takes.

So they run on a schedule instead, in batches, idempotently. The setup page
keeps a status line for the case where something is genuinely stuck, and no
buttons.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many rows one re-wrap pass touches. Each row is four AES operations and no
# KMS traffic at all -- the data key is unwrapped once and cached -- so this is
# bounded by the database round trip. Kept modest anyway: the job runs nightly
# and finishing next week is an acceptable outcome for hygiene work.
REWRAP_BATCH = 500


def _pii_columns():
    from app.models import ServiceRequest
    return (
        ServiceRequest._first_name_encrypted,
        ServiceRequest._last_name_encrypted,
        ServiceRequest._email_encrypted,
        ServiceRequest._phone_encrypted,
    )


def store_reachable() -> bool:
    """Whether there is an external secret store to move credentials into.

    False for the encrypted database, and that is not a failure. It is where the
    credentials already are, so there is nothing for `vault_secrets` to move and
    nothing for it to scrub -- which is the behaviour a town that chose the
    database asked for.

    False, too, when no store has been chosen. Sweeping credentials into a store
    nobody picked is the accidental-default this pass removes.
    """
    try:
        from app.services.secret_manager import _is_gcp_available, _secrets_provider

        provider = _secrets_provider()
        if provider in ("", "database"):
            return False
        if provider == "azure":
            from app.core import azure_keyvault
            return azure_keyvault.is_configured()
        if provider == "aws":
            from app.core import aws_secretsmanager
            return aws_secretsmanager.is_configured()
        return _is_gcp_available()
    except Exception as exc:
        logger.debug("storage maintenance: store reachability check failed: %s", exc)
        return False


def migration_status(
    *, verified: int, failed: int, skipped: int, unreadable: int = 0
) -> Dict[str, str]:
    """What a vaulting pass should call itself, given what it did.

    Split out and made pure because the version this replaces got the quiet case
    wrong, and got it wrong hourly. It read `"success" if verified else
    "partial_failure"`, so a pass with nothing to do -- every configured
    credential either a bootstrap key that must stay in the database, or already
    moved and scrubbed on some earlier run -- reported `partial_failure` with
    `failed: 0` and an empty `failed_keys`. That is the *steady state* of a
    healthy deployment: production has been reporting a partial failure of
    nothing every hour since the job was scheduled, which is how a status line
    stops being read.

    Nothing failed unless something failed:

      * something went wrong and nothing was verified -- the pass did not work;
      * something went wrong and something was verified -- genuinely partial;
      * something was verified -- success;
      * none of the above -- there was nothing to do, which is not a failure and
        not really a success either. `ok`, with a reason saying so.

    `unreadable` is counted with the failures and named separately, because it
    is the one that would otherwise hide in the quiet case. A secret encrypted
    under a SECRET_KEY this process no longer has decrypts to nothing, and the
    first version of this filed that under `skipped` -- so a stuck credential
    was reported as "already in the store or held in the database by design",
    which is a false statement about a real problem rather than a vague one.
    It also names its own fix, which `failed` does not: the key it was
    encrypted under.
    """
    problems = []
    if failed:
        problems.append(f"{failed} secret(s) could not be vaulted")
    if unreadable:
        problems.append(
            f"{unreadable} secret(s) could not be decrypted and were left in the "
            "database (encrypted under a previous SECRET_KEY)"
        )

    if problems and not verified:
        return {"status": "failure", "reason": "; ".join(problems)}
    if problems:
        return {
            "status": "partial_failure",
            "reason": f"{verified} secret(s) vaulted; " + "; ".join(problems),
        }
    if verified:
        return {"status": "success", "reason": f"{verified} secret(s) vaulted"}
    return {
        "status": "ok",
        "reason": (
            f"nothing to vault: {skipped} configured secret(s) are already in the "
            "store or are held in the database by design"
        ),
    }


async def vault_secrets(force: bool = False) -> Dict[str, Any]:
    """Move any database-held secrets into the configured store.

    Returns the migration summary, or a `skipped` result when there is no store
    to move them to. Never raises: this is called from a scheduled task and from
    a fire-and-forget hook after a provider is saved, and neither has anywhere
    useful to report an exception to.
    """
    try:
        if not force and not store_reachable():
            return {"status": "skipped", "reason": "no secret store configured", "migrated": 0}

        from app.services.secret_manager import migrate_to_secret_manager

        result = await migrate_to_secret_manager()
        moved = result.get("scrubbed") or 0
        if moved:
            logger.info("Vaulted %s secrets into the configured secret store", moved)
        return result
    except Exception as exc:
        logger.warning("Automatic secret vaulting failed (will retry): %s", exc)
        return {"status": "error", "error": str(exc), "migrated": 0}


async def _stale_segments(db, column, current: str) -> List[str]:
    """The distinct wrapped-data-key segments in `column` not on the current key.

    Every row encrypted by one process shares one wrapped-key string, so this is
    a handful of values however many rows there are -- which is what makes it
    affordable to ask the question before doing any work.
    """
    from sqlalchemy import func, select

    from app.services.storage_status import WRAP_TAGS

    segment = func.split_part(column, ":", 2)
    rows = (await db.execute(
        select(segment).where(column.isnot(None)).group_by(segment)
    )).all()

    stale = []
    for (wrapped_b64,) in rows:
        if not wrapped_b64:
            continue  # Not a pii2 value; handled by the legacy clause below.
        try:
            tag = WRAP_TAGS.get(base64.b64decode(wrapped_b64 + "==")[:1])
        except Exception:
            tag = None
        if tag != current:
            stale.append(wrapped_b64)
    return stale


def rewrap_value(value: Optional[str]) -> Optional[str]:
    """The re-encrypted form of one stored value, or None to leave it alone.

    Returns None in three cases that must all behave identically from the
    caller's side: nothing stored, nothing changed, and -- the one that matters
    -- the value could not be decrypted. A row whose wrapping key is gone is the
    only way this code can destroy data, and it would do so by writing an
    encryption of the empty string over the last copy of somebody's phone
    number. So an undecryptable value is left exactly as it is, on the chance
    that the key comes back.

    Split out from the batch loop so that guarantee can be tested without a
    database.
    """
    if not value:
        return None

    from app.core.encryption import decrypt_pii, encrypt_pii

    plaintext = decrypt_pii(value)
    if not plaintext:
        raise ValueError("value could not be decrypted; leaving it untouched")
    replacement = encrypt_pii(plaintext)
    return replacement if replacement != value else None


async def rewrap_pii(db, limit: int = REWRAP_BATCH) -> Dict[str, Any]:
    """Re-encrypt up to `limit` rows that are not on the current key.

    Selects only rows that need it, so a town with nothing outstanding runs one
    cheap query and stops. Safe to run repeatedly and safe to interrupt: each
    row is independent, and a row left unconverted is still readable.
    """
    out: Dict[str, Any] = {"status": "ok", "rows": 0, "fields": 0, "errors": 0, "remaining": 0}
    try:
        from sqlalchemy import func, or_, select

        from app.core.encryption import PII_V2_PREFIX, _kms_provider
        from app.models import ServiceRequest

        current = _kms_provider()
        columns = _pii_columns()

        clauses = []
        for column in columns:
            stale = await _stale_segments(db, column, current)
            # Either it predates the envelope format, or its wrapped key is one
            # of the stale ones. Both re-encrypt the same way.
            column_clause = [column.isnot(None) & ~column.like(f"{PII_V2_PREFIX}%")]
            if stale:
                column_clause.append(func.split_part(column, ":", 2).in_(stale))
            clauses.append(or_(*column_clause))

        rows = (await db.execute(
            select(ServiceRequest).where(or_(*clauses)).order_by(ServiceRequest.id).limit(limit)
        )).scalars().all()

        if not rows:
            return out

        for row in rows:
            touched = False
            for column in columns:
                name = column.key
                try:
                    replacement = rewrap_value(getattr(row, name, None))
                except Exception as exc:
                    logger.debug("re-wrap failed for request %s %s: %s", row.id, name, exc)
                    out["errors"] += 1
                    continue
                if replacement is not None:
                    setattr(row, name, replacement)
                    out["fields"] += 1
                    touched = True
            if touched:
                out["rows"] += 1

        await db.commit()

        if out["rows"]:
            logger.info("Re-wrapped %s requests (%s fields) onto the current key",
                        out["rows"], out["fields"])

        # Whether to come back sooner than the next nightly run.
        out["remaining"] = limit if len(rows) == limit else 0
    except Exception as exc:
        logger.warning("Automatic PII re-wrap failed (will retry): %s", exc)
        out["status"] = "error"
        out["error"] = str(exc)
    return out


def generated_backup_key() -> str:
    """A backup passphrase, so nobody has to invent one.

    The old field asked a town to make up a passphrase for AES-256 backup
    encryption, which produces either something guessable or something lost. The
    one thing that cannot be automated here is keeping a copy somewhere other
    than this server: a key held only inside the system it protects is no key at
    all once that system is gone. So this generates it, and the page shows it
    once and asks for an acknowledgement that it has been written down.
    """
    import secrets

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
