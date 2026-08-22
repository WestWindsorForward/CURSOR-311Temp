"""Tests for the startup schema reconciler.

This module decides whether a municipality's database gets altered with nobody
watching, so the classifier is the thing under real scrutiny here. Two failure
directions, and they are not symmetric:

  * a destructive migration classified additive gets auto-applied to live
    records -- a town loses data it is legally required to retain;
  * an additive migration classified destructive blocks the container -- a town
    is stuck on an old build until someone reads the log.

The second is annoying. The first is unrecoverable. So every ambiguous case
must resolve to DESTRUCTIVE, and the tests below are mostly about proving the
ambiguity actually resolves that way.

The false-positive cases are here too, because they are not hypothetical: the
first version of this classifier marked six of the seven existing migrations
destructive, on `sa.text()` used as a server_default.
"""

from pathlib import Path

import pytest

# No module-level importorskip. There used to be one, for alembic, and it made
# this whole file silently skip in CI -- which does not install alembic -- so
# the chain regression test below first actually ran inside the production
# image, months and ten revisions after it last passed. The classifier and
# revision_sources() are pure file-reading code with no third-party imports;
# only the tests that call into alembic itself guard on it, individually.
#
# Guard on a submodule, not "alembic", where a guard is needed: backend/alembic/
# is the migrations directory, and with alembic uninstalled Python resolves that
# directory as a namespace package, so importorskip("alembic") succeeds and the
# guard misses.
def _needs_alembic():
    pytest.importorskip("alembic.script")
    # Reading the chain executes every revision file, and the road-geometry
    # revision imports geoalchemy2 at module level -- so alembic being present
    # is not on its own enough to load the versions directory.
    pytest.importorskip("geoalchemy2.types")

from app.db.migrate import (
    ADDITIVE,
    DESTRUCTIVE,
    Plan,
    Revision,
    classify_revisions,
    classify_source,
    format_plan,
    revision_sources,
    sync_url,
    upgrade_body,
)


def migration(body: str, down: str = "    pass") -> str:
    """A realistic revision file wrapped around the body under test."""
    return (
        '"""Some change.\n\nRevision ID: abc123\n"""\n'
        "import sqlalchemy as sa\nfrom alembic import op\n\n"
        'revision = "abc123"\ndown_revision = "def456"\n\n\n'
        f"def upgrade() -> None:\n{body}\n\n\ndef downgrade() -> None:\n{down}\n"
    )


# ---- things that must be gated ----------------------------------------------

@pytest.mark.parametrize("body", [
    '    op.drop_column("service_requests", "media_url")',
    '    op.drop_table("legacy_photos")',
    '    op.rename_table("old", "new")',
    '    op.alter_column("t", "c", type_=sa.Integer())',
    '    op.alter_column("t", "c", new_column_name="d")',
    # Narrowing truncates stored values; a widen with no stated starting
    # length is not provably a widen. Both stay gated.
    '    op.alter_column("t", "c", existing_type=sa.String(500), type_=sa.String(200))',
    '    op.alter_column("t", "c", type_=sa.String(500))',
])
def test_data_losing_operations_are_destructive(body):
    assert classify_source(migration(body)) == DESTRUCTIVE


def test_widening_a_varchar_applies_unattended():
    """Lengthening a varchar is metadata-only in Postgres and cannot lose a
    byte -- and it is the fix for ciphertext outgrowing its column, which a
    town needs applied at deploy time, not gated behind a human. Only the
    provable case passes: both lengths stated, new >= old."""
    body = ('    op.alter_column(\n'
            '        "service_requests", "phone",\n'
            '        existing_type=sa.String(length=200),\n'
            '        type_=sa.String(length=500),\n'
            '        existing_nullable=True,\n'
            '    )')
    assert classify_source(migration(body)) == ADDITIVE


@pytest.mark.parametrize("body", [
    # postgresql_using is arbitrary SQL riding inside alter_column: with both
    # lengths equal the old check called this a "widen" while the USING clause
    # blanked every row.
    '    op.alter_column("t", "c", existing_type=sa.String(255), '
    'type_=sa.String(255), postgresql_using="\'\'")',
    # A genuine widen does not launder the USING clause riding with it.
    '    op.alter_column("t", "c", existing_type=sa.String(50), '
    'type_=sa.String(500), postgresql_using="NULL")',
    # Adjacent calls must be judged one by one. The old regex span could run
    # from the destructive call's opening paren to the widen's closing one,
    # merging them into a single argument list that read as a widen.
    ('    op.alter_column("t", "c", type_=sa.Integer())\n'
     '    op.alter_column("t", "d", existing_type=sa.String(50), type_=sa.String(500))'),
    ('    op.alter_column("t", "c", type_=sa.Integer()); '
     'op.alter_column("t", "d", existing_type=sa.String(50), type_=sa.String(500))'),
])
def test_alter_column_cannot_smuggle_work_past_the_widen_exemption(body):
    """The widen exemption accepts exactly one shape: both lengths stated,
    new >= old, and no kwargs beyond the ones a widen needs. Anything a call
    carries beyond that -- and any judging of two calls as one -- resolves the
    usual way: make a human look at it."""
    assert classify_source(migration(body)) == DESTRUCTIVE


def test_a_widen_next_to_another_widen_is_still_additive():
    """The per-call judging must not overcorrect: two legitimate widens in one
    upgrade() are as safe as one."""
    body = ('    op.alter_column("t", "c", existing_type=sa.String(50), type_=sa.String(500))\n'
            '    op.alter_column("t", "d", existing_type=sa.String(100), type_=sa.String(200))')
    assert classify_source(migration(body)) == ADDITIVE


def test_arbitrary_sql_is_destructive_because_it_cannot_be_read():
    assert classify_source(migration('    op.execute("DELETE FROM service_requests")')) == DESTRUCTIVE
    assert classify_source(migration('    op.execute("UPDATE users SET role = \'admin\'")')) == DESTRUCTIVE


def test_sql_assembled_at_runtime_is_destructive():
    """The literal is what gets read. A variable, an f-string substitution or a
    concatenation means the SQL that actually runs is not the SQL scanned."""
    assert classify_source(migration("    op.execute(stmt)")) == DESTRUCTIVE
    assert classify_source(migration('    op.execute(f"DROP TABLE {name}")')) == DESTRUCTIVE
    assert classify_source(migration('    op.execute("CREATE INDEX " + name)')) == DESTRUCTIVE
    assert classify_source(migration('    op.execute("CREATE INDEX {}".format(n))')) == DESTRUCTIVE


def test_a_looped_execute_is_destructive():
    """A tuple of statements iterated with op.execute(stmt) is unreadable at
    scan time. My own road-tables migration was written this way first and was
    correctly gated; it was rewritten as literal calls, not exempted."""
    body = ('    for stmt in ("CREATE INDEX a ON t (c)", "DROP TABLE u"):\n'
            "        op.execute(stmt)")
    assert classify_source(migration(body)) == DESTRUCTIVE


def test_sql_run_through_a_raw_connection_is_destructive():
    assert classify_source(migration('    op.get_bind().execute(sa.text("DELETE FROM t"))')) == DESTRUCTIVE
    assert classify_source(migration('    conn.execute(sa.text("DROP TABLE t"))')) == DESTRUCTIVE


def test_a_bind_variable_does_not_smuggle_sql_past_the_gate():
    """The check used to match only a variable literally named `conn`, so
    `bind = op.get_bind(); bind.execute(sa.delete(...))` -- a row deletion --
    classified ADDITIVE. A real revision (the system_settings singleton
    collapse) was written exactly that way; it happened to be gated anyway on
    a separate op.execute, which is the only reason the bypass never fired."""
    body = ("    bind = op.get_bind()\n"
            "    bind.execute(sa.delete(table).where(table.c.id.in_(drop)))")
    assert classify_source(migration(body)) == DESTRUCTIVE
    assert classify_source(migration("    session.execute(stmt)")) == DESTRUCTIVE


@pytest.mark.parametrize("source", [
    "",
    "revision = 'abc'\n",                       # metadata but no upgrade()
    "def upgrad(: broken syntax",
])
def test_a_file_with_no_readable_upgrade_is_destructive(source):
    """Truncated, unreadable, or not a migration at all. The first version of
    this returned ADDITIVE for an empty string, which would auto-apply a
    revision nothing had actually read."""
    assert classify_source(source) == DESTRUCTIVE


def test_an_explicitly_empty_upgrade_is_still_additive():
    """A no-op revision is legitimate -- data-only migrations and merge points
    look like this. The distinction is whether upgrade() exists, not whether it
    does anything."""
    assert classify_source(migration("    pass")) == ADDITIVE


def test_a_revision_whose_file_is_missing_is_destructive():
    """The database is ahead of the image; someone rolled a container back.
    Guessing what the missing revision did is exactly wrong."""
    assert classify_revisions(["nosuchrev"], {})[0].kind == DESTRUCTIVE


# ---- things that must NOT be gated ------------------------------------------

@pytest.mark.parametrize("body", [
    '    op.create_table("t", sa.Column("id", sa.Integer()))',
    '    op.add_column("t", sa.Column("c", sa.String()))',
    '    op.create_index("ix_t_c", "t", ["c"])',
    '    op.create_unique_constraint("uq_t_c", "t", ["c"])',
    '    op.alter_column("t", "c", nullable=True)',
    '    op.drop_index("ix_t_c", table_name="t")',
])
def test_additive_operations_apply_unattended(body):
    assert classify_source(migration(body)) == ADDITIVE


def test_a_server_default_of_text_now_is_not_raw_sql():
    """The false positive that marked six of seven real migrations destructive.
    sa.text() in a column default is the ordinary way to write one and changes
    no rows."""
    body = ('    op.create_table("t",\n'
            '        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")))')
    assert classify_source(migration(body)) == ADDITIVE


def test_get_bind_used_only_for_inspection_is_not_destructive():
    """Migrations call get_bind() to check what already exists so they can be
    idempotent. Reading is not writing."""
    body = ('    existing = set(sa.inspect(op.get_bind()).get_table_names())\n'
            '    if "t" not in existing:\n'
            '        op.create_table("t", sa.Column("id", sa.Integer()))')
    assert classify_source(migration(body)) == ADDITIVE


@pytest.mark.parametrize("sql", [
    "CREATE INDEX IF NOT EXISTS ix_a ON t (c)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_a ON t (a, b)",
    "CREATE EXTENSION IF NOT EXISTS postgis",
    "ANALYZE road_segments",
])
def test_provably_additive_raw_sql_is_allowed(sql):
    """Some DDL cannot be expressed through Alembic's op layer -- a GIST index
    on a cast expression, for one -- so gating every raw statement would mean
    the index road lookups depend on never gets created unattended."""
    assert classify_source(migration(f'    op.execute("{sql}")')) == ADDITIVE


def test_implicitly_concatenated_sql_reads_as_one_statement():
    """Long DDL is wrapped across lines as adjacent literals. Only the first
    carries the verb; judging the continuations separately would gate it."""
    body = ('    op.execute("CREATE INDEX IF NOT EXISTS ix_road_segments_geog "\n'
            '               "ON road_segments USING GIST ((geom::geography))")')
    assert classify_source(migration(body)) == ADDITIVE


def test_a_docstring_mentioning_a_drop_does_not_gate_the_migration():
    """Revisions are named after what they do. Scanning prose rather than code
    would gate anything whose docstring says "drop"."""
    source = migration('    op.add_column("t", sa.Column("c", sa.String()))')
    source = source.replace("Some change.", "Replace the column we drop in the next release.")
    assert classify_source(source) == ADDITIVE


def test_downgrade_is_not_scanned():
    """Every downgrade() is drops by construction -- that is what a downgrade
    is. Scanning it would mark every migration destructive."""
    source = migration('    op.create_table("t", sa.Column("id", sa.Integer()))',
                       down='    op.drop_table("t")')
    assert classify_source(source) == ADDITIVE
    assert "drop_table" not in upgrade_body(source)


# ---- the real migrations in this repository ---------------------------------

# Every revision the gate holds for a human, and why. This is a ledger, not a
# tolerance list: a new revision landing here must be a deliberate decision,
# because each entry means a town upgrading across it has its container stop
# and wait for an operator to run the printed override command.
#
# None of these are classifier false positives. Each one really does what the
# gate exists to make a human look at: drop or rename columns that live code
# may still be reading, or rewrite rows through raw SQL the scanner cannot
# prove harmless.
EXPECTED_GATED = {
    # Drops four service_requests columns, including photos and media_url.
    "20260203_1911_2237fb926131_add_uptime_records_table.py",
    # Deletes duplicate system_settings rows after folding their values.
    "20260731_1400_d0e1f2a3b4c5_system_settings_singleton.py",
    # UPDATEs retention_mode values ('anonymize' -> 'redact').
    "20260801_0400_e1f2a3b4c5d6_retention_scrub_fields.py",
    # Renames three ai_* columns -- breaks the old container mid-rolling-deploy.
    "20260801_0600_f2a3b4c5d6e7_ai_columns_and_town_timezone.py",
    # UPDATEs retention_mode values ('delete' -> 'purge').
    "20260801_0800_a3b4c5d6e7f8_retire_hard_delete.py",
    # Raw-SQL backfill of connector_health.last_result from last_error.
    "20260801_1000_b4c5d6e7f8a9_persist_last_check_result.py",
    # Raw-SQL backfill of NULL retention_mode rows.
    "20260802_0900_c5d6e7f8a9b0_retention_state_must_be_chosen.py",
    # Drops retention_state_code / retention_state_confirmed, renames
    # retention_days_override.
    "20260804_0900_d6e7f8a9b0c1_town_sets_its_own_retention.py",
    # Rewrites the modules JSON and seeds capability_switches over a raw bind.
    "20260804_1400_f1a2b3c4d5e6_capability_switches.py",
    # Raw-SQL backfill of setup_completed_at.
    "20260804_1500_a2b3c4d5e6f7_setup_completed_marker.py",
    # Drops the never-read integration_links.documents_pushed boolean --
    # deliberately its own revision so the additive fix before it applies
    # unattended and only this tidy-up waits for an operator.
    "20260806_0910_a7029676a2bc_drop_dead_documents_pushed_flag.py",
}


def test_every_shipped_migration_classifies_and_the_gated_set_is_deliberate():
    """A regression guard on the whole chain. If a future revision trips the
    gate, that should be a deliberate decision, not a surprise on a town's
    server at 3am.

    This test must never be guarded on an optional import: it skipped in CI
    for months behind a module-level importorskip("alembic.script") while ten
    gated revisions accumulated, and the first place it actually ran was the
    production image. revision_sources() and classify_source() read files and
    need nothing beyond the standard library."""
    sources = revision_sources()
    assert sources, "no migrations found"

    gated = {path.name for _, (path, src) in sources.items()
             if classify_source(src) == DESTRUCTIVE}

    assert gated == EXPECTED_GATED, (
        f"newly gated: {sorted(gated - EXPECTED_GATED)}; "
        f"no longer gated: {sorted(EXPECTED_GATED - gated)}"
    )


def test_the_road_tables_migration_exists_and_can_auto_apply():
    """It closes the gap where these tables existed only in the models, so
    `alembic upgrade head` produced a schema missing all of them."""
    sources = revision_sources()
    assert "e5f6a7b8c9d0" in sources
    path, source = sources["e5f6a7b8c9d0"]
    assert classify_source(source) == ADDITIVE
    for table in ("road_segments", "road_data_status", "blocked_request_log"):
        assert table in source
    # The GIST index must be on the geography cast, not the bare column: a query
    # on the cast will not use an index on the geometry, and the result is a
    # silent full scan on every road lookup.
    assert "GIST ((geom::geography))" in source


def test_the_revision_chain_is_linear_with_one_head():
    """Two heads means `alembic upgrade head` is ambiguous and the entrypoint
    would fail on every start."""
    _needs_alembic()
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


# ---- the plan and what it prints --------------------------------------------

def _plan(*kinds, **kw):
    return Plan(pending=[Revision(f"r{i}", Path(f"r{i}.py"), k)
                         for i, k in enumerate(kinds)], **kw)


def test_a_plan_with_any_destructive_revision_blocks():
    assert _plan(ADDITIVE, ADDITIVE).blocked is False
    assert _plan(ADDITIVE, DESTRUCTIVE).blocked is True


def test_an_empty_plan_is_a_no_op():
    assert Plan().nothing_to_do
    assert not Plan(baseline=True).nothing_to_do


def test_the_blocked_message_names_the_command_to_run():
    """This log is the only thing a town's IT contact ever sees of this system,
    and they read it while something is broken."""
    text = "\n".join(format_plan(_plan(DESTRUCTIVE)))
    assert "REFUSING TO START" in text
    assert "alembic upgrade head" in text
    assert "PINPOINT_ALLOW_DESTRUCTIVE_MIGRATION=1" in text


def test_every_pending_revision_is_named_in_the_log():
    text = "\n".join(format_plan(_plan(ADDITIVE, DESTRUCTIVE)))
    assert "r0" in text and "r1" in text
    assert "ADDITIVE" in text and "DESTRUCTIVE" in text


def test_the_baseline_case_says_no_schema_changes_were_made():
    """An operator seeing "baseline" must not think a migration ran."""
    text = "\n".join(format_plan(Plan(baseline=True)))
    assert "baseline" in text.lower()
    assert "no schema changes" in text.lower()


# ---- url handling -----------------------------------------------------------

def test_the_async_url_is_converted_for_alembic():
    """The app runs asyncpg; Alembic is synchronous and cannot use that driver."""
    assert sync_url("postgresql+asyncpg://u:p@db/x") == "postgresql://u:p@db/x"
    assert sync_url("postgresql://u:p@db/x") == "postgresql://u:p@db/x"


# ---- what running this against a real Postgres taught me --------------------
#
# Everything below is a regression guard for a bug that unit tests did not find
# and a live database did, first try.

def test_a_fresh_database_is_never_blocked_by_a_historical_drop():
    """The first live run refused to start on an EMPTY database.

    Replaying the chain from base means the drop_column in the February
    revision drops a column from a table created two revisions earlier in the
    same run, with no rows in it. Nothing can be lost that did not exist a
    second ago -- but the gate saw DESTRUCTIVE and stopped, which would have
    made every first-time install fail.

    The gate protects existing records. An empty database has none.
    """
    assert _plan(DESTRUCTIVE, fresh=True).blocked is False
    assert _plan(DESTRUCTIVE).blocked is True


def test_a_fresh_plan_does_not_replay_the_chain():
    """The chain cannot build a database from scratch: its base revision ALTERs
    `departments` and no revision creates that table. Nothing noticed because
    the schema was always built by create_all. A fresh install builds from the
    models and stamps head instead."""
    _needs_alembic()
    from app.db.migrate import build_plan
    plan = build_plan(current=None, has_tables=False)
    assert plan.fresh
    assert plan.pending == [], "a fresh install must not replay the migration chain"


def test_an_existing_database_with_no_history_is_adopted_not_replayed():
    _needs_alembic()
    from app.db.migrate import build_plan
    plan = build_plan(current=None, has_tables=True)
    assert plan.baseline and not plan.fresh
    assert plan.pending == []


def test_the_fresh_log_does_not_also_claim_to_be_up_to_date():
    """It printed "creating schema from scratch" immediately followed by
    "schema is up to date" -- two lines that cannot both be true."""
    text = " ".join(format_plan(Plan(fresh=True))).lower()
    assert "up to date" not in text
    assert "from the models" in text


def test_the_baseline_log_does_not_report_a_pending_count():
    """"pending: 0 revision(s)" under the baseline line reads as though
    something was checked and skipped rather than adopted."""
    assert not any("pending" in line for line in format_plan(Plan(baseline=True)))


def test_the_override_does_not_print_refusing_to_start():
    """With the override set the migration proceeds, so logging REFUSING TO
    START tells the reader the opposite of what happened."""
    text = "\n".join(format_plan(_plan(DESTRUCTIVE), allow_destructive=True))
    assert "REFUSING TO START" not in text
    assert "applying the destructive" in text


def test_the_fresh_plan_log_omits_the_classification():
    """DESTRUCTIVE printed next to a revision that is about to be applied
    anyway reads as a warning that was ignored."""
    assert "DESTRUCTIVE" not in " ".join(format_plan(_plan(DESTRUCTIVE, fresh=True)))


def test_boot_time_pii_widening_never_undercuts_the_model():
    """init_db's per-boot ALTERs ran saying VARCHAR(200) for phone after the
    model and migration e7f8a9b0c1d2 moved to 500 -- and Postgres allows a
    shrink whenever every stored value happens to fit, which is exactly the
    state right after the widen. So every restart quietly re-broke KMS phone
    writes. The boot statements must state sizes at least as large as the
    model's, or they are a tug of war the boot always wins."""
    import re

    init = Path("app/db/init_db.py").read_text()
    model = Path("app/models.py").read_text()
    stated = dict(re.findall(
        r"ALTER TABLE service_requests ALTER COLUMN (\w+) TYPE VARCHAR\((\d+)\)", init))
    for column in ("first_name", "last_name", "email", "phone"):
        declared = re.search(
            rf'Column\("{column}", String\((\d+)\)', model)
        assert declared, f"could not find the model size for {column}"
        assert column in stated, f"init_db no longer widens {column}; update this test"
        assert int(stated[column]) >= int(declared.group(1)), (
            f"init_db states VARCHAR({stated[column]}) for {column}, smaller than "
            f"the model's String({declared.group(1)}) -- every boot shrinks it back")
