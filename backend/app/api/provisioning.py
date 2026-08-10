"""Non-interactive provisioning API — ORCHESTRATOR_PLAN.md A4 (+ A7 lifecycle,
A8 break-glass exchange).

Called only by the state's orchestrator panel, never by browsers. Auth is the
PROVISIONING_TOKEN shared secret (constant-time compare); every endpoint is
inert until that token is configured, so standalone installs expose nothing.
"""

import base64
import hashlib
import hmac
import json
import secrets as pysecrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, get_password_hash
from app.core.config import get_settings
from app.core.encryption import encrypt
from app.core.managed import (
    LIFECYCLE_KEY,
    get_lifecycle_state,
    set_lifecycle_state,
)
from app.db.session import get_db
from app.models import SystemSecret, SystemSettings, User
from app.services.audit_service import AuditService

router = APIRouter()

ONBOARDING_LINK_TTL_HOURS = 72
BREAK_GLASS_MAX_MINUTES = 60


def require_provisioning_token(x_provisioning_token: str = Header(default="")) -> str:
    settings = get_settings()
    if not settings.provisioning_token:
        # Endpoints only exist when the deployment opts in (A4).
        raise HTTPException(status_code=404, detail="Provisioning API is not enabled")
    if not hmac.compare_digest(x_provisioning_token, settings.provisioning_token):
        raise HTTPException(status_code=401, detail="Invalid provisioning token")
    return "orchestrator"


class BootstrapRequest(BaseModel):
    township_name: str = Field(min_length=1, max_length=200)
    domain: Optional[str] = None
    admin_email: Optional[str] = Field(default=None, max_length=255)


@router.post("/bootstrap")
async def bootstrap_township(
    body: BootstrapRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_provisioning_token),
):
    """Replace the interactive first-run flow when the panel creates a town:
    set township name/domain, create/assign the initial admin, and return a
    one-time onboarding link (short-lived signed token)."""
    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings_row = result.scalar_one_or_none()
    if not settings_row:
        settings_row = SystemSettings()
        db.add(settings_row)
    settings_row.township_name = body.township_name
    if body.domain:
        settings_row.custom_domain = body.domain.strip().lower()

    admin_user = None
    if body.admin_email:
        email = body.admin_email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        admin_user = result.scalar_one_or_none()
        if admin_user:
            admin_user.role = "admin"
            admin_user.is_active = True
        else:
            username = email.split("@")[0][:80] or "admin"
            # keep username unique if the prefix is taken
            existing = await db.execute(select(User).where(User.username == username))
            if existing.scalar_one_or_none():
                username = f"{username}-{pysecrets.token_hex(3)}"
            admin_user = User(
                username=username,
                email=email,
                full_name="Town Administrator",
                role="admin",
                is_active=True,
                # random unusable password — the admin sets their own via the
                # onboarding link, or signs in through SSO once configured
                hashed_password=get_password_hash(pysecrets.token_urlsafe(32)),
            )
            db.add(admin_user)
    await db.commit()

    onboarding_link = None
    if admin_user:
        # Single-purpose + single-use: get_current_user rejects purpose-bearing
        # tokens, so this link is only redeemable at /api/auth/onboarding/redeem
        # (which burns the jti).
        token = create_access_token(
            data={
                "sub": admin_user.username,
                "purpose": "onboarding",
                "jti": pysecrets.token_hex(16),
            },
            expires_delta=timedelta(hours=ONBOARDING_LINK_TTL_HOURS),
        )
        host = settings_row.custom_domain or "localhost"
        onboarding_link = f"https://{host}/onboarding?token={token}"

    await AuditService.log_event(
        db,
        event_type="provisioning_bootstrap",
        success=True,
        username=actor,
        details={
            "township_name": body.township_name,
            "domain": settings_row.custom_domain,
            "admin_created": bool(admin_user),
        },
    )
    return {
        "township_name": settings_row.township_name,
        "domain": settings_row.custom_domain,
        "admin_username": admin_user.username if admin_user else None,
        "onboarding_link": onboarding_link,
        "onboarding_link_expires_hours": ONBOARDING_LINK_TTL_HOURS if onboarding_link else None,
    }


class LifecycleRequest(BaseModel):
    state: str = Field(pattern="^(active|suspended)$")


@router.post("/lifecycle")
async def set_lifecycle(
    body: LifecycleRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_provisioning_token),
):
    """Panel-set suspend/resume (A7). While suspended the API returns 503 for
    everything except health and this provisioning surface."""
    result = await db.execute(
        select(SystemSecret).where(SystemSecret.key_name == LIFECYCLE_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        row.key_value = encrypt(body.state)
        row.is_configured = True
    else:
        db.add(
            SystemSecret(
                key_name=LIFECYCLE_KEY,
                key_value=encrypt(body.state),
                is_configured=True,
                description="Instance lifecycle state (set by the state orchestrator)",
            )
        )
    await db.commit()
    set_lifecycle_state(body.state)
    await AuditService.log_event(
        db,
        event_type="provisioning_lifecycle",
        success=True,
        username=actor,
        details={"state": body.state},
    )
    return {"state": get_lifecycle_state()}


# ---- A8: break-glass exchange -------------------------------------------------
#
# The panel mints a short-lived token HMAC-signed with this town's
# PROVISIONING_TOKEN (already shared — no extra key distribution). Exchanging
# it yields a normal, short-lived staff JWT bound to a visible, audited
# state-ops account. Only accepted in managed mode.


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _verify_break_glass(token: str, key: str) -> dict:
    try:
        payload_b64, sig = token.split(".", 1)
        payload = _b64decode(payload_b64)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed break-glass token")
    expected = base64.urlsafe_b64encode(
        hmac.new(key.encode(), payload, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Invalid break-glass token signature")
    claims = json.loads(payload)
    if claims.get("typ") != "state_ops_break_glass":
        raise HTTPException(status_code=401, detail="Wrong token type")
    if datetime.now(timezone.utc).timestamp() > float(claims.get("exp", 0)):
        raise HTTPException(status_code=401, detail="Break-glass token expired")
    return claims


class BreakGlassExchange(BaseModel):
    token: str


@router.post("/break-glass")
async def exchange_break_glass(
    body: BreakGlassExchange,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a panel-issued break-glass token for a time-boxed admin JWT.
    The session is tied to a distinct, visible `state-ops:` account and lands
    in the town's own audit trail as actor_type=state_ops (A8)."""
    settings = get_settings()
    if not settings.managed_mode:
        raise HTTPException(status_code=404, detail="Not available outside managed mode")
    if not settings.provisioning_token:
        raise HTTPException(status_code=404, detail="Provisioning API is not enabled")

    claims = _verify_break_glass(body.token, settings.provisioning_token)
    ops_actor = str(claims.get("actor", ""))[:150]
    username = f"state-ops:{ops_actor}"[:100]

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            username=username,
            email=ops_actor if "@" in ops_actor else f"{username}@state-ops.invalid",
            full_name=f"State operations ({ops_actor})",
            role="admin",
            is_active=True,
            hashed_password=get_password_hash(pysecrets.token_urlsafe(32)),
        )
        db.add(user)
        await db.commit()

    remaining = datetime.fromtimestamp(float(claims["exp"]), tz=timezone.utc) - datetime.now(
        timezone.utc
    )
    ttl = min(remaining, timedelta(minutes=BREAK_GLASS_MAX_MINUTES))
    access_token = create_access_token(
        data={"sub": user.username, "actor_type": "state_ops", "jti": claims.get("jti")},
        expires_delta=ttl,
    )
    await AuditService.log_event(
        db,
        event_type="break_glass_access",
        success=True,
        username=username,
        user_id=user.id,
        session_id=str(claims.get("jti")),
        details={
            "actor_type": "state_ops",
            "actor": ops_actor,
            "expires_in_seconds": int(ttl.total_seconds()),
        },
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(ttl.total_seconds()),
        "actor_type": "state_ops",
    }


class ManagedSettingsRequest(BaseModel):
    settings: dict = Field(default_factory=dict)


# Keys the panel pushes that this app can actually enforce today. Others are
# retained in managed_policy for display/lock but have no runtime effect here.
_ENFORCED_KEYS = {"legal_hold", "retention_days", "pii_anonymization", "retention_mode"}


@router.post("/managed-settings")
async def set_managed_settings(
    body: ManagedSettingsRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_provisioning_token),
):
    """Apply state-set managed policy pushed by the orchestrator (A-plan).

    Maps the policy onto the settings this app enforces — instance-wide legal
    hold (suspends ALL retention purging) and data-retention window/mode — and
    records the full policy in ``managed_policy`` so those fields become
    state-controlled (the town can no longer override them).
    """
    incoming = body.settings or {}

    result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = SystemSettings()
        db.add(settings)

    applied = {}

    if "legal_hold" in incoming:
        settings.legal_hold = bool(incoming["legal_hold"])
        applied["legal_hold"] = settings.legal_hold

    if incoming.get("retention_days") is not None:
        try:
            settings.retention_days = int(incoming["retention_days"])
            applied["retention_days"] = settings.retention_days
        except (TypeError, ValueError):
            pass

    # PII anonymization on/off maps to the retention archive mode; an explicit
    # retention_mode wins if provided.
    if "pii_anonymization" in incoming:
        settings.retention_mode = "anonymize" if incoming["pii_anonymization"] else "delete"
        applied["retention_mode"] = settings.retention_mode
    if incoming.get("retention_mode") in ("anonymize", "delete"):
        settings.retention_mode = incoming["retention_mode"]
        applied["retention_mode"] = settings.retention_mode

    # Record the full pushed policy — presence of a key marks it state-controlled.
    settings.managed_policy = dict(incoming)

    await db.commit()

    try:
        await AuditService.log_event(
            db,
            event_type="provisioning_managed_settings",
            success=True,
            username=actor,
            details={"applied": applied, "keys": sorted(incoming.keys())},
        )
    except Exception:
        pass

    return {"status": "ok", "applied": applied, "enforced_keys": sorted(_ENFORCED_KEYS & set(incoming))}


class HostSecretsRequest(BaseModel):
    """The host's complete current set of shared credentials.

    Full replace, like managed-settings above and for the same reason: a
    partial push has no spelling for "stop sharing this", so a credential the
    host withdrew in its own console would stay live in the town's vault. What
    arrives here is everything the host is sharing right now, and a key that is
    absent is one to take back.
    """

    # Typed rather than a bare `dict`. A value that is not a string used to
    # reach `.strip()` inside the apply loop, raise AttributeError, and answer
    # 500 -- after earlier keys in the same batch had already been written and
    # committed. Pydantic rejects the malformed body up front now, and
    # `host_secrets.apply` refuses any residual non-string per key rather than
    # raising, so one bad entry costs that entry and nothing else.
    secrets: Dict[str, str] = Field(default_factory=dict)


@router.post("/host-secrets")
async def set_host_secrets(
    body: HostSecretsRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_provisioning_token),
):
    """Accept provider credentials the deployment's host supplies for this town.

    A town on a hosted deployment often has no account of its own with a map
    vendor or a translation API, and waiting for one is what stalls onboarding.
    The host has those accounts, so it can hand the instance working
    credentials -- and the setup page then shows a configured card with a note
    saying where the key came from, rather than an empty box for an account
    nobody has.

    Three rules, all enforced in `app.services.host_secrets`:

      * only provider credential keys, never the platform's own (the secret
        store, the KMS, anything `BACKUP_`), so this cannot become a door onto
        where the town's credentials live;
      * a key the TOWN configured is refused, however often it is pushed -- the
        town's own credential wins, and a clerk's value is never reverted by a
        machine;
      * keys the host provided and no longer sends are deleted, vault and
        database both.

    No secret store gate here, deliberately. The admin pages refuse a
    credential until the town has said where credentials go, because a value
    written before that decision can end up in an off-site backup nobody chose.
    That gate protects a person from an unmade decision; this caller is the host
    and the answer is already the encrypted database until the town says
    otherwise. Refusing the push would leave a freshly provisioned town with no
    credentials and no way to be given any until an admin logged in, which is
    the situation the feature exists to remove.

    Names only in the response and in the audit trail. The values arrive, go to
    the same store any credential goes to, and are never echoed, logged, or put
    in an error message.

    The response is {"status", "accepted", "refused", "revoked", "failed",
    "db_only"} -- five sorted lists of key names. `failed` and `db_only` were
    added after the first release and are additive on purpose, so a caller that
    only reads the first three is unchanged by them. `failed` names withdrawals
    that did not take (the vault refused the delete, so the credential is still
    live and still the host's to retry). `db_only` names writes that reached
    only the encrypted database copy because the external secret store would
    not take them -- the credential works, but not for the reason the host
    thinks, and it goes away when the database copies are scrubbed.
    """
    from app.services import host_secrets

    result = await host_secrets.apply(db, body.secrets or {})

    try:
        await AuditService.log_event(
            db,
            event_type="provisioning_host_secrets",
            success=True,
            username=actor,
            # Key names and counts. A value here would be a secret in an
            # exportable table, which is the one thing this endpoint must not
            # produce.
            details={
                "accepted": result["accepted"],
                "refused": result["refused"],
                "revoked": result["revoked"],
                # Withdrawals that did not take. Worth an audit line of its own:
                # the credential is still live in the town's vault, and this is
                # the only record that somebody tried to take it back.
                "failed": result["failed"],
                # Accepted, but only into the encrypted database copy -- the
                # external secret store did not take the write. The credential
                # works today and vanishes the day the database copies are
                # scrubbed, and this line is the only place that is visible.
                "db_only": result["db_only"],
                "counts": {k: len(v) for k, v in result.items()},
            },
        )
    except Exception:
        pass

    return {"status": "ok", **result}
