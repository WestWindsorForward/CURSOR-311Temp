"""A ceiling on the request body, inside the application.

Its own module rather than a block in main.py so it can be imported -- and
tested -- without pulling in every router, model and service the app has. The
thing it protects against is crude and cheap, so the check that stops it should
be neither hard to reach nor hard to verify.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# Largest request body this application will accept from anyone, ever.
#
# Sized off the one endpoint that needs a big one: /photos/screen takes a phone
# photo and caps it at 12MB. The margin covers multipart framing.
#
# It exists because that endpoint is the first unauthenticated multipart route
# in the app, and everything upstream of the handler runs before the handler's
# own size check can: FastAPI resolves `UploadFile` by parsing the whole
# multipart body and spooling anything over 1MB to the container's disk, and the
# rate limiter is a decorator, so it runs after that too. Caddy carries a
# matching `request_body max_size` and should refuse these first; this is here
# so a deployment whose proxy config was missed, edited, or bypassed on the
# compose network is still not one anonymous POST away from a full disk.
MAX_REQUEST_BODY_BYTES = 13 * 1024 * 1024


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """413 an over-large body before any route or dependency touches it.

    Content-Length covers the honest client. A chunked upload declares no
    length, so the body is also metered as it streams and cut off at the same
    ceiling -- otherwise the check is advisory and the attack is one header
    away.
    """

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})

        if request.headers.get("transfer-encoding", "").lower() == "chunked":
            seen = 0
            receive = request.receive

            async def metered():
                nonlocal seen
                message = await receive()
                if message.get("type") == "http.request":
                    seen += len(message.get("body", b""))
                    if seen > MAX_REQUEST_BODY_BYTES:
                        raise _BodyTooLarge()
                return message

            request._receive = metered
            try:
                return await call_next(request)
            except _BodyTooLarge:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})

        return await call_next(request)


class _BodyTooLarge(Exception):
    """Unwinds the ASGI read when a chunked body runs past the ceiling."""
