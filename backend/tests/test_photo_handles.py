"""Redeeming photos that were screened before the report existed.

The portal screens a photo when the resident picks it and holds a handle until
Submit. That moves a Google Vision round trip off the Submit button, and it
introduces a small trust boundary: an unauthenticated caller hands us a string
and we hand back somebody's photo. These tests are mostly about that boundary
and about the two ways it can go wrong quietly.

  * A handle that no longer resolves must be *reported*, not swallowed. The
    server never kept the original bytes, so it cannot fall back on its own --
    only the client can, by resending the photo it still holds. A dropped photo
    is invisible to the resident, which makes it the worst available outcome.

  * A blocked verdict reached at pick time must still block at submit time. The
    resident was already told, so the only way this matters is a client that
    ignores the answer -- which is precisely the client worth checking.
"""

import pytest

# The service itself is dependency-free, but resolve() reaches app.models
# through _take, and the CI job installs neither SQLAlchemy nor Pillow.
pytest.importorskip("sqlalchemy.orm")

from app.services import photo_handles as ph


DATA_URI = "data:image/jpeg;base64,AAAA"


class _Row:
    """The columns resolve() reads off a ScreenedPhoto."""

    def __init__(self, verdict="ready", media=None, reason="", faces=0, plates=0):
        self.verdict = verdict
        self.media = media
        self.reason = reason
        self.faces = faces
        self.plates = plates


def _rows(monkeypatch, table):
    """Stand in for the single-use fetch-and-delete, keyed by token."""
    taken = []

    async def _take(db, token):
        taken.append(token)
        return table.get(token)

    monkeypatch.setattr(ph, "_take", _take)
    return taken


def _handle(token):
    return ph.HANDLE_PREFIX + token


# ---------------------------------------------------------------- the token

def test_a_handle_is_long_and_random():
    """Possession of the token is the only authorisation there can be -- a
    resident has no account to sign in to -- so it has to be unguessable and
    unwalkable. Two mints must never collide, and 32 bytes of `secrets` is the
    floor, not a nice-to-have."""
    tokens = {ph.new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 40 for t in tokens)


def test_handles_are_distinguishable_from_the_media_they_travel_beside():
    """media_urls carries data URIs and http(s) URLs too, and classifying an
    entry must never be a guess."""
    assert ph.is_handle(_handle("abc"))
    assert not ph.is_handle(DATA_URI)
    assert not ph.is_handle("https://example.gov/photo.jpg")
    assert not ph.is_handle("")
    assert not ph.is_handle(None)


# --------------------------------------------------------------- resolving

@pytest.mark.asyncio
async def test_a_ready_handle_is_redeemed_without_screening_again(monkeypatch):
    """The whole point: the Vision call already happened at pick time, and the
    submit path must not repeat it."""
    _rows(monkeypatch, {"t1": _Row(media=DATA_URI, faces=2, plates=1)})

    out = await ph.resolve(None, [_handle("t1")])

    assert out.screened == [(0, DATA_URI)]
    assert out.inline == []
    assert (out.faces, out.plates) == (2, 1)
    assert not out.stale and not out.blocked


@pytest.mark.asyncio
async def test_media_that_is_not_a_handle_goes_down_the_inline_path_untouched(monkeypatch):
    """An Open311 client posting base64 and a connector posting a hosted URL
    both still work exactly as before. This path is additive."""
    _rows(monkeypatch, {})

    out = await ph.resolve(None, [DATA_URI, "https://example.gov/p.jpg"])

    assert out.inline == [(0, DATA_URI), (1, "https://example.gov/p.jpg")]
    assert out.screened == [] and out.stale == []


@pytest.mark.asyncio
async def test_a_mixed_list_keeps_the_residents_ordering(monkeypatch):
    """Indices come back with the values so the caller can rebuild the list in
    the order the photos were attached -- a report whose photos silently reorder
    is a report whose "before" and "after" shots have swapped."""
    _rows(monkeypatch, {"t1": _Row(media="redacted-a")})

    out = await ph.resolve(None, [DATA_URI, _handle("t1")])

    assert out.inline == [(0, DATA_URI)]
    assert out.screened == [(1, "redacted-a")]


@pytest.mark.asyncio
async def test_an_unknown_handle_is_reported_rather_than_dropped(monkeypatch):
    """Expired, already spent, or never minted -- all the same to the server,
    which never kept the bytes. It has to say so loudly enough that the client
    resends the photo inline."""
    _rows(monkeypatch, {})

    out = await ph.resolve(None, [_handle("gone")])

    assert out.stale == [_handle("gone")]
    assert out.screened == [] and out.inline == []


@pytest.mark.asyncio
async def test_a_handle_is_spent_once(monkeypatch):
    """The row is deleted on redemption, so a token that leaks after the report
    is filed names nothing, and two submissions quoting the same handle cannot
    both attach the photo."""
    table = {"t1": _Row(media=DATA_URI)}
    taken = _rows(monkeypatch, table)

    async def _take_once(db, token):
        taken.append(token)
        return table.pop(token, None)

    monkeypatch.setattr(ph, "_take", _take_once)

    first = await ph.resolve(None, [_handle("t1")])
    second = await ph.resolve(None, [_handle("t1")])

    assert first.screened == [(0, DATA_URI)]
    assert second.screened == []
    assert second.stale == [_handle("t1")]


@pytest.mark.asyncio
async def test_a_photo_blocked_at_pick_time_still_blocks_at_submit(monkeypatch):
    """The resident was told at pick time, which is better UX and is not a
    control -- it happens in their browser. The verdict is recorded server-side
    so a client that ignores it gets the same answer anyway."""
    _rows(monkeypatch, {"t1": _Row(verdict="blocked", reason="explicit")})

    out = await ph.resolve(None, [_handle("t1")])

    assert out.blocked
    assert out.screened == []


@pytest.mark.asyncio
async def test_a_photo_that_could_not_be_screened_is_withheld_not_published(monkeypatch):
    """The detector never answered, so nothing established that there is no face
    in it. It goes to staff, and the report still goes through."""
    _rows(monkeypatch, {"t1": _Row(verdict="needs_review", reason="provider-error")})

    out = await ph.resolve(None, [_handle("t1")])

    assert out.withheld == [{"media": "", "reason": "provider-error"}]
    assert out.screened == []
    assert not out.blocked


@pytest.mark.asyncio
async def test_handles_past_the_photo_cap_are_not_redeemed(monkeypatch):
    """Three photos is the ceiling everywhere else in intake, and this endpoint
    is reachable without an account -- a caller must not be able to attach
    thirty images by minting thirty handles."""
    table = {f"t{i}": _Row(media=f"redacted-{i}") for i in range(6)}
    taken = _rows(monkeypatch, table)

    out = await ph.resolve(None, [_handle(f"t{i}") for i in range(6)])

    assert len(out.screened) == ph.MAX_HANDLES
    # And the extras were never even looked up, so they stay redeemable rather
    # than being burned by a request that was not going to use them.
    assert len(taken) == ph.MAX_HANDLES


@pytest.mark.asyncio
async def test_no_media_at_all_is_not_an_error(monkeypatch):
    _rows(monkeypatch, {})
    out = await ph.resolve(None, None)
    assert out.inline == [] and out.screened == [] and out.stale == []
