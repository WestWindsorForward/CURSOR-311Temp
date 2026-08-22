"""Tell somebody when an integration breaks, or starts to.

The daily sweep already knew. It wrote the result to a table and a log line,
and then waited for an administrator to open the settings page -- which, for a
town where the setup is done and nothing is obviously wrong, may be months. In
between, the failure mode this whole health system exists to catch plays out
exactly as it did before: an expired client secret, no staff able to sign in on
Monday morning, and nobody aware that the software knew on Tuesday.

Two levels, because the interesting one is the earlier one:

    broken    three or more failures in a row. It is not working now.
    at risk   something failed, but not consistently -- or the daily sweep has
              gone several days without recording a success, so the last
              evidence we have is old enough that a key could have been revoked
              without us noticing.

"At risk" is the whole point of sending anything. A clerk told on the first
failed call has days to renew a secret; a clerk told when sign-in is fully down
is being informed of an outage they are already in.

What this deliberately does not do
----------------------------------

*Not one email per connector per day.* An alert that arrives every morning
whether or not anything changed is filtered into a folder within a fortnight,
and then the one that mattered is in the folder too. Mail goes out when a
connector's level *changes*, and after that on a cadence set by how bad it is:
daily while something is broken, every third day while something is at risk.
One digest covers all of them, so a cloud outage that takes four connectors
down is one message and not four.

*Not an alert for things that are switched off.* The sweep does not test what
is not configured, and `connector_verification.notify` filters switched-off
capabilities out of the rows handed to `dispatch` -- necessary because a
connector switched off *after* it started failing keeps its failing health row
(the sweep no longer runs it, so the counters never reset), and that frozen
row would otherwise generate a reminder email forever.

*Not a claim we cannot support.* "Your Azure secret expires on the 14th" would
be more useful than anything here, and we do not know it -- expiry dates are not
in any response we get. What we can say honestly is what failed, what the
provider said about it, and when it last worked. So that is what is sent.

The decision logic is pure and the sending is injected, so the rules can be
tested without a database, a mail server, or FastAPI -- none of which CI has.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

BROKEN = "broken"
AT_RISK = "at_risk"
HEALTHY = "healthy"

RANK = {HEALTHY: 0, AT_RISK: 1, BROKEN: 2}

# How long a connector may sit in the same state before it is mentioned again.
#
# Per level, because one interval for both was wrong in the direction that
# matters. A single weekly reminder meant staff sign-in could be completely
# down on the Monday and not mentioned again until the following Monday --
# which is not a quiet mailbox, it is a week of an outage nobody was nudged
# about.
#
# Something broken is worth a line every day: the sweep runs daily, the mail
# stops the moment it recovers, and a clerk who is already dealing with it has
# a genuine reason to see it until they are not. Something at risk is not yet
# an outage and does not earn that, but three days of a connector failing
# intermittently is no longer a blip either.
REMIND_AFTER: Dict[str, timedelta] = {
    BROKEN: timedelta(days=1),
    AT_RISK: timedelta(days=3),
}

# What a clerk should call each connector. The internal names are fine in a
# table and wrong in a sentence: "sms is down" is not something to forward to
# the township administrator.
CONNECTOR_LABEL: Dict[str, str] = {
    "ai": "AI triage",
    "translation": "Translation",
    "identity": "Staff sign-in",
    "maps": "Maps",
    "email": "Email to residents",
    "sms": "Text messages to residents",
    "kms": "Encryption keys",
    "redaction": "Photo redaction",
}


def label(connector: str) -> str:
    """A name for a connector that reads in a sentence."""
    if connector in CONNECTOR_LABEL:
        return CONNECTOR_LABEL[connector]
    # Infrastructure probes ride in this same table so they inherit the
    # escalation, the digest and the mute. They need their own names for the
    # same reason the connectors do: "system:disk is down" is not a sentence to
    # forward to a township administrator.
    if connector.startswith("system:"):
        from app.services.system_probes import label_for
        return label_for(connector)
    # "govtech:accela" -> "Accela". Anything unrecognised keeps its own name
    # rather than being dropped, so a new integration can still raise an alarm
    # the day it is added.
    tail = connector.split(":")[-1].replace("_", " ").strip()
    return tail[:1].upper() + tail[1:] if tail else connector


def alert_level(status: str) -> str:
    """Map a health status onto what, if anything, is worth saying.

    `stale` counts as at risk rather than healthy on purpose. It means no
    successful call across several consecutive daily sweeps -- so the connector
    is not known to be
    broken, and it is also not known to work, and the whole reason this
    subsystem exists is that those two are not the same thing.
    """
    if status == "down":
        return BROKEN
    if status in ("failing", "stale"):
        return AT_RISK
    return HEALTHY


@dataclass
class Alert:
    connector: str
    level: str
    #  new | escalated | reminder | recovered
    kind: str
    summary: str
    last_error: Optional[str] = None
    last_success_at: Optional[datetime] = None

    @property
    def title(self) -> str:
        return label(self.connector)


# How long "I know about this one" lasts before the alerts resume. Long enough
# to cover a vendor support ticket or a holiday; short enough that a problem
# muted and forgotten comes back rather than disappearing.
MUTE_FOR = timedelta(days=7)


def muted(
    *,
    level: str,
    muted_until: Optional[datetime],
    muted_level: Optional[str],
    now: datetime,
) -> bool:
    """Whether an administrator has already said "I know" about this.

    Muting is consent to a *known* problem, not to whatever that problem later
    becomes. Something acknowledged while it was failing intermittently, that
    then goes fully down, is new information and breaks through -- otherwise
    dismissing a warning would buy a week of silence over an outage, which is
    the one outcome a dismiss button must never be able to produce.
    """
    if muted_until is None:
        return False
    if muted_until.tzinfo is None:
        muted_until = muted_until.replace(tzinfo=timezone.utc)
    if now >= muted_until:
        return False
    # Unknown recorded level is treated as the worst, so a row we cannot
    # interpret errs towards staying quiet only for as long as the deadline --
    # never towards suppressing something worse than what was acknowledged.
    return RANK.get(level, 0) <= RANK.get(muted_level, RANK[BROKEN])


def decide(
    *,
    level: str,
    previous_level: Optional[str],
    alerted_at: Optional[datetime],
    now: datetime,
    remind_after: Dict[str, timedelta] = REMIND_AFTER,
    muted_until: Optional[datetime] = None,
    muted_level: Optional[str] = None,
) -> Optional[str]:
    """Whether to send anything about one connector, and what kind of thing.

    Returns None when the honest answer is "nothing has changed and it has not
    been long enough to mention it again".
    """
    previous = previous_level or HEALTHY

    if level != HEALTHY and muted(level=level, muted_until=muted_until,
                                  muted_level=muted_level, now=now):
        return None

    if level == HEALTHY:
        # Only worth saying if we had said the opposite. A connector that has
        # always been fine generates no mail, ever.
        return "recovered" if RANK.get(previous, 0) > 0 else None

    if RANK.get(previous, 0) == 0:
        return "new"
    if RANK[level] > RANK[previous]:
        return "escalated"
    if RANK[level] < RANK[previous]:
        # Improving but not better. Sending "good news, it is now only
        # intermittently broken" is not worth a message; the state is still
        # updated, so a later escalation is reported as one.
        return None

    if alerted_at is None:
        return "reminder"
    if alerted_at.tzinfo is None:
        alerted_at = alerted_at.replace(tzinfo=timezone.utc)
    # An unrecognised level falls back to the shorter interval. Erring towards
    # saying something is the right direction for a state we do not have a rule
    # for.
    gap = remind_after.get(level, REMIND_AFTER[BROKEN])
    return "reminder" if (now - alerted_at) >= gap else None


def plan(
    healths: Iterable[Any],
    *,
    now: Optional[datetime] = None,
    remind_after: Dict[str, timedelta] = REMIND_AFTER,
) -> List[Alert]:
    """Work out what to send, from health rows that carry their own alert state.

    Each item needs `connector`, `status`, and the two alert-state fields
    (`alerted_level`, `alerted_at`). Anything missing is treated as never
    alerted, which errs towards sending -- a duplicate email is a smaller
    failure than a silent outage.
    """
    now = now or datetime.now(timezone.utc)
    out: List[Alert] = []
    for h in healths:
        status = getattr(h, "status", None) or "unknown"
        level = alert_level(status)
        kind = decide(
            level=level,
            previous_level=getattr(h, "alerted_level", None),
            alerted_at=getattr(h, "alerted_at", None),
            now=now,
            remind_after=remind_after,
            muted_until=getattr(h, "alert_muted_until", None),
            muted_level=getattr(h, "alert_muted_level", None),
        )
        if kind is None:
            continue
        summary = h.summary() if callable(getattr(h, "summary", None)) else str(status)
        out.append(Alert(
            connector=getattr(h, "connector", "?"),
            level=level,
            kind=kind,
            summary=summary,
            last_error=getattr(h, "last_error", None),
            last_success_at=getattr(h, "last_success_at", None),
        ))
    # Worst first, so the subject line and the first paragraph are about the
    # thing that matters most.
    out.sort(key=lambda a: (-RANK[a.level], a.connector))
    return out


def subject(alerts: Sequence[Alert], town: str) -> str:
    """One line that survives being read on a phone's lock screen."""
    broken = [a for a in alerts if a.level == BROKEN]
    at_risk = [a for a in alerts if a.level == AT_RISK]
    recovered = [a for a in alerts if a.kind == "recovered"]

    if broken:
        head = broken[0].title if len(broken) == 1 else f"{len(broken)} services"
        return f"[{town}] {head} not working"
    if at_risk:
        head = at_risk[0].title if len(at_risk) == 1 else f"{len(at_risk)} services"
        return f"[{town}] {head} failing intermittently"
    if recovered:
        head = recovered[0].title if len(recovered) == 1 else f"{len(recovered)} services"
        return f"[{town}] {head} working again"
    return f"[{town}] Service check"


def _when(dt: Optional[datetime], now: datetime) -> str:
    if dt is None:
        return "never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now - dt).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def compose(
    alerts: Sequence[Alert],
    *,
    town: str,
    settings_url: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, str]:
    """Subject and body.

    Written to be actionable by a township clerk rather than complete for an
    engineer: what stopped working, in words; what the provider said, verbatim,
    because that string is what a web search resolves; when it last worked; and
    one link to the page where it is fixed. No stack traces, no connector ids,
    no advice we cannot stand behind.
    """
    from app.services import email_layout as L

    now = now or datetime.now(timezone.utc)
    lines: List[str] = []
    # The HTML half is described as layout blocks rather than as markup, so the
    # digest arrives looking like every other message the system sends -- and
    # so a provider's error string is escaped by the renderer rather than by
    # remembering to escape at every interpolation.
    blocks: List[Dict[str, Any]] = []

    broken = [a for a in alerts if a.level == BROKEN]
    at_risk = [a for a in alerts if a.level == AT_RISK]
    recovered = [a for a in alerts if a.kind == "recovered"]

    if broken:
        lines.append("Not working right now:")
        blocks.append(L.heading("Not working right now", 2))
        items = []
        for a in broken:
            detail = f" — {a.last_error}" if a.last_error else ""
            lines.append(f"  • {a.title}{detail}")
            lines.append(f"    Last worked: {_when(a.last_success_at, now)}")
            items.append((f"{a.title}{detail}",
                          f"Last worked: {_when(a.last_success_at, now)}"))
        blocks.append(L.bullets(items))
        lines.append("")

    if at_risk:
        # Not "may stop working". That predicted an outcome the sweep has no
        # basis for and left the reader anxious without telling them anything
        # to act on. The observed condition is that calls to these services are
        # failing some of the time, which is a fact, and each line below gives
        # the provider's own error and the date it last succeeded.
        lines.append("Failing intermittently:")
        blocks.append(L.heading("Failing intermittently", 2))
        items = []
        for a in at_risk:
            detail = f" — {a.last_error}" if a.last_error else f" — {a.summary}"
            lines.append(f"  • {a.title}{detail}")
            lines.append(f"    Last worked: {_when(a.last_success_at, now)}")
            items.append((f"{a.title}{detail}",
                          f"Last worked: {_when(a.last_success_at, now)}"))
        blocks.append(L.bullets(items))
        lines.append("")

    if recovered:
        names = ", ".join(a.title for a in recovered)
        lines.append(f"Working again: {names}")
        blocks.append(L.callout(names, "success", title="Working again"))
        lines.append("")

    if settings_url:
        # The same words as the button. This digest is the one message that
        # still composes its text half beside its blocks rather than from them,
        # so the two can drift -- and had.
        _settings_label = "Open the integrations settings page"
        lines.append(f"{_settings_label}: {settings_url}")
        blocks.append(L.button(_settings_label, settings_url))
        # Somebody who cannot stop a daily reminder filters the sender, and
        # that takes the next unrelated alert with it. So the way to stop it is
        # in the message itself -- stated as the guarantee it is, rather than
        # as an aside after a dash. The escalation carve-out is the whole
        # reason muting is safe to offer, so it gets its own sentence.
        stop = (f"Muting an alert on that page stops these emails about it for "
                f"{MUTE_FOR.days} days. An alert that escalates to a higher severity "
                f"is still sent while the mute is in effect.")
        lines.append(stop)
        blocks.append(L.paragraph(stop, muted=True))

    lines.append("")
    tail = ("Sent automatically by the daily service check, to administrators "
            "only, and only when a connector's status changes.")
    lines.append(tail)

    message = L.build_email(
        subject=subject(alerts, town),
        township_name=town,
        blocks=blocks,
        preheader=", ".join(a.title for a in (broken or at_risk or recovered))[:120],
        footer_lines=[tail],
        no_reply_note="",
    )

    return {
        "subject": message["subject"],
        "text": "\n".join(lines),
        "html": message["html"],
    }


# ---------------------------------------------------------------------------
# The part that touches the world.
# ---------------------------------------------------------------------------

async def dispatch(
    db,
    *,
    healths: Iterable[Any],
    send: Optional[Callable[..., bool]] = None,
    recipients: Optional[Sequence[str]] = None,
    town: Optional[str] = None,
    settings_url: Optional[str] = None,
    now: Optional[datetime] = None,
    remind_after: Dict[str, timedelta] = REMIND_AFTER,
) -> Dict[str, Any]:
    """Send one digest, then remember what was said. Never raises.

    One email covering everything rather than one per connector: a cloud
    outage takes four connectors down together, and four separate alarms about
    one event is how people learn to delete them unread.

    The state write happens after the send and only if the send worked, so a
    mail server that is itself down does not silently consume the alert.
    """
    now = now or datetime.now(timezone.utc)
    healths = list(healths)
    alerts = plan(healths, now=now, remind_after=remind_after)
    if not alerts:
        return {"sent": False, "alerts": []}

    try:
        if recipients is None:
            recipients = await admin_emails(db)
        if not recipients:
            # Worth a log line: a town with no active admin address cannot be
            # told anything, and that is itself a problem to notice.
            logger.warning("[Health] connector alerts raised but no admin address to send to")
            return {"sent": False, "alerts": [a.connector for a in alerts], "reason": "no-recipients"}

        if send is None:
            from app.services.notifications import NotificationService

            # Build the sender from the stored credentials first. The email
            # provider is a per-process singleton that the notification tasks
            # configure before they send -- and the sweep and the hourly probe
            # are usually the first thing to want email in a fresh worker, so
            # without this the alert died with "Email provider not configured"
            # while resident notifications from the same worker went out fine.
            # A lazy import: app.tasks pulls in Celery, which CI does not
            # install, and every CI caller of dispatch injects `send`.
            try:
                from app.tasks.service_requests import configure_notifications
                await configure_notifications(db)
            except Exception as exc:
                # If configuration fails the send below reports it honestly.
                logger.debug("[Health] could not configure the email sender: %s", str(exc)[:200])
            send = NotificationService.get_instance().send_email

        body = compose(alerts, town=town or "Pinpoint 311", settings_url=settings_url, now=now)
        delivered = False
        for address in recipients:
            try:
                # Sent one at a time rather than to a joined To: header, so one
                # town's administrators are not disclosed to each other's mail
                # providers, and one bad address cannot drop the whole batch.
                delivered = bool(send(
                    to=address,
                    subject=body["subject"],
                    body_html=body["html"],
                    body_text=body["text"],
                )) or delivered
            except Exception as exc:
                logger.warning("[Health] could not send a connector alert: %s", str(exc)[:200])

        if not delivered:
            return {"sent": False, "alerts": [a.connector for a in alerts], "reason": "send-failed"}

        await remember(db, alerts, now=now)
        return {"sent": True, "alerts": [a.connector for a in alerts],
                "subject": body["subject"], "recipients": len(recipients)}
    except Exception as exc:
        # Alerting that can break the sweep it rides on would be worse than no
        # alerting: the health table is the thing of record.
        logger.warning("[Health] connector alerting failed: %s", str(exc)[:300])
        return {"sent": False, "alerts": [a.connector for a in alerts], "reason": "error"}


def next_state(alert: Alert, *, now: datetime) -> tuple:
    """What a connector's alert state becomes once its message has gone out.

    A recovery clears the state rather than recording "healthy". If it stored a
    level instead, the connector's next failure would be compared against a
    stale entry and reported as a reminder -- so the mail saying it had broken
    again would arrive a day late at best, and not at all if it recovered and
    broke again inside the reminder window.
    """
    if alert.kind == "recovered":
        return (None, None)
    return (alert.level, now)


def clears_mute(alert: Alert) -> bool:
    """Whether this alert invalidates whatever mute the connector carries.

    Two ways a mute stops describing anything anyone agreed to. A recovery
    means the acknowledged problem is over, and carrying the mute into the next
    failure would silence a problem nobody has seen yet. An escalation means it
    got worse than what was acknowledged -- it has already broken through, so
    leaving the old deadline in place would re-suppress it tomorrow.
    """
    return alert.kind in ("recovered", "escalated")


def mute_until(now: datetime, *, days: Optional[int] = None) -> datetime:
    """When a mute taken now should expire."""
    return now + (timedelta(days=days) if days is not None else MUTE_FOR)


async def remember(db, alerts: Sequence[Alert], *, now: Optional[datetime] = None) -> None:
    """Write back what was announced, so tomorrow's sweep stays quiet."""
    now = now or datetime.now(timezone.utc)
    try:
        from sqlalchemy import select

        from app.models import ConnectorHealth

        for alert in alerts:
            result = await db.execute(
                select(ConnectorHealth).where(ConnectorHealth.connector == alert.connector)
            )
            row = result.scalar_one_or_none()
            if row is None:
                continue
            row.alerted_level, row.alerted_at = next_state(alert, now=now)
            if clears_mute(alert):
                row.alert_muted_until = None
                row.alert_muted_level = None
        await db.commit()
    except Exception as exc:
        logger.warning("[Health] could not record which alerts were sent: %s", str(exc)[:200])


async def admin_emails(db) -> List[str]:
    """Active administrators, in a stable order. Never raises."""
    try:
        from sqlalchemy import select

        from app.models import User

        result = await db.execute(
            select(User.email).where(User.role == "admin", User.is_active.is_(True))
        )
        return sorted({e for (e,) in result.all() if e})
    except Exception as exc:
        logger.warning("[Health] could not list administrators: %s", str(exc)[:200])
        return []
