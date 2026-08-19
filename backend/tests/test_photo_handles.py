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

import asyncio

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
async def test_a_photo_that_could_not_be_screened_is_never_published(monkeypatch):
    """The detector never answered, so nothing established that there is no face
    in it -- and, because the pick-time endpoint stores nothing when screening
    fails, there are no bytes here to hold back either.

    So the handle is reported as stale: the caller still has the original and is
    asked to resend it inline, where the submit path screens it properly. The
    earlier behaviour recorded a withheld entry whose `media` was the empty
    string, which is a photo the resident believes they attached, that no error
    mentions, and that nobody ever sees.

    The guarantee under test is unchanged either way: it is not published."""
    _rows(monkeypatch, {"t1": _Row(verdict="needs_review", reason="provider-error")})

    out = await ph.resolve(None, [_handle("t1")])

    assert out.screened == []
    assert not out.blocked
    # Nothing empty was smuggled into the report as an attachment.
    assert out.withheld == []
    # And the caller is told, so the photo can be resent rather than lost.
    assert out.stale == [_handle("t1")]


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


# --------------------------------------------------------------------------
# the endpoint's own verdict
# --------------------------------------------------------------------------

pytest.importorskip("fastapi.routing")

import inspect  # noqa: E402


class _FakeUpload:
    def __init__(self, raw, content_type="image/avif"):
        self._raw = raw
        self.content_type = content_type

    async def read(self, size=-1):
        return self._raw if size < 0 else self._raw[:size]


class _FakeDB:
    """Records what the endpoint tried to persist."""

    def __init__(self):
        self.added = []
        self.committed = 0

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed += 1

    async def execute(self, *a, **kw):           # _evict_over_cap's COUNT / reap
        class _R:
            def scalar_one(self_inner):
                return 0

            def scalars(self_inner):
                class _S:
                    def all(self_deep):
                        return []
                return _S()

            def first(self_inner):
                return None
        return _R()


# ISO-BMFF with `ftypavif`. Pillow 11 in this image has no AVIF plugin and no
# HEIF opener, so it cannot read this -- while every current browser renders it
# and every recent iPhone produces its HEIC cousin.
AVIF_SHAPED = (
    b"\x00\x00\x00\x20ftypavif\x00\x00\x00\x00avifmif1miaf"
    + b"\x00\x00\x00\x10meta" + b"\x00" * 64
)


async def _call_screen_photo(raw, content_type="image/avif"):
    from app.api import open311

    handler = inspect.unwrap(open311.screen_photo)
    db = _FakeDB()
    result = await handler(request=None, file=_FakeUpload(raw, content_type), db=db)
    return result, db


@pytest.mark.asyncio
async def test_a_photo_this_build_cannot_decode_is_never_certified_ready():
    """The endpoint's job is to say whether a photo is safe to publish, and it
    must not say yes about one nothing looked at.

    An AVIF (or HEIC) upload cannot be opened here, so Vision is never called,
    SafeSearch never runs, no face is ever sought and the EXIF GPS block is
    never removed. Answering "ready" would put that photo in screened_photos and
    then on the public map, unmoderated and unblurred, *because* we could not
    read it.
    """
    result, db = await _call_screen_photo(AVIF_SHAPED)

    assert result["status"] != "ready"
    assert result["status"] == "needs_review"
    assert "preview" not in result


@pytest.mark.asyncio
async def test_the_bytes_of_an_undecodable_photo_are_not_stored():
    """Not stored, so not reachable and not published. The client still holds
    them and sends them with the report, where they land in
    media_pending_review for a person to look at."""
    _, db = await _call_screen_photo(AVIF_SHAPED)

    assert len(db.added) == 1
    assert db.added[0].media is None
    assert db.added[0].verdict == "needs_review"


@pytest.mark.asyncio
async def test_a_photo_too_large_to_decode_is_refused_the_same_way():
    import io

    from PIL import Image

    huge = io.BytesIO()
    Image.new("L", (12000, 12000)).save(huge, format="PNG")
    result, db = await _call_screen_photo(huge.getvalue(), "image/png")

    assert result["status"] == "needs_review"
    assert db.added[0].media is None


@pytest.mark.asyncio
async def test_a_readable_photo_still_comes_back_ready_with_the_redacted_bytes():
    """The guard must not swallow the normal case."""
    import io

    from PIL import Image

    ok = io.BytesIO()
    Image.new("RGB", (600, 400), (90, 120, 160)).save(ok, format="JPEG")
    result, db = await _call_screen_photo(ok.getvalue(), "image/jpeg")

    assert result["status"] == "ready"
    assert result["preview"].startswith("data:image/")
    # Stored bytes are ours, not the resident's -- so the EXIF block is gone.
    assert db.added[0].media == result["preview"]
    assert db.added[0].verdict == "ready"


# --------------------------------------------------------------------------
# two submits, one handle
# --------------------------------------------------------------------------

class _AtomicStore:
    """A db whose DELETE ... RETURNING really is one indivisible step.

    Which is the point: the correctness has to come from the statement, not from
    the caller checking first. This fake will happily serve two concurrent
    redemptions, and only the database's own atomicity decides the winner.
    """

    def __init__(self, rows):
        self.rows = dict(rows)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        token = statement.compile().params.get("token_1")

        class _R:
            def first(_self):
                return None
        popped = self.rows.pop(token, None)
        if popped is None:
            return _R()

        class _Hit:
            def first(_self):
                return popped
        # Yielding here is what makes the race real: a read-then-delete
        # implementation would interleave across this point and both callers
        # would come away believing they had the photo.
        await asyncio.sleep(0)
        return _Hit()


@pytest.mark.asyncio
async def test_redeeming_a_handle_is_one_statement_not_two():
    """The docstring used to claim single-use while the code did SELECT then
    delete. That is a race with a live trigger -- this whole feature exists
    because people double-tap Submit -- and its outcomes were a 500 from the
    losing delete, or a 409 whose single inline retry files the report twice."""
    from datetime import datetime, timedelta, timezone

    later = datetime.now(timezone.utc) + timedelta(minutes=30)
    store = _AtomicStore({"t1": ("ready", DATA_URI, "", 0, 0, later)})

    await ph._take(store, "t1")

    assert len(store.statements) == 1, "redemption still takes more than one statement"
    sql = str(store.statements[0]).upper()
    assert sql.startswith("DELETE"), f"not a delete: {sql[:40]}"
    assert "RETURNING" in sql, "the delete does not return the row it removed"


@pytest.mark.asyncio
async def test_two_concurrent_submits_cannot_both_claim_the_same_photo():
    from datetime import datetime, timedelta, timezone

    later = datetime.now(timezone.utc) + timedelta(minutes=30)
    store = _AtomicStore({"t1": ("ready", DATA_URI, "", 0, 0, later)})

    first, second = await asyncio.gather(
        ph.resolve(store, [_handle("t1")]),
        ph.resolve(store, [_handle("t1")]),
        return_exceptions=True,
    )

    # Neither raises. The old version's loser hit StaleDataError -> a 500 on a
    # resident pressing Submit twice.
    assert not isinstance(first, Exception) and not isinstance(second, Exception)

    winners = [r for r in (first, second) if r.screened]
    losers = [r for r in (first, second) if not r.screened]
    assert len(winners) == 1 and len(losers) == 1
    # The loser is told the handle is stale, which is exactly true: someone
    # else spent it. Its client then resends inline rather than filing twice.
    assert losers[0].stale == [_handle("t1")]


@pytest.mark.asyncio
async def test_a_handle_past_the_cap_is_named_rather_than_swallowed():
    table = {f"t{i}": _Row(media=f"redacted-{i}") for i in range(5)}
    out = await _resolve_with(table, [_handle(f"t{i}") for i in range(5)])
    assert len(out.screened) == ph.MAX_HANDLES
    assert len(out.overflow) == 5 - ph.MAX_HANDLES


async def _resolve_with(table, media):
    """resolve() against a table, without monkeypatching module internals."""
    original = ph._take

    async def _take(db, token):
        return table.pop(token, None)

    ph._take = _take
    try:
        return await ph.resolve(None, media)
    finally:
        ph._take = original
