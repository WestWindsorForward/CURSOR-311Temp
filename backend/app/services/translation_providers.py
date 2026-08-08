"""Pluggable translation providers (Google Cloud Translation / Azure Translator).

The public translation API (translation.py: translate_text / translate_batch)
keeps its DB caching and usage tracking; only the raw provider call is
abstracted here so a jurisdiction can run translation on Google or Azure by
config. Default is google — existing deployments are unchanged.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

TRANSLATION_PROVIDER_KEY = "TRANSLATION_PROVIDER"

GOOGLE_TRANSLATE_API_URL = "https://translation.googleapis.com/language/translate/v2"
# Azure Translator: global host by default; Gov cloud uses ...microsofttranslator.us
DEFAULT_AZURE_TRANSLATOR_ENDPOINT = "https://api.cognitive.microsofttranslator.com"


class TranslationProvider:
    provider = "base"

    async def translate(self, texts: List[str], source_lang: str, target_lang: str) -> Optional[List[str]]:
        """Translate texts (aligned to input order). Return None if the provider
        is not configured; raise only on unexpected errors (callers handle)."""
        raise NotImplementedError


class GoogleTranslationProvider(TranslationProvider):
    provider = "google"

    async def translate(self, texts, source_lang, target_lang):
        # Reuse the existing Google auth resolution (service account or API key)
        from app.services.translation import _get_auth_headers
        auth = await _get_auth_headers()
        if not auth:
            return None
        headers, params = {}, {}
        if "_api_key" in auth:
            params["key"] = auth["_api_key"]
        else:
            headers = auth
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GOOGLE_TRANSLATE_API_URL, params=params, headers=headers,
                json={"q": texts, "source": source_lang, "target": target_lang, "format": "text"},
            )
            resp.raise_for_status()
            data = resp.json()
        translations = data.get("data", {}).get("translations", [])
        out = []
        for i, t in enumerate(texts):
            out.append(translations[i].get("translatedText", t) if i < len(translations) else t)
        return out


class AzureTranslationProvider(TranslationProvider):
    provider = "azure"

    def __init__(self, api_key: str, region: str, endpoint: Optional[str] = None):
        self.api_key = api_key
        self.region = region
        self.endpoint = (endpoint or DEFAULT_AZURE_TRANSLATOR_ENDPOINT).rstrip("/")

    async def translate(self, texts, source_lang, target_lang):
        if not self.api_key:
            return None
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        params = {"api-version": "3.0", "from": source_lang, "to": target_lang}
        body = [{"Text": t} for t in texts]
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.endpoint}/translate", params=params, headers=headers, json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        out = []
        for i, t in enumerate(texts):
            try:
                out.append(data[i]["translations"][0]["text"])
            except (IndexError, KeyError, TypeError):
                out.append(t)
        return out


class AWSTranslateProvider(TranslationProvider):
    """Amazon Translate via boto3. boto3 is synchronous, so calls run in a
    thread executor. For AWS GovCloud stacks (us-gov-*)."""
    provider = "aws"

    def __init__(self, region: str, access_key: Optional[str] = None,
                 secret_key: Optional[str] = None, session_token: Optional[str] = None):
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token

    def _client(self):
        import boto3
        kwargs = {"region_name": self.region}
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
            if self.session_token:
                kwargs["aws_session_token"] = self.session_token
        return boto3.client("translate", **kwargs)

    async def translate(self, texts, source_lang, target_lang):
        if not self.region:
            return None
        import asyncio

        def _run():
            client = self._client()
            out = []
            # Kept alongside the originals rather than raised: one bad string
            # must not cost the rest of the batch, but swallowing the error
            # entirely is how a ThrottlingException storm stays invisible --
            # the caller substitutes originals and nobody upstream ever sees
            # AWS said "Rate exceeded". The first error is enough evidence.
            errors = []
            # Amazon Translate is one string per call; batch sequentially.
            for t in texts:
                try:
                    resp = client.translate_text(
                        Text=t, SourceLanguageCode=source_lang, TargetLanguageCode=target_lang,
                    )
                    out.append(resp.get("TranslatedText", t))
                except Exception as exc:
                    if not errors:
                        errors.append(exc)
                    out.append(t)
            return out, errors

        try:
            out, errors = await asyncio.get_event_loop().run_in_executor(None, _run)
            if errors:
                # Back on the event loop, so the async health write is possible.
                from app.services import connector_health
                await connector_health.note_quota_failure(
                    "translation", errors[0], provider=self.provider)
            return out
        except Exception as e:
            logger.warning(f"AWS Translate failed: {e}")
            return None


TRANSLATION_CATALOG: Dict[str, Dict[str, Any]] = {
    "google": {
        "name": "Google Cloud Translation",
        "description": "Google Translate — the default; ~100+ languages.",
        # Dispatch authenticates with the service account
        # (translation._get_auth_headers reads GCP_SERVICE_ACCOUNT_JSON); the
        # project id is never read on this path. This card used to collect
        # GOOGLE_CLOUD_PROJECT as its required credential, so "configured"
        # meant "typed a project name" on a translator that could not
        # authenticate. The service account is a bootstrap credential the
        # first-run setup collects, so it is declared in `requires` -- the
        # badge sees it -- rather than offered as a second box writing the
        # same secret.
        "credential_fields": [],
        "requires": [
            {"key": "GCP_SERVICE_ACCOUNT_JSON", "label": "Google service account", "where": "Setup"},
        ],
    },
    "azure": {
        "name": "Azure AI Translator",
        "description": "Azure Cognitive Services Translator — for Microsoft/Azure-Government stacks.",
        "credential_fields": [
            {"key": "AZURE_TRANSLATOR_KEY", "label": "Translator Key", "secret": True},
            {"key": "AZURE_TRANSLATOR_REGION", "label": "Region", "secret": False},
            {"key": "AZURE_TRANSLATOR_ENDPOINT", "label": "Endpoint (optional; .us for Gov)", "secret": False},
        ],
        "field_help": {
            "AZURE_TRANSLATOR_KEY": "Key from your Azure Translator resource.",
            "AZURE_TRANSLATOR_REGION": "e.g. usgovvirginia or eastus.",
            "AZURE_TRANSLATOR_ENDPOINT": "Leave blank for global; use https://api.cognitive.microsofttranslator.us for Azure Government.",
        },
    },
    "aws": {
        "name": "Amazon Translate",
        "description": "AWS Translate — for AWS GovCloud stacks; uses your AWS credentials.",
        "credential_fields": [
            {"key": "AWS_REGION", "label": "AWS Region", "secret": False},
            {"key": "AWS_ACCESS_KEY_ID", "label": "Access Key ID (optional with instance role)", "secret": False},
            {"key": "AWS_SECRET_ACCESS_KEY", "label": "Secret Access Key (optional with instance role)", "secret": True},
        ],
        "field_help": {
            "AWS_REGION": "e.g. us-gov-west-1.",
            "AWS_ACCESS_KEY_ID": "Leave blank to use the instance role / default credential chain.",
            "AWS_SECRET_ACCESS_KEY": "Leave blank to use the instance role / default credential chain.",
        },
    },
}


def catalog_for_api() -> List[Dict[str, Any]]:
    return [{"provider": k, **v} for k, v in TRANSLATION_CATALOG.items()]


async def get_translation_provider() -> Optional[TranslationProvider]:
    """Return the configured translation provider (default google), or None if
    the selected provider isn't configured -- or if the town switched
    translation off while leaving its credentials in place.

    Google is the default and needs no key of its own beyond the cloud account,
    so before the switch existed a town on Google Cloud got translation whether
    or not it wanted it, and had nothing it could remove to stop it.
    """
    from app.services import capability_switches
    from app.services.secret_manager import get_secret

    if not await capability_switches.enabled("translation"):
        return None

    provider = (await get_secret(TRANSLATION_PROVIDER_KEY)) or "google"
    provider = provider.strip().lower()
    # A stored "none" is a choice, not a missing value -- the same words
    # image_redaction.resolve_provider honours. Falling through to the Google
    # default here dispatched translations for a town whose status page said
    # translation was off.
    if provider in ("none", "off", "disabled"):
        return None
    if provider == "azure":
        key = await get_secret("AZURE_TRANSLATOR_KEY")
        if not key:
            return None
        return AzureTranslationProvider(
            api_key=key,
            region=await get_secret("AZURE_TRANSLATOR_REGION") or "",
            endpoint=await get_secret("AZURE_TRANSLATOR_ENDPOINT"),
        )
    if provider == "aws":
        region = await get_secret("AWS_REGION")
        if not region:
            return None
        return AWSTranslateProvider(
            region=region,
            access_key=await get_secret("AWS_ACCESS_KEY_ID"),
            secret_key=await get_secret("AWS_SECRET_ACCESS_KEY"),
            session_token=await get_secret("AWS_SESSION_TOKEN"),
        )
    # Google is validated by _get_auth_headers at call time
    return GoogleTranslationProvider()
