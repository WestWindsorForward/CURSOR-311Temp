"""Nothing unauthenticated may stream an unbounded body into this container.

The photo-screening endpoint is the app's first unauthenticated multipart route,
and its own 12MB check is the LAST of three gates rather than the first. By the
time that handler runs, FastAPI has already parsed the multipart body to
populate `UploadFile` -- spooling anything over 1MB to the container's disk --
and the rate-limit decorator has run after that. So an anonymous POST streaming
an arbitrary body fills the disk without ever reaching a line of our code that
could say no.

Caddy carries a `request_body max_size` for this reason. This module is about
the second copy of that ceiling, inside the application, because the proxy
config can be edited, regenerated for a custom domain, or bypassed by anything
already on the compose network -- and "the disk fills up" is not a failure mode
worth leaving to a single point of configuration.
"""

import pytest

pytest.importorskip("starlette.middleware.base")
pytest.importorskip("starlette.responses")

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_caddy_caps_the_body_at_the_edge():
    if not (ROOT / "Caddyfile").exists():
        # Only the backend tree is mounted in some runners; the proxy config is
        # a repo-root file. The in-application ceiling below is checked either
        # way, which is the half that has to hold on its own anyway.
        pytest.skip("repository root not available")
    caddyfile = (ROOT / "Caddyfile").read_text()
    assert "request_body" in caddyfile, (
        "no request_body ceiling in the Caddyfile; an unauthenticated multipart "
        "POST reaches the API container with no size limit in front of it"
    )
    assert "max_size" in caddyfile


def test_the_application_does_not_rely_on_the_proxy_alone():
    main = (ROOT / "backend" / "app" / "main.py").read_text()
    assert "BodySizeLimitMiddleware" in main
    assert "app.add_middleware(BodySizeLimitMiddleware)" in main, (
        "the middleware exists but is not installed"
    )


@pytest.mark.asyncio
async def test_an_over_large_declared_body_is_refused_before_routing():
    from app.core.body_limit import MAX_REQUEST_BODY_BYTES, BodySizeLimitMiddleware

    reached = False

    async def call_next(request):
        nonlocal reached
        reached = True
        raise AssertionError("routing should not have been reached")

    class _Req:
        headers = {"content-length": str(MAX_REQUEST_BODY_BYTES + 1)}

    response = await BodySizeLimitMiddleware(app=None).dispatch(_Req(), call_next)
    assert response.status_code == 413
    assert not reached


@pytest.mark.asyncio
async def test_a_body_of_a_normal_size_passes_straight_through():
    from app.core.body_limit import BodySizeLimitMiddleware

    async def call_next(request):
        return "routed"

    class _Req:
        headers = {"content-length": "2048"}

    assert await BodySizeLimitMiddleware(app=None).dispatch(_Req(), call_next) == "routed"


@pytest.mark.asyncio
async def test_a_chunked_body_is_metered_rather_than_trusted():
    """A chunked upload declares no length, so the header check is one header
    away from being advisory. The body is counted as it arrives instead."""
    from app.core.body_limit import MAX_REQUEST_BODY_BYTES, BodySizeLimitMiddleware

    chunk = b"x" * (1024 * 1024)
    sent = 0

    async def receive():
        nonlocal sent
        sent += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    class _Req:
        headers = {"transfer-encoding": "chunked"}
        def __init__(self):
            self.receive = receive

    request = _Req()

    async def call_next(_):
        # Drain the way the multipart parser would.
        while True:
            await request._receive()

    response = await BodySizeLimitMiddleware(app=None).dispatch(request, call_next)
    assert response.status_code == 413
    assert sent <= MAX_REQUEST_BODY_BYTES + len(chunk), "read past the ceiling before stopping"
