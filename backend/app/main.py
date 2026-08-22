from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.body_limit import BodySizeLimitMiddleware, MAX_REQUEST_BODY_BYTES  # noqa: F401
from contextlib import asynccontextmanager
import os
import logging
import sentry_sdk

logger = logging.getLogger(__name__)

# Initialize Sentry for error tracking (optional - set SENTRY_DSN env var)
SENTRY_DSN = os.environ.get("SENTRY_DSN")


def _crash_reporting_wanted(event, hint):
    """Drop the event when the town has switched crash reporting off.

    "Crash reporting" is one of the ticks on the setup page, and unticking it
    used to do nothing at all: the DSN is an environment variable read once at
    import, so the box was cosmetic and reports kept leaving the building. This
    is the only choke point -- there is no per-call site to gate.

    Deliberately reading a cached snapshot rather than the database. This runs on
    the error path, and the error being reported may be that the database is
    unreachable; opening a session here would fail exactly when it matters, and
    `before_send` has no event loop to await one with. A process that has not yet
    read the switch sends the event, because swallowing the crash that stopped it
    from reading is the worse failure.
    """
    try:
        from app.services.capability_switches import wanted_sync

        if not wanted_sync("errors"):
            return None
    except Exception:
        # Never let this decide to lose a crash report.
        pass
    return event


if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,  # 10% of requests for performance monitoring
        profiles_sample_rate=0.1,
        environment=os.environ.get("ENVIRONMENT", "production"),
        send_default_pii=False,  # Don't send personally identifiable info
        before_send=_crash_reporting_wanted,
    )

from app.api import auth, users, departments, services, system, open311, gis, map_layers, comments, research, health, audit, setup, api_usage, data_export, integrations, provisioning, telemetry, roads, feedback
from app.db.init_db import seed_database

# Rate limiting setup
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Default per-IP rate limit. This used to be keyed off DEMO_MODE ("100/minute"
# when set, "500/minute" otherwise) purely so a publicly-reachable instance
# sharing one set of API keys could be throttled harder. That reason survives
# demo mode, so the limit is now a plain env var: any instance that wants a
# tighter ceiling sets RATE_LIMIT_DEFAULT instead of opting into a feature flag.
_default_limit = os.environ.get("RATE_LIMIT_DEFAULT") or "500/minute"
limiter = Limiter(key_func=get_remote_address, default_limits=[_default_limit])


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses for government compliance."""
    
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        
        # Skip security headers for developer docs pages (they need CDN resources)
        request_path = request.url.path
        if request_path in ["/api/docs", "/api/redoc"]:
            return response
        
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Enable XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Don't advertise the server software/version (information disclosure).
        response.headers["Server"] = "Pinpoint"
        if "X-Powered-By" in response.headers:
            del response.headers["X-Powered-By"]

        # Content Security Policy, decided by response TYPE (not URL path): JSON
        # responses load nothing, so lock them all the way down — a reflected
        # parameter is then completely inert (defense against reflected-XSS
        # probes). Server-rendered HTML pages (e.g. the /api/auth bootstrap
        # pages) legitimately use inline scripts/styles, so they get a policy
        # that keeps working while still denying plugins, framing, and base-tag
        # hijacking. Keying off path would have broken those HTML pages.
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self' https://maps.googleapis.com https://places.googleapis.com https://*.googleapis.com https://*.gstatic.com https://translate.googleapis.com https://api.open-meteo.com https://archive-api.open-meteo.com https://*.auth0.com https://fonts.googleapis.com wss:; "
                "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
            )

        # Force HTTPS for a year on this host and its subdomains (HSTS). Sent on
        # every response; browsers only honor it over TLS, so it's harmless on
        # plain HTTP. Both demo hosts serve over HTTPS behind the proxy.
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Restrict powerful browser features to only what the app uses
        # (geolocation for the map picker); deny the rest.
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(), payment=(), usb=()"

        # Prevent caching of sensitive data
        if "/api/" in request_path:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        
        return response


class AdminActionAuditMiddleware(BaseHTTPMiddleware):
    """Record every authenticated change, so nothing has to remember to.

    The admin audit log held sign-ins and almost nothing else. Not because it
    filtered anything out -- because fifty-two authenticated mutating endpoints
    never called it. Creating a user, deleting a department, saving a
    credential, replacing the town boundary: none of them left a record.

    Adding a call to each is the obvious fix and it is the fix that decays. The
    fifty-third endpoint will not have one either, and nothing will say so.

    So this is the backstop: any authenticated request that changes something
    and succeeds is recorded, with who, what, and when. Handlers that have
    something more specific to say -- "role changed to admin", "department
    deleted, name Public Works" -- say it and suppress this, so the trail has
    one entry per action rather than two.

    Three things it deliberately does not do:

      * never fail a request. An audit write that 500s a user's edit is worse
        than a missing line, and the edit has already been committed by the
        time this runs.
      * never record a read. GET is the overwhelming majority of traffic and a
        log nobody can scroll is a log nobody reads.
      * never record a failed or unauthenticated attempt here. Those are real
        and worth having, but they belong with the auth events that already
        capture them, and a 401 flood would bury the successful changes.

    Two things it does with care, because the first version of this got both
    wrong and buried the log it was meant to fill: it names the action in a
    sentence rather than filing everything under "Admin Change", and it does
    not record the many POSTs that change nothing -- testing a connection,
    re-running a check, generating a preview. Both decisions live in
    `audit_labels`, where they can be argued with in a test.
    """

    async def dispatch(self, request: Request, call_next):
        from app.services.admin_audit import begin_request, was_recorded

        token = begin_request()
        try:
            response = await call_next(request)
        finally:
            pass

        try:
            if response.status_code >= 400:
                return response

            from app.services.audit_labels import describe_action

            path = request.url.path
            action = describe_action(request.method, path)
            if action is None:
                return response
            if was_recorded():
                # The handler said something more specific.
                return response

            actor = _actor_from_request(request)
            if not actor:
                return response

            from app.db.session import SessionLocal
            from app.services.audit_service import AuditService

            async with SessionLocal() as db:
                await AuditService.log_event(
                    db,
                    event_type="admin_change",
                    success=True,
                    username=actor,
                    # Who did it from where. It was left out, so every one of
                    # these rows showed "-" in the IP column while the sign-in
                    # rows above them showed an address.
                    ip_address=_client_ip(request),
                    user_agent=(request.headers.get("User-Agent") or "")[:500] or None,
                    details={
                        "action": action,
                        "method": request.method.upper(),
                        # The path only. Query strings and bodies carry the
                        # values being set, and this table is exported.
                        "path": path,
                        "status": response.status_code,
                    },
                )
        except Exception as exc:  # noqa: BLE001 -- see the docstring
            logging.getLogger(__name__).warning(
                "[Audit] could not record %s %s: %s",
                request.method, request.url.path, exc,
            )
        finally:
            try:
                from app.services.admin_audit import _recorded
                _recorded.reset(token)
            except Exception:
                pass

        return response


def _actor_from_request(request: Request) -> "str | None":
    """The username on the bearer token, or None.

    Decoded rather than taken from `request.state`: BaseHTTPMiddleware hands
    the downstream app its own Request, so anything a dependency stashed is not
    reliably visible here. Signature and expiry are checked, so an actor name
    cannot be forged by sending an arbitrary token.
    """
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    try:
        from app.core.auth import decode_token

        payload = decode_token(header.split(" ", 1)[1].strip())
        return payload.get("sub")
    except Exception:
        return None


def _client_ip(request: Request) -> "str | None":
    """The address the change came from, through the reverse proxy.

    Caddy sits in front of everything, so `request.client.host` is Caddy's
    address on the compose network -- 172.19.0.x, the same for every user.
    The first entry in X-Forwarded-For is the caller.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else None





class ManagedModeMiddleware(BaseHTTPMiddleware):
    """Managed-hosting hooks that run on every request (ORCHESTRATOR_PLAN.md).

    - Counts responses by status class for the PII-free telemetry endpoint (A5).
    - Honors the panel-set suspended state (A7): everything except health and
      the provisioning surface answers 503 until the state resumes the town.
    """

    SUSPEND_EXEMPT_PREFIXES = ("/api/health", "/api/provisioning")

    async def dispatch(self, request: Request, call_next):
        from app.core.managed import get_lifecycle_state

        path = request.url.path
        if (
            get_lifecycle_state() == "suspended"
            and path.startswith("/api")
            and not path.startswith(self.SUSPEND_EXEMPT_PREFIXES)
        ):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={"detail": "This instance has been suspended by your state. Contact your state program administrator."},
            )

        response = await call_next(request)
        if path.startswith("/api"):
            from app.api.telemetry import record_request
            record_request(response.status_code)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager"""
    import asyncio
    from app.db.session import SessionLocal
    from app.api.health import (
        check_database, check_auth0, check_kms,
        check_secret_manager, check_vertex_ai, check_translation_api,
        record_uptime_check
    )
    import time
    
    # Background task for uptime monitoring
    async def uptime_monitor():
        """Run health checks every 5 minutes and record results."""
        while True:
            try:
                async with SessionLocal() as db:
                    # Infrastructure only, and deliberately.
                    #
                    # This list used to name Auth0, Vertex AI and Google
                    # Translate, so an Azure town accumulated a month of uptime
                    # history for three services it does not use -- the same
                    # hardcoded-vendor problem the health page had.
                    #
                    # The fix is not to widen the list. External dependencies
                    # are already swept daily by the connector check, which is
                    # daily *because* each one costs a call to somebody else's
                    # paid API; sampling eight of them every five minutes would
                    # be about 2,300 calls a day against a town's own account.
                    #
                    # So the two stop overlapping. The connector sweep owns
                    # external services, at a frequency that respects their
                    # cost. This owns the machine, where a check is a syscall
                    # and five-minute resolution is free.
                    services_to_check = [
                        ("database", check_database),
                    ]
                    
                    from app.services.uptime import uptime_status

                    for service_name, check_func in services_to_check:
                        start = time.time()
                        try:
                            check_result = await check_func(db)
                            response_time = int((time.time() - start) * 1000)
                            status = uptime_status(check_result["status"])
                            error = None if status == "healthy" else check_result.get("message")
                        except Exception as e:
                            response_time = int((time.time() - start) * 1000)
                            status = "down"
                            error = str(e)
                        
                        await record_uptime_check(db, service_name, status, response_time, error)
                    
                    # Cleanup: Delete records older than 30 days
                    from datetime import datetime, timedelta, timezone
                    from sqlalchemy import delete
                    from app.models import UptimeRecord
                    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                    result = await db.execute(
                        delete(UptimeRecord).where(UptimeRecord.checked_at < cutoff)
                    )
                    await db.commit()
                    deleted = result.rowcount
                    
                    if deleted > 0:
                        logger.info(f"[Uptime Monitor] Health check complete, cleaned up {deleted} old records")
                    else:
                        logger.debug(f"[Uptime Monitor] Health check complete at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception as e:
                logger.error(f"[Uptime Monitor] Error: {e}")
            
            # Wait 5 minutes before next check
            await asyncio.sleep(300)
    
    # Fail closed on insecure security configuration (default SECRET_KEY, etc.)
    from app.core.config import get_settings as _get_settings
    _settings = _get_settings()
    _security_problems = _settings.validate_security()
    if _security_problems:
        _msg = "Insecure security configuration:\n  - " + "\n  - ".join(_security_problems)
        if _settings.debug:
            logger.warning(f"[Security] {_msg}\n[Security] Continuing because debug=True — DO NOT run like this in production.")
        else:
            logger.critical(f"[Security] {_msg}")
            raise RuntimeError(
                "Refusing to start with insecure security configuration. "
                "Set a strong SECRET_KEY (or set DEBUG=true for local development only). " + _msg
            )

    # Startup: Initialize database with default data
    await seed_database()

    # Load the panel-set lifecycle state (managed hosting suspend/resume)
    from app.core.managed import load_lifecycle_state
    async with SessionLocal() as _lifecycle_db:
        _state = await load_lifecycle_state(_lifecycle_db)
    if _state == "suspended":
        logger.warning("[Managed] Instance is SUSPENDED — API serves 503 until the state resumes it")

    # Start background uptime monitoring task
    uptime_task = asyncio.create_task(uptime_monitor())
    logger.info("[Uptime Monitor] Started background health monitoring (every 5 minutes)")
    
    yield
    
    # Shutdown: Cancel background task
    uptime_task.cancel()
    try:
        await uptime_task
    except asyncio.CancelledError:
        pass  # Expected during shutdown
    logger.info("[Uptime Monitor] Stopped background health monitoring")


# Only expose the API schema/docs when debug is on. In production these
# would hand an attacker a full map of every route and model.
from app.core.config import get_settings as _get_settings_for_docs
_docs_enabled = _get_settings_for_docs().debug

app = FastAPI(
    title="Township 311 API",
    description="Open311-compliant civic engagement platform for municipal request management",
    version="1.0.0",
    docs_url=None,  # Disable default - we serve custom below (debug only)
    redoc_url="/api/redoc" if _docs_enabled else None,
    openapi_url="/api/openapi.json" if _docs_enabled else None,
    lifespan=lifespan
)


from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 422 validation errors without echoing the raw submitted values.

    FastAPI's default handler reflects the offending `input` (and `ctx`) back in
    the response body; a scanner injecting a payload sees it reflected and flags
    a (non-exploitable, JSON) reflected-XSS. Strip those echoed values — clients
    only need the field location, message, and type.
    """
    safe_errors = [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})


from fastapi.responses import HTMLResponse

@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui():
    """Custom Swagger UI that explicitly loads all required JS/CSS. Debug only."""
    if not _docs_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
<title>Township 311 API - Swagger UI</title>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
<script>
SwaggerUIBundle({
    url: '/api/openapi.json',
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: 'StandaloneLayout'
});
</script>
</body>
</html>
""")

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Refuse an over-large body before any route, dependency or decorator sees it.
app.add_middleware(BodySizeLimitMiddleware)

# Security headers middleware (added first, runs last)
app.add_middleware(SecurityHeadersMiddleware)

# Every authenticated change gets a line, whether or not its handler
# remembered to write one.
app.add_middleware(AdminActionAuditMiddleware)



# Managed hosting: suspend gate + telemetry request counters
app.add_middleware(ManagedModeMiddleware)

# CORS middleware - use environment-based origins for production security
# In production, set CORS_ORIGINS environment variable (comma-separated)
import os
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else []

# If no origins specified, allow localhost for development only
if not CORS_ORIGINS or CORS_ORIGINS == ['']:
    CORS_ORIGINS = [
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative dev port
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(departments.router, prefix="/api/departments", tags=["Departments"])
app.include_router(services.router, prefix="/api/services", tags=["Services"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(open311.router, prefix="/api/open311/v2", tags=["Open311"])
app.include_router(gis.router, prefix="/api/gis", tags=["GIS"])
app.include_router(roads.router, prefix="/api", tags=["Roads"])
app.include_router(map_layers.router, prefix="/api/map-layers", tags=["Map Layers"])
app.include_router(comments.router, tags=["Comments"])
app.include_router(research.router, prefix="/api/research", tags=["Research Suite"])
app.include_router(health.router, prefix="/api/health", tags=["Health Check"])
app.include_router(audit.router, prefix="/api/audit", tags=["Audit Logs"])
app.include_router(setup.router, prefix="/api/setup", tags=["Setup"])
app.include_router(provisioning.router, prefix="/api/provisioning", tags=["Provisioning (orchestrator)"])
app.include_router(telemetry.router, prefix="/api/telemetry", tags=["Telemetry (orchestrator)"])
app.include_router(api_usage.router, prefix="/api/system/api-usage", tags=["API Usage"])

# Mounted at /api because the router declares its own /export prefix --
# FastAPI concatenates the two, so the served paths are /api/export/requests
# and /api/export/statistics, exactly what the frontend calls. A "fix" here
# once moved this mount to /api/export on the theory the paths were off by a
# level; the result was /api/export/export/* and two dead buttons. The route
# table, not the mount line, is the fact to check.
app.include_router(data_export.router, prefix="/api", tags=["Data Export"])
app.include_router(integrations.router, prefix="/api/integrations", tags=["GovTech Integrations"])
# Optional module, off by default. The router is always mounted; every route
# on it checks system_settings.modules.platform_feedback and 404s when the
# town has not enabled it, so mounting it costs a disabled town nothing.
app.include_router(feedback.router, prefix="/api/feedback", tags=["Platform Feedback"])

# Mount uploads directory for serving uploaded files
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/project/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": "1.0.0"}


@app.get("/")
async def root():
    """Root endpoint - redirect info"""
    return {
        "message": "Township 311 API",
        "docs": "/api/docs",
        "health": "/api/health"
    }


@app.get("/api/sentry-debug")
async def sentry_debug():
    """Test endpoint to verify Sentry integration. Only available in debug mode."""
    from app.core.config import get_settings
    if not get_settings().debug:
        raise HTTPException(status_code=404, detail="Not found")
    if not SENTRY_DSN:
        return {"status": "sentry_not_configured", "message": "Set SENTRY_DSN env var to enable"}
    # Intentional error for testing
    raise Exception("Sentry test error - this is intentional!")


# Client error logging endpoint
from pydantic import BaseModel
from typing import Optional
import logging

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

client_error_logger = logging.getLogger("client_errors")

class ClientError(BaseModel):
    type: str
    message: str
    stack: Optional[str] = None
    componentStack: Optional[str] = None
    source: Optional[str] = None
    lineno: Optional[int] = None
    colno: Optional[int] = None
    url: Optional[str] = None
    timestamp: Optional[str] = None
    userAgent: Optional[str] = None

@app.post("/api/system/client-errors", status_code=204)
async def log_client_error(error: ClientError, db: AsyncSession = Depends(get_db)):
    """Log frontend errors for monitoring."""
    # The shared sanitizer rather than a local one: it strips the full control
    # range, not just \r\n, and it is the barrier the rest of the codebase uses.
    from app.core.sanitize import sanitize_for_log

    def sanitize(text):
        return sanitize_for_log(text, max_length=2000) if text else text

    # The stack goes in the same ERROR record, not a separate debug() call.
    #
    # It used to be logged at DEBUG, and nothing raises the level for this
    # logger, so Python's default of WARNING discarded every stack trace that
    # was ever submitted. What survived was one line naming a minified variable
    # -- "Cannot access 'Z' before initialization" -- with nothing to locate it
    # by. The report arrived and was useless, which is worse than not arriving,
    # because the UI told the user it had been handled.
    detail = (
        f"[CLIENT {sanitize(error.type)}] {sanitize(error.message)} | url={sanitize(error.url)} | "
        f"source={sanitize(error.source)}:{error.lineno}:{error.colno} | "
        f"ua={sanitize(error.userAgent)[:60] if error.userAgent else 'unknown'}"
    )
    if error.stack:
        detail += f"\n  stack: {sanitize(error.stack)[:1500]}"
    if error.componentStack:
        # For a React boundary this is usually more useful than the JS stack:
        # it names the component tree rather than minified frames.
        detail += f"\n  components: {sanitize(error.componentStack)[:800]}"
    client_error_logger.error(detail)

    # Also persisted, so it reaches somebody. The log line above only helps a
    # deployment with a Sentry DSN or an operator reading container logs;
    # neither describes a town running this on its own server, which is exactly
    # the deployment the error screen was promising a report to.
    from app.services import client_errors
    await client_errors.record(
        db,
        kind=error.type,
        message=error.message,
        stack=error.stack,
        component_stack=error.componentStack,
        url=error.url,
        user_agent=error.userAgent,
    )
    return Response(status_code=204)
