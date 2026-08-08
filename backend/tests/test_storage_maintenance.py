"""Storage hygiene that happens without anyone pressing a button.

Two maintenance jobs used to be buttons on the setup page -- "Vault Local
Secrets to GCP Identity" and "Re-encrypt All PII Data (after key rotation)".
Neither is something a clerk can be expected to know applies to them, so both
now run on a schedule. That turns three things into behaviour worth pinning:

  * the schedule exists, because an automation nobody registered is just a
    feature that was deleted;
  * an undecryptable value is never overwritten, because that is the single
    path by which this code could destroy the last copy of somebody's data;
  * the migration is no longer gated on Google specifically, which is what left
    towns on Azure and AWS with database copies of every credential forever.
"""

import base64

import pytest

from app.services import storage_maintenance as sm


# ---------------------------------------------------------------------------
# The one destructive path
# ---------------------------------------------------------------------------

def test_a_value_that_will_not_decrypt_is_left_alone(monkeypatch):
    """The key that wrapped this row is gone. Re-encrypting the empty string
    over it would replace a resident's phone number with nothing, permanently,
    and it would look like a successful run."""
    from app.core import encryption

    monkeypatch.setattr(encryption, "decrypt_pii", lambda v: "")
    monkeypatch.setattr(encryption, "encrypt_pii", lambda v: "SHOULD NOT BE WRITTEN")

    with pytest.raises(ValueError):
        sm.rewrap_value("pii2:whatever:nonce:ct")


def test_an_unchanged_value_is_not_rewritten(monkeypatch):
    """Already on the current key. Returning None keeps the row out of the
    counts, so a second pass over a converted database reports zero rather than
    reporting the same work again every night."""
    from app.core import encryption

    monkeypatch.setattr(encryption, "decrypt_pii", lambda v: "resident@example.gov")
    monkeypatch.setattr(encryption, "encrypt_pii", lambda v: "same")

    assert sm.rewrap_value("same") is None


def test_empty_columns_are_skipped(monkeypatch):
    """Most requests have no phone number; that is not work and not an error."""
    def _boom(_):
        raise AssertionError("should not have tried to decrypt an empty column")

    from app.core import encryption
    monkeypatch.setattr(encryption, "decrypt_pii", _boom)

    assert sm.rewrap_value(None) is None
    assert sm.rewrap_value("") is None


def test_a_stale_value_is_returned_re_encrypted(monkeypatch):
    from app.core import encryption

    monkeypatch.setattr(encryption, "decrypt_pii", lambda v: "resident@example.gov")
    monkeypatch.setattr(encryption, "encrypt_pii", lambda v: "pii2:new:n:c")

    assert sm.rewrap_value("pii2:old:n:c") == "pii2:new:n:c"


# ---------------------------------------------------------------------------
# The generated backup passphrase
# ---------------------------------------------------------------------------

def test_the_backup_passphrase_is_a_real_key():
    """It replaces a free-text box that asked a town to invent one. 256 bits,
    because the thing it protects is a full database dump."""
    key = sm.generated_backup_key()
    assert len(base64.urlsafe_b64decode(key + "==")) == 32


def test_backup_passphrases_are_not_repeated():
    assert len({sm.generated_backup_key() for _ in range(50)}) == 50


def test_the_backup_passphrase_survives_a_round_trip_through_a_url_and_a_shell():
    """It gets copied into password managers, .env files and, inevitably, a
    command line. urlsafe base64 with the padding stripped has no character
    that any of those treat specially."""
    key = sm.generated_backup_key()
    assert key.isascii()
    assert not (set(key) - set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    ))


# ---------------------------------------------------------------------------
# The schedule, and what it replaced
# ---------------------------------------------------------------------------

def test_both_jobs_are_actually_scheduled():
    pytest.importorskip("celery")
    from app.core.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    tasks = {entry["task"] for entry in schedule.values()}
    assert "app.tasks.storage.vault_secrets" in tasks
    assert "app.tasks.storage.rewrap_pii" in tasks


def test_the_task_module_is_imported_by_the_worker():
    """A beat entry naming a task nobody imported fails at run time, in a log
    the town will not read, having quietly done nothing for months."""
    pytest.importorskip("celery")
    from app.core.celery_app import celery_app

    assert "app.tasks.storage" in celery_app.conf.include


def test_vaulting_runs_often_enough_to_matter():
    """The window it closes opens the moment somebody finishes setup: anything
    entered before the cloud account was connected sits in the database until
    this fires. Daily would be a long time for that to be true of a live
    Twilio token."""
    pytest.importorskip("celery")
    from app.core.celery_app import celery_app

    from celery.schedules import crontab
    entry = celery_app.conf.beat_schedule["hourly-secret-vaulting"]
    # Once an hour at a fixed minute. crontab(minute=N) fires hourly by
    # construction and survives worker restarts, which the old <= 1h interval
    # did not.
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == set(range(24))


# ---------------------------------------------------------------------------
# Store-agnostic migration
# ---------------------------------------------------------------------------

def test_the_migration_is_not_gated_on_google():
    """It checked `_is_gcp_available()`, so a town on Azure Key Vault or AWS
    Secrets Manager could never move its database copies across -- the save
    path wrote to the vault, and the database copy stayed behind forever with
    nothing offering to remove it."""
    pytest.importorskip("sqlalchemy")
    import inspect

    from app.services import secret_manager

    src = inspect.getsource(secret_manager.migrate_to_secret_manager)
    assert "store_reachable()" in src
    assert "_is_gcp_available()" not in src


def test_store_reachable_answers_for_each_backend(monkeypatch):
    """One helper, three stores. It is the gate on a function that deletes
    database rows, so a backend it does not know about must read as
    unreachable rather than as fine."""
    pytest.importorskip("sqlalchemy")
    from app.services import secret_manager

    monkeypatch.setattr(secret_manager, "_secrets_provider", lambda: "google")
    monkeypatch.setattr(secret_manager, "_is_gcp_available", lambda: True)
    assert sm.store_reachable() is True

    monkeypatch.setattr(secret_manager, "_is_gcp_available", lambda: False)
    assert sm.store_reachable() is False

    monkeypatch.setattr(secret_manager, "_secrets_provider", lambda: "azure")
    from app.core import azure_keyvault
    monkeypatch.setattr(azure_keyvault, "is_configured", lambda: True)
    assert sm.store_reachable() is True

    monkeypatch.setattr(secret_manager, "_secrets_provider", lambda: "aws")
    from app.core import aws_secretsmanager
    monkeypatch.setattr(aws_secretsmanager, "is_configured", lambda: False)
    assert sm.store_reachable() is False


def test_vaulting_stops_before_it_touches_anything_when_no_store_is_configured(monkeypatch):
    """Every town without a cloud account runs this hourly forever. It has to
    be a no-op, not an attempt that fails somewhere inside."""
    pytest.importorskip("sqlalchemy")
    import asyncio

    monkeypatch.setattr(sm, "store_reachable", lambda: False)

    def _boom():
        raise AssertionError("migration must not be attempted with no store")

    from app.services import secret_manager
    monkeypatch.setattr(secret_manager, "migrate_to_secret_manager", _boom)

    result = asyncio.run(sm.vault_secrets())
    assert result["status"] == "skipped"


def test_the_scheduled_pass_never_raises(monkeypatch):
    """It runs unattended. An exception escaping here is a Celery traceback
    nobody sees, on an hourly cadence."""
    pytest.importorskip("sqlalchemy")
    import asyncio

    def _explode():
        raise RuntimeError("network down")

    monkeypatch.setattr(sm, "store_reachable", _explode)
    assert asyncio.run(sm.vault_secrets())["status"] in ("error", "skipped")


# ---------------------------------------------------------------------------
# No lock-in: the health dashboard must not be Google-shaped
# ---------------------------------------------------------------------------

class _NoDatabase:
    """get_config_value falls back to a database query and swallows failures,
    so this stands in for a session without needing one."""


def _kms_check(monkeypatch, *, wrapped_by: str, selected: str):
    import asyncio

    from app.api import health
    from app.core import encryption, pii_crypto

    monkeypatch.setattr(encryption, "encrypt_pii", lambda v: "pii2:w:n:c")
    monkeypatch.setattr(encryption, "decrypt_pii", lambda v: "health_check_test@example.com")
    monkeypatch.setattr(pii_crypto, "active_backend", lambda: wrapped_by)
    monkeypatch.setattr(encryption, "_kms_provider", lambda: selected)

    return asyncio.run(health.check_kms(_NoDatabase()))


@pytest.mark.parametrize("provider,label", [
    ("google", "Google Cloud KMS"),
    ("azure", "Azure Key Vault"),
    ("aws", "AWS KMS"),
])
def test_every_key_manager_reports_healthy(monkeypatch, provider, label):
    """The check used to open by looking for GOOGLE_CLOUD_PROJECT and return
    "GCP project not configured" if it was absent -- so a town running Azure
    Key Vault or AWS KMS, with envelope encryption working perfectly, saw a
    failure on its health dashboard. A false alarm on a supported setup is
    worse than no check."""
    pytest.importorskip("fastapi")
    result = _kms_check(monkeypatch, wrapped_by=provider, selected=provider)
    assert result["status"] == "healthy"
    assert result["kms_backend"] == provider
    assert label in result["message"]


def test_a_selected_but_unreachable_key_manager_is_reported(monkeypatch):
    """The failure mode this check exists for. `_wrap_dek` falls back to the
    application key when the chosen KMS is unreachable -- no exception, nothing
    logged above DEBUG. Reporting plain "healthy" here would hide the one thing
    an admin needs to know."""
    pytest.importorskip("fastapi")
    result = _kms_check(monkeypatch, wrapped_by="local", selected="azure")
    assert result["status"] == "fallback"
    assert "azure" in result["message"]
    assert "not reachable" in result["message"]


def test_no_cloud_kms_is_not_reported_as_a_problem(monkeypatch):
    """A self-hosted install with no cloud account at all. Supported, and the
    normal case for a small town."""
    pytest.importorskip("fastapi")
    result = _kms_check(monkeypatch, wrapped_by="local", selected="local")
    assert result["status"] == "fallback"
    assert "not reachable" not in result["message"]


@pytest.mark.parametrize("provider,label", [
    ("google", "Google Secret Manager"),
    ("azure", "Azure Key Vault"),
    ("aws", "AWS Secrets Manager"),
])
def test_the_secret_store_check_names_the_store_in_use(monkeypatch, provider, label):
    pytest.importorskip("fastapi")
    import asyncio

    from app.api import health
    from app.services import secret_manager

    monkeypatch.setattr(secret_manager, "_secrets_provider", lambda: provider)
    monkeypatch.setattr(sm, "store_reachable", lambda: True)

    result = asyncio.run(health.check_secret_manager(_NoDatabase()))
    assert result["status"] == "healthy"
    assert result["store"] == provider
    assert label in result["message"]


def test_the_health_response_does_not_key_these_checks_by_vendor():
    """They were `google_kms` and `google_secret_manager`, and the dashboard
    rendered them under Google headings whatever the town was running."""
    pytest.importorskip("fastapi")
    import inspect

    from app.api import health

    src = inspect.getsource(health.health_check)
    assert '"kms":' in src and '"secret_store":' in src
    assert '"google_kms"' not in src
