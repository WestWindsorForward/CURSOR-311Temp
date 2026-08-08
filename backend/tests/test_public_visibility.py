"""Tests for resident-chosen public-feed visibility ("unlisted" reports).

The contract:
  * unlisted reports are excluded from every PUBLIC listing/API,
  * they remain reachable by their direct tracking link,
  * they are always fully visible to town staff,
  * existing reports stay public (no silent retroactive hiding).

The listing filter is the load-bearing part — if it is ever dropped, every report
a resident asked to keep unlisted is silently republished. These tests pin it.
"""

import pytest

pytest.importorskip("sqlalchemy")
open311 = pytest.importorskip("app.api.open311")

from sqlalchemy import select

from app.models import ServiceRequest


def _public_sql() -> str:
    from app.services.public_visibility import publicly_listed_conditions
    q = select(ServiceRequest).where(*publicly_listed_conditions())
    return str(q.compile(compile_kwargs={"literal_binds": True})).lower()


# ---- the listing filter ----------------------------------------------------

def test_public_listing_filters_out_unlisted():
    assert "is_public" in _public_sql()


def test_public_listing_still_excludes_deleted():
    assert "deleted_at" in _public_sql()


def test_public_listing_requires_is_public_true():
    sql = _public_sql()
    # Must assert TRUE specifically — `IS NOT NULL` or similar would let
    # unlisted rows straight back into the feed.
    assert "is_public is true" in sql or "is_public = true" in sql or "is_public = 1" in sql


# ---- model defaults --------------------------------------------------------

def test_new_requests_default_to_public():
    """Opting out must be a deliberate choice, not the default."""
    assert ServiceRequest.__table__.c.is_public.default.arg is True


def test_is_public_is_not_nullable():
    """NULL would be ambiguous — every row must be definitively public or not."""
    assert ServiceRequest.__table__.c.is_public.nullable is False


def test_existing_rows_backfill_to_public():
    """The server_default keeps pre-existing reports visible after migration
    instead of silently hiding the town's entire history."""
    assert "true" in str(ServiceRequest.__table__.c.is_public.server_default.arg).lower()


def test_is_public_is_indexed():
    """The public feed filters on this column on every uncached request."""
    assert ServiceRequest.__table__.c.is_public.index is True


# ---- schema plumbing -------------------------------------------------------

def test_create_schemas_accept_the_choice_and_default_public():
    from app.schemas import ServiceRequestCreate, ManualIntakeCreate

    resident = ServiceRequestCreate(
        service_code="pothole", description="A large pothole here", email="a@b.com"
    )
    assert resident.is_public is True
    assert ServiceRequestCreate(
        service_code="pothole", description="A large pothole here",
        email="a@b.com", is_public=False,
    ).is_public is False

    # Staff taking a report by phone can honor the same request.
    assert ManualIntakeCreate(service_code="pothole", description="pothole").is_public is True
    assert ManualIntakeCreate(
        service_code="pothole", description="pothole", is_public=False
    ).is_public is False


# ---- admin module gate -----------------------------------------------------

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSettings:
    def __init__(self, modules):
        self.modules = modules


class _FakeDB:
    """Returns one SystemSettings row, or raises to simulate a read failure."""

    def __init__(self, modules=None, boom=False):
        self._modules = modules
        self._boom = boom

    async def execute(self, _q):
        if self._boom:
            raise RuntimeError("db down")
        return _FakeResult(None if self._modules is None else _FakeSettings(self._modules))


@pytest.mark.asyncio
async def test_opt_out_honored_when_module_enabled():
    assert await open311.resolve_is_public(
        _FakeDB({"unlisted_reports": True}), False
    ) is False


@pytest.mark.asyncio
async def test_opt_out_ignored_when_module_disabled():
    """A client POSTing is_public=false must not hide a report in a town that
    never turned the feature on."""
    assert await open311.resolve_is_public(
        _FakeDB({"unlisted_reports": False}), False
    ) is True


@pytest.mark.asyncio
async def test_legacy_key_name_still_honored():
    """The module key was renamed private_reports -> unlisted_reports. A town
    that had already turned it on must not silently lose the setting."""
    assert await open311.resolve_is_public(
        _FakeDB({"private_reports": True}), False
    ) is False


@pytest.mark.asyncio
async def test_opt_out_ignored_when_module_key_absent():
    assert await open311.resolve_is_public(_FakeDB({"ai_analysis": True}), False) is True


@pytest.mark.asyncio
async def test_opt_out_ignored_when_no_settings_row():
    assert await open311.resolve_is_public(_FakeDB(None), False) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [True, None])
async def test_absent_or_true_choice_is_always_public(requested):
    """No settings read is even needed — a failing DB would still return True."""
    assert await open311.resolve_is_public(_FakeDB(boom=True), requested) is True


@pytest.mark.asyncio
async def test_settings_read_failure_fails_public():
    """Better to publish than to silently hide a report on a transient error;
    the resident sees the outcome on their tracking page either way."""
    assert await open311.resolve_is_public(_FakeDB(boom=True), False) is True


def test_module_default_is_off():
    """Towns opt in to unlisted reports; they don't get it silently."""
    from app.models import SystemSettings

    assert SystemSettings.__table__.c.modules.default.arg["unlisted_reports"] is False


def test_response_schema_exposes_visibility_to_staff():
    """Staff need to see that a report is unlisted so they don't discuss it
    publicly; a legacy row with NULL reads as public."""
    from app.schemas import ServiceRequestResponse

    assert "is_public" in ServiceRequestResponse.model_fields
