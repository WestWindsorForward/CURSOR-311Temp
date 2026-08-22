"""Handles for photos screened before the report they belong to exists.

The problem
-----------
Screening a photo means a Google Vision round trip. Doing it inside the
create-request POST put that round trip on the Submit button: a resident with a
6MB phone photo pressed Submit and watched a generic spinner for the length of
an upload plus an annotate. Long enough to look broken, long enough that people
press Submit twice.

None of that work needs to be there. The photo is chosen minutes before Submit,
while the resident is still writing the description and placing the pin. So the
portal uploads it at pick time, gets it screened in the background, and Submit
carries a short opaque handle instead of megabytes of base64 -- and the verdict
is already known, so a photo that has to be blocked is refused while the
resident is still looking at it rather than after they have filled in a form.

What a handle is, and is not
----------------------------
It is a bearer token, and that is the whole of its security model: the resident
who uploaded the photo has no account to authenticate as, so possession of the
token is the only thing we can check. That forces three properties:

  * unguessable -- 32 bytes from `secrets`, so the space cannot be walked;
  * short-lived -- an hour, a generous form-filling session, then reaped;
  * spent on use -- redeeming a handle deletes the row, so a leaked token has a
    single-use window rather than a standing read of somebody's photo.

The row holds REDACTED bytes only. The blur happens before the row is written,
exactly as it does on the inline path, so this table does not become the
unredacted-original store the rest of the module exists to avoid.

Unknown handles are not an error
--------------------------------
A handle can expire while a resident is filling in a long form, and the server
cannot recover the bytes -- it never kept them. So an unrecognised handle is
reported back to the client as a specific, recoverable condition, and the
client resubmits those photos as inline data. That is a fallback onto the
existing inline path, not a dropped photo; silently discarding the photo would
be the one outcome the resident cannot detect.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Handles travel inside media_urls, which is otherwise data URIs and http(s)
# URLs. The prefix is a scheme that cannot collide with either, so classifying
# an entry never needs to guess.
HANDLE_PREFIX = "screened:"

# Long enough for a slow resident on a long form, short enough that an
# abandoned upload is not sitting in the database all day. Every row is a
# full-size image, and this table is written by an unauthenticated endpoint.
HANDLE_TTL_MINUTES = 60

# Same ceiling as the inline path. Nothing enforces a per-session budget beyond
# the endpoint's rate limit, so this is the cap on what one report can spend.
MAX_HANDLES = 3

# Hard ceiling on rows in flight, enforced on every mint rather than only on the
# hourly reap. Each row is a full-resolution JPEG as base64 text, so ~600 rows
# is a few hundred megabytes -- well above any real town's concurrent form
# traffic and well below anything that threatens the database.
MAX_STORED_PHOTOS = 600


def is_handle(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(HANDLE_PREFIX)


def _token_of(value: str) -> str:
    return value[len(HANDLE_PREFIX):]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _evict_over_cap(db) -> int:
    """Keep the table bounded, without waiting for the hourly reap.

    Every row is a full-resolution JPEG stored as base64 text in Postgres, and
    the writer is an endpoint with no login. Between two reaps the only other
    limit is the rate limit, so the steady state was "whatever an hour of
    accepted uploads weighs" -- comfortably into the gigabytes if anyone is
    trying, and it grows fastest under exactly the traffic that means abuse.

    Expired rows go first, since nothing can redeem them. Only if the table is
    still over the cap do live rows go, oldest first -- and an evicted live
    handle degrades to "stale", which the client already recovers from by
    resending the photo inline. Losing that photo was never on the table.
    """
    from sqlalchemy import delete, func, select

    from app.models import ScreenedPhoto

    total = (await db.execute(
        select(func.count()).select_from(ScreenedPhoto)
    )).scalar_one()
    if total < MAX_STORED_PHOTOS:
        return 0

    dropped = await reap(db)
    over = total - dropped - MAX_STORED_PHOTOS + 1
    if over <= 0:
        return dropped

    oldest = (await db.execute(
        select(ScreenedPhoto.id).order_by(ScreenedPhoto.created_at.asc()).limit(over)
    )).scalars().all()
    if oldest:
        await db.execute(delete(ScreenedPhoto).where(ScreenedPhoto.id.in_(oldest)))
        logger.warning(
            "[Photo handles] table at cap (%d); evicted %d live handle(s). "
            "Those submissions fall back to screening at submit time.",
            MAX_STORED_PHOTOS, len(oldest),
        )
    return dropped + len(oldest)


async def mint(db, *, verdict: str, media: Optional[str] = None,
               reason: str = "", faces: int = 0, plates: int = 0) -> str:
    """Store a screening result and return the handle that names it.

    `media` must already be redacted, and must be None for anything the caller
    is not prepared to have published.
    """
    from app.models import ScreenedPhoto

    await _evict_over_cap(db)

    token = new_token()
    db.add(ScreenedPhoto(
        token=token,
        verdict=verdict,
        reason=reason or "",
        media=media,
        faces=faces,
        plates=plates,
        expires_at=_now() + timedelta(minutes=HANDLE_TTL_MINUTES),
    ))
    await db.flush()
    return HANDLE_PREFIX + token


class _Taken:
    """The columns of a redeemed row, detached from the session it came from."""

    __slots__ = ("verdict", "media", "reason", "faces", "plates", "expires_at")

    def __init__(self, verdict, media, reason, faces, plates, expires_at):
        self.verdict = verdict
        self.media = media
        self.reason = reason
        self.faces = faces
        self.plates = plates
        self.expires_at = expires_at


async def _take(db, token: str):
    """Delete a row and return what it held, or None. Atomically.

    ONE statement, not a SELECT followed by a delete. The read-then-delete
    version was a race with a real trigger: this whole feature exists because
    people double-tap Submit. Both requests would load the row, both would
    believe they had claimed the photo, and then one of them would fail its
    delete -- a 500, or a 409 whose single inline retry goes through and files
    the report twice.

    DELETE ... RETURNING makes the database pick the winner. The loser gets no
    row and is told the handle is stale, which is exactly true: someone else
    spent it.
    """
    from sqlalchemy import delete

    from app.models import ScreenedPhoto

    row = (await db.execute(
        delete(ScreenedPhoto)
        .where(ScreenedPhoto.token == token)
        .returning(ScreenedPhoto.verdict, ScreenedPhoto.media, ScreenedPhoto.reason,
                   ScreenedPhoto.faces, ScreenedPhoto.plates, ScreenedPhoto.expires_at)
    )).first()
    if row is None:
        return None

    taken = _Taken(*row)
    expires_at = taken.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < _now():
        return None
    return taken


class ResolvedMedia:
    """What `resolve` worked out about one report's media list.

    `inline` is what still has to go through the existing screen-and-redact
    path; `screened` is what came back already done and must not be screened
    again. `stale` names the handles the server could not honour, which the
    caller turns into a recoverable error rather than a silent drop.
    """

    def __init__(self) -> None:
        # (index in the original list, value) so the original ordering can be
        # rebuilt after the inline half comes back.
        self.inline: List[Tuple[int, str]] = []
        self.screened: List[Tuple[int, str]] = []
        self.withheld: List[Dict[str, str]] = []
        self.stale: List[str] = []
        # Handles past MAX_HANDLES. Not redeemed and not an error -- the same
        # cap applies to inline media -- but named, so a fourth photo does not
        # vanish between the client and the log.
        self.overflow: List[str] = []
        self.blocked: bool = False
        self.faces: int = 0
        self.plates: int = 0
        self.length: int = 0


async def resolve(db, media: Optional[List[Any]]) -> ResolvedMedia:
    """Split a submitted media list into already-screened and still-to-screen.

    Anything that is not a handle -- a data URI from an Open311 client, a hosted
    URL from a connector -- passes straight through to `inline` untouched, which
    is what keeps the direct-POST path working exactly as it did.
    """
    out = ResolvedMedia()
    items = [m for m in (media or []) if isinstance(m, str)]
    out.length = len(items)

    taken = 0
    for index, item in enumerate(items):
        if not is_handle(item):
            out.inline.append((index, item))
            continue

        taken += 1
        if taken > MAX_HANDLES:
            # Past the cap the photo was never a candidate on the inline path
            # either. Do not redeem it -- but say so, rather than letting a
            # fourth photo disappear between the client and the log.
            out.overflow.append(item)
            logger.info("[Photo handles] ignoring handle past the %d-photo cap", MAX_HANDLES)
            continue

        row = await _take(db, _token_of(item))
        if row is None:
            # Expired, already spent, or never existed. The bytes are not here
            # to fall back on, so the caller has to ask the client for them.
            out.stale.append(item)
            continue

        if row.verdict == "blocked":
            out.blocked = True
            continue
        if row.verdict != "ready" or not row.media:
            # Screening never produced bytes we can publish -- Vision timed out,
            # the provider errored, or the image would not decode. The pick-time
            # endpoint deliberately stores nothing in that case, so there is no
            # copy here to fall back on. Treat it exactly like a stale handle:
            # the client still holds the original, so ask for it rather than
            # recording a withheld entry with an empty `media`, which is a photo
            # the resident believes they attached and nobody ever sees again.
            out.stale.append(item)
            continue

        out.screened.append((index, row.media))
        out.faces += row.faces or 0
        out.plates += row.plates or 0

    return out


async def reap(db, *, limit: int = 5000) -> int:
    """Delete handles nobody came back for. Returns how many went.

    Unspent rows are the normal case for an abandoned form, and each one is a
    full-size photo. Without this the table grows with every resident who
    changed their mind, and it would grow fastest under exactly the traffic an
    abuse case produces.
    """
    from sqlalchemy import delete, select

    from app.models import ScreenedPhoto

    stale = (await db.execute(
        select(ScreenedPhoto.id)
        .where(ScreenedPhoto.expires_at < _now())
        .limit(limit)
    )).scalars().all()
    if not stale:
        return 0
    await db.execute(delete(ScreenedPhoto).where(ScreenedPhoto.id.in_(stale)))
    return len(stale)
