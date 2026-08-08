"""A hard rate limit must fail invisibly in the portal and loudly for the admin.

The owner's requirement, verbatim: "if I set a hard rate limit for Google Maps
or AI and I hit it, it fails gracefully so it looks fine within the portal to
staff and to residents, but admin would get flagged somewhere."

Both halves are load-bearing and they pull in opposite directions:

  * the resident/staff half was already true -- geocoding falls through to
    OpenStreetMap, translation returns the originals, redaction degrades to the
    on-server detector, AI triage stores a manual-review default. These tests
    pin that half so the new flagging cannot break it.
  * the admin half was entirely missing: every one of those degradations was
    silent. `connector_health.note_quota_failure` is the fix, and the tests
    here prove each runtime path calls it -- once, because a 429 storm arrives
    hundreds at a time on the resident intake path and each write costing a DB
    round-trip would make the bookkeeping dearer than the failed call.

No module-level importorskip on third-party packages other than where a path
genuinely needs one -- see tests/test_migrate.py for why a blanket skip is how
a regression test silently stops running in CI. The pure detection and
throttle tests import only connector_health, which is stdlib + app.core.
Where a guard is needed it names a submodule (`sqlalchemy.ext.asyncio`), for
the reason test_migrate.py records: a directory next to the tests can resolve
as a namespace package and make importorskip on the bare name succeed against
a package that is not installed.
"""

import ast
from pathlib import Path

import pytest

from app.services import connector_health as ch


class _Recorder:
    """Captures record_failure calls so no test needs a database."""

    def __init__(self, monkeypatch):
        self.calls = []

        async def _record(db, connector, error, provider=None):
            self.calls.append((connector, str(error), provider))

        monkeypatch.setattr(ch, "record_failure", _record)
        ch._reset_quota_throttle()

    @property
    def connectors(self):
        return [c[0] for c in self.calls]


# ---- the quota shapes, per provider -----------------------------------------

@pytest.mark.parametrize("message", [
    "Vertex AI API error (429): RESOURCE_EXHAUSTED: Quota exceeded",
    "Azure OpenAI HTTP 429: {'error': {'code': '429'}}",
    "Google geocoding over quota (OVER_QUERY_LIMIT): rate limit exceeded",
    "An error occurred (ThrottlingException) when calling the TranslateText operation: Rate exceeded",
    "Too Many Requests",
    "openai insufficient_quota: you exceeded your current quota",
    "Esri geocoding over quota (HTTP 429)",
])
def test_provider_quota_messages_are_recognised(message):
    assert ch.is_quota_error(message)


@pytest.mark.parametrize("message", [
    "401 Unauthorized",
    "quota project not set for this credential",  # config problem, not a limit
    "connection refused",
    "Vertex AI API error (500): internal",
    "",
    None,
])
def test_non_quota_failures_are_not_recognised(message):
    assert not ch.is_quota_error(message)


def test_http_429_is_recognised_structurally_not_just_textually():
    """httpx.HTTPStatusError carries the status on .response; the message may
    not contain '429' at all once a proxy rewrites it."""
    class Response:
        status_code = 429

    class QuotaError(Exception):
        response = Response()

    assert ch.is_quota_error(QuotaError("something opaque"))


def test_a_street_address_containing_429_is_not_a_quota_error():
    """httpx bakes the request URL into every error string, and a geocoding
    URL's query legitimately carries the resident's address. The query string
    is scrubbed before matching, so '429 Elm St' cannot flag the maps card."""
    assert not ch.is_quota_error(
        "Client error '404' for url 'https://maps.example/geocode?address=429+Elm+St&key=x'"
    )


# ---- the throttle ------------------------------------------------------------

def test_first_quota_failure_in_a_window_records_and_repeats_do_not():
    ch._reset_quota_throttle()
    assert ch._quota_should_record("maps", now=1000.0)
    assert not ch._quota_should_record("maps", now=1001.0)
    # A different connector's storm is independent evidence.
    assert ch._quota_should_record("translation", now=1001.0)
    # And the window genuinely expires rather than muting forever.
    later = 1000.0 + ch.QUOTA_RECORD_EVERY.total_seconds() + 1
    assert ch._quota_should_record("maps", now=later)


async def test_note_quota_failure_writes_once_then_throttles(monkeypatch):
    rec = _Recorder(monkeypatch)
    assert await ch.note_quota_failure("ai", "HTTP 429 Too Many Requests", provider="vertex")
    assert not await ch.note_quota_failure("ai", "HTTP 429 Too Many Requests", provider="vertex")
    assert rec.calls == [("ai", "HTTP 429 Too Many Requests", "vertex")]


async def test_note_quota_failure_ignores_non_quota_errors(monkeypatch):
    """Callers pass every failure through unclassified; only limit-shaped ones
    may touch the health row, or an ordinary timeout would masquerade as a
    quota problem on the card."""
    rec = _Recorder(monkeypatch)
    assert not await ch.note_quota_failure("ai", "connection refused", provider="vertex")
    assert rec.calls == []


# ---- geocoding: Google over quota falls through to OSM, flags maps ----------

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _QuotaThenOsmClient:
    """Google answers OVER_QUERY_LIMIT; Nominatim answers with a result."""

    async def get(self, url, params=None, headers=None):
        if "googleapis" in url:
            return _Resp(200, {"status": "OVER_QUERY_LIMIT", "results": [],
                               "error_message": "You have exceeded your daily request quota"})
        return _Resp(200, [{"lat": "40.22", "lon": "-74.01", "display_name": "1 Main St, Testville"}])


async def test_google_geocode_quota_falls_back_to_osm_and_flags_maps(monkeypatch):
    pytest.importorskip("httpx")
    from app.services import geocode_dispatch as gd

    rec = _Recorder(monkeypatch)

    async def selected(db):
        return "google", {"apiKey": "k"}

    async def bias(db):
        return None

    monkeypatch.setattr(gd, "_selected", selected)
    monkeypatch.setattr(gd, "_bias", bias)

    result = await gd._run(None, "forward", _QuotaThenOsmClient(), "1 Main St")
    # The resident's answer is the OSM one -- the fallback IS the feature.
    assert result is not None
    assert result.formatted_address == "1 Main St, Testville"
    # And exactly one health row write, on the capability the cards read.
    assert rec.calls == [("maps",
                          "Google geocoding over quota (OVER_QUERY_LIMIT): "
                          "You have exceeded your daily request quota",
                          "google")]

    # The storm case: an identical failure a moment later is throttled.
    again = await gd._run(None, "forward", _QuotaThenOsmClient(), "2 Main St")
    assert again is not None
    assert len(rec.calls) == 1


async def test_esri_body_level_429_flags_maps(monkeypatch):
    """ArcGIS reports throttling as HTTP 200 with an error body, so the wire
    status never says 429."""
    pytest.importorskip("httpx")
    from app.services import geocode_dispatch as gd

    rec = _Recorder(monkeypatch)

    class Client:
        async def get(self, url, params=None, headers=None):
            return _Resp(200, {"error": {"code": 429, "message": "Rate limited"}})

    out = await gd._esri(Client(), "1 Main St", {}, None)
    assert out is None  # falls through; _run would try OSM next
    assert rec.connectors == ["maps"]


# ---- translation: originals come back, translation row is flagged -----------

async def test_translate_batch_quota_returns_originals_and_flags(monkeypatch):
    pytest.importorskip("sqlalchemy.ext.asyncio")
    from app.services import translation
    from app.services import translation_providers as tp

    rec = _Recorder(monkeypatch)

    class Response:
        status_code = 429

    class QuotaError(Exception):
        response = Response()

    class Provider:
        provider = "google"

        async def translate(self, texts, source_lang, target_lang):
            raise QuotaError("quota exceeded for translate.googleapis.com")

    async def get_provider():
        return Provider()

    async def no_cache(text, target_lang):
        return None

    monkeypatch.setattr(tp, "get_translation_provider", get_provider)
    monkeypatch.setattr(translation, "get_cached_translation", no_cache)

    out = await translation.translate_batch(["Pothole", "Streetlight out"], "en", "es")
    # Residents get English rather than a 5xx -- unchanged behavior.
    assert out == {"Pothole": "Pothole", "Streetlight out": "Streetlight out"}
    assert rec.connectors == ["translation"]
    assert rec.calls[0][2] == "google"

    # Second batch inside the window: same graceful answer, no second write.
    out2 = await translation.translate_batch(["Graffiti"], "en", "es")
    assert out2 == {"Graffiti": "Graffiti"}
    assert len(rec.calls) == 1


# ---- photo redaction: degrade to local detection, flag redaction ------------

async def test_cloud_detector_quota_degrades_to_local_and_flags(monkeypatch):
    from app.services import image_redaction as ir

    rec = _Recorder(monkeypatch)

    class Response:
        status_code = 429

    class QuotaError(Exception):
        response = Response()

    async def google_detect(raw, width, height, faces, plates):
        raise QuotaError("vision.googleapis.com rate limit")

    local_ran = []

    async def local_detect(raw, width, height, faces, plates):
        local_ran.append(True)
        return []

    async def usable(provider):
        return True

    monkeypatch.setattr(ir, "_google_detect", google_detect)
    monkeypatch.setattr(ir, "_local_detect", local_detect)
    monkeypatch.setattr(ir, "_usable", usable)
    monkeypatch.setattr(ir, "_decode", lambda m: (b"not-really-a-jpeg", "image/jpeg"))
    monkeypatch.setattr(ir, "image_size", lambda raw: (100, 100))

    result = await ir.redact_image("data:image/jpeg;base64,xxxx", "google", True, True)

    # The photo survives (the local detector answered), and the skip reason is
    # honest -- not "no-detector", which would mean nothing looked at it.
    assert result.skipped_reason == "no-detections"
    assert local_ran == [True]
    assert rec.calls == [("redaction", "vision.googleapis.com rate limit", "google")]


async def test_local_detector_failures_never_flag_redaction(monkeypatch):
    """Local detection has no quota to hit; its failures (OpenCV missing, a
    corrupt image) must not paint the redaction card with a provider story."""
    from app.services import image_redaction as ir

    rec = _Recorder(monkeypatch)

    async def local_detect(raw, width, height, faces, plates):
        raise RuntimeError("429")  # even a quota-shaped message

    monkeypatch.setattr(ir, "_local_detect", local_detect)

    assert await ir.detect("local", b"raw", 10, 10, True, True) is None
    assert rec.calls == []


# ---- AI triage: fallback dict instead of a raise, ai row is flagged ----------

async def test_azure_ai_429_returns_manual_review_fallback_and_flags(monkeypatch):
    pytest.importorskip("httpx")
    import httpx

    from app.services.ai.azure_openai import AzureOpenAIProvider

    class FakeResponse:
        status_code = 429
        text = '{"error": {"code": "429", "message": "Requests to the ChatCompletions_Create Operation have exceeded rate limit"}}'

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = AzureOpenAIProvider(endpoint="https://x.openai.azure.us", api_key="k")
    result = await provider.complete_json("triage this")

    # The adapter contract that keeps analyze_request processing the report:
    # never raise, hand back the manual-review default with the error attached.
    assert result["qualitative_analysis"].startswith("AI analysis could not be completed")
    assert "429" in result["_error"]

    # And the task-side wiring records that _error on the ai connector once.
    rec = _Recorder(monkeypatch)
    assert await ch.note_quota_failure("ai", result["_error"], provider="azure")
    assert not await ch.note_quota_failure("ai", result["_error"], provider="azure")
    assert rec.connectors == ["ai"]


def test_analyze_request_flags_both_ways_an_ai_quota_failure_can_arrive():
    """The triage task's two AI failure shapes both reach the health row.

    Read statically, which is a compromise worth naming: `analyze_request` is a
    Celery task that opens its own session, loads a request, enriches it from
    four services and writes back, so driving it needs a database and most of
    the app -- neither of which this suite has. The behaviour above is covered
    where it is testable (the adapter returns the fallback dict; the note is
    throttled and lands on `ai`); what is left to lose is the *wiring*, and the
    way to lose it is an edit to this task that drops one of the two branches.
    Both matter: the adapters are contracted never to raise, so the `_error`
    dict is the normal quota path, and the `except` is what catches an adapter
    that breaks that contract -- which is exactly when nobody is watching.
    """
    source = (Path(__file__).resolve().parents[1]
              / "app" / "tasks" / "service_requests.py").read_text()
    task = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == "analyze_request")

    def flags_ai(node) -> bool:
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr == "note_quota_failure":
                first = call.args[0] if call.args else None
                if isinstance(first, ast.Constant) and first.value == "ai":
                    return True
        return False

    error_branches = [n for n in ast.walk(task)
                      if isinstance(n, ast.If) and "_error" in ast.dump(n.test)]
    assert error_branches, "the AI fallback-dict branch moved or was renamed"
    assert any(flags_ai(b) for b in error_branches)

    handlers = [h for h in ast.walk(task) if isinstance(h, ast.ExceptHandler)]
    assert any(flags_ai(h) for h in handlers)
