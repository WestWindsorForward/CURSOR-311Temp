from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, timezone
import uuid
import logging

from app.db.session import get_db
from app.models import ServiceRequest, ServiceDefinition, User, RequestAuditLog, Department
from app.schemas import (
    ServiceRequestCreate, ServiceRequestResponse, ServiceRequestDetailResponse,
    ServiceRequestUpdate, ServiceRequestDelete, ManualIntakeCreate, RequestAuditLogResponse,
    PublicArchiveUpdate
)
from app.core.auth import get_current_staff
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

router = APIRouter()


def generate_request_id() -> str:
    """Generate unique request ID"""
    timestamp = datetime.now().strftime("%Y%m%d")
    unique = uuid.uuid4().hex[:8].upper()
    return f"REQ-{timestamp}-{unique}"


from app.services import road_blocking
from app.services.enqueue import enqueue
from app.services.public_visibility import publicly_listed_conditions
from app.core.config import get_settings
import redis.asyncio as redis
import json

# Redis cache for public requests (60s TTL)
_settings = get_settings()
redis_client = redis.from_url(_settings.redis_url, decode_responses=True)
CACHE_TTL = 60  # seconds


async def resolve_is_public(db: AsyncSession, requested_is_public) -> bool:
    """Decide a request's public-feed visibility, enforcing the admin setting.

    Residents may only opt out when the town has enabled the `unlisted_reports`
    module. This is enforced server-side on purpose: the checkbox can be absent
    from the UI, but a client could still POST is_public=false, and a town that
    hasn't turned the feature on must not end up with reports quietly missing
    from its public feed.

    Defaults to public whenever the choice is absent or the module is off.
    """
    if requested_is_public is not False:
        return True
    try:
        from app.models import SystemSettings
        result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
        settings_row = result.scalar_one_or_none()
        modules = (settings_row.modules if settings_row else None) or {}
        # `private_reports` was the key's original name; read it too so a town
        # that had already enabled the module doesn't silently lose the setting.
        enabled = modules.get("unlisted_reports", modules.get("private_reports", False))
        return not bool(enabled)
    except Exception as e:
        # Fail toward the town's configured default (public) rather than
        # silently hiding a report because a settings read hiccuped.
        logger.warning(f"unlisted_reports module check failed, defaulting to public: {e}")
        return True


async def read_settings_row(db: AsyncSession):
    """The SystemSettings singleton, or None if a read fails.

    Only the public listing needs this, and only to learn the archival policy.
    Failing soft is the right direction here for the same reason it is in
    `resolve_is_public`: a settings hiccup should leave the town's reports
    listed, not blank the public map.
    """
    try:
        from app.models import SystemSettings
        result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
        return result.scalar_one_or_none()
    except Exception as e:
        logger.warning(f"settings read failed, public archival policy not applied: {e}")
        return None


def direct_link_filters():
    """The rule for reaching ONE report by its id — a tracking link.

    Deliberately does not filter on `is_public`. Unlisted means "kept out of the
    town's public listing", not "hidden from the person who filed it": the
    resident has the link, staff have the link, and the whole point of the
    setting is that the report is still worked and still trackable.

    Named and shared for the same reason as the listing rule above, and because
    the two are easy to confuse. Adding `is_public` here would look like
    tightening security and would in fact break every tracking link for every
    unlisted report, silently -- the endpoint would 404 and the resident would
    conclude their report had been deleted.

    Two of the four by-id endpoints were also missing the soft-delete clause, so
    a deleted request still served its comments. Folded in here so there is one
    rule rather than four inline copies of most of it.
    """
    return (ServiceRequest.deleted_at.is_(None),)


@router.get("/public/requests")
async def list_public_requests(
    status: Optional[str] = Query(None, description="Filter by status"),
    service_code: Optional[str] = Query(None, description="Filter by service category"),
    limit: Optional[int] = Query(None, ge=1, description="Max number of results (no limit by default)"),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Public endpoint - List all requests WITHOUT personal information (cached)"""
    # Read before the cache lookup, and put the policy in the key: an admin who
    # changes the number expects the map to change, not to change in up to a
    # minute, and a cached list built under the old policy is exactly the wrong
    # thing to serve back. One indexed singleton row.
    settings_row = await read_settings_row(db)
    archive_days = getattr(settings_row, "public_archive_days", None) or 0

    cache_key = (
        f"public_requests:{status or 'all'}:{service_code or 'all'}:{limit}:{offset}"
        f":arch{archive_days}"
    )

    # Try cache first
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except redis.RedisError:
        logger.debug("Redis unavailable for cache read, proceeding without cache")

    # Three ways to be absent from this list, none of which deletes anything:
    # the resident asked for it to be unlisted, staff archived it from public
    # view, or the town's policy aged it out. All of them remain reachable by
    # direct tracking link (the by-id endpoints below) and fully visible to
    # staff. See app/services/public_visibility.py.
    query = select(ServiceRequest).where(*publicly_listed_conditions(settings_row))

    if status:
        query = query.where(ServiceRequest.status == status)
    if service_code:
        query = query.where(ServiceRequest.service_code == service_code)
    
    query = query.order_by(ServiceRequest.requested_datetime.desc())
    if limit:
        query = query.limit(limit)
    if offset:
        query = query.offset(offset)
    result = await db.execute(query)
    requests = result.scalars().all()
    
    # Build response - EXCLUDE large base64 media data for performance
    # Use has_media flags instead so frontend knows if media exists
    response_data = [
        {
            "service_request_id": r.service_request_id,
            "service_code": r.service_code,
            "service_name": r.service_name,
            "description": r.description[:500] if r.description else None,  # Truncate long descriptions
            "status": r.status,
            "address": r.address,
            "lat": r.lat,
            "long": r.long,
            "requested_datetime": r.requested_datetime.isoformat() if r.requested_datetime else None,
            "updated_datetime": r.updated_datetime.isoformat() if r.updated_datetime else None,
            "closed_substatus": r.closed_substatus,
            "media_urls": [],  # Excluded from list - use photo_count
            "photo_count": len(r.media_urls or []),  # Number of photos attached
            "completion_message": r.completion_message[:200] if r.completion_message else None,
            "completion_photo_url": None,  # Excluded from list - use has_completion_photo flag
            "has_completion_photo": bool(r.completion_photo_url),  # Flag indicating completion photo exists
        }
        for r in requests
    ]
    
    # Cache the response
    try:
        await redis_client.setex(cache_key, CACHE_TTL, json.dumps(response_data))
    except redis.RedisError:
        logger.debug("Redis unavailable for cache write, continuing without caching")
    
    return response_data


@router.get("/public/requests/{request_id}")
async def get_public_request_detail(request_id: str, db: AsyncSession = Depends(get_db)):
    """Get full public request details including media - for detail view"""
    result = await db.execute(
        select(ServiceRequest).options(
            selectinload(ServiceRequest.assigned_department)
        ).where(
            ServiceRequest.service_request_id == request_id,
            *direct_link_filters(),
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Return full details including media and assignment (but still excluding PII)
    return {
        "service_request_id": request.service_request_id,
        "service_code": request.service_code,
        "service_name": request.service_name,
        "description": request.description,  # Full description
        "status": request.status,
        "address": request.address,
        "lat": request.lat,
        "long": request.long,
        "requested_datetime": request.requested_datetime.isoformat() if request.requested_datetime else None,
        "updated_datetime": request.updated_datetime.isoformat() if request.updated_datetime else None,
        "closed_substatus": request.closed_substatus,
        "media_urls": request.media_urls or [],  # Full array of photo data for detail view
        "completion_message": request.completion_message,
        "completion_photo_url": request.completion_photo_url,  # Full completion photo
        "assigned_department_name": request.assigned_department.name if request.assigned_department else None,
    }


from app.models import RequestComment
from app.schemas import RequestCommentResponse


@router.get("/public/requests/{request_id}/comments", response_model=List[RequestCommentResponse])
async def get_public_comments(request_id: str, db: AsyncSession = Depends(get_db)):
    """Get external/public comments for a request - no auth required"""
    # Find the request
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Get only external comments
    comments_result = await db.execute(
        select(RequestComment)
        .where(RequestComment.service_request_id == request.id)
        .where(RequestComment.visibility == 'external')
        .order_by(RequestComment.created_at.asc())
    )
    return comments_result.scalars().all()


@router.post("/public/requests/{request_id}/comments", response_model=RequestCommentResponse)
@limiter.limit("5/minute")
async def add_public_comment(
    request: Request,
    request_id: str,
    content: str = Body(..., min_length=1, max_length=1000, embed=True),
    db: AsyncSession = Depends(get_db)
):
    """Add a public comment to a request - no auth required, always external visibility"""
    # Find the request
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    sr = result.scalar_one_or_none()
    if not sr:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Moderate the public comment. Explicit/abusive content is rejected (400);
    # mild profanity posts but flags the request for staff. Never silently drops.
    from app.services.content_moderation import screen_text
    mod = await screen_text(content)
    if mod.should_block:
        raise HTTPException(
            status_code=400,
            detail="Your comment contains explicit or abusive language and can't be posted. "
                   "Please rephrase without offensive content.",
        )
    if mod.flagged:
        sr.flagged = True
        sr.flag_reason = (mod.reason() + " (public comment)")[:255]

    # Create external comment (anonymous - "Resident")
    comment = RequestComment(
        service_request_id=sr.id,
        username="Resident",
        content=content,
        visibility="external"
    )
    db.add(comment)

    # Put it on the timeline. `comment_added` is a documented action on
    # RequestAuditLog and is rendered in both the resident tracker and the
    # staff dashboard, and until now nothing anywhere wrote one -- so a
    # timeline that showed a report being filed, routed and closed silently
    # omitted every word the resident said about it in between.
    #
    # The text itself is deliberately not copied here. It already lives in
    # request_comments, which is what the retention policy scrubs; duplicating
    # it into the audit trail would put a copy somewhere the scrub does not
    # reach, and the audit chain is append-only by design.
    db.add(RequestAuditLog(
        service_request_id=sr.id,
        action="comment_added",
        new_value="external",
        actor_type="resident",
        actor_name="Resident",
    ))
    await db.commit()
    await db.refresh(comment)

    # Mirror the comment to linked govtech platforms. Enqueued rather than
    # called: the comment is already saved, and a broker that is down must not
    # turn a saved comment into an error the resident reads as "try again".
    from app.tasks.integrations import push_comment_to_integrations
    enqueue(push_comment_to_integrations, comment.id)

    # Tell whoever is working the request that a resident has said something.
    # Without this a reply sits unread until somebody happens to open the
    # request, which from the resident's side is indistinguishable from being
    # ignored. `actor` matches no staff username, so nobody is skipped.
    from app.tasks.service_requests import notify_staff_of_activity
    enqueue(notify_staff_of_activity, sr.id, "comments", actor="Resident")

    return comment


# ============ Audit Log Endpoints ============

@router.get("/requests/{request_id}/audit-log", response_model=List[RequestAuditLogResponse])
async def get_audit_log(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Get audit log for a request (staff only - full history)"""
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    audit_result = await db.execute(
        select(RequestAuditLog)
        .where(RequestAuditLog.service_request_id == request.id)
        .order_by(RequestAuditLog.created_at.asc())
    )
    return audit_result.scalars().all()


@router.get("/requests/{request_id}/audit-log/verify")
async def verify_audit_log(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Verify the tamper-evidence of a request's audit trail.

    Recomputes each entry's chained HMAC-SHA256 from its stored fields +
    stored previous_hash and reports any mismatch — proving to a records
    officer that the log has not been altered since it was written.
    Entries written before the keyed-chain upgrade are accepted under the
    legacy unkeyed hash and counted separately, so an operator can see how
    much of the trail carries the stronger guarantee.
    """
    from app.models import compute_request_audit_hash, compute_request_audit_hash_legacy
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    entries = (await db.execute(
        select(RequestAuditLog)
        .where(RequestAuditLog.service_request_id == request.id)
        .order_by(RequestAuditLog.id.asc())
    )).scalars().all()

    tampered = []
    legacy_scheme = 0
    for e in entries:
        if not e.entry_hash:
            continue  # legacy entry written before hashing existed
        if compute_request_audit_hash(e, e.previous_hash) == e.entry_hash:
            continue  # keyed (HMAC) chain — strongest guarantee
        if compute_request_audit_hash_legacy(e, e.previous_hash) == e.entry_hash:
            legacy_scheme += 1  # pre-HMAC row, still chain-consistent
            continue
        tampered.append(e.id)

    hashed = [e for e in entries if e.entry_hash]
    return {
        "service_request_id": request_id,
        "total_entries": len(entries),
        "verified_entries": len(hashed),
        "legacy_unhashed": len(entries) - len(hashed),
        "legacy_scheme_entries": legacy_scheme,
        "intact": len(tampered) == 0,
        "tampered_entry_ids": tampered,
    }


@router.get("/public/requests/{request_id}/audit-log")
async def get_public_audit_log(request_id: str, db: AsyncSession = Depends(get_db)):
    """Get public audit log for a request - shows status changes only, no internal details.
    Staff usernames are redacted from public responses."""
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id,
            *direct_link_filters(),
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Only return submitted and status_change events (not assignments which may be internal)
    #
    # `comment_added` joins them, but only for comments the resident can
    # already read. An internal staff note is written to the same timeline with
    # new_value="internal", and surfacing even its existence here would tell
    # the public that staff had discussed a report privately -- which is the
    # thing the internal/external split exists to keep separate. The comment
    # text is never in the audit row either way; this is a marker, and the
    # words come from the comments endpoint.
    audit_result = await db.execute(
        select(RequestAuditLog)
        .where(RequestAuditLog.service_request_id == request.id)
        .where(or_(
            RequestAuditLog.action.in_(["submitted", "status_change"]),
            (RequestAuditLog.action == "comment_added")
            & (RequestAuditLog.new_value == "external"),
        ))
        .order_by(RequestAuditLog.created_at.asc())
    )
    entries = audit_result.scalars().all()
    
    # Redact staff usernames from public audit log
    return [
        {
            "id": e.id,
            "action": e.action,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "actor_type": e.actor_type,
            "actor_name": e.actor_name if e.actor_type == "resident" else "Staff",
            "created_at": e.created_at,
            "extra_data": e.extra_data,
        }
        for e in entries
    ]



@router.get("/discovery.json")
async def open311_discovery(request: Request):
    """
    Open311 v2 Discovery endpoint — API metadata and capability advertisement.
    
    Per the Open311 GeoReport v2 specification, this endpoint allows consumers
    to discover what this API supports, what endpoints are available, and
    the changeset/versioning information.
    """
    base_url = str(request.base_url).rstrip("/")
    
    return {
        "changeset": "2026-03-31T00:00:00Z",
        "contact": "You may email support@pinpoint311.org for any questions or issues.",
        "key_service": f"{base_url}/api/auth/login",
        "type": "production",
        "endpoints": [
            {
                "specification": "http://wiki.open311.org/GeoReport_v2",
                "url": f"{base_url}/api/open311/v2",
                "changeset": "2026-03-31T00:00:00Z",
                "type": "production",
                "formats": ["application/json"]
            }
        ],
        "extensions": {
            "public_portal": {
                "description": "Public request tracking with PII redaction",
                "endpoints": [
                    "GET /public/requests",
                    "GET /public/requests/{id}",
                    "GET /public/requests/{id}/comments",
                    "POST /public/requests/{id}/comments",
                    "GET /public/requests/{id}/audit-log"
                ]
            },
            "ai_triage": {
                "description": "Automated Vertex AI priority scoring on submission",
                "endpoints": [
                    "POST /requests/{id}/accept-ai-priority"
                ]
            },
            "asset_linking": {
                "description": "Link requests to infrastructure assets",
                "endpoints": [
                    "GET /requests/asset/{id}/related"
                ]
            }
        }
    }


@router.get("/services/{service_code}.json")
async def get_service_definition(service_code: str, db: AsyncSession = Depends(get_db)):
    """
    Open311 v2 Service Definition endpoint.
    
    Returns extended attributes for a service type, including metadata
    about what fields are required/optional for request submission.
    
    This enables external clients to dynamically build intake forms
    for any service category.
    """
    result = await db.execute(
        select(ServiceDefinition)
        .where(
            ServiceDefinition.service_code == service_code,
            ServiceDefinition.is_active == True
        )
        .options(selectinload(ServiceDefinition.departments))
    )
    service = result.scalar_one_or_none()
    
    if not service:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_code}")
    
    # Build Open311 v2 service_definition response with extended attributes
    return {
        "service_code": service.service_code,
        "service_name": service.service_name,
        "description": service.description,
        "metadata": True,  # Indicates this service has custom attributes
        "type": "realtime",
        "keywords": service.service_name.lower(),
        "group": "municipal",
        "attributes": [
            {
                "variable": True,
                "code": "description",
                "datatype": "text",
                "required": True,
                "datatype_description": "Detailed description of the issue (min 10 chars)",
                "order": 1,
                "description": "Please describe the issue"
            },
            {
                "variable": True,
                "code": "address",
                "datatype": "string",
                "required": False,
                "datatype_description": "Street address of the issue",
                "order": 2,
                "description": "Location address"
            },
            {
                "variable": True,
                "code": "lat",
                "datatype": "number",
                "required": False,
                "datatype_description": "Latitude coordinate",
                "order": 3,
                "description": "GPS latitude"
            },
            {
                "variable": True,
                "code": "long",
                "datatype": "number",
                "required": False,
                "datatype_description": "Longitude coordinate",
                "order": 4,
                "description": "GPS longitude"
            },
            {
                "variable": True,
                "code": "email",
                "datatype": "string",
                "required": True,
                "datatype_description": "Submitter's email address",
                "order": 5,
                "description": "Contact email"
            },
            {
                "variable": True,
                "code": "first_name",
                "datatype": "string",
                "required": False,
                "order": 6,
                "description": "First name"
            },
            {
                "variable": True,
                "code": "last_name",
                "datatype": "string",
                "required": False,
                "order": 7,
                "description": "Last name"
            },
            {
                "variable": True,
                "code": "phone",
                "datatype": "string",
                "required": False,
                "order": 8,
                "description": "Contact phone number"
            },
            {
                "variable": True,
                "code": "media_urls",
                "datatype": "text",
                "required": False,
                "datatype_description": "Up to 3 photo URLs or base64-encoded images",
                "order": 9,
                "description": "Photos of the issue"
            }
        ],
        "routing_mode": service.routing_mode or "township",
        "translations_available": list((service.translations or {}).keys()),
        "departments": [d.name for d in (service.departments or [])]
    }


@router.get("/tokens/{service_request_id}.json")
async def lookup_request_by_token(service_request_id: str, db: AsyncSession = Depends(get_db)):
    """
    Open311 v2 Token Lookup endpoint.
    
    Allows anonymous tracking of a service request using only the
    service_request_id (the token given at submission). No authentication
    required — returns only non-PII data.
    
    This enables residents to check the status of their request without
    needing an account.
    """
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == service_request_id,
            *direct_link_filters(),
        )
    )
    request_obj = result.scalar_one_or_none()
    
    if not request_obj:
        raise HTTPException(status_code=404, detail="Service request not found")
    
    return {
        "service_request_id": request_obj.service_request_id,
        "status": request_obj.status,
        "status_notes": request_obj.completion_message if request_obj.status == "closed" else None,
        "service_code": request_obj.service_code,
        "service_name": request_obj.service_name,
        "description": request_obj.description,
        "address": request_obj.address,
        "lat": request_obj.lat,
        "long": request_obj.long,
        "requested_datetime": request_obj.requested_datetime.isoformat() if request_obj.requested_datetime else None,
        "updated_datetime": request_obj.updated_datetime.isoformat() if request_obj.updated_datetime else None,
        "closed_substatus": request_obj.closed_substatus,
        "media_urls": request_obj.media_urls or [],
        "completion_photo_url": request_obj.completion_photo_url,
        "token": service_request_id
    }


@router.get("/services.json")
async def list_open311_services(db: AsyncSession = Depends(get_db)):
    """Open311 v2 compatible - List services"""
    result = await db.execute(
        select(ServiceDefinition).where(ServiceDefinition.is_active == True)
    )
    services = result.scalars().all()
    return [
        {
            "service_code": s.service_code,
            "service_name": s.service_name,
            "description": s.description,
            "type": "realtime",
            "keywords": s.service_name.lower(),
            "group": "municipal"
        }
        for s in services
    ]


@router.post("/requests.json", response_model=ServiceRequestResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_request(
    request: Request,
    request_data: ServiceRequestCreate,
    db: AsyncSession = Depends(get_db)
):
    """Open311 v2 compatible - Create a new service request (public)"""
    from app.core.sanitize import sanitize_for_log
    logger.info(f"[CREATE REQUEST] Received: service_code={sanitize_for_log(request_data.service_code)}")
    
    # Validate service code
    result = await db.execute(
        select(ServiceDefinition).where(
            ServiceDefinition.service_code == request_data.service_code,
            ServiceDefinition.is_active == True
        )
    )
    service = result.scalar_one_or_none()
    
    if not service:
        logger.error(f"[CREATE REQUEST] Invalid service code: {sanitize_for_log(request_data.service_code)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid service code: {request_data.service_code}"
        )

    # Content moderation: reject explicit/abusive text or images before the
    # report is created. Text uses better-profanity plus the cloud text moderator
    # when configured; images use the cloud image moderator when configured.
    # Mild profanity is allowed through (flagged later for staff) so legitimate
    # angry reports still go through.
    from app.services.content_moderation import screen_text
    if (await screen_text(request_data.description or "")).should_block:
        logger.info("[CREATE REQUEST] blocked: explicit/abusive description")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your report contains explicit or abusive language and can't be submitted. "
                   "Please describe the issue without offensive content and try again.",
        )

    # Screening and redaction are one step because they are one question asked
    # of the same bytes: Vision answers SafeSearch, faces and text as features of
    # a single annotate call, so folding them halves the round trips a resident
    # waits through. AWS and Azure expose these as separate APIs and fall back to
    # running the two passes in sequence, with identical behaviour either way.
    #
    # The blur is destructive and happens before anything is written -- see
    # image_redaction -- so no unredacted copy exists to leak through the
    # Open311 API, the research export or a public-records response. The EXIF block goes
    # too, which is the larger privacy win and costs nothing.
    from app.services import image_redaction
    verdict, redacted = await image_redaction.screen_and_redact(request_data.media_urls)
    if verdict.should_block:
        logger.info("[CREATE REQUEST] blocked: explicit image")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One of your photos appears to contain explicit or graphic content and "
                   "can't be submitted. Please remove it and try again.",
        )
    request_data.media_urls = redacted.media
    if redacted.changed:
        logger.info("[CREATE REQUEST] redacted %d face(s), %d plate(s)",
                    redacted.faces, redacted.plates)

    # Jurisdiction check. This used to live only in the resident portal's
    # JavaScript, so a report POSTed straight at this endpoint ignored road
    # rules entirely. Evaluating here means every intake path agrees, and the
    # redirect is recorded so the town can count how often it happens.
    decision = await road_blocking.evaluate(db, service, request_data.lat, request_data.long)
    if decision.blocked:
        await road_blocking.record(db, decision, service, request_data.lat, request_data.long)
        logger.info(
            "[CREATE REQUEST] redirected to %s (%s)", decision.jurisdiction, decision.block_type
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "redirected",
                "jurisdiction": decision.jurisdiction,
                "message": decision.message,
                "message_is_default": decision.message_is_default,
                "contacts": decision.contacts,
                "road": decision.road_name,
            },
        )

    # Auto-assignment based on service routing config
    assigned_department_id, assigned_to = await _resolve_assignment(db, service)

    # Create request with auto-assignment
    service_request = ServiceRequest(
        service_request_id=generate_request_id(),
        service_code=request_data.service_code,
        service_name=service.service_name,
        description=request_data.description,
        address=request_data.address,
        lat=request_data.lat,
        long=request_data.long,
        first_name=request_data.first_name,
        last_name=request_data.last_name,
        email=request_data.email,
        phone=request_data.phone,
        preferred_language=request_data.preferred_language or "en",  # Capture user's UI language
        media_urls=request_data.media_urls[:3] if request_data.media_urls else [],  # Limit to 3 photos
        matched_asset=request_data.matched_asset,
        is_public=await resolve_is_public(db, request_data.is_public),
        custom_fields=request_data.custom_fields,
        source="resident_portal",
        assigned_department_id=assigned_department_id,
        assigned_to=assigned_to
    )

    db.add(service_request)
    await db.commit()
    await db.refresh(service_request)

    # Run the shared post-creation pipeline (audit, AI triage, govtech push,
    # resident confirmation, department notification).
    await _finalize_new_request(
        db, service_request, assigned_department_id,
        actor_type="resident", actor_name="Resident", notify_resident=True,
    )

    return service_request


async def _resolve_assignment(db: AsyncSession, service: ServiceDefinition):
    """Resolve (assigned_department_id, assigned_to) from a service's routing
    config. Shared by resident and manual intake so both route identically."""
    assigned_department_id = service.assigned_department_id
    assigned_to = None
    if service.routing_config:
        config = service.routing_config
        if config.get('route_to') == 'specific_staff' and config.get('staff_ids'):
            from app.models import User
            staff_ids = config.get('staff_ids', [])
            if staff_ids:
                staff_result = await db.execute(
                    select(User).where(User.id == staff_ids[0])
                )
                staff = staff_result.scalar_one_or_none()
                if staff:
                    assigned_to = staff.username
    return assigned_department_id, assigned_to


async def _finalize_new_request(
    db: AsyncSession,
    service_request: ServiceRequest,
    assigned_department_id,
    *,
    actor_type: str,
    actor_name: str,
    notify_resident: bool,
):
    """Post-creation pipeline shared by resident and manual intake: audit log,
    AI triage, govtech push, resident confirmation, and department notification.

    `notify_resident` is False when no real resident email exists (e.g. a phone
    caller who didn't leave one) so we never email a placeholder address.
    """
    # Always-on text moderation of the public description (works with or without
    # AI). Never blocks intake — flags for staff review; the AI photo/text
    # assessment folds in later (analyze_request) when a provider is configured.
    try:
        from app.services.content_moderation import scan_text
        mod = scan_text(service_request.description or "")
        if mod.flagged:
            service_request.flagged = True
            service_request.flag_reason = mod.reason()[:255]
    except Exception:
        logger.warning("[Moderation] description scan failed", exc_info=True)

    audit_entry = RequestAuditLog(
        service_request_id=service_request.id,
        action="submitted",
        new_value="open",
        actor_type=actor_type,
        actor_name=actor_name,
    )
    db.add(audit_entry)
    await db.commit()

    # Everything from here is follow-up work on a report that is already saved.
    # It is enqueued rather than called, so an unreachable broker costs a
    # notification and a line in the log instead of answering "we could not
    # take your report" for a report that is in the database.
    from app.tasks.service_requests import analyze_request, send_branded_notification, send_department_notification
    # AI triage / priority — same as a resident submission
    enqueue(analyze_request, service_request.id)

    # Push to any connected govtech platforms (Accela, Tyler, CivicPlus, etc.)
    from app.tasks.integrations import push_request_to_integrations
    enqueue(push_request_to_integrations, service_request.id)

    # Send branded confirmation only when we have a real resident email
    if notify_resident:
        enqueue(send_branded_notification, service_request.id, "confirmation")

    # Notify staff about the new request — always enqueued, never gated on the
    # department having a routing_email. The worker resolves who to tell from
    # the request itself (assignee → department staff → admins) and applies each
    # person's Notification Settings; routing_email is only the no-recipients
    # archive fallback. The old gate (`if dept.routing_email`) meant a town that
    # never filled that field in — most of them — notified nobody, and the
    # preference toggles were never even consulted.
    routing_email = None
    if assigned_department_id:
        dept_result = await db.execute(
            select(Department).where(Department.id == assigned_department_id)
        )
        dept = dept_result.scalar_one_or_none()
        routing_email = (dept.routing_email or None) if dept else None
    enqueue(send_department_notification, service_request.id, routing_email)


@router.get("/requests.json", response_model=List[ServiceRequestResponse])
async def list_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    service_code: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Open311 v2 compatible - List service requests (staff only)"""
    query = select(ServiceRequest).order_by(ServiceRequest.requested_datetime.desc())
    
    # Filter out deleted unless admin requests them
    if not include_deleted or current_user.role != "admin":
        query = query.where(ServiceRequest.deleted_at.is_(None))

    # Department scoping: a non-admin staffer sees only requests routed to a
    # department they belong to, or assigned to them by name — plus unrouted
    # requests (no department yet), which still need someone to triage. Admins
    # see everything.
    if current_user.role != "admin":
        from app.models import user_departments
        dept_rows = await db.execute(
            select(user_departments.c.department_id)
            .where(user_departments.c.user_id == current_user.id)
        )
        my_dept_ids = [row[0] for row in dept_rows.all()]
        scope = [
            ServiceRequest.assigned_to == current_user.username,
            ServiceRequest.assigned_department_id.is_(None),
        ]
        if my_dept_ids:
            scope.append(ServiceRequest.assigned_department_id.in_(my_dept_ids))
        query = query.where(or_(*scope))

    if status_filter:
        query = query.where(ServiceRequest.status == status_filter)
    
    if service_code:
        query = query.where(ServiceRequest.service_code == service_code)
    
    if start_date:
        query = query.where(ServiceRequest.requested_datetime >= start_date)
    
    if end_date:
        query = query.where(ServiceRequest.requested_datetime <= end_date)
    
    result = await db.execute(query.limit(100))
    return result.scalars().all()


@router.get("/requests/{request_id}.json", response_model=ServiceRequestDetailResponse)
async def get_request(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_staff)
):
    """Get service request details (staff only)"""
    result = await db.execute(
        select(ServiceRequest)
        .options(selectinload(ServiceRequest.assigned_department))
        .where(ServiceRequest.service_request_id == request_id)
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    # Which external systems this record actually exists in. Drives whether the
    # "Refresh work order" button is offered at all: with no link there is
    # nothing to pull, and the refresh endpoint's own answer is "This request
    # isn't linked to any external platform."
    #
    # Set on the ORM instance rather than built into a dict, so the response
    # model keeps reading every other field straight off the row.
    request.external_links = await _linked_platforms(db, request.id)
    return request


async def _linked_platforms(db: AsyncSession, service_request_id: int) -> List[str]:
    """Display names of the platforms this request is linked to. Never raises.

    A failure here must not take down the request detail view -- the links are
    a nicety and the report is the point.
    """
    try:
        from app.models import IntegrationConfig, IntegrationLink

        result = await db.execute(
            select(IntegrationConfig.platform, IntegrationConfig.display_name)
            .join(IntegrationLink, IntegrationLink.integration_id == IntegrationConfig.id)
            .where(IntegrationLink.service_request_id == service_request_id)
        )
        return sorted({(row[1] or row[0]) for row in result.all()})
    except Exception as exc:
        logger.warning("Could not read integration links: %s", exc)
        return []


@router.put("/requests/{request_id}/status", response_model=ServiceRequestDetailResponse)
async def update_request_status(
    request_id: str,
    update_data: ServiceRequestUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Update service request status (staff only)"""
    result = await db.execute(
        select(ServiceRequest).options(selectinload(ServiceRequest.assigned_department)).where(ServiceRequest.service_request_id == request_id)
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    update_dict = update_data.model_dump(exclude_unset=True)
    
    # Restrict flagged (legal hold) to admin only
    if "flagged" in update_dict and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can toggle legal hold status")
    
    # Track old values for audit log
    old_status = request.status
    old_department_id = request.assigned_department_id
    old_assigned_to = request.assigned_to
    old_department_name = request.assigned_department.name if request.assigned_department else None
    old_flagged = request.flagged
    
    for field, value in update_dict.items():
        if value is not None:
            if field == "status":
                value = value.value
                if value == "closed" and request.status != "closed":
                    request.closed_datetime = datetime.now(timezone.utc)
            elif field == "closed_substatus":
                value = value.value  # Convert enum to string
            # Special handling for boolean flagged field
            if field == "flagged":
                logger.debug(f"[LEGAL HOLD] Setting flagged from {request.flagged} to {value} for request {request.service_request_id}")
            setattr(request, field, value)
    
    request.updated_datetime = datetime.now(timezone.utc)
    
    # Force flush to ensure changes are written
    await db.flush()
    await db.commit()
    
    # Create audit log entries for changes
    # Status change
    if "status" in update_dict and update_dict["status"] and update_dict["status"].value != old_status:
        new_status = update_dict["status"].value
        extra_data = None
        if new_status == "closed" and "closed_substatus" in update_dict:
            extra_data = {
                "substatus": update_dict["closed_substatus"].value if update_dict["closed_substatus"] else None,
                "completion_message": update_dict.get("completion_message")
            }
        audit_entry = RequestAuditLog(
            service_request_id=request.id,
            action="status_change",
            old_value=old_status,
            new_value=new_status,
            actor_type="staff",
            actor_name=current_user.username,
            extra_data=extra_data
        )
        db.add(audit_entry)
    
    # Department assignment change
    if "assigned_department_id" in update_dict and update_dict["assigned_department_id"] != old_department_id:
        new_dept_id = update_dict["assigned_department_id"]
        new_dept_name = None
        if new_dept_id:
            dept_result = await db.execute(select(Department).where(Department.id == new_dept_id))
            new_dept = dept_result.scalar_one_or_none()
            new_dept_name = new_dept.name if new_dept else str(new_dept_id)
        audit_entry = RequestAuditLog(
            service_request_id=request.id,
            action="department_assigned",
            old_value=old_department_name,
            new_value=new_dept_name,
            actor_type="staff",
            actor_name=current_user.username
        )
        db.add(audit_entry)
    
    # Staff assignment change - only log if new value is non-empty
    if "assigned_to" in update_dict and update_dict["assigned_to"] != old_assigned_to:
        # Only log if new staff is actually assigned (not cleared)
        if update_dict["assigned_to"]:
            audit_entry = RequestAuditLog(
                service_request_id=request.id,
                action="staff_assigned",
                old_value=old_assigned_to,
                new_value=update_dict["assigned_to"],
                actor_type="staff",
                actor_name=current_user.username
            )
            db.add(audit_entry)
    
    # Legal hold change
    if "flagged" in update_dict and update_dict["flagged"] != old_flagged:
        audit_entry = RequestAuditLog(
            service_request_id=request.id,
            action="legal_hold",
            old_value="enabled" if old_flagged else "disabled",
            new_value="enabled" if update_dict["flagged"] else "disabled",
            actor_type="admin",
            actor_name=current_user.username
        )
        db.add(audit_entry)
    
    await db.commit()
    
    # Send notification if status changed
    if "status" in update_dict and update_dict["status"] and update_dict["status"].value != old_status:
        # Enqueued, not called: the status change and its audit entry are
        # committed above. A broker outage must not roll a completed update
        # back into an error, because the staffer's next move is to set the
        # status again and the resident gets two "your report is closed"
        # emails when the queue recovers.
        from app.tasks.service_requests import send_branded_notification, notify_staff_of_activity
        enqueue(
            send_branded_notification,
            request.id,
            "status_update",
            old_status=old_status,
            completion_message=update_dict.get("completion_message")
        )

        # Notify assigned staff / department (respects each user's preferences).
        enqueue(notify_staff_of_activity, request.id, "status_changes", actor=current_user.username)

        # Mirror the status change to linked govtech platforms
        from app.tasks.integrations import push_status_to_integrations
        enqueue(push_status_to_integrations, request.id, notes=update_dict.get("completion_message"))
    
    # Reload with relationship for response
    await db.refresh(request)
    # Reload the relationship if department was set
    if request.assigned_department_id:
        await db.refresh(request, ['assigned_department'])
    return request


@router.patch("/requests/{request_id}/public-archive", response_model=ServiceRequestDetailResponse)
async def set_public_archived(
    request_id: str,
    payload: PublicArchiveUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Take one report off the public tracker and map, or put it back (staff).

    Nothing is deleted, redacted or hidden from staff, and the resident's
    tracking link keeps working — this only decides whether the report appears
    in public listings. Staff role, not admin: deciding that a report closed
    last spring no longer needs to be on the map is routine clerk work, not the
    legal-hold sort of decision.

    Never touches `is_public`. That is the resident's answer to a different
    question, and a report they asked to keep unlisted stays unlisted whatever
    is done here.
    """
    result = await db.execute(
        select(ServiceRequest).options(selectinload(ServiceRequest.assigned_department)).where(
            ServiceRequest.service_request_id == request_id,
            *direct_link_filters(),
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    was_archived = bool(request.public_archived)
    if was_archived == payload.public_archived:
        return request  # no change, no audit noise

    request.public_archived = payload.public_archived
    request.updated_datetime = datetime.now(timezone.utc)

    # Who did what, on the same tamper-evident chain as every other change to
    # this report, so "why did this drop off the map" has an answer.
    db.add(RequestAuditLog(
        service_request_id=request.id,
        action="public_archive",
        old_value="archived" if was_archived else "listed",
        new_value="archived" if payload.public_archived else "listed",
        actor_type="admin" if current_user.role == "admin" else "staff",
        actor_name=current_user.username,
    ))

    await db.commit()
    await db.refresh(request)
    if request.assigned_department_id:
        await db.refresh(request, ['assigned_department'])
    return request


@router.post("/requests/manual", response_model=ServiceRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_manual_intake(
    intake_data: ManualIntakeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Create a request from manual intake (phone, walk-in, or email) — staff only.

    Runs the exact same pipeline as a resident submission (assignment, AI triage,
    govtech push, notifications). Resident contact fields are optional; the
    caller is emailed a confirmation only if they actually provided an address.
    """
    # Validate service code
    result = await db.execute(
        select(ServiceDefinition).where(
            ServiceDefinition.service_code == intake_data.service_code,
            ServiceDefinition.is_active == True
        )
    )
    service = result.scalar_one_or_none()
    if not service:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid service code"
        )

    # Same redaction as a resident submission. A photo emailed in or attached
    # by a clerk from a phone call is no less likely to have a bystander in it,
    # and it goes to exactly the same public places.
    #
    # Redaction only, not the moderation screen: this path never image-moderated
    # and adding it here would start rejecting a clerk's own submissions on a
    # SafeSearch score, which is the wrong party to second-guess.
    from app.services import image_redaction
    intake_redacted = await image_redaction.redact_media(intake_data.media_urls)
    intake_data.media_urls = intake_redacted.media

    # Same jurisdiction rules as a resident submission, but a staffer may
    # knowingly override them. A clerk on the phone can see that the caller is
    # describing a county road and still decide to log it -- because the caller
    # is elderly, because the town has agreed to forward these, because it is
    # faster than explaining. Refusing them would just push the work into a
    # notepad. Residents get no override; staff exercising judgement is the
    # whole reason the role exists.
    block = await road_blocking.evaluate(db, service, intake_data.lat, intake_data.long)
    if block.blocked and not intake_data.override_jurisdiction:
        await road_blocking.record(db, block, service, intake_data.lat, intake_data.long)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "redirected",
                "jurisdiction": block.jurisdiction,
                "message": block.message,
                "contacts": block.contacts,
                "road": block.road_name,
                "can_override": True,
            },
        )

    # Same auto-assignment/routing as a resident submission
    assigned_department_id, assigned_to = await _resolve_assignment(db, service)

    # A caller may not leave an email; keep a unique placeholder for the row but
    # remember whether a real one was given so we only email real addresses.
    has_email = bool(intake_data.email)

    service_request = ServiceRequest(
        service_request_id=generate_request_id(),
        service_code=intake_data.service_code,
        service_name=service.service_name,
        description=intake_data.description,
        address=intake_data.address,
        lat=intake_data.lat,
        long=intake_data.long,
        first_name=intake_data.first_name,
        last_name=intake_data.last_name,
        email=intake_data.email or f"manual-{uuid.uuid4().hex[:8]}@intake.local",
        phone=intake_data.phone,
        preferred_language=intake_data.preferred_language or "en",
        media_urls=intake_data.media_urls[:3] if intake_data.media_urls else [],
        matched_asset=intake_data.matched_asset,
        is_public=await resolve_is_public(db, intake_data.is_public),
        custom_fields=intake_data.custom_fields,
        source=intake_data.source.value,
        assigned_department_id=assigned_department_id,
        assigned_to=assigned_to,
    )

    db.add(service_request)
    await db.commit()
    await db.refresh(service_request)

    # Attribute the submission to the staff member who took it down. The row is
    # already persisted, so a hiccup in the best-effort background pipeline
    # (AI triage, govtech push, notifications — all dispatched via the task
    # broker) must NOT fail the intake in the call taker's face. Log and return
    # the created request either way.
    try:
        await _finalize_new_request(
            db, service_request, assigned_department_id,
            actor_type="staff", actor_name=current_user.username,
            notify_resident=has_email,
        )
    except Exception:
        logger.exception(
            "Manual intake %s saved but post-creation pipeline dispatch failed",
            service_request.service_request_id,
        )

    return service_request


@router.delete("/requests/{request_id}")
async def delete_request(
    request_id: str,
    delete_data: ServiceRequestDelete,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Soft delete a service request with justification (staff/admin)"""
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    if request.deleted_at:
        raise HTTPException(status_code=400, detail="Request already deleted")
    
    # Soft delete
    request.deleted_at = datetime.now(timezone.utc)
    request.deleted_by = current_user.username
    request.delete_justification = delete_data.justification
    request.updated_datetime = datetime.now(timezone.utc)
    
    # Add audit log entry
    audit_entry = RequestAuditLog(
        service_request_id=request.id,
        action="deleted",
        actor_type="staff",
        actor_name=current_user.username,
        new_value=delete_data.justification
    )
    db.add(audit_entry)
    
    await db.commit()
    await db.refresh(request)
    
    return {"message": "Request deleted", "request_id": request_id}


@router.post("/requests/{request_id}/restore")
async def restore_request(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Restore a soft-deleted service request (staff/admin)"""
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    if not request.deleted_at:
        raise HTTPException(status_code=400, detail="Request is not deleted")
    
    # Restore the request
    request.deleted_at = None
    request.deleted_by = None
    request.delete_justification = None
    request.updated_datetime = datetime.now(timezone.utc)
    
    # Add audit log entry
    audit_entry = RequestAuditLog(
        service_request_id=request.id,
        action="restored",
        actor_type="staff",
        actor_name=current_user.username,
        new_value=f"Restored by {current_user.username}"
    )
    db.add(audit_entry)
    
    await db.commit()
    await db.refresh(request)
    
    return {"message": "Request restored", "request_id": request_id}


@router.post("/requests/{request_id}/accept-ai-priority")
async def accept_ai_priority(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Accept AI-suggested priority score (copies to manual_priority_score)"""
    result = await db.execute(
        select(ServiceRequest).where(
            ServiceRequest.service_request_id == request_id, *direct_link_filters())
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Get AI priority from stored analysis
    ai_priority = None
    if request.ai_analysis and isinstance(request.ai_analysis, dict):
        ai_priority = request.ai_analysis.get("priority_score")
    
    if ai_priority is None:
        raise HTTPException(status_code=400, detail="No AI priority score available")
    
    # Set the manual priority score to the AI suggestion
    request.manual_priority_score = float(ai_priority)
    request.updated_datetime = datetime.now(timezone.utc)
    
    # Add audit log entry
    audit_entry = RequestAuditLog(
        service_request_id=request.id,
        action="priority_accepted",
        actor_type="staff",
        actor_name=current_user.username,
        old_value=str(request.manual_priority_score) if request.manual_priority_score else None,
        new_value=str(ai_priority)
    )
    db.add(audit_entry)
    
    await db.commit()
    await db.refresh(request)
    
    return {"message": "AI priority accepted", "priority_score": ai_priority}


@router.get("/requests/asset/{asset_id}/related")
async def get_asset_related_requests(
    asset_id: str,
    exclude_request_id: Optional[str] = Query(None, description="Request ID to exclude from results"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_staff)
):
    """Get all requests that matched to the same asset (staff only)"""
    from sqlalchemy import text
    
    # Query using PostgreSQL JSON extraction - matched_asset->>'asset_id'
    query = select(ServiceRequest).where(
        ServiceRequest.matched_asset.isnot(None),
        ServiceRequest.deleted_at.is_(None),
        text("matched_asset->>'asset_id' = :asset_id")
    ).params(asset_id=asset_id).order_by(ServiceRequest.requested_datetime.desc())
    
    result = await db.execute(query)
    requests = result.scalars().all()
    
    # Filter out the excluded request and build response
    return [
        {
            "service_request_id": r.service_request_id,
            "service_name": r.service_name,
            "status": r.status,
            "requested_datetime": r.requested_datetime.isoformat() if r.requested_datetime else None,
            "address": r.address,
            "description": r.description[:100] if r.description else None,
        }
        for r in requests
        if r.service_request_id != exclude_request_id
    ]

