"""
Google Secret Manager Service

Securely retrieves secrets from Google Secret Manager.
Falls back to database storage for local development.

Secrets are bundled into 6 groups to fit the free tier:
- secret-auth: Auth0 SSO credentials
- secret-smtp: Email configuration
- secret-sms: SMS provider configuration
- secret-google: Google Cloud API keys
- secret-backup: S3/backup configuration
- secret-config: Township-specific settings
"""

import json
import logging
import os
import threading
import time
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# TTL cache for secret bundles: name -> (data, expiry_epoch). A TTL means a
# secret rotated in the manager out-of-band is picked up without a restart.
_secret_cache: Dict[str, Tuple[Dict[str, str], float]] = {}
_cache_lock = threading.Lock()
_bundle_locks: Dict[str, threading.Lock] = {}
_config: Dict[str, Any] = {"use_gcp": None}
_sm_client = None


def _cache_ttl() -> int:
    try:
        return int(os.getenv("SECRET_CACHE_TTL_SECONDS", "900"))
    except ValueError:
        return 900


def _cache_get(name: str) -> Optional[Dict[str, str]]:
    entry = _secret_cache.get(name)
    if not entry:
        return None
    data, expiry = entry
    if expiry < time.time():
        _secret_cache.pop(name, None)
        return None
    return data


def _cache_put(name: str, data: Dict[str, str]) -> None:
    _secret_cache[name] = (data, time.time() + _cache_ttl())


def _bundle_lock(name: str) -> threading.Lock:
    """A per-bundle lock serializes read-modify-write within this process so
    concurrent writes to the same bundle don't clobber each other's keys."""
    with _cache_lock:
        lk = _bundle_locks.get(name)
        if lk is None:
            lk = threading.Lock()
            _bundle_locks[name] = lk
        return lk


def _get_project_from_db() -> Optional[str]:
    """Get GCP project ID from database."""
    try:
        from app.db.session import sync_engine
        from sqlalchemy import text
        
        with sync_engine.connect() as conn:
            result = conn.execute(
                text("SELECT key_value FROM system_secrets WHERE key_name = 'GOOGLE_CLOUD_PROJECT'")
            )
            row = result.fetchone()
            if row and row[0]:
                from app.core.encryption import decrypt
                return decrypt(row[0])
    except Exception:
        pass  # Database not available yet during startup
    return None


def _get_sm_client():
    """Get Secret Manager client using encrypted service account key."""
    global _sm_client
    
    if _sm_client:
        return _sm_client
    
    try:
        from google.cloud import secretmanager
        from google.oauth2 import service_account
        import json as json_lib
        
        # Try service account from database (encrypted storage)
        try:
            from app.db.session import sync_engine
            from sqlalchemy import text
            
            with sync_engine.connect() as conn:
                result = conn.execute(
                    text("SELECT key_value FROM system_secrets WHERE key_name = 'GCP_SERVICE_ACCOUNT_JSON'")
                )
                row = result.fetchone()
                if row and row[0]:
                    from app.core.encryption import decrypt
                    sa_json = decrypt(row[0])
                    sa_data = json_lib.loads(sa_json)
                    credentials = service_account.Credentials.from_service_account_info(sa_data)
                    _sm_client = secretmanager.SecretManagerServiceClient(credentials=credentials)
                    logger.info("Secret Manager client initialized with encrypted service account key")
                    return _sm_client
        except Exception as db_err:
            logger.debug(f"Could not load SM credentials from database: {db_err}")
        
        # Fall back to default credentials (ADC)
        _sm_client = secretmanager.SecretManagerServiceClient()
        return _sm_client
    except Exception as e:
        logger.warning(f"Failed to initialize Secret Manager client: {e}")
        return None




# The four places a town's credentials can knowingly be kept.
#
# `database` is on this list on purpose. The encrypted database is a supported
# store -- it is the normal state of a small self-hosted install, and
# test_secret_store_ordering has said so for a while -- so a town whose cloud
# procurement is unfinished must be able to choose it and get on with setup. The
# gate this list exists for is about consent, not capability.
SECRET_STORES = ("google", "azure", "aws", "database")


def _secrets_provider() -> str:
    """Which secret store this town chose, or "" if it has not chosen one.

    Unset used to mean "google", and that default is the shape of accidental
    behaviour this codebase has been removing elsewhere: a town that never
    answered the question got Google Secret Manager as an answer, and if Google
    was not actually reachable every credential fell through to the encrypted
    database instead, silently.

    Which is the reason "" now means "". A credential saved before a store is
    chosen lands in the database, `vault_secrets` later sweeps it into the store
    and scrubs the database copy, and the live row heals -- but a *backup* taken
    inside that window keeps the secret forever, and backups go off-site. A
    pg_dump of this instance contains `COPY public.system_secrets (id, key_name,
    key_value, ...)`. Sweeping the live row does not reach a dump already taken.
    So the setup page refuses credentials until this answers something, and the
    town decides where its keys go before it has any.

    Callers that need the *effective* behaviour of an unanswered town -- reading
    a secret written before this existed -- treat "" as the historical
    fall-through, which is Google when a project is configured and the database
    otherwise. Callers deciding whether a choice has been made use
    `store_chosen()`.
    """
    val = os.getenv("SECRETS_PROVIDER")
    if val:
        return val.strip().lower()
    try:
        from app.core.encryption import _get_config_sync
        return (_get_config_sync("SECRETS_PROVIDER") or "").strip().lower()
    except Exception:
        # Unreadable is not the same as unchosen, but this is the safe
        # direction: it refuses credentials rather than filing them somewhere
        # nobody picked.
        return ""


def store_chosen() -> bool:
    """Whether a human has said where this town's credentials are kept."""
    return _secrets_provider() in SECRET_STORES


def external_store_configured() -> bool:
    """Is there a store OUTSIDE the encrypted database for secrets to go to.

    Not the same question as `store_chosen`, and the difference is the whole
    reason this exists. "database" is a real answer to "where do the keys
    live" -- a supported, deliberate choice -- so `store_chosen()` is True for
    it. But there is then nothing external to write to, and `set_secret`
    returns False for every key by design rather than by failure.

    Callers that read that False as "the external store would not take it"
    need this to tell the two apart. Without it, every credential saved on a
    database-store deployment is reported as a write that only reached the
    database and is about to disappear -- which is alarming, wrong, and
    describes the deployment working exactly as configured. An unanswered
    store ("") is external-less too: a value saved before the town has chosen
    lands in the database on purpose and `vault_secrets` sweeps it in later.
    """
    return _secrets_provider() in ("google", "azure", "aws")


def _is_gcp_available() -> bool:
    """Check if Google Cloud Secret Manager is available."""
    if _config["use_gcp"] is not None:
        return _config["use_gcp"]
    
    # Check for project ID in env or database
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or _get_project_from_db()
    if not project:
        _config["use_gcp"] = False
        logger.info("Google Cloud Project not set, using database for secrets")
        return False
    
    # Try to get a client
    client = _get_sm_client()
    if client:
        _config["use_gcp"] = True
        logger.info(f"Using Google Secret Manager for project: {project}")
        return True
    
    _config["use_gcp"] = False
    return False


def _get_secret_from_gcp(secret_name: str, force_refresh: bool = False) -> Optional[Dict[str, str]]:
    """Fetch a secret bundle from Google Secret Manager (TTL-cached)."""
    if not force_refresh:
        cached = _cache_get(secret_name)
        if cached is not None:
            return cached

    try:
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or _get_project_from_db()
        client = _get_sm_client()
        
        if not client or not project:
            return None
        
        name = f"projects/{project}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        
        # Track Secret Manager usage -- but never at the cost of the caller.
        #
        # This used to fall back to `asyncio.run(_track())` when no event loop
        # was running, and that fallback is reached on precisely the path that
        # matters most: a secret *write*. set_secret -> run_in_executor ->
        # set_secret_sync -> _get_secret_from_gcp(force_refresh=True) runs in a
        # worker thread with no loop of its own, so `asyncio.run` started a
        # second event loop and opened asyncpg connections through a pool whose
        # connections belong to the main one. asyncpg failed with "got Future
        # attached to a different loop" and left the pool wedged -- "got result
        # for unknown protocol state 3" -- which surfaced as a 500 on the save
        # request and on unrelated queries after it.
        #
        # The consequence was worse than a failed metric: the crash happened
        # before add_secret_version, so the new credential was never written and
        # the previous value stayed in the store. An administrator pasted a key,
        # saw a failure or a success, and the system went on using the old one.
        #
        # Metering a single secret read is not worth any of that. With no loop of
        # our own to schedule on, skip it.
        try:
            import asyncio

            async def _track():
                from app.db.session import SessionLocal
                from app.services.api_usage import track_api_usage
                async with SessionLocal() as db:
                    await track_api_usage(
                        db,
                        service_name="secret_manager",
                        operation="access_secret",
                        api_calls=1
                    )

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                loop.create_task(_track())
            else:
                logger.debug(
                    "Secret Manager read not metered: no event loop in this thread. "
                    "This is the secret-write path; metering it would cross event loops."
                )
        except Exception as track_err:
            logger.debug(f"Failed to track Secret Manager usage: {track_err}")
        
        secret_data = json.loads(response.payload.data.decode("UTF-8"))
        _cache_put(secret_name, secret_data)
        return secret_data
    except Exception as e:
        from app.core.sanitize import sanitize_for_log

        # A bundle that does not exist yet is the ordinary state of a town that
        # has not configured that group of settings -- there is no `secret-backup`
        # until somebody sets up backups. Cached as empty and logged at debug, so
        # it costs one lookup per TTL rather than one per key, and does not fill
        # the log with warnings about a feature nobody switched on.
        #
        # This matters more since /secrets began resolving every key through
        # here: reporting "is it really there" means asking about the absent ones
        # too, and the absent ones are exactly the ones with no bundle.
        if "not found" in str(e) or "has no versions" in str(e):
            _cache_put(secret_name, {})
            logger.debug(f"Secret bundle {secret_name} does not exist yet")
            return None
        logger.warning(f"Failed to get secret from GCP: {sanitize_for_log(str(e))}")
        return None


# Keys that must keep their encrypted database copy, and must never be scrubbed
# out of it after a migration.
#
# Two reasons, and both end in the same silent failure.
#
# Circularity: the Azure Key Vault and AWS credentials below are the credentials
# *for* the secret store. Moving them into the store they unlock leaves nothing
# able to open it -- which is why GCP's two were already excluded, and the other
# clouds' equivalents were not.
#
# Reader mismatch: KMS configuration is read by encryption._get_config_sync and
# aws_kms._cfg, which look at the environment and then the database and never
# consult Secret Manager at all. Migrating those keys therefore succeeded, and
# verified, and then scrubbed the only copy anything could read. PII encryption
# would quietly fall back to wrapping with the application SECRET_KEY -- no
# error, no log above DEBUG, and nothing on the page to say the KMS a town
# selected had stopped being used.
DB_REQUIRED_KEYS = frozenset({
    # Google bootstrap -- unlocks Secret Manager itself.
    "GCP_SERVICE_ACCOUNT_JSON", "GOOGLE_CLOUD_PROJECT",
    # Azure Key Vault's own credentials.
    "AZURE_KEYVAULT_URL", "AZURE_KEYVAULT_KEY", "AZURE_KEYVAULT_CLIENT_ID",
    "AZURE_KEYVAULT_CLIENT_SECRET", "AZURE_KEYVAULT_API_VERSION",
    "AZURE_KEYVAULT_SCOPE", "AZURE_TENANT_ID", "AZURE_AUTHORITY",
    # AWS Secrets Manager / KMS credentials.
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_REGION", "AWS_SECRETS_PREFIX", "AWS_KMS_KEY_ID",
    # KMS selection and key path, read only by the synchronous readers.
    "KMS_PROVIDER", "KMS_LOCATION", "KMS_KEY_RING", "KMS_KEY_ID",
    # Which store to use, read by _secrets_provider() -- env, then database,
    # never the store itself, because it is the answer to which store that is.
    # Scrubbing it silently reverts a town on Azure or AWS to the Google
    # default, at which point nothing can read any of its secrets.
    "SECRETS_PROVIDER",
})


async def get_secret(key_name: str) -> Optional[str]:
    """
    Get a single secret value.
    
    Uses Google Secret Manager if available, falls back to database.
    
    Secret key mappings:
    - AUTH0_* -> secret-auth bundle
    - SMTP_* -> secret-smtp bundle
    - SMS_*, TWILIO_* -> secret-sms bundle
    - GOOGLE_*, VERTEX_* -> secret-google bundle
    - BACKUP_* -> secret-backup bundle
    - Others -> secret-config bundle
    """
    # The encrypted database, chosen on purpose.
    #
    # Reached before the Google branch rather than falling through to it: a town
    # that picked the database may well have Google Cloud credentials on file
    # for AI or maps, and `_is_gcp_available()` would then quietly start reading
    # its keys out of Secret Manager -- which is a different store from the one
    # it chose, and the one whose contents nobody put there.
    if _secrets_provider() == "database":
        return await _get_secret_from_db(key_name)

    # Azure Key Vault backend (host-selected via SECRETS_PROVIDER=azure)
    if _secrets_provider() == "azure":
        try:
            from app.core import azure_keyvault
            if azure_keyvault.is_configured():
                val = azure_keyvault.get_secret(key_name)
                if val is not None:
                    return val
        except Exception as e:
            from app.core.sanitize import sanitize_for_log
            logger.warning(f"Azure Key Vault secret read failed for {sanitize_for_log(key_name)}")
        return await _get_secret_from_db(key_name)

    # AWS Secrets Manager backend (host-selected via SECRETS_PROVIDER=aws)
    if _secrets_provider() == "aws":
        try:
            from app.core import aws_secretsmanager
            if aws_secretsmanager.is_configured():
                val = aws_secretsmanager.get_secret(key_name)
                if val is not None:
                    return val
        except Exception:
            from app.core.sanitize import sanitize_for_log
            logger.warning(f"AWS Secrets Manager read failed for {sanitize_for_log(key_name)}")
        return await _get_secret_from_db(key_name)

    if _is_gcp_available():
        # Determine which bundle this key belongs to
        if key_name.startswith("AUTH0_"):
            bundle = _get_secret_from_gcp("secret-auth")
        elif key_name.startswith("SMTP_") or key_name.startswith("EMAIL_"):
            bundle = _get_secret_from_gcp("secret-smtp")
        elif key_name.startswith("SMS_") or key_name.startswith("TWILIO_"):
            bundle = _get_secret_from_gcp("secret-sms")
        elif key_name.startswith("GOOGLE_") or key_name.startswith("VERTEX_"):
            bundle = _get_secret_from_gcp("secret-google")
        elif key_name.startswith("BACKUP_"):
            bundle = _get_secret_from_gcp("secret-backup")
        else:
            bundle = _get_secret_from_gcp("secret-config")
        
        if bundle and key_name in bundle:
            return bundle[key_name]
    
    # Fallback to database
    return await _get_secret_from_db(key_name)


async def _get_secret_from_db(key_name: str) -> Optional[str]:
    """Fallback: get secret from encrypted database storage."""
    from app.db.session import SessionLocal
    from app.models import SystemSecret
    from app.core.encryption import decrypt_safe
    from sqlalchemy import select
    
    try:
        async with SessionLocal() as db:
            result = await db.execute(
                select(SystemSecret).where(SystemSecret.key_name == key_name)
            )
            secret = result.scalar_one_or_none()
            
            if secret and secret.key_value and secret.is_configured:
                return decrypt_safe(secret.key_value)
            return None
    except Exception as e:
        from app.core.sanitize import sanitize_for_log
        logger.error(f"Failed to get secret from database: {sanitize_for_log(str(e))}")
        return None


async def get_secrets_bundle(prefix: str) -> Dict[str, str]:
    """
    Get all secrets with a given prefix.

    Example: get_secrets_bundle("SMTP_") returns all SMTP settings.

    Only Google's path is bundle-shaped -- Key Vault and Secrets Manager store
    one secret per key, with no prefix query -- so for those this falls through
    to the database, which holds only the keys that could not be migrated. It is
    therefore not a safe way to read credentials on those stores, and nothing
    does: `get_secret` is the supported reader and handles all three. Kept for
    the Google bundle-inspection case only.
    """
    result = {}

    # "" is included because it is what an unanswered town reads as, and this
    # function's job is reading back what is already there. Refusing to look in
    # the bundle would hide credentials a town saved before the choice existed.
    if _secrets_provider() in ("", "google") and _is_gcp_available():
        # Map prefix to bundle name
        if prefix.startswith("AUTH0"):
            bundle = _get_secret_from_gcp("secret-auth")
        elif prefix.startswith("SMTP") or prefix.startswith("EMAIL"):
            bundle = _get_secret_from_gcp("secret-smtp")
        elif prefix.startswith("SMS") or prefix.startswith("TWILIO"):
            bundle = _get_secret_from_gcp("secret-sms")
        elif prefix.startswith("GOOGLE") or prefix.startswith("VERTEX"):
            bundle = _get_secret_from_gcp("secret-google")
        elif prefix.startswith("BACKUP"):
            bundle = _get_secret_from_gcp("secret-backup")
        else:
            bundle = _get_secret_from_gcp("secret-config")
        
        if bundle:
            for key, value in bundle.items():
                if key.startswith(prefix):
                    result[key] = value
            if result:
                return result
    
    # Fallback to database
    from app.db.session import SessionLocal
    from app.models import SystemSecret
    from app.core.encryption import decrypt_safe
    from sqlalchemy import select
    
    try:
        async with SessionLocal() as db:
            query = select(SystemSecret).where(
                SystemSecret.key_name.like(f"{prefix}%")
            )
            secrets = await db.execute(query)
            
            for secret in secrets.scalars():
                if secret.key_value and secret.is_configured:
                    result[secret.key_name] = decrypt_safe(secret.key_value)
    except Exception as e:
        logger.error(f"Failed to get secrets with prefix {prefix}: {e}")
    
    return result


def clear_cache(bundle: Optional[str] = None, key_name: Optional[str] = None) -> None:
    """Forget cached secrets. One bundle by default, everything if asked.

    This used to always drop all of it. Saving a single Maps key therefore threw
    away the cached auth, smtp, sms, config and google bundles too, and the next
    few requests refetched every one of them -- so a normal trip through the
    setup page, which saves several times, caused a small stampede each time.
    Reads are inside Google's free allowance so this was never a bill, but they
    are latency on somebody's request and cold starts on every deploy.

    `key_name` is the convenience the callers actually want: they know which
    secret they just wrote, not which bundle it belongs to.
    """
    global _secret_cache
    if key_name and not bundle:
        bundle = _get_bundle_name(key_name)
    if bundle:
        _secret_cache.pop(bundle, None)
        return
    _secret_cache = {}


# ============================================================================
# Secret Manager Write Operations
# ============================================================================

def _get_bundle_name(key_name: str) -> str:
    """Determine which Secret Manager bundle a key belongs to."""
    if key_name.startswith("AUTH0_"):
        return "secret-auth"
    elif key_name.startswith("SMTP_") or key_name.startswith("EMAIL_"):
        return "secret-smtp"
    elif key_name.startswith("SMS_") or key_name.startswith("TWILIO_"):
        return "secret-sms"
    elif key_name.startswith("GOOGLE_") or key_name.startswith("VERTEX_") or key_name.startswith("KMS_") or key_name.startswith("GCP_"):
        return "secret-google"
    elif key_name.startswith("BACKUP_"):
        return "secret-backup"
    else:
        return "secret-config"


def _create_secret_if_not_exists(client, project: str, secret_id: str) -> bool:
    """Create a secret in Secret Manager if it doesn't exist."""
    try:
        parent = f"projects/{project}"
        secret_name = f"{parent}/secrets/{secret_id}"
        
        # Try to get the secret first
        try:
            client.get_secret(request={"name": secret_name})
            return True  # Already exists
        except Exception:
            pass  # Doesn't exist, create it
        
        # Create the secret
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
        logger.info(f"Created secret: {secret_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to create secret {secret_id}: {e}")
        return False


def _keep_versions() -> int:
    """How many secret versions to keep. Newest first, including the live one."""
    try:
        return max(1, int(os.getenv("SECRET_KEEP_VERSIONS", "3")))
    except ValueError:
        return 3


def _prune_versions(client, secret_path: str) -> int:
    """Destroy versions this write superseded. Returns how many went.

    Google bills per *active* secret version, and every write adds one. Nothing
    ever retired the old ones, so a bundle written a few dozen times cost a few
    dozen times as much as the single version anything reads. On the deployment
    this was written against there were 166 active versions across five bundles
    -- about $9.96/month -- of which exactly five were reachable. Pruning to the
    newest three brought it to about $0.90.

    Three, not one: `latest` is what every read resolves, and keeping two behind
    it leaves room to roll a bad credential back by hand without reaching for a
    backup.

    Never destroys what `latest` resolves to, and treats a version that is
    already gone as success -- a concurrent writer pruning the same bundle is
    expected, not an error. A failure here is logged and swallowed: the write
    itself already succeeded, and refusing to return that because cleanup
    stumbled would turn a billing tidy-up into a lost credential.
    """
    keep = _keep_versions()
    try:
        from google.api_core import exceptions as gexc

        live = sorted(
            (int(v.name.rsplit("/", 1)[-1])
             for v in client.list_secret_versions(request={"parent": secret_path})
             if v.state.name == "ENABLED"),
            reverse=True,
        )
        if len(live) <= keep:
            return 0

        latest = int(client.access_secret_version(
            request={"name": f"{secret_path}/versions/latest"}).name.rsplit("/", 1)[-1])

        destroyed = 0
        for number in live[keep:]:
            if number == latest:
                # Should be impossible -- latest is the newest enabled version --
                # but this is the one mistake that would cost a credential.
                continue
            try:
                client.destroy_secret_version(
                    request={"name": f"{secret_path}/versions/{number}"})
                destroyed += 1
            except gexc.FailedPrecondition:
                pass          # already destroyed by another writer
            except gexc.NotFound:
                pass
        if destroyed:
            logger.info("Retired %d superseded version(s) of %s",
                        destroyed, secret_path.rsplit("/", 1)[-1])
        return destroyed
    except Exception as exc:
        logger.warning("Could not prune old versions of %s: %s",
                       secret_path.rsplit("/", 1)[-1], exc)
        return 0


def set_secret_sync(key_name: str, value: str) -> bool:
    """
    Write a secret to Google Secret Manager (sync version).
    
    Secrets are bundled into JSON objects to stay within free tier limits.
    Returns True if successful, False otherwise.
    """
    # The encrypted database, chosen on purpose. There is no external store to
    # write to, and False is exactly what the caller needs to hear: it means
    # "this is in the database", which is where the town asked for it.
    if _secrets_provider() == "database":
        return False

    # Azure Key Vault backend
    if _secrets_provider() == "azure":
        try:
            from app.core import azure_keyvault
            if azure_keyvault.is_configured():
                return azure_keyvault.set_secret(key_name, value)
        except Exception as e:
            logger.error(f"Azure Key Vault secret write failed for {key_name}: {e}")
        return False

    # AWS Secrets Manager backend
    if _secrets_provider() == "aws":
        try:
            from app.core import aws_secretsmanager
            if aws_secretsmanager.is_configured():
                return aws_secretsmanager.set_secret(key_name, value)
        except Exception as e:
            logger.error(f"AWS Secrets Manager write failed for {key_name}: {e}")
        return False

    if not _is_gcp_available():
        logger.debug(f"Secret Manager not available, skipping write for {key_name}")
        return False
    
    try:
        
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or _get_project_from_db()
        client = _get_sm_client()
        
        if not client or not project:
            return False
        
        bundle_name = _get_bundle_name(key_name)

        # Serialize writes to this bundle within the process, and read the
        # FRESHEST copy (bypassing the cache) right before merging, so a
        # concurrent write doesn't lose another key via a stale read.
        with _bundle_lock(bundle_name):
            existing_bundle = dict(_get_secret_from_gcp(bundle_name, force_refresh=True) or {})
            existing_bundle[key_name] = value

            # Create secret if it doesn't exist
            if not _create_secret_if_not_exists(client, project, bundle_name):
                return False

            # Add new version with updated bundle
            secret_path = f"projects/{project}/secrets/{bundle_name}"
            payload = json.dumps(existing_bundle).encode("UTF-8")

            client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": payload},
                }
            )

            # Refresh the cache with exactly what we just wrote.
            _cache_put(bundle_name, existing_bundle)

            # And retire the versions this one just superseded.
            _prune_versions(client, secret_path)

        logger.info(f"Secret {key_name} written to Secret Manager bundle {bundle_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to write secret {key_name} to Secret Manager: {e}")
        return False


async def set_secret(key_name: str, value: str) -> bool:
    """
    Write a secret to Google Secret Manager (async version).

    Wraps the sync version for async compatibility.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, set_secret_sync, key_name, value)


def delete_secret_sync(key_name: str) -> bool:
    """Remove a key from the configured store. Returns whether it is gone.

    Two callers have to be able to take back what was written: the secret-store
    round-trip check -- an empty `PINPOINT_SELFTEST_*` left in a bundle forever
    is exactly the litter an earlier probe left behind in Google as
    `test-write-check` -- and govtech-integration disconnect, which removes the
    INTEGRATION_<PLATFORM>_<FIELD> values the connection wrote so a vendor
    client secret an admin believes they revoked by pressing Disconnect does not
    stay live and unlisted. The setup page still clears a credential by storing
    an empty string, which is the right behaviour for a real key (the row stays,
    the card still lists it).

    On Google the keys live inside a shared JSON bundle, so this rewrites the
    bundle without that key rather than deleting anything; on Azure and AWS each
    key is its own secret and is deleted outright.
    """
    provider = _secrets_provider()

    if provider == "database":
        # Nothing outside the database holds it, so there is nothing here to
        # take back. The row itself is the caller's business.
        return False

    if provider == "azure":
        try:
            from app.core import azure_keyvault
            if not azure_keyvault.is_configured():
                return False
            return azure_keyvault.delete_secret(key_name)
        except Exception as e:
            logger.warning(f"Azure Key Vault delete failed for {key_name}: {e}")
            return False

    if provider == "aws":
        try:
            from app.core import aws_secretsmanager
            if not aws_secretsmanager.is_configured():
                return False
            return aws_secretsmanager.delete_secret(key_name)
        except Exception as e:
            logger.warning(f"AWS Secrets Manager delete failed for {key_name}: {e}")
            return False

    if not _is_gcp_available():
        return False

    try:
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or _get_project_from_db()
        client = _get_sm_client()
        if not client or not project:
            return False

        bundle_name = _get_bundle_name(key_name)
        # Same lock and same freshest-read as the write path: dropping one key
        # rewrites the whole bundle, so a concurrent write would otherwise be
        # lost.
        with _bundle_lock(bundle_name):
            bundle = dict(_get_secret_from_gcp(bundle_name, force_refresh=True) or {})
            if key_name not in bundle:
                _cache_put(bundle_name, bundle)
                return True
            bundle.pop(key_name)
            secret_path = f"projects/{project}/secrets/{bundle_name}"
            client.add_secret_version(request={
                "parent": secret_path,
                "payload": {"data": json.dumps(bundle).encode("UTF-8")},
            })
            _cache_put(bundle_name, bundle)
            # The removed value lives on in the superseded versions until these
            # are retired, which is the whole point of pruning here too.
            _prune_versions(client, secret_path)
        return True
    except Exception as e:
        logger.warning(f"Failed to delete secret {key_name} from Secret Manager: {e}")
        return False


async def delete_secret(key_name: str) -> bool:
    """Remove a secret from the vault of record and from the database fallback.

    Both, unconditionally: a town that migrated to an external vault may still
    have an encrypted copy in `system_secrets` from before the migration, and
    deleting only the one the current provider reads would leave the other
    readable by the next `get_secret` fallback.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    vault = await loop.run_in_executor(None, delete_secret_sync, key_name)
    database = await _delete_secret_from_db(key_name)
    return vault or database


async def _delete_secret_from_db(key_name: str) -> bool:
    """Drop the encrypted-in-database copy of a secret. Never raises."""
    from sqlalchemy import delete as sql_delete

    from app.db.session import SessionLocal
    from app.models import SystemSecret

    try:
        async with SessionLocal() as db:
            result = await db.execute(
                sql_delete(SystemSecret).where(SystemSecret.key_name == key_name)
            )
            await db.commit()
            return bool(result.rowcount)
    except Exception as e:
        from app.core.sanitize import sanitize_for_log
        logger.error(f"Failed to remove secret from database: {sanitize_for_log(str(e))}")
        return False


async def migrate_to_secret_manager() -> Dict[str, Any]:
    """
    Migrate all secrets from the database into the configured secret store.

    Works against whichever store is selected -- Google Secret Manager, Azure
    Key Vault or AWS Secrets Manager. It was previously gated on Google alone,
    so a town on Azure or AWS had its credentials written to the vault by the
    save path and its database copies left behind forever.

    SAFETY: Only scrubs secrets from the database AFTER verifying they can be
    read back from the store. This prevents data loss if the write fails.

    Returns a summary of migrated secrets.
    """
    from app.db.session import SessionLocal
    from app.models import SystemSecret
    from app.core.encryption import decrypt_safe
    from sqlalchemy import select

    from app.services.storage_maintenance import store_reachable

    if not store_reachable():
        return {
            "status": "skipped",
            "reason": "Secret Manager not available",
            "migrated": 0
        }

    migrated = []
    verified = []
    failed = []
    skipped = []
    scrubbed = []
    # Kept apart from `failed`: a write that the store refused is a different
    # problem from a value this process cannot read, and the second one names
    # its own fix (the key that encrypted it).
    unreadable = []
    
    # Keys that should NOT be migrated (they're needed to access Secret Manager itself)
    bootstrap_keys = set(DB_REQUIRED_KEYS)
    
    try:
        async with SessionLocal() as db:
            result = await db.execute(
                select(SystemSecret).where(SystemSecret.is_configured == True)
            )
            secrets = result.scalars().all()
            
            for secret in secrets:
                if secret.key_name in bootstrap_keys:
                    skipped.append(secret.key_name)
                    continue
                
                if not secret.key_value:
                    skipped.append(secret.key_name)
                    continue
                
                # Decrypt the value from database.
                #
                # A falsy result is not "nothing to do". `decrypt_safe` returns
                # a legacy plaintext value unchanged and an empty string when a
                # value that looks encrypted will not decrypt -- and the row got
                # past the `key_value` check above, so empty means the latter:
                # the value is stored under a SECRET_KEY this process does not
                # have. That is a stuck credential, and this used to file it
                # under `skipped`, where it read as "already in the store or
                # held in the database by design". It is neither. This
                # deployment has rotated SECRET_KEY before, so the case is real
                # rather than theoretical.
                #
                # The exception branch is the same condition arriving by a
                # different route (`decrypt_safe` swallows InvalidToken, but
                # `decrypt_pii` for a KMS-wrapped value may not), so both land
                # in the same list.
                try:
                    plaintext = decrypt_safe(secret.key_value)
                    if not plaintext:
                        unreadable.append({
                            "key": secret.key_name,
                            "error": "could not be decrypted with the current SECRET_KEY",
                        })
                        continue
                except Exception as exc:
                    unreadable.append({
                        "key": secret.key_name,
                        "error": f"could not be decrypted: {type(exc).__name__}",
                    })
                    continue
                
                # Write to Secret Manager
                success = await set_secret(secret.key_name, plaintext)
                
                if success:
                    migrated.append(secret.key_name)
                else:
                    failed.append({"key": secret.key_name, "error": "write failed"})
            
            # CRITICAL: Verify each migrated secret can be read back from GCP
            # Only scrub if verification passes
            clear_cache()  # Clear cache to force fresh reads
            
            for key_name in migrated:
                try:
                    # Try to read the secret back from GCP
                    read_value = await get_secret(key_name)
                    if read_value:
                        verified.append(key_name)
                    else:
                        failed.append({"key": key_name, "error": "verification failed - could not read back from GCP"})
                except Exception as e:
                    failed.append({"key": key_name, "error": f"verification failed: {str(e)}"})
            
            # Only scrub VERIFIED secrets from database, and never one that a
            # synchronous reader depends on -- see DB_REQUIRED_KEYS.
            if verified:
                for secret in secrets:
                    if secret.key_name in verified and secret.key_name not in DB_REQUIRED_KEYS:
                        # Clear the encrypted value but keep the record
                        secret.key_value = None
                        scrubbed.append(secret.key_name)
                
                await db.commit()
                logger.info(f"Scrubbed {len(scrubbed)} verified secrets from database after migration")
            elif migrated or failed:
                # Only worth a warning if something was actually attempted. A
                # pass that had nothing to move verifies nothing by definition,
                # and that used to log a warning every hour.
                logger.warning("No secrets verified - database values NOT scrubbed")

        from app.services.storage_maintenance import migration_status

        return {
            **migration_status(
                verified=len(verified),
                failed=len(failed),
                skipped=len(skipped),
                unreadable=len(unreadable),
            ),
            "migrated": len(migrated),
            "migrated_keys": migrated,
            "verified": len(verified),
            "verified_keys": verified,
            "scrubbed": len(scrubbed),
            "scrubbed_keys": scrubbed,
            "skipped": len(skipped),
            "skipped_keys": skipped,
            "failed": len(failed),
            "failed_keys": failed,
            "unreadable": len(unreadable),
            "unreadable_keys": unreadable,
        }

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "migrated": len(migrated),
            "migrated_keys": migrated,
            "warning": "Database values NOT scrubbed due to error"
        }

