"""The optional platform-feedback module: off means off, and nothing else is stored.

Three claims are worth pinning here, because all three are the kind that stay
true right up until somebody adds a field.

  1. OFF IS THE DEFAULT AND OFF IS ENFORCED. `platform_feedback` is false in the
     model default, the schema default and the seed, and every route in
     api/feedback.py checks the flag before doing anything. A resident-facing
     write endpoint that a town never enabled must not accept a row.

  2. THE TABLE HOLDS TWO COLUMNS AND NEITHER IDENTIFIES ANYBODY. One
     check-constrained categorical answer and a timestamp. No user id, no
     session, no IP (hashed or otherwise), no user agent, and above all no free
     text -- because free text in a municipal database is potentially
     responsive to a public-records request and can carry the resident's own
     PII. The "tell us more" path is a mailto: link, so the sentence lands in a
     mailbox and never becomes a record this town holds.

  3. NONE OF IT LEAVES THE BUILDING. Not to the research export, not through
     Open311, not into a work-order payload, not onto a public map or tracker.
     Structurally none of those read the table; these tests are what keeps that
     structural.

Imports are guarded per-test on a SUBMODULE rather than at module level: CI
installs only cryptography, httpx, pytest, pytest-asyncio and alembic, and the
source-reading tests below need none of them. A module-level skip would make
the whole file silently vanish from CI, which is how a suite stops being one.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "backend/app/models.py"
SCHEMAS = ROOT / "backend/app/schemas.py"
INIT_DB = ROOT / "backend/app/db/init_db.py"
API = ROOT / "backend/app/api/feedback.py"
MIGRATION = ROOT / "backend/alembic/versions/20260818_0900_a8c6e2f4b9d1_platform_feedback_module.py"

MODULE_KEY = "platform_feedback"
ANSWERS = {
    "much_easier",
    "somewhat_easier",
    "no_difference",
    "somewhat_harder",
    "much_harder",
}


def _needs_app():
    """The ORM and Pydantic, which CI does not install."""
    pytest.importorskip("fastapi.routing")
    pytest.importorskip("sqlalchemy.orm")
    pytest.importorskip("geoalchemy2.types")


# ---------------------------------------------------------------- off is off

def test_the_module_is_off_in_the_model_default():
    _needs_app()
    from app.models import SystemSettings

    default = SystemSettings.__table__.c.modules.default.arg
    assert MODULE_KEY in default, "the module key is missing from the modules default"
    assert default[MODULE_KEY] is False, (
        "platform_feedback defaults ON. Collecting anything from residents is a "
        "thing a town opts into."
    )


def test_the_module_is_off_in_the_settings_schema_default():
    _needs_app()
    from app.schemas import SystemSettingsBase

    assert SystemSettingsBase().modules[MODULE_KEY] is False


def test_the_module_is_off_in_the_seed():
    """A freshly seeded town has not agreed to run this."""
    source = INIT_DB.read_text()
    block = source[source.index("modules={"):source.index("capability_switches={}")]
    assert f'"{MODULE_KEY}": False' in block, block


def test_the_new_column_has_an_add_column_if_not_exists_guard():
    """Belt and braces alongside the alembic revision, the way recent columns do."""
    source = INIT_DB.read_text()
    assert (
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS platform_feedback_email"
        in source
    )


def test_every_route_checks_the_module_before_doing_anything():
    """The UI gate is a courtesy; this is the enforcement.

    Asserted structurally rather than by grepping the file, so a route added
    later without the check fails here instead of inheriting the coverage of
    its neighbours.
    """
    tree = ast.parse(API.read_text())
    routes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "patch", "delete"}
            for d in node.decorator_list
        )
    ]
    assert routes, "no routes found in api/feedback.py at all"
    for fn in routes:
        body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
        assert "_module_enabled" in body, (
            f"{fn.name} does not check whether the town enabled the module. "
            f"Off has to mean the endpoint refuses, not that the button is hidden."
        )


def test_a_disabled_module_reads_as_off_when_settings_cannot_be_read():
    """Fail closed. An unreadable settings row must not open a public write."""
    source = API.read_text()
    guard = source[source.index("async def _module_enabled"):source.index("def _off()")]
    assert "return False" in guard.split("except", 1)[1], (
        "_module_enabled does not fail closed"
    )
    assert 'modules.get("platform_feedback", False)' in guard, (
        "an absent key must read as off"
    )


def test_the_module_being_off_is_a_404_not_a_403():
    """403 would advertise a feature the town chose not to run."""
    source = API.read_text()
    off = source[source.index("def _off()"):source.index("@router.post")]
    assert "status_code=404" in off


# ------------------------------------------------- what the table may hold

def test_the_table_has_exactly_two_columns_and_neither_is_identifying():
    _needs_app()
    from app.models import PlatformFeedback

    columns = {c.name for c in PlatformFeedback.__table__.columns}
    assert columns == {"id", "platform_experience", "submitted_at"}, (
        f"platform_feedback grew columns: {sorted(columns)}. Every one of them "
        f"is a records-retention decision and a re-identification surface. "
        f"Read the class docstring before adding another."
    )


@pytest.mark.parametrize("forbidden", [
    # Anything that ties a row to a person, a device or a report.
    "user_id", "session_id", "ip_address", "ip_hash", "client_hash",
    "user_agent", "email", "name", "service_request_id", "request_id",
    "fingerprint", "cookie",
    # Free text, in any of its usual names. The point of this module is that a
    # resident's sentences live in a mailbox, not in the town's database.
    "comment", "comments", "message", "body", "text", "note", "notes",
    "free_text", "details", "description", "feedback",
])
def test_no_identifying_or_free_text_column_exists(forbidden):
    _needs_app()
    from app.models import PlatformFeedback

    assert forbidden not in PlatformFeedback.__table__.columns, (
        f"platform_feedback.{forbidden} exists. If this is free text: it is "
        f"stored in a municipal database, so it is potentially responsive to a "
        f"public-records request and can contain the resident's own PII. The "
        f"module routes longer feedback to email precisely to avoid that. If it "
        f"identifies a client: the aggregate stops being anonymous."
    )


def test_the_answer_is_constrained_in_the_column_not_only_in_python():
    _needs_app()
    from app.models import PlatformFeedback

    checks = [
        c for c in PlatformFeedback.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    ]
    assert checks, (
        "no CHECK constraint on platform_experience. 'A constrained categorical "
        "value' has to be true of the column, not just of the API in front of it."
    )
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for answer in ANSWERS:
        assert answer in sqltext, f"{answer} is not permitted by the CHECK"


def test_the_api_and_the_column_permit_the_same_five_answers():
    _needs_app()
    from app.models import PLATFORM_FEEDBACK_ANSWERS
    from app.schemas import PlatformFeedbackCreate

    assert set(PLATFORM_FEEDBACK_ANSWERS) == ANSWERS
    assert len(PLATFORM_FEEDBACK_ANSWERS) == 5, "the scale is five points"
    for answer in PLATFORM_FEEDBACK_ANSWERS:
        assert PlatformFeedbackCreate(platform_experience=answer).platform_experience == answer
    with pytest.raises(Exception):
        PlatformFeedbackCreate(platform_experience="amazing")
    with pytest.raises(Exception):
        # The shape a free-text field would sneak in as.
        PlatformFeedbackCreate(platform_experience="I would like to say that ...")


def test_the_submission_body_accepts_nothing_but_the_answer():
    """Extra keys must not become extra columns by accident later."""
    _needs_app()
    from app.schemas import PlatformFeedbackCreate

    assert set(PlatformFeedbackCreate.model_fields) == {"platform_experience"}


# ------------------------------------------------------------- aggregates

def test_the_aggregate_reports_a_distribution_and_no_mean():
    """The scale is ordinal. Averaging it would assert an even spacing between
    "much harder" and "somewhat harder" that nobody measured."""
    _needs_app()
    from app.schemas import PlatformFeedbackStatisticsResponse

    fields = set(PlatformFeedbackStatisticsResponse.model_fields)
    assert {"total_responses", "counts", "percentages", "responses_by_month"} <= fields
    assert not any("average" in f or f in {"mean", "score", "rating"} for f in fields), (
        f"a mean crept into the aggregate: {sorted(fields)}"
    )


def test_the_aggregate_exposes_no_individual_rows():
    _needs_app()
    from app.schemas import PlatformFeedbackStatisticsResponse

    for name, field in PlatformFeedbackStatisticsResponse.model_fields.items():
        assert "PlatformFeedback" not in str(field.annotation), (
            f"{name} carries model rows into a staff response; this endpoint "
            f"returns counts only."
        )


def test_the_statistics_endpoint_is_staff_only():
    source = API.read_text()
    signature = source[
        source.index("async def get_platform_feedback_statistics"):
        source.index("The distribution, for the statistics page")
    ]
    assert "get_current_staff" in signature, signature


def test_the_monthly_keys_match_the_other_trends_on_that_page():
    """AdvancedStatistics.requests_by_month is "YYYY-MM"; so is this, so the
    panel renders like its neighbours instead of inventing a second format."""
    assert '"YYYY-MM"' in API.read_text()


# ------------------------------------------------------------------- abuse

def test_the_submit_endpoint_is_rate_limited_and_the_table_is_capped():
    """Per-IP limiting is one global bucket behind Caddy (see main.py), so the
    endpoint needs both a burst ceiling and a bound on stored rows."""
    source = API.read_text()
    assert re.search(r"@limiter\.limit\(", source), "no rate limit on the module at all"
    assert "DAILY_SUBMISSION_CAP" in source
    submit = source[source.index("async def submit_platform_feedback"):source.index("@router.get")]
    assert "DAILY_SUBMISSION_CAP" in submit, "the cap is defined but never enforced"
    assert "429" in submit


def test_abuse_protection_stores_no_client_key():
    """Not even a salted hash. A per-client key, hashed or not, is exactly the
    link this module exists without."""
    _needs_app()
    from app.models import PlatformFeedback

    source = API.read_text()
    columns = {c.name for c in PlatformFeedback.__table__.columns}
    assert not any("hash" in c or "ip" in c or "client" in c for c in columns), columns
    stored = source[source.index("db.add(PlatformFeedback("):]
    stored = stored[:stored.index(")")]
    assert "platform_experience" in stored and "ip" not in stored and "hash" not in stored


def test_nothing_about_the_caller_is_read_on_submit():
    source = API.read_text()
    submit = source[source.index("async def submit_platform_feedback"):source.index("@router.get")]
    for leak in ("X-Forwarded-For", "request.client", "user_agent", "User-Agent", "cookies"):
        assert leak not in submit, f"the submit handler reads {leak}"


# --------------------------------------------------- nothing leaves the building

@pytest.mark.parametrize("path", [
    "backend/app/api/research.py",
    "backend/app/api/open311.py",
    "backend/app/tasks/integrations.py",
    "backend/app/api/data_export.py",
    "backend/app/services/public_visibility.py",
])
def test_the_feedback_table_is_absent_from_every_outbound_surface(path):
    """Research export, Open311, govtech work orders, the bulk export and the
    public tracker. None of them may learn this table exists.

    Aggregate sentiment about a vendor is not something to ship to that vendor,
    and it is not research data any town agreed to release.
    """
    source = (ROOT / path).read_text()
    for token in ("PlatformFeedback", "platform_feedback", "platform_experience"):
        assert token not in source, (
            f"{path} references {token}. Platform feedback is about Pinpoint, "
            f"not about a service request, and it belongs in none of these."
        )


def test_the_research_export_column_dictionary_never_mentions_it():
    from_research = (ROOT / "backend/app/api/research.py").read_text()
    assert "feedback" not in from_research.lower().replace("feedback_", ""), (
        "the research surface has grown a notion of platform feedback"
    )


# --------------------------------------------------------------- migration

def test_the_migration_is_additive():
    """A destructive classification halts the container on startup."""
    from app.db.migrate import ADDITIVE, classify_source

    assert classify_source(MIGRATION.read_text()) == ADDITIVE


def test_the_migration_creates_the_check_constraint():
    source = MIGRATION.read_text()
    assert "CheckConstraint" in source
    for answer in ANSWERS:
        assert answer in source


def test_min_db_revision_is_the_revision_before_head():
    """Additive migration: the new build still runs on the old schema, so the
    declared floor is the revision immediately before whatever head is.

    Derived from the revision chain rather than hardcoded. An earlier version
    pinned the literal 'f6b4d8e2a3c5', which meant every later migration failed
    this test whether or not the floor was right -- and the path of least
    resistance was to edit the expected string instead of checking the rule.
    A test that has to be silenced to land ordinary work protects nothing.
    """
    versions = ROOT / "backend/alembic/versions"
    revs = {}
    for path in versions.glob("*.py"):
        text = path.read_text()
        rev = re.search(r"(?m)^revision(?::[^=]*)?\s*=\s*['\"]([^'\"]+)", text)
        if not rev:
            continue
        down = re.search(
            r"(?m)^down_revision(?::[^=]*)?\s*=\s*(?:\(([^)]*)\)|['\"]([^'\"]+)['\"]|None)",
            text,
        )
        parents = []
        if down:
            if down.group(1):
                parents = [x.strip().strip("'\"") for x in down.group(1).split(",") if x.strip()]
            elif down.group(2):
                parents = [down.group(2)]
        revs[rev.group(1)] = parents

    children = {}
    for rev, parents in revs.items():
        for parent in parents:
            children.setdefault(parent, []).append(rev)
    heads = [r for r in revs if r not in children]
    assert len(heads) == 1, f"expected one head, found {heads}"

    expected = revs[heads[0]]
    assert len(expected) == 1, f"head {heads[0]} is a merge revision: {expected}"

    declared = [
        line.strip()
        for line in (ROOT / "backend/MIN_DB_REVISION").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert declared == expected, f"declared {declared}, head's parent is {expected}"
