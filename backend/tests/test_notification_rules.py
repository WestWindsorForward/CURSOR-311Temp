"""Tests for notification-eligibility enforcement (the Notification Settings
toggles + Assigned Only). Pure logic, no DB — proves the stored preferences are
actually applied at dispatch time."""

from app.services.notification_rules import (
    pick_recipient_group,
    should_notify_staff,
    wants_event_email,
    wants_event_sms,
)


# ---- defaults --------------------------------------------------------------

def test_email_defaults_on_sms_defaults_off():
    # Empty prefs: emails default ON, SMS default OFF.
    assert wants_event_email({}, "new_requests") is True
    assert wants_event_email({}, "status_changes") is True
    assert wants_event_email({}, "comments") is True
    assert wants_event_sms({}, "new_requests") is False
    assert wants_event_sms({}, "status_changes") is False


def test_comments_have_no_sms_channel():
    # Even if a stray key were set, comments never send SMS.
    assert wants_event_sms({"sms_comments": True}, "comments") is False


# ---- per-event email toggles are honored -----------------------------------

def test_status_changes_toggle_off_suppresses_email():
    prefs = {"email_status_changes": False}
    send_email, send_sms = should_notify_staff(
        prefs, "status_changes", is_assigned_to_me=False)
    assert send_email is False


def test_comments_toggle_off_suppresses_email():
    prefs = {"email_comments": False}
    send_email, _ = should_notify_staff(prefs, "comments", is_assigned_to_me=False)
    assert send_email is False


def test_new_requests_toggle_on_sends_email():
    send_email, _ = should_notify_staff({}, "new_requests", is_assigned_to_me=False)
    assert send_email is True


# ---- Assigned Only ---------------------------------------------------------

def test_assigned_only_blocks_unassigned():
    prefs = {"email_assigned_only": True, "email_new_requests": True}
    send_email, send_sms = should_notify_staff(
        prefs, "new_requests", is_assigned_to_me=False)
    assert (send_email, send_sms) == (False, False)


def test_assigned_only_allows_when_assigned():
    prefs = {"email_assigned_only": True, "email_new_requests": True}
    send_email, _ = should_notify_staff(
        prefs, "new_requests", is_assigned_to_me=True)
    assert send_email is True


def test_assigned_only_applies_to_status_and_comments():
    prefs = {"email_assigned_only": True}
    for event in ("status_changes", "comments"):
        send_email, _ = should_notify_staff(prefs, event, is_assigned_to_me=False)
        assert send_email is False, event
        send_email, _ = should_notify_staff(prefs, event, is_assigned_to_me=True)
        assert send_email is True, event


# ---- actor suppression -----------------------------------------------------

def test_actor_is_never_notified():
    prefs = {"email_status_changes": True}
    send_email, send_sms = should_notify_staff(
        prefs, "status_changes", is_assigned_to_me=True, is_actor=True)
    assert (send_email, send_sms) == (False, False)


# ---- SMS gating ------------------------------------------------------------

def test_sms_requires_global_module_and_pref():
    prefs = {"sms_new_requests": True}
    # Pref on but module off globally -> no SMS.
    _, send_sms = should_notify_staff(
        prefs, "new_requests", is_assigned_to_me=False, sms_enabled_globally=False)
    assert send_sms is False
    # Pref on and module on -> SMS.
    _, send_sms = should_notify_staff(
        prefs, "new_requests", is_assigned_to_me=False, sms_enabled_globally=True)
    assert send_sms is True


def test_sms_off_by_default_even_with_module_on():
    _, send_sms = should_notify_staff(
        {}, "status_changes", is_assigned_to_me=True, sms_enabled_globally=True)
    assert send_sms is False


# ---- recipient tiers ---------------------------------------------------------
#
# The live-site failure mode: every new request auto-routes to a department,
# the department has no members, and there is no assignee — so recipients
# resolved to nobody and the preference toggles were never consulted. The admin
# tier is the floor that keeps them meaningful.

def test_assignee_wins_over_everyone():
    got = pick_recipient_group("assignee", ["dept-a", "dept-b"], ["admin"])
    assert got == ["assignee"]


def test_department_staff_when_no_assignee():
    got = pick_recipient_group(None, ["dept-a", "dept-b"], ["admin"])
    assert got == ["dept-a", "dept-b"]


def test_admins_are_the_floor_when_department_is_empty():
    # Routed department with zero members — the demo town's exact shape.
    got = pick_recipient_group(None, [], ["admin1", "admin2"])
    assert got == ["admin1", "admin2"]


def test_nobody_at_all_is_an_empty_list_not_an_error():
    assert pick_recipient_group(None, [], []) == []
