"""Staff-controlled archival from the public tracker and map.

Archival here means one thing and must never quietly become another: the report
stops appearing in PUBLIC LISTINGS. It is not deleted, not redacted, not hidden
from staff, its tracking link still works, and it is still in the research
export. Every direction that could drift is pinned below, because each drifts
into a different kind of wrong:

  * the policy failing to hide anything -> the feature silently does nothing;
  * the policy hiding an OPEN report -> the town's live problems vanish;
  * archival reaching the by-id endpoints -> every tracking link 404s and the
    resident concludes the town deleted their report;
  * archival reaching the staff list -> staff lose reports they must still work;
  * archival reaching research -> a town's history is truncated at whatever
    date its map got busy, silently, inside longitudinal analyses.

The listing tests run against a real SQLite engine rather than asserting on
generated SQL, so the boundary arithmetic is checked by a database doing the
comparison rather than by this file agreeing with itself.
"""

from datetime import datetime, timedelta, timezone

import pytest

# Guard on a submodule, never bare "app" or bare "alembic": CI installs only
# cryptography, httpx, pytest, pytest-asyncio and alembic, and a bare guard on
# a name that resolves as a namespace package silently passes and takes the
# whole file with it. See the header of tests/test_migrate.py.
pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi.routing")

from sqlalchemy import create_engine, select, text

from app.models import ServiceRequest, SystemSettings
from app.services.public_visibility import archive_cutoff, publicly_listed_conditions


NOW = datetime.now(timezone.utc)


class Settings:
    """Stand-in for the SystemSettings row, which is all the helper reads."""

    def __init__(self, public_archive_days=None):
        self.public_archive_days = public_archive_days


# ---------------------------------------------------------------------------
# a real engine, with only the columns the listing rule touches
# ---------------------------------------------------------------------------

# Not ServiceRequest.__table__.create(): that table carries a PostGIS geometry
# column and encrypted-PII columns that SQLite cannot make and this rule never
# reads. The rule compiles to `SELECT id FROM service_requests WHERE ...` over
# exactly these six columns, so this is the whole surface under test.
_SCHEMA = """
CREATE TABLE service_requests (
    id INTEGER PRIMARY KEY,
    service_request_id VARCHAR(50),
    status VARCHAR(20),
    closed_datetime DATETIME,
    is_public BOOLEAN NOT NULL DEFAULT 1,
    public_archived BOOLEAN NOT NULL DEFAULT 0,
    deleted_at DATETIME
)
"""


def _engine(rows):
    """An in-memory database holding `rows`, each a dict of column values."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_SCHEMA))
        for row in rows:
            full = {
                "id": None, "service_request_id": None, "status": "open",
                "closed_datetime": None, "is_public": True,
                "public_archived": False, "deleted_at": None,
                **row,
            }
            conn.execute(
                text(
                    "INSERT INTO service_requests "
                    "(id, service_request_id, status, closed_datetime, is_public,"
                    " public_archived, deleted_at) "
                    "VALUES (:id, :service_request_id, :status, :closed_datetime,"
                    " :is_public, :public_archived, :deleted_at)"
                ),
                full,
            )
    return engine


def _listed(rows, settings=None):
    """The ids a public listing would show for `rows` under `settings`."""
    engine = _engine(rows)
    with engine.connect() as conn:
        result = conn.execute(
            select(ServiceRequest.id).where(*publicly_listed_conditions(settings))
        )
        return {r[0] for r in result}


def closed(days_ago: int, **kw):
    return {"status": "closed", "closed_datetime": NOW - timedelta(days=days_ago), **kw}


# ---------------------------------------------------------------------------
# the town-wide policy, at its boundary
# ---------------------------------------------------------------------------

def test_a_report_closed_just_inside_the_window_is_still_listed():
    rows = [dict(closed(29), id=1)]
    assert _listed(rows, Settings(30)) == {1}


def test_a_report_closed_just_outside_the_window_is_not():
    rows = [dict(closed(31), id=1)]
    assert _listed(rows, Settings(30)) == set()


def test_the_boundary_falls_between_the_two():
    """Both at once, so a rule that hides everything or nothing fails here."""
    rows = [dict(closed(29), id=1), dict(closed(31), id=2)]
    assert _listed(rows, Settings(30)) == {1}


def test_an_open_report_is_never_aged_out():
    """Age applies to finished business. A water main open for two years is
    exactly the report the map exists to show."""
    rows = [{"id": 1, "status": "open"}, {"id": 2, "status": "in_progress"}]
    assert _listed(rows, Settings(30)) == {1, 2}


def test_a_closed_report_with_no_closing_timestamp_stays_listed():
    """It has no age to measure -- rows predating closed_datetime, and rows
    closed by a direct database edit. Inventing one would unlist a town's
    history on the day it first sets the number."""
    rows = [{"id": 1, "status": "closed", "closed_datetime": None}]
    assert _listed(rows, Settings(30)) == {1}


@pytest.mark.parametrize("value", [None, 0])
def test_no_policy_means_nothing_is_aged_out(value):
    rows = [dict(closed(3650), id=1)]
    assert _listed(rows, Settings(value)) == {1}
    assert archive_cutoff(Settings(value)) is None


def test_absent_settings_row_applies_no_policy():
    """A town that has never opened the settings page, and the soft-fail path
    when the settings read errors. Both must leave the map alone."""
    rows = [dict(closed(3650), id=1)]
    assert _listed(rows, None) == {1}


def test_the_policy_is_retroactive_in_both_directions():
    """Nothing is stamped on a row, so widening the window brings reports back.
    This is the whole reason the policy is a WHERE clause and not a job."""
    rows = [dict(closed(100), id=1)]
    assert _listed(rows, Settings(30)) == set()
    assert _listed(rows, Settings(365)) == {1}
    assert _listed(rows, Settings(None)) == {1}


# ---------------------------------------------------------------------------
# the per-report staff flag
# ---------------------------------------------------------------------------

def test_a_manually_archived_report_is_excluded():
    rows = [{"id": 1, "public_archived": True}, {"id": 2, "public_archived": False}]
    assert _listed(rows) == {2}


def test_a_manually_archived_report_is_excluded_even_when_open():
    """The staff flag is a decision, not an age. It does not wait for closure."""
    rows = [{"id": 1, "status": "open", "public_archived": True}]
    assert _listed(rows, Settings(30)) == set()


def test_the_residents_own_choice_is_still_honoured():
    rows = [{"id": 1, "is_public": False}, {"id": 2, "is_public": True}]
    assert _listed(rows) == {2}


def test_soft_deleted_records_are_still_excluded():
    rows = [{"id": 1, "deleted_at": NOW}, {"id": 2}]
    assert _listed(rows) == {2}


def test_archival_and_the_residents_choice_are_separate_columns():
    """Staff must never write is_public: it records what the RESIDENT asked
    for. If archival reused it, unarchiving would republish a report somebody
    had deliberately kept off the map."""
    assert "is_public" in ServiceRequest.__table__.c
    assert "public_archived" in ServiceRequest.__table__.c


def test_nothing_is_archived_until_somebody_says_so():
    col = ServiceRequest.__table__.c.public_archived
    assert col.default.arg is False
    assert col.nullable is False
    assert "false" in str(col.server_default.arg).lower()


def test_the_policy_column_has_no_default():
    """NULL means "not configured", the same reading as retention_days next
    door. A default here would be the product choosing a town's policy."""
    assert SystemSettings.__table__.c.public_archive_days.default is None
    assert SystemSettings.__table__.c.public_archive_days.nullable is True


# ---------------------------------------------------------------------------
# everything archival must NOT reach
# ---------------------------------------------------------------------------

def _sql_of(fn_result):
    return str(
        select(ServiceRequest.id).where(*fn_result).compile(
            compile_kwargs={"literal_binds": True})
    )


def test_a_direct_link_still_works_for_an_archived_report():
    """The failure that would look like a deletion to the resident holding the
    link. The by-id rule is only the soft-delete clause and must stay that way."""
    from app.api.open311 import direct_link_filters

    sql = _sql_of(direct_link_filters())
    assert "public_archived" not in sql
    assert sql.split("WHERE")[1].strip() == "service_requests.deleted_at IS NULL"


def test_every_by_id_endpoint_still_uses_the_direct_link_rule():
    import inspect

    from app.api import open311

    for name in ("get_public_request_detail", "get_public_comments",
                 "get_public_audit_log", "lookup_request_by_token"):
        source = inspect.getsource(getattr(open311, name))
        assert "direct_link_filters()" in source, name
        assert "public_archived" not in source, f"{name} filters public_archived"


def test_the_staff_list_is_unaffected():
    """Staff must still see, and still be able to work, an archived report."""
    import inspect

    from app.api import open311

    source = inspect.getsource(open311.list_requests)
    assert "public_archived" not in source
    assert "publicly_listed_conditions" not in source


def test_research_exports_still_include_archived_reports():
    """Archival is decluttering, not a privacy request. Only the resident's own
    unlisted choice keeps a row out of research. See the docstring on
    research_visibility_conditions -- this is deliberate."""
    from app.api.research import research_visibility_conditions

    sql = _sql_of(research_visibility_conditions())
    assert "public_archived" not in sql
    assert "closed_datetime" not in sql
    assert "is_public" in sql  # the resident's choice IS still honoured


def test_the_public_listing_endpoint_applies_the_shared_rule():
    import inspect

    from app.api import open311

    source = inspect.getsource(open311.list_public_requests)
    assert "publicly_listed_conditions(settings_row)" in source
    # The cached response must not outlive the policy that produced it.
    assert "arch" in source and "cache_key" in source


# ---------------------------------------------------------------------------
# the staff toggle
# ---------------------------------------------------------------------------

def _code_of(fn) -> str:
    """A function's source with its docstring removed.

    These assertions are about what the code does; a docstring that explains
    what the code deliberately does NOT do would otherwise fail them.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def test_the_toggle_endpoint_is_staff_only_and_audited():
    import inspect

    from app.api import open311

    signature = inspect.getsource(open311.set_public_archived).split("):")[0]
    assert "Depends(get_current_staff)" in signature

    code = _code_of(open311.set_public_archived)
    assert "RequestAuditLog(" in code
    assert "action='public_archive'" in code or 'action="public_archive"' in code
    assert "actor_name=current_user.username" in code
    # It must never write the resident's own field.
    assert "is_public" not in code


def test_the_toggle_finds_an_already_archived_report():
    """It uses the by-id rule, so unarchiving works -- a toggle that could only
    be pressed once would be a trap."""
    import inspect

    from app.api import open311

    assert "direct_link_filters()" in inspect.getsource(open311.set_public_archived)


# ---------------------------------------------------------------------------
# the settings field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [(None, None), (0, None), ("", None), (30, 30), ("30", 30)])
def test_the_settings_field_stores_one_representation_of_unset(given, expected):
    from app.schemas import SystemSettingsBase

    parsed = SystemSettingsBase(public_archive_days=given)
    assert parsed.public_archive_days == expected


@pytest.mark.parametrize("bad", [-1, 36501, "soon"])
def test_the_settings_field_rejects_nonsense(bad):
    from app.schemas import SystemSettingsBase

    with pytest.raises(Exception):
        SystemSettingsBase(public_archive_days=bad)


def test_an_unmentioned_policy_is_not_cleared_by_an_unrelated_save():
    """POST /system/settings applies model_dump(exclude_unset=True). A save that
    never mentions the field must leave a configured policy alone."""
    from app.schemas import SystemSettingsBase

    dumped = SystemSettingsBase(township_name="Anytown").model_dump(exclude_unset=True)
    assert "public_archive_days" not in dumped


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------

def test_the_migration_is_additive():
    """An additive migration auto-applies at startup; a destructive one blocks
    the container until a human runs it. This one only adds columns, so a town
    gets the feature on deploy rather than on a maintenance window."""
    from app.db.migrate import ADDITIVE, classify_source, revision_sources

    sources = revision_sources()
    assert "d4f2a6b8c1e3" in sources, "the archival migration is not in the image"
    _path, source = sources["d4f2a6b8c1e3"]

    assert classify_source(source) == ADDITIVE
    assert "public_archived" in source
    assert "public_archive_days" in source
    # It was branched off the single head that existed when it was written.
    # Chain linearity as a whole is pinned in tests/test_migrate.py.
    assert 'down_revision: Union[str, None] = "c3d1e5f7a9b2"' in source
