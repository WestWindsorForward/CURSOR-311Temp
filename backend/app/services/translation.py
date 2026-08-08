"""
Translation service using Google Cloud Translation API with database caching.
Uses the GCP Service Account (server-side only) for authentication.
The Google Maps API key is public (sent to the browser) and should NOT be used here.

Flow:
1. Check database first for cached translation
2. If not found, call Google Translate API via service account auth
3. Store result in database for future use (persistent cache)
"""
from typing import Optional, Dict, List
import logging
import json
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)

GOOGLE_TRANSLATE_API_URL = "https://translation.googleapis.com/language/translate/v2"


async def _get_auth_headers() -> Optional[Dict[str, str]]:
    """
    Get OAuth2 authorization headers from GCP Service Account.
    Uses the server-side service account JSON (never exposed to frontend).
    """
    try:
        from app.services.secret_manager import get_secret

        sa_json = await get_secret("GCP_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            logger.warning("GCP_SERVICE_ACCOUNT_JSON not configured, translation unavailable")
            return None

        # Use google-auth to generate an access token from the service account
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request

        sa_info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-translation"]
        )
        credentials.refresh(Request())
        return {"Authorization": f"Bearer {credentials.token}"}
    except ImportError:
        logger.warning("google-auth not installed, falling back to API key auth")
        # Fallback: try the Maps API key (less secure, but works)
        return await _get_api_key_fallback()
    except Exception as e:
        logger.error(f"Failed to get GCP auth for translation: {e}")
        return None


async def _get_api_key_fallback() -> Optional[Dict[str, str]]:
    """Fallback: use API key if google-auth is not available."""
    try:
        from app.services.secret_manager import get_secret
        api_key = await get_secret("GOOGLE_MAPS_API_KEY")
        if api_key:
            # Return as a special marker so callers know to use ?key= param
            return {"_api_key": api_key}
        return None
    except Exception as e:
        logger.error(f"Fallback API key retrieval failed: {e}")
        return None


async def get_api_key() -> Optional[str]:
    """Check if translation is available (service account or API key)."""
    auth = await _get_auth_headers()
    if auth:
        return "configured"  # Just indicates availability
    return None



async def get_cached_translation(text: str, target_lang: str) -> Optional[str]:
    """Check database for cached translation."""
    try:
        from app.db.session import SessionLocal
        from app.models import Translation
        
        async with SessionLocal() as db:
            result = await db.execute(
                select(Translation).where(
                    and_(
                        Translation.source_text == text,
                        Translation.target_lang == target_lang
                    )
                )
            )
            cached = result.scalar_one_or_none()
            
            if cached:
                from app.core.sanitize import sanitize_for_log
                logger.debug(f"DB cache hit: '{text[:30]}...' -> '{sanitize_for_log(target_lang)}'")
                return cached.translated_text
            return None
    except Exception as e:
        logger.error(f"Failed to check translation cache: {e}")
        return None


async def save_translation_to_cache(text: str, target_lang: str, translated: str) -> None:
    """Save translation to database cache."""
    try:
        from app.db.session import SessionLocal
        from app.models import Translation
        
        async with SessionLocal() as db:
            # Check if already exists (race condition protection)
            result = await db.execute(
                select(Translation).where(
                    and_(
                        Translation.source_text == text,
                        Translation.target_lang == target_lang
                    )
                )
            )
            existing = result.scalar_one_or_none()
            
            if not existing:
                translation = Translation(
                    source_text=text,
                    source_lang="en",
                    target_lang=target_lang,
                    translated_text=translated
                )
                db.add(translation)
                await db.commit()
                from app.core.sanitize import sanitize_for_log
                logger.info(f"Cached translation: '{text[:30]}...' -> '{translated[:30]}...' ({sanitize_for_log(target_lang)})")
    except Exception as e:
        logger.error(f"Failed to save translation to cache: {e}")


async def translate_text(
    text: str,
    source_lang: str = "en",
    target_lang: str = "es"
) -> Optional[str]:
    """
    Translate text using Google Cloud Translation API with database caching.
    1. Check database cache first
    2. If miss, call Google Translate API
    3. Store result in database
    """
    if not text or not text.strip():
        return text
        
    if source_lang == target_lang:
        return text
    
    # 1. Check database cache first
    cached = await get_cached_translation(text, target_lang)
    if cached:
        return cached
    
    # 2. Call the configured translation provider (Google default, or Azure)
    from app.services.translation_providers import get_translation_provider
    provider = await get_translation_provider()
    if not provider:
        logger.warning("Translation not configured (no provider credentials)")
        return None

    try:
        out = await provider.translate([text], source_lang, target_lang)
        if not out:
            return None
        translated = out[0]

        # Track API usage for cost estimation
        try:
            from app.db.session import SessionLocal
            from app.services.api_usage import track_api_usage
            async with SessionLocal() as db:
                await track_api_usage(
                    db=db,
                    service_name="translation",
                    operation="translate_text",
                    characters=len(text)
                )
        except Exception as e:
            logger.warning(f"Failed to track translation usage: {e}")

        # 3. Save to database cache
        await save_translation_to_cache(text, target_lang, translated)

        return translated
    except Exception as e:
        from app.core.sanitize import sanitize_for_log
        logger.error(f"Translation failed ({sanitize_for_log(source_lang)} -> {sanitize_for_log(target_lang)}): {e}")
        # Returning None already keeps the caller (and the resident) whole;
        # a quota failure additionally belongs on the admin's health card, or
        # a town at its hard limit serves English silently for a month.
        from app.services import connector_health
        await connector_health.note_quota_failure(
            "translation", e, provider=getattr(provider, "provider", None))
        return None


async def translate_batch(
    texts: List[str],
    source_lang: str = "en",
    target_lang: str = "es"
) -> Dict[str, str]:
    """
    Translate multiple texts with database caching.
    Returns dict of {original_text: translated_text}.
    """
    if not texts:
        return {}
        
    if source_lang == target_lang:
        return {t: t for t in texts}
    
    results = {}
    uncached = []
    
    # 1. Check database cache for each text
    for text in texts:
        if not text or not text.strip():
            results[text] = text
            continue
            
        cached = await get_cached_translation(text, target_lang)
        if cached:
            results[text] = cached
        else:
            uncached.append(text)
    
    if not uncached:
        return results
    
    # 2. Call the configured translation provider (Google default, or Azure)
    from app.services.translation_providers import get_translation_provider
    provider = await get_translation_provider()
    if not provider:
        logger.warning("Translation not configured (no provider credentials)")
        for t in uncached:
            results[t] = t
        return results

    try:
        out = await provider.translate(uncached, source_lang, target_lang)
        if out is None:
            for t in uncached:
                results[t] = t
            return results
        for i, text in enumerate(uncached):
            translated = out[i] if i < len(out) else text
            results[text] = translated
            if translated != text:
                # 3. Save each to database
                await save_translation_to_cache(text, target_lang, translated)
        return results
    except Exception as e:
        logger.error(f"Batch translation failed: {e}")
        # Originals below keep /translate/batch a 200 whatever the provider
        # said; the health note is the only place a rate-limit surfaces.
        from app.services import connector_health
        await connector_health.note_quota_failure(
            "translation", e, provider=getattr(provider, "provider", None))
        for t in uncached:
            results[t] = t
        return results


async def translate_service_response(service_dict: dict, target_lang: str) -> dict:
    """
    Translate service response dict without modifying database.
    Returns a new dict with translated fields.
    """
    if target_lang == 'en':
        return service_dict
    
    result = dict(service_dict)
    
    # Translate service name
    if result.get('service_name'):
        translated = await translate_text(result['service_name'], 'en', target_lang)
        if translated:
            result['service_name'] = translated
    
    # Translate description
    if result.get('description'):
        translated = await translate_text(result['description'], 'en', target_lang)
        if translated:
            result['description'] = translated
    
    return result


def get_supported_languages() -> Dict[str, str]:
    """Get list of supported language codes and names."""
    return {
        "en": "English",
        "es": "Español", 
        "zh": "中文",
        "fr": "Français",
        "hi": "हिन्दी",
        "ar": "العربية"
    }


async def check_translation_service() -> bool:
    """Check if Google Translate API key is configured."""
    api_key = await get_api_key()
    return api_key is not None


async def get_translation_cache_stats() -> Dict:
    """Get statistics about the translation cache."""
    try:
        from app.db.session import SessionLocal
        from app.models import Translation
        from sqlalchemy import func
        
        async with SessionLocal() as db:
            # Count total translations
            total = await db.execute(select(func.count(Translation.id)))
            total_count = total.scalar()
            
            # Count by language
            by_lang = await db.execute(
                select(
                    Translation.target_lang,
                    func.count(Translation.id)
                ).group_by(Translation.target_lang)
            )
            lang_counts = {row[0]: row[1] for row in by_lang.fetchall()}
            
            return {
                "total_cached": total_count,
                "by_language": lang_counts
            }
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        return {"total_cached": 0, "by_language": {}}
