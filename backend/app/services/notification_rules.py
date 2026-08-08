"""Pure notification-eligibility rules.

These functions decide whether a given staff member should receive a
notification for a request event, based solely on their stored notification
preferences (the toggles in the Notification Settings modal) plus whether the
request is assigned to them. They contain no I/O so the enforcement is
unit-testable and cannot silently diverge between dispatch sites.

Events:
    "new_requests"    — a request was submitted to the staffer's department
    "status_changes"  — a request's status changed
    "comments"        — a comment was added to a request

Preference keys (on User.notification_preferences JSON):
    email_new_requests / email_status_changes / email_comments  (default True)
    sms_new_requests   / sms_status_changes                     (default False)
    email_assigned_only                                         (default False)
"""

from typing import List, Optional, Sequence, Tuple

_EMAIL_KEY = {
    "new_requests": "email_new_requests",
    "status_changes": "email_status_changes",
    "comments": "email_comments",
}

# Comments have no SMS channel by design.
_SMS_KEY = {
    "new_requests": "sms_new_requests",
    "status_changes": "sms_status_changes",
}


def wants_event_email(prefs: Optional[dict], event: str) -> bool:
    """Whether the staffer wants an email for this event (defaults on)."""
    key = _EMAIL_KEY.get(event)
    if not key:
        return False
    return bool((prefs or {}).get(key, True))


def wants_event_sms(prefs: Optional[dict], event: str) -> bool:
    """Whether the staffer wants an SMS for this event (defaults off)."""
    key = _SMS_KEY.get(event)
    if not key:
        return False
    return bool((prefs or {}).get(key, False))


def pick_recipient_group(
    assignee,
    department_staff: Sequence,
    admins: Sequence,
) -> List:
    """Who a request event goes to: the first non-empty tier wins.

    Tier order: the specifically-assigned person; else the routed department's
    staff; else the town's active admins. The admin tier exists because small
    towns routinely have no department memberships at all (the demo site is one
    admin, zero memberships) — without it, every "notify staff" preference is
    dead letter: recipients resolve to nobody and the toggles are never even
    consulted. Per-user preference filtering (should_notify_staff) still runs
    on whichever tier is chosen; this only decides who is *considered*.
    """
    if assignee is not None:
        return [assignee]
    if department_staff:
        return list(department_staff)
    return list(admins)


def should_notify_staff(
    prefs: Optional[dict],
    event: str,
    *,
    is_assigned_to_me: bool,
    is_actor: bool = False,
    sms_enabled_globally: bool = False,
) -> Tuple[bool, bool]:
    """Return (send_email, send_sms) for one staff member and one event.

    Enforces, in order:
      1. Never notify the actor about their own action.
      2. "Assigned Only" — if set, notify only when the request is assigned to
         this person (by username).
      3. The per-event email/SMS toggles (SMS additionally requires the SMS
         module to be enabled globally).
    """
    prefs = prefs or {}
    if is_actor:
        return (False, False)
    if prefs.get("email_assigned_only", False) and not is_assigned_to_me:
        return (False, False)
    send_email = wants_event_email(prefs, event)
    send_sms = bool(sms_enabled_globally) and wants_event_sms(prefs, event)
    return (send_email, send_sms)
