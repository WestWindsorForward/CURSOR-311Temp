"""What leaves the building, and what does not.

`_request_payload` builds the record Pinpoint pushes to a county's Accela or
Tyler system. Two ways to get it wrong and they pull in opposite directions:

  too little   the external work order shows an open job the town closed three
               weeks ago, because the resolution never went with it
  too much     a resident's PII, a staff member's private note, or the reason a
               report was flagged ends up in a vendor system the town does not
               control and cannot scrub

So both halves are pinned. The allow-list below is the whole payload; adding a
column to ServiceRequest and quietly including it here fails this file.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "backend/app/tasks/integrations.py"
MODELS = ROOT / "backend/app/models.py"


def _payload_keys() -> set:
    """Keys in the dict `_request_payload` builds, excluding the PII block."""
    source = TASKS.read_text()
    start = source.index("    payload = {")
    end = source.index('    if _flag(config, "share_pii")')
    return set(re.findall(r'"(\w+)":', source[start:end]))


def _pii_keys() -> set:
    source = TASKS.read_text()
    start = source.index('    if _flag(config, "share_pii")')
    return set(re.findall(r'"(\w+)":', source[start:start + 400]))


def _model_columns() -> list:
    source = MODELS.read_text()
    block = source[source.index("class ServiceRequest(Base)"):]
    block = block[:block.index("\nclass ")]
    return re.findall(r"^    (\w+) = Column", block, re.M)


# ---------------------------------------------------------------- what goes

REQUIRED = {
    # Identity and what was reported
    "service_request_id", "service_code", "service_name", "description",
    "address", "lat", "long", "status", "requested_datetime",
    # Attachments, as links only
    "media_urls",
    # Routing
    "priority", "assigned_to", "assigned_department", "due_date",
    # Outcome -- the half that was missing
    "closed_datetime", "closed_substatus", "completion_message",
    "completion_photo_url", "updated_datetime",
    # Context the far end can act on
    "source", "preferred_language", "matched_asset", "custom_fields",
}


def test_the_work_order_carries_everything_it_should():
    missing = REQUIRED - _payload_keys()
    assert not missing, (
        f"the outbound work order is missing {sorted(missing)}. A field the "
        f"external system never receives is one the town has to re-key by hand."
    )


def test_the_resolution_travels_with_the_record():
    """`completion_message` was already pushed on a status change and was absent
    from the record push, so a platform that only ingests the initial create
    never learned how anything ended."""
    keys = _payload_keys()
    for field in ("completion_message", "closed_datetime", "closed_substatus"):
        assert field in keys


# ------------------------------------------------------------ what does not

NEVER = {
    # Internal identifiers and storage
    "id", "location", "assigned_department_id",
    # Encrypted PII columns. The plaintext equivalents are sent only behind the
    # share_pii flag; these are the ciphertext and must never be sent at all.
    "_first_name_encrypted", "_last_name_encrypted",
    "_email_encrypted", "_phone_encrypted",
    # Written by staff, for staff.
    "staff_notes",
    # Legal hold, and the moderation text explaining a flag. Neither is the
    # vendor's business and flag_reason can quote what a resident wrote.
    "flagged", "flag_reason",
    # Deletion and archival bookkeeping.
    "deleted_at", "deleted_by", "delete_justification", "archived_at",
    # Where the report is listed on Pinpoint's own tracker and map is a Pinpoint
    # setting, whether the resident chose it (is_public) or staff did
    # (public_archived). Neither says anything about the work order.
    "is_public", "public_archived",
    # AI output. Sending it copies a generated assessment of a resident's
    # report into a system the retention policy cannot reach -- the scrub
    # clears these columns here and would leave the vendor's copy untouched.
    "ai_analysis", "ai_summary", "ai_classification", "ai_analyzed_at",
}


@pytest.mark.parametrize("field", sorted(NEVER))
def test_internal_fields_do_not_leave_the_building(field):
    assert field not in _payload_keys(), (
        f"{field} is being pushed to external platforms. See NEVER in this file "
        f"for why it should not be."
    )


def test_pii_is_only_sent_behind_the_flag():
    """The four plaintext contact fields live in a block guarded by
    `share_pii`, and nowhere else."""
    assert _pii_keys() >= {"first_name", "last_name", "email", "phone"}
    assert not (_payload_keys() & {"first_name", "last_name", "email", "phone"}), (
        "contact details are in the unconditional payload"
    )


def test_every_column_is_either_sent_or_deliberately_not():
    """The list that stops this file going stale.

    A new column on ServiceRequest is a decision: it goes to the work order or
    it does not. Left unlisted, it silently does not -- which is the safe
    default but not a recorded one, and the next person cannot tell whether it
    was considered.
    """
    columns = set(_model_columns())
    accounted = REQUIRED | NEVER | {"first_name", "last_name", "email", "phone"}
    # Columns that map onto a differently-named payload key.
    accounted |= {"manual_priority_score", "priority", "due_datetime",
                  "requested_datetime", "media_urls", "matched_asset",
                  "custom_fields", "closed_datetime", "updated_datetime"}
    unaccounted = columns - accounted
    assert not unaccounted, (
        f"new ServiceRequest columns nobody has ruled on: {sorted(unaccounted)}. "
        f"Add each to REQUIRED (and to the payload) or to NEVER."
    )


def test_attachments_are_links_and_never_inline_blobs():
    """Photos are stored base64 in some deployments. Posting a few megabytes of
    data URI into a county API is how an integration gets rate-limited off."""
    source = TASKS.read_text()
    assert 'u.startswith("http")' in source, "media_urls no longer filters to links"
    assert 'sr.completion_photo_url.startswith("http")' in source, (
        "the completion photo is not filtered to a link"
    )
