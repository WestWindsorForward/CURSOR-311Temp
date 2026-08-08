from celery import Celery
from celery.schedules import crontab
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "township_311",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.service_requests", "app.tasks.integrations", "app.tasks.road_data",
             "app.tasks.storage", "app.tasks.connector_checks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes
    worker_prefetch_multiplier=1,
    # Celery Beat Schedule
    #
    # Hourly-and-slower entries are crontab (wall-clock), not intervals. An
    # interval counts from worker boot and resets on every restart, so during
    # any stretch of active deployment the daily jobs simply never fired --
    # connector checks, backups, retention, all of them -- and several of the
    # comments below promised wall-clock times ("2:00 AM UTC") the interval
    # never honoured. A crontab fires at the stated time whatever the uptime.
    # The 15-minute pollers stay intervals: they fire within minutes of boot
    # regardless, and a fixed-minute stampede buys nothing.
    beat_schedule={
        # Proactive health scan: warn admins by email before something fails
        # (disk/memory/connections/backup staleness), every 15 minutes.
        "proactive-health-scan": {
            "task": "app.tasks.service_requests.proactive_health_scan",
            "schedule": 60 * 15,  # Every 15 minutes
            "options": {"queue": "default"}
        },
        # Road centreline refresh. Fires daily but acts on one day a month --
        # the day is derived from a hash of the township name so deployments
        # spread across the month instead of all hitting a publisher on the 1st.
        # Publishers republish about monthly (NJ's statewide NG911 layer does),
        # and stale road data fails open, so lag costs a new street going
        # unblocked briefly rather than a resident being turned away.
        "monthly-road-refresh": {
            "task": "app.tasks.road_data.refresh_roads_monthly",
            "schedule": crontab(hour=4, minute=15),  # checked daily, acts monthly
            "options": {"queue": "default"}
        },
        # Test every configured connector once a day.
        #
        # "The credentials are stored" is a fact about our database and stays
        # true forever; "the credentials work" is a fact about somebody else's
        # service and stops being true without warning. Without this the only
        # way to learn a key was revoked is an admin pressing Test, or a
        # resident who never got their email.
        # Infrastructure, hourly. A disk fills in hours; a connector's
        # credentials do not expire faster than once a day.
        "hourly-system-probe": {
            "task": "app.tasks.connector_checks.probe_system",
            "schedule": crontab(minute=7),
            "options": {"queue": "default"}
        },
        "daily-connector-check": {
            "task": "app.tasks.connector_checks.verify_connectors",
            "schedule": crontab(hour=9, minute=0),
            "options": {"queue": "default"}
        },
        # Daily anchor of the audit hash-chain head (tamper-evidence beyond the DB)
        "daily-audit-anchor": {
            "task": "app.tasks.service_requests.anchor_audit_chain",
            "schedule": crontab(hour=0, minute=30),
            "options": {"queue": "default"}
        },
        # Daily retention enforcement at 1:00 AM UTC (before backup)
        "daily-retention-enforcement": {
            "task": "app.tasks.service_requests.enforce_retention_policy",
            "schedule": crontab(hour=1, minute=0),
            "options": {"queue": "default"}
        },
        # Daily purge of IP addresses older than 90 days (privacy commitment)
        "daily-ip-purge": {
            "task": "app.tasks.service_requests.purge_old_ip_addresses",
            "schedule": crontab(hour=1, minute=30),
            "options": {"queue": "default"}
        },
        # Daily database backup at 2:00 AM UTC
        "daily-database-backup": {
            "task": "app.tasks.service_requests.backup_database",
            "schedule": crontab(hour=2, minute=0),
            "options": {"queue": "default"}
        },
        # Weekly backup cleanup on Sundays at 3:00 AM UTC
        "weekly-backup-cleanup": {
            "task": "app.tasks.service_requests.cleanup_expired_backups",
            "schedule": crontab(hour=3, minute=0, day_of_week="sunday"),
            "options": {"queue": "default"}
        },
        # Poll connected govtech platforms for external status changes
        "pull-integration-updates": {
            "task": "app.tasks.integrations.pull_integration_updates",
            "schedule": 60 * 15,  # Every 15 minutes
            "options": {"queue": "default"}
        },
        # Import new external comments on linked, active requests
        "pull-integration-comments": {
            "task": "app.tasks.integrations.pull_integration_comments",
            "schedule": 60 * 15,  # Every 15 minutes
            "options": {"queue": "default"}
        },
        # Mirror external asset inventories into Pinpoint map layers
        "sync-integration-assets": {
            "task": "app.tasks.integrations.sync_integration_assets",
            "schedule": crontab(hour=5, minute=0),
            "options": {"queue": "default"}
        },
        # Weekly staff digest emails on Mondays at 8:00 AM UTC
        "weekly-staff-digest": {
            "task": "app.tasks.service_requests.send_weekly_digest",
            "schedule": crontab(hour=8, minute=0, day_of_week="monday"),
            "options": {"queue": "default"}
        },
        # Storage hygiene that used to be two buttons on the setup page. Both
        # verify before they change anything and are no-ops with nothing to do,
        # so nobody has to work out whether they apply.
        "hourly-secret-vaulting": {
            "task": "app.tasks.storage.vault_secrets",
            "schedule": crontab(minute=37),
            "options": {"queue": "default"}
        },
        "nightly-pii-rewrap": {
            "task": "app.tasks.storage.rewrap_pii",
            "schedule": crontab(hour=6, minute=0),
            "options": {"queue": "default"}
        },
        # Refresh the live AI model lists so the picker stays current and can
        # flag a retired/deprecated model without anyone opening the admin UI.
        "daily-ai-model-refresh": {
            "task": "app.tasks.service_requests.refresh_ai_models",
            "schedule": crontab(hour=10, minute=0),
            "options": {"queue": "default"}
        },
    }
)
