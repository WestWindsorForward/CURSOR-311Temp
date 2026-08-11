import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from app.core.config import get_settings

settings = get_settings()


def _make_async_engine(**kwargs):
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,
        **kwargs,
    )


# Async engine for FastAPI endpoints. One long-lived event loop, so a pooled
# connection is still bound to a live loop the next time it is handed out.
engine = _make_async_engine(pool_size=10, max_overflow=20)

# Sync engine for non-async contexts (encryption, health checks)
# Convert asyncpg URL to psycopg2
sync_db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
sync_engine = create_engine(
    sync_db_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()


def use_null_pool() -> None:
    """Give this process an engine that pools nothing. Called in Celery workers.

    A worker task is a synchronous function that bridges into asyncio with
    `asyncio.run` (see `app.tasks.service_requests.run_async`), so every task
    gets a *fresh event loop* which is closed when the task ends. A pooled
    asyncpg connection outlives that loop: it stays in the pool holding a
    reference to a loop that no longer runs, and the next task's pool teardown
    calls `connection.close()` on it -- which lands in
    `loop.create_task`/`call_soon` on a closed loop and raises

        RuntimeError: Event loop is closed

    from inside `sqlalchemy/pool/base.py _close_connection`. Worse than the
    traceback: a task that is *handed* one of those connections fails outright
    with "attached to a different loop", which is what the hourly secret
    vaulting pass was doing in production.

    Individual call sites tried to paper over this by disposing the engine in
    their `finally` block, but that only helps if every one of them remembers
    -- `road_data`, `connector_checks`, `oauth_service` and `notifications` all
    call `asyncio.run` without disposing, and one forgotten site re-poisons the
    pool for everybody.

    NullPool removes the shared state instead of trying to clean it up: a
    connection is opened when a session asks for one and closed when the
    session ends, always inside the loop that created it. The cost is a
    connect() per session -- a few milliseconds against a local Postgres -- on
    a worker that runs a handful of scheduled jobs an hour. The alternative,
    one long-lived loop per worker process, would mean rewriting every
    `asyncio.run` call site and is not worth it at this throughput.

    Safe to call more than once; the signals below may both fire.
    """
    global engine

    if isinstance(engine.pool, NullPool):
        return

    previous = engine
    engine = _make_async_engine(poolclass=NullPool)
    # `SessionLocal` is imported by name all over the task modules, so it has to
    # be re-bound in place rather than replaced.
    SessionLocal.configure(bind=engine)
    # Nothing has been checked out of the old pool yet -- this runs at worker
    # start-up, before any task -- so dropping it is enough, and there is no
    # loop here to await a dispose() in anyway.
    previous.sync_engine.pool.dispose()
    logging.getLogger(__name__).info(
        "[db] worker process using NullPool: connections do not outlive a task's event loop"
    )


async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create any table the migrations did not, and complain if that happens.

    This used to be the *only* way the schema was created, which is why upgrades
    silently half-worked: create_all adds missing tables and will not touch an
    existing one, so a new column never appeared. Migrations now run in the
    entrypoint before the API starts (app/db/migrate.py).

    It is kept because it is the belt to the migrations' braces -- and because
    creating anything here now means the two descriptions of the schema have
    drifted apart again. A model was added without a migration. That is worth a
    loud warning rather than a silent fix: the silent fix is what let the drift
    accumulate in the first place, and a developer's laptop is where it should
    be caught, not a town's server.
    """
    from sqlalchemy import inspect

    async with engine.begin() as conn:
        before = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        await conn.run_sync(Base.metadata.create_all)
        after = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))

    created = sorted(after - before)
    if created:
        logging.getLogger(__name__).warning(
            "[schema] created %d table(s) that no migration describes: %s. "
            "Add an Alembic revision for these -- create_all cannot alter an "
            "existing table, so the next change to them will not apply.",
            len(created), ", ".join(created),
        )
