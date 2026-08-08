"""The daily sweep tests what is configured, and records what it finds.

"The credentials are stored" is a fact about our own database and stays true
forever. "The credentials work" is a fact about somebody else's service and
stops being true without anyone here doing anything -- a secret expires, a card
lapses, a departing employee's key is revoked.

Before this, the only ways to find out were an admin pressing Test at a moment
of their choosing, or a resident reporting that no email ever arrived.

The three behaviours worth pinning, because each is a way the sweep could be
actively worse than nothing:

  * an unconfigured capability is left alone, so a town that never set up text
    messages does not get an amber badge on something it switched off;
  * "cannot be checked from here" is not recorded as a failure, because a red
    badge that can never go green teaches people to ignore badges;
  * a check that raises is recorded rather than aborting the sweep, so one
    broken provider does not hide the state of the other seven.
"""

import asyncio

import pytest

# Deliberately no importorskip. The sweep's logic lives in a service that
# imports neither FastAPI nor Celery, precisely so these run in CI -- which
# installs four packages and would otherwise skip the whole file.
from app.services.connector_verification import verify_all


class FakeHealth:
    """Stands in for connector_health, recording what it was told."""

    def __init__(self):
        self.successes = []
        self.failures = []
        # Which provider each result was attributed to. The column has existed
        # since the table did and only the circuit breaker ever filled it, so
        # every row the sweep wrote said NULL -- and a verdict with no provider
        # against it is a verdict about whatever happened to be selected when
        # it ran, which nothing recorded.
        self.providers = []

    async def record_success(self, db, connector, provider=None, detail=None):
        self.successes.append(connector)
        self.providers.append((connector, provider))
        self.details = getattr(self, "details", []) + [(connector, detail)]

    async def record_failure(self, db, connector, error, provider=None):
        self.failures.append((connector, str(error)))
        self.providers.append((connector, provider))

    async def snapshot(self, db):
        return {}


class FakeAlerts:
    """Stands in for connector_alerts, recording that it was asked to send."""

    def __init__(self):
        self.calls = []

    async def dispatch(self, db, *, healths, **kwargs):
        self.calls.append(list(healths))
        return {"sent": bool(healths)}


@pytest.fixture
def sweep():
    """Drive verify_all with a chosen set of checks and configured capabilities."""
    health = FakeHealth()

    def run(checks, configured, providers=None):
        async def is_configured(capability):
            return capability in configured

        async def provider_of(capability):
            return (providers or {}).get(capability)

        summary = asyncio.run(verify_all(
            None, checks=checks, is_configured=is_configured,
            provider_of=provider_of, health=health, alerts=FakeAlerts()))
        return summary, health

    return run


def ok(detail="fine"):
    async def check(db=None):
        return {"ok": True, "detail": detail}
    return check


def bad(detail="the key was rejected"):
    async def check(db=None):
        return {"ok": False, "detail": detail}
    return check


def unverifiable(detail="cannot be checked from here"):
    async def check(db=None):
        return {"ok": False, "detail": detail, "recorded": False}
    return check


def explodes(message="boom"):
    async def check(db=None):
        raise RuntimeError(message)
    return check


def test_a_working_connector_is_recorded_as_working(sweep):
    result, health = sweep({"maps": ok()}, configured={"maps"})
    assert result["checked"]["maps"] == "working"
    assert health.successes == ["maps"]
    assert health.failures == []
    assert result["failing"] == []


def test_a_broken_connector_is_recorded_with_the_providers_own_words(sweep):
    """A clerk searching the web for their error needs the real string, not our
    paraphrase of it."""
    result, health = sweep({"email": bad("535 authentication failed")}, configured={"email"})
    assert result["checked"]["email"] == "failing"
    assert result["failing"] == ["email"]
    assert health.failures == [("email", "535 authentication failed")]


def test_an_unconfigured_capability_is_left_alone(sweep):
    """The badge-noise case. Testing something a town deliberately switched off
    writes a failure, and a page full of amber badges for things nobody wanted
    is a page nobody reads."""
    called = []

    async def check(db=None):
        called.append(True)
        return {"ok": False, "detail": "not configured"}

    result, health = sweep({"sms": check}, configured=set())
    assert result["checked"]["sms"] == "not-configured"
    assert called == [], "the sweep tested a capability nobody configured"
    assert health.failures == []
    assert result["failing"] == []


def test_cannot_check_from_here_is_not_a_failure(sweep):
    """Apple MapKit, ACS and a generic HTTP gateway genuinely cannot be verified
    from the server. Recording those as failures produces a red badge that can
    never go green, whatever the town does."""
    result, health = sweep({"maps": unverifiable()}, configured={"maps"})
    assert result["checked"]["maps"] == "unverifiable"
    assert health.failures == []
    assert health.successes == []
    assert result["failing"] == []


def test_one_exploding_check_does_not_hide_the_others(sweep):
    """A sweep that aborts on the first raise reports nothing about the seven
    connectors after it, which is the state it was meant to replace."""
    result, health = sweep(
        {"ai": explodes("provider SDK blew up"), "maps": ok(), "email": bad()},
        configured={"ai", "maps", "email"},
    )
    assert result["checked"] == {"ai": "error", "maps": "working", "email": "failing"}
    assert result["failing"] == ["ai", "email"]
    assert health.successes == ["maps"]
    assert any("provider SDK blew up" in err for _, err in health.failures)


def test_an_unanswerable_configured_check_still_gets_tested():
    """If we cannot tell whether something is configured, test it. A missed
    check is worse than a redundant one, and the sweep must not propagate the
    error either way -- it runs unattended."""
    health = FakeHealth()

    async def cannot_tell(capability):
        raise RuntimeError("cannot reach the secret store")

    result = asyncio.run(verify_all(
        None, checks={"ai": explodes("provider SDK blew up")},
        is_configured=cannot_tell, health=health))
    assert result["checked"]["ai"] == "error"
    assert health.failures


def test_it_is_registered_to_run_daily():
    """A task nothing schedules is a task that never runs, and the failure mode
    is silence -- which is indistinguishable from everything being fine."""
    pytest.importorskip("celery")
    from app.core.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    entry = schedule.get("daily-connector-check")
    assert entry, f"not scheduled; have {sorted(schedule)}"
    assert entry["task"] == "app.tasks.connector_checks.verify_connectors"
    # Wall-clock, not an interval: an interval counts from worker boot, so a
    # deploy cadence faster than daily meant the sweep never ran.
    from celery.schedules import crontab
    assert isinstance(entry["schedule"], crontab)
    assert "app.tasks.connector_checks" in celery_app.conf.include


# ---------------------------------------------------------------------------
# Finding out is only half of it
# ---------------------------------------------------------------------------

def test_the_sweep_hands_its_findings_to_the_alerting():
    """The original failure mode survived this whole subsystem for a while: the
    sweep wrote a row and a log line and waited for an administrator to open
    the settings page, which for a town where setup is finished may be months.
    """
    health = FakeHealth()
    alerts = FakeAlerts()

    async def is_configured(capability):
        return True

    result = asyncio.run(verify_all(
        None, checks={"identity": bad("client secret expired")},
        is_configured=is_configured, health=health, alerts=alerts))

    assert result["failing"] == ["identity"]
    assert len(alerts.calls) == 1, "the sweep found a failure and told nobody"
    assert "alerted" in result


def test_alerting_that_explodes_cannot_take_the_sweep_with_it():
    """The health table is the record. Bookkeeping about who was emailed must
    never turn a completed sweep into a failed one."""
    class Exploding:
        async def dispatch(self, db, **kwargs):
            raise RuntimeError("smtp is on fire")

    health = FakeHealth()

    async def is_configured(capability):
        return True

    result = asyncio.run(verify_all(
        None, checks={"maps": ok()}, is_configured=is_configured,
        health=health, alerts=Exploding()))

    assert result["checked"]["maps"] == "working"
    assert health.successes == ["maps"]
    assert result["alerted"]["sent"] is False


# ---------------------------------------------------------------------------
# Which provider a result is about
# ---------------------------------------------------------------------------
#
# `connector_health` has a provider column and it was NULL on every row: only
# the circuit breaker ever passed one. A verdict is only true of the provider
# that produced it, so a row without one is a result about an unrecorded
# vendor -- and the setup page was rendering those as the state of whatever is
# selected now. Live, the text messages card read "There is no way to check
# http without sending a real text" while SMS_PROVIDER was 'acs'.


def test_a_result_is_recorded_against_the_provider_that_produced_it(sweep):
    _, health = sweep({"sms": ok()}, configured={"sms"}, providers={"sms": "twilio"})
    assert health.providers == [("sms", "twilio")]


def test_a_failure_names_its_provider_too(sweep):
    """The direction that matters most: a red card has to say whose red it is,
    or switching provider looks like it did not help."""
    _, health = sweep({"email": bad()}, configured={"email"}, providers={"email": "ses"})
    assert health.providers == [("email", "ses")]


def test_a_provider_lookup_that_fails_does_not_lose_the_result(sweep):
    """Not knowing which vendor is not a reason to throw away a real verdict."""
    health = FakeHealth()

    async def is_configured(capability):
        return True

    async def explode(capability):
        raise RuntimeError("cannot reach the secret store")

    result = asyncio.run(verify_all(
        None, checks={"maps": ok()}, is_configured=is_configured,
        provider_of=explode, health=health, alerts=FakeAlerts()))

    assert result["checked"]["maps"] == "working"
    assert health.providers == [("maps", None)]


def test_the_sweep_still_runs_without_the_api_package(sweep):
    """`provider_of` is injected for the same reason the checks are: this module
    imports neither FastAPI nor Celery so that it runs in CI. Falling back to
    the real resolver must not make that untrue."""
    health = FakeHealth()

    async def is_configured(capability):
        return True

    result = asyncio.run(verify_all(
        None, checks={"maps": ok()}, is_configured=is_configured,
        health=health, alerts=FakeAlerts()))
    assert result["checked"]["maps"] == "working"


def test_a_switched_off_connector_does_not_keep_emailing(monkeypatch):
    """A connector switched off after it started failing keeps its failing
    health row -- the sweep no longer runs it, so the counters never reset --
    and that frozen row must not generate a reminder email forever. There is
    no card for a switched-off capability, so there is no mute button either;
    the switch itself has to be the thing that stops the mail."""
    from types import SimpleNamespace

    from app.services import capability_switches
    from app.services.connector_verification import notify

    rows = {
        "sms": SimpleNamespace(connector="sms"),
        "email": SimpleNamespace(connector="email"),
    }

    class SnapshotHealth(FakeHealth):
        async def snapshot(self, db):
            return rows

    async def enabled(capability):
        return capability != "sms"

    monkeypatch.setattr(capability_switches, "enabled", enabled)

    alerts = FakeAlerts()
    asyncio.run(notify(None, health=SnapshotHealth(), alerts=alerts))

    dispatched = [h.connector for h in alerts.calls[0]]
    assert dispatched == ["email"]


def test_a_broken_switch_lookup_still_alerts(monkeypatch):
    """Doubt resolves toward alerting: if the switches cannot be read, every
    row goes to dispatch rather than none of them."""
    from types import SimpleNamespace

    from app.services import capability_switches
    from app.services.connector_verification import notify

    class SnapshotHealth(FakeHealth):
        async def snapshot(self, db):
            return {"sms": SimpleNamespace(connector="sms")}

    async def explode(capability):
        raise RuntimeError("settings table unreadable")

    monkeypatch.setattr(capability_switches, "enabled", explode)

    alerts = FakeAlerts()
    asyncio.run(notify(None, health=SnapshotHealth(), alerts=alerts))

    assert [h.connector for h in alerts.calls[0]] == ["sms"]


def test_the_probe_alert_hook_gets_the_dispatcher_signature():
    """probe_system routes its `alerts` callable through notify, which calls
    it as a dispatcher: `healths` supplied, town and settings link alongside.
    So the callable the celery task wires in is `connector_alerts.dispatch`
    itself, and this pins the shape it is called with -- the original wiring
    called dispatch as `alerts(db)` and raised TypeError on the email step of
    every hourly probe, so the full disk was recorded, the dashboard showed
    it, and the email the task exists to send never was."""
    from app.services.connector_verification import probe_system

    calls = []

    async def alerts(db, *, healths, **kwargs):
        calls.append(list(healths))
        return {"sent": False}

    health = FakeHealth()
    result = asyncio.run(probe_system(
        None, readings={"disk": {"ok": False, "detail": "98% full"}},
        health=health, alerts=alerts))

    assert len(calls) == 1, "the probe recorded a failure and told nobody"
    assert result["probes"]["disk"] == "failing"


def test_a_disconnected_town_system_does_not_keep_emailing(monkeypatch):
    """Disabling or deleting a govtech integration stops the sweep testing it,
    so its health row freezes red (or drifts to stale, which alerts as
    at-risk). There is no card for a deleted integration and therefore no mute
    button; the off switch itself has to stop the mail. Only rows for
    currently-enabled integrations may reach dispatch."""
    from types import SimpleNamespace

    from app.services import connector_verification as cv

    rows = {
        "govtech:accela": SimpleNamespace(connector="govtech:accela"),
        "govtech:open311": SimpleNamespace(connector="govtech:open311"),
        "email": SimpleNamespace(connector="email"),
    }

    class SnapshotHealth(FakeHealth):
        async def snapshot(self, db):
            return rows

    async def enabled_integrations(db):
        return [SimpleNamespace(platform="open311")]

    monkeypatch.setattr(cv, "_enabled_integrations", enabled_integrations)

    # Pin the capability switches on, like the neighbouring tests: this test
    # is about the govtech filter, and the legacy EMAIL_ENABLED opt-in would
    # otherwise swallow the email row in an environment with no database.
    from app.services import capability_switches

    async def everything_on(capability):
        return True

    monkeypatch.setattr(capability_switches, "enabled", everything_on)

    alerts = FakeAlerts()
    asyncio.run(cv.notify(None, health=SnapshotHealth(), alerts=alerts))

    dispatched = sorted(h.connector for h in alerts.calls[0])
    assert dispatched == ["email", "govtech:open311"]
