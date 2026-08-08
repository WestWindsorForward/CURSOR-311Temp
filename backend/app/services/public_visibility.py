"""What the public may see in a listing, in one place.

Three separate reasons a report can be absent from the public tracker and map,
and none of them deletes, redacts or hides anything from staff:

  * the resident asked for it to be unlisted (`is_public = false`) — their
    choice, recorded at submission, and staff must never overwrite it;
  * staff archived this one report from public view (`public_archived = true`);
  * the town's archival policy has aged it out (`public_archive_days`), which
    is evaluated here at query time and never written to a row.

The policy is deliberately not a background job that stamps records. Evaluating
it in the WHERE clause makes it reversible and retroactive in the only way an
admin would expect: raise the number and last month's reports come back, clear
it and everything comes back, all without touching a single row. A job that
stamped `public_archived` on 40,000 reports would make "undo" mean "guess which
ones the job did and which ones a clerk did".

One function rather than a copy per endpoint, because the failure mode of a
missing clause is silent: the listing keeps working and simply shows reports it
should not. Every public listing calls this. Nothing else does — in particular
the by-id endpoints (see `open311.direct_link_filters`) must not, or every
tracking link to an archived report 404s and the resident concludes the town
deleted their report.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_

from app.models import ServiceRequest


def archive_cutoff(settings) -> Optional[datetime]:
    """The instant before which a CLOSED report drops off public listings.

    None means the town has no policy: NULL and 0 both read as "never archive",
    matching `retention_days` next door, where an unset number means the feature
    does nothing rather than the feature picking a number on the town's behalf.
    """
    days = getattr(settings, "public_archive_days", None) if settings is not None else None
    try:
        days = int(days) if days is not None else 0
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def publicly_listed_conditions(settings=None):
    """WHERE clauses defining what appears in a PUBLIC listing.

    Pass the SystemSettings row to apply the town's age-based policy; pass
    nothing (or None) and only the two per-row rules apply, which is the correct
    reading of "no policy configured".
    """
    conditions = [
        ServiceRequest.deleted_at.is_(None),
        # The resident's own choice. Never written by staff.
        ServiceRequest.is_public.is_(True),
        # Staff's per-report choice. A separate column precisely so that
        # unarchiving cannot resurrect a report the resident wanted unlisted.
        ServiceRequest.public_archived.is_(False),
    ]

    cutoff = archive_cutoff(settings)
    if cutoff is not None:
        conditions.append(
            or_(
                # Anything still being worked stays listed regardless of age;
                # the policy declutters finished business, it does not hide a
                # pothole that has been open for two years.
                ServiceRequest.status != "closed",
                # A closed report with no closing timestamp has no age to
                # measure. Rows predating `closed_datetime`, and rows closed
                # by a direct database edit, land here. Keeping them listed is
                # the conservative answer: inventing an age would silently
                # unlist history on the day a town first sets the number.
                ServiceRequest.closed_datetime.is_(None),
                ServiceRequest.closed_datetime >= cutoff,
            )
        )

    return tuple(conditions)
