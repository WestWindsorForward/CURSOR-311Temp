"""The platform-feedback module: one anonymous question, aggregates for staff.

A town switches this on with `system_settings.modules.platform_feedback`, which
is OFF on every existing install and every new one. Off means off, in all three
directions and independently of each other:

  * the resident portal renders no entry point;
  * `POST /api/feedback/platform` answers 404, so a saved request or a curl
    against a town that has not enabled the module stores nothing;
  * `GET /api/feedback/platform/statistics` answers 404, so the statistics page
    has no panel to draw.

The UI check is a courtesy. This file is the enforcement.

See models.PlatformFeedback for what is stored (a five-way categorical answer
and a timestamp, nothing else) and why there is no free-text field.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import PLATFORM_FEEDBACK_ANSWERS, PlatformFeedback, SystemSettings, User
from app.core.auth import get_current_staff
from app.schemas import PlatformFeedbackCreate, PlatformFeedbackStatisticsResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Its own store, like open311.py and system.py's `_cost_limiter`, so this
# module's ceiling is independent of theirs.
limiter = Limiter(key_func=get_remote_address)


# How many answers this town will store in any rolling 24 hours.
#
# The second half of the abuse story, and the half that actually holds. Rate
# limiting in this app is keyed on `get_remote_address`, and the app sits behind
# Caddy without `--forwarded-allow-ips`, so `request.client.host` is the same
# proxy address for every visitor: a "per-IP" limit here is really one global
# bucket (main.py documents this at the Limiter construction). A per-minute
# global bucket stops a burst and does nothing about a patient script, so the
# table needs a ceiling of its own.
#
# Not keyed to a client on purpose. The obvious next move is a salted hash of
# the IP with a short retention, and this module declines it: the whole point of
# the design is that a row cannot be linked to a person, and a per-client key is
# exactly such a link, hashed or not. Losing the tail of a day's answers is a
# cheaper failure than turning an anonymous table into a pseudonymous one.
#
# 2000/day is far above any real town's response rate and far below anything
# that grows the table without bound.
DAILY_SUBMISSION_CAP = 2000


async def _module_enabled(db: AsyncSession) -> bool:
    """Whether this town has switched the module on. Absent key reads as off."""
    try:
        row = (
            await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
        ).scalar_one_or_none()
        modules = (row.modules if row else None) or {}
        return bool(modules.get("platform_feedback", False))
    except Exception as exc:  # pragma: no cover - defensive
        # Fail closed. An unreadable settings row must not open an
        # unauthenticated write endpoint on a town that never asked for it.
        logger.warning("platform_feedback module check failed, treating as off: %s", exc)
        return False


def _off() -> HTTPException:
    """404, not 403.

    A town that has not enabled the module has no such feature, and saying
    "forbidden" would advertise one it has chosen not to run.
    """
    return HTTPException(status_code=404, detail="Not found")


@router.post("/platform", status_code=201)
@limiter.limit("30/minute")
async def submit_platform_feedback(
    request: Request,
    payload: PlatformFeedbackCreate,
    db: AsyncSession = Depends(get_db),
):
    """Record one anonymous answer. Public (resident-facing), by design.

    Unauthenticated because requiring a login to say the platform is hard to use
    excludes exactly the people whose answer matters. Nothing about the caller is
    read, logged or stored -- not the address, not the user agent, not a session
    id, and not the bearer token if one happens to be attached.

    The 30/minute above is a GLOBAL ceiling, not a per-resident one, for the
    proxy reason described at DAILY_SUBMISSION_CAP. It is set high enough that a
    town cannot rate-limit its own residents out of answering and low enough to
    blunt a burst; the daily cap is what bounds the table.
    """
    if not await _module_enabled(db):
        raise _off()

    since = datetime.now(timezone.utc) - timedelta(days=1)
    recent = (
        await db.execute(
            select(func.count(PlatformFeedback.id)).where(PlatformFeedback.submitted_at >= since)
        )
    ).scalar() or 0
    if recent >= DAILY_SUBMISSION_CAP:
        raise HTTPException(
            status_code=429,
            detail="We have collected as much feedback as we can store today. Please try again tomorrow.",
        )

    db.add(PlatformFeedback(platform_experience=payload.platform_experience))
    await db.commit()
    # No echo of what was stored and no id: there is nothing for a caller to
    # look up later, and returning a row id would be the first step toward one.
    return {"status": "recorded"}


@router.get("/platform/statistics", response_model=PlatformFeedbackStatisticsResponse)
async def get_platform_feedback_statistics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_staff),
):
    """The distribution, for the statistics page. Staff only.

    Counts and shares across the five options plus a monthly trend. There are no
    individual records to expose here even if this were public -- but it is not,
    because a live sentiment reading is the town's own operational information.
    """
    if not await _module_enabled(db):
        raise _off()

    rows = (
        await db.execute(
            select(PlatformFeedback.platform_experience, func.count(PlatformFeedback.id))
            .group_by(PlatformFeedback.platform_experience)
        )
    ).all()
    tallied = {answer: int(count) for answer, count in rows}
    # Every option, zeros included, in scale order.
    counts = {answer: tallied.get(answer, 0) for answer in PLATFORM_FEEDBACK_ANSWERS}
    total = sum(counts.values())

    percentages = {
        answer: (round(count * 100.0 / total, 1) if total else 0.0)
        for answer, count in counts.items()
    }
    easier = percentages["much_easier"] + percentages["somewhat_easier"]
    harder = percentages["somewhat_harder"] + percentages["much_harder"]
    net_easier_percent = round(easier - harder, 1)

    # "YYYY-MM", matching AdvancedStatistics.requests_by_month. Twelve months,
    # which is as far back as the other trends on that page look.
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    monthly = (
        await db.execute(
            select(
                func.to_char(PlatformFeedback.submitted_at, "YYYY-MM"),
                func.count(PlatformFeedback.id),
            )
            .where(PlatformFeedback.submitted_at >= cutoff)
            .group_by(func.to_char(PlatformFeedback.submitted_at, "YYYY-MM"))
        )
    ).all()
    responses_by_month = {period: int(count) for period, count in sorted(monthly)}

    return PlatformFeedbackStatisticsResponse(
        total_responses=total,
        counts=counts,
        percentages=percentages,
        net_easier_percent=net_easier_percent,
        responses_by_month=responses_by_month,
    )
