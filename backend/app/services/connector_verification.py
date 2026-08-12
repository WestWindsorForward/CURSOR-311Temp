"""Test every configured connector, and record what happened.

The setup page could always answer "are the credentials stored". That is a
question about our own database, and it stays green forever. Whether the
credentials still *work* is a question about somebody else's service, and the
answer changes without anyone here doing anything: a client secret expires, a
card on file lapses, a departing employee's key is revoked, a vendor tightens a
scope.

Until now the only ways to learn that were an admin opening the settings page
and pressing Test -- at a moment chosen by the person least likely to be
surprised by the answer -- or a resident reporting that no email ever arrived.

Three things this deliberately does not do, each of which is a way a health
sweep can be worse than none at all:

  * It does not test what is not configured. A town that never set up text
    messages has not made a mistake, and an amber badge on something switched
    off is the noise that teaches people to ignore badges.
  * It does not record "cannot be checked from here" as a failure. Apple
    MapKit, ACS and a generic HTTP gateway genuinely cannot be verified from
    the server, and a red badge that can never go green is worse than none.
  * It does not stop at the first check that raises. Aborting there would leave
    the other seven connectors unreported, which is the state this replaces.

Nothing here sends anything to a resident: the email and text checks
authenticate and query rather than delivering a message.

The checks and the is-it-configured predicate are injected rather than imported
at module scope. That keeps this importable -- and therefore testable -- without
FastAPI or Celery, neither of which CI installs.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from app.core.sanitize import sanitize_for_log

logger = logging.getLogger(__name__)

Check = Callable[..., Awaitable[Dict[str, Any]]]


async def verify_all(
    db,
    *,
    checks: Optional[Mapping[str, Check]] = None,
    is_configured: Optional[Callable[[str], Awaitable[bool]]] = None,
    provider_of: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
    health=None,
    alerts=None,
    integrations=None,
) -> Dict[str, Any]:
    """Run each capability's live check. Returns a summary; never raises."""
    if checks is None or is_configured is None:
        from app.api.system import _CAPABILITY_TESTS, capability_is_configured
        checks = checks if checks is not None else _CAPABILITY_TESTS
        is_configured = is_configured or capability_is_configured
    if health is None:
        from app.services import connector_health as health

    async def _provider(capability: str) -> Optional[str]:
        """Which provider this verdict is about.

        Recorded with every result, because a verdict is only true of the
        provider that produced it. The column has existed since the table did
        and only the circuit breaker ever filled it, so a switch from one
        provider to another left yesterday's answer on the card with nothing to
        say it was about yesterday's vendor.

        Resolved here rather than at the top, and swallowed: like the checks
        themselves this is injected so the module stays importable without
        FastAPI, and a sweep must not fail to record a real result because it
        could not name the vendor that produced it.
        """
        try:
            resolve = provider_of
            if resolve is None:
                from app.api.system import effective_provider_for as resolve
            return await resolve(capability)
        except Exception:
            return None

    checked: Dict[str, str] = {}
    for capability, check in checks.items():
        try:
            if not await is_configured(capability):
                checked[capability] = "not-configured"
                continue
        except Exception:
            # If we cannot tell, test it. A missed check is worse than a
            # redundant one.
            pass

        provider = await _provider(capability)

        try:
            outcome = await check(db)
        except Exception as exc:
            # The provider's own words. A clerk searching the web for their
            # error needs the real string, not our paraphrase of it.
            await health.record_failure(db, capability, str(exc)[:300], provider=provider)
            checked[capability] = "error"
            logger.info("[Health] %s raised during the daily check: %s",
                        sanitize_for_log(capability), sanitize_for_log(str(exc)[:200]))
            continue

        if outcome.get("recorded") is False:
            # Nothing written, as before. "We cannot check this from here" is
            # not evidence either way, and the sweep runs unattended -- writing
            # it here would let a nightly pass mark a connector unverifiable
            # over a verdict an administrator had already got by hand.
            checked[capability] = "unverifiable"
        elif outcome.get("ok"):
            # The message too, not only the timestamp. The manual Test button
            # stores what the check did (the translated word, the wrapped-key
            # byte count); a nightly pass that wiped it back to nothing made
            # the card less transparent every morning.
            await health.record_success(db, capability, provider=provider,
                                        detail=outcome.get("detail"))
            checked[capability] = "working"
        else:
            await health.record_failure(db, capability, outcome.get("detail", ""), provider=provider)
            checked[capability] = "failing"

    # The govtech integrations a town connected itself. Same table, same
    # escalation, same mute -- they were the one group of connectors nothing
    # ever swept, so a health row appeared only when a resident happened to
    # file a report, and `FRESH_FOR` (three days, justified by "there is now a
    # sweep that actively tests every configured connector once a day") was
    # counting quiet days as evidence of decay. Three quiet days emailed the
    # town that Accela may stop working when nothing was wrong with it.
    checked.update(await verify_integrations(
        db, integrations=integrations, health=health))

    failing = sorted(k for k, v in checked.items() if v in ("failing", "error"))
    if failing:
        logger.warning("[Health] daily connector check found problems: %s",
                       sanitize_for_log(", ".join(failing)))

    # Having found out, tell somebody.
    #
    # Writing the result to a table and a log line and then waiting for an
    # administrator to open the settings page is how the original failure mode
    # survived this whole subsystem: the software knew on Tuesday and the town
    # found out on Monday, from a resident.
    alerted = await notify(db, health=health, alerts=alerts)
    return {"checked": checked, "failing": failing, "alerted": alerted}


# The name a govtech integration reports health under. Shared with the push
# paths in tasks/integrations.py and the admin Test button, so one connector has
# one row rather than one per code path that happens to call it.
def health_key(platform: str) -> str:
    return f"govtech:{platform}"


async def verify_integrations(db, *, integrations=None, build=None, guard=None,
                              health=None) -> Dict[str, str]:
    """Test every enabled govtech integration. Returns {name: outcome}.

    Disabled integrations are skipped, for the same reason the capability sweep
    skips what is not configured: an amber badge on a connection a town turned
    off is the noise that teaches people to ignore badges.

    Everything is injected -- the rows, the connector factory, the breaker --
    so this runs in CI, which has neither a database nor FastAPI.
    """
    if health is None:
        from app.services import connector_health as health
    if build is None:
        from app.integrations import build_connector_for as build
    if guard is None:
        from app.services.circuit_breaker import guard
    from app.services.circuit_breaker import CircuitOpen

    if integrations is None:
        integrations = await _enabled_integrations(db)

    checked: Dict[str, str] = {}
    for integration in integrations:
        platform = getattr(integration, "platform", None) or "?"
        name = health_key(platform)
        # Building is recorded separately from calling, because `guard` writes
        # the health row for a call that failed and cannot know about one that
        # never happened. Recording both here double-counted the failure and
        # pushed a connector to "down" in two sweeps instead of three.
        try:
            connector = await build(integration)
        except Exception as exc:
            # A missing base_url or an unresolvable vault reference never
            # reaches the vendor, and is just as much a reason this does not
            # work as a rejected password.
            await health.record_failure(db, name, str(exc)[:300], provider=platform)
            checked[name] = "error"
            logger.info("[Health] %s could not be built for the daily check: %s",
                        sanitize_for_log(name), sanitize_for_log(str(exc)[:200]))
            continue

        try:
            # Through the breaker with `db` supplied, so a sweep records the
            # outcome exactly like a resident-report push does and a vendor that
            # is already known to be down is not called again on our schedule.
            result = await guard(name, connector.test_connection, db=db,
                                 provider=platform)
        except CircuitOpen:
            # Deliberately not recorded. The failures that opened the circuit are
            # already in the row; adding one per sweep for a call we declined to
            # make would inflate the count with no new evidence behind it.
            checked[name] = "paused"
            continue
        except Exception as exc:
            checked[name] = "error"
            logger.info("[Health] %s raised during the daily check: %s",
                        sanitize_for_log(name), sanitize_for_log(str(exc)[:200]))
            continue

        if isinstance(result, dict) and result.get("verified") is False:
            # Reachable, but nothing proved the credential -- an Open311 server
            # that answers /services.json to anybody, for instance. Recorded as
            # unverifiable so the card can say so instead of showing a green
            # tick earned by an anonymous request.
            await health.record_unverifiable(
                db, name, str(result.get("detail") or "")[:500], provider=platform)
            checked[name] = "unverifiable"
        else:
            checked[name] = "working"
    return checked


async def check_integration_now(db, integration, *, build=None, guard=None,
                                health=None, breaker=None) -> Dict[str, Any]:
    """Run one integration's connection check on an admin's behalf.

    Differs from the sweep in two ways, both because a person is waiting on it:

      * the circuit breaker is cleared rather than obeyed. Somebody pressing
        Test has usually just changed a credential, and refusing the call would
        leave them staring at a cooldown earned by the broken one -- which is
        precisely the case `Breaker.reset` was documented for and had no callers
        outside its own tests.
      * a pass clears the cooldown as well, so the next queued report is
        attempted immediately instead of waiting out a penalty the fix already
        resolved.

    Returns the connector's own result dict, or an `{ok: False, detail}` one.
    """
    if health is None:
        from app.services import connector_health as health
    if build is None:
        from app.integrations import build_connector_for as build
    if guard is None:
        from app.services.circuit_breaker import guard
    if breaker is None:
        from app.services.circuit_breaker import breaker
    from app.services.circuit_breaker import CircuitOpen

    platform = getattr(integration, "platform", None) or "?"
    name = health_key(platform)

    # Building is guarded separately from calling, for the same reason as in the
    # sweep: `guard` writes the health row for a call that failed, so recording
    # it again here would count one rejected password twice and take the
    # connector to "down" a sweep early.
    try:
        connector = await build(integration)
    except Exception as exc:
        await health.record_failure(db, name, str(exc)[:300], provider=platform)
        return {"ok": False, "detail": str(exc)}

    try:
        result = await guard(name, connector.test_connection, db=db, provider=platform)
    except CircuitOpen:
        breaker.reset(name)
        try:
            result = await guard(name, connector.test_connection, db=db, provider=platform)
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}

    breaker.reset(name)
    if isinstance(result, dict) and result.get("verified") is False:
        await health.record_unverifiable(
            db, name, str(result.get("detail") or "")[:500], provider=platform)
    return result if isinstance(result, dict) else {"ok": True, "detail": "OK"}


async def _enabled_integrations(db):
    """The enabled IntegrationConfig rows. Never raises -- a sweep that cannot
    read them still has seven capabilities to report on."""
    try:
        from sqlalchemy import select

        from app.models import IntegrationConfig

        result = await db.execute(
            select(IntegrationConfig).where(IntegrationConfig.enabled.is_(True))
        )
        return list(result.scalars().all())
    except Exception as exc:
        logger.warning("[Health] could not list integrations to check: %s",
                       sanitize_for_log(str(exc)[:300]))
        return []


async def notify(db, *, health=None, alerts=None, dispatch=None) -> Dict[str, Any]:
    """Send the digest for whatever the sweep just recorded. Never raises.

    `alerts` is the alerting *module* (it is looked up for `.dispatch`);
    `dispatch` is the dispatcher callable itself, for callers that already hold
    one. Both spellings are accepted because both are already injected: the
    sweep hands in the module, the hourly probe hands its `alerts` callable on
    as `dispatch`. Guessing which one arrived is how the probe's alert path
    came to call `dispatch(db)` with no `healths` and raise every time.
    """
    try:
        if health is None:
            from app.services import connector_health as health
        if dispatch is None:
            if alerts is None:
                from app.services import connector_alerts as alerts
            dispatch = alerts if callable(alerts) else alerts.dispatch

        snapshot = await health.snapshot(db)
        # A connector switched off *after* it started failing keeps its red
        # health row -- honest history -- but must not keep emailing about it.
        # The sweep skips switched-off capabilities, so their failure counters
        # never reset, and without this filter the digest would carry a daily
        # reminder about a service the town has said it is not using -- with no
        # card, and therefore no mute button, to stop it.
        off = await _switched_off(list(snapshot.keys()))
        # Govtech rows have the same frozen-row problem and their own off
        # switch: disabling or disconnecting an integration stops the sweep
        # testing it, so a red govtech:accela row (or a healthy one going
        # stale after three untested days) would email reminders forever --
        # and a deleted integration has no card, hence no mute button. Only
        # rows for currently-enabled integrations may alert.
        enabled_keys = {health_key(i.platform) for i in await _enabled_integrations(db)}
        # `health:` rows are not connectors at all -- they exist only to carry
        # the mute for a proactive health check, which has its own alerting
        # task. Left in, they would arrive here as connectors nothing has ever
        # verified and earn a digest line each sweep.
        from app.services import health_mutes
        healths = [h for h in snapshot.values()
                   if h.connector not in off
                   and not health_mutes.is_health_row(h.connector)
                   and (not h.connector.startswith("govtech:") or h.connector in enabled_keys)]
        return await dispatch(
            db,
            healths=healths,
            town=await _town_name(db),
            settings_url=await _settings_url(db),
        )
    except Exception as exc:
        logger.warning("[Health] could not send connector alerts: %s",
                       sanitize_for_log(str(exc)[:300]))
        return {"sent": False, "reason": "error"}


async def _switched_off(connectors) -> set:
    """Which of these the town has switched off. Empty on any doubt.

    Doubt resolves toward alerting: a redundant email about a switched-off
    service is noise, a swallowed email about a wanted one is the original
    failure mode this whole subsystem exists to prevent.
    """
    off = set()
    try:
        from app.services import capability_switches

        for connector in connectors:
            if connector in capability_switches.SWITCHABLE \
                    and not await capability_switches.enabled(connector):
                off.add(connector)
    except Exception as exc:
        logger.debug("[Health] could not read capability switches: %s",
                     sanitize_for_log(str(exc)[:200]))
        return set()
    return off


async def _town_name(db) -> str:
    try:
        from sqlalchemy import select

        from app.models import SystemSettings

        result = await db.execute(select(SystemSettings.township_name).limit(1))
        return result.scalar_one_or_none() or "Pinpoint 311"
    except Exception:
        return "Pinpoint 311"


async def _settings_url(db) -> Optional[str]:
    """The real address of the settings page, not wherever a browser happened
    to be. A link built from an internal hostname is a link nobody can open."""
    try:
        from app.api.system import public_origin

        origin = await public_origin(db)
        return f"{origin.rstrip('/')}/admin?tab=integration" if origin else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

async def probe_system(db, *, readings=None, health=None, alerts=None):
    """Record disk, database, cache and backup state as health rows.

    Deliberately the same table, the same escalation and the same mute as the
    connectors, rather than a second alerting path beside the first. Two paths
    would drift, and the town would learn which one to trust the hard way.

    `readings` is injectable so the whole thing runs in CI, which has no disk
    quota worth failing, no PostgreSQL and no Redis.
    """
    from app.services import system_probes as probes

    if health is None:
        from app.services import connector_health as health
    if readings is None:
        readings = await _collect_readings(db)

    recorded = {}
    for connector, outcome in readings.items():
        # "We could not measure this" is not "this is broken" -- the same rule
        # the connector sweep follows for things it cannot check.
        if outcome.get("recorded") is False:
            recorded[connector] = "unmeasured"
            continue
        try:
            if outcome["ok"]:
                await health.record_success(db, connector)
            else:
                await health.record_failure(db, connector, outcome["detail"])
            recorded[connector] = "ok" if outcome["ok"] else "failing"
        except Exception:
            logger.warning("[Probe] could not record %s", sanitize_for_log(connector))
    if alerts is not None:
        # `alerts` is `connector_alerts.dispatch`, which requires the health
        # snapshot as a keyword argument. This used to call `alerts(db)` and so
        # raised TypeError on every single run -- caught one frame up and logged
        # as "hourly system probe could not run", which reads like a probe
        # failure rather than what it was: the readings were recorded fine and
        # no probe alert email had ever been sent. A full disk was measured
        # hourly and mentioned to nobody. Routed through notify so the probe
        # digest gets the same snapshot, town name, settings link and
        # switched-off filtering as the daily sweep's.
        await notify(db, health=health, dispatch=alerts)
    return {"probes": recorded, "labels": {k: probes.label_for(k) for k in readings}}


async def _collect_readings(db) -> Dict[str, Dict[str, Any]]:
    """The real measurements. Every one is guarded: a probe that raises must
    not take the other three with it."""
    from sqlalchemy import text

    from app.services import system_probes as probes

    out: Dict[str, Dict[str, Any]] = {}

    # Disk. Not just `/`: photos and pre-migration database dumps sit on their
    # own volumes, and if either of those is mounted on a second disk it is the
    # one that fills while the root filesystem stays comfortable. The fullest of
    # them is reported, by name.
    try:
        out["system:disk"] = probes.describe_disk(probes.worst_disk(probes.read_disks()))
    except Exception:
        out["system:disk"] = probes.classify_disk(None)

    # Memory, measured against this container's cap rather than the host's RAM.
    try:
        out["system:memory"] = probes.classify_memory(probes.read_memory())
    except Exception:
        out["system:memory"] = probes.classify_memory({"percent": None})

    try:
        await db.execute(text("SELECT 1"))
        out["system:database"] = probes.classify_reachable("The database", True)
    except Exception as e:
        # Never the exception text.
        #
        # This string is stored in connector_health.last_error, shown on the
        # card, and put in the alert email. A PostgreSQL connection failure
        # routinely quotes the DSN back at you -- host, user and password --
        # so a database that goes down would email its own credentials to
        # every administrator, and leave them in a table and an inbox
        # afterwards. The type is enough to act on; the rest goes to the log,
        # through the sanitiser, where it is already handled.
        logger.warning("[Probe] database unreachable: %s", sanitize_for_log(str(e)[:300]))
        out["system:database"] = probes.classify_reachable(
            "The database", False, probes.failure_summary(e))

    try:
        from app.core.redis_client import redis_client
        if redis_client is None:
            # Not configured is not broken. Redis is optional here.
            out["system:cache"] = {"ok": True, "detail": "No cache configured.", "recorded": False}
        else:
            await redis_client.ping()
            out["system:cache"] = probes.classify_reachable("The cache", True)
    except Exception as e:
        # Same reasoning as the database above: a Redis URL carries a password.
        logger.warning("[Probe] cache unreachable: %s", sanitize_for_log(str(e)[:300]))
        out["system:cache"] = probes.classify_reachable(
            "The cache", False, probes.failure_summary(e))

    try:
        from datetime import datetime, timezone

        from app.services.backup_service import get_backup_status
        status = await get_backup_status()
        last = (status or {}).get("last_backup_at") or (status or {}).get("last_backup")
        if isinstance(last, str):
            from datetime import datetime as _dt
            last = _dt.fromisoformat(last.replace("Z", "+00:00"))
        out["system:backups"] = probes.classify_backup(last, datetime.now(timezone.utc))
    except Exception:
        # Backups not being configured at all is a real and supported state for
        # a town whose host takes them, so this is not reported as a failure.
        out["system:backups"] = {"ok": True, "detail": "Backup status unavailable.", "recorded": False}

    return out
