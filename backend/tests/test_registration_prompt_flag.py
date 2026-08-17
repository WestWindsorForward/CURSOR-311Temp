"""The operator answering the registration prompt for the whole deployment.

The "Register your deployment" prompt is dismissed per browser, deliberately:
it is a nudge, not a task, and there was nothing on the server to key it to.
`system_settings.registration_prompt_dismissed` is the one thing now worth
keying to it -- not a user putting the nudge aside, but the operator saying the
question is settled for this instance.

Two properties matter more than the rest of this file:

  * OFF by default, everywhere. A town that has never heard of this setting
    keeps exactly the prompt it had, because that prompt is the only channel
    this product has for telling a town about a security fix. Every ambiguity
    here -- no settings row, an unreadable one, a save that never mentions the
    field -- must resolve to "not dismissed".

  * The flag rides on the config payload the console already reads, so the
    decision costs no extra request and cannot arrive after the prompt has
    already opened.
"""

import asyncio
from pathlib import Path

import pytest

# system.py pulls in the whole API stack; the submodule import is what actually
# proves it is installed (see the header note in tests/test_migrate.py).
pytest.importorskip("fastapi.routing")
pytest.importorskip("sqlalchemy")
pytest.importorskip("geoalchemy2.types")  # via app.models -> Geometry columns


# ---------------------------------------------------------------------------
# the column
# ---------------------------------------------------------------------------

def test_the_column_is_off_by_default_and_never_null():
    from app.models import SystemSettings

    col = SystemSettings.__table__.c.registration_prompt_dismissed
    assert col.default.arg is False
    assert col.nullable is False
    assert "false" in str(col.server_default.arg).lower()


def test_the_startup_guard_adds_the_column_with_the_same_default():
    """init_db's belt-and-braces ADD COLUMN IF NOT EXISTS runs on installs that
    never see the migration. If it disagreed with the migration about the
    default, whether a deployment prompts would depend on how it was upgraded."""
    source = Path(__file__).resolve().parents[1].joinpath("app/db/init_db.py").read_text()
    assert (
        "ADD COLUMN IF NOT EXISTS registration_prompt_dismissed "
        "BOOLEAN NOT NULL DEFAULT false" in source
    )


# ---------------------------------------------------------------------------
# the settings schema
# ---------------------------------------------------------------------------

def test_the_schema_defaults_to_not_dismissed():
    from app.schemas import SystemSettingsBase

    assert SystemSettingsBase().registration_prompt_dismissed is False


def test_an_unmentioned_flag_is_not_cleared_by_an_unrelated_save():
    """POST /system/settings applies model_dump(exclude_unset=True). Without
    that, a console saving only its own fields -- or an older one that has never
    heard of this -- would switch the prompt back on by omission."""
    from app.schemas import SystemSettingsBase

    dumped = SystemSettingsBase(township_name="Anytown").model_dump(exclude_unset=True)
    assert "registration_prompt_dismissed" not in dumped


def test_an_admin_can_turn_it_on_through_the_settings_payload():
    from app.schemas import SystemSettingsBase

    parsed = SystemSettingsBase(registration_prompt_dismissed=True)
    assert parsed.registration_prompt_dismissed is True
    assert parsed.model_dump(exclude_unset=True) == {"registration_prompt_dismissed": True}


# ---------------------------------------------------------------------------
# the public config payload
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, value):
        self.registration_prompt_dismissed = value


def _config(monkeypatch, settings_row):
    """GET /system/config with the settings row stubbed.

    Only the two db-touching helpers are replaced; the endpoint body -- the part
    that decides what the browser is told -- runs for real.
    """
    from app.api import system as system_api

    async def _origin(_db):
        return "https://town.example"

    async def _row(_db, **_kw):
        if isinstance(settings_row, Exception):
            raise settings_row
        return settings_row

    monkeypatch.setattr(system_api, "public_origin", _origin)
    monkeypatch.setattr(system_api, "read_settings_row", _row)
    return asyncio.run(system_api.get_deployment_config(db=None))


def test_the_flag_travels_on_the_config_the_console_already_reads(monkeypatch):
    payload = _config(monkeypatch, _Row(True))

    assert payload["registration_prompt_dismissed"] is True
    # Alongside its siblings rather than instead of them: the console decides
    # whether to show the prompt and what shape it takes from one response.
    assert "contact_form_url" in payload
    assert "contact_form_embed" in payload


def test_a_deployment_that_has_not_answered_reports_false(monkeypatch):
    assert _config(monkeypatch, _Row(False))["registration_prompt_dismissed"] is False


def test_a_missing_row_reports_false(monkeypatch):
    """A brand-new install has no settings row yet, and creating one is not this
    endpoint's job -- it is public and unauthenticated."""
    assert _config(monkeypatch, None)["registration_prompt_dismissed"] is False


def test_an_unreadable_settings_row_reports_false(monkeypatch):
    """The asymmetry that decides the error handling. Wrongly prompting a
    registered deployment is a nuisance; wrongly silencing an unregistered one
    means a database hiccup costs a town its only advisory channel."""
    payload = _config(monkeypatch, RuntimeError("database is having a day"))
    assert payload["registration_prompt_dismissed"] is False


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------

def test_the_migration_is_additive_and_defaults_the_column_off():
    """Additive auto-applies at startup; destructive halts the container until a
    human runs it. Adding a column with a server default is the former, so towns
    get this on deploy rather than in a maintenance window."""
    from app.db.migrate import ADDITIVE, classify_source, revision_sources

    sources = revision_sources()
    assert "f6b4d8e2a3c5" in sources, "the registration-flag migration is not in the image"
    _path, source = sources["f6b4d8e2a3c5"]

    assert classify_source(source) == ADDITIVE
    assert "registration_prompt_dismissed" in source
    assert "server_default='false'" in source
    # Branched off the head that existed when it was written; chain linearity as
    # a whole is pinned in tests/test_migrate.py.
    assert "down_revision: Union[str, None] = 'e5a3c7b9d1f4'" in source


def test_min_db_revision_stays_at_the_previous_head():
    """The expand case of the contract documented in backend/MIN_DB_REVISION: an
    additive migration leaves the floor at the revision before head, because this
    build still starts against the un-migrated schema."""
    text = Path(__file__).resolve().parents[1].joinpath("MIN_DB_REVISION").read_text()
    declared = [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    assert declared == ["e5a3c7b9d1f4"]
