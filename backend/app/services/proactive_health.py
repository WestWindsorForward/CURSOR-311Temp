"""Proactive (leading-indicator) health checks.

The point of this module is to warn *before* something fails, not just report
that it already did. It evaluates capacity/resource signals that predict an
outage on a small self-hosted deployment — disk filling up, memory pressure,
database connections nearing the limit, backups going stale, Redis memory — and
classifies each against a WARNING and a CRITICAL threshold.

Three consumers:
  - Admins get the full detail (which check, the number, and a suggested action)
    in the operations panel, alongside the restart/diagnose runbooks.
  - Non-technical staff get a single plain-language rollup ("All systems normal"
    / "Minor issue" / "Service issue").
  - A scheduled task emails admins when a check crosses into warning/critical, so
    problems surface early instead of after downtime.

The threshold classification is pure and unit-tested; the metric collectors do
I/O and are each defensive (a failing probe degrades to "unknown", never raises).
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Severity ordering for rollups. "unknown" is treated as non-actionable (0) so a
# probe we couldn't run never escalates the overall status on its own.
_SEVERITY = {"ok": 0, "unknown": 0, "warning": 1, "critical": 2}


def classify_metric(
    value: Optional[float],
    warn: float,
    crit: float,
    *,
    higher_is_worse: bool = True,
) -> str:
    """Classify a numeric metric against warn/crit thresholds.

    Returns "ok" | "warning" | "critical", or "unknown" if value is None.
    """
    if value is None:
        return "unknown"
    if higher_is_worse:
        if value >= crit:
            return "critical"
        if value >= warn:
            return "warning"
    else:
        if value <= crit:
            return "critical"
        if value <= warn:
            return "warning"
    return "ok"


def rollup_status(checks: List[Dict[str, Any]]) -> str:
    """Worst status across all checks -> overall status."""
    worst = "ok"
    for c in checks:
        if _SEVERITY.get(c.get("status"), 0) > _SEVERITY.get(worst, 0):
            worst = c["status"]
    return worst


def clerk_summary(overall: str) -> Dict[str, str]:
    """Plain-language rollup for non-technical staff — no numbers, no jargon."""
    table = {
        "ok": {
            "level": "ok",
            "label": "All systems normal",
            "detail": "Residents can submit and track requests normally.",
        },
        "warning": {
            "level": "warning",
            "label": "Minor issue — IT notified",
            "detail": "The system is running normally, but staff have been alerted to look into something.",
        },
        "critical": {
            "level": "critical",
            "label": "Service issue — IT notified",
            "detail": "Something needs attention and IT has been alerted. Some features may be affected.",
        },
    }
    return table.get(overall, table["ok"])


def is_worse(new_status: str, old_status: Optional[str]) -> bool:
    """True if new_status is more severe than old_status (for alert de-duping)."""
    return _SEVERITY.get(new_status, 0) > _SEVERITY.get(old_status or "ok", 0)


def escalations(
    checks: List[Dict[str, Any]], previous: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Which checks just got worse and are worth emailing about.

    A muted check is one an admin has already said "I know" about, so it does
    not page anyone -- but note that `evaluate` still returns it, at its real
    status. The mute is applied here, at the mail, and nowhere else.
    """
    return [
        c for c in checks
        if c.get("status") in ("warning", "critical")
        and not c.get("muted")
        and is_worse(c["status"], previous.get(c.get("key")))
    ]


def next_alert_state(
    checks: List[Dict[str, Any]], previous: Dict[str, Any]
) -> Dict[str, Any]:
    """The per-check state to persist after a scan.

    Normally the new status, so a recovery resets the de-duping and the next
    failure is news again. The exception is a muted check that is still bad,
    which keeps whatever state it had: a mute *defers* an alert, it does not
    cancel it. Recording "critical" while nobody was told would mean that when
    the mute expires the check is no longer worse than last time, and the mail
    nobody ever received would never arrive at all.
    """
    return {
        c["key"]: (
            previous.get(c["key"])
            if c.get("muted") and c.get("status") in ("warning", "critical")
            else c.get("status")
        )
        for c in checks
    }


# --------------------------------------------------------------------------- #
# Metric collectors — each returns a check dict and never raises.
# --------------------------------------------------------------------------- #

def _check(key: str, label: str, status: str, value, message: str, action: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "status": status, "value": value, "message": message, "action": action}


def _disk_check() -> Dict[str, Any]:
    """Every filesystem this deployment writes to, not just the root one.

    `shutil.disk_usage("/")` reads whatever is behind the container's root. On a
    default Docker install that is the host disk, so it was usually right by
    accident. Photos (`uploads_data`) and pre-migration database dumps
    (`migration_backups`) are named volumes, and a town that puts either on a
    second disk was having the wrong one watched -- the root filesystem stays
    comfortable while the volume taking a photo per report fills.
    """
    try:
        from app.services.system_probes import read_disks, worst_disk

        worst = worst_disk(read_disks())
        if not worst:
            return _check("disk", "Disk space", "unknown", None, "Could not read disk usage.")
        pct = worst["percent"]
        status = classify_metric(pct, warn=80, crit=92)
        free_gb = round(worst["free"] / (1024 ** 3), 1)
        where = worst.get("label") or worst.get("path")
        return _check(
            "disk", "Disk space", status, pct,
            f"{where} is {pct}% full ({free_gb} GB free).",
            "Delete old backups/logs or expand the volume before it fills." if status != "ok" else "",
        )
    except Exception as e:
        logger.warning(f"[proactive] disk check failed: {e}")
        return _check("disk", "Disk space", "unknown", None, "Could not read disk usage.")


def _memory_check() -> Dict[str, Any]:
    """The limit that kills this container, not the RAM in the machine.

    `/proc/meminfo` is the host's -- it is not namespaced, so a container reads
    the whole server's memory no matter how small its own cap. compose caps the
    backend at 1G; on a 32GB server, a backend at 990MB and one allocation away
    from being OOM-killed mid-report reported 3% used and a green tick. The
    cgroup limit is read first and the host is only the fallback, for a
    deployment that genuinely runs uncapped.
    """
    try:
        from app.services.system_probes import read_memory

        reading = read_memory()
        pct = reading["percent"]
        if pct is None:
            return _check("memory", "Memory", "unknown", None, "Could not read memory usage.")
        status = classify_metric(pct, warn=85, crit=95)
        limit_gb = round((reading["limit_bytes"] or 0) / (1024 ** 3), 2)
        scope = ("this container's limit" if reading["scope"] == "container"
                 else "the server's RAM")
        return _check(
            "memory", "Memory", status, pct,
            f"Memory is {pct}% of {scope} ({limit_gb} GB).",
            "Raise the container's memory limit or reduce what is running; at 100% it is "
            "killed and restarted." if status != "ok" else "",
        )
    except Exception as e:
        logger.warning(f"[proactive] memory check failed: {e}")
        return _check("memory", "Memory", "unknown", None, "Could not read memory usage.")


async def _db_connection_check(db) -> Dict[str, Any]:
    try:
        from sqlalchemy import text
        used = (await db.execute(text("SELECT count(*) FROM pg_stat_activity"))).scalar()
        max_conn = (await db.execute(text("SHOW max_connections"))).scalar()
        max_conn = int(max_conn)
        pct = round(used / max_conn * 100, 1) if max_conn else None
        status = classify_metric(pct, warn=75, crit=90)
        return _check(
            "db_connections", "Database connections", status, pct,
            f"{used} of {max_conn} connections in use ({pct}%).",
            "Check for connection leaks or raise the pool/limit before it's exhausted." if status != "ok" else "",
        )
    except Exception as e:
        logger.warning(f"[proactive] db connection check failed: {e}")
        return _check("db_connections", "Database connections", "unknown", None, "Could not read connection stats.")


async def _backup_age_check() -> Dict[str, Any]:
    try:
        from datetime import datetime, timezone
        from app.services.backup_service import get_backup_status
        status_info = await get_backup_status()
        last = status_info.get("last_backup") if isinstance(status_info, dict) else None
        if not last or not last.get("created_at"):
            return _check(
                "backup", "Backup freshness", "warning", None,
                "No database backup found yet.",
                "Run a backup now so a fresh restore point exists.",
            )
        created = last["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "").replace("+00:00", ""))
        hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        status = classify_metric(round(hours, 1), warn=36, crit=72)
        return _check(
            "backup", "Backup freshness", status, round(hours, 1),
            f"Last backup was {round(hours)}h ago.",
            "Backups are stale — verify the backup task is running." if status != "ok" else "",
        )
    except Exception as e:
        logger.warning(f"[proactive] backup age check failed: {e}")
        return _check("backup", "Backup freshness", "unknown", None, "Could not read backup status.")


async def _redis_check() -> Dict[str, Any]:
    try:
        import redis.asyncio as aioredis
        url = os.getenv("REDIS_URL") or "redis://redis:6379/0"
        client = aioredis.from_url(url, socket_timeout=3)
        try:
            info = await client.info("memory")
        finally:
            await client.aclose()
        used = info.get("used_memory", 0)
        max_mem = info.get("maxmemory", 0)
        used_mb = round(used / (1024 ** 2), 1)
        if not max_mem:
            # No cap configured — report healthy with the current footprint.
            return _check("redis", "Cache (Redis)", "ok", used_mb, f"Redis using {used_mb} MB (no cap set).")
        pct = round(used / max_mem * 100, 1)
        status = classify_metric(pct, warn=80, crit=92)
        return _check(
            "redis", "Cache (Redis)", status, pct,
            f"Redis memory at {pct}% of its cap ({used_mb} MB).",
            "Raise maxmemory or review the eviction policy before keys are evicted." if status != "ok" else "",
        )
    except Exception as e:
        logger.warning(f"[proactive] redis check failed: {e}")
        return _check("redis", "Cache (Redis)", "unknown", None, "Could not reach Redis.")


async def _kms_check() -> Dict[str, Any]:
    """Is resident data still being encrypted with the key the town chose?

    This is the only check here whose failure is not recoverable by fixing it
    later, which is why it is critical rather than warning.

    A cloud KMS key that stops answering -- scheduled for deletion, permissions
    revoked, credentials expired -- does not raise anything. `_wrap_dek` falls
    back to the application key and carries on: new reports save fine, and the
    rows written under the old key quietly stop being readable. The gap can run
    for weeks, and if the cause was a scheduled deletion, the window to cancel
    it closes inside that time. After that the data is gone for good.

    So this compares what the town selected against what actually wrapped the
    data key just now. Those agreeing is the only evidence that the arrangement
    still works; a settings page showing a key name proves nothing, because the
    key name is still there when the key is not.
    """
    try:
        from app.core import pii_crypto
        from app.core.encryption import _kms_provider

        selected = _kms_provider()
        if selected not in ("google", "azure", "aws"):
            # No cloud KMS chosen. Encrypting with the application key is then
            # the configured behaviour, not a fault.
            return _check("kms", "Resident data encryption", "ok", "local",
                          "Resident data is encrypted with the application key, as configured.")

        # A live wrap, not `active_backend()`. That reads the data key this
        # process cached at startup, so a worker that has been running since
        # before the key broke would answer with the state of the world an
        # arbitrary time ago -- and would keep saying "ok" through the entire
        # deletion window, which is the one stretch where saying otherwise
        # matters. Wrapping a throwaway key costs one KMS call every fifteen
        # minutes and answers the question as of now.
        actual = pii_crypto.probe_backend()
        if actual == selected:
            return _check("kms", "Resident data encryption", "ok", actual,
                          f"Resident data is being encrypted with your {actual} key.")

        return _check(
            "kms", "Resident data encryption", "critical", actual,
            f"Your {selected} key is not being used — new resident data is being "
            f"encrypted with the application key instead.",
            action=(
                "Act today. Check that the key still exists and has not been scheduled "
                "for deletion, and that its credentials have not expired. Records saved "
                "before this started may already be unreadable, and if a deletion is "
                "pending it can only be cancelled inside the waiting period."
            ),
        )
    except Exception:
        logger.debug("KMS health probe failed", exc_info=True)
        return _check("kms", "Resident data encryption", "unknown", None,
                      "Could not determine which key is encrypting resident data.")


async def _redaction_check() -> Dict[str, Any]:
    """Is the detector a town chose the one actually blurring its photos?

    Redaction fails more quietly than anything else here. A cloud detector with
    no credentials returns an empty result, and so does a photo of an empty
    street -- the two are the same value. Photos get stored, the card stays
    green, and nobody finds out until a resident's face is on the public map.

    The dispatch now degrades to on-server detection rather than to nothing, so
    the harm is contained. But degraded is still not what the town selected: it
    is paying for Azure and getting OpenCV, which finds fewer faces. That is
    worth a warning rather than silence.
    """
    try:
        from app.services.image_redaction import (
            _usable, effective_provider, resolve_provider,
        )

        selected = await resolve_provider()
        if not selected:
            return _check("redaction", "Photo redaction", "ok", "off",
                          "Photo redaction is switched off, as configured.")

        actual, degraded_from = await effective_provider(selected)

        if degraded_from == actual:
            return _check(
                "redaction", "Photo redaction", "critical", "none",
                "No detector is available — resident photos are being stored "
                "without blurring faces or licence plates.",
                action=(
                    "Check the Photo Redaction card. On-server detection needs no "
                    "account and works anywhere, so this usually means the image is "
                    "missing OpenCV rather than that a credential is wrong."
                ),
            )

        if degraded_from:
            return _check(
                "redaction", "Photo redaction", "warning", actual,
                f"Blurring is running on this server because {degraded_from} has no "
                f"usable credentials. Photos are still redacted, less accurately.",
                action=(
                    f"Fix the {degraded_from} credentials on the Photo Redaction card, "
                    f"or choose on-server detection so the page matches what is running."
                ),
            )

        return _check("redaction", "Photo redaction", "ok", actual,
                      f"Faces and plates are being blurred using {actual}.")
    except Exception:
        logger.debug("Redaction health probe failed", exc_info=True)
        return _check("redaction", "Photo redaction", "unknown", None,
                      "Could not determine which detector is redacting photos.")


async def _cpu_check(window: float = 0.4) -> Dict[str, Any]:
    """CPU, as a share of what this container is allowed rather than the host's.

    Reported, not alerted on: one short sample cannot tell a PDF being rendered
    from a server in trouble, and an email a day about a spike is an email
    nobody reads. It is here because "the site feels slow" is a real report and
    this is the number that answers it.
    """
    try:
        import asyncio

        from app.services.system_probes import (
            cpu_percent, read_cpu_allowance, read_cpu_seconds,
        )

        cores = read_cpu_allowance()
        before = read_cpu_seconds()
        if before is None:
            return _check("cpu", "CPU", "unknown", None, "Could not read CPU usage.")
        await asyncio.sleep(window)
        after = read_cpu_seconds()
        pct = cpu_percent(
            None if after is None else after - before, window, cores)
        if pct is None:
            return _check("cpu", "CPU", "unknown", None, "Could not read CPU usage.")
        allowance = f"{cores:g} core{'s' if cores and cores != 1 else ''}" if cores else "the server's cores"
        return _check("cpu", "CPU", "ok", pct,
                      f"CPU was {pct}% of {allowance} over the last {window:g}s.")
    except Exception as e:
        logger.warning(f"[proactive] cpu check failed: {e}")
        return _check("cpu", "CPU", "unknown", None, "Could not read CPU usage.")


async def _retention_check(db) -> Dict[str, Any]:
    """Has this town set a retention period, and said what a run removes?

    This belongs here rather than only on the compliance tab because the
    unconfigured state is silent everywhere else and does not resolve itself.
    Nothing is archived or deleted until both halves are set, which means every
    name, phone number and free-text description a resident has ever submitted
    is still on the record — and a town's own published privacy policy usually
    says otherwise. Data minimisation is an obligation in its own right; the
    absence of a policy is a live condition, not a blank field.

    Warning rather than critical, and the distinction is about direction. Nothing
    is being destroyed while this is unset, so it is recoverable — a town can
    configure it tomorrow and the records are all still there. Contrast
    `_kms_check`, which is critical because its failure window closes on data
    that cannot be got back. But it stays a warning permanently, and says what
    is actually happening rather than "not configured".
    """
    try:
        from app.services.retention_config import load_retention_config

        config = await load_retention_config(db)
        if config.configured:
            days = config.retention_days
            years = days / 365
            period = f"{years:.1f} years".replace(".0 ", " ") if days >= 365 else f"{days} days"
            return _check("retention", "Records retention", "ok", f"{days} days",
                          f"Closed requests are kept for {period}, then "
                          f"{'every field is cleared' if config.mode == 'purge' else 'the chosen fields are cleared'} "
                          f"({len(config.scrub_fields or [])} selected). One period for all "
                          f"records, set by this town.")

        return _check(
            "retention", "Records retention", "warning", None,
            config.detail or "No records-retention schedule is configured.",
            action=(
                "Open Settings → Compliance → Document Retention and set how long closed "
                "requests are kept, and what a run removes from them. Your clerk has the "
                "town's approved records retention schedule; this product will not guess "
                "at it. Until both are set, resident personal data is kept indefinitely."
            ),
        )
    except Exception:
        logger.debug("Retention health probe failed", exc_info=True)
        return _check("retention", "Records retention", "unknown", None,
                      "Could not determine which retention schedule is in force.")


# Every key `collect_checks` can emit. Named here so the mute route can refuse
# a key that is not a real check -- muting is a write, and without an allowlist
# any admin request could insert arbitrary rows into the health table.
CHECK_KEYS = (
    "disk",
    "memory",
    "cpu",
    "db_connections",
    "backup",
    "redis",
    "kms",
    "redaction",
    "retention",
)


async def collect_checks(db) -> List[Dict[str, Any]]:
    """Run all proactive checks. Never raises; failed probes return 'unknown'."""
    checks = [
        _disk_check(),
        _memory_check(),
        await _cpu_check(),
        await _db_connection_check(db),
        await _backup_age_check(),
        await _redis_check(),
        await _kms_check(),
        await _redaction_check(),
        await _retention_check(db),
    ]
    return checks


async def evaluate(db) -> Dict[str, Any]:
    """Full proactive-health evaluation for the API/alerting layers."""
    from datetime import datetime, timezone
    checks = await collect_checks(db)

    # Mute state rides along; it does not filter. `overall_status` below is
    # computed from the real statuses, and a muted check keeps its own -- so
    # the panel still shows the problem, marked muted, and only the mail
    # stops. A mute that dropped the row would be a dismiss button that
    # deletes evidence, which is the failure this subsystem exists to prevent.
    try:
        from app.services import health_mutes

        await health_mutes.annotate(db, checks)
    except Exception:
        logger.debug("Could not read health mutes", exc_info=True)
        for c in checks:
            c.setdefault("muted", False)
            c.setdefault("muted_until", None)

    overall = rollup_status(checks)
    return {
        "overall_status": overall,
        "summary": clerk_summary(overall),
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
