"""The public config endpoint carries the operator's registration-form URL.

CONTACT_FORM_URL is a deployment-level setting: when the operator hosts a
registration form (a Microsoft Form, in practice), the admin console links out
to it instead of showing the built-in contact form. The frontend branches on
`contact_form_url` from GET /system/config, so what matters here is the
contract: the key is always present, it is an empty string when nothing is
configured (the UI's fallback signal), and it reflects the environment,
trimmed, when something is.
"""

import pytest

pytest.importorskip("fastapi")  # system.py pulls in the whole API stack

from app.api.system import get_deployment_config
from app.core.config import get_settings


class _NoDatabase:
    """Whatever `public_origin` asks of it raises, which that helper treats as
    'no domain configured'. The contact-form URL must not depend on the
    database either way."""

    def __getattr__(self, name):
        raise RuntimeError("no database in this test")


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    """Settings are lru_cached; each test gets a cache that reflects its env."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_unconfigured_is_an_empty_string(monkeypatch):
    """Empty, not absent and not None: the frontend reads falsy-string as
    'use the in-app form', and a missing key would read as a broken fetch."""
    monkeypatch.delenv("CONTACT_FORM_URL", raising=False)
    config = await get_deployment_config(db=_NoDatabase())
    assert config["contact_form_url"] == ""


async def test_the_environment_value_comes_through(monkeypatch):
    monkeypatch.setenv("CONTACT_FORM_URL", "https://forms.office.com/r/example")
    config = await get_deployment_config(db=_NoDatabase())
    assert config["contact_form_url"] == "https://forms.office.com/r/example"


async def test_whitespace_is_not_a_configuration(monkeypatch):
    """A stray space in .env must not make the UI offer a blank link."""
    monkeypatch.setenv("CONTACT_FORM_URL", "   ")
    config = await get_deployment_config(db=_NoDatabase())
    assert config["contact_form_url"] == ""


async def test_embedding_is_the_default(monkeypatch):
    """Answering without leaving the console is the point of the setting, so an
    operator who configures a form and nothing else gets the embedded form."""
    monkeypatch.delenv("CONTACT_FORM_EMBED", raising=False)
    config = await get_deployment_config(db=_NoDatabase())
    assert config["contact_form_embed"] is True


async def test_embedding_can_be_switched_off(monkeypatch):
    """What a form restricted to the operator's own organisation needs: framed,
    those serve a sign-in page rather than the questions."""
    monkeypatch.setenv("CONTACT_FORM_EMBED", "false")
    config = await get_deployment_config(db=_NoDatabase())
    assert config["contact_form_embed"] is False
