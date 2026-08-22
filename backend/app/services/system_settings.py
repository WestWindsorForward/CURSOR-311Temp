"""One row, and the same row every time.

`system_settings` is a singleton by convention and by nothing else. Seven code
paths create it with a non-atomic "read, and insert if missing", and twenty-odd
read it back with `select(SystemSettings).limit(1)` and no ORDER BY.

Both halves of that are a problem, and together they produce a bug that looks
like a UI fault. `LIMIT 1` without `ORDER BY` gives PostgreSQL permission to
return whichever row is cheapest to reach, and an UPDATE physically relocates a
row in the heap. So on a table that has ever ended up with two rows, a save can
write row 1 and the read immediately afterwards can return row 2 -- the setting
goes into the database, the page reloads, and the old value comes back. It is
intermittent, it is invisible in the logs, and it looks exactly like "the state
I choose for the retention policy isn't persisting".

It is not a retention bug. Retention is just where it was noticed. The same
read is used for the municipal boundary, the road-data state, the AI model
cache, backup configuration and the public origin -- and, worse than any of
those, by the nightly retention task itself, which means the policy actually
enforced at 1am can differ from the one displayed on the compliance page.

Two fixes, because either alone leaves the door open. This module makes every
read deterministic, and the migration alongside it collapses any duplicates
that already exist and adds a constraint so a second row cannot be created
again.
"""

from typing import Any, Iterable, List, Optional, Sequence, TypeVar

Row = TypeVar("Row")


def pick_canonical(rows: Sequence[Row]) -> Optional[Row]:
    """The one true settings row: the oldest, by primary key.

    Lowest id rather than newest, deliberately. The first row is the one the
    deployment has been reading for its whole life, so it holds the values a
    town actually configured; a duplicate created later by a racing request
    holds mostly defaults. Picking the newest would silently swap a configured
    row for an empty one.
    """
    if not rows:
        return None
    return min(rows, key=lambda r: (getattr(r, "id", None) is None, getattr(r, "id", 0)))


def merge_into_canonical(rows: Sequence[Row], columns: Iterable[str]) -> tuple[Optional[Row], List[Row]]:
    """Fold duplicates into the canonical row, and say which are now spare.

    A value set on a duplicate is still a value somebody typed into this
    product, so it is carried over wherever the canonical row has nothing --
    never over the top of an existing answer. Deleting the duplicates without
    this would quietly discard configuration.

    Returns (canonical, duplicates-to-delete).
    """
    canonical = pick_canonical(rows)
    if canonical is None:
        return None, []
    others = [r for r in rows if r is not canonical]
    # Oldest first, so that when two duplicates both have a value the earlier
    # one wins -- the same tie-break as picking the canonical row itself.
    for other in sorted(others, key=lambda r: (getattr(r, "id", None) is None, getattr(r, "id", 0))):
        for column in columns:
            if getattr(canonical, column, None) is None:
                value = getattr(other, column, None)
                if value is not None:
                    setattr(canonical, column, value)
    return canonical, others


async def get_settings(
    db: Any, *, create: bool = False, fresh: bool = False
) -> Optional[Any]:
    """Read the settings row deterministically.

    Ordered by id, so this returns the same row on every call regardless of how
    many exist or what the planner feels like doing. `create=True` inserts one
    when the table is empty, for the write paths.

    `fresh=True` re-reads the row's columns from the database instead of
    accepting the copy this session already has. The default is off because
    almost every caller wants the cheap read, but it is not merely an
    optimisation to skip: the SELECT below runs either way, and without
    `populate_existing` the ORM discards the result and returns the identity
    map's instance with whatever values it was first loaded with. A caller that
    re-reads specifically to notice somebody else's committed change -- see
    `host_secrets._store`, which re-reads mid-push to catch a town admin taking
    a key back -- gets a stale answer and silently reverts them. Anything
    checking for a concurrent write needs this flag.
    """
    from sqlalchemy import select

    from app.models import SystemSettings

    query = select(SystemSettings).order_by(SystemSettings.id).limit(1)
    if fresh:
        query = query.execution_options(populate_existing=True)
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None and create:
        row = SystemSettings()
        db.add(row)
        await db.flush()
    return row
