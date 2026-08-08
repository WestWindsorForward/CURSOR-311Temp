"""Track whether each integration is actually working.

The setup page's badges answered "are the credentials stored" -- a question
about our own database, which is always answerable and rarely the one being
asked. A clerk reading a green tick believes something stronger: that reports
are reaching the county and emails are going out. Those look identical right up
until a resident complains.

Three states, and the middle one is the point:

    working   a real call succeeded recently
    failing   a real call failed, and here is what the provider said
    unknown   nothing has called it, so we genuinely do not know

Collapsing `unknown` into either of the others is how an expired key survives
for a month. A connector nobody has exercised is not healthy; it is unobserved.
A manual Test button has the same weakness from the other side -- it proves the
credential worked once, at a moment chosen by the person least likely to be
surprised by the answer.

Nothing here raises. Health reporting that can break the thing it reports on is
worse than no health reporting, so every function swallows its own errors: a
failed write loses one data point, and losing a resident's report to a
bookkeeping bug would be indefensible.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.sanitize import sanitize_for_log

logger = logging.getLogger(__name__)

# How long a success stays meaningful. Past this a connector is reported as
# stale rather than working: the last real evidence is old enough that a key
# could have been revoked, a card could have expired, or a vendor could have
# changed a scope, and we would not know.
#
# Was a week, from when the only evidence was organic traffic -- a connector
# nobody happened to use for six days was unremarkable. There is now a sweep
# that actively tests every configured connector once a day, so with the worker
# running this resets daily and three days without a success means three
# consecutive sweeps found nothing to record. A week of that is a long time to
# call something healthy.
#
# Towns with no Celery worker have no sweep, so nothing here alerts on their
# behalf either; their badges simply go honest sooner.
FRESH_FOR = timedelta(days=3)

# Failures before "failing" becomes "down". One failed call is a blip -- a
# timeout, a redeploy, a rate limit -- and paging a clerk for it teaches them to
# ignore the badge.
DOWN_AFTER = 3

ERROR_MAX_CHARS = 500

WORKING = "working"
FAILING = "failing"
DOWN = "down"
STALE = "stale"
UNKNOWN = "unknown"


@dataclass
class Health:
    """A connector's state, as the admin UI needs it."""

    connector: str
    status: str = UNKNOWN
    provider: Optional[str] = None
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    # Carried through so the alerting layer can tell "this is new" from "we
    # already said this on Tuesday" without a second query per connector.
    alerted_level: Optional[str] = None
    alerted_at: Optional[datetime] = None
    # What the last check said, either way, and whether one is even possible.
    last_result: Optional[str] = None
    verifiable: Optional[bool] = None
    # Until when alerts for this connector are silenced.
    #
    # The column and the mute endpoint were added together; this field was not,
    # and `/connectors/health` reads `h.alert_muted_until` on every row. A
    # dataclass has no such attribute, so the endpoint raised AttributeError and
    # returned 500 -- every time, for everyone. On screen that is the banner
    # saying the status of these services could not be read, which reads as an
    # outage somewhere else entirely, and it takes every provider card down with
    # it because they hydrate from the same response.
    alert_muted_until: Optional[datetime] = None
    # What was wrong when the mute was taken.
    #
    # The deadline alone cannot express what an administrator agreed to. Muting
    # is consent to a *known* problem, and `connector_alerts.muted` compares the
    # current level against this one so something acknowledged while it was
    # failing intermittently still breaks through when it goes fully down.
    # Without the field the comparison read `getattr(h, ..., None)` on every
    # row, fell back to treating the mute as covering `broken`, and the
    # documented escalation-beats-a-mute invariant quietly did not hold.
    alert_muted_level: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == WORKING

    def summary(self) -> str:
        """One line for a clerk, naming the provider's own words when failing."""
        if self.status == UNKNOWN:
            return "Not used yet — nothing has called this"
        if self.status == WORKING:
            return "Working"
        if self.status == STALE:
            days = max(1, FRESH_FOR.days)
            return f"No recent activity — nothing has worked here in over {days} days"
        detail = f" — {self.last_error}" if self.last_error else ""
        if self.status == DOWN:
            return f"Failing repeatedly ({self.consecutive_failures} in a row){detail}"
        return f"Last call failed{detail}"


def classify(row: Any, *, now: Optional[datetime] = None) -> str:
    """Derive the status from stored counters.

    Pure, so the interesting decisions -- when a blip becomes an outage, when a
    success goes stale -- are testable without a database.
    """
    now = now or datetime.now(timezone.utc)
    failures = getattr(row, "consecutive_failures", 0) or 0
    last_success = getattr(row, "last_success_at", None)
    last_error = getattr(row, "last_error_at", None)

    if failures >= DOWN_AFTER:
        return DOWN
    if failures > 0:
        return FAILING
    if last_success is None:
        # An error with no success ever is still a failure, not "unknown" --
        # something tried and could not.
        return FAILING if last_error else UNKNOWN

    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    return WORKING if (now - last_success) <= FRESH_FOR else STALE


def to_health(row: Any, *, now: Optional[datetime] = None) -> Health:
    return Health(
        connector=row.connector,
        status=classify(row, now=now),
        provider=getattr(row, "provider", None),
        last_success_at=getattr(row, "last_success_at", None),
        last_error_at=getattr(row, "last_error_at", None),
        last_error=getattr(row, "last_error", None),
        consecutive_failures=getattr(row, "consecutive_failures", 0) or 0,
        total_successes=getattr(row, "total_successes", 0) or 0,
        total_failures=getattr(row, "total_failures", 0) or 0,
        alerted_level=getattr(row, "alerted_level", None),
        alerted_at=getattr(row, "alerted_at", None),
        last_result=getattr(row, "last_result", None),
        verifiable=getattr(row, "verifiable", None),
        # getattr, like every other column here, so a deployment that has not
        # run the mute migration yet degrades to "not muted" instead of 500ing
        # the whole health page.
        alert_muted_until=getattr(row, "alert_muted_until", None),
        alert_muted_level=getattr(row, "alert_muted_level", None),
    )


# Provider errors echo the request, and the request carries the credential.
# httpx's raise_for_status message quotes the *full* URL, query string and all,
# so a failing Google Translate call arrives here as
# "... for url 'https://.../v2?key=AIza...'". A URL's query string is where
# keys travel; nothing a card or an alert email needs lives after the "?".
_URL_QUERY = re.compile(r"\?[^\s'\"<>]+")

# `param=value` shapes outside a URL -- a vendor echoing form fields or config
# back in an error body. The value goes, the name stays: "api_key=[redacted]"
# still tells support which field to look at.
_CREDENTIAL_PARAM = re.compile(
    r"(?i)\b(key|api[-_]?key|apikey|token|access[-_]?token|signature|secret|"
    r"password|passwd|pwd|subscription[-_]?key)\s*=\s*[^&\s'\"<>]+")


def _scrub_secrets(text: str) -> str:
    text = _URL_QUERY.sub("?…", text)
    return _CREDENTIAL_PARAM.sub(lambda m: f"{m.group(1)}=[redacted]", text)


def clean_error(exc: Any) -> str:
    """The provider's message, trimmed and stripped of anything secret.

    Provider errors sometimes echo the request, and the request contains the
    credential. Storing that would put a key in a table the admin UI renders,
    the alert emails quote, and the support process copy-pastes out of.
    sanitize_for_log alone was not enough: it strips log-injection characters,
    not the ?key=... that httpx bakes into every status-error message.
    """

    text = _scrub_secrets(sanitize_for_log(str(exc)).strip())
    return text[:ERROR_MAX_CHARS] if text else "Unknown error"


# Columns added by migration b4c5d6e7f8a9. Everything above them predates it.
#
# A deployment that has not run migrations yet still has to record health. If
# it cannot -- and it could not, because setting an attribute puts the column
# in the UPDATE and the whole statement fails -- then `record_success` catches
# its own error, writes nothing at all, and the card says "not checked yet"
# immediately after somebody watched the test pass.
#
# That is the worst version: the feature looks broken rather than partly
# unavailable, and the reason (a pending migration) is invisible.
LATER_COLUMNS = ("last_result", "verifiable")


async def _commit_or_retry_without(db, row, columns) -> bool:
    """Commit; if a newer column is missing, drop it and commit the rest.

    Returns whether the detail columns survived. The counters and timestamps
    matter more than the message, and losing all of them because one column
    is not there yet is not a trade anybody would choose.
    """
    try:
        await db.commit()
        return True
    except Exception as exc:
        text = str(exc).lower()
        if not any(c in text for c in columns) and "column" not in text:
            raise
        await db.rollback()
        logger.warning(
            "[Health] %s unavailable -- run the pending migrations to record "
            "what a check found, not only when it ran.", ", ".join(columns)
        )
        # Re-apply without them. The row is expired after a rollback, so this
        # re-reads and re-sets rather than reusing the stale instance.
        return False


def _is_real_session(db) -> bool:
    try:
        from sqlalchemy.ext.asyncio import AsyncSession
    except Exception:
        return False
    return isinstance(db, AsyncSession)


@asynccontextmanager
async def _recording_session(db):
    """A session to write one health row in, preferring one of our own.

    `db=None` means the caller has no session at all -- the runtime quota flags
    (`note_quota_failure`) fire from places like the translation cache path and
    the photo-redaction detectors, which deliberately never hold one. Opening
    our own is the same isolation argument as below, just without a caller
    session to protect; if SessionLocal itself cannot be imported the ImportError
    propagates into record_*'s own catch and costs one data point, as designed.

    These functions commit, and they are called from inside per-integration
    loops -- through `guard`, on every push. Committing the *caller's* session
    there flushed work that iteration had only half applied: in
    `push_status_to_integrations` a link's new `external_status` and
    `last_pushed_at` were written to disk by a health counter update, before the
    document push that might still have failed. Health is operational state
    about a connector; it has no business deciding when a resident's report is
    durable.

    Falls back to the caller's session when there is no real one to replace --
    which is every test in this suite, and any caller passing a stand-in.
    """
    if db is None:
        from app.db.session import SessionLocal
        session = SessionLocal()
        try:
            yield session
        finally:
            try:
                await session.close()
            except Exception:
                pass
        return
    if not _is_real_session(db):
        yield db
        return
    try:
        from app.db.session import SessionLocal
    except Exception:
        yield db
        return
    session = SessionLocal()
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception:
            pass


async def _row(db, connector: str):
    from sqlalchemy import select

    from app.models import ConnectorHealth

    result = await db.execute(
        select(ConnectorHealth).where(ConnectorHealth.connector == connector)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row

    # The first write for a connector is a race. Two workers both found no row,
    # both added one, and `connector` is unique -- so one committed and the other
    # took an IntegrityError that record_* swallowed by design, losing the very
    # data point it was called to store. Which is the worst time to lose one: it
    # is the first evidence anybody has about that connector.
    #
    # ON CONFLICT DO NOTHING makes the loser a no-op rather than an error, and
    # the re-read below finds the winner's row to update.
    inserted = await _insert_if_absent(db, connector)
    if inserted is not None:
        return inserted

    row = ConnectorHealth(connector=connector, consecutive_failures=0,
                          total_successes=0, total_failures=0)
    db.add(row)
    return row


async def _insert_if_absent(db, connector: str):
    """Claim the row with an upsert, then return it. None if unavailable.

    Returns None on any dialect without ON CONFLICT support, or on a session
    that is not a real one, so the plain add() path below still works -- this is
    a narrowing of a race, not a new requirement.
    """
    try:
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        from app.models import ConnectorHealth

        await db.execute(
            insert(ConnectorHealth)
            .values(connector=connector, consecutive_failures=0,
                    total_successes=0, total_failures=0)
            .on_conflict_do_nothing(index_elements=["connector"])
        )
        result = await db.execute(
            select(ConnectorHealth).where(ConnectorHealth.connector == connector)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


async def record_success(db, connector: str, provider: Optional[str] = None,
                         detail: Optional[str] = None) -> None:
    """A real call worked. Never raises."""
    try:
        now = datetime.now(timezone.utc)
        async with _recording_session(db) as session:
            row = await _row(session, connector)
            row.provider = provider or row.provider
            row.last_attempt_at = now
            row.last_success_at = now
            # Kept, so a card can say what the last check found rather than only
            # when it happened. Scrubbed like an error: a success detail is
            # still a vendor string, and it lands in the same rendered column.
            row.last_result = _scrub_secrets(detail or "")[:500] or None
            row.verifiable = True
            # Reset, not decrement: the connector demonstrably works right now,
            # and carrying old failures forward would keep it amber after it
            # recovered.
            row.consecutive_failures = 0
            row.last_error = None
            row.total_successes = (row.total_successes or 0) + 1
            if not await _commit_or_retry_without(session, row, LATER_COLUMNS):
                # Second pass with only the columns every deployment has.
                row = await _row(session, connector)
                row.provider = provider or row.provider
                row.last_attempt_at = now
                row.last_success_at = now
                row.consecutive_failures = 0
                row.last_error = None
                row.total_successes = (row.total_successes or 0) + 1
                await session.commit()
    except Exception as exc:
        logger.warning("[Health] could not record success for %s: %s",
                       sanitize_for_log(connector), sanitize_for_log(str(exc)))


async def record_unverifiable(db, connector: str, detail: str,
                              provider: Optional[str] = None) -> None:
    """We tried, and this provider cannot be checked from here at all.

    Recorded so the answer survives the browser session that produced it.
    Without this the card reverts to "not checked yet" on reload and invites
    somebody to press a button that can never succeed -- and worse, a genuinely
    unchecked connector and one that is unverifiable by nature look identical.

    Deliberately touches neither the success nor the failure counters. It is
    not evidence either way, and letting it move the failure count would feed
    the escalation that emails administrators about a connector nobody can do
    anything about.
    """
    try:
        async with _recording_session(db) as session:
            row = await _row(session, connector)
            row.provider = provider or row.provider
            row.last_attempt_at = datetime.now(timezone.utc)
            # Same scrub as record_success: also a vendor-shaped string, also
            # rendered.
            row.last_result = _scrub_secrets(detail or "")[:500] or None
            row.verifiable = False
            if not await _commit_or_retry_without(session, row, LATER_COLUMNS):
                # Nothing left to record: "we tried and cannot tell" lives
                # entirely in the columns this deployment does not have yet. The
                # attempt timestamp is still worth keeping.
                row = await _row(session, connector)
                row.provider = provider or row.provider
                row.last_attempt_at = datetime.now(timezone.utc)
                await session.commit()
    except Exception as exc:
        logger.warning("[Health] could not record unverifiable for %s: %s",
                       sanitize_for_log(connector), sanitize_for_log(str(exc)))


async def record_failure(db, connector: str, error: Any,
                         provider: Optional[str] = None) -> None:
    """A real call failed. Never raises."""
    try:
        now = datetime.now(timezone.utc)
        async with _recording_session(db) as session:
            row = await _row(session, connector)
            row.provider = provider or row.provider
            row.last_attempt_at = now
            row.last_error_at = now
            row.last_error = clean_error(error)
            row.last_result = row.last_error
            row.verifiable = True
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            row.total_failures = (row.total_failures or 0) + 1
            if not await _commit_or_retry_without(session, row, LATER_COLUMNS):
                row = await _row(session, connector)
                row.provider = provider or row.provider
                row.last_attempt_at = now
                row.last_error_at = now
                row.last_error = clean_error(error)
                row.consecutive_failures = (row.consecutive_failures or 0) + 1
                row.total_failures = (row.total_failures or 0) + 1
                await session.commit()
    except Exception as exc:
        logger.warning("[Health] could not record failure for %s: %s",
                       sanitize_for_log(connector), sanitize_for_log(str(exc)))


async def snapshot(db) -> Dict[str, Health]:
    """Every connector's health, keyed by connector name. Never raises."""
    try:
        from sqlalchemy import select

        from app.models import ConnectorHealth

        result = await db.execute(select(ConnectorHealth))
        return {r.connector: to_health(r) for r in result.scalars().all()}
    except Exception as exc:
        logger.warning("[Health] could not read connector health: %s", sanitize_for_log(str(exc)))
        return {}


def worst_first(healths: List[Health]) -> List[Health]:
    """Order for display: the ones needing attention at the top.

    `unknown` deliberately outranks `working`. A connector nobody has exercised
    is the one most likely to be quietly broken, and sorting it below the
    healthy ones buries exactly the thing worth looking at.
    """
    rank = {DOWN: 0, FAILING: 1, STALE: 2, UNKNOWN: 3, WORKING: 4}
    return sorted(healths, key=lambda h: (rank.get(h.status, 9), h.connector))


# ---------------------------------------------------------------------------
# Quota failures observed at runtime.
#
# Every runtime consumer of a metered provider already degrades gracefully --
# geocoding falls through to OpenStreetMap, redaction falls back to the
# on-server detector, translation returns the originals, AI triage stores a
# manual-review default. The degradation is the feature; the problem was that
# it is *silent*, so a town could run against its hard rate limit for a month
# and only notice on the invoice, or in translation quality nobody reports.
#
# These helpers put that event where an administrator already looks: the same
# connector_health row the spotlight cards and the daily alert digest read.
# Residents and staff see nothing new -- the fallback already covered them.
# ---------------------------------------------------------------------------

# The words providers use for "you have hit your limit". Matched against the
# scrubbed message, because httpx bakes the request URL into every status-error
# string and a query string can legitimately contain "429 Elm St".
#
# Deliberately *not* matched: a bare "quota". Google auth errors say things
# like "quota project not set", which is a configuration problem, not a limit
# being hit, and flagging it here would page an administrator with the wrong
# story.
_QUOTA_TEXT = re.compile(
    r"(?i)(\b429\b|too ?many ?requests|rate.?limit|rate exceeded|throttl|"
    r"over[_ ]?query[_ ]?limit|resource[_ ]exhausted|over quota|"
    r"quota (?:exceeded|exhausted|reached)|exceeded.{0,40}quota|"
    r"insufficient[_ ]quota|out of quota|dailylimitexceeded)"
)


def _http_status_of(error: Any) -> Optional[int]:
    """The HTTP status an exception carries, if it carries one at all.

    Covers httpx.HTTPStatusError (`.response.status_code`), aiohttp and raw
    response objects (`.status` / `.status_code`) without importing any of
    them -- this must work on whatever a monkeypatched test hands it too.
    """
    for attr in ("status_code", "status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def is_quota_error(error: Any) -> bool:
    """Whether this failure is the provider saying "you hit your limit".

    Pure, so every provider's shapes (HTTP 429, Google OVER_QUERY_LIMIT /
    RESOURCE_EXHAUSTED, AWS ThrottlingException, OpenAI insufficient_quota)
    are testable without a provider.
    """
    if error is None:
        return False
    if _http_status_of(error) == 429:
        return True
    return bool(_QUOTA_TEXT.search(_scrub_secrets(str(error))))


# How long one recorded quota failure speaks for its successors. A quota storm
# is hundreds of identical 429s in minutes -- on the resident intake path --
# and each one costing a DB write would make the bookkeeping more expensive
# than the failed call. One row-write per connector per window is plenty: the
# card goes red on the first one, and DOWN_AFTER only needs the failures to
# keep arriving, not to arrive densely.
QUOTA_RECORD_EVERY = timedelta(minutes=10)

# Per-process on purpose (web worker and Celery worker each keep their own),
# so the worst case is one write per window per process, which is still cheap.
_quota_last_recorded: Dict[str, float] = {}


def _quota_should_record(connector: str, *, now: Optional[float] = None) -> bool:
    """Claim this window for `connector`, or report that it is already taken.

    Stamps *before* the caller writes, so a storm cannot stampede the database
    while a slow or failing write is in flight -- losing one repeat data point
    is the cheap direction.
    """
    now = time.monotonic() if now is None else now
    last = _quota_last_recorded.get(connector)
    if last is not None and (now - last) < QUOTA_RECORD_EVERY.total_seconds():
        return False
    _quota_last_recorded[connector] = now
    return True


def _reset_quota_throttle() -> None:
    """Tests only: monotonic time cannot be rewound, so the dict is cleared."""
    _quota_last_recorded.clear()


async def note_quota_failure(connector: str, error: Any,
                             provider: Optional[str] = None) -> bool:
    """Flag a runtime quota failure on the connector's health row. Never raises.

    Returns whether a row was written -- False for a non-quota error (the
    caller can pass every failure through without classifying it first) and
    for a repeat inside the throttle window. Callers hold no session; the
    write happens in one of our own via `_recording_session(None)` inside
    `record_failure`, so it cannot commit anything the caller half-applied.
    """
    try:
        if not is_quota_error(error):
            return False
        if not _quota_should_record(connector):
            return False
        await record_failure(None, connector, error, provider=provider)
        return True
    except Exception as exc:  # record_failure swallows; this covers the rest
        logger.warning("[Health] could not note quota failure for %s: %s",
                       sanitize_for_log(connector), sanitize_for_log(str(exc)))
        return False
