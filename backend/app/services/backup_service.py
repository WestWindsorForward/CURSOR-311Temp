"""
Database Backup Service

Provides automated encrypted backups to S3-compatible object storage.
Supports Oracle Cloud Object Storage, AWS S3, and other S3-compatible services.
"""

import os
import logging
import tempfile
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)

# Backup file naming convention: backup_YYYYMMDD_HHMMSS.sql.gpg
BACKUP_PREFIX = "db_backup_"
BACKUP_EXTENSION = ".sql.gpg"

# Whether this process has already said that backups are not configured. See
# `get_backup_config` for why it is a latch and not a plain warning.
_CONFIG_INCOMPLETE_LOGGED = False


async def get_backup_config() -> Optional[Dict[str, str]]:
    """Get backup configuration from Secret Manager."""
    global _CONFIG_INCOMPLETE_LOGGED
    try:
        from app.services.secret_manager import get_secret
        
        # Get all backup-related secrets from Secret Manager
        config = {}
        secret_keys = [
            "BACKUP_S3_ENDPOINT",
            "BACKUP_S3_BUCKET",
            "BACKUP_S3_ACCESS_KEY",
            "BACKUP_S3_SECRET_KEY",
            "BACKUP_ENCRYPTION_KEY",
            "BACKUP_S3_REGION"
        ]
        
        for key in secret_keys:
            value = await get_secret(key)
            if value:
                config[key] = value
        
        # Check required keys
        required = ["BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY", "BACKUP_ENCRYPTION_KEY"]
        if not all(k in config for k in required):
            # Once per process. This is read on every backup-adjacent path,
            # including the health check that runs every fifteen minutes, so a
            # deployment that has not set up off-site backups was filling its
            # log with a line that never changed and never would -- which is
            # how a log stops being read.
            #
            # Nothing is lost by demoting it: "no backups configured" already
            # reaches the admin as a warning on the health card
            # (proactive_health._backup_age_check, key "backup"), which is
            # where a persistent *state* belongs. A log line is for the first
            # time a process notices, and for that it is still a warning.
            if _CONFIG_INCOMPLETE_LOGGED:
                logger.debug("Backup configuration incomplete - missing required secrets")
            else:
                logger.warning("Backup configuration incomplete - missing required secrets")
                _CONFIG_INCOMPLETE_LOGGED = True
            return None

        # Configured now, so a later un-configuration is news again.
        _CONFIG_INCOMPLETE_LOGGED = False
        return config
    except Exception as e:
        logger.error(f"Failed to get backup config: {e}")
        return None



def get_s3_client(config: Dict[str, str]):
    """Create S3 client from config."""
    endpoint = config.get("BACKUP_S3_ENDPOINT")
    region = config.get("BACKUP_S3_REGION", "us-ashburn-1")
    
    client_config = BotoConfig(
        signature_version='s3v4',
        retries={'max_attempts': 3}
    )
    
    return boto3.client(
        's3',
        endpoint_url=endpoint if endpoint else None,
        aws_access_key_id=config["BACKUP_S3_ACCESS_KEY"],
        aws_secret_access_key=config["BACKUP_S3_SECRET_KEY"],
        region_name=region,
        config=client_config
    )


def create_database_dump(output_path: str) -> bool:
    """Create a PostgreSQL dump file."""
    try:
        # Get database connection from environment
        db_url = os.environ.get("DATABASE_URL", "")
        
        # Parse connection string: postgresql+asyncpg://user:pass@host/dbname
        # Convert to pg_dump format
        if "postgresql" in db_url:
            # Remove async driver prefix
            clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
            
            # Run pg_dump
            result = subprocess.run(
                ["pg_dump", "-Fc", "-f", output_path, clean_url],
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"pg_dump failed: {result.stderr}")
                return False
            
            logger.info(f"Database dump created: {output_path}")
            return True
        else:
            logger.error("Invalid database URL format")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("pg_dump timed out after 10 minutes")
        return False
    except Exception as e:
        logger.error(f"Failed to create database dump: {e}")
        return False


def encrypt_file(input_path: str, output_path: str, passphrase: str) -> bool:
    """Encrypt a file using GPG symmetric encryption."""
    try:
        # Use gpg for symmetric encryption
        result = subprocess.run(
            [
                "gpg", "--batch", "--yes",
                "--symmetric", "--cipher-algo", "AES256",
                "--passphrase", passphrase,
                "--output", output_path,
                input_path
            ],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            logger.error(f"GPG encryption failed: {result.stderr}")
            return False
        
        logger.info(f"File encrypted: {output_path}")
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("GPG encryption timed out")
        return False
    except FileNotFoundError:
        logger.error("GPG not found - please install gnupg")
        return False
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return False


def decrypt_file(input_path: str, output_path: str, passphrase: str) -> bool:
    """Decrypt a GPG-encrypted file."""
    try:
        result = subprocess.run(
            [
                "gpg", "--batch", "--yes",
                "--decrypt",
                "--passphrase", passphrase,
                "--output", output_path,
                input_path
            ],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            logger.error(f"GPG decryption failed: {result.stderr}")
            return False
        
        logger.info(f"File decrypted: {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return False


async def create_backup() -> Dict[str, Any]:
    """
    Create a new database backup.
    
    1. Creates pg_dump
    2. Encrypts with GPG
    3. Uploads to S3
    4. Cleans up temp files
    
    Returns dict with status and backup details.
    """
    config = await get_backup_config()
    if not config:
        return {"status": "error", "message": "Backup not configured - add S3 credentials in Admin Console → Secrets"}
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{BACKUP_PREFIX}{timestamp}{BACKUP_EXTENSION}"
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_path = os.path.join(tmpdir, "backup.sql")
            encrypted_path = os.path.join(tmpdir, backup_name)
            
            # Step 1: Create database dump
            logger.info("Creating database dump...")
            if not create_database_dump(dump_path):
                return {"status": "error", "message": "Failed to create database dump"}
            
            dump_size = os.path.getsize(dump_path)
            
            # Step 2: Encrypt the dump
            logger.info("Encrypting backup...")
            if not encrypt_file(dump_path, encrypted_path, config["BACKUP_ENCRYPTION_KEY"]):
                return {"status": "error", "message": "Failed to encrypt backup"}
            
            encrypted_size = os.path.getsize(encrypted_path)
            
            # Step 3: Upload to S3
            logger.info(f"Uploading to S3: {backup_name}...")
            s3 = get_s3_client(config)
            
            with open(encrypted_path, 'rb') as f:
                s3.upload_fileobj(
                    f,
                    config["BACKUP_S3_BUCKET"],
                    backup_name,
                    ExtraArgs={
                        'ContentType': 'application/octet-stream',
                        'Metadata': {
                            'unencrypted-size': str(dump_size),
                            'created-at': datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
            
            logger.info(f"Backup uploaded successfully: {backup_name}")
            
            return {
                "status": "success",
                "backup_name": backup_name,
                "size_bytes": encrypted_size,
                "unencrypted_size_bytes": dump_size,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "bucket": config["BACKUP_S3_BUCKET"]
            }
            
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return {"status": "error", "message": str(e)}


async def list_backups() -> Dict[str, Any]:
    """List all available backups from S3."""
    config = await get_backup_config()
    if not config:
        return {"status": "error", "message": "Backup not configured", "backups": []}
    
    try:
        s3 = get_s3_client(config)
        
        response = s3.list_objects_v2(
            Bucket=config["BACKUP_S3_BUCKET"],
            Prefix=BACKUP_PREFIX
        )
        
        backups = []
        for obj in response.get('Contents', []):
            if obj['Key'].endswith(BACKUP_EXTENSION):
                # Parse timestamp from filename
                name = obj['Key']
                try:
                    # Extract timestamp: db_backup_20260127_020000.sql.gpg
                    ts_str = name.replace(BACKUP_PREFIX, "").replace(BACKUP_EXTENSION, "")
                    created_at = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                except Exception:
                    created_at = obj.get('LastModified', datetime.now(timezone.utc))
                
                backups.append({
                    "name": name,
                    "size_bytes": obj['Size'],
                    "created_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
                    "age_days": (datetime.now(timezone.utc) - created_at).days if isinstance(created_at, datetime) else 0
                })
        
        # Sort by date, newest first
        backups.sort(key=lambda x: x['created_at'], reverse=True)
        
        return {
            "status": "success",
            "count": len(backups),
            "backups": backups
        }
        
    except Exception as e:
        logger.error(f"Failed to list backups: {e}")
        return {"status": "error", "message": str(e), "backups": []}


async def delete_backup(backup_name: str) -> Dict[str, Any]:
    """Delete a specific backup from S3."""
    config = await get_backup_config()
    if not config:
        return {"status": "error", "message": "Backup not configured"}
    
    try:
        s3 = get_s3_client(config)
        from app.core.sanitize import sanitize_path_component, sanitize_for_log
        safe_name = sanitize_path_component(backup_name)
        s3.delete_object(Bucket=config["BACKUP_S3_BUCKET"], Key=safe_name)
        
        logger.info(f"Deleted backup: {sanitize_for_log(safe_name)}")
        return {"status": "success", "deleted": safe_name}
        
    except Exception as e:
        logger.error(f"Failed to delete backup: {e}")
        return {"status": "error", "message": "Failed to delete backup"}


async def cleanup_old_backups(retention_days: int = None) -> Dict[str, Any]:
    """
    Delete backups older than the retention period.
    Uses the town's configured retention period if none is specified.

    With no period configured this deletes nothing rather than picking one. The
    old code fell back to an invented per-state schedule and then, if even that
    failed, to a bare seven years — two guesses at how long a town must be able
    to restore from, both on the destructive side of the decision. Keeping
    backups too long costs storage; deleting them early can cost the town the
    only copy of records it is required to produce.
    """
    config = await get_backup_config()
    if not config:
        return {"status": "error", "message": "Backup not configured"}

    # Get retention days from policy if not specified
    if retention_days is None:
        try:
            from app.db.session import SessionLocal
            from app.services.retention_config import load_retention_config

            async with SessionLocal() as db:
                retention = await load_retention_config(db)

            if not retention.configured:
                logger.info("[Backups] No retention schedule configured (%s) — "
                            "keeping every backup this run", retention.reason)
                return {
                    "status": "skipped",
                    "reason": "retention_unconfigured",
                    "deleted": [],
                    "deleted_count": 0,
                    "message": retention.detail,
                }

            retention_days = retention.retention_days
        except Exception as e:
            # Unreadable settings are not permission to start deleting either.
            logger.warning(f"Could not get retention policy, keeping every backup: {e}")
            return {
                "status": "skipped",
                "reason": "retention_unreadable",
                "deleted": [],
                "deleted_count": 0,
                "message": "Could not read the retention policy, so no backups were removed.",
            }

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    try:
        result = await list_backups()
        if result["status"] != "success":
            return result
        
        deleted = []
        for backup in result["backups"]:
            try:
                backup_date = datetime.fromisoformat(backup["created_at"].replace('Z', '+00:00').replace('+00:00', ''))
                if backup_date < cutoff:
                    delete_result = await delete_backup(backup["name"])
                    if delete_result["status"] == "success":
                        deleted.append(backup["name"])
            except Exception as e:
                logger.warning(f"Could not parse date for {backup['name']}: {e}")
        
        return {
            "status": "success",
            "retention_days": retention_days,
            "cutoff_date": cutoff.isoformat(),
            "deleted_count": len(deleted),
            "deleted": deleted
        }
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return {"status": "error", "message": str(e)}


async def get_backup_status() -> Dict[str, Any]:
    """Get backup system status including last backup and configuration."""
    config = await get_backup_config()
    
    if not config:
        return {
            "configured": False,
            "message": "Add backup credentials in Admin Console → Secrets",
            "required_secrets": [
                "BACKUP_S3_BUCKET",
                "BACKUP_S3_ACCESS_KEY", 
                "BACKUP_S3_SECRET_KEY",
                "BACKUP_ENCRYPTION_KEY"
            ],
            "optional_secrets": [
                "BACKUP_S3_ENDPOINT",
                "BACKUP_S3_REGION"
            ]
        }
    
    # Get list of backups to find most recent
    backups_result = await list_backups()
    
    last_backup = None
    if backups_result["status"] == "success" and backups_result["backups"]:
        last_backup = backups_result["backups"][0]  # Already sorted newest first
    
    return {
        "configured": True,
        "bucket": config["BACKUP_S3_BUCKET"],
        "endpoint": config.get("BACKUP_S3_ENDPOINT", "AWS S3"),
        "last_backup": last_backup,
        "total_backups": backups_result.get("count", 0),
        "next_scheduled": "Daily at 02:00 UTC"
    }


async def download_backup(backup_name: str, output_path: str) -> bool:
    """Download a backup file from S3."""
    config = await get_backup_config()
    if not config:
        return False
    
    try:
        s3 = get_s3_client(config)
        s3.download_file(
            config["BACKUP_S3_BUCKET"],
            backup_name,
            output_path
        )
        logger.info(f"Downloaded backup: {backup_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to download backup: {e}")
        return False


def restore_database_dump(dump_path: str) -> bool:
    """Restore a PostgreSQL dump file to the database."""
    try:
        db_url = os.environ.get("DATABASE_URL", "")
        
        if "postgresql" not in db_url:
            logger.error("Invalid database URL format")
            return False
        
        # Convert async driver URL to standard format
        clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        
        # Run pg_restore
        result = subprocess.run(
            [
                "pg_restore",
                "--clean",  # Drop existing objects before restore
                "--if-exists",  # Don't error if objects don't exist
                "-d", clean_url,
                dump_path
            ],
            capture_output=True,
            text=True,
            timeout=1800  # 30 minute timeout for large databases
        )
        
        # pg_restore may return non-zero even on successful restore due to warnings
        if result.returncode != 0 and "error" in result.stderr.lower():
            logger.error(f"pg_restore failed: {result.stderr}")
            return False
        
        logger.info("Database restored successfully")
        return True
        
    except subprocess.TimeoutExpired:
        logger.error("pg_restore timed out after 30 minutes")
        return False
    except Exception as e:
        logger.error(f"Failed to restore database: {e}")
        return False


async def restore_backup(backup_name: str) -> Dict[str, Any]:
    """
    Restore database from an encrypted backup.
    
    1. Downloads backup from S3
    2. Decrypts with GPG
    3. Restores to database with pg_restore
    4. Cleans up temp files
    
    WARNING: This will overwrite the current database!
    
    Returns dict with status and details.
    """
    config = await get_backup_config()
    if not config:
        return {"status": "error", "message": "Backup not configured"}
    
    # Verify backup exists
    backups = await list_backups()
    if backups["status"] != "success":
        return {"status": "error", "message": "Could not list backups"}
    
    backup_exists = any(b["name"] == backup_name for b in backups.get("backups", []))
    if not backup_exists:
        return {"status": "error", "message": f"Backup not found: {backup_name}"}
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: Download from S3
            from app.core.sanitize import sanitize_path_component, sanitize_for_log
            safe_backup_name = sanitize_path_component(backup_name)
            encrypted_path = os.path.join(tmpdir, safe_backup_name)
            decrypted_path = os.path.join(tmpdir, "backup.sql")
            
            logger.info(f"Downloading backup: {sanitize_for_log(safe_backup_name)}...")
            if not await download_backup(safe_backup_name, encrypted_path):
                return {"status": "error", "message": "Failed to download backup from S3"}
            
            encrypted_size = os.path.getsize(encrypted_path)
            
            # Step 2: Decrypt
            logger.info("Decrypting backup...")
            if not decrypt_file(encrypted_path, decrypted_path, config["BACKUP_ENCRYPTION_KEY"]):
                return {"status": "error", "message": "Failed to decrypt backup - check encryption key"}
            
            decrypted_size = os.path.getsize(decrypted_path)
            
            # Step 3: Restore to database
            logger.info("Restoring database...")
            if not restore_database_dump(decrypted_path):
                return {"status": "error", "message": "Failed to restore database - check pg_restore logs"}
            
            logger.info(f"Database restored from backup: {sanitize_for_log(safe_backup_name)}")
            
            return {
                "status": "success",
                "backup_name": backup_name,
                "encrypted_size_bytes": encrypted_size,
                "decrypted_size_bytes": decrypted_size,
                "restored_at": datetime.now(timezone.utc).isoformat(),
                "warning": "Database has been restored. You may need to restart the application."
            }
            
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return {"status": "error", "message": str(e)}

