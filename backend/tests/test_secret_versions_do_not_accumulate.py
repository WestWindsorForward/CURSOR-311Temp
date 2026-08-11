"""Google bills per active secret version, and every write adds one.

Nothing retired the old ones. On the deployment this was written against there
were 166 active versions across five bundles -- about $9.96/month -- of which
exactly five were ever read: the `latest` of each. The other 161 were superseded
copies nobody could reach.

Pruning by hand fixes the bill once. These are about the write path doing it
every time, so it cannot come back on the next deployment.
"""

import pytest

pytest.importorskip("cryptography")

from app.services import secret_manager as sm


class _Version:
    def __init__(self, number, state="ENABLED"):
        self.name = f"projects/p/secrets/secret-x/versions/{number}"
        self.state = type("S", (), {"name": state})()


class _Client:
    """Enough of the Secret Manager client to watch what gets destroyed."""

    def __init__(self, numbers, latest=None, enabled_only=True):
        self._versions = [_Version(n) for n in numbers] if enabled_only else list(numbers)
        self._latest = latest if latest is not None else max(numbers)
        self.destroyed = []

    def list_secret_versions(self, request):
        return list(self._versions)

    def access_secret_version(self, request):
        return type("R", (), {"name": f"projects/p/secrets/secret-x/versions/{self._latest}"})()

    def destroy_secret_version(self, request):
        self.destroyed.append(int(request["name"].rsplit("/", 1)[-1]))


PATH = "projects/p/secrets/secret-x"


def _needs_google():
    """`_prune_versions` imports google.api_core for the two exception types it
    treats as success. With the client library absent that import fails inside
    the function's own try/except, which logs and returns 0 -- so every test
    below would pass while destroying nothing and proving nothing.

    Guard on the submodule: google-* packages install into a `google/` namespace
    package, so a bare importorskip("google") can succeed with none of the
    libraries present.
    """
    pytest.importorskip("google.api_core")


def test_it_keeps_the_newest_three_and_destroys_the_rest():
    _needs_google()
    client = _Client(range(1, 44))          # v1..v43, like the real bundle
    sm._prune_versions(client, PATH)
    assert sorted(client.destroyed) == list(range(1, 41))
    assert 43 not in client.destroyed and 42 not in client.destroyed and 41 not in client.destroyed


def test_it_never_destroys_what_latest_points_to():
    """The one mistake here costs a live credential, so it is checked explicitly
    rather than inferred from the ordering."""
    _needs_google()
    client = _Client(range(1, 10), latest=4)
    sm._prune_versions(client, PATH)
    assert 4 not in client.destroyed


def test_nothing_to_do_when_there_are_only_a_few():
    _needs_google()
    client = _Client([1, 2, 3])
    assert sm._prune_versions(client, PATH) == 0
    assert client.destroyed == []


def test_a_version_already_gone_is_not_an_error():
    """Two writers pruning the same bundle is expected, not a failure."""
    _needs_google()
    from google.api_core import exceptions as gexc

    client = _Client(range(1, 10))

    def _boom(request):
        raise gexc.FailedPrecondition("already destroyed")

    client.destroy_secret_version = _boom
    assert sm._prune_versions(client, PATH) == 0     # swallowed, no raise


def test_pruning_failure_never_fails_the_write():
    """The credential is already stored by the time this runs. Reporting failure
    because cleanup stumbled would turn a billing tidy-up into a lost secret."""
    _needs_google()

    class _Broken:
        def list_secret_versions(self, request):
            raise RuntimeError("permission denied")

    assert sm._prune_versions(_Broken(), PATH) == 0


def test_how_many_to_keep_is_configurable_but_never_zero():
    import os

    original = os.environ.get("SECRET_KEEP_VERSIONS")
    try:
        os.environ["SECRET_KEEP_VERSIONS"] = "0"
        assert sm._keep_versions() >= 1, "keeping zero would destroy the live version"
        os.environ["SECRET_KEEP_VERSIONS"] = "5"
        assert sm._keep_versions() == 5
        os.environ["SECRET_KEEP_VERSIONS"] = "nonsense"
        assert sm._keep_versions() == 3
    finally:
        os.environ.pop("SECRET_KEEP_VERSIONS", None)
        if original is not None:
            os.environ["SECRET_KEEP_VERSIONS"] = original


# --- cache invalidation -----------------------------------------------------

def test_clearing_one_bundle_leaves_the_others_alone():
    """Saving a Maps key used to throw away the cached auth, smtp, sms and config
    bundles too, so the next few requests refetched every one of them."""
    sm._cache_put("secret-google", {"GOOGLE_MAPS_API_KEY": "x"})
    sm._cache_put("secret-smtp", {"SMTP_HOST": "y"})
    sm.clear_cache(key_name="GOOGLE_MAPS_API_KEY")
    assert sm._cache_get("secret-google") is None
    assert sm._cache_get("secret-smtp") == {"SMTP_HOST": "y"}


def test_clearing_everything_is_still_possible():
    sm._cache_put("secret-google", {"a": "1"})
    sm._cache_put("secret-smtp", {"b": "2"})
    sm.clear_cache()
    assert sm._cache_get("secret-google") is None
    assert sm._cache_get("secret-smtp") is None


def test_a_key_maps_to_its_own_bundle():
    """`clear_cache(key_name=...)` is the form callers want: they know which
    secret they wrote, not which bundle it belongs to."""
    assert sm._get_bundle_name("GOOGLE_MAPS_API_KEY") == sm._get_bundle_name("VERTEX_AI_PROJECT")
    assert sm._get_bundle_name("SMTP_HOST") != sm._get_bundle_name("GOOGLE_MAPS_API_KEY")
