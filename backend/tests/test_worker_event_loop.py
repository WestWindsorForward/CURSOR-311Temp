"""The worker's database connections must not outlive the task that made them.

Every Celery task in this codebase is a synchronous function that bridges into
asyncio with `asyncio.run` -- `app.tasks.service_requests.run_async`,
`app.tasks.storage._run`, and bare `asyncio.run` in `road_data`,
`connector_checks`, `oauth_service` and `notifications`. Each of those makes a
new event loop and closes it when the task ends.

A pooled asyncpg connection does not know that. It stays in the shared pool
holding a reference to the closed loop, and the next task that touches the pool
gets one of two failures, both of which were in production:

    RuntimeError: Event loop is closed
      ... sqlalchemy/pool/base.py, _close_connection
      ... asyncpg/connection.py, close -> self._loop.create_task(...)

    Task ... got Future ... attached to a different loop
      (app.tasks.storage.vault_secrets, 2026-08-11 01:37 UTC)

The fix is structural rather than per-call-site: the worker's engine pools
nothing, so a connection is opened and closed inside one loop and there is no
shared state to contaminate. Call sites that remember to dispose are then a
belt, not the braces -- the point being that half of them never remembered.

These tests pin the arrangement, not the traceback: reproducing the traceback
needs a live Postgres and two sequential loops, which CI does not have.
"""

import pytest

# Guards on submodules, not top-level names: `backend/app` is a real package
# here, so a bare importorskip("app...") would resolve a namespace package and
# skip nothing. CI installs neither sqlalchemy's async stack nor asyncpg.
pytest.importorskip("sqlalchemy.ext.asyncio")
pytest.importorskip("asyncpg.connection")
pytest.importorskip("app.core.config")


@pytest.fixture
def session_module():
    """The db session module, with its module-level engine restored after."""
    from app.db import session as mod

    original = mod.engine
    try:
        yield mod
    finally:
        mod.engine = original
        mod.SessionLocal.configure(bind=original)


def test_the_worker_engine_pools_nothing(session_module):
    """The whole fix. A NullPool opens a connection when a session asks for one
    and closes it when the session ends -- always inside the loop that created
    it -- so nothing survives `asyncio.run` returning."""
    from sqlalchemy.pool import NullPool

    assert not isinstance(session_module.engine.pool, NullPool), (
        "the API engine should still pool; it has one loop for the life of the process"
    )

    session_module.use_null_pool()

    assert isinstance(session_module.engine.pool, NullPool)


def test_sessions_made_after_the_swap_use_the_new_engine(session_module):
    """`SessionLocal` is imported by name at module import time all over
    app/tasks/, so those modules keep whatever object existed at import. If the
    swap replaced the sessionmaker instead of re-binding it, every task would
    carry on using the pooled engine and nothing would have changed."""
    from sqlalchemy.pool import NullPool

    maker_before = session_module.SessionLocal

    session_module.use_null_pool()

    assert session_module.SessionLocal is maker_before
    bind = session_module.SessionLocal.kw["bind"]
    assert bind is session_module.engine
    assert isinstance(bind.pool, NullPool)


def test_swapping_twice_is_harmless(session_module):
    """Both worker signals are connected, and with a prefork pool both fire."""
    from sqlalchemy.pool import NullPool

    session_module.use_null_pool()
    engine_after_first = session_module.engine
    session_module.use_null_pool()

    assert session_module.engine is engine_after_first
    assert isinstance(session_module.engine.pool, NullPool)


def test_celery_swaps_the_pool_at_worker_start_up(session_module):
    """A correct engine nobody installs is not a fix. The worker has to reach
    it before it runs its first task, whichever pool implementation it uses."""
    pytest.importorskip("celery.signals")
    from sqlalchemy.pool import NullPool

    from app.core import celery_app as mod
    from celery.signals import worker_init, worker_process_init

    # Forked children see worker_process_init; --pool=solo only ever sees
    # worker_init. Neither alone covers both.
    assert worker_process_init.has_listeners()
    assert worker_init.has_listeners()

    mod._use_null_pool_in_worker()
    assert isinstance(session_module.engine.pool, NullPool)


def test_the_api_process_is_left_alone():
    """Importing the Celery app is not the same as being a worker: the API
    imports it to publish tasks, and its pooled engine is correct -- one loop
    for the life of the process, and a connect() per request would be waste."""
    pytest.importorskip("celery.signals")
    from sqlalchemy.pool import NullPool

    import importlib

    from app.db import session as mod

    importlib.import_module("app.core.celery_app")

    assert not isinstance(mod.engine.pool, NullPool)
