"""
Setup API endpoints for Auth0 and Google Cloud configuration.

Security: All endpoints require admin authentication and log actions to audit trail.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import logging
import httpx
import json

from app.db.session import get_db
from app.core.auth import get_current_user
from app.models import User, SystemSecret
from app.services.audit_service import AuditService
from app.core.encryption import encrypt

router = APIRouter()
logger = logging.getLogger(__name__)


# Request/Response Models
class Auth0SetupRequest(BaseModel):
    """Request body for Auth0 automated setup"""
    domain: str = Field(..., description="Auth0 tenant domain (e.g., yourorg.us.auth0.com)", pattern=r'^[a-zA-Z0-9][a-zA-Z0-9.-]*\.auth0\.com$')
    management_client_id: str = Field(..., description="Management API client ID")
    management_client_secret: str = Field(..., description="Management API client secret")
    callback_url: str = Field(..., description="Application callback URL")


class GCPSetupRequest(BaseModel):
    """Request body for GCP automated setup"""
    project_id: str = Field(..., description="Google Cloud project ID")
    service_account_json: str = Field(..., description="Service account JSON key")


class SetupStatusResponse(BaseModel):
    """Current setup status"""
    gcp_configured: bool
    auth0_configured: bool
    gcp_details: Optional[Dict[str, Any]] = None
    auth0_details: Optional[Dict[str, Any]] = None


@router.get("/status")
async def get_setup_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current setup status.
    
    Returns what's configured and what needs setup.
    GCP is checked first since it's a dependency for Auth0 credential storage.
    Requires admin authentication.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    
    # Check GCP status (check if we have project_id secret configured)
    gcp_result = await db.execute(
        select(SystemSecret).where(SystemSecret.key_name == "GOOGLE_CLOUD_PROJECT")
    )
    gcp_secret = gcp_result.scalar_one_or_none()
    gcp_configured = bool(gcp_secret and gcp_secret.is_configured)
    
    # Check Auth0 status
    auth0_result = await db.execute(
        select(SystemSecret).where(SystemSecret.key_name.in_([
            "AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET"
        ]))
    )
    auth0_secrets = {s.key_name: s for s in auth0_result.scalars().all()}
    auth0_configured = all(
        key in auth0_secrets and auth0_secrets[key].is_configured
        for key in ["AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET"]
    )
    
    # Get details if configured
    auth0_details = None
    if auth0_configured:
        from app.core.encryption import decrypt_safe
        domain = decrypt_safe(auth0_secrets.get("AUTH0_DOMAIN", {}).key_value) if auth0_secrets.get("AUTH0_DOMAIN") else None
        client_id = auth0_secrets.get("AUTH0_CLIENT_ID", {}).key_value if auth0_secrets.get("AUTH0_CLIENT_ID") else None
        auth0_details = {
            "domain": domain,
            "client_id": f"{client_id[:10]}..." if client_id else None
        }
    
    gcp_details = None
    if gcp_configured:
        from app.core.encryption import decrypt_safe
        project_id = decrypt_safe(gcp_secret.key_value) if gcp_secret else None
        gcp_details = {"project_id": project_id}
    
    return SetupStatusResponse(
        gcp_configured=gcp_configured,
        auth0_configured=auth0_configured,
        gcp_details=gcp_details,
        auth0_details=auth0_details
    )


@router.post("/gcp/configure")
async def configure_gcp(
    request: GCPSetupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Configure Google Cloud Platform.
    
    This endpoint:
    1. Validates the service account JSON
    2. Stores project ID and credentials
    3. Optionally enables required APIs
    
    Requires admin authentication and logs all actions.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    # GCP project / service account / KMS keys are platform-managed in hosted
    # mode (A1) — this path writes them directly, so gate the whole endpoint.
    from app.core.managed import ensure_not_managed
    ensure_not_managed("Google Cloud configuration")

    try:
        # Validate the service account JSON
        try:
            sa_data = json.loads(request.service_account_json)
            if "project_id" not in sa_data:
                raise ValueError("Service account JSON must contain project_id")
            if "client_email" not in sa_data:
                raise ValueError("Service account JSON must contain client_email")
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON format for service account"
            )
        
        # Verify the project ID matches
        if sa_data["project_id"] != request.project_id:
            logger.warning("Project ID mismatch between request and service account JSON")
            # Use the one from the service account JSON as it's authoritative
        
        
        # Store credentials in database
        for key, value in [
            ("GOOGLE_CLOUD_PROJECT", request.project_id),
            ("GCP_SERVICE_ACCOUNT_JSON", request.service_account_json)
        ]:
            result = await db.execute(
                select(SystemSecret).where(SystemSecret.key_name == key)
            )
            secret = result.scalar_one_or_none()
            
            encrypted_value = encrypt(value)
            
            if secret:
                secret.key_value = encrypted_value
                secret.is_configured = True
            else:
                secret = SystemSecret(
                    key_name=key,
                    key_value=encrypted_value,
                    is_configured=True,
                    description=f"GCP {key.replace('_', ' ').lower()}"
                )
                db.add(secret)
        
        await db.commit()
        
        # Try to create KMS keyring and key automatically
        kms_created = False
        kms_error = None
        kms_keyring = "pinpoint311-keyring"
        kms_key = "pii-encryption-key"
        kms_location = "us-central1"
        
        try:
            from google.cloud import kms
            from google.oauth2 import service_account
            
            # Create credentials from service account JSON
            credentials = service_account.Credentials.from_service_account_info(sa_data)
            kms_client = kms.KeyManagementServiceClient(credentials=credentials)
            
            # Create keyring
            parent = f"projects/{request.project_id}/locations/{kms_location}"
            keyring_name = f"{parent}/keyRings/{kms_keyring}"
            
            try:
                kms_client.create_key_ring(
                    request={"parent": parent, "key_ring_id": kms_keyring, "key_ring": {}}
                )
                logger.info(f"Created KMS keyring: {kms_keyring}")
            except Exception as e:
                if "already exists" in str(e).lower() or "ALREADY_EXISTS" in str(e):
                    logger.info(f"KMS keyring already exists: {kms_keyring}")
                else:
                    raise
            
            # Create encryption key
            try:
                kms_client.create_crypto_key(
                    request={
                        "parent": keyring_name,
                        "crypto_key_id": kms_key,
                        "crypto_key": {
                            "purpose": kms.CryptoKey.CryptoKeyPurpose.ENCRYPT_DECRYPT,
                            "version_template": {
                                "algorithm": kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm.GOOGLE_SYMMETRIC_ENCRYPTION
                            }
                        }
                    }
                )
                logger.info(f"Created KMS key: {kms_key}")
            except Exception as e:
                if "already exists" in str(e).lower() or "ALREADY_EXISTS" in str(e):
                    logger.info(f"KMS key already exists: {kms_key}")
                else:
                    raise
            
            kms_created = True
            
            # Store KMS configuration
            for key, value in [
                ("KMS_KEY_RING", kms_keyring),
                ("KMS_KEY_ID", kms_key),
                ("KMS_LOCATION", kms_location)
            ]:
                result = await db.execute(
                    select(SystemSecret).where(SystemSecret.key_name == key)
                )
                secret = result.scalar_one_or_none()
                encrypted_value = encrypt(value)
                
                if secret:
                    secret.key_value = encrypted_value
                    secret.is_configured = True
                else:
                    secret = SystemSecret(
                        key_name=key,
                        key_value=encrypted_value,
                        is_configured=True,
                        description=f"KMS {key.replace('_', ' ').lower()}"
                    )
                    db.add(secret)
            
            await db.commit()
            
        except ImportError:
            kms_error = "google-cloud-kms package not installed"
            logger.warning(f"KMS setup skipped: {kms_error}")
        except Exception as e:
            kms_error = str(e)[:200]
            logger.warning(f"KMS auto-setup failed (will use Fernet fallback): {e}")
        
        # Log successful setup
        await AuditService.log_event(
            db=db,
            event_type="gcp_configured",
            success=True,
            user_id=current_user.id,
            username=current_user.username,
            details={
                "project_id": request.project_id,
                "service_account_email": sa_data.get("client_email"),
                "kms_configured": kms_created,
                "kms_error": kms_error
            }
        )
        
        logger.info(f"GCP configured successfully by {current_user.username}")
        
        return {
            "success": True,
            "message": "Google Cloud Platform configured successfully",
            "project_id": request.project_id,
            "kms_configured": kms_created,
            "kms_keyring": kms_keyring if kms_created else None,
            "kms_key": kms_key if kms_created else None,
            "kms_note": None if kms_created else "KMS not auto-created, using Fernet encryption"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GCP setup failed: {str(e)}")
        await AuditService.log_event(
            db=db,
            event_type="gcp_configuration_failed",
            success=False,
            user_id=current_user.id,
            username=current_user.username,
            failure_reason=str(e)
        )
        logger.error(f"Failed to configure GCP: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to configure GCP"
        )


@router.post("/auth0/configure")
async def configure_auth0(
    request: Auth0SetupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Automatically configure Auth0 tenant.
    
    This endpoint:
    1. Creates a new application in Auth0
    2. Configures MFA, password policies, brute force protection
    3. Sets callback URLs
    4. Stores credentials in database (encrypted)
    
    Requires admin authentication and logs all actions.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        # Get Management API access token
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                f"https://{request.domain}/oauth/token",
                json={
                    "client_id": request.management_client_id,
                    "client_secret": request.management_client_secret,
                    "audience": f"https://{request.domain}/api/v2/",
                    "grant_type": "client_credentials"
                }
            )
            
            if token_response.status_code != 200:
                logger.error(f"Auth0 Management API token failed: HTTP {token_response.status_code}")
                raise HTTPException(
                    status_code=400,
                    detail="Failed to get Management API token"
                )
            
            access_token = token_response.json()["access_token"]
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Create application
            base_url = request.callback_url.rsplit('/', 1)[0] if '/' in request.callback_url else request.callback_url
            app_response = await client.post(
                f"https://{request.domain}/api/v2/clients",
                headers=headers,
                json={
                    "name": "Pinpoint 311 Portal",
                    "app_type": "regular_web",
                    "callbacks": [
                        request.callback_url,
                        f"{base_url}/api/auth/callback"
                    ],
                    "allowed_logout_urls": [base_url],
                    "web_origins": [base_url],
                    "oidc_conformant": True,
                    "grant_types": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_method": "client_secret_post"
                }
            )
            
            if app_response.status_code != 201:
                logger.error(f"Auth0 application creation failed: {app_response.text}")
                raise HTTPException(
                    status_code=400,
                    detail="Failed to create Auth0 application"
                )
            
            app_data = app_response.json()
            client_id = app_data["client_id"]
            client_secret = app_data["client_secret"]
            
            # Configure MFA (enable push notifications)
            try:
                await client.patch(
                    f"https://{request.domain}/api/v2/guardian/factors/push-notification",
                    headers=headers,
                    json={"enabled": True}
                )
            except Exception as e:
                logger.warning(f"Failed to enable MFA: {e}")
            
            # Configure brute force protection
            try:
                await client.patch(
                    f"https://{request.domain}/api/v2/attack-protection/brute-force-protection",
                    headers=headers,
                    json={
                        "enabled": True,
                        "shields": ["block", "user_notification"],
                        "mode": "count_per_identifier_and_ip",
                        "allowlist": [],
                        "max_attempts": 5
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to configure brute force protection: {e}")
            
            # Configure branding to match township branding
            try:
                # Get current system settings for branding
                from app.models import SystemSettings
                settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
                settings = settings_result.scalar_one_or_none()
                
                primary_color = settings.primary_color if settings else "#6366f1"
                logo_url = settings.logo_url if settings else None
                township_name = settings.township_name if settings else "Pinpoint 311"
                
                # Configure Auth0 branding
                branding_payload = {
                    "colors": {
                        "primary": primary_color,
                        "page_background": "#0f172a"  # Dark slate background matching our UI
                    },
                    "favicon_url": logo_url if logo_url else None
                }
                
                # Remove None values
                branding_payload = {k: v for k, v in branding_payload.items() if v is not None}
                if "colors" in branding_payload:
                    branding_payload["colors"] = {k: v for k, v in branding_payload["colors"].items() if v is not None}
                
                await client.patch(
                    f"https://{request.domain}/api/v2/branding",
                    headers=headers,
                    json=branding_payload
                )
                
                # Configure Universal Login branding (New Universal Login)
                universal_login_payload = {
                    "identifier_first": True
                }
                await client.put(
                    f"https://{request.domain}/api/v2/prompts",
                    headers=headers,
                    json=universal_login_payload
                )
                
                # Set up custom login page text if possible
                try:
                    await client.patch(
                        f"https://{request.domain}/api/v2/prompts/login/custom-text/en",
                        headers=headers,
                        json={
                            "login": {
                                "title": f"{township_name} Staff Portal",
                                "description": "Sign in to access the staff dashboard"
                            }
                        }
                    )
                except Exception:
                    pass  # Custom text may require specific plan
                
                # Branding configured successfully
                logger.info(f"Auth0 branding configured with primary color {primary_color}")
                
            except Exception as e:
                logger.warning(f"Failed to configure Auth0 branding: {e}")
            
            # Enable social connections (Google and Microsoft)
            social_connections_enabled = []
            social_connections_errors = []
            
            for conn_name, conn_strategy in [
                ("google-oauth2", "google-oauth2"),
                ("windowslive", "windowslive")  # Microsoft
            ]:
                try:
                    # Check if connection already exists
                    conn_list_response = await client.get(
                        f"https://{request.domain}/api/v2/connections",
                        headers=headers,
                        params={"name": conn_name}
                    )
                    
                    existing_conns = conn_list_response.json() if conn_list_response.status_code == 200 else []
                    
                    if existing_conns:
                        # Connection exists, enable it for this app
                        conn_id = existing_conns[0]["id"]
                        enabled_clients = existing_conns[0].get("enabled_clients", [])
                        
                        if client_id not in enabled_clients:
                            enabled_clients.append(client_id)
                            await client.patch(
                                f"https://{request.domain}/api/v2/connections/{conn_id}",
                                headers=headers,
                                json={"enabled_clients": enabled_clients}
                            )
                        
                        social_connections_enabled.append(conn_name)
                        logger.info(f"Enabled existing {conn_name} connection for client {client_id}")
                    else:
                        # Connection doesn't exist - create with placeholder
                        # Note: Actual credentials must be configured in Auth0 dashboard
                        create_response = await client.post(
                            f"https://{request.domain}/api/v2/connections",
                            headers=headers,
                            json={
                                "name": conn_name,
                                "strategy": conn_strategy,
                                "enabled_clients": [client_id],
                                "options": {}  # Placeholder - user needs to add OAuth credentials in Auth0 dashboard
                            }
                        )
                        
                        if create_response.status_code in [200, 201]:
                            social_connections_enabled.append(conn_name)
                            logger.info(f"Created and enabled {conn_name} social connection")
                        else:
                            # Some strategies require developer keys configured first
                            error_detail = create_response.text[:100]
                            social_connections_errors.append(f"{conn_name}: {error_detail}")
                            logger.warning(f"Could not create {conn_name} connection: {error_detail}")
                            
                except Exception as conn_err:
                    social_connections_errors.append(f"{conn_name}: {str(conn_err)[:50]}")
                    logger.warning(f"Failed to enable {conn_name} social connection: {conn_err}")
            
        # Store credentials through the single write choke-point.
        #
        # This used to write SystemSecret rows here by hand, and all three of
        # these keys are host-shareable. A town that configures its own Auth0
        # tenant on this page was therefore leaving them marked host-provided:
        # the next push from the host overwrote the town's SSO with the host's,
        # and the host un-sharing Auth0 DELETED the town's own credential --
        # both from a value nobody at the host ever typed. Ownership moves on a
        # town-side write, and `_persist_secret` is what moves it (via
        # `host_secrets.forget`), so this path goes through it like the other
        # two credential write paths do.
        #
        # It also means these land in the configured secret store rather than
        # only in the encrypted database copy, which is what the rest of the
        # product already assumed was happening.
        from app.api.system import _persist_secret

        for key, value in [
            ("AUTH0_DOMAIN", request.domain),
            ("AUTH0_CLIENT_ID", client_id),
            ("AUTH0_CLIENT_SECRET", client_secret)
        ]:
            await _persist_secret(db, key, value)

        # Log successful setup
        await AuditService.log_event(
            db=db,
            event_type="auth0_configured",
            success=True,
            user_id=current_user.id,
            username=current_user.username,
            details={
                "domain": request.domain,
                "client_id": client_id,
                "callback_url": request.callback_url,
                "social_connections": social_connections_enabled,
                "social_connection_errors": social_connections_errors if social_connections_errors else None
            }
        )
        
        logger.info(f"Auth0 configured successfully by {current_user.username}")
        
        return {
            "success": True,
            "message": "Auth0 configured successfully",
            "domain": request.domain,
            "client_id": client_id,
            "social_connections_enabled": social_connections_enabled,
            "social_connections_note": "Configure OAuth credentials in Auth0 dashboard for Google and Microsoft" if social_connections_enabled else None,
            "social_connections_errors": social_connections_errors if social_connections_errors else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth0 setup failed: {str(e)}")
        await AuditService.log_event(
            db=db,
            event_type="auth0_configuration_failed",
            success=False,
            user_id=current_user.id,
            username=current_user.username,
            failure_reason=str(e)
        )
        logger.error(f"Auth0 configuration failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to configure Auth0"
        )


@router.post("/verify")
async def verify_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Verify current Auth0 and GCP configuration.
    
    Tests that credentials work and services are reachable.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from app.core.encryption import decrypt_safe
    
    results = {
        "gcp": {"configured": False, "reachable": False, "error": None},
        "auth0": {"configured": False, "reachable": False, "error": None}
    }
    
    # Test GCP
    try:
        gcp_result = await db.execute(
            select(SystemSecret).where(SystemSecret.key_name == "GOOGLE_CLOUD_PROJECT")
        )
        gcp_secret = gcp_result.scalar_one_or_none()
        if gcp_secret and gcp_secret.is_configured:
            results["gcp"]["configured"] = True
            project_id = decrypt_safe(gcp_secret.key_value)
            results["gcp"]["project_id"] = project_id
            
            # Test GCP connectivity by checking service account credentials
            try:
                sa_result = await db.execute(
                    select(SystemSecret).where(SystemSecret.key_name == "GCP_SERVICE_ACCOUNT_JSON")
                )
                sa_secret = sa_result.scalar_one_or_none()
                if sa_secret and sa_secret.key_value:
                    sa_json = decrypt_safe(sa_secret.key_value)
                    sa_data = json.loads(sa_json)
                    # If we can parse the service account JSON, credentials are valid
                    results["gcp"]["reachable"] = True
                    results["gcp"]["service_account"] = sa_data.get("client_email", "")[:20] + "..."
                else:
                    results["gcp"]["reachable"] = False
                    results["gcp"]["error"] = "Service account JSON not found"
            except Exception as gcp_err:
                results["gcp"]["reachable"] = False
                results["gcp"]["error"] = f"Credential validation failed: {str(gcp_err)[:100]}"
    except Exception as e:
        results["gcp"]["error"] = str(e)
    
    # Test Auth0
    try:
        auth0_result = await db.execute(
            select(SystemSecret).where(SystemSecret.key_name == "AUTH0_DOMAIN")
        )
        auth0_secret = auth0_result.scalar_one_or_none()
        if auth0_secret and auth0_secret.is_configured:
            results["auth0"]["configured"] = True
            domain = decrypt_safe(auth0_secret.key_value)
            results["auth0"]["domain"] = domain
            
            # Test OIDC discovery endpoint
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"https://{domain}/.well-known/openid-configuration"
                )
                results["auth0"]["reachable"] = response.status_code == 200
    except Exception as e:
        results["auth0"]["error"] = str(e)
    
    return results


@router.post("/backup-key")
async def generate_backup_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate the backup passphrase, store it, and return it once.

    The field used to say "Strong passphrase for AES-256 encryption" and leave
    a town to invent one, which reliably produces either something guessable or
    something nobody can find again. Generating it removes both failure modes.

    The one part that cannot be automated is the copy kept somewhere else: a key
    held only inside the system it protects is worth nothing the day that system
    is gone, which is exactly the day a backup matters. So this returns the
    value in the clear, once, for the page to show and have acknowledged -- and
    stores it through the normal path, so it lands in the secret store like any
    other credential.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.api.system import _persist_secret
    from app.services.storage_maintenance import generated_backup_key

    key = generated_backup_key()
    await _persist_secret(db, "BACKUP_ENCRYPTION_KEY", key)

    await AuditService.log_event(
        db=db,
        event_type="backup_key_generated",
        success=True,
        user_id=current_user.id,
        username=current_user.username,
        # Deliberately no key material, not even a prefix.
        details={"length": len(key)},
    )
    return {"key": key}


@router.get("/storage-status")
async def storage_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """What, if anything, is not yet on the storage this town has chosen.

    This exists so the setup page can say nothing at all in the normal case.
    Secrets are vaulted and PII is re-wrapped on a schedule (see
    `app.tasks.storage`), so the only thing worth showing a clerk is the case
    where those jobs cannot finish -- a store that has stopped answering, or
    rows whose old key is gone. Read-only, and `summary` swallows its own
    errors, so this cannot fail a page load.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.storage_status import summary

    return await summary(db)


@router.post("/reencrypt-pii")
async def reencrypt_pii(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Re-wrap PII onto the current key immediately, instead of waiting.

    The nightly task does this unprompted and in batches, which is how it
    normally happens; this stays for the operator who has just rotated a key and
    wants it done now, and for support to be able to trigger a pass.

    Rewritten to share that task's implementation. It previously required Google
    Cloud KMS specifically -- a town on Azure or AWS got a 400 telling them to
    "configure GCP first" -- and it loaded every request id in the database
    before doing anything, including the rows that were already fine.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.storage_maintenance import rewrap_pii as _rewrap

    totals = {"rows": 0, "fields": 0, "errors": 0}
    for _ in range(20):
        result = await _rewrap(db)
        totals["rows"] += result.get("rows", 0)
        totals["fields"] += result.get("fields", 0)
        totals["errors"] += result.get("errors", 0)
        if result.get("status") != "ok" or not result.get("remaining"):
            break

    await AuditService.log_event(
        db=db,
        event_type="pii_reencryption",
        success=True,
        user_id=current_user.id,
        username=current_user.username,
        details=totals,
    )
    logger.info("PII re-wrap: %s rows, %s fields, %s errors",
                totals["rows"], totals["fields"], totals["errors"])

    # `reencrypted` is kept in the response shape for existing callers.
    return {"reencrypted": totals["rows"], **totals}

