"""Tests for "is this integration actually working".

Every badge on the setup page used to answer "are the credentials stored" --
a question about our own database, which is always answerable and rarely the one
being asked. A clerk reading a green tick believes reports are reaching the
county and emails are going out. Those look identical until someone complains.

The state that carries the weight is `unknown`. A connector nothing has called
is not healthy, it is unobserved, and collapsing that into either green or red
is how an expired key survives a month. Most of these tests exist to stop that
collapse happening.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import connector_health as ch


class Row:
    """Just the columns classify() reads."""

    def __init__(self, **kw):
        self.connector = kw.get("connector", "ai")
        self.provider = kw.get("provider")
        self.consecutive_failures = kw.get("consecutive_failures", 0)
        self.last_success_at = kw.get("last_success_at")
        self.last_error_at = kw.get("last_error_at")
        self.last_error = kw.get("last_error")
        self.total_successes = kw.get("total_successes", 0)
        self.total_failures = kw.get("total_failures", 0)


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


# ---- the unknown state -------------------------------------------------------

def test_a_connector_nothing_has_called_is_unknown_not_working():
    """The whole point. Reporting this as healthy is how a key that was revoked
    months ago keeps its green tick."""
    assert ch.classify(Row(), now=NOW) == ch.UNKNOWN


def test_unknown_is_not_ok():
    assert not ch.to_health(Row(), now=NOW).ok


def test_unknown_says_so_in_plain_words():
    assert "not used yet" in ch.to_health(Row(), now=NOW).summary().lower()


def test_an_error_with_no_success_ever_is_failing_not_unknown():
    """Something tried and could not. That is knowledge, not absence of it."""
    row = Row(last_error_at=NOW, last_error="401 Unauthorized")
    assert ch.classify(row, now=NOW) == ch.FAILING


# ---- working, and going stale ------------------------------------------------

def test_a_recent_success_is_working():
    assert ch.classify(Row(last_success_at=NOW - timedelta(hours=2)), now=NOW) == ch.WORKING


def test_an_old_success_goes_stale_rather_than_staying_green():
    """A week-old success is not evidence the credential still works -- keys get
    revoked, cards expire, vendors change scopes, and none of that calls us."""
    row = Row(last_success_at=NOW - ch.FRESH_FOR - timedelta(days=1))
    assert ch.classify(row, now=NOW) == ch.STALE


def test_the_freshness_boundary_is_inclusive():
    row = Row(last_success_at=NOW - ch.FRESH_FOR)
    assert ch.classify(row, now=NOW) == ch.WORKING


def test_a_naive_timestamp_does_not_crash_the_comparison():
    """Some drivers hand back naive datetimes. Comparing one to an aware `now`
    raises, and a health check that throws is worse than none."""
    row = Row(last_success_at=NOW.replace(tzinfo=None) - timedelta(hours=1))
    assert ch.classify(row, now=NOW) == ch.WORKING


# ---- blips versus outages ----------------------------------------------------

def test_one_failure_is_a_blip_not_an_outage():
    """Timeouts, redeploys and rate limits all produce a single failure. Paging
    a clerk for each one teaches them to ignore the badge."""
    assert ch.classify(Row(consecutive_failures=1), now=NOW) == ch.FAILING


def test_repeated_failures_escalate():
    assert ch.classify(Row(consecutive_failures=ch.DOWN_AFTER), now=NOW) == ch.DOWN


def test_failures_outrank_a_recent_success():
    """It worked an hour ago and has failed three times since. The recent
    success must not win, or a connector that just broke reads as healthy."""
    row = Row(consecutive_failures=3, last_success_at=NOW - timedelta(hours=1))
    assert ch.classify(row, now=NOW) == ch.DOWN


# ---- what a clerk reads ------------------------------------------------------

def test_the_summary_repeats_the_providers_own_words():
    """"Request failed" sends someone to us. "21608: unverified number" sends
    them to the actual fix."""
    row = Row(consecutive_failures=1, last_error="21608: unverified number")
    assert "21608" in ch.to_health(row, now=NOW).summary()


def test_the_summary_counts_repeated_failures():
    row = Row(consecutive_failures=5, last_error="timeout")
    assert "5" in ch.to_health(row, now=NOW).summary()


def test_a_missing_error_message_does_not_produce_a_dangling_dash():
    assert not ch.to_health(Row(consecutive_failures=1), now=NOW).summary().rstrip().endswith("—")


# ---- error text --------------------------------------------------------------

def test_error_text_is_truncated():
    assert len(ch.clean_error("x" * 5000)) <= ch.ERROR_MAX_CHARS


def test_an_empty_error_still_says_something():
    assert ch.clean_error("") == "Unknown error"
    assert ch.clean_error("   ") == "Unknown error"


def test_error_text_goes_through_the_log_sanitiser():
    """Provider errors sometimes echo the request, and the request carries the
    credential. This table is rendered in the admin UI and copy-pasted into
    support threads."""
    import inspect
    assert "sanitize_for_log" in inspect.getsource(ch.clean_error)


def test_a_url_query_string_never_reaches_the_stored_error():
    """httpx's raise_for_status message quotes the full request URL -- and for
    a key-in-query provider (Google Translate's ?key=...) the full URL *is* the
    credential. This column lands on the setup cards and in the alert emails."""
    msg = ("Client error '403 Forbidden' for url "
           "'https://translation.googleapis.com/v2?key=AIzaSECRET123&q=hi'")
    cleaned = ch.clean_error(msg)
    assert "AIzaSECRET123" not in cleaned
    # The diagnosable parts survive: the verdict and the host.
    assert "403 Forbidden" in cleaned
    assert "translation.googleapis.com" in cleaned


def test_credential_shaped_params_are_redacted_outside_urls_too():
    """Vendors echo form fields and config back in error bodies. The value
    goes; the name stays, so support can still tell which field to look at."""
    cleaned = ch.clean_error(
        "rejected: api_key=sk-live-123 password=hunter2 Subscription-Key=abc")
    for secret in ("sk-live-123", "hunter2", "abc"):
        assert secret not in cleaned
    assert "api_key=" in cleaned
    assert "password=" in cleaned


def test_a_normal_error_passes_through_readable():
    """The scrub must not garble the message a clerk is meant to read."""
    msg = "HTTP 500 from vendor: internal server error (request id 12345)"
    assert ch.clean_error(msg) == msg


async def test_a_success_detail_is_scrubbed_before_it_is_stored():
    """record_success stores what the check found, and what a check found is a
    vendor string like any other -- same column, same cards, same emails."""
    stored = Row()

    class Result:
        def scalar_one_or_none(self):
            return stored

    class Session:
        async def execute(self, *a, **kw):
            return Result()

        def add(self, *a):
            pass

        async def commit(self):
            pass

    await ch.record_success(
        Session(), "govtech:accela",
        detail="reached https://api.example.com/v4/ping?token=SECRETVALUE fine")
    assert "SECRETVALUE" not in (stored.last_result or "")
    assert "api.example.com" in (stored.last_result or "")


# ---- ordering ----------------------------------------------------------------

def test_problems_sort_above_healthy_connectors():
    order = ch.worst_first([
        ch.Health("a", ch.WORKING),
        ch.Health("b", ch.DOWN),
        ch.Health("c", ch.STALE),
        ch.Health("d", ch.FAILING),
    ])
    assert [h.connector for h in order] == ["b", "d", "c", "a"]


def test_unknown_sorts_above_working():
    """A connector nobody has exercised is likelier to be quietly broken than
    one that just succeeded. Burying it under the healthy ones hides the thing
    worth looking at."""
    order = ch.worst_first([ch.Health("working", ch.WORKING), ch.Health("unknown", ch.UNKNOWN)])
    assert [h.connector for h in order] == ["unknown", "working"]


def test_ordering_is_stable_by_name_within_a_status():
    order = ch.worst_first([ch.Health("z", ch.DOWN), ch.Health("a", ch.DOWN)])
    assert [h.connector for h in order] == ["a", "z"]


# ---- never raising -----------------------------------------------------------

@pytest.mark.asyncio
async def test_recording_against_a_broken_session_does_not_raise():
    """Health bookkeeping must never be able to fail the request it is
    observing. Losing one data point is fine; losing a resident's report to a
    counter update would not be."""
    class Broken:
        async def execute(self, *a, **kw):
            raise RuntimeError("database gone")

        def add(self, *a):
            raise RuntimeError("database gone")

        async def commit(self):
            raise RuntimeError("database gone")

    await ch.record_success(Broken(), "ai")
    await ch.record_failure(Broken(), "ai", "boom")
    assert await ch.snapshot(Broken()) == {}


# ---------------------------------------------------------------------------
# The staleness threshold has to mean something
# ---------------------------------------------------------------------------

def test_a_success_goes_stale_within_a_few_sweeps():
    """Seven days was written when the only evidence was organic traffic.

    There is now a sweep that actively tests every configured connector once a
    day, so `last_success_at` resets daily on its own. Under the old threshold
    a connector could go a full week with every one of those seven sweeps
    recording nothing, and still be reported as working -- and, since the
    alerting reads this same status, still not be mentioned to anyone.

    Bounded in sweeps rather than in days so the two constants cannot drift
    apart: raising the beat interval without revisiting this would quietly
    restore the old behaviour.
    """
    from datetime import timedelta

    from app.services import connector_health as ch

    sweep = timedelta(seconds=60 * 60 * 24)
    assert ch.FRESH_FOR <= 3 * sweep, (
        f"a connector may be called healthy after {ch.FRESH_FOR // sweep} silent sweeps"
    )
    # And not so tight that one missed run trips it.
    assert ch.FRESH_FOR >= 2 * sweep


def test_the_sweep_really_does_run_daily():
    """The bound above is only meaningful if the schedule is what it claims."""
    pytest.importorskip("celery")
    from app.core.celery_app import celery_app

    from celery.schedules import crontab
    entry = celery_app.conf.beat_schedule["daily-connector-check"]
    # A crontab, not an interval. An interval counts from worker boot and
    # resets on every restart, so during active deployment the daily sweep
    # never fired at all -- cards read "checked 2 days ago" and meant it.
    assert isinstance(entry["schedule"], crontab)


def test_the_stale_summary_does_not_quote_a_threshold_of_its_own():
    """It read "last worked more than a week ago" while the constant said three
    days. A sentence shown to a clerk must not carry a number the code stopped
    using -- that is a small lie they have no way to check."""
    from datetime import datetime, timedelta, timezone

    from app.services import connector_health as ch

    now = datetime.now(timezone.utc)
    health = ch.Health(connector="ai", status=ch.STALE,
                       last_success_at=now - ch.FRESH_FOR - timedelta(days=1))
    assert str(ch.FRESH_FOR.days) in health.summary()
