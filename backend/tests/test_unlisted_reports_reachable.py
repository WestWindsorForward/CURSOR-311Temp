"""An unlisted report must still be reachable by its own link.

"Unlisted" means kept out of the town's public listing. It does not mean hidden
from the person who filed it: they have the link, staff have the link, and the
report is still worked. The listing rule and the by-id rule are therefore
different, and confusing them breaks things in opposite directions --

  * `is_public` missing from the listing rule republishes every report a
    resident asked to keep unlisted;
  * `is_public` added to the by-id rule 404s every tracking link for those same
    reports, silently, and the resident concludes their report was deleted.

Both rules are now named functions, and these tests compile the SQL they
produce rather than reading the source, so a change to how the endpoints are
written cannot make the test pass or fail on its own.
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import select

from app.api.open311 import direct_link_filters
from app.models import ServiceRequest
from app.services.public_visibility import publicly_listed_conditions


def _sql(filters):
    return str(select(ServiceRequest.id).where(*filters).compile(
        compile_kwargs={"literal_binds": True}))


def test_the_public_listing_hides_unlisted_reports():
    assert "is_public" in _sql(publicly_listed_conditions())


def test_a_direct_link_does_not_hide_them():
    """The bug this file exists for. Adding is_public here would look like
    tightening security and would break every tracking link for every unlisted
    report."""
    assert "is_public" not in _sql(direct_link_filters())


def test_both_rules_exclude_soft_deleted_records():
    """Two of the four by-id endpoints were missing this, so a deleted request
    still served its comments."""
    assert "deleted_at IS NULL" in _sql(publicly_listed_conditions())
    assert "deleted_at IS NULL" in _sql(direct_link_filters())


def test_the_direct_rule_is_only_the_soft_delete_clause():
    """Anything else added here narrows who can follow a link, which is the
    failure mode. Stated as an equality so a new clause has to be argued for."""
    sql = _sql(direct_link_filters())
    assert sql.count("WHERE") == 1
    assert sql.split("WHERE")[1].strip() == "service_requests.deleted_at IS NULL"


def test_every_by_id_endpoint_uses_the_shared_rule():
    """The rule was repeated inline at each endpoint, which is how two of them
    came to disagree with the other two."""
    import inspect

    from app.api import open311

    for name in ("get_public_request_detail", "get_public_comments",
                 "get_public_audit_log", "lookup_request_by_token"):
        fn = getattr(open311, name)
        source = inspect.getsource(fn)
        assert "direct_link_filters()" in source, name
        assert "is_public" not in source, f"{name} filters is_public"
