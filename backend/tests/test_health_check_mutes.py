"""Muting a proactive health check.

The connector cards have had "I know about this one" for a while. The proactive
checks -- disk, backups, Redis, retention -- did not, and they are the ones that
mail every fifteen minutes. This covers the extension, and specifically the
three properties that make a mute honest rather than a delete:

  1. It silences the mail and nothing else. The check keeps its real status and
     is still returned by the health endpoint, marked muted.
  2. An escalation breaks through it. A mute taken while something was merely at
     risk must not buy silence over an outage -- this rides
     `connector_alerts.muted()` precisely so that rule is not re-implemented and
     re-broken here.
  3. It defers rather than cancels. A muted check that is still bad does not
     advance the persisted alert state, so when the mute expires the alert
     nobody received still fires.
"""

from datetime import datetime, timedelta, timezone

import pytest

# The submodule, not the package: `app` and `backend/alembic` resolve as
# namespace packages, so importing the top name can succeed while nothing
# underneath it is importable. See tests/test_migrate.py for the same trap.
pytest.importorskip("fastapi.routing")
pytest.importorskip("sqlalchemy.orm")

from app.services import connector_alerts as alerts  # noqa: E402
from app.services import health_mutes  # noqa: E402

NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=3)


class FakeRow:
    def __init__(self, connector, until=None, level=None):
        self.connector = connector
        self.alert_muted_until = until
        self.alert_muted_level = level


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_kw):
        return FakeResult(self._rows)


# --------------------------------------------------------------------------- #
# Naming: these rows are bookkeeping, not connectors.
# --------------------------------------------------------------------------- #

def test_health_rows_are_namespaced_and_round_trip():
    assert health_mutes.mute_key("disk") == "health:disk"
    assert health_mutes.check_key("health:disk") == "disk"


def test_a_real_connector_is_not_mistaken_for_a_health_row():
    # If it were, the setup page would grow a phantom card for every mute, and
    # the connector digest would start mailing about "disk".
    assert health_mutes.is_health_row("health:backup") is True
    assert health_mutes.is_health_row("sms") is False
    assert health_mutes.is_health_row("system:backups") is False
    assert health_mutes.check_key("sms") is None
    assert health_mutes.check_key("health:") is None


# --------------------------------------------------------------------------- #
# Severity: mapped onto the alert ladder so `muted()` can compare them.
# --------------------------------------------------------------------------- #

def test_check_severity_maps_onto_the_alert_ladder():
    assert health_mutes.alert_level("critical") == alerts.BROKEN
    assert health_mutes.alert_level("warning") == alerts.AT_RISK
    assert health_mutes.alert_level("ok") == alerts.HEALTHY
    # A probe we could not run is not a problem we have observed.
    assert health_mutes.alert_level("unknown") == alerts.HEALTHY


# --------------------------------------------------------------------------- #
# The mute itself.
# --------------------------------------------------------------------------- #

def test_a_live_mute_silences_the_check():
    assert health_mutes.muted_now("warning", LATER, alerts.AT_RISK, now=NOW) is True


def test_an_expired_mute_silences_nothing():
    assert health_mutes.muted_now("warning", NOW - timedelta(minutes=1),
                                  alerts.AT_RISK, now=NOW) is False


def test_no_mute_at_all_silences_nothing():
    assert health_mutes.muted_now("critical", None, None, now=NOW) is False


def test_an_escalation_breaks_through_a_mute_taken_at_a_lower_severity():
    # Acknowledging a disk at 84% must not buy a week of silence over a disk at
    # 95%. This is the whole reason for reusing connector_alerts.muted().
    assert health_mutes.muted_now("critical", LATER, alerts.AT_RISK, now=NOW) is False
    # Same deadline, same acknowledgement, at the level it was taken at.
    assert health_mutes.muted_now("warning", LATER, alerts.AT_RISK, now=NOW) is True


def test_a_passing_check_is_never_reported_as_muted():
    # A muted badge on a green row would make the mute look broader than it is.
    assert health_mutes.muted_now("ok", LATER, alerts.BROKEN, now=NOW) is False


# --------------------------------------------------------------------------- #
# annotate(): adds mute state, changes nothing else.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_annotate_marks_the_muted_check_and_leaves_its_status_alone():
    db = FakeDB([FakeRow("health:disk", LATER, alerts.AT_RISK)])
    checks = [
        {"key": "disk", "status": "warning", "label": "Disk space"},
        {"key": "backup", "status": "critical", "label": "Backup freshness"},
    ]
    out = await health_mutes.annotate(db, checks, now=NOW)

    disk, backup = out
    assert disk["muted"] is True
    assert disk["muted_until"] == LATER.isoformat()
    # Still a warning. Muting hides the noise, not the truth.
    assert disk["status"] == "warning"
    # An unmuted check is untouched.
    assert backup["muted"] is False
    assert backup["muted_until"] is None
    assert backup["status"] == "critical"


@pytest.mark.asyncio
async def test_annotate_never_drops_a_check():
    db = FakeDB([FakeRow("health:disk", LATER, alerts.BROKEN)])
    checks = [{"key": "disk", "status": "critical", "label": "Disk space"}]
    out = await health_mutes.annotate(db, checks, now=NOW)
    assert len(out) == 1 and out[0]["key"] == "disk"


@pytest.mark.asyncio
async def test_an_unreadable_mute_table_means_no_mutes_not_no_alerts():
    class Broken:
        async def execute(self, *_a, **_kw):
            raise RuntimeError("no such column: alert_muted_until")

    checks = [{"key": "disk", "status": "critical", "label": "Disk space"}]
    out = await health_mutes.annotate(Broken(), checks, now=NOW)
    # Erring towards alerting is the safe direction: a deployment mid-migration
    # should be noisy, not silent.
    assert out[0]["muted"] is False


# --------------------------------------------------------------------------- #
# The scan: muted checks do not page, and do not lose their place in the queue.
# --------------------------------------------------------------------------- #

from app.services.proactive_health import (  # noqa: E402
    escalations as _escalations,
    next_alert_state as _next_state,
)


def test_a_muted_check_does_not_page_anyone():
    checks = [{"key": "disk", "status": "critical", "muted": True}]
    assert _escalations(checks, {}) == []


def test_an_unmuted_check_still_pages():
    checks = [{"key": "disk", "status": "critical", "muted": False}]
    assert [c["key"] for c in _escalations(checks, {})] == ["disk"]


def test_a_mute_defers_the_alert_rather_than_cancelling_it():
    # Muted and bad: the persisted state must not move on. If it recorded
    # "critical" while nobody was told, then when the mute lifts the check is no
    # longer worse than last time and the mail nobody received never arrives.
    prev = {"disk": "ok"}
    checks = [{"key": "disk", "status": "critical", "muted": True}]
    state = _next_state(checks, prev)
    assert state["disk"] == "ok"

    # Mute expires; same reading; now it pages.
    checks_after = [{"key": "disk", "status": "critical", "muted": False}]
    assert [c["key"] for c in _escalations(checks_after, state)] == ["disk"]


def test_a_muted_check_that_recovered_does_reset_its_state():
    prev = {"disk": "critical"}
    checks = [{"key": "disk", "status": "ok", "muted": False}]
    assert _next_state(checks, prev)["disk"] == "ok"


# --------------------------------------------------------------------------- #
# The route only accepts real checks.
# --------------------------------------------------------------------------- #

def test_every_check_the_collector_emits_is_mutable():
    """The allowlist and the collector must not drift.

    A key missing from CHECK_KEYS is a check with a mute button that 404s; a key
    in CHECK_KEYS that nothing emits is a row nobody can ever clear.
    """
    import ast
    import pathlib

    from app.services.proactive_health import CHECK_KEYS

    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "proactive_health.py"
    tree = ast.parse(src.read_text())
    emitted = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "_check"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert emitted == set(CHECK_KEYS), f"drifted: {emitted ^ set(CHECK_KEYS)}"
