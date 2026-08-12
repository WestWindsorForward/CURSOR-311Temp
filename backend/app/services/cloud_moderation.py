"""Cloud-native content moderation (text + image), provider-agnostic.

Optional layer on top of the always-on better-profanity text scan. When a
moderation provider is configured it screens text AND images through the
configured cloud's native, purpose-built moderation service:

    google  -> Vision SafeSearch (image) + Natural Language moderateText (text)
    azure   -> Azure AI Content Safety  image:analyze / text:analyze
    aws     -> Rekognition DetectModerationLabels (image) + Comprehend
               DetectToxicContent (text)

Local footprint is ~zero — the ML runs in the cloud; we just make an HTTPS/boto
call (httpx and boto3 are already dependencies). It is decoupled from the AI
*triage* provider, so it stays available even if AI analysis is off, and it is
treated as an optional provider: if it isn't configured or a call fails, these
functions return None / an unflagged result and the caller falls back (text ->
better-profanity only; image -> the AI vision assessment). Nothing ever raises
into the request path.

The provider is chosen by MODERATION_PROVIDER, defaulting to match the cloud
the deployment already runs its AI on (vertex->google, azure->azure,
bedrock->aws), so "pick your cloud" fans out here too.
"""

import asyncio
import base64
import logging
from typing import Any, Dict, List, Optional

from app.services.content_moderation import ModerationResult

logger = logging.getLogger(__name__)

MODERATION_PROVIDER_KEY = "MODERATION_PROVIDER"

# Category labels (per cloud) that we treat as "explicit/abusive" for blocking.
_AZURE_BLOCK_CATS = {"Sexual", "Hate", "Violence", "SelfHarm"}
_GOOGLE_TEXT_BLOCK_CATS = {"Toxic", "Insult", "Sexual", "Violent", "Death, Harm & Tragedy",
                           "Firearms & Weapons", "Illicit Drugs", "Profanity"}
_REKOGNITION_BLOCK_CATS = {"Explicit Nudity", "Explicit", "Sexual", "Violence",
                           "Visually Disturbing", "Hate Symbols", "Nudity", "Graphic Violence"}
_COMPREHEND_BLOCK_LABELS = {"SEXUAL", "HATE_SPEECH", "VIOLENCE_OR_THREAT", "GRAPHIC",
                            "HARASSMENT_OR_ABUSE", "PROFANITY"}


async def _provider() -> Optional[str]:
    from app.services.secret_manager import get_secret
    override = (await get_secret(MODERATION_PROVIDER_KEY)) or ""
    override = override.strip().lower()
    if override in ("google", "azure", "aws"):
        return override
    if override in ("none", "off", "disabled"):
        return None
    ai = ((await get_secret("AI_PROVIDER")) or "vertex").strip().lower()
    return {"vertex": "google", "azure": "azure", "bedrock": "aws"}.get(ai)


# --------------------------- pure severity mappers ---------------------------
# Take a parsed provider response and return a ModerationResult. No network, so
# they are unit-testable. severity 0-7 (Azure) / likelihood / confidence are all
# normalized to none|mild|severe; severe -> blocked.

def azure_severity(categories_analysis: List[Dict[str, Any]]) -> ModerationResult:
    worst = 0
    cats = []
    for c in categories_analysis or []:
        sev = int(c.get("severity", 0) or 0)
        if c.get("category") in _AZURE_BLOCK_CATS and sev > 0:
            cats.append(c["category"])
            worst = max(worst, sev)
    if worst >= 4:
        return ModerationResult(True, "severe", ["cloud:azure"], sorted(set(cats)))
    if worst >= 2:
        return ModerationResult(True, "mild", ["cloud:azure"], sorted(set(cats)))
    return ModerationResult()


_LIKELY = {"VERY_LIKELY": 4, "LIKELY": 3, "POSSIBLE": 2, "UNLIKELY": 1, "VERY_UNLIKELY": 0, "UNKNOWN": 0}


def google_safesearch_severity(annotation: Dict[str, Any]) -> ModerationResult:
    adult = _LIKELY.get(annotation.get("adult", "UNKNOWN"), 0)
    violence = _LIKELY.get(annotation.get("violence", "UNKNOWN"), 0)
    racy = _LIKELY.get(annotation.get("racy", "UNKNOWN"), 0)
    if adult >= 3 or violence >= 4:
        return ModerationResult(True, "severe", ["cloud:google_vision"], ["adult" if adult >= 3 else "violence"])
    if adult >= 2 or violence >= 3 or racy >= 4:
        return ModerationResult(True, "mild", ["cloud:google_vision"], ["racy"])
    return ModerationResult()


def google_text_severity(categories: List[Dict[str, Any]]) -> ModerationResult:
    hits, worst = [], 0.0
    for c in categories or []:
        conf = float(c.get("confidence", 0) or 0)
        if c.get("name") in _GOOGLE_TEXT_BLOCK_CATS:
            hits.append(c["name"])
            worst = max(worst, conf)
    if worst >= 0.8:
        return ModerationResult(True, "severe", ["cloud:google_text"], sorted(set(hits)))
    if worst >= 0.5:
        return ModerationResult(True, "mild", ["cloud:google_text"], sorted(set(hits)))
    return ModerationResult()


def rekognition_severity(labels: List[Dict[str, Any]]) -> ModerationResult:
    hits, worst = [], 0.0
    for lab in labels or []:
        conf = float(lab.get("Confidence", 0) or 0)
        name = lab.get("Name", "")
        parent = lab.get("ParentName", "")
        if name in _REKOGNITION_BLOCK_CATS or parent in _REKOGNITION_BLOCK_CATS:
            hits.append(name or parent)
            worst = max(worst, conf)
    if worst >= 80:
        return ModerationResult(True, "severe", ["cloud:rekognition"], sorted(set(hits)))
    if worst >= 50:
        return ModerationResult(True, "mild", ["cloud:rekognition"], sorted(set(hits)))
    return ModerationResult()


def comprehend_severity(result: Dict[str, Any]) -> ModerationResult:
    worst, hits = 0.0, []
    for seg in result.get("ResultList", []) or []:
        worst = max(worst, float(seg.get("Toxicity", 0) or 0))
        for lab in seg.get("Labels", []) or []:
            score = float(lab.get("Score", 0) or 0)
            if lab.get("Name") in _COMPREHEND_BLOCK_LABELS:
                hits.append(lab["Name"])
                worst = max(worst, score)
    if worst >= 0.8:
        return ModerationResult(True, "severe", ["cloud:comprehend"], sorted(set(hits)))
    if worst >= 0.5:
        return ModerationResult(True, "mild", ["cloud:comprehend"], sorted(set(hits)))
    return ModerationResult()


# ------------------------------ image helpers --------------------------------

def _to_bytes(media: str) -> Optional[bytes]:
    """Decode a media entry to raw bytes for the moderation API. Handles
    data: URIs and bare base64; http(s) URLs are skipped (not fetched — SSRF)."""
    if not media or not isinstance(media, str):
        return None
    s = media.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return None  # rely on the AI vision path for externally-hosted URLs
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        return None


# ------------------------------ text moderation ------------------------------

async def moderate_text(text: str) -> Optional[ModerationResult]:
    """Cloud text moderation. Returns None when no provider is configured or the
    call fails (caller keeps the better-profanity verdict). Never raises."""
    if not text or not text.strip():
        return None
    provider = await _provider()
    if not provider:
        return None
    try:
        if provider == "azure":
            return await _azure_text(text)
        if provider == "google":
            return await _google_text(text)
        if provider == "aws":
            return await _aws_text(text)
    except Exception as e:
        from app.core.sanitize import sanitize_for_log
        logger.info("[Moderation] cloud text (%s) unavailable: %s", provider, sanitize_for_log(str(e)))
    return None


async def moderate_images(media: List[str]) -> ModerationResult:
    """Cloud image moderation over up to 3 media entries. Returns the strongest
    verdict, or an unflagged result when no provider is configured / on error."""
    provider = await _provider()
    if not provider or not media:
        return ModerationResult()
    strongest = ModerationResult()
    rank = {"none": 0, "mild": 1, "severe": 2}
    for m in media[:3]:
        raw = _to_bytes(m)
        if not raw:
            continue
        try:
            if provider == "azure":
                r = await _azure_image(raw)
            elif provider == "google":
                r = await _google_image(raw)
            elif provider == "aws":
                r = await _aws_image(raw)
            else:
                r = ModerationResult()
        except Exception as e:
            from app.core.sanitize import sanitize_for_log
            logger.info("[Moderation] cloud image (%s) unavailable: %s", provider, sanitize_for_log(str(e)))
            continue
        if rank[r.severity] > rank[strongest.severity]:
            strongest = r
    return strongest


# ------------------------------ Azure Content Safety -------------------------

async def _azure_creds():
    from app.services.secret_manager import get_secret
    endpoint = (await get_secret("AZURE_CONTENT_SAFETY_ENDPOINT")) or ""
    key = await get_secret("AZURE_CONTENT_SAFETY_KEY")
    return endpoint.rstrip("/"), key


async def _azure_text(text: str) -> Optional[ModerationResult]:
    import httpx
    endpoint, key = await _azure_creds()
    if not endpoint or not key:
        return None
    url = f"{endpoint}/contentsafety/text:analyze?api-version=2024-09-01"
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0)) as client:
        resp = await client.post(url, headers={"Ocp-Apim-Subscription-Key": key}, json={"text": text[:10000]})
        resp.raise_for_status()
        return azure_severity(resp.json().get("categoriesAnalysis", []))


async def _azure_image(raw: bytes) -> ModerationResult:
    import httpx
    endpoint, key = await _azure_creds()
    if not endpoint or not key:
        return ModerationResult()
    url = f"{endpoint}/contentsafety/image:analyze?api-version=2024-09-01"
    payload = {"image": {"content": base64.b64encode(raw).decode("ascii")}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=6.0)) as client:
        resp = await client.post(url, headers={"Ocp-Apim-Subscription-Key": key}, json=payload)
        resp.raise_for_status()
        return azure_severity(resp.json().get("categoriesAnalysis", []))


# ------------------------------ Google ---------------------------------------

async def _google_token_and_project():
    """Reuse the Vertex service-account path for a bearer token + project."""
    from app.services.secret_manager import get_secret
    sa_json = await get_secret("VERTEX_AI_SERVICE_ACCOUNT_KEY")
    project = await get_secret("VERTEX_AI_PROJECT") or await get_secret("GOOGLE_CLOUD_PROJECT")

    def _sync():
        import json
        import google.auth
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if sa_json:
            creds = service_account.Credentials.from_service_account_info(json.loads(sa_json), scopes=scopes)
        else:
            creds, _ = google.auth.default(scopes=scopes)
        creds.refresh(Request())
        return creds.token

    token = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return token, project


async def _google_text(text: str) -> Optional[ModerationResult]:
    import httpx
    token, _ = await _google_token_and_project()
    if not token:
        return None
    url = "https://language.googleapis.com/v2/documents:moderateText"
    body = {"document": {"type": "PLAIN_TEXT", "content": text[:20000]}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0)) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json=body)
        resp.raise_for_status()
        return google_text_severity(resp.json().get("moderationCategories", []))


# Read timeout for one images:annotate call.
#
# Was 20s, chosen when the full-size phone photo went up the wire and the upload
# could genuinely take that long. Two things changed. The bytes are now a
# downscaled copy (see image_redaction.SCREEN_MAX_EDGE), roughly a twentieth of
# the payload, so the upload term that justified 20s is gone. And a failure no
# longer publishes the photo unredacted -- it withholds it for staff review --
# so waiting longer no longer buys safety, it only buys a longer wait before the
# same fallback.
#
# 12s is roughly 4x the p99 of an annotate on a ~200KB image. Long enough that a
# slow-but-working Vision still answers, short enough that a wedged one is a
# noticeable pause rather than an abandoned form.
VISION_TIMEOUT_SECONDS = 12.0


async def vision_annotate(raw: bytes, features: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One images:annotate call, however many features are asked for.

    Vision bills per feature but charges one round trip for the batch, so
    SafeSearch + face + text together cost three feature-units and one HTTPS
    request rather than three. That matters because this runs on a resident's
    latency budget -- at photo-pick time on the portal, and still at submit time
    for API clients that post media directly.

    Returns {} when Google is not configured, so callers can treat "no
    credentials" the same as "nothing detected".
    """
    import httpx
    token, _ = await _google_token_and_project()
    if not token or not features:
        return {}
    body = {"requests": [{"image": {"content": base64.b64encode(raw).decode("ascii")},
                          "features": features}]}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(VISION_TIMEOUT_SECONDS, connect=5.0)
    ) as client:
        resp = await client.post("https://vision.googleapis.com/v1/images:annotate",
                                 headers={"Authorization": f"Bearer {token}"}, json=body)
        resp.raise_for_status()
        return resp.json()


def safesearch_from_payload(payload: Dict[str, Any]) -> ModerationResult:
    """Pull the SafeSearch verdict out of an annotate response.

    Split from the request so the redaction path, which asks for SafeSearch and
    the detection features in the same call, can read the moderation half of
    that shared response without issuing a second one.
    """
    responses = (payload or {}).get("responses") or []
    return google_safesearch_severity(responses[0].get("safeSearchAnnotation", {}) if responses else {})


async def _google_image(raw: bytes) -> ModerationResult:
    return safesearch_from_payload(await vision_annotate(raw, [{"type": "SAFE_SEARCH_DETECTION"}]))


# ------------------------------ AWS ------------------------------------------

async def _aws_kwargs():
    from app.services.secret_manager import get_secret
    region = await get_secret("AWS_REGION")
    kwargs = {"region_name": region} if region else {}
    ak, sk = await get_secret("AWS_ACCESS_KEY_ID"), await get_secret("AWS_SECRET_ACCESS_KEY")
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        st = await get_secret("AWS_SESSION_TOKEN")
        if st:
            kwargs["aws_session_token"] = st
    return kwargs if region else None


async def _aws_text(text: str) -> Optional[ModerationResult]:
    kwargs = await _aws_kwargs()
    if not kwargs:
        return None

    def _sync():
        import boto3
        client = boto3.client("comprehend", **kwargs)
        return client.detect_toxic_content(
            TextSegments=[{"Text": text[:10000]}], LanguageCode="en")

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return comprehend_severity(result)


async def _aws_image(raw: bytes) -> ModerationResult:
    kwargs = await _aws_kwargs()
    if not kwargs:
        return ModerationResult()

    def _sync():
        import boto3
        client = boto3.client("rekognition", **kwargs)
        return client.detect_moderation_labels(Image={"Bytes": raw}, MinConfidence=50)

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return rekognition_severity(result.get("ModerationLabels", []))
