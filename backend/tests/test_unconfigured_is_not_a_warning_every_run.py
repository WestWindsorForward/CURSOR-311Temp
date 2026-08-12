"""Not configured is a state, not an event.

Two lines were being logged at WARNING on every scheduled run:

    [SMS Config] Unknown or empty SMS_PROVIDER - SMS will not work
    Backup configuration incomplete - missing required secrets

`configure_notifications` and `get_backup_config` are both called from the
fifteen-minute proactive health scan, so a deployment that has simply chosen not
to send texts, or not to ship backups off-site yet, wrote each of these about a
hundred times a day. A warning that is always present is not a warning, it is
background, and it is what makes people stop reading the log -- so the one time
something does go wrong, nobody sees it.

The fix is a per-process latch: warn the first time, DEBUG afterwards. What is
deliberately *not* changed:

  - `NotificationService.send_sms` still warns every single time something
    actually tries to text with no provider. That is a real event with a real
    victim -- a resident who was not told their pothole was fixed.
  - The backup state still reaches the admin as a warning on the health card
    (`proactive_health._backup_age_check`), which is where a standing condition
    belongs. Demoting the log line does not hide it, it moves it to the surface
    built for it.
"""

import logging

import pytest

# Submodules, per tests/test_migrate.py: `app` resolves as a namespace package,
# so importing the bare name can succeed while nothing underneath is importable.
pytest.importorskip("sqlalchemy.orm")
pytest.importorskip("boto3.session")


def _lines(caplog, needle, level=None):
    return [
        r for r in caplog.records
        if needle in r.getMessage() and (level is None or r.levelno == level)
    ]


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #

NEEDLE_BACKUP = "Backup configuration incomplete"


@pytest.fixture
def backup_service(monkeypatch):
    import app.services.backup_service as bs

    monkeypatch.setattr(bs, "_CONFIG_INCOMPLETE_LOGGED", False, raising=False)
    return bs


def _stub_secrets(monkeypatch, values):
    import app.services.secret_manager as sm

    async def fake_get_secret(key, *a, **kw):
        return values.get(key, "")

    monkeypatch.setattr(sm, "get_secret", fake_get_secret)


COMPLETE = {
    "BACKUP_S3_BUCKET": "b",
    "BACKUP_S3_ACCESS_KEY": "k",
    "BACKUP_S3_SECRET_KEY": "s",
    "BACKUP_ENCRYPTION_KEY": "e",
}


@pytest.mark.asyncio
async def test_missing_backup_secrets_warn_once_then_drop_to_debug(
    backup_service, monkeypatch, caplog
):
    _stub_secrets(monkeypatch, {})
    with caplog.at_level(logging.DEBUG, logger=backup_service.__name__):
        assert await backup_service.get_backup_config() is None
        assert await backup_service.get_backup_config() is None
        assert await backup_service.get_backup_config() is None

    assert len(_lines(caplog, NEEDLE_BACKUP, logging.WARNING)) == 1
    # Still recoverable from a debug log; just not shouted every fifteen minutes.
    assert len(_lines(caplog, NEEDLE_BACKUP, logging.DEBUG)) == 2


@pytest.mark.asyncio
async def test_a_complete_backup_config_says_nothing_at_all(
    backup_service, monkeypatch, caplog
):
    _stub_secrets(monkeypatch, COMPLETE)
    with caplog.at_level(logging.DEBUG, logger=backup_service.__name__):
        assert await backup_service.get_backup_config() is not None
    assert _lines(caplog, NEEDLE_BACKUP) == []


@pytest.mark.asyncio
async def test_losing_a_backup_secret_later_warns_again(
    backup_service, monkeypatch, caplog
):
    """The latch is per-state, not per-lifetime. Backups that were configured
    and then broke are news, and a latch that never reset would swallow exactly
    the transition worth hearing about."""
    _stub_secrets(monkeypatch, {})
    with caplog.at_level(logging.DEBUG, logger=backup_service.__name__):
        await backup_service.get_backup_config()          # warns
        _stub_secrets(monkeypatch, COMPLETE)
        await backup_service.get_backup_config()          # configured; resets
        _stub_secrets(monkeypatch, {})
        await backup_service.get_backup_config()          # warns again

    assert len(_lines(caplog, NEEDLE_BACKUP, logging.WARNING)) == 2


@pytest.mark.asyncio
async def test_the_missing_backup_state_still_reaches_the_admin(monkeypatch):
    """The log line was demoted, so the health card has to be carrying this --
    otherwise quieting the log really did hide something."""
    pytest.importorskip("app.services.proactive_health")
    from app.services import proactive_health as ph

    async def no_status():
        return {"configured": False, "message": "Add backup credentials"}

    monkeypatch.setattr(
        "app.services.backup_service.get_backup_status", no_status, raising=False
    )
    check = await ph._backup_age_check()
    assert check["key"] == "backup"
    assert check["status"] in ("warning", "critical")


# --------------------------------------------------------------------------- #
# SMS
# --------------------------------------------------------------------------- #

NEEDLE_SMS = "Unknown or empty SMS_PROVIDER"


@pytest.mark.asyncio
async def test_unconfigured_sms_warns_once_then_drops_to_debug(monkeypatch, caplog):
    pytest.importorskip("celery.app")
    import app.tasks.service_requests as sr

    monkeypatch.setattr(sr, "_SMS_UNCONFIGURED_LOGGED", False, raising=False)

    async def no_secret(db, key):
        return ""

    monkeypatch.setattr(sr, "get_secret", no_secret)

    async def enabled(_name):
        return True

    monkeypatch.setattr("app.services.capability_switches.enabled", enabled)

    with caplog.at_level(logging.DEBUG, logger=sr.__name__):
        for _ in range(4):
            try:
                await sr.configure_notifications(None)
            except Exception:
                # Only the SMS half of this function is under test; whatever the
                # email half needs is not stubbed and may well raise.
                pass

    assert len(_lines(caplog, NEEDLE_SMS, logging.WARNING)) == 1
    assert len(_lines(caplog, NEEDLE_SMS, logging.DEBUG)) == 3


@pytest.mark.asyncio
async def test_an_actual_send_with_no_provider_still_warns_every_time(monkeypatch, caplog):
    """The honest warning is the one at the point of use. Somebody tried to text
    a resident and could not -- that is an event, it has a victim, and it must
    not be latched along with the configuration notice."""
    notifications = pytest.importorskip("app.services.notifications")

    svc = notifications.NotificationService()
    monkeypatch.setattr(svc, "_sms_provider", None, raising=False)

    with caplog.at_level(logging.DEBUG, logger=notifications.__name__):
        assert await svc.send_sms("+15550000000", "hello") is False
        assert await svc.send_sms("+15550000001", "hello again") is False

    assert len(_lines(caplog, "SMS provider not configured", logging.WARNING)) == 2


def test_the_sms_latch_resets_when_a_provider_is_configured():
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "tasks" / "service_requests.py"
    tree = ast.parse(src.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "configure_notifications"
    )
    body = ast.unparse(fn)
    assert "_SMS_UNCONFIGURED_LOGGED = False" in body, \
        "the latch never resets, so re-breaking SMS would be logged at debug forever"
