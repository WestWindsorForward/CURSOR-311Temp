"""Accela signs in through Accela, and the sign-in survives its own rotation.

Three things are worth holding still here.

**The state parameter is the only authentication the callback has.** Accela
redirects a browser back to us with no session, no header, and no cookie we
control. If a forged or stale state were accepted, an attacker could hand an
admin a link carrying *their* authorization code and quietly point the town's
Accela sync at their own account. So the signature, the expiry, and the binding
to one integration are asserted directly rather than assumed.

**A refresh token that isn't written back is a connection that works once.**
Accela retires the old refresh token on every exchange. The rotation is easy to
implement and easy to forget to persist, and the failure shows up hours later as
"the nightly sync stopped", not as a test failure.

**Scope.** The token used to be requested with `records` alone while
`pull_assets` called `/v4/assets` — an endpoint that scope never covered. The
asset sync is the one part of this connector nobody watches interactively, so
the scope is pinned in a test rather than left to a comment.
"""

import logging
import time

import httpx
import pytest

import app.integrations.base as base

# The autouse fixture below disables the SSRF guard so ordinary tests can use a
# fake transport; the auth_base tests put the real one back.
_REAL_ASSERT_PUBLIC_URL = base._assert_public_url
from app.integrations import accela_oauth
from app.integrations.base import ConnectorError
from app.integrations.connectors.accela import AccelaConnector, _clear_token_cache
from app.integrations.registry import PLATFORM_CATALOG, build_connector



def _needs_fastapi():
    """The connector itself is plain httpx, so most of this file runs anywhere.
    Only the handful of tests that reach into the API layer need the web stack;
    they guard here rather than at module scope, which would take the OAuth and
    scope assertions down with them."""
    pytest.importorskip("fastapi.routing")


CONFIG = {"agency_name": "SPRINGFIELD", "environment": "PROD", "record_type": "SR/General/Complaint/NA"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Every test starts with an empty token cache and no real DNS or network."""
    _clear_token_cache()
    monkeypatch.setattr(base, "_assert_public_url", lambda url: None)
    yield
    _clear_token_cache()


def _transport(monkeypatch, handler):
    """Answer every outbound request with `handler(request)`, recording each one."""
    seen = []

    async def fake(self, request):
        body = request.content.decode() if request.content else ""
        seen.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "form": dict(httpx.QueryParams(body)) if body else {},
        })
        return handler(request, len(seen) - 1)

    monkeypatch.setattr(base.httpx.AsyncHTTPTransport, "handle_async_request", fake)
    return seen


def _json(request, payload, status=200):
    return httpx.Response(status, json=payload, request=request)


def _app(monkeypatch, client_id="pinpoint-app", client_secret="pinpoint-secret"):
    async def _creds():
        return client_id, client_secret
    monkeypatch.setattr(accela_oauth, "app_credentials", _creds)


# ---------------------------------------------------------------------------
# CSRF state
# ---------------------------------------------------------------------------

def test_state_carries_the_integration_admin_and_redirect_it_was_minted_for():
    state = accela_oauth.sign_state(7, "admin-42", "https://town.gov/api/integrations/accela/oauth/callback")
    payload = accela_oauth.verify_state(state)
    assert payload is not None
    assert payload["iid"] == 7
    assert payload["uid"] == "admin-42"
    assert payload["ru"] == "https://town.gov/api/integrations/accela/oauth/callback"


def test_two_states_are_never_the_same():
    """A replayed state should be distinguishable from a fresh one, so the nonce
    has to actually vary."""
    a = accela_oauth.sign_state(1, "u", "https://town.gov/cb")
    b = accela_oauth.sign_state(1, "u", "https://town.gov/cb")
    assert a != b


@pytest.mark.parametrize("mangle", [
    lambda s: s.split(".")[0],                              # signature stripped
    lambda s: s.split(".")[0] + ".AAAA",                    # signature replaced
    lambda s: "x" + s,                                      # payload edited
    lambda s: s.replace(".", "-"),                          # not even the right shape
    lambda s: "",
])
def test_a_state_we_did_not_sign_is_refused(mangle):
    state = accela_oauth.sign_state(3, "admin", "https://town.gov/cb")
    assert accela_oauth.verify_state(mangle(state)) is None


def test_a_state_signed_with_another_key_is_refused(monkeypatch):
    """The attacker's own deployment must not be able to mint states for ours."""
    monkeypatch.setattr(accela_oauth, "_signing_key", lambda: b"someone-elses-key" * 2)
    forged = accela_oauth.sign_state(3, "attacker", "https://town.gov/cb")
    monkeypatch.undo()
    assert accela_oauth.verify_state(forged) is None


def test_a_state_goes_stale(monkeypatch):
    state = accela_oauth.sign_state(3, "admin", "https://town.gov/cb")
    real_time = time.time
    monkeypatch.setattr(accela_oauth.time, "time",
                        lambda: real_time() + accela_oauth.STATE_TTL_SECONDS + 5)
    assert accela_oauth.verify_state(state) is None


def test_a_state_from_the_future_is_refused(monkeypatch):
    """Guards the other end of the window: a clock-skewed forgery shouldn't buy
    an attacker an indefinitely valid token."""
    state = accela_oauth.sign_state(3, "admin", "https://town.gov/cb")
    real_time = time.time
    monkeypatch.setattr(accela_oauth.time, "time", lambda: real_time() - 3600)
    assert accela_oauth.verify_state(state) is None


# ---------------------------------------------------------------------------
# The authorize redirect
# ---------------------------------------------------------------------------

def test_the_authorize_url_asks_accela_for_a_code_with_both_scopes():
    url = accela_oauth.authorize_url(
        client_id="pinpoint-app",
        redirect_uri="https://town.gov/api/integrations/accela/oauth/callback",
        state="signed-state",
        agency_name="SPRINGFIELD",
        environment="test",
        config=CONFIG,
    )
    params = dict(httpx.QueryParams(url.split("?", 1)[1]))
    assert url.startswith("https://auth.accela.com/oauth2/authorize?")
    assert params["response_type"] == "code"
    assert params["client_id"] == "pinpoint-app"
    assert params["redirect_uri"] == "https://town.gov/api/integrations/accela/oauth/callback"
    assert params["agency_name"] == "SPRINGFIELD"
    assert params["environment"] == "TEST"
    assert params["state"] == "signed-state"
    assert set(params["scope"].split()) == {"records", "assets"}


def test_a_town_can_override_the_scope():
    url = accela_oauth.authorize_url(
        client_id="a", redirect_uri="https://t/cb", state="s", agency_name="A",
        config={"scope": "records"},
    )
    assert dict(httpx.QueryParams(url.split("?", 1)[1]))["scope"] == "records"


async def test_a_pinned_redirect_uri_wins(monkeypatch):
    async def _pinned(key):
        return "https://shared.pinpoint311.org/api/integrations/accela/oauth/callback"
    monkeypatch.setattr(accela_oauth, "_deployment_value", _pinned)
    assert await accela_oauth.redirect_uri_for(None, "https://town.gov/") == (
        "https://shared.pinpoint311.org/api/integrations/accela/oauth/callback"
    )


async def test_the_redirect_uri_comes_from_the_configured_public_origin(monkeypatch):
    """Behind the TLS-terminating proxy, request.base_url is http://backend:8000/ —
    a URL Accela can neither match nor redirect to. The deployment's configured
    public origin is what the callback must be built on."""
    _needs_fastapi()
    import app.api.system as system_mod

    monkeypatch.delenv(accela_oauth.REDIRECT_URI_KEY, raising=False)
    monkeypatch.setattr(accela_oauth, "_deployment_value", lambda key: _none())

    async def _origin(db):
        return "https://town.pinpoint311.org"

    monkeypatch.setattr(system_mod, "public_origin", _origin)
    assert await accela_oauth.redirect_uri_for(object(), "http://backend:8000/") == (
        "https://town.pinpoint311.org/api/integrations/accela/oauth/callback"
    )


async def test_with_nothing_configured_the_request_url_is_a_logged_last_resort(monkeypatch, caplog):
    _needs_fastapi()
    import app.api.system as system_mod

    monkeypatch.delenv(accela_oauth.REDIRECT_URI_KEY, raising=False)
    monkeypatch.setattr(accela_oauth, "_deployment_value", lambda key: _none())

    async def _no_origin(db):
        return None

    monkeypatch.setattr(system_mod, "public_origin", _no_origin)
    with caplog.at_level(logging.WARNING, logger="app.integrations.accela_oauth"):
        uri = await accela_oauth.redirect_uri_for(object(), "https://town.gov/")
    assert uri == "https://town.gov/api/integrations/accela/oauth/callback"
    assert any("public origin" in r.getMessage() for r in caplog.records)


async def _none():
    return None


# ---------------------------------------------------------------------------
# Where the sign-in is allowed to point
# ---------------------------------------------------------------------------
#
# auth_base is an admin-settable config key, and the code exchange posts the
# deployment-level client secret to it. Without these checks, one town's admin
# on a shared host could point the flow at their own server and collect the
# secret every town uses.

def _resolves_to(monkeypatch, ip):
    monkeypatch.setattr(
        base.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, (ip, 443))],
    )


def test_without_an_override_the_auth_base_is_pinned_to_accela():
    assert accela_oauth.auth_base({}) == "https://auth.accela.com"
    assert accela_oauth.auth_base(None) == "https://auth.accela.com"


def test_an_override_on_accelas_own_domain_is_accepted(monkeypatch):
    monkeypatch.setattr(base, "_assert_public_url", _REAL_ASSERT_PUBLIC_URL)
    _resolves_to(monkeypatch, "34.201.10.10")  # any public address
    assert accela_oauth.auth_base({"auth_base": "https://auth.accela.com/"}) == (
        "https://auth.accela.com"
    )


@pytest.mark.parametrize("bad", [
    "https://attacker.example.com",
    "https://evilaccela.com",            # dot-boundary: not a subdomain
    "https://accela.com.attacker.net",   # suffix in the middle doesn't count
    "https://ACCELA.example.org",        # case games don't help either
    "https://127.0.0.1:8443",
])
def test_an_override_off_accela_dot_com_is_refused(monkeypatch, bad):
    monkeypatch.setattr(base, "_assert_public_url", _REAL_ASSERT_PUBLIC_URL)
    with pytest.raises(accela_oauth.OAuthError) as exc:
        accela_oauth.auth_base({"auth_base": bad})
    assert "accela.com" in str(exc.value)


def test_an_internal_host_is_refused_even_dressed_as_accela(monkeypatch):
    """A name under accela.com that resolves inward is still SSRF — the public-URL
    guard has to run on the override, not just the suffix check."""
    monkeypatch.setattr(base, "_assert_public_url", _REAL_ASSERT_PUBLIC_URL)
    _resolves_to(monkeypatch, "10.0.0.5")
    with pytest.raises(accela_oauth.OAuthError) as exc:
        accela_oauth.auth_base({"auth_base": "https://internal.accela.com"})
    assert "internal" in str(exc.value)


async def test_the_exchange_never_posts_the_secret_off_accela(monkeypatch):
    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {"refresh_token": "rt"}))
    monkeypatch.setattr(accela_oauth.httpx, "AsyncClient", base.httpx.AsyncClient)
    with pytest.raises(accela_oauth.OAuthError):
        await accela_oauth.exchange_code(
            code="c", redirect_uri="https://town.gov/cb",
            config={"auth_base": "https://attacker.example.com"},
        )
    assert seen == []


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------

async def test_exchanging_a_code_uses_the_deployment_app(monkeypatch):
    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
    }))
    monkeypatch.setattr(accela_oauth.httpx, "AsyncClient", base.httpx.AsyncClient)

    tokens = await accela_oauth.exchange_code(
        code="the-code", redirect_uri="https://town.gov/cb", config=CONFIG,
    )
    assert tokens["refresh_token"] == "rt"
    form = seen[0]["form"]
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == "https://town.gov/cb"
    assert form["client_id"] == "pinpoint-app"
    assert seen[0]["headers"]["x-accela-appid"] == "pinpoint-app"


async def test_a_deployment_with_no_accela_app_says_so(monkeypatch):
    _app(monkeypatch, client_id=None, client_secret=None)
    with pytest.raises(accela_oauth.OAuthError) as exc:
        await accela_oauth.exchange_code(code="c", redirect_uri="https://town.gov/cb")
    assert "ACCELA_CLIENT_ID" in str(exc.value)


async def test_an_exchange_without_a_refresh_token_is_a_failure(monkeypatch):
    """An access token alone expires in an hour and leaves no way back — that is
    a broken connection, not a connected one."""
    _app(monkeypatch)
    _transport(monkeypatch, lambda req, i: _json(req, {"access_token": "at", "expires_in": 3600}))
    monkeypatch.setattr(accela_oauth.httpx, "AsyncClient", base.httpx.AsyncClient)
    with pytest.raises(accela_oauth.OAuthError) as exc:
        await accela_oauth.exchange_code(code="c", redirect_uri="https://town.gov/cb")
    assert "refresh token" in str(exc.value).lower()


async def test_a_rejected_exchange_does_not_echo_the_client_secret(monkeypatch):
    _app(monkeypatch)
    _transport(monkeypatch, lambda req, i: httpx.Response(
        400, text='{"error":"invalid_grant","client_secret":"pinpoint-secret"}', request=req))
    monkeypatch.setattr(accela_oauth.httpx, "AsyncClient", base.httpx.AsyncClient)
    with pytest.raises(accela_oauth.OAuthError) as exc:
        await accela_oauth.exchange_code(code="c", redirect_uri="https://town.gov/cb")
    assert "pinpoint-secret" not in str(exc.value)


# ---------------------------------------------------------------------------
# The connector's token handling
# ---------------------------------------------------------------------------

async def test_a_stored_refresh_token_is_what_gets_exchanged(monkeypatch):
    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "fresh-access", "refresh_token": "rt-1", "expires_in": 3600,
    }))
    c = AccelaConnector(CONFIG, {"refresh_token": "rt-0"})

    assert await c._get_token() == "fresh-access"
    form = seen[0]["form"]
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "rt-0"
    assert "password" not in form and "username" not in form


async def test_a_rotated_refresh_token_is_written_back(monkeypatch):
    """Accela retires the old token on exchange. Losing the new one means the
    next nightly sync is locked out, hours after anyone was watching."""
    _app(monkeypatch)
    _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "at", "refresh_token": "rt-NEW", "expires_in": 3600,
    }))
    persisted = []

    async def _persist(values):
        persisted.append(values)

    c = AccelaConnector(CONFIG, {"refresh_token": "rt-OLD"})
    c.persist_credentials = _persist
    await c._get_token()

    assert persisted == [{"refresh_token": "rt-NEW"}]
    assert c.credentials["refresh_token"] == "rt-NEW"


async def test_an_unrotated_refresh_token_is_not_rewritten(monkeypatch):
    _app(monkeypatch)
    _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "at", "refresh_token": "rt-SAME", "expires_in": 3600,
    }))
    persisted = []

    async def _persist(values):
        persisted.append(values)

    c = AccelaConnector(CONFIG, {"refresh_token": "rt-SAME"})
    c.persist_credentials = _persist
    await c._get_token()
    assert persisted == []


async def test_the_access_token_is_reused_across_operations(monkeypatch):
    """Not just a latency win: each refresh rotates the refresh token, so a
    connector that refreshed per operation would rotate several times per sync
    and race itself when two operations overlap."""
    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": f"at-{i}", "refresh_token": f"rt-{i}", "expires_in": 3600,
    }))
    c = AccelaConnector(CONFIG, {"refresh_token": "rt-0"})

    first = await c._get_token()
    second = await AccelaConnector(CONFIG, {"refresh_token": c.credentials["refresh_token"]})._get_token()

    assert first == second == "at-0"
    assert len(seen) == 1


async def test_an_expired_access_token_is_refreshed(monkeypatch):
    import app.integrations.connectors.accela as accela_mod

    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": f"at-{i}", "refresh_token": f"rt-{i + 1}", "expires_in": 3600,
    }))
    c = AccelaConnector(CONFIG, {"refresh_token": "rt-0"})
    assert await c._get_token() == "at-0"

    real_time = time.time
    monkeypatch.setattr(accela_mod.time, "time", lambda: real_time() + 7200)
    assert await c._get_token() == "at-1"
    assert len(seen) == 2


async def test_different_agencies_do_not_share_a_token(monkeypatch):
    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": f"at-{i}", "refresh_token": "rt", "expires_in": 3600,
    }))
    await AccelaConnector({**CONFIG, "agency_name": "A"}, {"refresh_token": "rt"})._get_token()
    await AccelaConnector({**CONFIG, "agency_name": "B"}, {"refresh_token": "rt"})._get_token()
    assert len(seen) == 2


async def test_the_password_grant_still_works_for_towns_that_want_it(monkeypatch):
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "at", "expires_in": 3600,
    }))
    c = AccelaConnector(CONFIG, {
        "client_id": "town-app", "client_secret": "town-secret",
        "username": "svc-311", "password": "hunter2",
    })
    assert await c._get_token() == "at"
    form = seen[0]["form"]
    assert form["grant_type"] == "password"
    assert form["username"] == "svc-311"
    assert form["agency_name"] == "SPRINGFIELD"


@pytest.mark.parametrize("creds", [
    {"refresh_token": "rt-0"},
    {"client_id": "town-app", "client_secret": "town-secret",
     "username": "svc-311", "password": "hunter2"},
])
async def test_every_grant_asks_for_the_assets_scope(monkeypatch, creds):
    """pull_assets calls /v4/assets. A token scoped to records alone gets a 403
    there — silently, on a nightly job nobody is watching."""
    _app(monkeypatch)
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
    }))
    await AccelaConnector(CONFIG, creds)._get_token()

    if seen[0]["form"]["grant_type"] == "password":
        # The password grant names its scope on the token request.
        assert set(seen[0]["form"]["scope"].split()) == {"records", "assets"}
    else:
        # The refresh grant inherits the scope granted at authorize time, which
        # is where the connector's scope property is applied.
        assert set(AccelaConnector(CONFIG, creds).scope.split()) == {"records", "assets"}


async def test_a_town_that_supplies_its_own_app_uses_it(monkeypatch):
    _app(monkeypatch, client_id="pinpoint-app", client_secret="pinpoint-secret")
    seen = _transport(monkeypatch, lambda req, i: _json(req, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
    }))
    c = AccelaConnector(CONFIG, {
        "refresh_token": "rt-0", "client_id": "town-app", "client_secret": "town-secret",
    })
    await c._get_token()
    assert seen[0]["form"]["client_id"] == "town-app"


async def test_a_connection_with_nothing_stored_asks_to_be_signed_in(monkeypatch):
    _app(monkeypatch)
    _transport(monkeypatch, lambda req, i: _json(req, {}))
    with pytest.raises(ConnectorError) as exc:
        await AccelaConnector(CONFIG, {})._get_token()
    assert "not signed in" in str(exc.value).lower()


async def test_agency_name_is_still_required(monkeypatch):
    _app(monkeypatch)
    with pytest.raises(ConnectorError) as exc:
        await AccelaConnector({}, {"refresh_token": "rt"})._get_token()
    assert "agency_name" in str(exc.value)


async def test_a_dead_refresh_token_surfaces_as_a_refresh_failure(monkeypatch):
    """The admin needs to be told to sign in again, not to check a password the
    connection doesn't have."""
    _app(monkeypatch)
    _transport(monkeypatch, lambda req, i: httpx.Response(
        400, text='{"error":"invalid_grant"}', request=req))
    with pytest.raises(ConnectorError) as exc:
        await AccelaConnector(CONFIG, {"refresh_token": "revoked"})._get_token()

    _needs_fastapi()
    from app.api.integrations import _friendly_test_error
    assert "sign in again" in _friendly_test_error(str(exc.value)).lower()


# ---------------------------------------------------------------------------
# What the admin UI is told
# ---------------------------------------------------------------------------

def test_the_catalog_no_longer_asks_a_clerk_for_an_agency_password():
    entry = PLATFORM_CATALOG["accela"]
    required_config = {f["key"] for f in entry["config_fields"] if f.get("required")}
    assert "agency_name" in required_config

    assert entry["oauth"]["flow"] == "authorization_code"
    assert entry["oauth"]["credential_key"] == "refresh_token"

    # The password fields survive as an explicitly-labelled fallback, not as the
    # thing the wizard leads with.
    for key in ("username", "password", "client_id", "client_secret"):
        field = next(f for f in entry["credential_fields"] if f["key"] == key)
        assert "password sign-in only" in field["label"].lower()

    joined = " ".join(entry["what_you_need"]).lower()
    assert "no password is typed into pinpoint" in joined


def test_the_catalog_entry_still_builds_a_connector():
    c = build_connector("accela", CONFIG, {"refresh_token": "rt"})
    assert isinstance(c, AccelaConnector)
    assert "assets" in c.capabilities


def test_the_callback_route_is_unauthenticated_on_purpose_and_checks_its_state():
    """It has to be reachable by a browser redirect that carries no session, so
    the signed state is the only thing gating it. If that check were ever
    dropped, anyone could bind this town's Accela sync to their own account."""
    from pathlib import Path
    source = Path(base.__file__).parents[1].joinpath("api/integrations.py").read_text()
    handler = source[source.index("async def accela_oauth_callback"):]
    handler = handler[:handler.index("\n@router")]
    assert "get_current_admin" not in handler
    assert "verify_state(state)" in handler
    assert "if not payload:" in handler


def test_signing_in_clears_the_stored_password():
    """The whole point of the flow is that a government password stops living in
    our vault — including one left over from a previous password-grant setup."""
    from pathlib import Path
    source = Path(base.__file__).parents[1].joinpath("api/integrations.py").read_text()
    handler = source[source.index("async def accela_oauth_callback"):]
    assert 'for field in ("username", "password"):' in handler
    assert "merged.pop(field, None)" in handler
