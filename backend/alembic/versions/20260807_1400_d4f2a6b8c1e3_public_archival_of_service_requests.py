"""Let staff take closed reports off the public tracker without deleting them.

A town that has run this for three years has a public map that is mostly a
decade of resolved potholes, and the one open water-main break is somewhere
underneath. The only tool that existed was soft delete, which is the wrong
answer twice: it removes the record from staff too, and it breaks the tracking
link the resident was given.

So two additions, neither of which loses anything:

  * `service_requests.public_archived` -- one report, taken off the public
    listing by staff. Separate from `is_public`, which is the RESIDENT's answer
    to a different question; overwriting theirs would lose it, and would make
    "unarchive" republish a report somebody asked to keep unlisted.

  * `system_settings.public_archive_days` -- the town-wide policy. Closed
    reports older than N days drop off public listings. Evaluated in the WHERE
    clause at read time, never written to a row, so an admin can change or
    clear the number and the listing simply comes back. That is why this
    migration adds a settings column and no backfill: there is nothing to
    backfill, and a job that stamped 40,000 rows would make undo impossible.

Both default to "behaves exactly as today": no report is archived, no policy is
set. Existing towns see no change until somebody chooses one.

Revision ID: d4f2a6b8c1e3
Revises: c3d1e5f7a9b2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f2a6b8c1e3"
down_revision: Union[str, None] = "c3d1e5f7a9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column name, factory). Each guarded independently so a half-applied
# state -- one column added by init_db's bootstrap, the other not -- converges
# rather than erroring on the one that is already there.
COLUMNS = (
    (
        "service_requests",
        "public_archived",
        lambda: sa.Column(
            "public_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    ),
    (
        "system_settings",
        "public_archive_days",
        lambda: sa.Column("public_archive_days", sa.Integer(), nullable=True),
    ),
)

INDEXES = (
    ("ix_service_requests_public_archived", "service_requests", ["public_archived"]),
)


def _columns(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    for table, name, make in COLUMNS:
        existing = _columns(table)
        if not existing:
            continue  # fresh install: created from the models, already complete
        if name not in existing:
            op.add_column(table, make())

    for index_name, table, cols in INDEXES:
        if not _columns(table):
            continue
        if index_name not in _indexes(table):
            op.create_index(index_name, table, cols)


def downgrade() -> None:
    for index_name, table, _cols in INDEXES:
        if index_name in _indexes(table):
            op.drop_index(index_name, table_name=table)

    for table, name, _make in reversed(COLUMNS):
        if name in _columns(table):
            op.drop_column(table, name)
