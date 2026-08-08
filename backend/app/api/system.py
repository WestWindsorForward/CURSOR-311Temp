from fastapi import (APIRouter, BackgroundTasks, Body, Depends, HTTPException, status, UploadFile,
                     File, Request, Query)
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Any, List, Optional, Dict
from pydantic import BaseModel
import subprocess
import os
import uuid
import logging
import aiofiles

logger = logging.getLogger(__name__)

from app.db.session import get_db
from app.models import SystemSettings, SystemSecret, ServiceRequest, User, DisclaimerAcknowledgment, AuditLog
from app.schemas import (
    SystemSettingsBase, SystemSettingsResponse,
    SecretCreate, SecretResponse,
    StatisticsResponse
)
from app.core.auth import get_current_admin, get_current_staff
from app.services.system_settings import get_settings as read_settings_row
from app.services.backlog_age import bucket_ages
from app.services.enqueue import QUEUE_UNAVAILABLE
from app.core.sanitize import sanitize_for_log
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter()

# Tighter per-route limits for endpoints that call paid Google APIs, on top of
# the app-wide default limit. Decorator-based enforcement (own in-memory store).
_cost_limiter = Limiter(key_func=get_remote_address)


# ============ Settings ============

@router.get("/settings", response_model=SystemSettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get system settings (public - for branding)"""
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = result.scalar_one_or_none()
    if not settings:
        # Create default settings if none exist
        settings = SystemSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def public_origin(db) -> Optional[str]:
    """The address residents actually use, or None if nothing has set one.

    Setup instructions hand out callback and redirect URLs to paste into a
    vendor console, and until now those were built from `window.location.origin`
    -- whatever the admin happened to type into their own browser. An admin on
    `http://10.0.0.7:3000`, or on a hostname that only resolves inside the
    town's network, registered a URL the identity provider can never redirect
    to. The password is accepted and the login then fails on the redirect, which
    reads as a wrong secret rather than a wrong URL.

    The deployment already knows its real address in two places, so this is a
    lookup rather than a new setting: the township's `custom_domain`, and the
    DOMAIN environment variable the compose file sets. Prefer the database,
    because that is the one an admin can change without a redeploy.
    """
    domain = os.environ.get("DOMAIN", "").strip()
    try:
        from sqlalchemy import select

        from app.models import SystemSettings

        row = (await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))).scalar_one_or_none()
        if row and (row.custom_domain or "").strip():
            domain = row.custom_domain.strip()
    except Exception:
        # Advisory only. A failure here falls back to the browser's origin,
        # which is what happened before this existed.
        pass

    if not domain or domain == "localhost":
        return None
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}"


@router.get("/config")
async def get_deployment_config(db: AsyncSession = Depends(get_db)):
    """Deployment-mode flags (public). The setup UI uses managed_mode to show
    'Managed by your state' placeholders instead of the Google Cloud / Backups
    / domain cards (A1), and public_origin to build the callback URLs it tells
    an admin to paste into a vendor console."""
    from app.core.config import get_settings as get_app_settings
    app_settings = get_app_settings()

    # Whether translating anything is currently possible, so the resident
    # portal can hide its language picker instead of offering Spanish and then
    # serving English. Switched off and not-configured read the same to a
    # resident: nothing translates.
    try:
        from app.services import capability_switches

        translation_enabled = (
            await capability_switches.enabled("translation")
            and await capability_is_configured("translation")
        )
    except Exception:
        # Only a definite "off" may hide the picker. An error working it out
        # must not take a working language switcher off the page.
        translation_enabled = True

    return {
        "managed_mode": app_settings.managed_mode,
        "app_version": app_settings.app_version,
        "public_origin": await public_origin(db),
        "translation_enabled": translation_enabled,
        # Operator-hosted registration form (CONTACT_FORM_URL). Empty string
        # when none is configured; the UI then falls back to the in-app form.
        # `contact_form_embed` says whether it may be shown in a frame inside
        # the console rather than opened in a new tab.
        "contact_form_url": (app_settings.contact_form_url or "").strip(),
        "contact_form_embed": bool(app_settings.contact_form_embed),
    }


def _field_required(field: Dict[str, Any]) -> bool:
    """Whether a credential field must be present for a provider to count as set up.

    Two conventions exist in the catalogs. The maps catalog carries an explicit
    `required` boolean; the older AI, identity and translation catalogs encode it
    by ending the label with "(optional)". Trust the flag when it is there and
    fall back to the label when it is not, rather than making every catalog
    change shape at once.
    """
    if "required" in field:
        return bool(field["required"])
    return not str(field.get("label", "")).rstrip().endswith("(optional)")


def _borrowed_requirements(provider: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Credentials this provider cannot work without but does not collect.

    Photo redaction on Google and AWS runs on the service account and access
    keys entered on other cards, and offering a second box for the same secret
    is its own bug -- whichever was filled last wins. So those providers
    declared no credentials at all, which left `_configured_map` with nothing
    required to check and a green badge on a detector with no cloud account
    behind it.

    Declaring the requirement without drawing the box separates the two
    questions that had been collapsed: what this card asks you to type, and what
    this provider needs in order to work.
    """
    return [r for r in (provider.get("requires") or []) if r.get("key")]


def _requirement_groups(provider: Dict[str, Any]) -> List[List[str]]:
    """Alternative credential sets, any one of which is enough.

    Some providers cannot be described by a per-field flag. Azure photo
    redaction needs an AI Face resource for faces and a separate AI Vision
    resource for plates, and having either one is a working setup -- a town
    stuck behind Microsoft's Limited Access review for Face runs on Vision
    alone. Marking all four required calls that town unconfigured; marking none
    required is what let an Azure card with four empty boxes read as ready.

    So the provider declares the alternatives and this reads them. Fields that
    appear in a group are deliberately *not* also flagged required, or the group
    would never get a chance to be the thing that decides.
    """
    return [list(group) for group in (provider.get("requires_any") or []) if group]


async def _skippable_keys() -> set:
    """Credentials an attached cloud identity supplies, so nothing is entered.

    The credential form already greys these out and says "nothing to enter"
    (ProviderCredentialSteps reads the same list). Without this the badge
    disagreed with the box directly above it: the form said there was nothing to
    supply and the badge said the provider was not set up because it had not
    been supplied.
    """
    try:
        from app.services import cloud_identity

        detected = cloud_identity.detect()
        if not detected:
            return set()
        return set(cloud_identity.SKIPPABLE.get(detected.get("provider") or "", []))
    except Exception:
        # A metadata probe that fails means "no attached identity", which is the
        # same as the empty set. It must never make a configured provider look
        # broken.
        return set()


async def providers_for(capability: str) -> List[Dict[str, Any]]:
    """The catalog for one capability, without going through its endpoint.

    The eight catalog endpoints each import their own module and call its
    `catalog_for_api`. That is fine for a request, but the daily connector sweep
    needs the same lists with no request to hang them off, and copying the eight
    imports into a task module would be a second place to update when a ninth
    capability appears.
    """
    if capability in ("email", "sms", "kms", "redaction", "secrets"):
        from app.services.delivery_providers import catalog_for_api as delivery
        return delivery(capability)
    loaders = {
        "ai": ("app.services.ai.registry", "catalog_for_api"),
        "translation": ("app.services.translation_providers", "catalog_for_api"),
        "identity": ("app.services.identity", "catalog_for_api"),
        "maps": ("app.services.map_provider", "catalog_for_api"),
    }
    entry = loaders.get(capability)
    if not entry:
        return []
    module, name = entry
    return getattr(__import__(module, fromlist=[name]), name)()


# What each capability falls back to when its selection secret is empty. These
# are not guesses: each one is the default the dispatch code itself applies, and
# each was already hard-coded in that capability's catalog endpoint. Gathered
# here so "which provider is this town on" has one answer instead of nine.
_CAPABILITY_DEFAULT_PROVIDER = {
    "ai": "vertex",
    "translation": "google",
    "identity": "auth0",
}

# Values that mean "switched off" wherever a provider is selected.
_OFF_VALUES = ("none", "off", "disabled")


async def effective_provider_for(capability: str) -> Optional[str]:
    """The provider this capability is actually running on right now.

    Not the stored secret. An empty `REDACTION_PROVIDER` does not mean no
    detector: `resolve_provider()` falls through to the moderation provider and
    then to the AI provider, and lands on on-server detection if neither says
    anything -- so on a town running Vertex, photo redaction is Google Cloud
    Vision and the secret is blank. Reading the raw value answered "nothing",
    and both the card and the setup checklist repeated it about a detector that
    was actively blurring faces.

    Every other capability has the milder version of the same gap: a blank
    secret means the dispatch default, which each catalog endpoint knew and
    nothing else did.

    Returns None only where "off" is a real state a town has chosen.
    """
    from app.services.secret_manager import get_secret

    select_key = _PROVIDER_SELECT_KEY.get(capability)
    if not select_key:
        return None

    if capability == "redaction":
        # The one capability whose selection is inferred rather than stored.
        from app.services import image_redaction

        return await image_redaction.resolve_provider()

    raw = ((await get_secret(select_key)) or "").strip().lower()

    if capability == "secrets":
        # `_secrets_provider()` is the reader of record: it consults the
        # environment before the database, because the host can pin the store
        # and the database copy would then be describing somewhere else. Ask it
        # rather than re-deriving the answer from the secret.
        from app.services.secret_manager import _secrets_provider
        from app.services.storage_maintenance import store_reachable

        chosen = _secrets_provider()
        # Nothing has been chosen. Not "database" -- a town that has not
        # answered the question has not chosen the database either, and saying
        # it had would tick the box the setup gate exists to hold open.
        if not chosen:
            return None
        if chosen == "database":
            return "database"
        # An unreachable store means credentials are in the encrypted database,
        # which is a supported place for them to be and a different provider
        # from the one that was selected. Saying "Google Secret Manager" about a
        # town whose secrets are all in Postgres is the kind of confident wrong
        # answer this pass is removing.
        return chosen if store_reachable() else "database"

    if capability in ("email", "sms", "kms"):
        from app.services.delivery_providers import normalize_provider

        # The ENABLED flags used to be folded in here, so that a switched-off
        # capability reported no provider at all.
        #
        # That is the conflation this pass exists to undo. "Which provider is
        # selected" and "does the town want this" are two facts, and answering
        # both with one field is why a card could not tell "switched off, key
        # still saved" from "never set up" -- it saw no provider either way, so
        # it looked at the credentials for a provider it had not been given and
        # concluded nothing was there. `/providers/status` now reports `enabled`
        # beside `configured`, and every dispatch path asks
        # `capability_switches.enabled` directly.
        resolved = normalize_provider(capability, raw)
        return None if resolved in _OFF_VALUES else resolved

    if capability == "maps":
        from app.services.map_provider import normalize_provider as normalize_map

        return normalize_map(raw)

    if raw in _OFF_VALUES:
        return None
    return raw or _CAPABILITY_DEFAULT_PROVIDER.get(capability)


async def capability_is_configured(capability: str) -> bool:
    """Whether the provider currently selected for this capability has its
    credentials stored.

    Used to decide what the daily sweep bothers testing, and it is the single
    answer the setup page's checklist now reads. A town that has not set up text
    messages has not made a mistake, and testing it would write a failure that
    shows an amber badge on something deliberately switched off -- which is the
    noise that teaches people to ignore badges.

    "Selected" means what dispatch resolves, not what is stored. Asking the raw
    secret meant photo redaction -- whose selection is inferred -- was reported
    as unconfigured on a deployment where it was blurring every photo.
    """
    from app.services import capability_switches

    # A town that switched something off has not left work outstanding, so the
    # sweep has nothing to check. Same reasoning as the paragraph above about
    # text messages, generalised now that every capability can be switched off
    # rather than only the two with an ENABLED flag.
    if not await capability_switches.enabled(capability):
        return False
    current = await effective_provider_for(capability)
    if not current:
        return False
    providers = await providers_for(capability)
    return (await _configured_map(providers)).get(current, False)


async def _stored_fields(providers: List[Dict[str, Any]]) -> Dict[str, bool]:
    """{credential key: is there a value stored for it}.

    Per field, because the form's "Saved" hint was per provider: once a provider
    counted as configured, every one of its boxes claimed to be saved --
    including an optional one nobody had ever filled in. An empty box marked
    "Saved" is worse than an unmarked one, because the reason the hint exists is
    to tell a clerk that leaving a box empty will keep the stored value rather
    than clear it, and that promise is false where there is nothing stored.

    Presence only. No value ever leaves here, and the same fact is already
    implied by `configured` -- this only says which of the fields it is made of
    are filled.
    """
    from app.services.secret_manager import get_secret

    keys = {
        field["key"]
        for provider in providers
        for field in provider.get("credential_fields", [])
    }
    out: Dict[str, bool] = {}
    for key in sorted(keys):
        try:
            out[key] = bool((await get_secret(key) or "").strip())
        except Exception:
            out[key] = False
    return out


async def _configured_map(providers: List[Dict[str, Any]]) -> Dict[str, bool]:
    """{provider id: are all of its required credentials stored}.

    This exists because three of the four capability catalogs were not returning
    it at all. The admin UI reads `configured[current_provider]`, so identity,
    translation and maps cards resolved it to undefined and reported "not
    configured" however well set up they actually were -- a false negative on a
    working connector, which is worse than no badge, because it sends someone off
    to re-paste credentials that were already fine.

    A provider with no required fields counts as configured: there is nothing to
    supply, so there is nothing missing. That rule is right and it was also
    load-bearing in the wrong direction, because a catalog that simply had not
    declared its credentials looked identical to one that needs none. Every
    photo-redaction provider declared the two blur toggles and nothing else, all
    four toggles were optional, and so all four detectors -- including Amazon
    Rekognition and Azure AI Vision on a deployment with no AWS or Azure account
    -- reported themselves configured. A provider that needs nothing is now a
    claim its catalog entry has to make on purpose.
    """
    from app.services.secret_manager import get_secret

    skippable = await _skippable_keys()

    out: Dict[str, bool] = {}
    for provider in providers:
        pkey = provider.get("provider") or provider.get("id") or ""
        if not pkey:
            continue
        required = [
            f["key"] for f in provider.get("credential_fields", [])
            if _field_required(f) and f["key"] not in skippable
        ] + [
            r["key"] for r in _borrowed_requirements(provider)
            if r["key"] not in skippable
        ]
        async def stored(key: str) -> bool:
            try:
                # `.strip()`, so that a value of " " is absent here as well as
                # everywhere else.
                #
                # Two definitions of empty had drifted apart. This one counted
                # any truthy string, and the live test stripped before checking
                # -- so a whitespace credential made a provider "configured"
                # and simultaneously untestable, and the card said "Set up.
                # There is no way to test this one from here" about a service
                # nobody had entered anything for.
                return bool((await get_secret(key) or "").strip())
            except Exception:
                # An unreachable secret store is not the same as an unconfigured
                # provider. Say nothing rather than say something false.
                return False

        present = True
        for key in required:
            if not await stored(key):
                present = False
                break

        groups = [
            [k for k in group if k not in skippable]
            for group in _requirement_groups(provider)
        ]
        if present and groups:
            present = False
            for group in groups:
                satisfied = True
                for key in group:
                    if not await stored(key):
                        satisfied = False
                        break
                if satisfied:
                    present = True
                    break

        out[pkey] = present
    return out


@router.get("/client-errors")
async def list_client_errors(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Browser crashes residents and staff have hit.

    The error screen promises a report. Without this the promise resolved to a
    line in a container log, which for a self-hosted town is the same as
    nowhere. Identical crashes are collapsed with a count, so a render loop
    shows as one row seen 400 times rather than burying every other fault.
    """
    from app.services import client_errors

    rows = await client_errors.recent(db, limit=min(max(limit, 1), 200))
    return {
        "errors": [
            {
                "id": r.id,
                "kind": r.kind,
                "message": r.message,
                "stack": r.stack,
                "component_stack": r.component_stack,
                "url": r.url,
                "occurrences": r.occurrences,
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            }
            for r in rows
        ],
    }


@router.get("/connectors/health")
async def connector_health_report(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """What each integration is actually doing, as opposed to whether its
    credentials are stored.

    Deliberately separate from the catalog endpoints. "Configured" is a fact
    about our database and is always answerable; "working" is a fact about
    someone else's service and is sometimes genuinely unknown. Merging them
    into one field would force the unknown case to pick a side, and it always
    picks green.
    """
    from app.services import connector_health as ch

    healths = ch.worst_first(list((await ch.snapshot(db)).values()))
    return {
        "connectors": [
            {
                "connector": h.connector,
                "provider": h.provider,
                "status": h.status,
                "summary": h.summary(),
                "last_success_at": h.last_success_at.isoformat() if h.last_success_at else None,
                "last_error_at": h.last_error_at.isoformat() if h.last_error_at else None,
                "last_error": h.last_error,
                "consecutive_failures": h.consecutive_failures,
                # What the last check said, and whether one is even possible.
                "last_result": h.last_result,
                "verifiable": h.verifiable,
                "total_successes": h.total_successes,
                "total_failures": h.total_failures,
                # Surfaced so the card can say alerts are muted and until when.
                # A mute that silenced the email and left no trace on screen
                # would be indistinguishable from the alerting being broken.
                "alerts_muted_until": (
                    h.alert_muted_until.isoformat() if h.alert_muted_until else None
                ),
            }
            for h in healths
        ],
        "needs_attention": [h.connector for h in healths if h.status in (ch.DOWN, ch.FAILING)],
    }


@router.post("/connectors/{connector}/mute")
async def mute_connector_alerts(
    connector: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """"I know about this one." Stops the emails; leaves the badge alone.

    Pass `{"days": 0}` to lift a mute early.

    Nothing here touches how the connector is reported on screen. A dismiss
    that also cleared the red card would turn a known problem into an invisible
    one, which is the exact failure the health system exists to prevent -- so
    the card keeps saying it is broken, and adds that nobody is being emailed
    about it and until when.
    """
    from sqlalchemy import select

    from app.models import ConnectorHealth
    from app.services import connector_alerts as alerts

    row = (await db.execute(
        select(ConnectorHealth).where(ConnectorHealth.connector == connector)
    )).scalar_one_or_none()
    if row is None:
        # Nothing has ever reported health for this name. Creating a row here
        # would let any admin request insert arbitrary connectors into a table
        # the setup page renders.
        raise HTTPException(status_code=404, detail="No health has been recorded for that service yet.")

    raw_days = payload.get("days", alerts.MUTE_FOR.days)
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days must be a whole number.")
    # Bounded. An unbounded mute is a permanently disabled alarm that nobody
    # remembers turning off, and a negative one is a mute that has already
    # expired dressed up as a mute.
    if not 0 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 0 and 90.")

    now = datetime.now(timezone.utc)
    if days == 0:
        row.alert_muted_until = None
        row.alert_muted_level = None
    else:
        row.alert_muted_until = alerts.mute_until(now, days=days)
        row.alert_muted_level = alerts.alert_level(ch_classify(row, now=now))
    await db.commit()

    # Muting an alarm is exactly the kind of act that wants a name against it
    # six months later, when somebody asks why nobody was told.
    try:
        from app.services.audit_service import AuditService

        await AuditService.log_event(
            db,
            event_type="connector_alerts_muted" if days else "connector_alerts_unmuted",
            username=getattr(admin, "username", None) or str(getattr(admin, "id", "")),
            user_id=getattr(admin, "id", None),
            success=True,
            details={"connector": connector, "days": days},
        )
    except Exception:
        # The mute has already been committed. Losing its audit line is worth
        # noting, not worth failing the request over.
        from app.core.sanitize import sanitize_for_log
        logger.warning("[Health] could not audit the mute of %s", sanitize_for_log(connector))
    return {
        "connector": connector,
        "muted_until": row.alert_muted_until.isoformat() if row.alert_muted_until else None,
        "muted_level": row.alert_muted_level,
    }


def ch_classify(row, *, now=None):
    from app.services.connector_health import classify

    return classify(row, now=now)


async def _last_result_for(db, capability: str, current: Optional[str] = None) -> Optional[dict]:
    """What the last check of this capability said, for its catalog response.

    One helper for all the catalogs. The generic route grew this and the four
    hand-written ones (ai, identity, translation, maps) did not, so their
    result boxes came back empty on reload -- in the setup guide, where
    nothing falls back to the health endpoint, the test message a clerk had
    just earned simply vanished.

    Only a verdict about the provider now selected is returned. A result
    belongs to the provider that produced it, and rehydrating the box from
    whatever was last recorded put "there is no way to check http without
    sending a real text" on a town whose SMS provider had since changed. A row
    with no provider recorded is kept: everything written before that column
    was filled looks like that, and discarding a real verdict is the worse of
    the two mistakes.
    """
    from app.services import connector_health

    h = (await connector_health.snapshot(db)).get(capability)
    if h and current and h.provider and h.provider != current:
        h = None
    if h and (h.last_result or h.last_error):
        return {
            "ok": h.ok,
            "detail": h.last_result or h.last_error,
            "status": h.status,
            "verifiable": h.verifiable,
        }
    return None


@router.get("/identity/catalog")
async def get_identity_catalog(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Identity provider catalog for the admin UI (Auth0 / Entra / Okta / OIDC),
    plus which provider is active."""
    from app.services.identity import catalog_for_api, IDENTITY_PROVIDER_KEY
    from app.services.secret_manager import get_secret
    current = ((await get_secret(IDENTITY_PROVIDER_KEY)) or "auth0").strip().lower()
    providers = catalog_for_api()
    return {"current_provider": current, "default_provider": "auth0",
            "providers": providers, "configured": await _configured_map(providers),
            "stored_fields": await _stored_fields(providers),
            "last_result": await _last_result_for(db, "identity", current)}


@router.get("/translation/catalog")
async def get_translation_catalog(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Translation provider catalog (Google / Azure) + current selection."""
    from app.services.translation_providers import catalog_for_api, TRANSLATION_PROVIDER_KEY
    from app.services.secret_manager import get_secret
    current = ((await get_secret(TRANSLATION_PROVIDER_KEY)) or "google").strip().lower()
    providers = catalog_for_api()
    return {"current_provider": current, "default_provider": "google",
            "providers": providers, "configured": await _configured_map(providers),
            "stored_fields": await _stored_fields(providers),
            "last_result": await _last_result_for(db, "translation", current)}


@router.get("/maps/catalog")
async def get_maps_catalog(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Map provider catalog. Maps is a capability like AI or translation, so it
    uses the same catalog/save/test endpoints and the same card in the UI --
    a town switches its map the way it switches anything else."""
    from app.services.map_provider import MAP_PROVIDER_KEY, catalog_for_api, normalize_provider
    from app.services.secret_manager import get_secret
    current = normalize_provider(await get_secret(MAP_PROVIDER_KEY))
    providers = catalog_for_api()
    return {"current_provider": current, "default_provider": "google",
            "providers": providers, "configured": await _configured_map(providers),
            "stored_fields": await _stored_fields(providers),
            "last_result": await _last_result_for(db, "maps", current)}


@router.get("/ai/catalog")
async def get_ai_catalog(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """AI provider catalog for the admin UI: available boundaries (Vertex /
    Azure Government / Bedrock), their models, and the fields each needs, plus
    which provider is currently selected and which are configured.

    Model lists are served from the live-discovery cache when available (a
    provider's actual current models, refreshed on demand and daily) and fall
    back to the curated catalog. No live network call happens here — the load
    stays fast; refreshing is explicit (POST /ai/models/refresh) or scheduled."""
    from app.services.ai.registry import AI_CATALOG, catalog_for_api, AI_PROVIDER_KEY, AI_MODEL_KEY
    from app.services.ai import model_discovery as md
    from app.services.secret_manager import get_secret

    current_provider = (await get_secret(AI_PROVIDER_KEY)) or "vertex"
    current_model = await get_secret(AI_MODEL_KEY)

    # Overlay the discovered model lists (per-provider) onto the curated catalog.
    cache = await md.load_db_cache(db)
    providers = catalog_for_api()

    # Shared with the other three capabilities rather than a second copy of the
    # same rule. The AI-only version this replaces inferred "required" purely
    # from the label ending in "(optional)", which silently mis-reads any
    # catalog that states it as a flag.
    configured = await _configured_map(providers)
    for p in providers:
        entry = cache.get(p["provider"])
        if entry and entry.get("models"):
            p["models"] = entry["models"]
            p["models_source"] = entry.get("source", "live")
            p["models_fetched_at"] = entry.get("fetched_at")
        else:
            p["models_source"] = "curated"
            p["models_fetched_at"] = None

    resolved_model = current_model or AI_CATALOG.get(current_provider, {}).get("default_model")
    current_models = next((p["models"] for p in providers if p["provider"] == current_provider), [])
    return {
        "current_provider": current_provider,
        "default_provider": "vertex",
        "current_model": resolved_model,
        "current_model_available": md.model_is_available(current_models, current_model),
        "configured": configured,
        "stored_fields": await _stored_fields(providers),
        "providers": providers,
        "last_result": await _last_result_for(db, "ai", current_provider),
    }


class AIModelRefreshRequest(BaseModel):
    provider: str


@router.post("/ai/models/refresh")
@_cost_limiter.limit("6/minute")  # live provider API call
async def refresh_ai_models(
    request: Request,
    body: AIModelRefreshRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Live-discover a provider's current models and update the shared cache.
    Returns the merged list plus whether the configured model is still offered."""
    from app.services.ai.registry import AI_CATALOG
    provider = (body.provider or "").strip().lower()
    if provider not in AI_CATALOG:
        raise HTTPException(status_code=400, detail=f"Unknown AI provider: {provider}")
    from app.services.ai import model_discovery as md
    result = await md.refresh_provider(db, provider)
    return {"provider": provider, **result}


@router.get("/{capability}/catalog", include_in_schema=False)
async def get_capability_catalog(
    capability: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Catalog for the capabilities added after the first four, which each had
    their own near-identical route. Declared before /ai/catalog would shadow it
    Registered after every hand-written catalog route, because FastAPI takes the
    first match: declared earlier, this would shadow /ai/catalog and 404 it."""
    from app.services.delivery_providers import _CATALOGS, catalog_for_api, normalize_provider
    if capability not in _CATALOGS:
        raise HTTPException(status_code=404, detail="Unknown capability")
    from app.services.secret_manager import get_secret
    from app.services.delivery_providers import _DEFAULTS

    # What is running, not what is stored. `normalize_provider` alone answered
    # "on this server (no cloud)" for photo redaction on a deployment where
    # `resolve_provider()` had settled on Google Cloud Vision and was using it
    # -- the card named one detector and a different one did the work.
    current = await effective_provider_for(capability)
    if current is None and capability != "secrets":
        current = normalize_provider(
            capability, await get_secret(_PROVIDER_SELECT_KEY[capability])
        )
    providers = catalog_for_api(capability)

    return {
        "current_provider": current,
        # No default for the secret store, because having one is the bug the
        # setup gate exists to close. `_DEFAULTS["secrets"]` is still "google",
        # and reporting it here made the card draw Google Secret Manager as the
        # selected store on a town that had chosen nothing -- while the gate
        # beside it asked where credentials should go and every save returned
        # 409. Two answers to one question, and the confident one was wrong.
        "default_provider": None if capability == "secrets" else _DEFAULTS[capability],
        "providers": providers,
        "configured": await _configured_map(providers),
        # Which individual boxes have something in them, so the form's
        # "Saved" hint stops appearing on empty optional ones.
        "stored_fields": await _stored_fields(providers),
        "last_result": await _last_result_for(db, capability, current),
        # Whether this card may change the selection. The secret store may not:
        # every credential the town has is in the current one and repointing the
        # setting does not move them, so a Save button here would be one click
        # that makes every other card unreadable. The card shows what is in use
        # and offers the check; the cloud-profile flow owns the switch.
        "selectable": capability not in _READ_ONLY_SELECTION,
    }


# ---- Unified provider save + test (AI / translation / identity) ----

_PROVIDER_SELECT_KEY = {
    "ai": "AI_PROVIDER",
    "translation": "TRANSLATION_PROVIDER",
    "identity": "IDENTITY_PROVIDER",
    "maps": "MAP_PROVIDER",
    # Four capabilities whose provider switch already existed in the dispatch
    # code and had no catalog, so nothing surfaced them: notifications went out
    # through a hand-written SMTP/Twilio card, and KMS and photo redaction could
    # only be changed by setting a secret by hand.
    "email": "EMAIL_PROVIDER",
    "sms": "SMS_PROVIDER",
    "kms": "KMS_PROVIDER",
    "redaction": "REDACTION_PROVIDER",
    # Where credentials themselves are kept. Listed so it can be badged, swept
    # and tested like the rest; see _READ_ONLY_SELECTION for why it cannot be
    # switched from a card.
    "secrets": "SECRETS_PROVIDER",
}


# Capabilities whose provider may be reported but not chosen here.
#
# Switching the secret store is not like switching a map vendor: every
# credential this town has is in the old one, and repointing the setting does
# not move them. A picker on a card would be a single click that makes every
# other card's credentials unreadable. It is set by the cloud-profile flow,
# which moves the secrets across, and that stays the only way.
_READ_ONLY_SELECTION = {"secrets"}


# Settings resolved once per process and then held for its lifetime. That is
# right for something fixed at deploy and wrong for anything this console can
# edit, and each of these is editable from a card.
_PROCESS_CACHED_PREFIXES = ("KMS_", "GCP_SERVICE_ACCOUNT_JSON", "GOOGLE_CLOUD_PROJECT")
_IDENTITY_PREFIXES = ("AUTH0_", "ENTRA_", "OKTA_", "OIDC_", "IDENTITY_PROVIDER")


def _invalidate_process_caches(key_name: str) -> None:
    """Drop whatever this process is holding that the new value replaces.

    Never raises: this runs inside a save, and failing to clear a cache must not
    fail the write that produced it.
    """
    try:
        if key_name.startswith(_PROCESS_CACHED_PREFIXES):
            # The KMS client and the resolved key path are both process-lifetime
            # globals. Change the key ring or the key name and the process keeps
            # wrapping resident data against the old path while the card reports
            # the new one.
            from app.core.encryption import reset_kms_cache

            reset_kms_cache()
        if key_name.startswith(_IDENTITY_PREFIXES):
            # The OIDC discovery document is cached per issuer with no
            # expiry, so a provider that moves its endpoints -- or a town that
            # corrects a mistyped issuer -- keeps being sent to the old ones.
            from app.services import identity

            identity._discovery_cache.clear()
    except Exception:
        from app.core.sanitize import sanitize_for_log

        logger.warning("Could not clear the in-process cache for %s", sanitize_for_log(key_name))


async def _vaulted_key_names() -> set:
    """Keys whose only copy is in the secret store.

    A migrated secret keeps its database row with `key_value` scrubbed to NULL,
    so this is exactly the set that becomes unreadable if the store is
    repointed. Never raises: it decides whether to add a warning, and failing to
    warn must not fail the operation being warned about.
    """
    try:
        from app.db.session import SessionLocal

        async with SessionLocal() as session:
            rows = await session.execute(
                select(SystemSecret.key_name).where(
                    SystemSecret.is_configured.is_(True),
                    SystemSecret.key_value.is_(None),
                )
            )
            return set(rows.scalars().all())
    except Exception:
        return set()


def _require_a_secret_store() -> None:
    """Refuse a credential until the town has said where credentials go.

    Not tidiness. `_persist_secret` falls back to the encrypted database when
    the external store is unreachable, and says so (`db_only`). `vault_secrets`
    later sweeps those into the store and scrubs the database copy -- on a
    schedule, and again after every provider save -- so the live database heals
    itself and the whole thing looks harmless.

    Database *backups* taken inside that window do not heal. They keep the
    plaintext-equivalent row forever and they go off-site: a pg_dump of this
    instance contains `COPY public.system_secrets (id, key_name, key_value,
    ...)`. Sweeping the live row reaches nothing that has already been dumped.
    So the credential has to not be written until somebody has decided where it
    belongs.

    The gate is on the *choice*, not on standing up a cloud vault. The encrypted
    database is one of the four answers, the on-screen copy says what that means
    for backups, and a town whose cloud procurement is unfinished is not dead-
    ended. What must not happen is a town landing there without being asked.
    """
    from app.services.secret_manager import SECRET_STORES, store_chosen

    if store_chosen():
        return
    raise HTTPException(
        status_code=409,
        detail=(
            "Choose where this town's credentials are kept before entering any. "
            "Until that is answered a credential is written to the encrypted "
            "database, and any backup taken before it is moved keeps a copy that "
            "moving it cannot reach. Pick a store in Setup Instructions — the "
            "encrypted database is one of the answers."
        ),
        headers={"X-Pinpoint-Secret-Stores": ",".join(SECRET_STORES)},
    )


# Settings that record where credentials go, rather than being one.
#
# The gate above cannot apply to these or nothing could ever get through it, and
# `SECRETS_PROVIDER` in particular must never be written into the store it
# names: that is circular, and a town whose store became unreadable would have
# no way to find out which store it was.
_STORE_CHOICE_KEYS = {"SECRETS_PROVIDER"}


async def _persist_secret(db: AsyncSession, key_name: str, value: str) -> bool:
    """Write a secret to the configured store and keep an encrypted DB copy.

    Returns whether the external store took it. That return value matters: when
    the store is not reachable yet, set_secret returns False and logs at DEBUG,
    which nothing raises the level for -- so the secret quietly lived only in
    the database and the town had no way to know. It is a real ordering trap,
    because the credentials that make Secret Manager reachable are themselves
    entered on this page: anything saved before them lands in the database and
    stays there until somebody happens to run the migration.

    The caller surfaces this rather than swallowing it.
    """
    from app.core.encryption import encrypt
    from app.core.managed import reject_platform_key_writes
    from app.services.secret_manager import set_secret, clear_cache
    reject_platform_key_writes(key_name)
    # Surrounding whitespace is never part of a credential, and a paste that
    # picks up a leading space is invisible in a password box.
    #
    # This is not hypothetical: SMTP_USER on this deployment was stored as
    # " a475c9001@smtp-brevo.com" and the relay answered 535 Authentication
    # failed, so no resident email was going out. Nothing caught it. The
    # advisory in credential_checks strips before it looks for spaces, so it
    # only ever sees the ones in the middle; `_configured_map` strips before
    # deciding a value is present, so the badge was green; and the value handed
    # to the vendor was the only unstripped one in the chain.
    #
    # Stripping here rather than at each reader, because a value that differs
    # depending on who asks is the thing that made this hard to see.
    value = (value or "").strip()
    # Deliberately database-only: these are what makes the secret store
    # reachable in the first place, or say which store it is, so putting them in
    # it is circular. `SECRETS_PROVIDER` was going through the normal path and
    # relying on DB_REQUIRED_KEYS to keep its database copy from being scrubbed
    # -- which worked, and still wrote the name of the store into the store.
    bootstrap_keys = {"GCP_SERVICE_ACCOUNT_JSON", "GOOGLE_CLOUD_PROJECT"} | _STORE_CHOICE_KEYS
    stored_externally = key_name in bootstrap_keys
    if value and key_name not in bootstrap_keys:
        try:
            if await set_secret(key_name, value):
                stored_externally = True
                # Only the bundle this key lives in. Dropping all of them made a
                # single save refetch every other capability's credentials too.
                clear_cache(key_name=key_name)
        except Exception as e:
            from app.core.sanitize import sanitize_for_log
            logger.warning(f"Provider secret store write failed for {sanitize_for_log(key_name)}: {sanitize_for_log(str(e))}")
    # Some settings are read once per process and then held. Saving one has to
    # reach the process, or the card reports a change the running system is not
    # making -- which is the shape of every bug in this pass.
    _invalidate_process_caches(key_name)
    result = await db.execute(select(SystemSecret).where(SystemSecret.key_name == key_name))
    secret = result.scalar_one_or_none()
    enc = encrypt(value) if value else None
    if secret:
        secret.key_value = enc
        secret.is_configured = bool(value)
    else:
        db.add(SystemSecret(key_name=key_name, key_value=enc, is_configured=bool(value)))
    await db.commit()
    return stored_externally


class ProviderSaveRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    settings: Dict[str, str] = {}


def _capability_catalog(capability: str) -> Dict:
    if capability == "ai":
        from app.services.ai.registry import AI_CATALOG
        return AI_CATALOG
    if capability == "translation":
        from app.services.translation_providers import TRANSLATION_CATALOG
        return TRANSLATION_CATALOG
    if capability == "identity":
        from app.services.identity import IDENTITY_CATALOG
        return IDENTITY_CATALOG
    if capability == "maps":
        from app.services.map_provider import MAP_CATALOG
        return MAP_CATALOG
    if capability in ("email", "sms", "kms", "redaction", "secrets"):
        from app.services.delivery_providers import _CATALOGS
        return _CATALOGS[capability]
    return {}


@router.post("/providers/{capability}/save")
async def save_provider(
    capability: str,
    body: ProviderSaveRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Select a provider for a capability and save its settings/secrets.
    Blank values are ignored (existing secret kept)."""
    if capability not in _PROVIDER_SELECT_KEY:
        raise HTTPException(status_code=400, detail=f"Unknown capability: {capability}")
    if capability in _READ_ONLY_SELECTION:
        raise HTTPException(status_code=400, detail=(
            "The secret store cannot be switched from a card: every credential this town has "
            "is in the current one, and repointing the setting does not move them. Set up the "
            "new store's credentials first, let the hourly migration copy them across, and "
            "change the store after that."
        ))
    # Before anything is written, and before the provider selection too: a save
    # that recorded "we have decided on Twilio" and then refused the account SID
    # would leave the card describing a decision with no credentials behind it.
    if body.settings:
        _require_a_secret_store()
    catalog = _capability_catalog(capability)
    provider_id = (body.provider or "").strip().lower()
    if provider_id not in catalog:
        raise HTTPException(status_code=400, detail=f"Unknown {capability} provider: {body.provider}")
    # Only the credential keys this provider's catalog declares may be written
    # through this endpoint — it must not become an arbitrary secret writer.
    allowed_keys = {f["key"] for f in catalog[provider_id].get("credential_fields", [])}
    unknown = [k for k in (body.settings or {}) if k not in allowed_keys]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unexpected settings for {provider_id}: {', '.join(sorted(unknown))}")
    if capability == "ai" and body.model:
        # Validate against curated ∪ live-discovered models. A model the provider
        # actually offers (from the discovery cache) is accepted even if it isn't
        # in the curated list — that's the whole point of live discovery. Only
        # reject when we can positively prove the id is offered nowhere.
        allowed_models = {m["id"] for m in catalog[provider_id].get("models", [])}
        try:
            from app.services.ai import model_discovery as md
            cache = await md.load_db_cache(db)
            entry = cache.get(provider_id) or {}
            allowed_models |= {m["id"] for m in entry.get("models", []) if m.get("id")}
        except Exception:
            # Discovery is an *additional* source of valid model ids. If the
            # cache can't be read, fall back to validating against the curated
            # list alone rather than rejecting the save.
            pass
        if allowed_models and body.model not in allowed_models:
            raise HTTPException(status_code=400, detail=f"Unknown model for {provider_id}: {body.model}")
    await _persist_secret(db, _PROVIDER_SELECT_KEY[capability], provider_id)
    if capability == "ai" and body.model:
        await _persist_secret(db, "AI_MODEL", body.model)
    # Track where each credential actually landed. A False here is not an
    # error -- the encrypted database is a supported store -- but it is
    # something the town has to be told, because the usual cause is saving
    # this card before the credentials that make the secret store reachable,
    # and the fix (enter those, then re-save) is only obvious if you know.
    db_only: List[str] = []
    for key, value in (body.settings or {}).items():
        if value:  # blank = keep existing
            if not await _persist_secret(db, key, value):
                db_only.append(key)
    # No clear_cache() here: _persist_secret already dropped the bundle for each
    # key it wrote, and the provider-selection key above. A blanket clear would
    # undo that targeting and refetch every other capability's credentials.
    # The backstop middleware would record "Updated the SMS provider settings",
    # which says which capability but not which provider was chosen or which
    # credentials changed. Key *names* only -- the values are the secrets this
    # endpoint exists to protect, and this table is exported.
    from app.services.admin_audit import record_admin_action
    await record_admin_action(
        db, event_type="provider_saved", actor=_,
        details={
            "capability": capability,
            "provider": provider_id,
            **({"model": body.model} if capability == "ai" and body.model else {}),
            # Named "settings", not "credentials": `safe_details` redacts any
            # key containing that word, and this is a list of field names.
            "settings_updated": sorted(k for k, v in (body.settings or {}).items() if v),
        },
    )
    # Shape findings are advisory and never block: a rule is a heuristic about
    # someone else's format, and refusing a credential that would have worked is
    # a worse failure than accepting one that will not -- the second is
    # discoverable, the first is a dead end.
    from app.services.credential_checks import inspect_settings
    findings = inspect_settings(body.settings)
    warnings = [{"key": f.key, "severity": f.severity, "message": f.message} for f in findings]
    if db_only:
        from app.services.secret_manager import _secrets_provider
        warnings.append({
            "key": db_only[0],
            "severity": "info",
            "message": (
                f"Saved and encrypted in the database. "
                f"{ {'azure': 'Azure Key Vault', 'aws': 'AWS Secrets Manager'}.get(_secrets_provider(), 'Google Secret Manager') }"
                " is not reachable yet — once you finish the cloud credentials above,"
                " this moves across on its own. Nothing further to do here."
            ),
        })
    else:
        # The store took everything, which means it is reachable -- and this may
        # be the moment it became reachable, if what was just saved were the
        # cloud credentials themselves. Sweep anything entered earlier across
        # now rather than leaving it for the hourly pass. Scheduled after the
        # response so a slow store cannot make Save feel broken.
        from app.services.storage_maintenance import vault_secrets as _vault
        background.add_task(_vault)

    if capability == "kms":
        # Saving a key service is the moment resident records wrapped with the
        # old key become stale, and "re-encrypted overnight" is a long time to
        # show an admin who just pressed Save. The worker task runs the same
        # bounded batches the nightly pass does; if the queue is down, the
        # nightly pass still covers it, so a failure here is only logged.
        try:
            from app.core.celery_app import celery_app as _celery
            _celery.send_task("app.tasks.storage.rewrap_pii")
        except Exception:
            logger.debug("[Save] could not queue the PII re-wrap; the nightly pass will run it")
    # Whether this provider can actually be used, and what is missing if not.
    #
    # `settings: {}` means "keep what is stored", which is right when a town is
    # changing a model on a provider whose credentials already live in the
    # vault. It also means selecting Twilio with nothing entered saves
    # successfully and answers ok:true -- and the card then said "Set up" about
    # a service with no account SID.
    #
    # Not a 400. Choosing a provider before the credentials arrive is a real
    # state -- "we have decided on Twilio, the account is still being approved"
    # -- and the setup guide is deliberately stepwise. Refusing the save would
    # make the decision unrecordable and would 400 every later save whose
    # credentials are already in an external store.
    #
    # So the save stands and the answer stops overstating it. The caller gets
    # the same reading of "configured" the badge uses, from the same function,
    # rather than inferring it from a 200.
    spec = dict(catalog[provider_id], provider=provider_id)
    configured_now = (await _configured_map([spec])).get(provider_id, False)
    missing: List[str] = []
    if not configured_now:
        from app.services.secret_manager import get_secret

        skippable = await _skippable_keys()
        labels = {
            f["key"]: (f.get("label") or f["key"])
            for f in catalog[provider_id].get("credential_fields", [])
        }

        async def stored(key: str) -> bool:
            try:
                return bool((await get_secret(key) or "").strip())
            except Exception:
                return False

        for field in catalog[provider_id].get("credential_fields", []):
            # An attached cloud identity supplies these, and the form says so.
            # Naming them here would ask for a value the page has just told the
            # clerk not to enter.
            if not _field_required(field) or field["key"] in skippable:
                continue
            if not await stored(field["key"]):
                missing.append(labels[field["key"]])

        # Named with where to go, because there is no box for them on this card.
        # "Google service account" alone would be a dead end.
        for borrowed in _borrowed_requirements(catalog[provider_id]):
            if borrowed["key"] in skippable or await stored(borrowed["key"]):
                continue
            label = borrowed.get("label") or borrowed["key"]
            where = borrowed.get("where")
            missing.append(f"{label} (on {where})" if where else label)

        # Alternatives read as one clause, not as every box in every branch:
        # listing four Azure fields when any two of them would do reads as
        # "fill in all four".
        groups = [
            [k for k in group if k not in skippable]
            for group in _requirement_groups(catalog[provider_id])
        ]
        if groups:
            phrases = []
            for group in groups:
                if all([await stored(k) for k in group]):
                    phrases = []
                    break
                phrases.append(" + ".join(labels.get(k, k) for k in group))
            if phrases:
                missing.append("either " + ", or ".join(phrases))

    return {
        "ok": True,
        # Saved is not the same as ready. The toast reads one of these.
        "configured": configured_now,
        "missing": missing,
        "provider": provider_id,
        "warnings": warnings,
    }


# ---- live tests for the capabilities that had none ---------------------------
#
# `test_provider` validated eight capabilities and could test three. Pressing
# Save & Test on maps, email, text messages, encryption or photo redaction
# returned "A live test is not available for this capability" -- a button whose
# whole job is to tell you whether something works, telling five of eight cards
# that it cannot.
#
# Two of the five turned out to be the most valuable tests on the page, because
# they check the two things that fail without saying so: which key is actually
# encrypting resident data, and whether the photo detector can answer at all.
#
# The rest need a real network call. Where one exists that does not send
# anything to a resident, it is made. Where it does not -- a generic HTTP SMS
# gateway cannot be exercised without sending a text -- the response says so and
# is deliberately NOT recorded as a connector failure, because "we cannot check
# this from here" is not the same as "this is broken", and a red badge that
# never goes green teaches people to ignore badges.


def _steps(*lines: str) -> str:
    """A test's work, shown step by step.

    One numbered line per thing the test actually did, each carrying what came
    back -- the translated word, the resolved coordinates, the byte count of
    the wrapped key. The result box renders newlines, so this is the difference
    between "Wrapped a test key" (a claim) and a log an admin can verify.
    """
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


async def _test_ai(db) -> dict:
    from app.services.ai import get_ai_provider
    provider = await get_ai_provider(db)
    if not provider:
        return {"ok": False, "detail": "No AI provider is configured. Enter the required fields and save first."}
    result = await provider.complete_json('Reply with {"priority_score": 5}. This is a connection test.')
    # Reachability and auth, not whether a trivial prompt produced parseable
    # JSON. Providers set `_reachable` when the API answered at all.
    reachable = isinstance(result, dict) and ("_error" not in result or bool(result.get("_reachable")))
    if reachable:
        # The model's own reply, shown. "Reachable and authenticated" is a
        # claim; the actual answer to the actual prompt is evidence, and it is
        # what lets an admin verify the test did what it says it did.
        import json as _json
        shown = {k: v for k, v in result.items() if not str(k).startswith("_")}
        reply = _json.dumps(shown)[:160] if shown else "an empty JSON object"
        return {"ok": True, "detail": _steps(
            f'Sent {provider.provider}/{provider.model} a live prompt: '
            f'Reply with {{"priority_score": 5}}. This is a connection test.',
            f"It answered with {reply} — a real completion from the model you picked.",
        )}
    return {"ok": False, "detail": f"Call failed: {result.get('_error', 'unknown')[:200]}"}


async def _test_translation(db) -> dict:
    from app.services.translation_providers import get_translation_provider
    provider = await get_translation_provider()
    if not provider:
        return {"ok": False, "detail": "No translation provider is configured."}
    out = await provider.translate(["hello"], "en", "es")
    # The translation itself, not a summary of it. "Sample translated" is a
    # claim; "hello came back as hola" is something an admin can check.
    return {"ok": bool(out),
            "detail": _steps(
                'Sent the word "hello" to the live API, English → Spanish.',
                f'It came back as "{out[0]}".',
            ) if out else "No translation returned"}


async def _test_identity(db) -> dict:
    """Discovery, and then the client credentials against the token endpoint.

    Discovery alone was the whole check, and it proves nothing about this
    town's registration: `.well-known/openid-configuration` is public. A card
    could sit green with a client secret that had been rotated in the vendor
    console a month earlier, and the first anybody heard of it was a member of
    staff being bounced *after* their password was accepted -- which reads as a
    forgotten password, not as a misconfiguration.

    There is no user present, so the authorization code flow cannot be run.
    A client_credentials request can: the token endpoint has to identify the
    client before it can decide anything about the grant, so its answer
    separates "we do not know this client / that secret is wrong" from "we know
    you, and you may not do this". The second is a pass. Most towns do not
    enable client_credentials for a login app, so the refusal is the expected
    result and is deliberately not reported as a failure.
    """
    import httpx

    from app.services.identity import resolve_identity_config, get_oidc_metadata

    cfg = await resolve_identity_config(db)
    if not cfg:
        return {"ok": False, "detail": "No identity provider is configured."}
    meta = await get_oidc_metadata(cfg)
    if not meta.get("authorization_endpoint"):
        return {"ok": False, "detail": (
            f"{cfg['issuer_base']} answered, but the response is not an OIDC "
            f"discovery document — check the issuer URL.")}

    found = f"Discovered {cfg['provider']} endpoints at {cfg['issuer_base']}"
    token_endpoint = meta.get("token_endpoint")
    if not token_endpoint:
        return {"ok": True, "detail": f"{found}. It publishes no token endpoint, so the "
                                      f"client secret could not be checked from here."}

    try:
        from app.integrations.base import _assert_public_url
        _assert_public_url(token_endpoint)
        data = {
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }
        audience = (cfg.get("extra_authorize_params") or {}).get("audience")
        if audience:
            data["audience"] = audience
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(token_endpoint, data=data)
    except Exception as exc:
        # The discovery document was fetched, so the issuer is real and
        # reachable. Failing the whole card on a token-endpoint hiccup would
        # report a working sign-in as broken.
        from app.core.sanitize import sanitize_for_log
        logger.info("[Identity] token endpoint probe failed: %s", sanitize_for_log(str(exc)[:200]))
        return {"ok": True, "detail": f"{found}. The client secret could not be checked from here."}

    if resp.status_code == 200:
        return {"ok": True, "detail": f"{found}, and the client ID and secret were accepted."}

    try:
        error = (resp.json() or {}).get("error") or ""
    except Exception:
        error = ""

    # The vendor knows this client and refused the *grant*. That is the normal
    # answer for a login app, and it is only reachable once the secret has been
    # verified -- so it is the strongest evidence this check can produce.
    if error in ("unauthorized_client", "invalid_grant", "unsupported_grant_type",
                 "access_denied", "invalid_scope", "invalid_target", "invalid_request"):
        return {"ok": True, "detail": (
            f"{found}, and the client ID and secret were accepted "
            f"(the provider recognised this application and declined a "
            f"machine-to-machine token, which is expected for a sign-in app).")}

    if error == "invalid_client" or resp.status_code in (401, 403):
        return {"ok": False, "detail": (
            f"{cfg['provider']} rejected the client ID or client secret. Staff sign-in will "
            f"fail after the password is accepted. Re-copy both from the provider console.")}

    return {"ok": True, "detail": (
        f"{found}. The client secret could not be checked from here "
        f"(the token endpoint answered HTTP {resp.status_code}).")}


async def _test_kms(db=None) -> dict:
    """Wrap a throwaway key and see which service actually did it.

    The most valuable check on the page. A KMS that stops answering does not
    raise -- pii_crypto falls back to the application key and carries on -- so
    "is the key I selected the one encrypting resident data" is a question
    nothing else asks out loud. `probe_backend` rather than `active_backend`,
    because the latter reads the data key this process cached at startup and
    would answer for the world as it was then.
    """
    from app.core import pii_crypto
    from app.core.encryption import _kms_provider

    selected = _kms_provider()
    probe = pii_crypto.probe_wrap()
    actual = probe["backend"]
    if actual == selected:
        label = {"google": "Google Cloud KMS", "azure": "Azure Key Vault",
                 "aws": "AWS KMS", "local": "the application key"}.get(actual, actual)
        evidence = (f"a {probe['wrapped_len']}-byte wrapped key came back"
                    + (f" (ciphertext begins {probe['peek']}…)" if probe.get("peek") else ""))
        return {"ok": True, "detail": _steps(
            "Generated a throwaway 256-bit data key on this server.",
            f"Asked the key service to wrap it — {evidence}.",
            f"The wrapping is tagged as {label}, which is the provider you selected — "
            f"so resident data is being encrypted with the key you chose.",
            "Discarded the test key. Nothing was stored.",
        )}
    return {"ok": False, "detail": (
        f"Selected {selected}, but a test key was wrapped with {actual}. Resident "
        f"data is not being encrypted with the key you chose — check the "
        f"credentials and that the key still exists.")}


async def _test_redaction(db=None) -> dict:
    """Can the chosen detector answer, and is it the chosen one?

    A detector with no credentials returns the same empty result as a photo
    with nobody in it, so this is the only place the difference is visible.
    """
    from app.services.image_redaction import effective_provider, resolve_provider

    selected = await resolve_provider()
    if not selected:
        return {"ok": True, "detail": "Photo redaction is switched off, as configured."}
    actual, degraded_from = await effective_provider(selected)
    if degraded_from == actual:
        return {"ok": False, "detail": (
            "No detector is available, so photos would be stored without blurring. "
            "On-server detection needs no account — this usually means OpenCV is missing.")}
    if degraded_from:
        return {"ok": False, "detail": (
            f"{degraded_from} has no usable credentials, so blurring is falling back to "
            f"on-server detection. Photos are still redacted, less accurately.")}

    # Everything above only asked whether credentials are *present*, and for AWS
    # and Azure that is all `_usable` can tell -- only Google's check reaches the
    # vendor. So a key that is present and rejected passed every test on this
    # page while every resident photo went out unblurred. Send one tiny image
    # through the real detector, which is the only question worth answering here:
    # does this detector answer when we ask it to blur something?
    from app.services.image_redaction import detect

    probe = _one_pixel_probe_image()
    try:
        answered = await detect(actual, probe, 64, 64, True, True)
    except Exception as exc:                     # detect() should not raise, but this page must not 500
        return {"ok": False, "detail": f"{actual} raised while detecting: {str(exc)[:160]}"}

    if answered is None:
        return {"ok": False, "detail": (
            f"{actual} has credentials saved but rejected them when asked to scan an image, "
            f"so photos would be stored without blurring. Check the key has not expired or "
            f"been rotated, and that the region and endpoint match. The server log line "
            f"beginning \"[Redaction] {actual} could not detect\" has the vendor's own words.")}

    return {"ok": True, "detail": _steps(
        "Built a one-pixel test image on this server.",
        f"Sent it to {actual} for a live face and licence-plate scan.",
        "It answered: nothing to blur — the correct answer for this image — "
        "so the detector is accepting requests and real photos will be redacted.",
    )}


def _one_pixel_probe_image() -> bytes:
    """A tiny valid PNG, for asking a detector whether it answers at all.

    Deliberately not a photograph of anybody: the useful signal is whether the
    call succeeds, not what it finds, and sending a real face to a vendor to test
    a checkbox would be its own problem. 64x64 mid-grey, built here so the check
    needs no fixture file on disk.
    """
    import struct
    import zlib

    width = height = 64
    row = b"\x00" + b"\x80" * (width * 3)
    raw = row * height

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


async def _test_email(db=None) -> dict:
    return await _test_delivery("email")


async def _test_sms(db=None) -> dict:
    return await _test_delivery("sms")


def _unverifiable(detail: str) -> dict:
    """A result that is shown but not written to connector health."""
    return {"ok": False, "detail": detail, "recorded": False}


def _referrer_restricted(text: str) -> bool:
    """Google's way of saying "this key only works from a browser".

    A key restricted to Websites — which the setup guide tells administrators to
    do, and which is the right thing to do — cannot be exercised from a server at
    all. That is a correctly configured key, not a broken one, so it must not be
    reported as a failure.
    """
    lowered = (text or "").lower()
    return "referer" in lowered or "referrer" in lowered


async def _test_maps(db=None) -> dict:
    """Geocode a known address. Reads only, costs a fraction of a cent."""
    import httpx

    from app.services.map_provider import MAP_PROVIDER_KEY, normalize_provider
    from app.services.secret_manager import get_secret

    # A server can check that the APIs are on and billing is attached. It cannot
    # check that the key works in a resident's browser: Google enforces an HTTP
    # referrer restriction when the map initialises, not when the script is
    # fetched -- the bootstrap returns byte-identical JS for any Referer, so
    # there is nothing here to inspect.
    #
    # That check now runs where it is answerable. The setup page loads the real
    # SDK from the town's own origin and watches Google's `gm_authFailure` hook,
    # which is exactly what a resident's browser does. See
    # frontend/src/maps/browserCheck.ts. This function stopped guessing at it.

    # MAP_PROVIDER, not MAPS_PROVIDER. Nothing writes the plural, so this read
    # always missed and every town was tested as though it were on Google --
    # an Esri town pressing Test got a verdict on a Google key it does not have.
    provider = normalize_provider(await get_secret(MAP_PROVIDER_KEY))
    sample = "1600 Pennsylvania Ave NW, Washington DC"

    async with httpx.AsyncClient(timeout=12.0) as client:
        if provider == "google":
            key = await get_secret("GOOGLE_MAPS_API_KEY")
            if not key:
                return {"ok": False, "detail": "No Google Maps API key is saved."}

            r = await client.get("https://maps.googleapis.com/maps/api/geocode/json",
                                 params={"address": sample, "key": key})
            body = r.json()
            status = body.get("status")
            geocode_error = body.get("error_message", "")

            if status != "OK":
                if _referrer_restricted(geocode_error):
                    return _unverifiable(
                        "This key is restricted to your website, so it cannot be tested from "
                        "the server — which is the correct way to restrict it. Open the "
                        "resident report form and confirm the map draws and the address box "
                        "offers suggestions.")
                # Google's own words matter here: REQUEST_DENIED with "billing" is
                # the single most common failure on this page and its remedy is
                # nothing to do with the key.
                return {"ok": False, "detail": f"Google returned {status}. {geocode_error}".strip()}

            # Geocoding alone is not enough to call this working.
            #
            # The address box on the report form runs on Places API (New)
            # (places.googleapis.com), which is a *separate* product from both
            # the Geocoding API and the older "Places API" — enabling one does
            # not enable the others. Testing only Geocoding meant this page
            # showed a green tick while residents got an address box that
            # returned nothing, and there was no way to tell from here which of
            # the two APIs was missing.
            pr = await client.post(
                "https://places.googleapis.com/v1/places:autocomplete",
                headers={"Content-Type": "application/json", "X-Goog-Api-Key": key},
                json={"input": sample, "includedRegionCodes": ["us"],
                      "includedPrimaryTypes": ["geocode"]},
            )
            if pr.status_code == 200:
                # Show the round trip itself: which address was sent, and what
                # Google resolved it to. Evidence an admin can verify beats a
                # sentence asking to be believed.
                first = (body.get("results") or [{}])[0]
                where = first.get("formatted_address") or sample
                loc = first.get("geometry", {}).get("location", {})
                coords = (f" at ({loc.get('lat'):.4f}, {loc.get('lng'):.4f})"
                          if loc.get("lat") is not None else "")
                suggestions = len((pr.json() or {}).get("suggestions", []) or [])
                return {"ok": True, "detail": _steps(
                    f'Sent the test address "{sample}" to the Geocoding API.',
                    f"Google resolved it to {where}{coords}.",
                    f"Asked Places API (New) to autocomplete the same address — "
                    f"it returned {suggestions or 'live'} suggestions.",
                    "So both APIs are enabled and billing is attached. The map in a "
                    "resident's browser is checked separately, on this page.",
                )}

            places_error = ""
            try:
                places_error = pr.json().get("error", {}).get("message", "")
            except Exception:
                places_error = pr.text[:200]

            if _referrer_restricted(places_error):
                return _unverifiable(
                    "Geocoding works. Address autocomplete could not be tested because the "
                    "key is restricted to your website, which is the correct way to restrict "
                    "it. Open the resident report form and confirm the address box offers "
                    "suggestions as you type.")

            return {"ok": False, "detail": (
                "Geocoding works, but Places API (New) rejected the key, so the address "
                "box on the report form will offer no suggestions. In Google Cloud enable "
                "\"Places API (New)\" — it is a separate API from both \"Geocoding API\" "
                "and the older \"Places API\", and if the key is restricted to a list of "
                f"APIs it must be ticked there too. Google said: {places_error}").strip()}

        if provider == "azure":
            key = await get_secret("AZURE_MAPS_KEY")
            if not key:
                return {"ok": False, "detail": "No Azure Maps key is saved."}
            r = await client.get("https://atlas.microsoft.com/search/address/json",
                                 params={"api-version": "1.0", "subscription-key": key, "query": sample})
            if r.status_code == 200:
                try:
                    pos = (r.json().get("results") or [{}])[0].get("position", {})
                    coords = (f" → ({pos.get('lat'):.4f}, {pos.get('lon'):.4f})"
                              if pos.get("lat") is not None else "")
                except Exception:
                    coords = ""
                return {"ok": True, "detail": (
                    f'Geocoded the test address "{sample}" live{coords} — the key is accepted.')}
            return {"ok": False, "detail": f"Azure Maps returned HTTP {r.status_code}."}

        if provider == "esri":
            key = await get_secret("ARCGIS_API_KEY")
            if not key:
                return {"ok": False, "detail": "No ArcGIS API key is saved."}
            locator = (await get_secret("ARCGIS_LOCATOR_URL")) or (
                "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer")
            r = await client.get(f"{locator.rstrip('/')}/findAddressCandidates",
                                 params={"SingleLine": sample, "f": "json", "token": key})
            body = r.json() if r.status_code == 200 else {}
            if "error" in body:
                return {"ok": False, "detail": f"ArcGIS: {body['error'].get('message', 'rejected the key')}"}
            if r.status_code == 200:
                first = (body.get("candidates") or [{}])[0]
                found = first.get("address")
                return {"ok": True, "detail": (
                    f'Geocoded the test address "{sample}" live — the locator answered'
                    + (f' with "{found}"' if found else "") + "; the key is accepted.")}
            return {"ok": False, "detail": f"ArcGIS returned HTTP {r.status_code}."}

        if provider == "apple":
            return _unverifiable(
                "Apple Maps credentials can only be checked by signing a token in the "
                "browser, so there is nothing to test from here. Open the resident "
                "portal and confirm the map draws.")

    return _unverifiable(f"No live test is available for {provider}.")


async def _test_delivery(capability: str) -> dict:
    """Email and text messages, without sending anything to a resident."""
    import httpx

    from app.services.delivery_providers import (
        EMAIL_CATALOG, SMS_CATALOG, describe_missing, required_keys,
    )
    from app.services.secret_manager import get_secret

    async def _nothing_saved(catalog: dict, provider: str) -> Optional[dict]:
        """"You have not filled this in" beats any statement about the vendor.

        The fallthroughs at the ends of both halves of this function reported on
        the *provider* -- "there is no way to check http without sending a real
        text message", "ACS has no check that avoids sending a real message" --
        without first asking whether the town had entered anything. A clerk who
        had never touched text messages was told their gateway was untestable,
        which is true of the gateway and irrelevant to them: what they needed to
        know is that the boxes are empty.
        """
        entry = catalog.get(provider)
        missing = [k for k in required_keys(entry) if not (await get_secret(k) or "").strip()]
        if not missing:
            return None
        # `configured: False` rather than only `recorded: False`.
        #
        # These are different facts and the frontend was collapsing them: it
        # read "not recorded" as "this provider cannot be tested", which is
        # true of an HTTP gateway in general and wrong about a town that has
        # entered nothing. Saying which it is lets the card correct itself even
        # when the catalog disagrees.
        return {
            "ok": False,
            "detail": describe_missing(entry, missing),
            "recorded": False,
            "configured": False,
        }

    if capability == "email":
        provider = (await get_secret("EMAIL_PROVIDER")) or "smtp"
        blank = await _nothing_saved(EMAIL_CATALOG, provider)
        if blank:
            return blank
        if provider == "smtp":
            import asyncio
            import smtplib
            host = await get_secret("SMTP_HOST")
            user = await get_secret("SMTP_USER")
            password = await get_secret("SMTP_PASSWORD")
            if not host:
                return {"ok": False, "detail": "No SMTP host is saved."}
            port = int((await get_secret("SMTP_PORT")) or 587)

            sender = ((await get_secret("SMTP_FROM_EMAIL")) or "").strip()

            def _connect():
                """Connect, authenticate, and offer the envelope. No DATA, so
                no message is queued and this is safe to press repeatedly.

                The envelope is the half that was missing. Signing in proves the
                relay knows this account; it does not prove the relay will carry
                mail *from this address*, and that is a separate permission on
                every hosted relay -- a verified sender on Brevo, an authorised
                domain on Mailgun, a verified identity on SES. A town that
                switches relay keeps its From address and loses the
                authorisation attached to it, which is precisely the moment this
                button gets pressed.

                Relays differ on when they enforce it: some refuse at MAIL FROM,
                some at DATA. This catches the first kind and says so; the
                second cannot be caught without sending, and the message does
                not pretend otherwise.
                """
                if port == 465:
                    server = smtplib.SMTP_SSL(host, port, timeout=12)
                else:
                    server = smtplib.SMTP(host, port, timeout=12)
                    server.starttls()
                with server:
                    if user and password:
                        server.login(user, password)
                    if not sender:
                        return None
                    try:
                        code, message = server.mail(sender)
                        if code >= 400:
                            return (code, message)
                        code, message = server.rcpt(sender)
                        if code >= 400:
                            return (code, message)
                    finally:
                        # Abandon the envelope explicitly rather than relying on
                        # the disconnect, so nothing is left half-stated on a
                        # relay that counts attempts.
                        try:
                            server.rset()
                        except Exception:
                            pass
                return None

            refusal = await asyncio.get_event_loop().run_in_executor(None, _connect)
            if refusal:
                code, message = refusal
                said = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
                return {"ok": False, "detail": (
                    f"Signed in to {host}, and it will not carry mail from {sender}: "
                    f"{code} {said.strip()[:200]}. That address has to be a verified sender on "
                    f"this relay — the sign-in and the permission to send as an address are two "
                    f"different things, and switching relay keeps the address and loses the "
                    f"permission."
                )}
            # Only claim the sign-in that actually happened. Without a username
            # and password this opens a socket and negotiates TLS, which proves
            # the host is reachable and nothing about whether it will accept
            # mail from us -- and the message said "signed in" either way.
            if user and password:
                envelope = f" It accepts mail from {sender}." if sender else ""
                return {"ok": True, "detail": (
                    f"Connected to {host}:{port} and signed in.{envelope} Nothing was sent.")}
            return {"ok": True, "detail": (
                f"Connected to {host}:{port}. No username and password are saved, so nothing "
                f"was signed in to — the server is reachable, but whether it will relay for "
                f"this town is untested.")}

        if provider == "ses":
            import asyncio
            from app.services.cloud_moderation import _aws_kwargs
            kwargs = await _aws_kwargs()
            if not kwargs:
                return {"ok": False, "detail": "No AWS region or credentials are saved."}

            def _quota():
                import boto3
                return boto3.client("ses", **kwargs).get_send_quota()

            quota = await asyncio.get_event_loop().run_in_executor(None, _quota)
            sent, cap = quota.get("SentLast24Hours", 0), quota.get("Max24HourSend", 0)
            if cap and cap <= 200:
                # The sandbox cap. Everything looks fine and residents receive
                # nothing, so it is worth saying rather than passing green.
                return {"ok": False, "detail": (
                    f"Credentials work, but the 24-hour cap is {int(cap)} — this account is still "
                    f"in the SES sandbox and will not deliver to unverified addresses. "
                    f"Request production access.")}
            return {"ok": True, "detail": f"SES reachable. {int(sent)} of {int(cap)} sent in the last 24 hours."}

        return _unverifiable(
            "Azure Communication Services has no check that avoids sending a real "
            "message. Save, then send yourself a test from a request.")

    # The "texting is switched off" branch that used to be here has moved up to
    # `test_provider`, which is the only caller and now answers it for all eight
    # capabilities rather than for the one that happened to have a flag. This
    # function is what its docstring says again: does this provider work.
    provider = (await get_secret("SMS_PROVIDER")) or "none"
    if provider == "none":
        return {"ok": True, "detail": "Text messages are switched off, as configured."}

    blank = await _nothing_saved(SMS_CATALOG, provider)
    if blank:
        return blank

    if provider == "twilio":
        sid = await get_secret("TWILIO_ACCOUNT_SID")
        token = await get_secret("TWILIO_AUTH_TOKEN")
        if not (sid and token):
            return {"ok": False, "detail": "Account SID or auth token is missing."}
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                                 auth=(sid, token))
        if r.status_code == 200:
            status = r.json().get("status", "active")
            if status != "active":
                return {"ok": False, "detail": f"Credentials work, but the Twilio account is {status}."}
            return {"ok": True, "detail": "Twilio credentials accepted. Nothing was sent."}
        if r.status_code == 401:
            return {"ok": False, "detail": "Twilio rejected the Account SID or auth token."}
        return {"ok": False, "detail": f"Twilio returned HTTP {r.status_code}."}

    if provider == "sns":
        import asyncio
        from app.services.cloud_moderation import _aws_kwargs
        kwargs = await _aws_kwargs()
        if not kwargs:
            return {"ok": False, "detail": "No AWS region or credentials are saved."}

        def _attrs():
            import boto3
            return boto3.client("sns", **kwargs).get_sms_attributes()

        await asyncio.get_event_loop().run_in_executor(None, _attrs)
        return {"ok": True, "detail": "SNS reachable and authenticated. Nothing was sent."}

    if provider == "acs":
        # ACS has no read-only SMS call, but the phone-number list on the same
        # resource takes the same HMAC key -- so this authenticates the access
        # key AND answers the question that actually breaks sends: is the number
        # in the From box one this resource owns? Nothing is sent.
        from app.services.notifications import _acs_auth_headers

        endpoint = ((await get_secret("ACS_ENDPOINT")) or "").rstrip("/")
        access_key = await get_secret("ACS_ACCESS_KEY")
        from_number = ((await get_secret("SMS_FROM_NUMBER")) or "").strip()
        url = f"{endpoint}/phoneNumbers?api-version=2022-12-01"
        try:
            headers = _acs_auth_headers("GET", url, b"", access_key)
        except Exception:
            return {"ok": False, "detail": (
                "The ACS access key is not valid base64, so it cannot sign a request. "
                "Copy it again from the Keys page of the Communication Services resource."
            )}
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url, headers=headers)

        if r.status_code in (401, 403):
            return {"ok": False, "detail": "Azure rejected the ACS endpoint or access key."}
        if not r.is_success:
            return {"ok": False, "detail": f"Azure Communication Services returned HTTP {r.status_code}."}

        try:
            owned = [n.get("phoneNumber") for n in (r.json() or {}).get("value", [])]
        except Exception:
            owned = []
        if from_number and owned and from_number not in owned:
            return {"ok": False, "detail": (
                f"The access key works, but {from_number} is not one of the numbers this "
                f"Communication Services resource owns. Sends will be rejected. Numbers on "
                f"this resource: {', '.join(n for n in owned if n) or 'none'}."
            )}
        if not owned:
            return {"ok": False, "detail": (
                "The access key works, but this Communication Services resource owns no phone "
                "numbers, so it cannot send. Buy a number in the Azure portal first."
            )}
        return {"ok": True, "detail": (
            f"Azure Communication Services accepted the key and owns {from_number or owned[0]}. "
            f"Nothing was sent."
        )}

    if provider == "http":
        api_url = ((await get_secret("SMS_HTTP_API_URL")) or "").strip()
        api_key = ((await get_secret("SMS_HTTP_API_KEY")) or "").strip()
        test_url = ((await get_secret("SMS_HTTP_TEST_URL")) or "").strip()

        # Textbelt is the one generic gateway this code already knows by name
        # -- GenericHTTPSMSProvider branches on it -- and it publishes a quota
        # endpoint. Knowing the vendor and then reporting it untestable would be
        # a choice, not a limitation.
        if not test_url and "textbelt" in api_url.lower() and api_key:
            test_url = f"https://textbelt.com/quota/{api_key}"

        if not test_url:
            return _unverifiable(
                "This gateway has not been given a status URL, and a generic HTTP gateway has no "
                "standard one — so the only way to exercise it is to send a real text. Add a "
                "status URL above if your gateway publishes one, or send yourself a message from "
                "a request to confirm delivery.")

        try:
            from app.integrations.base import _assert_public_url
            _assert_public_url(test_url)
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(test_url)
        except Exception as exc:
            return {"ok": False, "detail": f"The status URL could not be reached: {str(exc)[:200]}"}

        if r.status_code in (401, 403):
            return {"ok": False, "detail": "The gateway rejected the API key."}
        if not r.is_success:
            return {"ok": False, "detail": f"The status URL answered HTTP {r.status_code}."}

        # Textbelt answers {"success": true, "quotaRemaining": n}. A remaining
        # quota of zero is a key that authenticates and cannot send, which is
        # the failure a plain 200 would have called healthy.
        try:
            body = r.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            if body.get("success") is False:
                return {"ok": False, "detail": (
                    f"The gateway rejected the API key: {body.get('error') or 'no reason given'}.")}
            quota = body.get("quotaRemaining")
            if isinstance(quota, int):
                if quota <= 0:
                    return {"ok": False, "detail": (
                        "The API key is valid and has no messages left on it, so nothing will "
                        "be delivered. Top it up with your gateway.")}
                return {"ok": True, "detail": (
                    f"The gateway accepted the API key. {quota} messages remaining. "
                    f"Nothing was sent.")}
        return {"ok": True, "detail": "The gateway accepted the API key. Nothing was sent."}

    return _unverifiable(
        f"There is no way to check {provider} without sending a real text message. "
        f"Save, then send yourself one from a request to confirm delivery.")


async def _test_secrets(db=None) -> dict:
    """Write a throwaway key, read it back, and take it away again.

    The one capability with no check at all, and the one whose failure is least
    visible. Everything else on this page depends on it: a credential is saved
    through `set_secret`, and when that returns False the value quietly stays in
    the encrypted database and the card still shows a tick. The only signal was
    a DEBUG line.

    The existing signal, `store_reachable()`, asks the credential store whether
    it has credentials -- `_is_gcp_available()` on Google. That is the shape of
    check this pass exists to replace: it answers yes for a service account
    whose Secret Manager permission was revoked last week, because the service
    account is still there.

    So this does the round trip the rest of the system does, in order: write,
    read back, compare, delete. Each stage fails differently and the message
    says which, because "the secret store is broken" and "the secret store
    accepts writes and serves stale reads" have completely different remedies.

    The probe key is named for what it is and is deleted whichever way the check
    goes. An earlier hand-run probe left a `test-write-check` secret behind in
    Google, and a self-test that litters is one nobody runs twice.
    """
    import uuid

    from app.services.secret_manager import (
        _secrets_provider, clear_cache, delete_secret, get_secret, set_secret,
    )

    provider = _secrets_provider()
    label = {"azure": "Azure Key Vault", "aws": "AWS Secrets Manager"}.get(
        provider, "Google Secret Manager")

    # Named so anybody who finds it in a console knows what it is and that it is
    # safe to remove, and unique per run so two checks at once cannot read each
    # other's value and both pass.
    key = "PINPOINT_SELFTEST_WRITE_CHECK"
    expected = f"pinpoint-selftest-{uuid.uuid4().hex}"

    async def _cleanup() -> bool:
        try:
            gone = await delete_secret(key)
        except Exception:
            gone = False
        clear_cache(key_name=key)
        return gone

    try:
        wrote = await set_secret(key, expected)
    except Exception as e:
        await _cleanup()
        return {"ok": False, "detail": f"{label} refused a test write: {str(e)[:200]}"}

    if not wrote:
        await _cleanup()
        # Not a failure of the town's doing when no store is configured: the
        # encrypted database is a supported place for secrets to live.
        from app.services.storage_maintenance import store_reachable
        if not store_reachable():
            return _unverifiable(
                f"{label} is not configured, so there is nothing to check. Credentials are "
                f"held encrypted in the database, which is supported — they move across on "
                f"their own once a store is set up.")
        return {"ok": False, "detail": (
            f"{label} is configured but would not accept a test write. Credentials saved on "
            f"this page are being kept in the encrypted database instead.")}

    # Straight past the cache. Reading our own write out of memory would pass
    # on a store that never received it, which is the exact failure.
    clear_cache(key_name=key)
    try:
        got = await get_secret(key)
    except Exception as e:
        await _cleanup()
        return {"ok": False, "detail": f"{label} took the write but the read back failed: {str(e)[:200]}"}

    if got != expected:
        await _cleanup()
        if got is None:
            return {"ok": False, "detail": (
                f"{label} accepted a test write and then had nothing to return. Credentials "
                f"saved here may not be readable by the workers that need them.")}
        return {"ok": False, "detail": (
            f"{label} returned a different value than the one just written to it. "
            f"Something else is writing the same key, or reads are being served stale.")}

    removed = await _cleanup()
    if not removed:
        # The check passed. Say the part that needs a human anyway.
        return {"ok": True, "detail": (
            f"Wrote a test key to {label}, read it back, and could not remove it again. "
            f"Delete {key} by hand — nothing depends on it.")}
    return {"ok": True, "detail": _steps(
        f"Wrote a secret named {key} with the random value {expected} to {label}.",
        "Cleared the cache, so the next read had to come from the store itself.",
        "Read it back — the store returned exactly that value.",
        "Deleted it again and confirmed it is gone.",
    )}


# One table, so the set of capabilities the endpoint accepts and the set it can
# actually test cannot drift apart. They did: the accept-list was widened when
# maps, email, SMS, encryption and redaction got catalogs, and five of eight
# cards then answered "a live test is not available for this capability" -- a
# button whose whole job is to say whether something works, saying it could not.
_CAPABILITY_TESTS = {
    "ai": _test_ai,
    "translation": _test_translation,
    "identity": _test_identity,
    "maps": lambda db=None: _test_maps(db),
    "email": _test_email,
    "sms": _test_sms,
    "kms": _test_kms,
    "redaction": _test_redaction,
    # The one everything else on this page depends on, and the last to get a
    # check of its own.
    "secrets": _test_secrets,
}


@router.post("/providers/{capability}/test")
@_cost_limiter.limit("10/minute")  # live paid-API call — bound the cost
async def test_provider(
    request: Request,
    capability: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Live connectivity check for the currently-configured provider of a
    capability. Returns {ok, detail}.

    The outcome is recorded, so a test is not just a moment on screen. That
    matters in the failing direction especially: an admin who tests, sees red
    and walks away leaves a record the setup page can keep showing, instead of
    a green badge that only means credentials exist.
    """
    from app.services import connector_health

    # Validate before anything else, the way save_provider does. Without this,
    # `capability` is an unvalidated path segment that reaches
    # connector_health._row(), which creates a row for whatever name it is
    # given -- so any admin request could insert arbitrary junk rows into a
    # table the setup page renders. It also kept the raw segment out of the
    # 400 body below.
    if capability not in _PROVIDER_SELECT_KEY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown capability. Expected one of: {', '.join(sorted(_PROVIDER_SELECT_KEY))}.",
        )

    # Which provider this verdict is about, captured before the check runs.
    #
    # The column existed and only the circuit breaker ever filled it, so every
    # row written by this button had provider NULL -- and a stored verdict with
    # no provider against it is a verdict about whatever was selected at the
    # time, which nothing records and nobody can reconstruct. Live: the SMS card
    # was showing "There is no way to check http without sending a real text"
    # while SMS_PROVIDER read 'acs'. Had the old provider passed instead, the
    # card would have shown green for a provider the town no longer uses.
    tested_provider = await effective_provider_for(capability)

    async def _remember(outcome: dict) -> dict:
        try:
            # A check that failed part-way may have left the session in a
            # failed transaction -- several of them run queries and swallow
            # their own errors. Any statement after that raises
            # PendingRollbackError, so the write below was caught by its own
            # except and lost, and the card went on saying "not checked yet"
            # immediately after somebody watched the test run.
            try:
                await db.rollback()
            except Exception:
                pass

            if outcome.get("ok"):
                # The message too, not just the timestamp. "Checked 6 hours
                # ago" cannot say what it found.
                await connector_health.record_success(
                    db, capability, provider=tested_provider, detail=outcome.get("detail"))
            else:
                await connector_health.record_failure(
                    db, capability, outcome.get("detail", ""), provider=tested_provider)
        except Exception:
            # Bookkeeping must never turn a passing test into a failing one.
            pass
        return outcome

    # A capability the town switched off is not tested, and the result is not
    # recorded either way.
    #
    # Before the switch existed there was nothing to check here, and the two
    # capabilities that did have an off flag checked it inside their own test
    # function -- so the other six would happily make a live paid API call
    # against an integration nobody uses, and write a red badge when it failed.
    # A failure on something deliberately switched off is the noise that teaches
    # people to ignore badges.
    from app.services import capability_switches

    if not await capability_switches.enabled(capability):
        return {
            "ok": True,
            "detail": (
                "This is switched off, so nothing runs through it. Its credentials "
                "are still saved — switch it back on in Setup Instructions and they "
                "will be used as they were."
            ),
            # Neither a pass nor a fail: there was no check. `configured: False`
            # keeps `_remember` and the unverifiable path from writing anything.
            "recorded": False,
            "configured": False,
            "off": True,
        }

    check = _CAPABILITY_TESTS.get(capability)
    if check is None:
        # Cannot happen while the accept-list is derived from this table, and
        # a test asserts that it is. Kept as a real branch rather than an
        # assertion because a 400 is a better failure than a 500.
        raise HTTPException(status_code=400, detail="A live test is not available for this capability.")

    try:
        outcome = await check(db)
        # An outcome we could not verify is shown but not written to connector
        # health: "we cannot check this from here" is not "this is broken", and
        # a red badge that can never go green teaches people to ignore badges.
        if outcome.get("recorded") is False:
            # Not a failure, and not nothing. Stored as "we tried and cannot
            # tell", so the answer survives a reload instead of living only in
            # the session that produced it.
            try:
                if outcome.get("configured") is not False:
                    await connector_health.record_unverifiable(
                        db, capability, outcome.get("detail", ""), provider=tested_provider)
            except Exception:
                pass
            return outcome
        return await _remember(outcome)
    except HTTPException:
        raise
    except Exception as e:
        # The provider's own words, plus a next step when we recognise the
        # shape of the complaint. Never a replacement -- a clerk searching the
        # web for their error needs the actual string.
        from app.services.credential_checks import describe_failure
        return await _remember({"ok": False, "detail": describe_failure(str(e)[:300])})


# ---- Cloud environment profile (hybrid one-choice front door) ----
#
# The real decision a jurisdiction makes is its compliance boundary — it is
# authorized under ONE cloud (Google, or Azure Government / GCC High), so a
# single choice should set AI + translation + secret-store together. Identity is
# deliberately NOT bundled: an Azure-cloud town may still use Auth0/Okta for SSO,
# so we only *recommend* the matching IdP and switch it on explicit opt-in.
# Google Maps is fixed regardless of profile.
CLOUD_PROFILES: Dict[str, Dict[str, str]] = {
    "google": {
        "label": "Google Cloud",
        "boundary": "Google Cloud — FedRAMP High / StateRAMP",
        "ai": "vertex",
        "translation": "google",
        "secrets": "google",
        "kms": "google",
        # Google has no first-party SMS; email works via SMTP (Workspace/relay).
        "email": "smtp",
        "sms": "",
        "identity_recommended": "auth0",
    },
    "azure": {
        "label": "Microsoft Azure (Government)",
        "boundary": "Azure Government / GCC High — FedRAMP High, DoD IL4/5",
        "ai": "azure",
        "translation": "azure",
        "secrets": "azure",
        "kms": "azure",
        "email": "acs",
        "sms": "acs",
        "identity_recommended": "entra",
    },
    "aws": {
        "label": "Amazon Web Services (GovCloud)",
        "boundary": "AWS GovCloud — FedRAMP High, DoD IL4/5",
        "ai": "bedrock",
        "translation": "aws",
        "secrets": "aws",
        "kms": "aws",
        "email": "ses",
        "sms": "sns",
        "identity_recommended": "oidc",
    },
}


def _derive_cloud_profile(ai: str, translation: str, secrets: str, kms: str = None) -> str:
    """Report which named profile the current core selections match, or 'mixed'.
    Matches on the boundary-defining set (AI, translation, secret store, and KMS
    when provided); email/SMS can legitimately differ and aren't part of the match."""
    for pid, p in CLOUD_PROFILES.items():
        if ai == p["ai"] and translation == p["translation"] and secrets == p["secrets"]:
            if kms is not None and kms != p["kms"]:
                continue
            return pid
    return "mixed"


class CloudProfileRequest(BaseModel):
    profile: str
    # Opt-in: also switch the staff sign-in provider to the profile's
    # recommended IdP. Off by default because identity is a separate contract.
    apply_identity: bool = False


@router.get("/providers/status")
async def get_provider_status(_: User = Depends(get_current_admin)):
    """Which provider each capability is on, and which providers are set up.

    One request instead of eight. The setup guide's task list needs to know
    whether each item is finished before anything is opened, and the only
    honest answer is per *provider* rather than per capability.

    That distinction is the bug this exists to fix. The page was deciding from
    the stored secrets, where "maps is configured" meant any map provider's key
    was present -- so a town that had set up Google Maps and then switched to
    Esri saw a green tick against a provider with no credentials at all, and the
    guide skipped straight past the thing it most needed to ask for.

    `ready` is that judgement made once, here, rather than eight times in the
    browser. The page had been recomputing it from hard-coded secret names ORed
    across providers, which disagreed with this endpoint in both directions:
    photo redaction and PII encryption were reported as not set up while both
    were demonstrably running, and `AWS_REGION` -- shared by SES, SNS, Bedrock,
    AWS KMS and AWS Translate -- would have marked AI and translation as set up
    the moment a town configured email.
    """
    from app.core.sanitize import sanitize_for_log
    from app.services import capability_switches

    switches = await capability_switches.all_enabled()

    out: Dict[str, Any] = {}
    for capability in _PROVIDER_SELECT_KEY:
        try:

            providers = await providers_for(capability)
            current = await effective_provider_for(capability)
            configured = await _configured_map(providers)
            wanted = switches.get(capability, True)
            out[capability] = {
                "current_provider": current,
                "configured": configured,
                # The third fact, and the one the page could not previously get
                # from anywhere.
                #
                # Deliberately not folded into `current_provider` or into
                # `configured`. "Switched off" and "not set up" were
                # indistinguishable, so a town that saved an AI key and then
                # decided not to use AI got a card that said it had never been
                # configured -- and the obvious response to that is to paste the
                # key in again. Reported alongside `configured` rather than
                # instead of it, so the card can say switched off *and* that the
                # credentials are still there.
                "enabled": wanted,
                # Off is not unfinished. A town that has deliberately switched
                # text messages off has answered the question, and a checklist
                # that keeps asking is one people learn to ignore -- but it is
                # not set up either, so this is False and the page says
                # "switched off" rather than ticking it.
                "ready": wanted and bool(current) and configured.get(current, False),
            }
        except Exception as exc:
            # One capability failing to report must not blank the other seven.
            # An absent entry reads as "unknown", which the page shows as
            # unfinished -- the safe direction, since the cost is asking about
            # something already done rather than skipping something that isn't.
            logger.warning("provider status failed for %s: %s",
                           sanitize_for_log(capability), sanitize_for_log(str(exc)))

    # The two switchable things with no provider catalog behind them. They have
    # no entry above because there is no vendor to pick, and leaving them out
    # would mean the page had to hold their answer somewhere else -- which is
    # what it was doing, in React state that never reached the server.
    for extra in ("backups", "errors"):
        out[extra] = {"enabled": switches.get(extra, True)}
    return out


@router.get("/setup/state")
async def get_setup_state(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Whether anybody has said this town is set up.

    Nothing answered this. The setup guide opened itself when sign-in or maps
    happened to be unconfigured, which is "is everything set up" wearing a
    disguise -- and a town that deliberately switches most things off never
    satisfies that, so the guide would greet it on every login forever. A banner
    that never goes away is one people stop reading.
    """
    row = (await db.execute(
        select(SystemSettings).order_by(SystemSettings.id).limit(1)
    )).scalar_one_or_none()
    when = getattr(row, "setup_completed_at", None) if row else None
    return {"completed": when is not None, "completed_at": when.isoformat() if when else None}


@router.post("/setup/state")
async def mark_setup_complete(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """"I am done here." Said by a person, because nothing else can say it.

    Deliberately not gated on anything being configured. Two things are actually
    required before a town can take a report -- staff sign-in and a map -- and
    the page already says so and shows what is outstanding. Refusing to let
    somebody close the guide until a checklist is green would make the guide the
    thing standing between them and the console, which is the opposite of what
    it is for.
    """
    from datetime import datetime, timezone

    row = (await db.execute(
        select(SystemSettings).order_by(SystemSettings.id).limit(1)
    )).scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        db.add(row)
        await db.flush()
    # Kept if it is already there. Re-marking would rewrite the date somebody
    # may later want to point at, and the guide is reopenable from the tab
    # regardless -- this flag only decides what happens on sign-in.
    if row.setup_completed_at is None:
        row.setup_completed_at = datetime.now(timezone.utc)
        await db.commit()

    from app.services.admin_audit import record_admin_action

    await record_admin_action(db, event_type="setup.complete", actor=admin)
    return await get_setup_state(db, admin)


class SecretStoreChoice(BaseModel):
    store: str


@router.get("/secrets/store")
async def get_secret_store_choice(_: User = Depends(get_current_admin)):
    """Where this town's credentials are kept, and whether anyone said so.

    `chosen: false` is the state the setup gate holds open. It used to be
    unreachable, because `_secrets_provider()` answered "google" for a town that
    had never been asked -- so the page could not tell a deliberate choice of
    Google Secret Manager from silence, and neither could the code.
    """
    from app.services.secret_manager import SECRET_STORES, _secrets_provider, store_chosen
    from app.services.storage_maintenance import store_reachable

    chosen = store_chosen()
    return {
        "chosen": chosen,
        "store": _secrets_provider() or None,
        "options": list(SECRET_STORES),
        # Whether the store the town picked can actually be contacted. Not the
        # same question, and not a blocker: choosing a vault whose credentials
        # have not arrived yet is a real state, and the credentials that make it
        # reachable are entered on this same page.
        "reachable": store_reachable(),
    }


@router.post("/secrets/store")
async def choose_secret_store(
    body: SecretStoreChoice,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Record where this town's credentials go. Once, deliberately.

    Set-once, and the refusal to change it is the same reasoning that makes the
    secret store card read-only: every credential the town has is in the current
    store, and repointing this setting does not move them. A second choice here
    would be one click that makes every other card unreadable. Moving stores is
    the cloud-profile flow, which migrates first.

    `database` is accepted like the other three. The encrypted database is a
    supported store, and a town whose cloud procurement is unfinished must be
    able to get on with setup -- the gate is about consent, not capability. What
    it must not be is where a town arrives without being asked, which is what it
    was.
    """
    from app.services.secret_manager import SECRET_STORES, _secrets_provider, store_chosen

    store = (body.store or "").strip().lower()
    if store not in SECRET_STORES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown secret store: {body.store}. Expected one of: {', '.join(SECRET_STORES)}.",
        )

    current = _secrets_provider()
    if store_chosen() and current != store:
        raise HTTPException(status_code=409, detail=(
            f"This town's credentials are in {current}. Repointing this setting does not "
            f"move them, so every card would stop being able to read its own key. Set up "
            f"the new store's credentials, let the migration copy them across, and change "
            f"the store after that."
        ))

    if os.getenv("SECRETS_PROVIDER"):
        # The host pinned it. Saying so beats accepting a write that the reader
        # will go on ignoring, which is the shape of every bug in this area.
        raise HTTPException(status_code=409, detail=(
            "The secret store is pinned by this deployment's SECRETS_PROVIDER environment "
            "variable, so it cannot be changed from here."
        ))

    await _persist_secret(db, "SECRETS_PROVIDER", store)

    # Recorded, because "we knowingly chose the encrypted database" is exactly
    # the kind of decision somebody will need to point at later -- and because
    # the whole reason for the gate is that nobody could tell a choice from a
    # default.
    from app.services.admin_audit import record_admin_action

    await record_admin_action(
        db, event_type="secret_store.choose", actor=admin, details={"store": store},
    )
    return await get_secret_store_choice(admin)


class CapabilitySwitchRequest(BaseModel):
    """A partial map: only the switches the caller is changing.

    Partial on purpose. The questionnaire posts the one chip that was clicked,
    and a town that has never been asked about photo redaction must not acquire
    an answer to it because somebody unticked backups.
    """

    switches: Dict[str, bool]


@router.put("/capabilities")
async def set_capability_switches(
    body: CapabilitySwitchRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Record which integrations the town wants, credentials aside.

    There was no endpoint for this because there was no such fact. The setup
    page held the answer in a `Set<string>` initialised to every feature, so
    unticking one hid part of the guide, survived nothing, and switched nothing
    off -- while the page's own copy said "untick to remove it".
    """
    from app.services import capability_switches
    from app.services.admin_audit import record_admin_action

    switches = await capability_switches.set_enabled(db, body.switches)
    # "Who decided this town does not send text messages" is a question with a
    # date on it. The request body is the answer: only the switches the caller
    # actually changed, not the full map that comes back.
    await record_admin_action(
        db, event_type="capability_switches_changed", actor=_,
        details={
            "turned_on": sorted(k for k, v in (body.switches or {}).items() if v),
            "turned_off": sorted(k for k, v in (body.switches or {}).items() if not v),
        },
    )
    return {"switches": switches}


@router.get("/providers/cloud-identity")
async def cloud_identity(_: User = Depends(get_current_admin)):
    """Whether this server already has an identity on its cloud.

    When it does, the credential boxes for that cloud are not merely optional --
    leaving them empty is the better answer. The token is issued minutes at a
    time and rotated by the platform, so there is no long-lived secret to leak,
    to vault, or to expire on a date nobody recorded.

    Two of the three already behaved this way by accident: boto3 falls through
    to the instance role and google-auth to Application Default Credentials when
    nothing is configured. Nothing said so, so every town pasted a key anyway.
    """
    from app.services.cloud_identity import summary
    return summary()


@router.get("/providers/cloud-profile")
async def get_cloud_profile(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Report the current cloud environment: the matched profile (or 'mixed'),
    the per-capability selections behind it, and the fixed Maps provider."""
    from app.services.secret_manager import get_secret, _secrets_provider
    from app.core.config import get_settings as _app_settings
    from app.core.encryption import _kms_provider
    from app.services.map_provider import MAP_CATALOG, MAP_PROVIDER_KEY, normalize_provider
    maps_provider = normalize_provider(await get_secret(MAP_PROVIDER_KEY))
    ai = (await get_secret("AI_PROVIDER")) or "vertex"
    translation = (await get_secret("TRANSLATION_PROVIDER")) or "google"
    identity = (await get_secret("IDENTITY_PROVIDER")) or "auth0"
    email = (await get_secret("EMAIL_PROVIDER")) or "smtp"
    sms = (await get_secret("SMS_PROVIDER")) or ""
    secrets = _secrets_provider()
    kms = _kms_provider()
    return {
        "profile": _derive_cloud_profile(ai, translation, secrets, kms),
        "managed": _app_settings().managed_mode,
        "components": {
            "ai": ai, "translation": translation, "secrets": secrets, "kms": kms,
            "identity": identity, "email": email, "sms": sms,
        },
        # Maps was pinned to Google when this endpoint was written. It has since
        # become a switchable capability like the others -- /maps/catalog offers
        # Google, Esri, Azure and Apple, and _PROVIDER_SELECT_KEY routes saves to
        # MAP_PROVIDER -- so reporting it as locked contradicted the card sitting
        # directly beneath the banner that showed it.
        "maps": {
            "provider": maps_provider,
            "locked": False,
            "label": MAP_CATALOG.get(maps_provider, {}).get("name")
                     or maps_provider.replace("_", " ").title(),
        },
        "profiles": [{"id": k, **v} for k, v in CLOUD_PROFILES.items()],
    }


@router.post("/providers/cloud-profile")
async def set_cloud_profile(
    body: CloudProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Apply a whole cloud environment in one choice: sets the AI, translation and
    secret-store providers to the profile's defaults. In managed (state-hosted)
    mode this is locked — the compliance boundary is set by the hosting platform.
    """
    # The compliance boundary is a state-level decision when hosted centrally.
    from app.core.config import get_settings as _app_settings
    if _app_settings().managed_mode:
        raise HTTPException(
            status_code=403,
            detail="The cloud environment is set by your state's hosting platform and can't be changed here.",
        )
    pid = (body.profile or "").strip().lower()
    if pid not in CLOUD_PROFILES:
        raise HTTPException(status_code=400, detail=f"Unknown cloud profile: {body.profile}")
    p = CLOUD_PROFILES[pid]

    await _persist_secret(db, _PROVIDER_SELECT_KEY["ai"], p["ai"])
    await _persist_secret(db, _PROVIDER_SELECT_KEY["translation"], p["translation"])
    await _persist_secret(db, "SECRETS_PROVIDER", p["secrets"])
    await _persist_secret(db, "KMS_PROVIDER", p["kms"])
    # Email/SMS only when the cloud has a native option — Google has no first-party
    # SMS, so leave the existing SMS provider (e.g. Twilio) untouched there.
    if p.get("email"):
        await _persist_secret(db, "EMAIL_PROVIDER", p["email"])
    if p.get("sms"):
        await _persist_secret(db, "SMS_PROVIDER", p["sms"])

    warnings: List[str] = []
    # Gov-readiness: flipping the secret store to a vault that isn't wired up yet
    # is allowed (writes fall back to the encrypted DB), but say so plainly.
    if p["secrets"] == "azure":
        try:
            from app.core import azure_keyvault
            if not azure_keyvault.is_configured():
                warnings.append(
                    "Azure Key Vault isn't configured yet — secrets stay in the encrypted "
                    "database until Key Vault credentials are added."
                )
        except Exception:
            warnings.append("Could not verify Azure Key Vault configuration.")
    elif p["secrets"] == "google":
        try:
            from app.services.secret_manager import _is_gcp_available
            if not _is_gcp_available():
                warnings.append(
                    "Google Secret Manager isn't reachable yet — secrets stay in the "
                    "encrypted database until GOOGLE_CLOUD_PROJECT and credentials are set."
                )
        except Exception:
            # This block only decides whether to add an advisory warning. Failing
            # to determine reachability is not a reason to fail the profile switch.
            pass
    elif p["secrets"] == "aws":
        try:
            from app.core import aws_secretsmanager
            if not aws_secretsmanager.is_configured():
                warnings.append(
                    "AWS Secrets Manager isn't configured yet (set AWS_REGION + credentials) — "
                    "secrets stay in the encrypted database until then."
                )
        except Exception:
            # As above: advisory only, so an unavailable check stays silent
            # rather than blocking the switch.
            pass

    # Secret-store migration safety.
    #
    # This repoints SECRETS_PROVIDER and moves nothing. Every credential the
    # town has already entered is in the old store, and most have had their
    # encrypted database copy scrubbed after being verified there -- so the
    # moment the pointer moves, `get_secret` starts asking somewhere that has
    # never heard of them and each one reads as absent.
    #
    # The KMS half of this has carried a warning since it was written and the
    # secret half never did, which is the more dangerous of the two: PII stays
    # readable while the old KMS credentials are in place, whereas a repointed
    # secret store takes the mail relay, the map key and the identity provider
    # with it.
    # No DB_REQUIRED_KEYS filter needed: those always keep their database copy
    # and so never appear in this set by construction.
    if await _vaulted_key_names():
        warnings.append(
            f"Credentials already saved are in your previous secret store and are not moved "
            f"by this. They stay readable only while that store is reachable. Enter the "
            f"{ {'azure': 'Azure Key Vault', 'aws': 'AWS Secrets Manager'}.get(p['secrets'], 'Google Secret Manager') } "
            f"credentials, then let the hourly migration copy everything across before "
            f"retiring the old one."
        )

    # KMS migration safety: existing PII is unwrapped by the KMS tag stored in
    # each value, so it stays readable ONLY while the previous KMS credentials
    # remain in place. New PII uses the new KMS immediately.
    warnings.append(
        f"PII encryption now uses {p['kms'].upper()} KMS for new data. Existing encrypted "
        "records still need the previous KMS's credentials to decrypt — keep them in place, "
        "or run “Re-encrypt All PII” to migrate everything to the new key."
    )

    identity_applied = False
    if body.apply_identity:
        await _persist_secret(db, _PROVIDER_SELECT_KEY["identity"], p["identity_recommended"])
        identity_applied = True

    from app.services.secret_manager import clear_cache
    # Deliberately the whole cache: a profile switch rewrites the selection for
    # several capabilities at once, and which bundles those land in is the
    # profile's business rather than this function's.
    clear_cache()
    # Drop the cached wrapped-DEK so the next PII write re-wraps under the new KMS.
    try:
        from app.core import pii_crypto
        pii_crypto.clear_caches()
    except Exception:
        # Best-effort. A stale cached DEK means the next PII write re-wraps
        # under the old KMS, which the migration warning above already covers;
        # it is not worth failing an otherwise-applied profile over.
        pass

    from app.core.sanitize import sanitize_for_log
    logger.info(
        f"[Providers] {sanitize_for_log(current_user.username)} set cloud profile → "
        f"{sanitize_for_log(pid)} (identity_applied={identity_applied})"
    )
    return {
        "ok": True,
        "profile": pid,
        "components": {
            "ai": p["ai"], "translation": p["translation"], "secrets": p["secrets"],
            "kms": p["kms"], "email": p.get("email", ""), "sms": p.get("sms", ""),
        },
        "identity_recommended": p["identity_recommended"],
        "identity_applied": identity_applied,
        "warnings": warnings,
    }


@router.post("/settings", response_model=SystemSettingsResponse)
async def update_settings(
    settings_data: SystemSettingsBase,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Update system settings (admin only)"""
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = result.scalar_one_or_none()
    
    if not settings:
        settings = SystemSettings(**settings_data.model_dump())
        db.add(settings)
    else:
        # IMPORTANT: Only update fields that were explicitly provided in the request
        # Using exclude_unset=True prevents default values in the schema from 
        # overwriting saved values when the frontend doesn't send all fields
        for key, value in settings_data.model_dump(exclude_unset=True).items():
            setattr(settings, key, value)
    
    await db.commit()
    await db.refresh(settings)
    return settings


# ============ Disclaimer Acknowledgment ============

@router.post("/disclaimer/acknowledge")
async def log_disclaimer_acknowledgment(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Log that a user has acknowledged the non-emergency disclaimer.
    This is stored for legal protection and audit purposes.
    Public endpoint - no authentication required."""
    
    # Get client info for logging
    body = await request.json()
    session_id = body.get("session_id", "unknown")
    
    # Get real IP (handle proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else "unknown"
    
    user_agent = request.headers.get("User-Agent", "unknown")[:500]
    
    # Create log entry
    acknowledgment = DisclaimerAcknowledgment(
        session_id=session_id,
        ip_address=ip_address,
        user_agent=user_agent,
        disclaimer_version="1.0"
    )
    db.add(acknowledgment)
    await db.commit()
    
    from app.core.sanitize import sanitize_for_log
    logger.info(f"Disclaimer acknowledged: session={sanitize_for_log(session_id)}, ip={sanitize_for_log(ip_address)}")
    
    return {"status": "acknowledged", "session_id": session_id}


# ============ Secrets ============

@router.get("/secrets", response_model=List[SecretResponse])
async def list_secrets(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Every secret this town has, and whether it is really there.

    `is_configured` is reported from the store of record rather than from the
    database column of that name, and the two are not the same claim. The column
    says "a value was written here once". `get_secret` says "there is a value
    there now" -- which is what every other part of this console means by
    configured, and what `_configured_map` answers for the provider cards.

    They diverge exactly when it matters. A secret migrated into the vault keeps
    `is_configured = True` in the database with its encrypted copy scrubbed, so
    when the vault is unreachable the column still says yes about a value
    nothing can currently read. The provider cards say "we cannot tell, so no";
    this endpoint said yes; and the settings with no provider card -- backups,
    crash reporting -- had only this answer, so a town whose vault was down saw
    a tick on backups whose credentials the backup task could not load.

    One definition, and it is the pessimistic one: if we cannot read it, we do
    not claim it. Reads are bundle-cached, so this is a handful of fetches
    rather than one per key.

    No values are returned, for any key. Four were exempted as "config choices,
    not secrets" -- SMS_PROVIDER, EMAIL_ENABLED, SMTP_USE_TLS and SMTP_PORT --
    and the exemption returned `secret.key_value`, the *database* column. That
    column holds ciphertext while a secret is in the database and None once it
    has been migrated to the vault and scrubbed, which all four have been. So
    the branch returned null for every key it existed to expose, and would have
    returned an encrypted blob if it had not. Nothing read it either: the one
    consumer compared the returned SMS_PROVIDER against 'none' to decide whether
    texting was on, and now asks /providers/status, which reports what dispatch
    resolves rather than what is stored.
    """
    from app.services.secret_manager import get_secret

    result = await db.execute(select(SystemSecret))
    secrets = result.scalars().all()

    response = []
    for secret in secrets:
        data = SecretResponse.model_validate(secret)
        data.key_value = None
        try:
            data.is_configured = bool((await get_secret(secret.key_name) or "").strip())
        except Exception:
            # An unreachable store is not the same as an unconfigured secret,
            # and saying so is the safe direction: the cost is asking about
            # something already done, rather than ticking something unreadable.
            data.is_configured = False
        response.append(data)

    return response


@router.post("/secrets", response_model=SecretResponse)
async def create_or_update_secret(
    secret_data: SecretCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Create or update a secret (admin only) - values are encrypted at rest and stored in Secret Manager"""
    from app.core.encryption import encrypt
    from app.core.managed import reject_platform_key_writes
    from app.services.secret_manager import set_secret, clear_cache

    reject_platform_key_writes(secret_data.key_name)
    # The other door into the secret table. Gating only the provider cards would
    # leave the plain credential fields -- backups, crash reporting, the Google
    # account -- writing into the encrypted database with nobody having chosen
    # it, which is the case the gate exists for.
    if secret_data.key_name not in _STORE_CHOICE_KEYS:
        _require_a_secret_store()

    # Same reason as `_persist_secret`: surrounding whitespace is never part of
    # a credential, and this is the endpoint the plain secret fields on the
    # setup page post to -- the one SMTP_USER came in through with a leading
    # space, which the mail relay answered with 535 Authentication failed.
    secret_data.key_value = (secret_data.key_value or "").strip()

    # Bootstrap keys that must stay in database (needed to access Secret Manager)
    bootstrap_keys = {"GCP_SERVICE_ACCOUNT_JSON", "GOOGLE_CLOUD_PROJECT"}
    
    # Try to write to Secret Manager first (if not a bootstrap key)
    sm_success = False
    if secret_data.key_value and secret_data.key_name not in bootstrap_keys:
        try:
            sm_success = await set_secret(secret_data.key_name, secret_data.key_value)
            if sm_success:
                clear_cache(key_name=secret_data.key_name)  # Clear cache so reads get fresh data
        except Exception as e:
            logger.warning(f"Failed to write to Secret Manager, using database only: {e}")
    
    # Always store in database as backup (encrypted)
    result = await db.execute(
        select(SystemSecret).where(SystemSecret.key_name == secret_data.key_name)
    )
    secret = result.scalar_one_or_none()
    
    # Encrypt the secret value before storing
    encrypted_value = encrypt(secret_data.key_value) if secret_data.key_value else None
    
    if secret:
        secret.key_value = encrypted_value
        secret.is_configured = bool(secret_data.key_value)
    else:
        secret = SystemSecret(
            key_name=secret_data.key_name,
            key_value=encrypted_value,
            description=secret_data.description,
            is_configured=bool(secret_data.key_value)
        )
        db.add(secret)
    
    await db.commit()
    await db.refresh(secret)

    # Sweep now, not at the top of the hour. This endpoint keeps an encrypted
    # database copy of everything it writes, and only the vault sweep scrubs
    # those into the chosen store -- so a town that finished setup was shown
    # "N keys are still in the database" until the hourly task fired, and the
    # hourly timer restarts from zero on every deploy. Kicked even when this
    # write did not reach the store: a bootstrap key saved here (the Google
    # service account) is exactly the save that makes the store reachable for
    # everything entered before it. The sweep no-ops with no reachable store.
    # After the response, so a slow store cannot make Save feel broken.
    from app.services.storage_maintenance import vault_secrets as _vault
    background.add_task(_vault)

    # The backstop middleware records "Added or updated: /api/system/secrets",
    # which does not say which credential -- and which credential is the whole
    # entry. The key name only; the value is what this table must never carry.
    from app.services.admin_audit import record_admin_action
    await record_admin_action(
        db, event_type="secret_saved", actor=_,
        details={
            "key_name": secret_data.key_name,
            "configured": secret.is_configured,
            "stored_in": "secret_manager" if sm_success else "database",
        },
    )

    return {
        **secret.__dict__,
        "secret_manager": sm_success,
        "_sa_instance_state": None  # Remove SQLAlchemy internal state
    }



@router.post("/secrets/sync")
async def sync_secrets(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Add any missing secrets from the default list (admin only)"""
    from app.db.init_db import DEFAULT_SECRETS
    
    added = []
    for secret_data in DEFAULT_SECRETS:
        result = await db.execute(
            select(SystemSecret).where(SystemSecret.key_name == secret_data["key_name"])
        )
        existing = result.scalar_one_or_none()
        
        if not existing:
            secret = SystemSecret(
                key_name=secret_data["key_name"],
                description=secret_data.get("description", ""),
                is_configured=False
            )
            db.add(secret)
            added.append(secret_data["key_name"])
    
    await db.commit()
    return {"status": "success", "added_secrets": added, "count": len(added)}


@router.post("/secrets/migrate-to-secret-manager")
async def migrate_secrets_to_secret_manager(
    _: User = Depends(get_current_admin)
):
    """
    Migrate all configured secrets from database to Google Secret Manager.
    
    This copies encrypted database secrets to Secret Manager for production use.
    Bootstrap keys (GCP_SERVICE_ACCOUNT_JSON, GOOGLE_CLOUD_PROJECT) are skipped
    as they're needed to access Secret Manager itself.
    
    Safe to run multiple times - existing secrets are overwritten with latest values.
    """
    from app.services.secret_manager import migrate_to_secret_manager
    
    result = await migrate_to_secret_manager()
    return result


@router.post("/secrets/migrate-encryption")
async def migrate_secrets_to_encrypted(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Migrate existing plaintext secrets to encrypted format.
    
    This is a one-time migration for secrets that were stored before 
    encryption was implemented. Safe to run multiple times - already
    encrypted values are skipped.
    """
    from app.core.encryption import encrypt, is_encrypted
    
    result = await db.execute(
        select(SystemSecret).where(SystemSecret.is_configured == True)
    )
    secrets = result.scalars().all()
    
    migrated = []
    already_encrypted = []
    errors = []
    
    for secret in secrets:
        if not secret.key_value:
            continue
            
        # Check if already encrypted (starts with gAAAA)
        if is_encrypted(secret.key_value):
            already_encrypted.append(secret.key_name)
            continue
        
        try:
            # Encrypt the plaintext value
            secret.key_value = encrypt(secret.key_value)
            migrated.append(secret.key_name)
        except Exception as e:
            errors.append({"key": secret.key_name, "error": "Migration failed"})
    
    await db.commit()
    
    return {
        "status": "success",
        "migrated": migrated,
        "migrated_count": len(migrated),
        "already_encrypted": already_encrypted,
        "already_encrypted_count": len(already_encrypted),
        "errors": errors
    }


# ============ Document Retention ============

# There is no /retention/states endpoint any more. It existed to populate a
# state picker, and the picker existed to look up a retention period in a table
# the product had invented — 51 jurisdictions, 41 of them five years, each with
# a different records authority named as the source. A municipality's schedule
# comes from its own clerk; see app/services/retention_config.py.


@router.get("/retention/policy")
async def get_current_retention_policy(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Get current retention policy configuration.

    Answers "is there a policy" before "what is it". The two halves that make
    one — how long a closed request is kept, and what is removed when that
    expires — are both the town's to state, and this used to fill in both from
    a per-state table nobody had verified.
    """
    from app.services.retention_config import read_retention_config
    from app.services.retention_scrub import describe_selection
    from app.services.retention_service import get_retention_stats

    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = result.scalar_one_or_none()

    config = read_retention_config(settings)
    scrub_fields = describe_selection(
        getattr(settings, "retention_scrub_fields", None) if settings else None
    )

    if not config.configured:
        return {
            "configured": False,
            "reason": config.reason,
            "detail": config.detail,
            # Whatever half is filled in, labelled as what it is: the screen
            # needs it to render the form it is asking somebody to finish,
            # which is a different thing from reporting a policy in force.
            "retention_days": config.retention_days,
            "mode": config.mode,
            "scrub_fields": scrub_fields,
            "stats": None,
        }

    stats = await get_retention_stats(db, config.retention_days)

    return {
        "configured": True,
        "retention_days": config.retention_days,
        "mode": config.mode,
        # The catalog and this town's choice in one object, so the screen never
        # has to hold its own copy of what the fields are called.
        "scrub_fields": scrub_fields,
        "stats": stats
    }


@router.post("/retention/policy")
async def update_retention_policy(
    retention_days: int = None,
    mode: str = None,
    scrub_fields: Optional[List[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Update retention policy configuration (admin only).

    Both halves arrive from the town. There is no state to select and nothing
    to inherit: `retention_days` is the period its own records retention
    schedule sets, and `scrub_fields` is what a run removes when that expires.
    """
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = result.scalar_one_or_none()

    if not settings:
        settings = SystemSettings()
        db.add(settings)

    # If the state has pushed a managed retention/legal-hold policy, the town
    # can't override it here — it's controlled from the hosting control plane.
    managed = getattr(settings, "managed_policy", None) or {}
    if any(k in managed for k in ("retention_days", "retention_mode", "pii_anonymization", "legal_hold")):
        raise HTTPException(
            403,
            "Data-retention policy is managed by your state and can't be changed here.",
        )

    if retention_days is not None:
        # 0 clears it, which puts the town back to unconfigured and stops
        # retention running. Without an explicit way to clear, a period once
        # set could only ever be changed to another period.
        if retention_days == 0:
            settings.retention_days = None
        elif retention_days < 1:
            raise HTTPException(400, "Retention period must be at least 1 day, or 0 to clear it.")
        elif retention_days > 36500:
            # A hundred years. Not a real schedule, and the likeliest way to
            # get here is a period typed in days when years were meant.
            raise HTTPException(400, "Retention period must be 36,500 days (100 years) or less.")
        else:
            settings.retention_days = retention_days

    if mode:
        from app.services.retention_scrub import MODES, normalise_mode
        resolved = normalise_mode(mode)
        if resolved not in MODES:
            raise HTTPException(400, f"Mode must be one of: {', '.join(MODES)}")
        settings.retention_mode = resolved

    if scrub_fields is not None:
        from app.services.retention_scrub import FIELD_IDS, normalise_fields
        unknown = [f for f in scrub_fields if f not in FIELD_IDS]
        if unknown:
            # Rejected rather than quietly dropped. A field silently ignored is
            # a town believing it removes something it does not.
            raise HTTPException(400, f"Unknown fields to scrub: {', '.join(sorted(unknown))}")
        settings.retention_scrub_fields = normalise_fields(scrub_fields)

    await db.commit()
    await db.refresh(settings)

    from app.services.retention_config import read_retention_config
    config = read_retention_config(settings)

    return {
        "status": "updated",
        # Whether retention will now actually run, and if not, why. A save that
        # stored a mode but left the period blank still archives nothing, and
        # the screen should not have to infer that from an absent field.
        "configured": config.configured,
        "reason": config.reason,
        "detail": config.detail,
        "retention_days": settings.retention_days,
        "mode": settings.retention_mode,
        "scrub_fields": settings.retention_scrub_fields,
    }


@router.get("/timezone")
async def get_town_timezone(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Which clock the console shows times on.

    Storage does not move. Every timestamp column is timestamptz and stays in
    UTC; this only decides what a screen converts it into.
    """
    from app.services.town_time import COMMON_TIMEZONES, normalise_timezone, offset_label

    settings = await read_settings_row(db)
    current = normalise_timezone(getattr(settings, "timezone", None) if settings else None)
    return {
        "timezone": current,
        "offset": offset_label(current),
        "configured": bool(getattr(settings, "timezone", None)) if settings else False,
        "common": [{"id": z, "offset": offset_label(z)} for z in COMMON_TIMEZONES],
    }


@router.post("/timezone")
async def set_town_timezone(
    payload: Dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    from app.services.town_time import is_valid_timezone, offset_label

    name = str(payload.get("timezone", "")).strip()
    if not is_valid_timezone(name):
        # Rejected rather than quietly falling back to UTC. Silently storing
        # something other than what was chosen is how a town ends up certain
        # its times are local when they are not.
        raise HTTPException(status_code=400, detail=f"Not a timezone this server recognises: {name or '(empty)'}")

    settings = await read_settings_row(db, create=True)
    settings.timezone = name
    await db.commit()
    return {"timezone": name, "offset": offset_label(name)}


@router.get("/retention/preview")
async def preview_retention_run(
    limit: int = Query(50, ge=1, le=500, description="How many records to list"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """What pressing "Run now" would actually do.

    The button had no preview and no confirmation. In `delete` mode it
    permanently destroys resident records on one click, and the response came
    back before the task had touched anything, so nothing on screen could say
    what happened. Somebody deserves to see the number and the mode before
    that, not after.
    """
    from app.models import ServiceRequest
    from app.services.retention_config import read_retention_config
    from app.services.retention_scrub import describe_selection
    from app.services.retention_service import get_retention_stats

    settings = await read_settings_row(db)
    config = read_retention_config(settings)
    if settings is not None and getattr(settings, "legal_hold", False):
        return {
            "eligible": 0,
            "on_legal_hold": 0,
            # Through normalise_mode, like everywhere else. Rows written before
            # this column had a default hold NULL, and the raw `or "anonymize"`
            # here answered with a legacy name the screen does not match on.
            "mode": config.mode,
            "blocked": "legal_hold",
            # Listing records that "will be archived next run" while a hold
            # means nothing can be archived is the sort of contradiction that
            # gets a screen distrusted.
            "records": [],
            "summary": None,
        }

    if not config.configured:
        # The honest preview of a run that would do nothing. Answering with a
        # cutoff computed from an invented period, and a list of records
        # "eligible for archival" under it, would be a screen inviting somebody
        # to approve a schedule this town never chose.
        return {
            "eligible": 0,
            "on_legal_hold": 0,
            "will_act_on": 0,
            "mode": config.mode,
            "blocked": "unconfigured",
            "reason": config.reason,
            "detail": config.detail,
            "retention_days": config.retention_days,
            "cutoff_date": None,
            "records": [],
            "summary": None,
        }

    retention_days = config.retention_days
    mode = config.mode
    stats = await get_retention_stats(db, retention_days)

    # Flagged records are past their date and must not be touched. They are
    # counted separately rather than hidden, because "142 eligible" and "142
    # eligible, 3 of which will be skipped" are different sentences to somebody
    # approving a deletion.
    held = (await db.execute(
        select(func.count(ServiceRequest.id)).where(
            and_(ServiceRequest.status == "closed", ServiceRequest.flagged.is_(True))
        )
    )).scalar() or 0

    eligible = stats.get("eligible_for_archival", 0) if isinstance(stats, dict) else 0

    from app.services.retention_service import get_records_for_archival
    from app.services.retention_window import describe_record, retention_cutoff, summarise
    from app.services.town_time import normalise_timezone

    cutoff = retention_cutoff(retention_days)
    rows = await get_records_for_archival(db, retention_days, limit=limit)
    records = [describe_record(r, cutoff=cutoff) for r in rows]
    records.sort(key=lambda r: r["age_days"] if r["age_days"] is not None else -1, reverse=True)

    return {
        "eligible": eligible,
        "on_legal_hold": held,
        "will_act_on": max(0, eligible - held),
        "mode": mode,
        "retention_days": retention_days,
        "cutoff_date": stats.get("cutoff_date") if isinstance(stats, dict) else None,
        # The word the caller has to send back. Deleting resident records on a
        # single click is not something to make easy.
        # Purge clears every field on every eligible record and cannot be
        # undone, so it is typed out rather than clicked.
        "confirmation_required": "PURGE" if mode == "purge" else None,
        "scrub_fields": [
            f["label"] for f in describe_selection(
                getattr(settings, "retention_scrub_fields", None) if settings else None
            ) if f["selected"]
        ],
        # The records themselves, oldest first.
        #
        # A count is not reviewable. It cannot show that the oldest eligible
        # record is four years past its date because the policy has never
        # actually run, and it cannot show that a report somebody assumed was
        # exempt is in the list because nobody set the hold on it.
        #
        # Drawn from get_records_for_archival -- the function the sweep itself
        # calls -- rather than a second query that resembles it. A preview
        # computed differently from the run invites somebody to confirm against
        # a list that is not the list.
        "records": records,
        "summary": summarise(records, total=eligible,
                             retention_days=retention_days,
                             cutoff=cutoff),
        "timezone": normalise_timezone(getattr(settings, "timezone", None) if settings else None),
    }


@router.post("/retention/run")
async def run_retention_now(
    payload: Dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Run retention enforcement now.

    In `delete` mode this permanently destroys records, so it requires the
    caller to echo back a confirmation word. A modal alone is a client-side
    courtesy; anything that can be done by a stray fetch should not be able to
    delete resident data.
    """
    from app.tasks.service_requests import enforce_retention_policy

    from app.services.retention_config import read_retention_config

    settings = await read_settings_row(db)
    config = read_retention_config(settings)

    # The hold outranks everything, and is checked before the policy for the
    # same reason the task checks it first: it is not a variation on the
    # schedule, it is the schedule not applying.
    #
    # Refused rather than queued, which it was not. The task declines correctly
    # and archives nothing, so no record was ever at risk -- but the endpoint
    # answered "Retention enforcement started" with a task id, and the only
    # place contradicting it was a worker log. An admin who places a litigation
    # hold and then sees a retention run report as started has been told the
    # opposite of what happened, on the one subject where being sure matters.
    if settings is not None and getattr(settings, "legal_hold", False):
        raise HTTPException(
            status_code=409,
            detail="An instance-wide legal hold is in place, so nothing can be archived "
                   "or deleted. Lift the hold first if this run is meant to happen.",
        )

    # Refused here rather than queued, for the same reason.
    if not config.configured:
        raise HTTPException(
            status_code=409,
            detail=config.detail or "No records-retention schedule is configured.",
        )

    mode = config.mode
    if mode == "purge" and str(payload.get("confirm", "")).strip() != "PURGE":
        raise HTTPException(
            status_code=400,
            detail='This policy clears every field on every eligible record and cannot be '
                   'undone. Send confirm="PURGE" to proceed.',
        )

    # Not swallowed. The admin typed a confirmation word to get here and is
    # being handed a task id to watch; answering "triggered" for a job that
    # never reached a worker would be the worst version of this -- a retention
    # run they believe happened, on the strength of which they stop checking.
    try:
        task = enforce_retention_policy.delay()
    except Exception as exc:
        logger.warning("[Retention] could not queue enforcement: %s", sanitize_for_log(str(exc)))
        raise HTTPException(status_code=503, detail=QUEUE_UNAVAILABLE)

    return {
        "status": "triggered",
        "task_id": task.id,
        "mode": mode,
        "message": "Retention enforcement started. It works through every eligible record.",
    }


@router.get("/retention/legal-hold")
async def get_legal_hold_requests(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Get all requests currently under legal hold (flagged)"""
    from app.models import ServiceRequest
    
    result = await db.execute(
        select(ServiceRequest).where(
            and_(
                ServiceRequest.flagged == True,
                ServiceRequest.deleted_at.is_(None)
            )
        ).order_by(ServiceRequest.requested_datetime.desc())
    )
    requests = result.scalars().all()
    
    return {
        "count": len(requests),
        "requests": [
            {
                "id": r.id,
                "service_request_id": r.service_request_id,
                "service_name": r.service_name,
                "description": r.description[:100] + "..." if r.description and len(r.description) > 100 else r.description,
                "status": r.status,
                "address": r.address,
                "requested_datetime": r.requested_datetime.isoformat() if r.requested_datetime else None,
                "closed_datetime": r.closed_datetime.isoformat() if r.closed_datetime else None,
            }
            for r in requests
        ]
    }


@router.get("/retention/export/fields")
async def public_records_export_fields(
    _: User = Depends(get_current_admin),
):
    """What a records custodian can choose to include, and what is sensitive.

    Served rather than hardcoded in the UI so the picker, the export and the
    audit entry are all describing the same catalog.
    """
    from app.services.records_export import describe_fields

    return {"fields": describe_fields()}


@router.get("/retention/export")
async def export_for_public_records(
    start_date: Optional[str] = Query(None, description="ISO date or datetime"),
    end_date: Optional[str] = Query(None, description="ISO date or datetime; a bare date means end of that day"),
    statuses: Optional[List[str]] = Query(None, description="open / in_progress / closed"),
    service_codes: Optional[List[str]] = Query(None, description="Limit to these categories"),
    request_ids: Optional[List[str]] = Query(None, description="Specific request ids"),
    fields: Optional[List[str]] = Query(None, description="Field ids; omit for the usual set"),
    include_archived: bool = Query(True, description="Include records whose contents retention has cleared"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """Export records in answer to a public-records request.

    A custodian is answering a specific request: releasing the records asked
    for, and not the ones that were not. This used to offer a date range and a
    fixed set of ten columns, so "pothole complaints on Main Street in 2024"
    was answered with every report the town has ever taken, and a request that
    should have excluded internal notes could not exclude them.

    Over-disclosure is the failure that matters. A resident's phone number
    released in answer to a request that did not ask for it cannot be taken
    back, and they never knew it was in scope -- so the reporter fields are
    opt-in by name, and choosing one is recorded.
    """
    from datetime import datetime, timezone
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from sqlalchemy.orm import selectinload

    from app.models import ServiceRequest
    from app.services.admin_audit import record_admin_action
    from app.services.records_export import (
        UnknownField, build_row, headers, normalise_fields, parse_boundary,
        preamble, sensitive_selected,
    )

    try:
        chosen = normalise_fields(fields)
        start = parse_boundary(start_date)
        end = parse_boundary(end_date, end=True)
    except (UnknownField, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not chosen:
        raise HTTPException(status_code=400, detail="Choose at least one field to export.")

    # Releasing the reporter's identity is a decision, not a default. The
    # export endpoint elsewhere already gates bulk PII on admin; this one is
    # admin-only throughout, so the gate here is that it is recorded and that
    # the file says which fields were left out.
    sensitive = sensitive_selected(chosen)

    # The file no longer headlines a statute. It used to, from a per-state
    # table nobody had verified, defaulting to "Federal FOIA" — a legal claim
    # printed on a document that leaves the building. The custodian knows which
    # law they are answering under; the export does not need to guess, and a
    # wrong citation is worse than none.
    query = select(ServiceRequest).options(
        selectinload(ServiceRequest.assigned_department)
    ).where(ServiceRequest.deleted_at.is_(None))

    if start:
        query = query.where(ServiceRequest.requested_datetime >= start)
    if end:
        query = query.where(ServiceRequest.requested_datetime <= end)
    if statuses:
        query = query.where(ServiceRequest.status.in_(statuses))
    if service_codes:
        query = query.where(ServiceRequest.service_code.in_(service_codes))
    if request_ids:
        query = query.where(ServiceRequest.service_request_id.in_(request_ids))
    if not include_archived:
        query = query.where(ServiceRequest.archived_at.is_(None))

    query = query.order_by(ServiceRequest.requested_datetime.desc())
    records = (await db.execute(query)).scalars().all()

    generated = datetime.now(timezone.utc)
    output = io.StringIO()
    for line in preamble(
        total=len(records), exported_by=current_user.username, fields=chosen,
        filters={
            "start_date": start_date, "end_date": end_date, "statuses": statuses,
            "service_codes": service_codes, "request_ids": request_ids,
        },
        generated=generated,
    ):
        output.write(line + "\n")

    writer = csv.writer(output)
    writer.writerow(headers(chosen))
    for record in records:
        row = build_row(record, chosen)
        # A record retention has cleared still exists and still counts; saying
        # so beats a row of blanks that reads like a broken export.
        if getattr(record, "archived_at", None) and "description" in chosen:
            row[chosen.index("description")] = "[Content cleared per retention policy]"
        writer.writerow(row)

    # Every export, not only the ones carrying PII. "Which records left this
    # building, when, and who took them" is the question an audit of a records
    # process asks first.
    await record_admin_action(
        db, event_type="public_records_export", actor=current_user,
        details={
            "records": len(records),
            "fields": chosen,
            "sensitive_fields": sensitive,
            "start_date": start_date, "end_date": end_date,
            "statuses": statuses, "service_codes": service_codes,
            "request_ids_count": len(request_ids) if request_ids else 0,
        },
    )

    output.seek(0)
    filename = f"records_export_{generated.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============ Statistics ============

@router.get("/statistics", response_model=StatisticsResponse)
async def get_statistics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_staff)
):
    """Get system statistics (staff only)"""
    # Total counts by status
    total = await db.execute(select(func.count(ServiceRequest.id)))
    total_count = total.scalar() or 0
    
    open_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(ServiceRequest.status == "open")
    )
    open_count = open_result.scalar() or 0
    
    in_progress_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(ServiceRequest.status == "in_progress")
    )
    in_progress_count = in_progress_result.scalar() or 0
    
    closed_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(ServiceRequest.status == "closed")
    )
    closed_count = closed_result.scalar() or 0
    
    # Requests by category
    category_result = await db.execute(
        select(ServiceRequest.service_name, func.count(ServiceRequest.id))
        .group_by(ServiceRequest.service_name)
    )
    requests_by_category = {row[0]: row[1] for row in category_result.all()}
    
    # Recent requests
    recent_result = await db.execute(
        select(ServiceRequest)
        .order_by(ServiceRequest.requested_datetime.desc())
        .limit(10)
    )
    recent_requests = recent_result.scalars().all()
    
    return StatisticsResponse(
        total_requests=total_count,
        open_requests=open_count,
        in_progress_requests=in_progress_count,
        closed_requests=closed_count,
        requests_by_category=requests_by_category,
        requests_by_status={
            "open": open_count,
            "in_progress": in_progress_count,
            "closed": closed_count
        },
        recent_requests=recent_requests
    )


# ============ Advanced Statistics (PostGIS-powered) ============

from sqlalchemy import text, extract
from datetime import timedelta
import json
from app.schemas import (
    AdvancedStatisticsResponse, HeatmapDataResponse, HotspotData, TrendData, DepartmentMetrics,
    PredictiveInsights, CostEstimate, RepeatLocation
)
from app.models import Department

# Redis client import (reuse from open311)
try:
    from app.api.open311 import redis_client
except ImportError:
    redis_client = None

STATS_CACHE_TTL = 300  # 5 minutes


@router.get("/advanced-statistics", response_model=AdvancedStatisticsResponse)
async def get_advanced_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """Advanced statistics, with the reason written down when it cannot build them.

    This endpoint is six hundred lines of aggregation and a single unguarded
    exception anywhere in it returns a bare 500. That is what happened: an
    aware/naive datetime subtraction raised TypeError, FastAPI turned it into
    "Internal Server Error", and the browser console said `Request failed`.
    Nothing named the line, the file, or the exception -- diagnosing it needed
    the source rather than the logs.

    So the failure is now logged with a traceback and the response says which
    exception type it was. Not the message: an aggregation error can quote a
    row, and this is rendered in a browser. The type plus the server log is
    enough to find it and carries nothing a resident wrote.
    """
    try:
        return await _advanced_statistics(db)
    except Exception as exc:
        logger.exception(
            "[Statistics] advanced-statistics failed for %s",
            sanitize_for_log(getattr(current_user, "username", "?")),
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"The advanced statistics could not be built ({type(exc).__name__}). "
                f"The full error and its traceback are in the server log."
            ),
        )


async def _advanced_statistics(db: AsyncSession):
    """Get advanced PostGIS-powered statistics (staff only, cached for 5 minutes)"""
    
    # Check cache first
    cache_key = "advanced_statistics"
    try:
        if redis_client:
            cached = await redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                data["cached_at"] = data.get("cached_at")
                return AdvancedStatisticsResponse(**data)
    except Exception:
        pass  # Redis unavailable

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    
    # ========== Basic Counts ==========
    
    total_result = await db.execute(select(func.count(ServiceRequest.id)).where(ServiceRequest.deleted_at.is_(None)))
    total_count = total_result.scalar() or 0
    
    open_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(ServiceRequest.deleted_at.is_(None), ServiceRequest.status == "open")
    )
    open_count = open_result.scalar() or 0
    
    in_progress_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(ServiceRequest.deleted_at.is_(None), ServiceRequest.status == "in_progress")
    )
    in_progress_count = in_progress_result.scalar() or 0
    
    closed_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(ServiceRequest.deleted_at.is_(None), ServiceRequest.status == "closed")
    )
    closed_count = closed_result.scalar() or 0
    
    # ========== Temporal Analytics ==========
    
    # Requests by hour of day
    hour_query = select(
        extract('hour', ServiceRequest.requested_datetime).label('hour'),
        func.count(ServiceRequest.id)
    ).where(ServiceRequest.deleted_at.is_(None)).group_by('hour')
    hour_result = await db.execute(hour_query)
    requests_by_hour = {int(row[0]): row[1] for row in hour_result.all() if row[0] is not None}
    # Fill missing hours with 0
    for h in range(24):
        if h not in requests_by_hour:
            requests_by_hour[h] = 0
    
    # Requests by day of week
    dow_query = select(
        extract('dow', ServiceRequest.requested_datetime).label('dow'),
        func.count(ServiceRequest.id)
    ).where(ServiceRequest.deleted_at.is_(None)).group_by('dow')
    dow_result = await db.execute(dow_query)
    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    requests_by_day_of_week = {}
    for row in dow_result.all():
        if row[0] is not None:
            requests_by_day_of_week[day_names[int(row[0])]] = row[1]
    for day in day_names:
        if day not in requests_by_day_of_week:
            requests_by_day_of_week[day] = 0
    
    # Requests by month (last 12 months)
    month_query = select(
        func.to_char(ServiceRequest.requested_datetime, 'YYYY-MM').label('month'),
        func.count(ServiceRequest.id)
    ).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.requested_datetime >= now - timedelta(days=365)
    ).group_by('month').order_by('month')
    month_result = await db.execute(month_query)
    requests_by_month = {row[0]: row[1] for row in month_result.all() if row[0]}
    
    # Average resolution hours by category
    resolution_query = select(
        ServiceRequest.service_name,
        func.avg(
            extract('epoch', ServiceRequest.closed_datetime - ServiceRequest.requested_datetime) / 3600
        ).label('avg_hours')
    ).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.status == "closed",
        ServiceRequest.closed_datetime.isnot(None)
    ).group_by(ServiceRequest.service_name)
    resolution_result = await db.execute(resolution_query)
    avg_resolution_hours_by_category = {
        row[0]: round(float(row[1]), 2) if row[1] else 0 
        for row in resolution_result.all() if row[0]
    }
    
    # ========== Geospatial Analytics (PostGIS) ==========
    
    # Hotspot detection using PostGIS ST_ClusterDBSCAN
    # Cluster points within 500m with minimum 2 points per cluster
    hotspots = []
    try:
        # Get clusters with addresses, categories, and unique reporter count
        hotspot_query = text("""
            WITH clustered AS (
                SELECT 
                    id, lat, long, address, service_name, email,
                    ST_ClusterDBSCAN(location, eps := 0.005, minpoints := 2) OVER () as cluster_id
                FROM service_requests
                WHERE deleted_at IS NULL 
                AND location IS NOT NULL
            ),
            cluster_stats AS (
                SELECT 
                    cluster_id,
                    AVG(lat) as center_lat,
                    AVG(long) as center_lng,
                    COUNT(*) as point_count,
                    COUNT(DISTINCT email) as unique_reporters,
                    (ARRAY_AGG(address ORDER BY id DESC))[1] as sample_address,
                    (ARRAY_AGG(DISTINCT service_name))[1:3] as top_categories
                FROM clustered
                WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id
            )
            SELECT center_lat, center_lng, point_count, cluster_id, sample_address, top_categories, unique_reporters
            FROM cluster_stats
            ORDER BY point_count DESC
            LIMIT 10
        """)
        hotspot_result = await db.execute(hotspot_query)
        for row in hotspot_result.mappings().all():
            hotspots.append(HotspotData(
                lat=float(row['center_lat']),
                lng=float(row['center_lng']),
                count=int(row['point_count']),
                cluster_id=int(row['cluster_id']),
                sample_address=row.get('sample_address'),
                top_categories=row.get('top_categories') or [],
                unique_reporters=int(row['unique_reporters']) if row.get('unique_reporters') else None
            ))
    except Exception as e:
        logger.warning(f"Hotspot query failed (PostGIS may not be enabled): {e}")
    
    # Geographic center
    center_query = select(
        func.avg(ServiceRequest.lat),
        func.avg(ServiceRequest.long)
    ).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.lat.isnot(None),
        ServiceRequest.long.isnot(None)
    )
    center_result = await db.execute(center_query)
    center_row = center_result.one_or_none()
    geographic_center = None
    if center_row and center_row[0] and center_row[1]:
        geographic_center = {"lat": float(center_row[0]), "lng": float(center_row[1])}
    
    # Geospatial metrics in imperial units (miles)
    # 1 meter = 0.000621371 miles, 1 sq meter = 0.0000003861 sq miles
    geographic_spread_miles = None
    total_coverage_sq_miles = None
    avg_distance_from_center_miles = None
    furthest_request_miles = None
    
    try:
        geo_metrics_query = text("""
            WITH centroid AS (
                SELECT ST_Centroid(ST_Collect(location))::geography as center
                FROM service_requests 
                WHERE deleted_at IS NULL AND location IS NOT NULL
            ),
            distances AS (
                SELECT 
                    ST_Distance(location::geography, (SELECT center FROM centroid)) as dist_meters
                FROM service_requests
                WHERE deleted_at IS NULL AND location IS NOT NULL
            )
            SELECT 
                STDDEV(dist_meters) * 0.000621371 as spread_miles,
                AVG(dist_meters) * 0.000621371 as avg_distance_miles,
                MAX(dist_meters) * 0.000621371 as max_distance_miles,
                (SELECT ST_Area(ST_ConvexHull(ST_Collect(location))::geography) * 0.0000003861 
                 FROM service_requests WHERE deleted_at IS NULL AND location IS NOT NULL) as coverage_sq_miles
            FROM distances
        """)
        geo_result = await db.execute(geo_metrics_query)
        geo_row = geo_result.mappings().one_or_none()
        if geo_row:
            geographic_spread_miles = round(float(geo_row['spread_miles']), 2) if geo_row['spread_miles'] else None
            avg_distance_from_center_miles = round(float(geo_row['avg_distance_miles']), 2) if geo_row['avg_distance_miles'] else None
            furthest_request_miles = round(float(geo_row['max_distance_miles']), 2) if geo_row['max_distance_miles'] else None
            total_coverage_sq_miles = round(float(geo_row['coverage_sq_miles']), 2) if geo_row['coverage_sq_miles'] else None
    except Exception as e:
        logger.warning(f"Geographic metrics query failed: {e}")
    
    # ========== Department Analytics ==========
    
    dept_result = await db.execute(select(Department))
    departments = dept_result.scalars().all()
    
    department_metrics = []
    for dept in departments:
        dept_total = await db.execute(
            select(func.count(ServiceRequest.id)).where(
                ServiceRequest.deleted_at.is_(None),
                ServiceRequest.assigned_department_id == dept.id
            )
        )
        dept_total_count = dept_total.scalar() or 0
        
        dept_open = await db.execute(
            select(func.count(ServiceRequest.id)).where(
                ServiceRequest.deleted_at.is_(None),
                ServiceRequest.assigned_department_id == dept.id,
                ServiceRequest.status == "open"
            )
        )
        dept_open_count = dept_open.scalar() or 0
        
        dept_closed = await db.execute(
            select(func.count(ServiceRequest.id)).where(
                ServiceRequest.deleted_at.is_(None),
                ServiceRequest.assigned_department_id == dept.id,
                ServiceRequest.status == "closed"
            )
        )
        dept_closed_count = dept_closed.scalar() or 0
        
        # Average resolution time for department
        dept_resolution = await db.execute(
            select(func.avg(
                extract('epoch', ServiceRequest.closed_datetime - ServiceRequest.requested_datetime) / 3600
            )).where(
                ServiceRequest.deleted_at.is_(None),
                ServiceRequest.assigned_department_id == dept.id,
                ServiceRequest.status == "closed",
                ServiceRequest.closed_datetime.isnot(None)
            )
        )
        dept_avg_hours = dept_resolution.scalar()
        
        department_metrics.append(DepartmentMetrics(
            name=dept.name,
            total_requests=dept_total_count,
            open_requests=dept_open_count,
            avg_resolution_hours=round(float(dept_avg_hours), 2) if dept_avg_hours else None,
            resolution_rate=round(dept_closed_count / dept_total_count * 100, 1) if dept_total_count > 0 else 0
        ))
    
    # Top staff by resolutions
    staff_query = select(
        ServiceRequest.assigned_to,
        func.count(ServiceRequest.id)
    ).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.status == "closed",
        ServiceRequest.assigned_to.isnot(None),
        ServiceRequest.assigned_to != ""
    ).group_by(ServiceRequest.assigned_to).order_by(func.count(ServiceRequest.id).desc()).limit(10)
    staff_result = await db.execute(staff_query)
    top_staff_by_resolutions = {row[0]: row[1] for row in staff_result.all() if row[0]}
    
    # ========== Performance Metrics ==========
    
    # Average resolution time overall
    overall_resolution = await db.execute(
        select(func.avg(
            extract('epoch', ServiceRequest.closed_datetime - ServiceRequest.requested_datetime) / 3600
        )).where(
            ServiceRequest.deleted_at.is_(None),
            ServiceRequest.status == "closed",
            ServiceRequest.closed_datetime.isnot(None)
        )
    )
    avg_resolution_hours = overall_resolution.scalar()
    if avg_resolution_hours:
        avg_resolution_hours = round(float(avg_resolution_hours), 2)
    
    # Backlog by age
    open_requests_query = select(ServiceRequest.requested_datetime).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.status.in_(["open", "in_progress"])
    )
    open_requests_result = await db.execute(open_requests_query)
    backlog_by_age = bucket_ages((row[0] for row in open_requests_result.all()), now)
    
    # Resolution rate (fixed: proper completion rate)
    # This is the percentage of all requests that have been successfully closed
    resolution_rate = round(closed_count / total_count * 100, 1) if total_count > 0 else 0
    
    # Category distribution
    category_query = select(
        ServiceRequest.service_name,
        func.count(ServiceRequest.id)
    ).where(ServiceRequest.deleted_at.is_(None)).group_by(ServiceRequest.service_name)
    category_result = await db.execute(category_query)
    requests_by_category = {row[0]: row[1] for row in category_result.all() if row[0]}
    
    # Flagged count
    flagged_result = await db.execute(
        select(func.count(ServiceRequest.id)).where(
            ServiceRequest.deleted_at.is_(None),
            ServiceRequest.flagged == True
        )
    )
    flagged_count = flagged_result.scalar() or 0
    
    # ========== Infrastructure Metrics ==========
    
    # Backlog by priority (current open + in_progress)
    priority_query = select(
        ServiceRequest.priority,
        func.count(ServiceRequest.id)
    ).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.status.in_(["open", "in_progress"])
    ).group_by(ServiceRequest.priority)
    priority_result = await db.execute(priority_query)
    backlog_by_priority = {int(row[0]): row[1] for row in priority_result.all() if row[0]}
    # Ensure all priorities 1-10 are represented
    for p in range(1, 11):
        if p not in backlog_by_priority:
            backlog_by_priority[p] = 0
    
    # Current workload by staff (active assignments)
    workload_query = select(
        ServiceRequest.assigned_to,
        func.count(ServiceRequest.id)
    ).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.status.in_(["open", "in_progress"]),
        ServiceRequest.assigned_to.isnot(None),
        ServiceRequest.assigned_to != ""
    ).group_by(ServiceRequest.assigned_to)
    workload_result = await db.execute(workload_query)
    workload_by_staff = {row[0]: row[1] for row in workload_result.all() if row[0]}
    
    # SLA tracking (open requests only, by age)
    open_only_query = select(ServiceRequest.requested_datetime).where(
        ServiceRequest.deleted_at.is_(None),
        ServiceRequest.status == "open"  # Only "open" status, not in_progress
    )
    open_only_result = await db.execute(open_only_query)
    open_by_age_sla = bucket_ages((row[0] for row in open_only_result.all()), now)
    
    # ========== Predictive & Government Analytics ==========
    
    # Labor cost rates (hourly, in dollars)
    LABOR_RATES = {
        "Pothole": 50, "Street Repair": 75, "Snow Removal": 50,
        "Sewer": 85, "Water": 85, "Traffic Signal": 65,
        "Drainage": 70, "Road Maintenance": 65
    }
    DEFAULT_LABOR_RATE = 55
    
    # Cost estimates by category
    cost_estimates = []
    for category, total_cat_count in requests_by_category.items():
        # Get avg resolution hours for this category
        avg_hours = avg_resolution_hours_by_category.get(category, 2.5)
        labor_rate = LABOR_RATES.get(category, DEFAULT_LABOR_RATE)
        estimated_cost = avg_hours * labor_rate
        
        # Count open tickets in this category
        open_cat_query = await db.execute(
            select(func.count(ServiceRequest.id)).where(
                ServiceRequest.deleted_at.is_(None),
                ServiceRequest.service_name == category,
                ServiceRequest.status.in_(["open", "in_progress"])
            )
        )
        open_in_category = open_cat_query.scalar() or 0
        
        cost_estimates.append(CostEstimate(
            category=category,
            avg_hours=round(avg_hours, 2),
            estimated_cost=round(estimated_cost, 2),
            open_tickets=open_in_category,
            total_estimated_cost=round(open_in_category * estimated_cost, 2)
        ))
    
    # Sort by total cost descending
    cost_estimates.sort(key=lambda x: x.total_estimated_cost, reverse=True)
    
    # Repeat locations (infrastructure maintenance indicators)
    repeat_locations = []
    try:
        repeat_query = text("""
            SELECT address, lat, long, COUNT(*) as request_count
            FROM service_requests
            WHERE deleted_at IS NULL 
            AND address IS NOT NULL
            AND lat IS NOT NULL
            AND long IS NOT NULL
            GROUP BY address, lat, long
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """)
        repeat_result = await db.execute(repeat_query)
        for row in repeat_result.mappings().all():
            if row['address'] and row['lat'] and row['long']:
                repeat_locations.append(RepeatLocation(
                    address=str(row['address']),
                    lat=float(row['lat']),
                    lng=float(row['long']),
                    request_count=int(row['request_count'])
                ))
    except Exception as e:
        logger.warning(f"Repeat locations query failed: {e}")
    
    # High-priority aging: requests still unresolved (open or in progress) for
    # more than 7 days whose *effective* priority is high (>= 8 on the 1-10 scale
    # the rest of the app uses). Effective priority mirrors the UI: the
    # human-approved manual_priority_score if set, else the AI suggestion in
    # ai_analysis, else the neutral default of 5. The legacy `priority` column is
    # unused (always its default), so it must not be used here.
    aging_candidates = await db.execute(
        select(ServiceRequest.manual_priority_score, ServiceRequest.ai_analysis).where(
            ServiceRequest.deleted_at.is_(None),
            ServiceRequest.status.in_(["open", "in_progress"]),
            ServiceRequest.requested_datetime < now - timedelta(days=7),
        )
    )
    aging_high_priority_count = 0
    for manual_score, ai in aging_candidates.all():
        if manual_score is not None:
            effective = manual_score
        elif isinstance(ai, dict) and ai.get("priority_score") is not None:
            effective = ai.get("priority_score")
        else:
            effective = 5
        try:
            if float(effective) >= 8:
                aging_high_priority_count += 1
        except (TypeError, ValueError):
            # A non-numeric priority in ai_analysis is bad data, not a high
            # priority. Skip the row rather than counting or raising on it.
            pass
    
    # ========== Trends ==========
    
    # Weekly trend (last 8 weeks)
    weekly_trend = []
    for i in range(7, -1, -1):
        week_start = now - timedelta(weeks=i+1)
        week_end = now - timedelta(weeks=i)
        # Label each point with the week's start date (e.g. "Jul 21") rather than
        # a generic "W1"..."W8", so the axis and tooltip show real dates.
        week_label = f"{week_start.strftime('%b')} {week_start.day}"
        
        week_stats = {"period": week_label, "open": 0, "in_progress": 0, "closed": 0, "total": 0}
        for status in ["open", "in_progress", "closed"]:
            count_result = await db.execute(
                select(func.count(ServiceRequest.id)).where(
                    ServiceRequest.deleted_at.is_(None),
                    ServiceRequest.status == status,
                    ServiceRequest.requested_datetime >= week_start,
                    ServiceRequest.requested_datetime < week_end
                )
            )
            week_stats[status] = count_result.scalar() or 0
        week_stats["total"] = week_stats["open"] + week_stats["in_progress"] + week_stats["closed"]
        weekly_trend.append(TrendData(**week_stats))
    
    # Monthly trend (last 6 months)
    monthly_trend = []
    for i in range(5, -1, -1):
        month_start = (now.replace(day=1) - timedelta(days=30*i)).replace(day=1)
        if i > 0:
            month_end = (now.replace(day=1) - timedelta(days=30*(i-1))).replace(day=1)
        else:
            month_end = now
        month_label = month_start.strftime("%b")
        
        month_stats = {"period": month_label, "open": 0, "in_progress": 0, "closed": 0, "total": 0}
        for status in ["open", "in_progress", "closed"]:
            count_result = await db.execute(
                select(func.count(ServiceRequest.id)).where(
                    ServiceRequest.deleted_at.is_(None),
                    ServiceRequest.status == status,
                    ServiceRequest.requested_datetime >= month_start,
                    ServiceRequest.requested_datetime < month_end
                )
            )
            month_stats[status] = count_result.scalar() or 0
        month_stats["total"] = month_stats["open"] + month_stats["in_progress"] + month_stats["closed"]
        monthly_trend.append(TrendData(**month_stats))
    
    # Predictive insights
    # Volume forecast (simple moving average of last 4 weeks)
    if len(weekly_trend) >= 4:
        recent_volumes = [w.total for w in weekly_trend[-4:]]
        volume_forecast_next_week = int(sum(recent_volumes) / len(recent_volumes))
    else:
        volume_forecast_next_week = 0
    
    # Trend direction (compare last 2 weeks vs previous 2 weeks)
    if len(weekly_trend) >= 4:
        recent_avg = sum(w.total for w in weekly_trend[-2:]) / 2
        previous_avg = sum(w.total for w in weekly_trend[-4:-2]) / 2
        if recent_avg > previous_avg * 1.1:
            trend_direction = "increasing"
        elif recent_avg < previous_avg * 0.9:
            trend_direction = "decreasing"
        else:
            trend_direction = "stable"
    else:
        trend_direction = "stable"
    
    # Seasonal patterns
    peak_day = max(requests_by_day_of_week.items(), key=lambda x: x[1])[0] if requests_by_day_of_week else "Monday"
    peak_month = max(requests_by_month.items(), key=lambda x: x[1])[0] if requests_by_month else "January"
    if peak_month:
        # Extract month name from YYYY-MM format
        try:
            from datetime import datetime as dt, timezone
            peak_month = dt.strptime(peak_month, "%Y-%m").strftime("%B")
        except Exception:
            pass  # Month format conversion failed, keep original
    
    predictive_insights = PredictiveInsights(
        volume_forecast_next_week=volume_forecast_next_week,
        trend_direction=trend_direction,
        seasonal_peak_day=peak_day,
        seasonal_peak_month=peak_month
    )
    
    # Build response
    response_data = AdvancedStatisticsResponse(
        total_requests=total_count,
        open_requests=open_count,
        in_progress_requests=in_progress_count,
        closed_requests=closed_count,
        requests_by_hour=requests_by_hour,
        requests_by_day_of_week=requests_by_day_of_week,
        requests_by_month=requests_by_month,
        avg_resolution_hours_by_category=avg_resolution_hours_by_category,
        hotspots=hotspots,
        geographic_center=geographic_center,
        geographic_spread_miles=geographic_spread_miles,
        total_coverage_sq_miles=total_coverage_sq_miles,
        avg_distance_from_center_miles=avg_distance_from_center_miles,
        furthest_request_miles=furthest_request_miles,
        requests_density_by_zone={},
        department_metrics=department_metrics,
        top_staff_by_resolutions=top_staff_by_resolutions,
        avg_resolution_hours=avg_resolution_hours,
        avg_first_response_hours=None,  # Would need audit log analysis
        backlog_by_age=backlog_by_age,
        resolution_rate=resolution_rate,
        backlog_by_priority=backlog_by_priority,
        workload_by_staff=workload_by_staff,
        open_by_age_sla=open_by_age_sla,
        predictive_insights=predictive_insights,
        cost_estimates=cost_estimates,
        avg_response_time_hours=None,  # Would need comment/audit log analysis
        repeat_locations=repeat_locations,
        aging_high_priority_count=aging_high_priority_count,
        requests_by_category=requests_by_category,
        flagged_count=flagged_count,
        weekly_trend=weekly_trend,
        monthly_trend=monthly_trend,
        cached_at=now
    )
    
    # Cache the result
    try:
        if redis_client:
            cache_data = response_data.model_dump()
            cache_data["cached_at"] = now.isoformat()
            await redis_client.setex(cache_key, STATS_CACHE_TTL, json.dumps(cache_data, default=str))
    except Exception:
        pass  # Redis cache write failed, non-critical
    
    return response_data


# ============ Spatial Bias Heatmap ============


@router.get("/heatmap-data", response_model=HeatmapDataResponse)
async def get_heatmap_data(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_staff)
):
    """
    Return two sets of weighted coordinates for spatial bias detection:
    - report_points: every request location (weight 1)
    - reporter_points: deduplicated by reporter email, rounded to ~100m grid

    Comparing the two heatmaps reveals areas where many reports come from
    few unique reporters (potential reporting bias / squeaky wheel effect).
    """
    from sqlalchemy import text as sa_text

    # Check cache
    cache_key = "heatmap_data"
    try:
        if redis_client:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception:
        # Cache read is an optimisation; on any failure fall through and
        # recompute from the database.
        pass

    # All request coordinates (for "reports" heatmap)
    # Rounded to ~100m grid to avoid pinpointing individual addresses
    report_query = sa_text("""
        SELECT ROUND(lat::numeric, 3) as lat, ROUND(long::numeric, 3) as lng
        FROM service_requests
        WHERE deleted_at IS NULL
          AND lat IS NOT NULL
          AND long IS NOT NULL
    """)
    report_result = await db.execute(report_query)
    report_points = [
        {"lat": float(r["lat"]), "lng": float(r["lng"]), "weight": 1}
        for r in report_result.mappings().all()
    ]

    # Deduplicated reporter locations (rounded to ~100m grid cells)
    # Each unique (email, grid_cell) pair counts as 1 point
    # ROUND(lat, 3) ≈ 111m, ROUND(long, 3) ≈ 85m at mid-latitudes
    reporter_query = sa_text("""
        SELECT
            ROUND(lat::numeric, 3) as lat,
            ROUND(long::numeric, 3) as lng,
            COUNT(*) as weight
        FROM (
            SELECT DISTINCT ON (COALESCE(email, id::text), ROUND(lat::numeric, 3), ROUND(long::numeric, 3))
                lat, long, email, id
            FROM service_requests
            WHERE deleted_at IS NULL
              AND lat IS NOT NULL
              AND long IS NOT NULL
        ) unique_reporters
        GROUP BY ROUND(lat::numeric, 3), ROUND(long::numeric, 3)
    """)
    reporter_result = await db.execute(reporter_query)
    reporter_points = [
        {"lat": float(r["lat"]), "lng": float(r["lng"]), "weight": int(r["weight"])}
        for r in reporter_result.mappings().all()
    ]

    result = {
        "report_points": report_points,
        "reporter_points": reporter_points,
        "total_reports": len(report_points),
        "total_unique_reporters": len(reporter_points),
    }

    # Cache for 5 minutes
    try:
        if redis_client:
            await redis_client.setex(cache_key, STATS_CACHE_TTL, json.dumps(result))
    except Exception:
        # Failing to cache costs the next caller a recompute. The result is
        # already correct and must still be returned.
        pass

    return result


# ============ System Update ============


def _reject_if_managed():
    """In state-hosted (managed) mode, in-app self-update is disabled — upgrades
    come only from the orchestrator (publish image → panel rolls out). This also
    removes the biggest infra risk (git pull + docker compose via a mounted
    Docker socket) from the hosted fleet."""
    from app.core.config import get_settings
    if get_settings().managed_mode:
        raise HTTPException(
            status_code=403,
            detail="Updates are managed by your state's hosting platform and can't be triggered from here.",
        )


@router.post("/update")
async def update_system(_: User = Depends(get_current_admin)):
    """Pull updates from GitHub (admin only). Code changes reload automatically."""
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Upgrading")  # hosted upgrades come only from the orchestrator (A2)
    try:
        # Get the project root
        project_root = os.environ.get("PROJECT_ROOT", "/project")
        
        # Add safe directory to fix ownership issues in Docker
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", project_root],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Pull latest code
        pull_result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if pull_result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Git pull failed: {pull_result.stderr}"
            )
        
        # Check what was updated
        git_output = pull_result.stdout.strip()
        
        # Determine if restart is needed
        needs_restart = any(x in git_output.lower() for x in [
            'requirements.txt', 'dockerfile', 'docker-compose', 'package.json'
        ])
        
        return {
            "status": "success",
            "message": "Updates pulled successfully. " + (
                "Container restart may be needed for dependency changes." 
                if needs_restart 
                else "Code changes will reload automatically."
            ),
            "git_output": git_output,
            "needs_restart": needs_restart
        }
    
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Update operation timed out"
        )
    except Exception as e:
        logger.error(f"Update operation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update operation failed"
        )


# ============ Version Switcher ============

GITHUB_REPO = "Pinpoint-311/Pinpoint-311"
GITHUB_API_BASE = "https://api.github.com"


@router.get("/current-version")
async def get_current_version(_: User = Depends(get_current_admin)):
    """Get current git version information (admin only)."""
    try:
        project_root = os.environ.get("PROJECT_ROOT", "/project")
        
        # Add safe directory to fix ownership issues in Docker
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", project_root],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Get current commit SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        if sha_result.returncode != 0:
            logger.warning(f"Git rev-parse failed: {sha_result.stderr}")
        current_sha = sha_result.stdout.strip()[:7] if sha_result.returncode == 0 else "unknown"
        
        # Get current tag (if on a tag)
        tag_result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        current_tag = tag_result.stdout.strip() if tag_result.returncode == 0 else None
        
        # Get commit date
        date_result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        commit_date = date_result.stdout.strip() if date_result.returncode == 0 else None
        
        # Get commit message (first line)
        msg_result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        commit_message = msg_result.stdout.strip()[:60] if msg_result.returncode == 0 else None
        
        return {
            "sha": current_sha,
            "tag": current_tag,
            "commit_date": commit_date,
            "commit_message": commit_message,
            "display": current_tag or f"@{current_sha}"
        }
    except Exception as e:
        logger.error(f"Failed to get current version: {e}")
        return {"sha": "unknown", "tag": None, "commit_date": None, "display": "@unknown"}


@router.get("/releases")
async def get_releases(_: User = Depends(get_current_admin)):
    """Fetch available releases from GitHub (admin only)."""
    import httpx
    
    logger.info(f"[Releases] Starting fetch from GitHub API")
    logger.info(f"[Releases] GITHUB_API_BASE={GITHUB_API_BASE}, GITHUB_REPO={GITHUB_REPO}")
    
    try:
        releases = []
        recent_commits = []
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Fetch releases
            releases_url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/releases"
            logger.info(f"[Releases] Fetching releases from: {releases_url}")
            response = await client.get(
                releases_url,
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            logger.info(f"[Releases] Releases response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    for r in data:
                        releases.append({
                            "tag": r.get("tag_name", "unknown"),
                            "name": r.get("name") or r.get("tag_name", "unknown"),
                            "body": r.get("body") or "No release notes.",
                            "published_at": r.get("published_at"),
                            "author": r.get("author", {}).get("login") if r.get("author") else None,
                            "html_url": r.get("html_url", ""),
                            "prerelease": r.get("prerelease", False),
                            "target_commitish": r.get("target_commitish")
                        })
            else:
                logger.warning(f"GitHub releases API returned {response.status_code}")
            
            # Also get recent commits from main for unreleased versions
            commits_response = await client.get(
                f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/commits",
                params={"per_page": 15, "sha": "main"},
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            
            if commits_response.status_code == 200:
                data = commits_response.json()
                if isinstance(data, list):
                    for commit in data:
                        c = commit.get("commit", {})
                        recent_commits.append({
                            "sha": commit.get("sha", "")[:7],
                            "full_sha": commit.get("sha", ""),
                            "message": c.get("message", "").split("\n")[0][:80],
                            "date": c.get("committer", {}).get("date", ""),
                            "author": c.get("author", {}).get("name", "Unknown")
                        })
            else:
                logger.warning(f"GitHub commits API returned {commits_response.status_code}")
            
            return {
                "releases": releases,
                "recent_commits": recent_commits
            }
    except httpx.TimeoutException:
        logger.warning("GitHub API request timed out")
        return {"releases": [], "recent_commits": []}
    except Exception as e:
        logger.error(f"Failed to fetch releases: {e}")
        return {"releases": [], "recent_commits": []}


@router.get("/releases/{ref}/security")
async def get_release_security(ref: str, _: User = Depends(get_current_admin)):
    """
    Fetch security verification status for a specific release/commit (admin only).
    Returns workflow run status for security scans, CodeQL, accessibility, etc.
    """
    import httpx
    
    # Workflow names to check - must match exactly the 'name:' field in each workflow file
    # Note: Accessibility only runs on PRs and schedule, not direct pushes
    SECURITY_WORKFLOWS = {
        "Security Scan (OWASP ZAP)": {"icon": "🛡️", "key": "owasp_zap"},
        "CodeQL Security Analysis": {"icon": "🔒", "key": "codeql"},
        "Build and Publish Docker Images": {"icon": "📦", "key": "docker_build"},
        "Accessibility Audit (axe-core)": {"icon": "♿", "key": "accessibility"}
    }
    
    try:
        import re
        # Validate ref to prevent SSRF via URL manipulation
        if not re.match(r'^[a-zA-Z0-9._\-/]+$', ref):
            raise HTTPException(status_code=400, detail="Invalid ref format")
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # First, resolve the ref to a commit SHA if it's a tag
            commit_sha = ref
            if ref.startswith("v") or not ref.isalnum():
                # It's likely a tag, resolve to SHA
                tag_response = await client.get(
                    f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/git/refs/tags/{ref}",
                    headers={"Accept": "application/vnd.github.v3+json"}
                )
                if tag_response.status_code == 200:
                    tag_data = tag_response.json()
                    # Handle both lightweight and annotated tags
                    if tag_data["object"]["type"] == "commit":
                        commit_sha = tag_data["object"]["sha"]
                    else:
                        # Annotated tag - need to dereference
                        annotated_response = await client.get(
                            tag_data["object"]["url"],
                            headers={"Accept": "application/vnd.github.v3+json"}
                        )
                        if annotated_response.status_code == 200:
                            commit_sha = annotated_response.json().get("object", {}).get("sha", ref)
            
            # Get workflow runs for this commit
            runs_response = await client.get(
                f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/actions/runs",
                params={"head_sha": commit_sha, "per_page": 50},
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            
            verification = {}
            
            if runs_response.status_code == 200:
                runs = runs_response.json().get("workflow_runs", [])
                
                for workflow_name, info in SECURITY_WORKFLOWS.items():
                    matching_runs = [r for r in runs if r["name"] == workflow_name]
                    
                    if matching_runs:
                        # Get the most recent run for this workflow
                        latest_run = matching_runs[0]
                        verification[info["key"]] = {
                            "name": workflow_name,
                            "icon": info["icon"],
                            "status": latest_run["status"],  # queued, in_progress, completed
                            "conclusion": latest_run.get("conclusion"),  # success, failure, neutral, cancelled, skipped, timed_out
                            "run_url": latest_run["html_url"],
                            "run_id": latest_run["id"],
                            "created_at": latest_run["created_at"],
                            "passed": latest_run.get("conclusion") == "success"
                        }
                    else:
                        verification[info["key"]] = {
                            "name": workflow_name,
                            "icon": info["icon"],
                            "status": "not_found",
                            "conclusion": None,
                            "run_url": None,
                            "passed": None
                        }
            
            # Calculate overall security score
            checks = [v for v in verification.values() if v.get("conclusion") is not None]
            passed_checks = len([v for v in checks if v.get("passed")])
            total_checks = len(checks)
            
            return {
                "ref": ref,
                "commit_sha": commit_sha[:7] if len(commit_sha) > 7 else commit_sha,
                "verification": verification,
                "summary": {
                    "passed": passed_checks,
                    "total": total_checks,
                    "all_passed": passed_checks == total_checks and total_checks > 0,
                    "score": f"{passed_checks}/{total_checks}" if total_checks > 0 else "N/A"
                }
            }
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="GitHub API request timed out"
        )
    except Exception as e:
        logger.error(f"Failed to fetch security status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch security status"
        )


@router.post("/switch-version")
async def switch_version(
    ref: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Production-grade version deployment with automatic rollback.
    Disabled in managed mode — rollouts come only from the orchestrator (A2).

    This endpoint performs a full deployment cycle:
    1. Save rollback point (current git HEAD)
    2. Create database backup (pg_dump)
    3. Git checkout target version
    4. Run database migrations (alembic upgrade)
    5. Rebuild and restart all containers
    6. Health check the new deployment
    7. Automatic rollback on any failure
    """
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Upgrading")
    import httpx
    from datetime import datetime, timezone

    project_root = os.environ.get("PROJECT_ROOT", "/project")
    deployment_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = "/project/backups"
    
    # Auto-detect the Docker Compose project name from running containers
    # IMPORTANT: Multiple compose projects may be running (production + demo instances).
    # We must identify the production project by matching its ConfigFiles to our project_root.
    try:
        project_name_result = subprocess.run(
            ["docker", "compose", "ls", "--format", "json"],
            capture_output=True, text=True, timeout=10
        )
        import json as _json
        if project_name_result.returncode == 0:
            projects = _json.loads(project_name_result.stdout)
            compose_project = "wwf-open-source-311-template"  # sensible default
            
            # Find the project whose config files match our project root
            for proj in projects:
                config_files = proj.get("ConfigFiles", "")
                # The production project's docker-compose.yml lives in project_root's parent
                # e.g. /home/ubuntu/WWF-Open-Source-311-Template/docker-compose.yml
                if project_root in config_files or "WWF-Open-Source-311-Template/docker-compose.yml" in config_files:
                    # Skip demo projects (their names contain "demo" or "p311demo")
                    if "demo" not in proj["Name"].lower():
                        compose_project = proj["Name"]
                        break
        else:
            compose_project = "wwf-open-source-311-template"
    except Exception:
        compose_project = "wwf-open-source-311-template"
    
    logger.info(f"[Deploy] Using compose project: {compose_project}")
    
    # Deployment state tracking
    state = {
        "original_sha": None,
        "backup_file": None,
        "migrations_run": False,
        "containers_rebuilt": False,
        "steps_completed": [],
        "errors": []
    }
    
    def log_step(step: str, success: bool = True, detail: str = ""):
        state["steps_completed"].append({
            "step": step,
            "success": success,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        if not success:
            state["errors"].append(f"{step}: {detail}")
    
    async def rollback():
        """Rollback to original state on failure."""
        rollback_errors = []
        
        # 1. Restore git to original SHA
        if state["original_sha"]:
            try:
                subprocess.run(
                    ["git", "checkout", state["original_sha"]],
                    cwd=project_root,
                    capture_output=True,
                    timeout=60
                )
                log_step("rollback_git", True, f"Restored to {state['original_sha'][:7]}")
            except Exception as e:
                rollback_errors.append(f"Git rollback failed: {e}")
        
        # 2. Note: We don't auto-restore database because migrations are forward-only
        # and designed to be non-destructive. Admin should review if needed.
        if state["migrations_run"]:
            log_step("rollback_db", True, "Migrations were forward-only (additive). Manual review recommended if issues persist.")
        
        # 3. Restart original containers
        try:
            rollback_cmd = ["docker", "compose", "-p", compose_project]
            prod_compose = os.path.join(project_root, "docker-compose.prod.yml")
            if os.path.exists(prod_compose):
                rollback_cmd.extend(["-f", "docker-compose.yml", "-f", "docker-compose.prod.yml"])
            rollback_cmd.extend(["up", "-d", "--force-recreate", "backend", "frontend"])
            subprocess.run(
                rollback_cmd,
                cwd=project_root,
                capture_output=True,
                timeout=120
            )
            log_step("rollback_containers", True, "Restarted containers with original code")
        except Exception as e:
            rollback_errors.append(f"Container restart failed: {e}")
        
        return rollback_errors
    
    try:
        # ===== STEP 0: Setup =====
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", project_root],
            cwd=project_root,
            capture_output=True,
            timeout=10
        )
        
        # Create backup directory if it doesn't exist
        os.makedirs(backup_dir, exist_ok=True)
        
        # ===== STEP 1: Save Rollback Point =====
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        if sha_result.returncode != 0:
            raise Exception("Failed to get current git HEAD")
        
        state["original_sha"] = sha_result.stdout.strip()
        log_step("save_rollback_point", True, f"Saved: {state['original_sha'][:7]}")
        
        # ===== STEP 2: Create Database Backup =====
        backup_file = f"{backup_dir}/db_backup_{deployment_id}.sql"
        state["backup_file"] = backup_file
        
        # Get database URL from environment
        db_url = os.environ.get("DATABASE_URL", "")
        # Parse connection info (format: postgresql+asyncpg://user:pass@host/db)
        try:
            # Extract host, user, password, dbname from DATABASE_URL
            import re
            match = re.match(r"postgresql\+asyncpg://([^:]+):([^@]+)@([^/]+)/(.+)", db_url)
            if match:
                db_user, db_pass, db_host, db_name = match.groups()
                
                # Run pg_dump
                env = os.environ.copy()
                env["PGPASSWORD"] = db_pass
                
                backup_result = subprocess.run(
                    ["pg_dump", "-h", db_host.split(":")[0], "-U", db_user, "-d", db_name, "-f", backup_file],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 min timeout for large DBs
                )
                
                if backup_result.returncode == 0:
                    # Get backup size
                    backup_size = os.path.getsize(backup_file) if os.path.exists(backup_file) else 0
                    log_step("database_backup", True, f"Created {backup_size // 1024}KB backup")
                else:
                    log_step("database_backup", False, backup_result.stderr[:200])
                    # Continue anyway - backup failure shouldn't block deployment
            else:
                log_step("database_backup", False, "Could not parse DATABASE_URL")
        except Exception as e:
            log_step("database_backup", False, str(e)[:200])
        
        # ===== STEP 3: Git Fetch and Checkout =====
        # Stash any local changes
        subprocess.run(
            ["git", "stash"],
            cwd=project_root,
            capture_output=True,
            timeout=30
        )
        
        # Fetch all
        fetch_result = subprocess.run(
            ["git", "fetch", "--all", "--tags"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if fetch_result.returncode != 0:
            log_step("git_fetch", False, fetch_result.stderr[:200])
            raise Exception(f"Git fetch failed: {fetch_result.stderr}")
        
        log_step("git_fetch", True, "Fetched latest from remote")
        
        # Checkout target ref
        checkout_result = subprocess.run(
            ["git", "checkout", ref],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if checkout_result.returncode != 0:
            log_step("git_checkout", False, checkout_result.stderr[:200])
            raise Exception(f"Git checkout failed: {checkout_result.stderr}")
        
        # Get new SHA
        new_sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        new_sha = new_sha_result.stdout.strip()[:7] if new_sha_result.returncode == 0 else "unknown"
        log_step("git_checkout", True, f"Checked out {new_sha}")
        
        # ===== STEP 4: Run Database Migrations =====
        # Check if alembic.ini exists and run migrations
        alembic_cfg = os.path.join(project_root, "backend", "alembic.ini")
        if os.path.exists(alembic_cfg):
            try:
                migration_result = subprocess.run(
                    ["alembic", "upgrade", "head"],
                    cwd=os.path.join(project_root, "backend"),
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                if migration_result.returncode == 0:
                    state["migrations_run"] = True
                    log_step("database_migrations", True, "Applied pending migrations")
                else:
                    # Check if it's just "no migrations to run"
                    if "already at head" in migration_result.stdout.lower() or "no upgrade" in migration_result.stdout.lower():
                        log_step("database_migrations", True, "No new migrations")
                    else:
                        log_step("database_migrations", False, migration_result.stderr[:200])
                        raise Exception(f"Migration failed: {migration_result.stderr}")
            except subprocess.TimeoutExpired:
                log_step("database_migrations", False, "Migration timed out")
                raise Exception("Database migration timed out")
        else:
            log_step("database_migrations", True, "No alembic config found, skipping")
        
        # ===== STEP 5: Rebuild Containers =====
        try:
            # Detect which compose files are in use
            compose_files = []
            for f in ["docker-compose.yml", "docker-compose.prod.yml"]:
                fpath = os.path.join(project_root, f)
                if os.path.exists(fpath):
                    compose_files.append(f)
            log_step("compose_files_detected", True, ", ".join(compose_files))

            # Check if production compose is in use (uses prebuilt images, no local build)
            prod_compose = os.path.join(project_root, "docker-compose.prod.yml")
            uses_prebuilt = os.path.exists(prod_compose)

            if uses_prebuilt:
                # Production mode: pull latest images from GHCR instead of building locally
                log_step("deploy_mode", True, "Production (prebuilt GHCR images)")

                pull_result = subprocess.run(
                    ["docker", "compose", "-p", compose_project,
                     "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
                     "pull", "frontend", "backend"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=300
                )

                if pull_result.returncode != 0:
                    combined = (pull_result.stderr + pull_result.stdout)[-800:]
                    log_step("image_pull", False, combined)
                    raise Exception(f"Image pull failed: {combined}")

                log_step("image_pull", True, "Pulled latest images from GHCR")

                # Recreate containers with new images
                up_result = subprocess.run(
                    ["docker", "compose", "-p", compose_project,
                     "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
                     "up", "-d", "--force-recreate", "frontend", "backend"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if up_result.returncode != 0:
                    combined = (up_result.stderr + up_result.stdout)[-800:]
                    log_step("container_recreate", False, combined)
                    raise Exception(f"Container recreate failed: {combined}")

                state["containers_rebuilt"] = True
                log_step("container_recreate", True, "Recreated containers with new images")
            else:
                # Dev mode: build locally
                log_step("deploy_mode", True, "Development (local Docker build)")

                build_result = subprocess.run(
                    ["docker", "compose", "-p", compose_project, "build", "--no-cache", "frontend"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=600
                )

                if build_result.returncode != 0:
                    combined = (build_result.stderr + build_result.stdout)[-800:]
                    log_step("container_build", False, combined)
                    raise Exception(f"Container build failed: {combined}")

                log_step("container_build", True, "Built frontend image")

                restart_result = subprocess.run(
                    ["docker", "compose", "-p", compose_project, "up", "-d", "--force-recreate", "frontend"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if restart_result.returncode != 0:
                    combined = (restart_result.stderr + restart_result.stdout)[-800:]
                    log_step("container_restart", False, combined)
                    raise Exception(f"Container restart failed: {combined}")

                state["containers_rebuilt"] = True
                log_step("container_restart", True, "Restarted frontend container with new code")

        except subprocess.TimeoutExpired:
            log_step("container_rebuild", False, "Container rebuild timed out (10 min limit)")
            raise Exception("Container rebuild timed out")
        
        # ===== STEP 6: Health Check =====
        # Wait for containers to be healthy
        import asyncio
        await asyncio.sleep(10)  # Give containers time to start
        
        health_ok = False
        for attempt in range(6):  # 6 attempts over 30 seconds
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    # Check backend health
                    health_resp = await client.get("http://localhost:8000/api/health")
                    if health_resp.status_code == 200:
                        health_ok = True
                        break
            except Exception:
                pass  # Health check attempt failed, will retry
            await asyncio.sleep(5)
        
        if not health_ok:
            log_step("health_check", False, "Backend health check failed after 30s")
            raise Exception("Health check failed - backend not responding")
        
        log_step("health_check", True, "Backend healthy and responding")
        
        # ===== STEP 7: Log to Audit =====
        try:
            audit_entry = AuditLog(
                user_id=current_user.id,
                username=current_user.username if hasattr(current_user, 'username') else str(current_user.id),
                event_type="version_deployed",
                success=True,
                details={
                    "deployment_id": deployment_id,
                    "from_sha": state["original_sha"][:7],
                    "to_sha": new_sha,
                    "to_ref": ref,
                    "backup_file": state["backup_file"],
                    "migrations_run": state["migrations_run"],
                    "steps": state["steps_completed"]
                }
            )
            db.add(audit_entry)
            await db.commit()
        except Exception as e:
            logger.warning(f"Failed to log deployment to audit: {e}")
        
        # ===== SUCCESS =====
        return {
            "status": "success",
            "message": f"Successfully deployed @{new_sha}",
            "deployment_id": deployment_id,
            "from_sha": state["original_sha"][:7],
            "to_sha": new_sha,
            "ref": ref,
            "backup_file": state["backup_file"],
            "migrations_run": state["migrations_run"],
            "steps": state["steps_completed"]
        }
        
    except Exception as e:
        # ===== ROLLBACK ON FAILURE =====
        log_step("deployment_failed", False, str(e)[:800])
        logger.error(f"Deployment failed, initiating rollback: {e}")
        
        rollback_errors = await rollback()
        
        # Log failed deployment to audit
        try:
            audit_entry = AuditLog(
                user_id=current_user.id,
                username=current_user.username if hasattr(current_user, 'username') else str(current_user.id),
                event_type="version_deployment_failed",
                success=False,
                failure_reason=str(e)[:255],
                details={
                    "deployment_id": deployment_id,
                    "target_ref": ref,
                    "error": str(e)[:1000],
                    "rollback_errors": rollback_errors,
                    "steps": state["steps_completed"]
                }
            )
            db.add(audit_entry)
            await db.commit()
        except Exception as audit_error:
            logger.warning(f"Failed to log deployment failure: {audit_error}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Deployment failed and rollback initiated",
                "error": str(e)[:1500],
                "rollback_performed": True,
                "rollback_errors": rollback_errors,
                "backup_available": state["backup_file"],
                "steps": state["steps_completed"],
                "deployment_id": deployment_id,
                "compose_project": compose_project,
            }
        )


# ============ Custom Domain ============

# Caddy's admin API, inside the compose network. Port 2019 is not published,
# so this is reachable from sibling containers and not from the internet.
CADDY_ADMIN = os.environ.get("CADDY_ADMIN_URL", "http://caddy:2019")


@router.post("/domain/configure")
async def configure_domain(
    domain: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Point the town's own domain at this deployment, and make Caddy serve it."""
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Domain/DNS configuration")  # platform-managed in hosted mode (A1)
    import httpx

    settings = await read_settings_row(db, create=True)

    # One snippet, in the directory the base Caddyfile already imports.
    #
    # This used to regenerate the whole Caddyfile from a template here, which
    # dropped the security headers, dropped `import /etc/caddy/tenants/*.caddy`
    # (every provisioned tenancy), dropped the orchestrator panel block, and
    # pointed the frontend at `frontend:5173` -- the Vite dev server -- so a
    # reload that worked would have returned 502 for every page.
    from app.services.caddy_config import (
        InvalidDomain, SNIPPET_NAME, describe_reload, normalise_domain, render_snippet,
    )

    try:
        domain = normalise_domain(domain)
    except InvalidDomain as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    settings.custom_domain = domain
    await db.commit()

    snippet_dir = os.environ.get("CADDY_TENANT_PATH", "/etc/caddy/tenants")
    snippet_path = os.path.join(snippet_dir, SNIPPET_NAME)
    try:
        os.makedirs(snippet_dir, exist_ok=True)
        with open(snippet_path, "w") as handle:
            handle.write(render_snippet(domain))
    except OSError as exc:
        # The domain is saved either way -- it is what the emails and the
        # portal links use. Say which half worked rather than rolling back a
        # setting the administrator did successfully change.
        logger.warning("[Domain] could not write %s: %s",
                       sanitize_for_log(snippet_path), sanitize_for_log(str(exc)))
        return {
            "status": "partial",
            "domain": domain,
            "url": f"https://{domain}",
            **describe_reload(False, "the reverse proxy config could not be written"),
        }

    # Ask Caddy to pick it up. The admin endpoint has to be reachable for this
    # to work at all, which is why the shipped Caddyfile now sets
    # `admin 0.0.0.0:2019` -- it defaults to localhost inside Caddy's own
    # container, so this call was refused every time it has ever been made.
    reload_ok, detail = False, ""
    try:
        caddyfile_path = os.environ.get("CADDYFILE_PATH", "/etc/caddy/Caddyfile")
        caddyfile_content = None
        if os.path.exists(caddyfile_path):
            with open(caddyfile_path, "r", encoding="utf-8") as f:
                caddyfile_content = f.read()

        async with httpx.AsyncClient(timeout=10.0) as client:
            if caddyfile_content:
                response = await client.post(
                    f"{CADDY_ADMIN}/load",
                    content=caddyfile_content.encode("utf-8"),
                    headers={"Content-Type": "text/caddyfile", "Cache-Control": "must-revalidate"},
                )
            else:
                response = await client.post(
                    f"{CADDY_ADMIN}/load",
                    headers={"Cache-Control": "must-revalidate"},
                )
            reload_ok = response.status_code in (200, 204)
            if not reload_ok:
                detail = f"the proxy answered {response.status_code}"
    except Exception as exc:
        detail = f"the proxy could not be reached ({type(exc).__name__})"

    return {
        "status": "success" if reload_ok else "partial",
        "domain": domain,
        "url": f"https://{domain}",
        **describe_reload(reload_ok, detail),
    }


@router.get("/domain/status")
async def get_domain_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Get current domain configuration status"""
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = result.scalar_one_or_none()
    
    return {
        "custom_domain": settings.custom_domain if settings else None,
        # Was a hardcoded IP of one particular machine, which is wrong for
        # every self-hosted town and is the address they are told to point DNS
        # at. Read from the environment, and absent rather than confidently
        # wrong when nothing has set it.
        "server_ip": os.environ.get("PUBLIC_IP") or os.environ.get("SERVER_IP") or None
    }


# ============ Image Upload ============

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/project/uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    _: User = Depends(get_current_staff)
):
    """Upload an image file (staff only). Returns the URL to access it."""
    # Validate file extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Read and validate file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"
        )
    
    # Generate unique filename
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    
    # Ensure upload directory exists
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    # Save file
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    # Return URL (relative to API)
    return {
        "url": f"/api/uploads/{unique_filename}",
        "filename": unique_filename
    }


# ============ Translation ============

from pydantic import BaseModel

class TranslationRequest(BaseModel):
    text: str
    source_lang: str = "en"
    target_lang: str = "es"


class MultiTranslationRequest(BaseModel):
    data: dict  # e.g., {"name": "Pothole", "description": "..."}
    source_lang: str = "en"
    target_languages: list[str] = None  # If None, translates to all supported languages


@router.get("/translate/languages")
async def get_supported_languages():
    """Get list of supported languages (public)"""
    from app.services.translation import get_supported_languages
    languages = get_supported_languages()
    return {
        "languages": [
            {"code": code, "name": name} 
            for code, name in languages.items()
        ]
    }


@router.post("/translate/health")
async def check_translation_service(
    _: User = Depends(get_current_admin)
):
    """Check if Google Cloud Translation API key is configured (admin only)"""
    from app.services.translation import check_translation_service
    is_available = await check_translation_service()
    return {
        "available": is_available,
        "service": "Google Cloud Translation",
        "url": "https://cloud.google.com/translate"
    }


@router.post("/translate/suggest")
async def suggest_translation(
    request: TranslationRequest,
    _: User = Depends(get_current_admin)
):
    """
    Auto-translate text using LibreTranslate (admin only).
    Used by Admin Console to suggest translations for service categories, departments, etc.
    """
    from app.services.translation import translate_text
    
    translated = await translate_text(
        request.text,
        request.source_lang,
        request.target_lang
    )
    
    if translated is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation service unavailable"
        )
    
    return {
        "original": request.text,
        "translated": translated,
        "source_lang": request.source_lang,
        "target_lang": request.target_lang
    }


@router.post("/translate/auto")
async def auto_translate_object(
    request: MultiTranslationRequest,
    _: User = Depends(get_current_admin)
):
    """
    Auto-translate an object (dictionary) to multiple languages (admin only).
    Returns translation dictionary ready to be saved to database.
    
    Example input:
    {
        "data": {"name": "Pothole Repair", "description": "Report road damage"},
        "source_lang": "en",
        "target_languages": ["es", "zh"]
    }
    
    Example output:
    {
        "translations": {
            "en": {"name": "Pothole Repair", "description": "Report road damage"},
            "es": {"name": "Repar

ación de Baches", "description": "Reportar daños en carreteras"},
            "zh": {"name": "坑洼修复", "description": "报告道路损坏"}
        }
    }
    """
    from app.services.translation import auto_translate_object as translate_obj
    
    translations = await translate_obj(
        request.data,
        request.source_lang,
        request.target_languages
    )
    
    return {"translations": translations}


@router.post("/translate/batch")
@_cost_limiter.limit("60/minute")
async def batch_translate(
    request: Request
):
    """
    Batch translate multiple UI strings (public endpoint).
    Used by frontend to translate static UI text.
    Uses database caching - first call hits Google API, subsequent calls use DB.
    """
    from app.services.translation import translate_batch
    
    data = await request.json()
    texts = data.get("texts", [])
    target_lang = data.get("target_lang", "es")
    
    if not texts:
        return {"translations": []}
    
    # Use batch translation with database caching
    results = await translate_batch(texts, "en", target_lang)
    
    # Return translations in order
    translations = [results.get(text, text) for text in texts]
    
    return {"translations": translations}



# ============ Database Backups ============

@router.get("/backups/status")
async def get_backup_status_endpoint(
    _: User = Depends(get_current_admin)
):
    """Get backup system status including configuration and last backup"""
    from app.services.backup_service import get_backup_status
    return await get_backup_status()


@router.get("/backups")
async def list_backups_endpoint(
    _: User = Depends(get_current_admin)
):
    """List all available database backups"""
    from app.services.backup_service import list_backups
    return await list_backups()


@router.post("/backups/create")
async def create_backup_endpoint(
    _: User = Depends(get_current_admin)
):
    """Trigger a manual database backup"""
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Backup management")  # state-run DR in hosted mode (A1)
    from app.services.backup_service import create_backup

    result = await create_backup()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail="Backup failed")
    
    return result


@router.post("/backups/cleanup")
async def cleanup_backups_endpoint(
    retention_days: int = None,
    _: User = Depends(get_current_admin)
):
    """Clean up old backups based on retention policy"""
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Backup management")
    from app.services.backup_service import cleanup_old_backups
    return await cleanup_old_backups(retention_days)


@router.delete("/backups/{backup_name}")
async def delete_backup_endpoint(
    backup_name: str,
    _: User = Depends(get_current_admin)
):
    """Delete a specific backup"""
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Backup management")
    from app.services.backup_service import delete_backup

    result = await delete_backup(backup_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail="Delete failed")
    
    return result


@router.post("/backups/{backup_name}/restore")
async def restore_backup_endpoint(
    backup_name: str,
    confirm: bool = Query(False, description="Must be true to confirm restore - this will overwrite the database!"),
    _: User = Depends(get_current_admin)
):
    """
    Restore database from a backup.
    
    WARNING: This will overwrite the current database!
    You must pass confirm=true to proceed.
    """
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Backup restore")
    if not confirm:
        raise HTTPException(
            status_code=400, 
            detail="You must pass confirm=true to restore. WARNING: This will overwrite the current database!"
        )
    
    from app.services.backup_service import restore_backup
    
    result = await restore_backup(backup_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail="Restore failed")
    
    return result


# ============ Health Dashboard (Bus Factor Mitigation) ============

@router.get("/health-dashboard")
async def get_health_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Comprehensive system health dashboard for non-technical administrators.
    Returns status of all services, database metrics, and last backup info.
    """
    from datetime import datetime, timezone
    
    health = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {},
        "database": {},
        "cache": {},
        "last_backup": None,
        "overall_status": "healthy"
    }
    
    # Check service health via network (works from inside containers)
    
    # Every value in here is a *detail*, not an uptime.
    #
    # The field was called "uptime" and held strings like "Port 5173 active"
    # and "6.2 GB - 12 conns". Nothing in this product measures availability
    # over a period, so the name promised a statistic that does not exist and
    # was never computed. Renamed to `detail`; `uptime` is still emitted
    # alongside it for one release so an older frontend does not blank out.
    def _svc(status: str, detail: str) -> dict:
        return {"status": status, "detail": detail, "uptime": detail}

    # True by construction: this code is running, so the backend is.
    health["services"]["backend"] = _svc("running", "Responding to this request")
    
    # Check frontend via socket - try configured port or common ports
    import os
    frontend_ports = []
    # Check env var first
    if os.getenv("FRONTEND_PORT"):
        frontend_ports.append(int(os.getenv("FRONTEND_PORT")))
    # Common frontend ports (Vite default, then others)
    frontend_ports.extend([5173, 3000, 80, 8080])
    # Remove duplicates while preserving order
    frontend_ports = list(dict.fromkeys(frontend_ports))
    
    # The hostname was hardcoded to 'frontend', which is a docker-compose
    # service name. On any deployment not using that exact topology -- a single
    # container, a static bundle behind a CDN, separate hosts -- the connect
    # fails and the page reports the frontend down while a clerk is looking at
    # it. Configurable, and the failure below now says "could not check" rather
    # than "stopped".
    frontend_host = os.getenv("FRONTEND_HOST", "frontend")

    frontend_found = False
    for port in frontend_ports:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((frontend_host, port))
            sock.close()
            if result == 0:
                health["services"]["frontend"] = _svc("running", f"Reachable on port {port}")
                frontend_found = True
                break
        except Exception:
            continue
    
    if not frontend_found:
        # "stopped" would be a claim. All that is known is that nothing
        # answered at the address we tried, which on an unusual topology says
        # more about the address than about the frontend.
        health["services"]["frontend"] = {
            **_svc("unknown", f"No answer from {frontend_host} on "
                              f"{', '.join(str(p) for p in frontend_ports[:3])}"),
            "error": "No ports reachable",
        }
    
    # Database - checked below via SQL
    health["services"]["db"] = _svc("pending", "Checking...")
    
    # Redis - checked via redis_client
    health["services"]["redis"] = _svc("pending", "Checking...")
    
    # Caddy was hardcoded to "running" with no probe at all, on the reasoning
    # that a received request proves the proxy routed it. That holds only when
    # the request came through the proxy, and an admin on a port-forward or a
    # dev server talking to the backend directly gets a confident green tick on
    # a proxy that may be stopped.
    #
    # Now it says which of those it is, and never claims more than it checked.
    from app.services.system_probes import proxy_status
    _proxy = proxy_status(request.headers)
    health["services"]["caddy"] = _svc(_proxy["status"], _proxy["detail"])
    
    # Database size and connection count
    try:
        db_size_result = await db.execute(text(
            "SELECT pg_size_pretty(pg_database_size(current_database())) as size"
        ))
        health["database"]["size"] = db_size_result.scalar()
        
        conn_result = await db.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        ))
        health["database"]["connections"] = conn_result.scalar()
        health["database"]["status"] = "healthy"
        # Update service status
        health["services"]["db"] = _svc(
            "running",
            f"{health['database']['size']} • {health['database']['connections']} connections",
        )
    except Exception as e:
        health["database"]["status"] = "error"
        health["database"]["error"] = str(e)
        health["overall_status"] = "degraded"
        health["services"]["db"] = {"status": "error", "error": str(e)[:50]}
    
    # Redis health check - use dynamic port from environment
    try:
        import redis
        import os
        redis_port = 6379  # default
        # Check for explicit port
        if os.getenv("REDIS_PORT"):
            redis_port = int(os.getenv("REDIS_PORT"))
        # Or parse from REDIS_URL if available
        elif os.getenv("REDIS_URL"):
            import re
            match = re.search(r':(\d+)', os.getenv("REDIS_URL", ""))
            if match:
                redis_port = int(match.group(1))
        
        redis_direct = redis.Redis(host="redis", port=redis_port, socket_timeout=3)
        info = redis_direct.info()
        health["cache"]["status"] = "healthy"
        health["cache"]["used_memory"] = info.get("used_memory_human", "unknown")
        health["cache"]["connected_clients"] = info.get("connected_clients", 0)
        # Update service status
        health["services"]["redis"] = _svc(
            "running", f"Port {redis_port} • {health['cache']['used_memory']} memory",
        )
    except redis.ConnectionError:
        health["cache"]["status"] = "not_configured"
        health["cache"]["error"] = "Redis not available"
        health["services"]["redis"] = _svc("stopped", "Not reachable")
    except Exception as e:
        health["cache"]["status"] = "error"
        health["cache"]["error"] = str(e)
        health["overall_status"] = "degraded"
        health["services"]["redis"] = {"status": "error", "error": str(e)[:50]}
    
    # Last backup info
    try:
        from app.services.backup_service import list_backups
        backups = await list_backups()
        if backups.get("backups"):
            last = backups["backups"][0]
            health["last_backup"] = {
                "name": last.get("name"),
                "created": last.get("created"),
                "size": last.get("size")
            }
    except Exception:
        health["last_backup"] = {"status": "unknown"}
    
    # Check for any down services
    for svc, data in health["services"].items():
        if data.get("status") != "running":
            health["overall_status"] = "degraded"
            break
    
    return health


# ============ Runbook Automation (Emergency Operations) ============

@router.post("/runbook/{action}")
async def execute_runbook(
    action: str,
    backup_name: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Execute emergency runbook actions (admin only).
    
    Available actions:
    - restart-all: Restart all containers
    - restart-{service}: Restart specific service (backend, frontend, redis, caddy)
    - clear-cache: Clear Redis cache
    - vacuum: Run PostgreSQL vacuum analyze
    - restore: Restore from backup (requires backup_name parameter)

    Disabled in managed mode — infrastructure operations come only from the
    orchestrator (A2).
    """
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Infrastructure runbook execution")
    from datetime import datetime, timezone
    
    result = {
        "action": action,
        "executed_by": current_user.email,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "details": {}
    }
    
    import shutil

    def _compose_cmd():
        """Resolve a usable Docker Compose command, or None if unavailable.

        Prefers Compose v2 (`docker compose`) and falls back to v1
        (`docker-compose`). Also requires the project directory to exist — the
        app can only drive Compose if it has the Docker socket + the repo on disk
        (e.g. a self-hosted host mount), which isn't the case in many
        environments. Returns (cmd_list, project_root) or (None, project_root)."""
        project_root = os.environ.get("PROJECT_ROOT", "/project")
        if not os.path.isdir(project_root):
            return None, project_root
        if shutil.which("docker"):
            return ["docker", "compose"], project_root
        if shutil.which("docker-compose"):
            return ["docker-compose"], project_root
        return None, project_root

    try:
        if action == "restart-all" or action.startswith("restart-"):
            services = (
                ["backend", "frontend", "redis", "caddy"]
                if action == "restart-all"
                else [action.replace("restart-", "")]
            )
            for service in services:
                if service not in ["backend", "frontend", "redis", "caddy"]:
                    raise HTTPException(status_code=400, detail=f"Cannot restart service: {service}")

            compose, project_root = _compose_cmd()
            if not compose:
                # Report honestly rather than pretending it worked.
                result["status"] = "unavailable"
                result["details"]["error"] = (
                    "Container restarts aren't available to the app in this environment "
                    "(Docker Compose or the project directory isn't reachable). "
                    "Run `docker compose restart <service>` on the host instead."
                )
            else:
                restarted, failed = [], {}
                for service in services:
                    proc = subprocess.run(
                        compose + ["restart", service],
                        cwd=project_root, capture_output=True, timeout=60, text=True,
                    )
                    if proc.returncode == 0:
                        restarted.append(service)
                    else:
                        failed[service] = (proc.stderr or proc.stdout or "restart failed").strip()[:300]
                result["details"]["restarted"] = restarted
                if failed:
                    result["details"]["failed"] = failed
                    result["status"] = "partial" if restarted else "error"
                if action == "restart-all":
                    result["details"]["note"] = "Database not restarted for safety"

        elif action == "clear-cache":
            if redis_client:
                await redis_client.flushdb()
                result["details"]["cleared"] = True
            else:
                result["status"] = "skipped"
                result["details"]["reason"] = "Redis not configured"

        elif action == "vacuum":
            # VACUUM cannot run inside a transaction block, so use a dedicated
            # AUTOCOMMIT connection rather than the request's transactional session.
            from app.db.session import engine
            async with engine.connect() as conn:
                ac = conn.execution_options(isolation_level="AUTOCOMMIT")
                await ac.execute(text("VACUUM ANALYZE"))
            result["details"]["operation"] = "VACUUM ANALYZE completed"
        
        elif action == "restore":
            if not backup_name:
                raise HTTPException(status_code=400, detail="backup_name required for restore action")
            from app.services.backup_service import restore_backup
            restore_result = await restore_backup(backup_name)
            result["details"] = restore_result
        
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
        
        # Log the action to audit
        try:
            from app.models import RequestAuditLog
            audit = RequestAuditLog(
                request_id=None,
                user_id=current_user.id,
                action=f"runbook_{action}",
                details=json.dumps(result["details"])
            )
            db.add(audit)
            await db.commit()
        except Exception:
            pass  # Don't fail if audit logging fails
        
        return result
        
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["details"]["error"] = "Operation timed out after 60 seconds"
        raise HTTPException(status_code=504, detail="Operation timed out")
    except HTTPException:
        raise
    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)
        raise HTTPException(status_code=500, detail="Operation failed")


# ============ AI Analytics Chat ============

from pydantic import BaseModel
from datetime import timedelta

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class AnalyticsChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []

class AnalyticsChatResponse(BaseModel):
    response: str
    context_used: List[str]


def staff_role_labels(names) -> dict:
    """{assignee name -> "staff member N"} with a stable, sorted numbering.

    Staff names are employee data the model has no need for: the question the
    chat answers is about workload shape, not about which named person is slow.
    Sorting before numbering keeps the labels stable across the tables in one
    prompt (and across calls while the roster is unchanged), so "staff member 2"
    means the same person in the workload and resolution lists.
    """
    return {name: f"staff member {i + 1}" for i, name in enumerate(sorted(names))}


@router.post("/analytics-chat", response_model=AnalyticsChatResponse)
@_cost_limiter.limit("20/minute")
async def analytics_chat(
    request: Request,
    body: AnalyticsChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_staff)
):
    """
    Conversational AI analytics — chat with Vertex AI about all system data.
    Gathers comprehensive context from across the platform (excluding resident PII)
    and uses Gemini 3.1 Flash-Lite to answer questions.

    Data minimization: what goes to the model is what the question needs —
    addresses are anonymized to block level, staff names become role labels,
    and free-text snippets pass through the research module's PII redaction.
    """
    from datetime import datetime, timezone

    # The town's AI switch governs every model call, including this one — an
    # admin who turned AI off must not find staff still chatting with Gemini.
    from app.services import capability_switches
    if not await capability_switches.enabled("ai"):
        raise HTTPException(
            status_code=403,
            detail="AI features are switched off for this town. An administrator can turn them on under Setup & Integrations.",
        )

    # Address anonymization + free-text redaction come from the research
    # module so the analytics chat and the research exports apply the SAME
    # minimization, not two drifting copies.
    from app.api.research import anonymize_address, sanitize_description

    context_used = []
    now = datetime.now(timezone.utc)
    
    # ========== 1. System Settings (Township Identity) ==========
    settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = settings_result.scalar_one_or_none()
    township_name = settings.township_name if settings and hasattr(settings, 'township_name') and settings.township_name else "the municipality"
    context_used.append("system_settings")
    
    # ========== 2. All Requests — Sanitized (No PII) ==========
    all_requests_result = await db.execute(
        select(ServiceRequest).where(ServiceRequest.deleted_at.is_(None))
    )
    all_requests = all_requests_result.scalars().all()
    context_used.append("all_service_requests")
    
    total = len(all_requests)
    open_count = sum(1 for r in all_requests if r.status == "open")
    in_progress_count = sum(1 for r in all_requests if r.status == "in_progress")
    closed_count = sum(1 for r in all_requests if r.status == "closed")
    
    # Category breakdown
    categories = {}
    for r in all_requests:
        cat = r.service_name or "Unknown"
        categories[cat] = categories.get(cat, 0) + 1
    
    # Priority distribution
    high_priority = sum(1 for r in all_requests if (getattr(r, 'manual_priority_score', None) or (r.ai_analysis or {}).get('priority_score', 5) if isinstance(r.ai_analysis, dict) else 5) >= 8)
    med_priority = sum(1 for r in all_requests if 5 <= (getattr(r, 'manual_priority_score', None) or (r.ai_analysis or {}).get('priority_score', 5) if isinstance(r.ai_analysis, dict) else 5) < 8)
    low_priority = total - high_priority - med_priority
    
    # Resolution time for closed requests
    resolution_times = []
    for r in all_requests:
        if r.status == "closed" and r.closed_datetime and r.requested_datetime:
            hours = (r.closed_datetime - r.requested_datetime).total_seconds() / 3600
            resolution_times.append(hours)
    avg_resolution = sum(resolution_times) / len(resolution_times) if resolution_times else 0
    
    # ========== 3. Temporal Patterns ==========
    hourly = {}
    daily = {}
    monthly = {}
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for r in all_requests:
        if r.requested_datetime:
            h = r.requested_datetime.hour
            hourly[h] = hourly.get(h, 0) + 1
            d = day_names[r.requested_datetime.weekday()]
            daily[d] = daily.get(d, 0) + 1
            m = r.requested_datetime.strftime("%Y-%m")
            monthly[m] = monthly.get(m, 0) + 1
    
    busiest_hour = max(hourly, key=hourly.get) if hourly else "N/A"
    busiest_day = max(daily, key=daily.get) if daily else "N/A"
    context_used.append("temporal_patterns")
    
    # ========== 4. Geographic Data — Anonymized Hotspots ==========
    # Counted by ANONYMIZED address: an exact street address in a prompt is a
    # resident's location handed to a third-party model. Block-level areas
    # answer "where are the hotspots" just as well.
    address_counts = {}
    for r in all_requests:
        if r.address:
            area = anonymize_address(r.address, "fuzzed")
            address_counts[area] = address_counts.get(area, 0) + 1
    top_addresses = sorted(address_counts.items(), key=lambda x: x[1], reverse=True)[:15]
    context_used.append("geographic_data")
    
    # ========== 5. Staff Data (role labels, not names) ==========
    # Staff names stay out of the prompt: the model needs the workload SHAPE,
    # not which named employee it belongs to. Stable "staff member N" labels
    # keep the two tables cross-referencable.
    staff_workload = {}
    staff_resolutions = {}
    assignees = set()
    for r in all_requests:
        assigned = getattr(r, 'assigned_to', None) or getattr(r, 'agency_responsible', None)
        if assigned:
            assignees.add(assigned)
            if r.status in ("open", "in_progress"):
                staff_workload[assigned] = staff_workload.get(assigned, 0) + 1
            if r.status == "closed":
                staff_resolutions[assigned] = staff_resolutions.get(assigned, 0) + 1
    role_labels = staff_role_labels(assignees)
    staff_workload = {role_labels[k]: v for k, v in staff_workload.items()}
    staff_resolutions = {role_labels[k]: v for k, v in staff_resolutions.items()}
    context_used.append("staff_data")
    
    # ========== 6. Department Data ==========
    from app.models import Department
    dept_result = await db.execute(select(Department).where(Department.is_active == True))
    departments = dept_result.scalars().all()
    dept_info = [{"name": d.name, "description": d.description or ""} for d in departments]
    context_used.append("departments")
    
    # ========== 7. AI Analysis Summaries ==========
    # Summaries and flag reasons quote resident text, so they pass through the
    # same PII redaction the research exports use; addresses go block-level.
    ai_summaries = []
    flagged_requests = []
    for r in all_requests:
        if r.ai_summary:
            ai_summaries.append({
                "id": r.service_request_id,
                "category": r.service_name,
                "address": anonymize_address(r.address, "fuzzed") or "Unknown",
                "summary": sanitize_description(r.ai_summary)[:200],
                "classification": r.ai_classification,
                "priority": (r.ai_analysis or {}).get("priority_score") if isinstance(r.ai_analysis, dict) else None
            })
        if r.flagged:
            flagged_requests.append({
                "id": r.service_request_id,
                "reason": sanitize_description(r.flag_reason) if r.flag_reason else None,
                "category": r.service_name,
                "address": anonymize_address(r.address, "fuzzed") or "Unknown"
            })
    context_used.append("ai_analysis")
    
    # ========== 8. Weather Context ==========
    weather_summary = ""
    try:
        # Get weather for the township's approximate center (from most common request coords)
        lats = [r.lat for r in all_requests if r.lat]
        lngs = [r.long for r in all_requests if r.long]
        if lats and lngs:
            # Town centroid at ~1km precision — weather is city-scale data and
            # the exact centroid of everyone's reports shouldn't leave either.
            # (get_weather_context also rounds defensively at the boundary.)
            center_lat = round(sum(lats) / len(lats), 2)
            center_lng = round(sum(lngs) / len(lngs), 2)
            from app.api.research import get_weather_context
            # This call was missing its await — the coroutine's .get() raised
            # and the except swallowed it, so weather context never worked.
            weather = await get_weather_context(now, center_lat, center_lng)
            if weather.get("temp_max_c") is not None:
                weather_summary = f"Recent weather: High {weather['temp_max_c']}°C, Low {weather['temp_min_c']}°C, Precip {weather['precip_24h_mm']}mm"
                context_used.append("weather")
    except Exception as e:
        logger.warning(f"Weather context unavailable: {e}")
    
    # ========== 9. Research Fields — Social Equity, Sentiment, Friction ==========
    equity_summary = ""
    sentiment_summary = ""
    friction_summary = ""
    infra_summary = ""
    
    try:
        from app.api.research import (
            get_infrastructure_category, analyze_sentiment,
            detect_trust_indicators, generate_zone_id, enabled_packs
        )

        # The research pack switches gate GENERATION, not just export: a town
        # that switched off sentiment_trust decided per-message tone scores
        # should never exist, and that includes computing them here to feed a
        # third-party model's prompt. Same for the Census-derived equity
        # lookups under social_equity.
        _research_packs = enabled_packs(settings)

        # --- Infrastructure category breakdown (lightweight string lookup) ---
        infra_categories = {}
        for r in all_requests:
            cat = get_infrastructure_category(r.service_code)
            if cat:
                infra_categories[cat] = infra_categories.get(cat, 0) + 1
        if infra_categories:
            infra_summary = "Infrastructure Category Breakdown:\n" + \
                chr(10).join(f"- {cat}: {count}" for cat, count in sorted(infra_categories.items(), key=lambda x: x[1], reverse=True))
            context_used.append("infrastructure_categories")
        
        # --- Sentiment & Trust aggregates (sample ~50 recent with descriptions) ---
        # Never computed when the pack is off — not computed-and-withheld.
        desc_requests = (
            [r for r in all_requests if r.description and len(r.description.strip()) > 10][:50]
            if _research_packs.get("sentiment_trust") else []
        )
        if desc_requests:
            sentiments = [analyze_sentiment(r.description) for r in desc_requests]
            valid_sentiments = [s for s in sentiments if s is not None]
            avg_sentiment = sum(valid_sentiments) / len(valid_sentiments) if valid_sentiments else 0
            
            trust_results = [detect_trust_indicators(r.description) for r in desc_requests]
            repeat_pct = sum(1 for t in trust_results if t.get('is_repeat_report')) / len(trust_results) * 100
            frustration_pct = sum(1 for t in trust_results if t.get('frustration_expressed')) / len(trust_results) * 100
            prior_ref_pct = sum(1 for t in trust_results if t.get('prior_report_mentioned')) / len(trust_results) * 100
            
            sentiment_summary = f"""Resident Sentiment Analysis (sampled {len(desc_requests)} recent requests):
- Average sentiment score: {avg_sentiment:.2f} (scale: -1.0 angry to +1.0 positive)
- Frustration expressed: {frustration_pct:.0f}% of requests
- Repeat reports: {repeat_pct:.0f}% of requests
- Prior report referenced: {prior_ref_pct:.0f}% of requests"""
            context_used.append("sentiment_trust")
        
        # --- Social Equity aggregates (sample requests with coordinates) ---
        try:
            from app.api.research import (
                get_census_tract_geoid, get_social_vulnerability_index,
                get_income_quintile_from_zone, get_population_density_category,
                get_housing_tenure_mix
            )
            
            # No Census/ACS/CDC call leaves when the equity pack is off.
            geo_requests = (
                [r for r in all_requests if r.lat and r.long][:30]
                if _research_packs.get("social_equity") else []
            )
            if geo_requests:
                svi_values = []
                income_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
                density_dist = {"low": 0, "medium": 0, "high": 0}
                renter_pcts = []
                import asyncio
                
                async def fetch_equity_data(r):
                    try:
                        zone_id = generate_zone_id(r.lat, r.long)
                        geoid = await get_census_tract_geoid(r.lat, r.long)
                        
                        if not geoid:
                            return None
                        
                        # Fetch all metrics for this geoid concurrently
                        svi_task = get_social_vulnerability_index(geoid)
                        iq_task = get_income_quintile_from_zone(zone_id, geoid)
                        pd_cat_task = get_population_density_category(zone_id, geoid)
                        ht_task = get_housing_tenure_mix(geoid)
                        
                        svi, iq, pd_cat, ht = await asyncio.gather(
                            svi_task, iq_task, pd_cat_task, ht_task, 
                            return_exceptions=True
                        )
                        
                        return {
                            "svi": svi if not isinstance(svi, Exception) else None,
                            "iq": iq if not isinstance(iq, Exception) else None,
                            "pd_cat": pd_cat if not isinstance(pd_cat, Exception) else None,
                            "ht": ht if not isinstance(ht, Exception) else None
                        }
                    except Exception as e:
                        return None
                
                # Fetch all requests concurrently, with an overall timeout of 8 seconds
                tasks = [fetch_equity_data(r) for r in geo_requests]
                try:
                    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=8.0)
                except asyncio.TimeoutError:
                    results = []
                
                for res in results:
                    if isinstance(res, dict):
                        if res.get("svi") is not None:
                            svi_values.append(res["svi"])
                        if res.get("iq") and res["iq"] in income_dist:
                            income_dist[res["iq"]] += 1
                        if res.get("pd_cat") and res["pd_cat"] in density_dist:
                            density_dist[res["pd_cat"]] += 1
                        if res.get("ht") is not None:
                            renter_pcts.append(res["ht"])
                
                avg_svi = sum(svi_values) / len(svi_values) if svi_values else None
                avg_renter = sum(renter_pcts) / len(renter_pcts) if renter_pcts else None
                
                equity_parts = [f"Social Equity Metrics (sampled {len(geo_requests)} requests):"]
                if avg_svi is not None:
                    equity_parts.append(f"- Average Social Vulnerability Index: {avg_svi:.2f} (0=low, 1=high)")
                if any(income_dist.values()):
                    equity_parts.append(f"- Income quintile distribution: Q1(lowest)={income_dist[1]}, Q2={income_dist[2]}, Q3={income_dist[3]}, Q4={income_dist[4]}, Q5(highest)={income_dist[5]}")
                if any(density_dist.values()):
                    equity_parts.append(f"- Population density: Low={density_dist['low']}, Medium={density_dist['medium']}, High={density_dist['high']}")
                if avg_renter is not None:
                    equity_parts.append(f"- Average renter percentage in request areas: {avg_renter*100:.0f}%")
                
                if len(equity_parts) > 1:
                    equity_summary = chr(10).join(equity_parts)
                    context_used.append("social_equity")
        except Exception as e:
            logger.warning(f"Social equity data unavailable: {e}")
        
        # --- Bureaucratic Friction aggregates ---
        try:
            from app.api.research import (
                calculate_time_to_triage, count_reassignments,
                is_off_hours_submission, calculate_escalation_occurred
            )
            
            # Load audit logs for a sample of requests
            sample_ids = [r.id for r in all_requests[:100]]
            if sample_ids:
                from app.models import RequestAuditLog
                audit_result = await db.execute(
                    select(RequestAuditLog).where(
                        RequestAuditLog.service_request_id.in_(sample_ids)
                    )
                )
                audit_logs_all = audit_result.scalars().all()
                
                # Group by request ID
                audit_by_request = {}
                for log in audit_logs_all:
                    audit_by_request.setdefault(log.service_request_id, []).append(log)
                
                triage_times = []
                reassignment_counts = []
                off_hours_count = 0
                escalation_count = 0
                sample_count = min(len(all_requests), 100)
                
                for r in all_requests[:100]:
                    req_audits = audit_by_request.get(r.id, [])
                    
                    tt = calculate_time_to_triage(r.requested_datetime, req_audits)
                    if tt is not None:
                        triage_times.append(tt)
                    
                    rc = count_reassignments(req_audits)
                    reassignment_counts.append(rc)
                    
                    if is_off_hours_submission(r.requested_datetime):
                        off_hours_count += 1
                    
                    if calculate_escalation_occurred(req_audits):
                        escalation_count += 1
                
                avg_triage = sum(triage_times) / len(triage_times) if triage_times else None
                avg_reassign = sum(reassignment_counts) / len(reassignment_counts) if reassignment_counts else 0
                
                friction_parts = [f"Bureaucratic Friction Metrics (sampled {sample_count} requests):"]
                if avg_triage is not None:
                    friction_parts.append(f"- Average time-to-triage: {avg_triage:.1f} hours")
                friction_parts.append(f"- Average reassignment count: {avg_reassign:.1f}")
                friction_parts.append(f"- Off-hours submissions: {off_hours_count}/{sample_count} ({off_hours_count/sample_count*100:.0f}%)")
                friction_parts.append(f"- Escalated requests: {escalation_count}/{sample_count} ({escalation_count/sample_count*100:.0f}%)")
                
                friction_summary = chr(10).join(friction_parts)
                context_used.append("bureaucratic_friction")
        except Exception as e:
            logger.warning(f"Bureaucratic friction data unavailable: {e}")
    
    except Exception as e:
        logger.warning(f"Research field aggregates unavailable: {e}")
    
    # ========== 10. Request Details — Sanitized List ==========
    # Block-level addresses and PII-redacted descriptions: this list is the
    # densest resident data in the prompt, so it gets the strictest treatment.
    request_details = []
    for r in all_requests[:100]:  # Cap at 100 most recent
        desc = sanitize_description(r.description) if r.description else r.description
        detail = {
            "id": r.service_request_id,
            "category": r.service_name,
            "status": r.status,
            "priority": r.priority,
            "address": anonymize_address(r.address, "fuzzed") or "Unknown",
            "description": (desc[:150] + "...") if desc and len(desc) > 150 else desc,
            "source": r.source,
            "created": r.requested_datetime.isoformat() if r.requested_datetime else None,
            "closed": r.closed_datetime.isoformat() if r.closed_datetime else None,
        }
        if r.ai_analysis and isinstance(r.ai_analysis, dict):
            detail["ai_priority"] = r.ai_analysis.get("priority_score")
            detail["ai_category"] = r.ai_classification
        if r.flagged:
            detail["flagged"] = True
            detail["flag_reason"] = sanitize_description(r.flag_reason) if r.flag_reason else None
        request_details.append(detail)

    # ========== Audit: every analytics-chat call leaves a trace ==========
    # Staff conversing with a model over the whole request corpus is data
    # access like any bulk export — who asked, roughly what, and how much
    # context went out. The question is truncated and no record contents or
    # model output are stored.
    try:
        from app.services.audit_service import AuditService
        # The proxy convention, not request.client -- behind Caddy that host is
        # the proxy's own address on every row, which defeats the one thing an
        # audit IP is for. Same helper the research access log uses.
        from app.api.research import client_info
        _ip, _ua = client_info(request)
        await AuditService.log_event(
            db,
            event_type="analytics_chat",
            success=True,
            username=current_user.username,
            user_id=getattr(current_user, "id", None),
            ip_address=_ip,
            user_agent=_ua,
            details={
                "question_preview": body.message[:100],
                "context_sizes": {
                    "total_requests": total,
                    "request_details": len(request_details),
                    "ai_summaries": len(ai_summaries),
                    "flagged": len(flagged_requests),
                    "history_messages": len(body.history),
                },
            },
        )
    except Exception as e:
        logger.warning(f"Failed to write analytics-chat audit log: {e}")
    
    # ========== Build the System Prompt ==========
    system_prompt = f"""You are an expert municipal analytics advisor for {township_name}. You have deep access to the community's 311 service request system INCLUDING advanced research-grade data: social equity metrics, resident sentiment analysis, bureaucratic friction indicators, and infrastructure categorization.

Your role is to provide **specific, data-driven insights** with exact numbers. You are speaking to municipal staff who need actionable intelligence.

## CURRENT SYSTEM DATA

### Overview
- **Total requests (all time): {total}**
- Open: {open_count} | In Progress: {in_progress_count} | Closed: {closed_count}
- Resolution rate: {(closed_count / total * 100) if total else 0:.1f}%
- Average resolution time: {avg_resolution:.1f} hours

### Priority Distribution
- High priority (8-10): {high_priority} ({(high_priority/total*100) if total else 0:.1f}%)
- Medium priority (5-7): {med_priority} ({(med_priority/total*100) if total else 0:.1f}%)
- Low priority (1-4): {low_priority} ({(low_priority/total*100) if total else 0:.1f}%)

### Requests by Category
{chr(10).join(f'- {cat}: {count}' for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True))}

### Temporal Patterns
- Busiest hour: {busiest_hour}:00
- Busiest day: {busiest_day}
- Hourly distribution: {json.dumps(dict(sorted(hourly.items())))}
- Daily distribution: {json.dumps(daily)}
- Monthly trend: {json.dumps(dict(sorted(monthly.items())))}

### Top Problem Areas (block-level, by request count)
{chr(10).join(f'- {addr}: {count} requests' for addr, count in top_addresses)}

### Staff Performance
Top Resolvers:
{chr(10).join(f'- {staff}: {count} resolved' for staff, count in sorted(staff_resolutions.items(), key=lambda x: x[1], reverse=True)[:10]) if staff_resolutions else '- No resolution data yet'}

Current Workload:
{chr(10).join(f'- {staff}: {count} active' for staff, count in sorted(staff_workload.items(), key=lambda x: x[1], reverse=True)[:10]) if staff_workload else '- No active assignments'}

### Departments
{chr(10).join(f'- {d["name"]}: {d["description"]}' for d in dept_info) if dept_info else '- No departments configured'}

### AI Analysis Highlights
- Requests with AI analysis: {len(ai_summaries)}
- Flagged requests: {len(flagged_requests)}
{chr(10).join(f'- FLAGGED [{r["id"]}] at {r["address"]}: {r["reason"]}' for r in flagged_requests[:10]) if flagged_requests else ''}

### Infrastructure Categories
{infra_summary if infra_summary else 'No infrastructure category data available'}

### Social Equity Metrics
{equity_summary if equity_summary else 'Social equity data not available (Census API may be unavailable)'}

### Resident Sentiment & Trust
{sentiment_summary if sentiment_summary else 'Sentiment data not available'}

### Government Responsiveness (Bureaucratic Friction)
{friction_summary if friction_summary else 'Friction metrics not available'}

### Environmental Context
{weather_summary if weather_summary else 'Weather data not available'}
Season: {'Winter' if now.month in [12,1,2] else 'Spring' if now.month in [3,4,5] else 'Summer' if now.month in [6,7,8] else 'Fall'}

### Recent Request Details (sanitized, no personal info)
{json.dumps(request_details[:50], indent=None, default=str)}

## RESPONSE FORMAT RULES
The chat UI renders plain text with basic inline markdown only — it does NOT render markdown tables or headers, so those come out as raw pipes and hashes. Format accordingly:
- NEVER use markdown tables (| col | col |). To compare departments, categories, or time periods, use a bullet list with a bold label per item instead — e.g. "**Public Works** — 42 requests, 3.1 day avg".
- Do NOT use markdown headers (#, ##, ###). For a section title, put a short **bold line** on its own line instead.
- Use bullet points ("- ") for lists of 3+ items, and numbered lists ("1. ") for prioritized actions.
- **Bold** all key numbers, percentages, and metric values.
- Keep paragraphs short: 2-3 sentences maximum.
- End substantive responses with a bold **Key Takeaway** line followed by 1-2 sentences.

## IMPORTANT RULES
- NEVER share or reference resident names, emails, or phone numbers
- Always cite specific data points — never say "some" or "several" when you have exact numbers
- If asked about something not in the data, say so clearly
- When discussing equity metrics, explain what the numbers mean in plain language (e.g., "An SVI of 0.7 means this area is in the top 30% most vulnerable nationally")
- Provide actionable recommendations when relevant
- Cross-reference data across categories when it adds insight (e.g., connect sentiment to response time, or equity to priority patterns)
"""


    # ========== Build Conversation & Call Vertex AI ==========
    try:
        from app.services.secret_manager import get_secret as sm_get_secret
        
        project_id = await sm_get_secret("VERTEX_AI_PROJECT")
        if not project_id:
            # Try alternative key
            project_id = os.getenv("GOOGLE_VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
        
        if not project_id:
            raise HTTPException(status_code=503, detail="Vertex AI not configured — VERTEX_AI_PROJECT not set")
        
        service_account_json = await sm_get_secret("VERTEX_AI_SERVICE_ACCOUNT_KEY")
        
        # Build the full conversation prompt
        conversation = system_prompt + "\n\n## CONVERSATION\n"
        for msg in body.history[-20:]:  # Last 20 messages for context
            role_label = "Staff" if msg.role == "user" else "AI Advisor"
            conversation += f"\n**{role_label}:** {msg.content}\n"
        conversation += f"\n**Staff:** {body.message}\n\n**AI Advisor:**"
        
        # Call Vertex AI — use lower temperature for factual responses
        import google.auth
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        import aiohttp
        
        if service_account_json:
            sa_info = json.loads(service_account_json)
            credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
        else:
            credentials, _ = google.auth.default(
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
        
        credentials.refresh(Request())
        
        endpoint = f"https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/global/publishers/google/models/gemini-3.1-flash-lite:generateContent"
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": conversation}]}],
            "generationConfig": {
                "temperature": 0.3,
                "topP": 0.8,
                "maxOutputTokens": 4096,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Vertex AI error: {error_text}")
                    raise HTTPException(status_code=502, detail=f"AI service error: {response.status}")
                
                result = await response.json()
        
        # Extract response text
        ai_response = ""
        if 'candidates' in result and result['candidates']:
            parts = result['candidates'][0].get('content', {}).get('parts', [])
            for part in parts:
                if 'text' in part:
                    ai_response += part['text']
        
        if not ai_response:
            ai_response = "I wasn't able to generate a response. Please try rephrasing your question."
        
        # Track API usage
        try:
            from app.services.api_usage import track_api_call
            await track_api_call(db, "vertex_ai", endpoint="analytics_chat", tokens_used=len(conversation) // 4 + len(ai_response) // 4)
        except Exception:
            pass  # API usage tracking is non-critical
        
        return AnalyticsChatResponse(
            response=ai_response.strip(),
            context_used=context_used
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analytics chat error: {e}")
        raise HTTPException(status_code=500, detail="AI analytics chat failed")



@router.get("/statistics/redirected")
async def get_redirected_statistics(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_staff),
):
    """How many residents were redirected instead of filing, and to whom.

    These are not service requests -- nobody worked them and they appear in no
    queue, feed, export or map. But the count is the only way a town learns that
    one road is turning away twenty people a month, which is either evidence for
    a conversation with the county or a sign the routing config is wrong.

    Road-based redirects (this road belongs to someone else) and whole-category
    redirects (the whole service is handled elsewhere) are reported separately
    because they mean different things to a clerk.
    """
    # system.py imports datetime lazily per-function; do the same rather than
    # adding a module-level import that the rest of the file does not expect.
    from datetime import datetime, timedelta, timezone

    from app.models import BlockedRequestLog

    since = datetime.now(timezone.utc) - timedelta(days=days)

    async def grouped(column):
        rows = (
            await db.execute(
                select(column, func.count(BlockedRequestLog.id).label("count"))
                .where(BlockedRequestLog.created_at >= since)
                .group_by(column)
                .order_by(func.count(BlockedRequestLog.id).desc())
            )
        ).all()
        return [{"label": row[0] or "Unspecified", "count": row[1]} for row in rows]

    try:
        total = (
            await db.execute(
                select(func.count(BlockedRequestLog.id)).where(BlockedRequestLog.created_at >= since)
            )
        ).scalar() or 0

        by_type = {
            row["label"]: row["count"] for row in await grouped(BlockedRequestLog.block_type)
        }

        return {
            "days": days,
            "total": total,
            "road_based": by_type.get("road_based", 0),
            "category": by_type.get("category", 0),
            "by_jurisdiction": await grouped(BlockedRequestLog.jurisdiction_name),
            # road_name is null for whole-category redirects, which is why this
            # list will not sum to `total`.
            "by_road": [
                r for r in await grouped(BlockedRequestLog.road_name) if r["label"] != "Unspecified"
            ],
            "by_service": await grouped(BlockedRequestLog.service_name),
        }
    except Exception as e:
        # A town that has never redirected anyone has no table rows and possibly
        # no table yet; that is an empty report, not a failure.
        logger.info(f"Redirected statistics unavailable: {e}")
        return {
            "days": days, "total": 0, "road_based": 0, "category": 0,
            "by_jurisdiction": [], "by_road": [], "by_service": [],
        }
