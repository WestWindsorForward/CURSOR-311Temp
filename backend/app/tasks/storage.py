"""Scheduled storage hygiene: vaulting secrets and re-wrapping PII.

These replace two buttons on the setup page. See
`app.services.storage_maintenance` for why both are safe to run unattended --
in short, vaulting verifies a read-back before it deletes anything, and PII on
an older key is still readable, so neither job is load-bearing at the moment it
runs.

Both are written to be dull: no retries, no chaining, no partial state carried
between runs. If a pass does nothing because a store was briefly unreachable,
the next pass does it.
"""

import asyncio
import logging

from app.core.celery_app import celery_app
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _run(coro):
    """Run one coroutine in a loop of its own, the way every Celery task does.

    The loop is closed on the way out, so a connection that outlived it would be
    a connection bound to a dead loop -- which is what the worker's NullPool
    engine (`app.db.session.use_null_pool`) exists to make impossible. The
    dispose here is the belt to those braces; see
    `app.tasks.service_requests.run_async`.
    """
    from app.db.session import engine

    async def _runner():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_runner())


@celery_app.task(name="app.tasks.storage.vault_secrets")
def vault_secrets():
    """Hourly: move any secret still held in the database into the store.

    Hourly rather than nightly because the window it closes is the one right
    after somebody finishes setup -- credentials entered before the cloud
    account was connected sit in the database until this runs, and "by tomorrow"
    is a long time for that to be true of a town's Twilio token.

    The result's `status` comes from `storage_maintenance.migration_status`, and
    the case that matters for a job on an hourly timer is the quiet one: a pass
    with nothing to move says `ok`, not `partial_failure`. See that function.
    """
    from app.services.storage_maintenance import vault_secrets as _vault

    try:
        result = _run(_vault())
        logger.info(
            "[storage] secret vaulting pass: %s (%s)",
            result.get("status"), result.get("reason", "no detail"),
        )
        return result
    except Exception as exc:
        logger.warning("[storage] secret vaulting pass failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="app.tasks.storage.rewrap_pii")
def rewrap_pii():
    """Nightly: move PII onto the current key, a batch at a time.

    Keeps going within one run while there is more to do, up to a ceiling, so a
    town that has just connected a KMS converts in a few nights rather than a
    few hundred. The ceiling exists so this cannot become an all-night job on a
    large database; whatever is left is picked up tomorrow.
    """
    from app.services.storage_maintenance import rewrap_pii as _rewrap

    MAX_BATCHES = 20

    async def _go():
        totals = {"status": "ok", "rows": 0, "fields": 0, "errors": 0, "batches": 0}
        async with SessionLocal() as db:
            for _ in range(MAX_BATCHES):
                result = await _rewrap(db)
                totals["rows"] += result.get("rows", 0)
                totals["fields"] += result.get("fields", 0)
                totals["errors"] += result.get("errors", 0)
                totals["batches"] += 1
                if result.get("status") != "ok" or not result.get("remaining"):
                    break
        return totals

    try:
        return _run(_go())
    except Exception as exc:
        logger.warning("[storage] PII re-wrap pass failed: %s", exc)
        return {"status": "error", "error": str(exc)}
