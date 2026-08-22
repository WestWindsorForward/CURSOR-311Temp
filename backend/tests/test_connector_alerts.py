"""When something breaks, somebody is told -- once.

The daily sweep already found the failures. What it did with them was write a
row and a log line, which for a self-hosted town is the same as nothing: the
software knew on Tuesday and the clerk found out on Monday, from a resident who
never got their confirmation email.

Two ways to get this wrong, and the second is the one that actually happens.
Silence is the obvious failure. The other is an email every morning saying the
same thing, which is filtered into a folder within a fortnight -- and then the
one that mattered is in the folder too. So the rules under test are as much
about *not* sending as about sending.

No database, no mail server and no FastAPI here: the decisions are pure, which
is the only reason they can be checked at all in CI.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import connector_alerts as A


NOW = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


class FakeHealth:
    """Enough of a health row for the planner. Mirrors connector_health.Health."""

    def __init__(self, connector, status, *, alerted_level=None, alerted_at=None,
                 last_error=None, last_success_at=None):
        self.connector = connector
        self.status = status
        self.alerted_level = alerted_level
        self.alerted_at = alerted_at
        self.last_error = last_error
        self.last_success_at = last_success_at

    def summary(self):
        return f"{self.status} summary"


# ---------------------------------------------------------------------------
# Which states are worth an email
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("working", A.HEALTHY),
    ("unknown", A.HEALTHY),
    ("failing", A.AT_RISK),
    ("stale", A.AT_RISK),
    ("down", A.BROKEN),
])
def test_the_status_maps_onto_a_level(status, expected):
    assert A.alert_level(status) == expected


def test_stale_is_a_warning_and_not_health():
    """No successful call in over a week is not "fine".

    It is the exact state a revoked key produces on a connector nobody has
    exercised, and treating it as healthy is how an expired credential survives
    until the morning somebody needs it.
    """
    assert A.alert_level("stale") == A.AT_RISK


def test_the_first_failed_call_is_the_alert_worth_having():
    """A clerk told on the first failure has days to renew a secret. A clerk
    told once sign-in is fully down is being informed of an outage they are
    already standing in."""
    assert A.alert_level("failing") == A.AT_RISK
    assert A.alert_level("down") == A.BROKEN


# ---------------------------------------------------------------------------
# When to actually send
# ---------------------------------------------------------------------------

def test_a_connector_that_has_always_worked_generates_nothing():
    assert A.decide(level=A.HEALTHY, previous_level=None, alerted_at=None, now=NOW) is None


def test_a_new_problem_is_announced():
    assert A.decide(level=A.AT_RISK, previous_level=None, alerted_at=None, now=NOW) == "new"


def test_getting_worse_is_announced_again():
    assert A.decide(level=A.BROKEN, previous_level=A.AT_RISK,
                    alerted_at=NOW - timedelta(hours=1), now=NOW) == "escalated"


def test_something_still_broken_tomorrow_is_mentioned_again():
    """One interval for both levels was wrong in the direction that matters.

    Under a flat weekly reminder, staff sign-in could be completely down on the
    Monday and not raised again until the following Monday. A week is a long
    time for something to be broken, and the mail stops the moment it recovers.
    """
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=1), now=NOW) == "reminder"


def test_something_broken_is_not_mentioned_twice_in_one_day():
    """Daily, not per sweep. A manual recheck must not send a second copy."""
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(hours=6), now=NOW) is None


def test_something_merely_at_risk_does_not_nag_daily():
    """Not yet an outage, so it does not earn an outage's cadence."""
    assert A.decide(level=A.AT_RISK, previous_level=A.AT_RISK,
                    alerted_at=NOW - timedelta(days=1), now=NOW) is None


def test_but_three_days_of_intermittent_failure_is_no_longer_a_blip():
    assert A.decide(level=A.AT_RISK, previous_level=A.AT_RISK,
                    alerted_at=NOW - timedelta(days=3), now=NOW) == "reminder"


def test_broken_is_chased_harder_than_at_risk():
    """The property, rather than the two numbers: whatever the intervals are,
    an outage must never be raised less often than a warning."""
    assert A.REMIND_AFTER[A.BROKEN] < A.REMIND_AFTER[A.AT_RISK]


def test_a_level_with_no_interval_configured_errs_towards_saying_something():
    """The guard is for a real future mistake: adding a third severity to RANK
    and forgetting to give it a cadence. Silence would be the wrong default --
    a connector could sit in that state forever, mentioned exactly once."""
    assert A.decide(level=A.AT_RISK, previous_level=A.AT_RISK,
                    alerted_at=NOW - timedelta(days=1), now=NOW,
                    remind_after={A.BROKEN: timedelta(days=1)}) == "reminder"


def test_every_level_that_can_be_alerted_on_has_an_interval():
    """Which is what stops that guard from ever being needed."""
    for level in A.RANK:
        if level != A.HEALTHY:
            assert level in A.REMIND_AFTER, f"{level} would fall back to a default"


def test_partial_improvement_is_not_worth_an_email():
    """"Good news, it is now only intermittently broken" is not news."""
    assert A.decide(level=A.AT_RISK, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=1), now=NOW) is None


def test_recovery_is_worth_an_email_but_only_if_we_complained():
    assert A.decide(level=A.HEALTHY, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=1), now=NOW) == "recovered"
    assert A.decide(level=A.HEALTHY, previous_level=A.HEALTHY,
                    alerted_at=None, now=NOW) is None


def test_a_missing_timestamp_errs_towards_sending():
    """A duplicate email is a smaller failure than a silent outage."""
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN, alerted_at=None, now=NOW) == "reminder"


def test_a_naive_timestamp_does_not_explode():
    """Some databases hand back timestamps without a timezone. Subtracting one
    of those from an aware `now` raises, and it would raise inside the daily
    sweep -- taking the health write with it."""
    naive = (NOW - timedelta(days=9)).replace(tzinfo=None)
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN, alerted_at=naive, now=NOW) == "reminder"


# ---------------------------------------------------------------------------
# Planning across connectors
# ---------------------------------------------------------------------------

def test_only_the_connectors_that_changed_are_in_the_plan():
    healths = [
        FakeHealth("email", "working"),
        FakeHealth("identity", "down"),
        FakeHealth("sms", "down", alerted_level=A.BROKEN, alerted_at=NOW - timedelta(hours=2)),
    ]
    plan = A.plan(healths, now=NOW)
    assert [a.connector for a in plan] == ["identity"]


def test_the_worst_thing_is_first():
    healths = [
        FakeHealth("translation", "failing"),
        FakeHealth("identity", "down"),
    ]
    plan = A.plan(healths, now=NOW)
    assert [a.connector for a in plan] == ["identity", "translation"]


def test_a_row_with_no_alert_state_is_treated_as_never_alerted():
    class Bare:
        connector = "ai"
        status = "down"

    plan = A.plan([Bare()], now=NOW)
    assert [(a.connector, a.kind) for a in plan] == [("ai", "new")]


# ---------------------------------------------------------------------------
# What the email says
# ---------------------------------------------------------------------------

def test_the_subject_names_the_service_in_words_a_clerk_uses():
    """"sms is down" is not something to forward to a township administrator."""
    plan = A.plan([FakeHealth("sms", "down")], now=NOW)
    assert A.subject(plan, "Montclair") == "[Montclair] Text messages to residents not working"


def test_the_subject_counts_rather_than_lists_when_several_break():
    plan = A.plan([FakeHealth("sms", "down"), FakeHealth("identity", "down")], now=NOW)
    assert A.subject(plan, "Montclair") == "[Montclair] 2 services not working"


def test_broken_outranks_at_risk_in_the_subject():
    plan = A.plan([FakeHealth("sms", "failing"), FakeHealth("identity", "down")], now=NOW)
    assert "not working" in A.subject(plan, "Montclair")
    assert "may stop" not in A.subject(plan, "Montclair")


def test_an_unknown_connector_still_gets_a_readable_name():
    """A new integration is the one most likely to be misconfigured, so it must
    not be the one that cannot raise an alarm."""
    assert A.label("govtech:accela") == "Accela"
    assert A.label("sms") == "Text messages to residents"


def test_the_body_carries_the_providers_own_words():
    """A clerk searching the web for their error needs the actual string. Our
    paraphrase of it resolves to nothing."""
    plan = A.plan([FakeHealth("identity", "down", last_error="AADSTS7000222: client secret expired")],
                  now=NOW)
    body = A.compose(plan, town="Montclair", settings_url="https://montclair.gov/admin", now=NOW)
    assert "AADSTS7000222" in body["text"]
    assert "AADSTS7000222" in body["html"]
    assert "https://montclair.gov/admin" in body["text"]


def test_the_body_says_when_it_last_worked():
    plan = A.plan([FakeHealth("email", "down", last_success_at=NOW - timedelta(days=3))], now=NOW)
    body = A.compose(plan, town="T", now=NOW)
    assert "3 days ago" in body["text"]


def test_a_connector_that_never_worked_says_so_rather_than_guessing():
    plan = A.plan([FakeHealth("email", "down")], now=NOW)
    assert "never" in A.compose(plan, town="T", now=NOW)["text"]


def test_a_provider_error_cannot_inject_markup_into_the_email():
    """`last_error` is remote text of unbounded shape, and it is being placed
    into HTML we send. Anything that reaches an inbox unescaped is a hole."""
    nasty = '<img src=x onerror="alert(1)">'
    plan = A.plan([FakeHealth("ai", "down", last_error=nasty)], now=NOW)
    html = A.compose(plan, town="T", now=NOW)["html"]
    assert "<img" not in html
    assert "&lt;img" in html


def test_broken_and_at_risk_are_separate_headings():
    """Filing "failing intermittently" under "not working" is the same class of
    lie as a green tick on a revoked key.

    The at-risk heading used to read "May stop working", which predicted an
    outcome the sweep has no basis for while telling the reader nothing to act
    on. It states the observed condition now.
    """
    plan = A.plan([FakeHealth("sms", "failing"), FakeHealth("identity", "down")], now=NOW)
    text = A.compose(plan, town="T", now=NOW)["text"]
    assert "Not working right now:" in text
    assert "Failing intermittently:" in text
    assert "may stop" not in text.lower()
    assert text.index("Not working right now:") < text.index("Failing intermittently:")


# ---------------------------------------------------------------------------
# Sending, and remembering that we sent
# ---------------------------------------------------------------------------

class FakeDB:
    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_one_digest_covers_everything_rather_than_one_email_each():
    """A cloud outage takes four connectors down together. Four separate
    alarms about one event is how people learn to delete them unread."""
    sent = []
    recorded = []

    async def fake_remember(db, alerts, *, now=None):
        recorded.extend(a.connector for a in alerts)

    A_remember, A.remember = A.remember, fake_remember
    try:
        result = await A.dispatch(
            FakeDB(),
            healths=[FakeHealth("ai", "down"), FakeHealth("translation", "down")],
            send=lambda **kw: sent.append(kw) or True,
            recipients=["clerk@montclair.gov"],
            town="Montclair",
            now=NOW,
        )
    finally:
        A.remember = A_remember

    assert len(sent) == 1
    assert result["sent"] is True
    assert sorted(recorded) == ["ai", "translation"]


@pytest.mark.asyncio
async def test_every_administrator_is_written_to_separately():
    """One town's administrators are not disclosed to each other's mail
    providers, and one bad address does not drop the whole batch."""
    sent = []

    async def fake_remember(db, alerts, *, now=None):
        pass

    A_remember, A.remember = A.remember, fake_remember
    try:
        await A.dispatch(
            FakeDB(),
            healths=[FakeHealth("ai", "down")],
            send=lambda **kw: sent.append(kw["to"]) or True,
            recipients=["a@t.gov", "b@t.gov"],
            town="T",
            now=NOW,
        )
    finally:
        A.remember = A_remember

    assert sent == ["a@t.gov", "b@t.gov"]


@pytest.mark.asyncio
async def test_one_failing_address_does_not_stop_the_others():
    delivered = []

    async def fake_remember(db, alerts, *, now=None):
        pass

    def send(**kw):
        if kw["to"] == "bad@t.gov":
            raise RuntimeError("550 no such mailbox")
        delivered.append(kw["to"])
        return True

    A_remember, A.remember = A.remember, fake_remember
    try:
        result = await A.dispatch(
            FakeDB(), healths=[FakeHealth("ai", "down")], send=send,
            recipients=["bad@t.gov", "good@t.gov"], town="T", now=NOW,
        )
    finally:
        A.remember = A_remember

    assert delivered == ["good@t.gov"]
    assert result["sent"] is True


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_nothing_could_be_sent():
    """The state write says "we told them". A mail server that is itself down
    must not silently consume the alert -- tomorrow's sweep has to try again."""
    recorded = []

    async def fake_remember(db, alerts, *, now=None):
        recorded.append(alerts)

    A_remember, A.remember = A.remember, fake_remember
    try:
        result = await A.dispatch(
            FakeDB(), healths=[FakeHealth("ai", "down")],
            send=lambda **kw: False, recipients=["a@t.gov"], town="T", now=NOW,
        )
    finally:
        A.remember = A_remember

    assert result["sent"] is False
    assert recorded == []


@pytest.mark.asyncio
async def test_a_town_with_no_administrator_address_is_not_an_exception():
    result = await A.dispatch(
        FakeDB(), healths=[FakeHealth("ai", "down")],
        send=lambda **kw: True, recipients=[], town="T", now=NOW,
    )
    assert result["sent"] is False
    assert result["reason"] == "no-recipients"


@pytest.mark.asyncio
async def test_a_quiet_day_sends_nothing_at_all():
    calls = []
    result = await A.dispatch(
        FakeDB(), healths=[FakeHealth("ai", "working"), FakeHealth("email", "working")],
        send=lambda **kw: calls.append(kw) or True, recipients=["a@t.gov"], town="T", now=NOW,
    )
    assert calls == []
    assert result["sent"] is False


def test_a_recovery_clears_the_state_rather_than_recording_health():
    """If it stored a level instead, the connector's next failure would be
    compared against a stale entry and reported as a reminder -- so the email
    saying it had broken again would arrive a week late, or never."""
    recovered = A.Alert(connector="ai", level=A.HEALTHY, kind="recovered", summary="")
    assert A.next_state(recovered, now=NOW) == (None, None)


def test_an_ongoing_problem_records_its_level_and_the_time():
    broken = A.Alert(connector="ai", level=A.BROKEN, kind="new", summary="")
    assert A.next_state(broken, now=NOW) == (A.BROKEN, NOW)


def test_the_recorded_state_is_what_silences_tomorrow():
    """The two halves have to agree: whatever `next_state` writes is what
    `decide` reads back the following day, and a mismatch means either daily
    spam or permanent silence."""
    broken = A.Alert(connector="ai", level=A.BROKEN, kind="new", summary="")
    level, at = A.next_state(broken, now=NOW)
    assert A.decide(level=A.BROKEN, previous_level=level, alerted_at=at,
                    now=NOW + timedelta(hours=2)) is None
    assert A.decide(level=A.BROKEN, previous_level=level, alerted_at=at,
                    now=NOW + timedelta(days=1)) == "reminder"


# ---------------------------------------------------------------------------
# "I know about this one"
# ---------------------------------------------------------------------------

def mute(days_left=5, level=A.BROKEN):
    return {"muted_until": NOW + timedelta(days=days_left), "muted_level": level}


def test_a_muted_problem_stops_emailing():
    """Without this the only way to stop a daily reminder about something you
    are already dealing with is to filter the sender, and that takes the next
    unrelated alert with it."""
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=5), now=NOW, **mute()) is None


def test_a_mute_expires_on_its_own():
    """A problem muted and forgotten has to come back, not disappear."""
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=9), now=NOW,
                    **mute(days_left=-1)) == "reminder"


def test_muting_a_warning_does_not_buy_silence_over_an_outage():
    """The one outcome a dismiss button must never be able to produce.

    Acknowledging "the AI key failed once" and thereby not being told when AI
    stops working entirely would make the button actively dangerous.
    """
    assert A.decide(level=A.BROKEN, previous_level=A.AT_RISK,
                    alerted_at=NOW - timedelta(days=1), now=NOW,
                    **mute(level=A.AT_RISK)) == "escalated"


def test_muting_an_outage_covers_the_outage_itself():
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=5), now=NOW,
                    **mute(level=A.BROKEN)) is None


def test_a_mute_never_suppresses_the_recovery_notice():
    """The good news is short, it arrives once, and it is how somebody learns
    they can stop worrying."""
    assert A.decide(level=A.HEALTHY, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=1), now=NOW,
                    **mute()) == "recovered"


def test_an_unreadable_mute_level_does_not_suppress_anything_worse():
    """A row we cannot interpret must fail towards telling somebody."""
    assert A.decide(level=A.BROKEN, previous_level=A.BROKEN,
                    alerted_at=NOW - timedelta(days=5), now=NOW,
                    muted_until=NOW + timedelta(days=5), muted_level=None) is None
    # ...but a genuinely unknown, higher-ranked level is not covered by it.
    assert A.muted(level=A.BROKEN, muted_until=NOW + timedelta(days=5),
                   muted_level=A.AT_RISK, now=NOW) is False


def test_a_naive_mute_deadline_does_not_explode():
    naive = (NOW + timedelta(days=5)).replace(tzinfo=None)
    assert A.muted(level=A.BROKEN, muted_until=naive, muted_level=A.BROKEN, now=NOW) is True


def stored_row(**overrides):
    """A stand-in for one `connector_health` table row.

    Attribute names are the column names, so what comes out of `to_health` is
    what the real sweep would build from the real row.
    """
    class Row:
        connector = "ai"
        provider = None
        last_success_at = None
        last_error_at = NOW - timedelta(days=5)
        last_error = "the key was rejected"
        consecutive_failures = 5
        total_successes = 0
        total_failures = 5
        alerted_level = A.BROKEN
        alerted_at = NOW - timedelta(days=5)
        last_result = None
        verifiable = True
        alert_muted_until = None
        alert_muted_level = None

    for key, value in overrides.items():
        setattr(Row, key, value)
    return Row()


def test_the_planner_reads_the_mute_off_the_health_the_sweep_builds():
    """Through the real `to_health`, not an object shaped to make it pass.

    This test used to hand the planner an ad-hoc class carrying
    `alert_muted_level` directly. `Health` had no such field and `to_health`
    never copied it, so in production the planner read `None`, fell back to
    treating every mute as covering `broken`, and the mute logic did not do what
    the test said it did. The bug lived behind a passing assertion for as long
    as the collaborator was faked.
    """
    from app.services.connector_health import to_health

    health = to_health(stored_row(alert_muted_until=NOW + timedelta(days=3),
                                  alert_muted_level=A.BROKEN), now=NOW)
    assert health.alert_muted_level == A.BROKEN, "to_health dropped the muted level"
    assert A.plan([health], now=NOW) == []


def test_an_escalation_breaks_through_a_mute_built_by_the_sweep():
    """The invariant the missing field silently repealed.

    Something acknowledged while it was failing intermittently, that then goes
    fully down, is new information. If `alert_muted_level` does not survive
    `to_health`, the planner compares against a default of `broken` and the
    outage is suppressed for the rest of the week -- the one outcome a dismiss
    button must never be able to produce.
    """
    from app.services.connector_health import to_health

    # Five failures in a row, so `classify` derives "down" -> level broken.
    health = to_health(stored_row(alerted_level=A.AT_RISK,
                                  alert_muted_until=NOW + timedelta(days=3),
                                  alert_muted_level=A.AT_RISK), now=NOW)
    assert [a.kind for a in A.plan([health], now=NOW)] == ["escalated"]


def test_a_row_predating_the_mute_columns_is_simply_not_muted():
    """Rows written before the migration have neither attribute."""
    class Old:
        connector = "ai"
        status = "down"

    assert [a.kind for a in A.plan([Old()], now=NOW)] == ["new"]


def test_recovery_clears_the_mute_so_the_next_failure_is_heard():
    """The administrator acknowledged the old problem, not a future one."""
    recovered = A.Alert(connector="ai", level=A.HEALTHY, kind="recovered", summary="")
    assert A.next_state(recovered, now=NOW) == (None, None)


def test_the_email_says_how_to_stop_it():
    """Somebody who cannot find the off switch filters the sender instead."""
    plan = A.plan([FakeHealth("ai", "down")], now=NOW)
    body = A.compose(plan, town="T", settings_url="https://t.gov/admin", now=NOW)
    assert "Mute" in body["text"] or "mute" in body["text"]
    assert str(A.MUTE_FOR.days) in body["text"]


def test_a_mute_is_shorter_than_forever():
    assert timedelta(days=1) <= A.MUTE_FOR <= timedelta(days=30)


def test_an_escalation_throws_the_mute_away():
    """It already broke through once. Leaving the old deadline in place would
    re-suppress the outage on tomorrow's sweep, so the dismiss button would buy
    a week of silence over an outage after all -- one day late."""
    escalated = A.Alert(connector="ai", level=A.BROKEN, kind="escalated", summary="")
    assert A.clears_mute(escalated) is True


def test_a_recovery_throws_the_mute_away():
    assert A.clears_mute(A.Alert(connector="ai", level=A.HEALTHY, kind="recovered", summary="")) is True


def test_a_routine_reminder_leaves_the_mute_alone():
    """Muting is not undone by the passage of time alone -- that is what the
    deadline is for."""
    for kind in ("new", "reminder"):
        assert A.clears_mute(A.Alert(connector="ai", level=A.BROKEN, kind=kind, summary="")) is False


# ---------------------------------------------------------------------------
# The endpoint behind the button
# ---------------------------------------------------------------------------

def _system_api() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "app/api/system.py").read_text()


def test_there_is_somewhere_to_press_mute():
    source = _system_api()
    assert '"/connectors/{connector}/mute"' in source
    assert "async def mute_connector_alerts" in source


def test_muting_cannot_be_forever():
    """An unbounded mute is a permanently disabled alarm that nobody remembers
    turning off."""
    source = _system_api()
    assert "0 <= days <= 90" in source


def _mute_endpoint_source() -> str:
    source = _system_api()
    block = source[source.index("async def mute_connector_alerts"):]
    return block[:block.index("\n@router") if "\n@router" in block else len(block)]


def test_muting_an_unknown_connector_does_not_invent_one():
    """`connector` is an unvalidated path segment. Creating a row for whatever
    name it is given would let any admin request insert arbitrary junk into a
    table the setup page renders -- the same hole that was closed on the test
    endpoint.

    One row the endpoint *may* create: `health:<check>`, which carries the mute
    for a proactive health check. Those checks are computed fresh every run and
    have nothing to record against, so there is no pre-existing row to find.
    That is the narrow exception, and it is only safe because of the allowlist
    asserted here -- the prefix is a namespace, not a licence.
    """
    block = _mute_endpoint_source()
    assert "status_code=404" in block

    if "ConnectorHealth(" not in block:
        return
    creates = block.index("ConnectorHealth(")
    assert "CHECK_KEYS" in block, "the mute endpoint creates health rows with no allowlist"
    assert block.index("CHECK_KEYS") < creates, \
        "the mute endpoint creates a health row before checking the allowlist"
    assert "not in CHECK_KEYS" in block


def test_only_a_real_health_check_can_be_muted_into_existence():
    """The allowlist is the whole defence, so it has to come from the module
    that actually emits the checks. A list copied next to the route would drift
    the moment a check is renamed, and drift here is either a dead mute button
    or a name nobody validates."""
    block = _mute_endpoint_source()
    if "ConnectorHealth(" not in block:
        return
    assert "from app.services.proactive_health import CHECK_KEYS" in block


def test_muting_changes_no_health_field():
    """Silencing the email must not touch what the card reports. A dismiss that
    also cleared the red badge would turn a known problem into an invisible
    one."""
    source = _system_api()
    block = source[source.index("async def mute_connector_alerts"):]
    block = block[:block.index("\ndef ch_classify")]
    for field in ("consecutive_failures", "last_error", "last_success_at", "total_failures"):
        assert f"row.{field}" not in block, f"the mute endpoint rewrites {field}"


def test_the_card_can_tell_that_alerts_are_muted():
    """A mute that silenced the email and left no trace on screen would be
    indistinguishable from the alerting being broken."""
    assert '"alerts_muted_until"' in _system_api()


def test_the_mute_button_appears_exactly_where_an_alert_exists():
    """The frontend decides whether to offer "Mute alerts" from a list of
    statuses. If that list drifts from `alert_level`, the button either offers
    to silence something that was never going to make a sound, or hides on a
    connector that is emailing every day.

    `stale` is why this is pinned rather than eyeballed: it displays as "not
    checked yet" and it alerts, so neither the raw status nor what the card
    shows answers the question on its own.
    """
    import re
    from pathlib import Path

    ui = (Path(__file__).resolve().parents[2] / "frontend/src/components/capabilityUI.tsx").read_text()
    m = re.search(r"ALERTING_STATUSES = \[(.*?)\]", ui, re.S)
    assert m, "the frontend no longer declares which statuses alert"
    frontend = set(re.findall(r"'([a-z]+)'", m.group(1)))

    backend = {s for s in ("down", "failing", "stale", "working", "unknown")
               if A.alert_level(s) != A.HEALTHY}
    assert frontend == backend, (
        f"the mute button and the alerting rules disagree: "
        f"frontend={sorted(frontend)} backend={sorted(backend)}"
    )
