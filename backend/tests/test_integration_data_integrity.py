"""Schema, sync windows and vault hygiene for the govtech integrations.

Five separate ways a connection that looked configured lost data quietly:

  * `documents_pushed_count` was on the model and in init_db's ad-hoc ALTER list
    but in no migration, so an Alembic-only deployment raised UndefinedColumn on
    the first photo attached to a work order -- caught and logged by the push
    path, so the photo simply never arrived;
  * `platform` was indexed but not uniquely, against a SELECT-then-INSERT create
    endpoint, so two concurrent connects meant every report pushed twice;
  * `last_sync_at` was stamped after the fetch, so anything the vendor changed
    during it fell in a window the next poll started after;
  * the Accela pull sent a date without a time and no offset loop, refetching the
    whole day every fifteen minutes and dropping the 101st changed record;
  * disconnecting an integration deleted the row and left the vendor's client
    secret in the town's vault.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import app.integrations.base as base
from app.integrations.registry import build_connector

BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def tasks():
    """The sync tasks module. Needs the ORM and Celery, which CI does not have."""
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("celery.app")
    import app.tasks.integrations as module
    return module


def pull_task_source() -> str:
    source = (BACKEND / "app/tasks/integrations.py").read_text()
    block = source[source.index("def pull_integration_updates"):]
    return block[:block.index("@celery_app.task", 1)]


def code_only(source: str) -> str:
    """Statements without comments, so an assertion about what the code does is
    not satisfied -- or defeated -- by a comment saying it."""
    return "\n".join(line.split("#")[0] for line in source.splitlines())


def migrations() -> str:
    return "\n".join(p.read_text() for p in (BACKEND / "alembic/versions").glob("*.py"))


# ---------------------------------------------------------------------------
# 1. The column the migration chain never created
# ---------------------------------------------------------------------------

def test_documents_pushed_count_is_in_a_migration_not_only_in_init_db():
    """init_db's ALTER list runs on create_all deployments. One that migrates
    never sees it, and the first document push there raised UndefinedColumn."""
    assert "documents_pushed_count" in migrations()


def test_the_model_and_the_migrations_agree_on_the_integration_link_columns():
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("geoalchemy2.types")   # app.models declares Geometry columns
    from app.models import IntegrationLink

    columns = {c.name for c in IntegrationLink.__table__.columns}
    assert "documents_pushed_count" in columns
    # Written twice by the push path and read nowhere; dropped in a7029676a2bc.
    assert "documents_pushed" not in columns


def test_nothing_writes_the_dropped_flag_any_more():
    source = (BACKEND / "app/tasks/integrations.py").read_text()
    assert "documents_pushed " not in source.replace("documents_pushed_count", "")
    assert ".documents_pushed =" not in source


def test_the_column_drop_is_its_own_revision():
    """A drop_column is classified DESTRUCTIVE and halts the container until an
    operator allows it. Bundling it with the documents_pushed_count fix would
    have held an urgent correctness change behind that manual step."""
    adds = [p for p in (BACKEND / "alembic/versions").glob("*.py")
            if '"documents_pushed_count"' in p.read_text()]
    drops = [p for p in (BACKEND / "alembic/versions").glob("*.py")
             if 'op.drop_column("integration_links", "documents_pushed")' in p.read_text()]
    assert len(adds) == 1 and len(drops) == 1
    assert adds[0] != drops[0], "the additive fix is gated behind the destructive tidy-up"


# ---------------------------------------------------------------------------
# 2. One integration per platform
# ---------------------------------------------------------------------------

def test_the_platform_index_is_unique():
    """Two enabled rows for one vendor means every resident report is pushed
    there twice, as two records, with two work orders opened against them."""
    source = next(p.read_text() for p in (BACKEND / "alembic/versions").glob("*.py")
                  if "ix_integration_configs_platform" in p.read_text()
                  and "unique=True" in p.read_text())
    assert 'op.create_index(\n        op.f("ix_integration_configs_platform")' in source \
        or "unique=True" in source


def test_init_db_also_creates_it_unique():
    """The two schema paths have to agree, or a create_all deployment keeps the
    race that the migrating one just fixed."""
    source = (BACKEND / "app/db/init_db.py").read_text()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ix_integration_configs_platform" in source


def test_the_create_endpoint_answers_a_lost_race_with_the_same_409():
    """The SELECT is not a lock. When the unique index rejects the second
    insert, an admin should see what the SELECT would have told them, not a 500."""
    source = (BACKEND / "app/api/integrations.py").read_text()
    block = source[source.index("async def create_integration"):]
    block = block[:block.index("@router.put")]
    assert "IntegrityError" in block
    assert "status_code=409" in block
    assert "await db.rollback()" in block


# ---------------------------------------------------------------------------
# 3. The pull window
# ---------------------------------------------------------------------------

def test_the_poll_window_starts_before_the_fetch_not_after_it():
    block = code_only(pull_task_source())
    # Stamped from a clock read before the connector call, not at the end of it.
    assert "started_at = datetime.now(timezone.utc)" in block
    assert "integration.last_sync_at = started_at" in block
    assert "integration.last_sync_at = datetime.now" not in block


def test_the_poll_overlaps_the_previous_window():
    """Vendor timestamps are written from the vendor's clock at edit time and
    become queryable later, so an exact boundary drops records whose stamp falls
    before it but which only appeared after."""
    module = tasks()

    class Row:
        last_sync_at = NOW

    assert module._pull_since(Row()) == NOW - module.PULL_OVERLAP
    assert timedelta(minutes=1) <= module.PULL_OVERLAP <= timedelta(hours=1)


def test_a_first_ever_poll_asks_for_everything():
    class Row:
        last_sync_at = None

    assert tasks()._pull_since(Row()) is None


def test_a_naive_stored_timestamp_does_not_explode_the_beat():
    class Row:
        last_sync_at = NOW.replace(tzinfo=None)

    assert tasks()._pull_since(Row()).tzinfo is not None


def test_a_failed_poll_does_not_step_over_the_window_it_failed_on():
    """Advancing the watermark after a failure permanently loses every change
    made during the failed interval -- and nothing anywhere would say so."""
    block = pull_task_source()
    handler = code_only(block[block.index("except Exception as e:"):])
    assert "last_sync_at" not in handler, "the error path moves the watermark"
    assert 'last_sync_status="error"' in handler


def test_the_newest_change_is_reported_rather_than_used_as_the_watermark():
    """A vendor clock running fast would push the watermark into the future and
    skip everything between now and then -- a worse and quieter failure."""
    _newest_change = tasks()._newest_change

    class Record:
        def __init__(self, updated_at):
            self.updated_at = updated_at

    assert _newest_change([]) is None
    assert _newest_change([Record(None)]) is None
    assert _newest_change([Record(NOW), Record(NOW - timedelta(hours=2))]) == NOW
    # Naive vendor stamps are normalised rather than crashing the comparison.
    assert _newest_change([Record(NOW.replace(tzinfo=None)), Record(NOW)]) == NOW


# ---------------------------------------------------------------------------
# 4. The Accela pull
# ---------------------------------------------------------------------------

@pytest.fixture
def accela(monkeypatch):
    monkeypatch.setattr(base, "_assert_public_url", lambda url: None)

    def serve(pages):
        seen = []

        async def transport(self, request):
            if "oauth2/token" in str(request.url):
                return httpx.Response(200, json={"access_token": "t"}, request=request)
            seen.append(request)
            offset = int(dict(request.url.params).get("offset", 0))
            return httpx.Response(200, json={"result": pages.get(offset, [])},
                                  request=request)

        monkeypatch.setattr(base.httpx.AsyncHTTPTransport,
                            "handle_async_request", transport)
        return seen

    return serve


def connector():
    return build_connector(
        "accela", {"agency_name": "SPRINGFIELD", "record_type": "SR/G/C/NA"},
        {"client_id": "i", "client_secret": "s", "username": "u", "password": "p"})


@pytest.mark.asyncio
async def test_the_accela_pull_pages_past_the_first_hundred(accela):
    """It sent limit=100 with no offset loop, so a busy agency's 101st changed
    record was dropped -- and the sync log said "100 record(s) fetched", which
    reads like a complete answer."""
    accela({
        0: [{"id": f"R{i}"} for i in range(100)],
        100: [{"id": f"R{i}"} for i in range(100, 150)],
    })
    records = await connector().pull_updates(since=NOW)
    assert len(records) == 150


@pytest.mark.asyncio
async def test_the_accela_pull_stops_on_a_short_page(accela):
    seen = accela({0: [{"id": "R1"}]})
    await connector().pull_updates(since=NOW)
    assert len(seen) == 1, "kept asking after a page that was not full"


@pytest.mark.asyncio
async def test_a_record_shifting_between_pages_is_not_counted_twice(accela):
    """Paginating a feed that is being edited under you repeats records. Applying
    one twice is harmless; counting it twice makes the sync log lie."""
    accela({
        0: [{"id": f"R{i}"} for i in range(100)],
        100: [{"id": "R99"}, {"id": "R100"}],
    })
    records = await connector().pull_updates(since=NOW)
    assert len(records) == 101
    assert len({r.external_id for r in records}) == 101


@pytest.mark.asyncio
async def test_the_accela_pull_asks_for_a_time_not_a_whole_day(accela):
    """Date-only granularity meant a fifteen-minute poll re-fetched the entire
    day, every run, all day."""
    seen = accela({0: []})
    await connector().pull_updates(since=NOW)
    sent = dict(seen[0].url.params)["updateDateFrom"]
    assert sent == "2026-08-05 12:00:00", sent


@pytest.mark.asyncio
async def test_the_accela_window_is_sent_in_utc(accela):
    """The stored watermark is UTC; sending it in another zone would shift the
    window by hours in whichever direction loses records."""
    seen = accela({0: []})
    eastern = NOW.astimezone(timezone(timedelta(hours=-4)))
    await connector().pull_updates(since=eastern)
    assert dict(seen[0].url.params)["updateDateFrom"] == "2026-08-05 12:00:00"


# ---------------------------------------------------------------------------
# 5. Vault hygiene
# ---------------------------------------------------------------------------

def test_disconnecting_removes_the_vendor_credentials_from_the_vault():
    """Otherwise a client secret an admin believes they revoked by pressing
    Disconnect stays live, and unlisted -- nothing in the UI mentions it."""
    source = (BACKEND / "app/api/integrations.py").read_text()
    block = source[source.index("async def delete_integration"):]
    assert "_forget_vault_secrets" in block
    # Read before the delete, or the only record of which entries belonged to
    # this integration is gone with the row.
    assert block.index("integration.credentials") < block.index("await db.delete")


def test_the_secret_store_can_actually_delete():
    """`store_credentials` had no inverse anywhere, which is why disconnect
    could not clean up even in principle."""
    import app.services.secret_manager as sm

    assert callable(sm.delete_secret)
    assert callable(sm.delete_secret_sync)


def test_deleting_clears_the_database_copy_too():
    """A town that migrated to an external vault may still hold an encrypted
    copy from before the migration, which the get_secret fallback would read."""
    source = (BACKEND / "app/services/secret_manager.py").read_text()
    block = source[source.index("async def delete_secret("):]
    block = block[:block.index("async def _delete_secret_from_db")]
    assert "_delete_secret_from_db" in block
    assert "delete_secret_sync" in block


@pytest.mark.parametrize("field_list,key", [
    ("credential_fields", "client_id"),
    ("config_fields", "agency_name"),
])
def test_declared_fields_are_accepted(field_list, key):
    pytest.importorskip("fastapi.routing")
    from app.api.integrations import _allowed_keys, _reject_unknown_keys

    assert key in _allowed_keys("accela", field_list)
    payload = {key: "x"}
    if field_list == "credential_fields":
        _reject_unknown_keys("accela", payload, None)
    else:
        _reject_unknown_keys("accela", None, payload)


def test_an_undeclared_credential_field_cannot_name_a_vault_key():
    """`credentials` keys become Secret Manager key names
    (INTEGRATION_<PLATFORM>_<FIELD>), so an unvalidated dict let an admin write
    arbitrary entries into the namespace the platform itself reads from."""
    pytest.importorskip("fastapi.routing")
    from fastapi import HTTPException

    from app.api.integrations import _reject_unknown_keys

    with pytest.raises(HTTPException) as caught:
        _reject_unknown_keys("accela", {"SECRET_KEY": "hijack"}, None)
    assert caught.value.status_code == 400
    assert "SECRET_KEY" in caught.value.detail


def test_an_undeclared_setting_is_refused_rather_than_silently_ignored():
    """Config is a JSON blob the connectors read by key, so an unrecognised key
    is a setting the admin believes they set and nothing will ever read."""
    pytest.importorskip("fastapi.routing")
    from fastapi import HTTPException

    from app.api.integrations import _reject_unknown_keys

    with pytest.raises(HTTPException):
        _reject_unknown_keys("accela", None, {"agancy_name": "typo"})


def test_the_per_vendor_mapping_keys_the_connectors_read_are_still_accepted():
    """These are real settings an integrator sets deliberately; they are just
    not wizard fields. Rejecting them would break a working install."""
    pytest.importorskip("fastapi.routing")
    from app.api.integrations import _reject_unknown_keys

    _reject_unknown_keys("generic_rest", None, {
        "share_pii": True, "field_map": {"description": "desc"},
        "comments_path": "/tickets/{id}/notes", "max_pull_pages": 5,
    })


def test_vaulted_is_all_or_nothing_not_any():
    """A vault write that failed for one field falls back to keeping that value
    encrypted in this database. `any` reported the whole set as vaulted on the
    strength of the fields that succeeded -- a trust signal rounding up."""
    pytest.importorskip("fastapi.routing")
    from app.api.integrations import _vaulted_state

    assert _vaulted_state({}) == "none"
    assert _vaulted_state({"a": "@secret:A", "b": "@secret:B"}) == "all"
    assert _vaulted_state({"a": "@secret:A", "b": "raw-value"}) == "partial"
    assert _vaulted_state({"a": "raw-value"}) == "none"


def test_a_decrypt_failure_is_logged_with_the_id_and_never_the_ciphertext():
    """A rotated SECRET_KEY makes every integration's credentials unreadable at
    once. Returning a bare {} presented that as "someone deleted the
    credentials", and the advice on screen became "fill them in again" -- which
    overwrites the vault references and makes it permanent."""
    source = (BACKEND / "app/models.py").read_text()
    block = source[source.index("def credentials(self):"):]
    block = block[:block.index("@credentials.setter")]
    assert ".error(" in block
    assert "SECRET_KEY" in block
    assert "self.id" in block
    assert "_credentials_encrypted" not in block.split("except")[1]
