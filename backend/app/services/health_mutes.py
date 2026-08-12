"""Muting for the proactive health checks, on the connector mute mechanism.

The proactive checks (disk, backups, Redis, retention...) email admins from the
`proactive_health_scan` task. Until now they had no "I know about this one"
switch: a town deliberately running close to a disk threshold, or one that has
decided it does not want off-site backups yet, got the same mail every time a
check crossed a line, and the only way to stop it was to stop running the scan
-- which also stops the checks nobody has acknowledged.

These mutes ride the mechanism that already exists rather than a second one.
A `connector_health` row keyed `health:<check>` carries the same
`alert_muted_until` / `alert_muted_level` columns, is set through the same
`POST /api/system/connectors/{connector}/mute` route, writes the same
`connector_alerts_muted` / `connector_alerts_unmuted` audit events, and is
read through the same `connector_alerts.muted()` rule. That last one is the
reason not to invent a parallel mechanism: `muted()` already knows that a mute
taken while something was merely *at risk* must not silence it once it is
broken, and a second implementation would have had to re-earn that and would
probably have got it wrong.

Two things these rows are deliberately not:

  - They are not connectors. `connector_health_report` and the connector
    digest filter them out (`is_health_row`), so muting "disk" does not put a
    phantom card on the Setup & Integrations page.
  - They are not a way to make a check look fine. `annotate` adds `muted` and
    `muted_until` to each check and changes nothing else; the status stays
    whatever the probe said and the overall rollup still counts it. Muting
    hides the noise, not the truth.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Namespace for the bookkeeping rows. Kept distinct from `system:` (which is
# real infrastructure probes that *are* connectors and do earn cards).
PREFIX = "health:"


def mute_key(check_key: str) -> str:
    """The connector_health row name that carries this check's mute."""
    return f"{PREFIX}{check_key}"


def check_key(connector: str) -> Optional[str]:
    """The proactive check a row name refers to, or None if it is not one."""
    if not connector or not connector.startswith(PREFIX):
        return None
    return connector[len(PREFIX):] or None


def is_health_row(connector: str) -> bool:
    """Whether this connector_health row is a health-check mute, not a connector."""
    return bool(connector) and connector.startswith(PREFIX)


def alert_level(status: Optional[str]) -> str:
    """Map a proactive check status onto the alert severity ladder.

    The ladder is `connector_alerts`' own, so that `muted()` can compare a
    mute taken against a warning with an escalation to critical and let the
    escalation through. `unknown` is not an alert -- a probe we could not run
    is not a problem we have observed.
    """
    from app.services import connector_alerts as alerts

    if status == "critical":
        return alerts.BROKEN
    if status == "warning":
        return alerts.AT_RISK
    return alerts.HEALTHY


async def load(db) -> Dict[str, Tuple[Optional[datetime], Optional[str]]]:
    """Every health-check mute on record, as {check_key: (until, level)}.

    Never raises. A deployment that has not run the mute migration, or has no
    connector_health table at all, has no mutes -- which is the safe answer,
    because it errs towards alerting.
    """
    try:
        from sqlalchemy import select

        from app.models import ConnectorHealth

        rows = (await db.execute(
            select(ConnectorHealth).where(ConnectorHealth.connector.like(f"{PREFIX}%"))
        )).scalars().all()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not read health mutes: %s", exc)
        return {}

    out: Dict[str, Tuple[Optional[datetime], Optional[str]]] = {}
    for row in rows:
        key = check_key(getattr(row, "connector", "") or "")
        if not key:
            continue
        out[key] = (
            getattr(row, "alert_muted_until", None),
            getattr(row, "alert_muted_level", None),
        )
    return out


def muted_now(
    status: Optional[str],
    until: Optional[datetime],
    level: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Whether this check's alerts are currently silenced, at this severity."""
    from app.services import connector_alerts as alerts

    lvl = alert_level(status)
    if lvl == alerts.HEALTHY:
        # Nothing to silence. Reporting a healthy check as "muted" would put a
        # muted badge on a green row and make the mute look broader than it is.
        return False
    return alerts.muted(
        level=lvl,
        muted_until=until,
        muted_level=level,
        now=now or datetime.now(timezone.utc),
    )


async def annotate(
    db, checks: List[Dict[str, Any]], *, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Add `muted` and `muted_until` to each check. Changes nothing else.

    In particular it does not touch `status`, so the rollup, the badge and the
    "needs attention" list all keep telling the truth about a muted check --
    it simply stops sending mail about it.
    """
    now = now or datetime.now(timezone.utc)
    mutes = await load(db)
    for c in checks:
        until, level = mutes.get(c.get("key"), (None, None))
        is_muted = muted_now(c.get("status"), until, level, now=now)
        c["muted"] = is_muted
        c["muted_until"] = until.isoformat() if (is_muted and until) else None
    return checks
