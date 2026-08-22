"""A setting the console changes has to change what the process does.

Every bug in this audit had the same shape: the page reports one thing and the
running system does another. Caching is the last place that shape hides, and it
hides well, because the answer is right for the first fifteen minutes and right
again after the next deploy.

Three kinds of holding, and they fail differently:

  * the secret cache -- bounded, fifteen minutes, and cleared on write in the
    process that wrote. It is fine, and it is deliberately not shared through
    Redis: that would put decrypted secrets in a second datastore to fix a
    latency problem that does not exist.
  * process-lifetime globals -- the KMS client and the resolved key path, the
    OIDC discovery document. Resolved once and held until restart, so a changed
    setting never lands at all.
  * a singleton that keeps what it was last given -- NotificationService. The
    dangerous direction is switching something OFF, because the configure call
    is what gets skipped, and skipping it leaves the previous sender in place
    and sending.
"""

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Process-lifetime globals
# ---------------------------------------------------------------------------

def test_the_kms_key_path_can_be_forgotten():
    """`_get_kms_key_name` resolves once and caches for the life of the process.
    Change the key ring, the location or the key name on the PII Encryption card
    and the process carries on wrapping resident data against the old path."""
    from app.core import encryption

    encryption._kms_key_name = "projects/p/locations/l/keyRings/old/cryptoKeys/k"
    encryption._kms_client = object()
    encryption.reset_kms_cache()
    assert encryption._kms_key_name is None
    assert encryption._kms_client is None


def test_rotating_the_key_drops_the_path_as_well_as_the_key():
    """`clear_caches()` documents itself as the thing to call after rotating the
    KMS key, and it dropped the data key while keeping the path that data key is
    wrapped against -- so the re-wrap went straight back to the key the town had
    just moved off. The one case it existed for was the one it could not
    complete."""
    from app.core import encryption, pii_crypto

    encryption._kms_key_name = "projects/p/locations/l/keyRings/old/cryptoKeys/k"
    pii_crypto.clear_caches()
    assert encryption._kms_key_name is None


def test_saving_a_kms_setting_clears_the_path(monkeypatch):
    pytest.importorskip("fastapi.routing")
    from app.api import system
    from app.core import encryption

    for key in ("KMS_KEY_ID", "KMS_KEY_RING", "KMS_LOCATION", "GOOGLE_CLOUD_PROJECT"):
        encryption._kms_key_name = "stale"
        system._invalidate_process_caches(key)
        assert encryption._kms_key_name is None, key


def test_saving_an_identity_setting_clears_the_discovery_document():
    """The OIDC discovery document is cached per issuer with no expiry. A town
    correcting a mistyped issuer, or a provider moving its endpoints, kept being
    sent to the old ones for the life of the process."""
    pytest.importorskip("fastapi.routing")
    from app.api import system
    from app.services import identity

    identity._discovery_cache["https://old.example"] = {"authorization_endpoint": "x"}
    system._invalidate_process_caches("AUTH0_DOMAIN")
    assert identity._discovery_cache == {}


def test_saving_something_unrelated_clears_nothing(monkeypatch):
    """Clearing everything on every save would refetch each capability's
    credentials on a page that saves several times."""
    pytest.importorskip("fastapi.routing")
    from app.api import system
    from app.core import encryption

    encryption._kms_key_name = "kept"
    system._invalidate_process_caches("SMTP_HOST")
    assert encryption._kms_key_name == "kept"


def test_clearing_a_cache_never_fails_the_save(monkeypatch):
    """This runs inside the write. Failing to drop a cache must not lose the
    credential that was being saved."""
    pytest.importorskip("fastapi.routing")
    from app.api import system
    from app.core import encryption

    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(encryption, "reset_kms_cache", boom)
    system._invalidate_process_caches("KMS_KEY_ID")  # must not raise


# ---------------------------------------------------------------------------
# The sender singleton
# ---------------------------------------------------------------------------
#
# `notification_service` outlives `configure_notifications`, and the off paths
# were the ones that skipped the configure call -- so turning something off left
# the thing that was on still on.

def _config_source():
    import inspect

    # Reading the source of a Celery task module still executes its imports,
    # and app.tasks.service_requests builds the Celery app at import time.
    pytest.importorskip("celery.app")

    from app.tasks import service_requests

    return inspect.getsource(service_requests.configure_notifications)


def test_switching_email_off_clears_the_sender():
    """EMAIL_ENABLED != true skipped the configure call entirely, leaving the
    sender built by the previous one in place. A town that switched resident
    email off carried on emailing residents until the worker restarted."""
    src = _config_source()
    assert "_email_provider = None" in src


def test_an_unknown_sms_provider_clears_the_sender():
    """Switching from Twilio to a blank or mistyped provider left Twilio doing
    the work, and the log line said the opposite."""
    src = _config_source()
    assert "_sms_provider = None" in src


def test_the_provider_name_is_cleared_with_the_sender():
    """Otherwise the next health row is attributed to a sender that is no longer
    there."""
    src = _config_source()
    assert "_sms_provider_name = None" in src
    assert "_email_provider_name = None" in src


def test_the_configuration_is_re_read_rather_than_remembered():
    """The senders are rebuilt from the secret store on every call, so a changed
    credential lands as soon as the secret cache expires. It is only the *off*
    paths that needed the explicit clear."""
    src = _config_source()
    assert src.count("await get_secret(db,") > 10


# ---------------------------------------------------------------------------
# Everything else is read per call
# ---------------------------------------------------------------------------

def test_the_capabilities_that_build_from_secrets_hold_nothing():
    """AI, translation and photo redaction resolve their provider and their
    credentials on every call. A changed setting reaches them as soon as the
    secret cache expires, with nothing to invalidate."""
    import inspect

    from app.services.ai.registry import get_ai_provider
    from app.services.image_redaction import resolve_provider

    for fn in (get_ai_provider, resolve_provider):
        assert "get_secret" in inspect.getsource(fn), fn.__name__


def test_no_new_process_lifetime_config_cache_has_appeared():
    """A regression guard on the shape rather than on the instances. A module
    global holding resolved configuration is the thing that makes a saved
    setting invisible, so a new one has to be a deliberate decision with an
    invalidation path -- which means adding it here."""
    import pathlib
    import re

    known = {
        # The Secret Manager client itself. Its credentials are the bootstrap
        # pair, which cannot be changed from a provider card.
        ("app/services/secret_manager.py", "_sm_client"),
        ("app/services/secret_manager.py", "_config"),
        # Both reset by reset_kms_cache().
        ("app/core/encryption.py", "_kms_client"),
        ("app/core/encryption.py", "_kms_key_name"),
    }
    found = set()
    for path in ("app/services/secret_manager.py", "app/core/encryption.py",
                 "app/core/pii_crypto.py", "app/services/identity.py",
                 "app/services/translation.py", "app/services/notifications.py"):
        src = pathlib.Path(path).read_text()
        for name in re.findall(r"^(_[a-z_]*(?:client|config|creds)[a-z_]*) *[:=]", src, re.M):
            found.add((path, name))
    assert found <= known, f"new process-lifetime config cache with no invalidation: {found - known}"
