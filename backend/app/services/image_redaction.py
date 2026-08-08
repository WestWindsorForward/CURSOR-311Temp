"""Blur faces and licence plates out of resident photos, on ingest.

Why this is destructive, and why it happens here
------------------------------------------------
The obvious design -- keep the original, blur it when it is displayed -- is the
wrong one for a public records system. A 311 photo is a government record: it
goes out through the Open311 API, the research export, public-records responses and
whatever the town's own dashboard renders, and every one of those is a separate
place to forget to apply the blur. Worse, the unredacted face is still sitting
in the database until retention deletes it, which is exactly the biometric
retention question a state privacy review will ask about.

So the blur is applied once, at intake, to the bytes that get stored. There is
no original. That is a real trade-off -- if a photo's evidentiary value was the
face, it is gone -- and it is the trade-off a municipality should want.

What it will get wrong
----------------------
Face detection is good and still not perfect: profiles, faces smaller than
roughly 30px, heavy shadow and motion blur are all missed. Plate detection is
worse -- see `plate_like` -- because none of the cloud vendors sell a plate
detector, so we are inferring "this text region is a plate" from OCR output and
geometry. It over-blurs (a STOP sign, a house number, a bus route board) and it
under-blurs (angled, dirty, obscured, or partially cropped plates).

This is a mitigation, not a guarantee, and the admin UI must say so. A town that
needs a guarantee has to review photos manually.

Providers
---------
    google  -> Vision  images:annotate FACE_DETECTION / TEXT_DETECTION
    aws     -> Rekognition DetectFaces / DetectText
    azure   -> AI Face detect  +  AI Vision Image Analysis 4.0 read
    local   -> OpenCV Haar cascades, offline, free, and noticeably weaker

`local` exists because the per-photo cost of the cloud providers is small but
not zero, and because "the unredacted image never leaves the building" is a
materially better answer to a privacy review than "we send it to Google and
they say they don't keep it."
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.content_moderation import ModerationResult

logger = logging.getLogger(__name__)

REDACTION_PROVIDER_KEY = "REDACTION_PROVIDER"
REDACT_FACES_KEY = "REDACT_FACES"
REDACT_PLATES_KEY = "REDACT_PLATES"

PROVIDERS = ("google", "aws", "azure", "local")

# Detection confidence below which we ignore a hit. Deliberately low for faces:
# a false blur costs a slightly worse photo, a missed face costs someone's
# biometric data being published on a municipal website. The asymmetry is not
# close, so we bias hard toward blurring.
FACE_MIN_CONFIDENCE = 0.35

# Plates get the opposite treatment. The plate signal is a guess built on OCR
# geometry, and at a low threshold half the street signs in New Jersey get
# smeared. Staff need to be able to read the report.
PLATE_MIN_CONFIDENCE = 0.55

# Detected boxes are grown before blurring. A face box is drawn tight around the
# features and leaves hairline, jaw and ears legible; an OCR box is drawn around
# the characters and leaves the plate's border and state name visible.
FACE_PADDING = 0.25
PLATE_PADDING = 0.45

# Below this the blur is cosmetic rather than irreversible -- a lightly blurred
# 200px face can be partially recovered. Radius scales with the box so a large
# face is destroyed as thoroughly as a small one.
BLUR_RADIUS_RATIO = 0.28
MIN_BLUR_RADIUS = 6

MAX_IMAGES = 3


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Box:
    """A detection, in fractions of image width/height rather than pixels.

    Fractional coordinates because the four providers disagree: Rekognition
    returns ratios, Vision and Azure return pixels, and OpenCV returns pixels in
    whatever resolution we downscaled to before running it. Normalising at the
    parser boundary means the blur code never has to care.
    """

    left: float
    top: float
    right: float
    bottom: float
    kind: str = "face"          # face | plate
    confidence: float = 1.0

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        return self.width * self.height

    def clamped(self) -> "Box":
        """Trim to the image. Providers routinely return boxes that run off the
        edge for a face at the frame border, and PIL will not crop past it."""
        left, right = sorted((self.left, self.right))
        top, bottom = sorted((self.top, self.bottom))
        return Box(
            max(0.0, min(1.0, left)),
            max(0.0, min(1.0, top)),
            max(0.0, min(1.0, right)),
            max(0.0, min(1.0, bottom)),
            self.kind,
            self.confidence,
        )

    def padded(self, ratio: float) -> "Box":
        """Grow by a fraction of the box's own size, then clamp."""
        dx = self.width * ratio / 2
        dy = self.height * ratio / 2
        return Box(self.left - dx, self.top - dy, self.right + dx, self.bottom + dy,
                   self.kind, self.confidence).clamped()

    def to_pixels(self, width: int, height: int) -> Tuple[int, int, int, int]:
        box = self.clamped()
        return (
            int(box.left * width),
            int(box.top * height),
            max(int(box.left * width) + 1, int(box.right * width)),
            max(int(box.top * height) + 1, int(box.bottom * height)),
        )


def _overlap(a: Box, b: Box) -> float:
    """Intersection area over the smaller box, not IoU.

    IoU is the usual choice and it is wrong here. A small face box sitting
    entirely inside a larger one -- which is what two providers, or a face
    detector and a text detector, produce for the same subject -- has a low IoU
    and should still be merged. Containment is the relationship we care about.
    """
    inter_w = max(0.0, min(a.right, b.right) - max(a.left, b.left))
    inter_h = max(0.0, min(a.bottom, b.bottom) - max(a.top, b.top))
    inter = inter_w * inter_h
    smaller = min(a.area, b.area)
    return inter / smaller if smaller > 0 else 0.0


def merge_boxes(boxes: Sequence[Box], threshold: float = 0.4) -> List[Box]:
    """Collapse overlapping detections into their union.

    Blurring the same face twice is harmless, but each blur pass softens the
    surrounding pixels again, and stacked passes on a busy photo turn it to
    soup. Merging first also keeps the count we report to staff honest -- "3
    faces blurred" should mean three people.
    """
    merged: List[Box] = []
    for box in sorted(boxes, key=lambda b: b.area, reverse=True):
        box = box.clamped()
        if box.area <= 0:
            continue
        for i, existing in enumerate(merged):
            if existing.kind == box.kind and _overlap(existing, box) >= threshold:
                merged[i] = Box(
                    min(existing.left, box.left),
                    min(existing.top, box.top),
                    max(existing.right, box.right),
                    max(existing.bottom, box.bottom),
                    box.kind,
                    max(existing.confidence, box.confidence),
                )
                break
        else:
            merged.append(box)
    return merged


def _from_vertices(vertices: List[Dict[str, Any]], width: int, height: int,
                   kind: str, confidence: float) -> Optional[Box]:
    """Build a Box from a Vision-style vertex list.

    Vision omits `x` or `y` entirely when the value is 0 rather than sending a
    zero, so `v.get("x", 0)` is load-bearing and not defensive padding.
    """
    if not vertices or width <= 0 or height <= 0:
        return None
    xs = [float(v.get("x", 0) or 0) for v in vertices]
    ys = [float(v.get("y", 0) or 0) for v in vertices]
    if not xs or not ys:
        return None
    return Box(min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height,
               kind, confidence).clamped()


# --------------------------------------------------------------------------
# pure response parsers -- no network, so they are the testable surface
# --------------------------------------------------------------------------

_GOOGLE_LIKELIHOOD = {
    "VERY_LIKELY": 0.95, "LIKELY": 0.75, "POSSIBLE": 0.5,
    "UNLIKELY": 0.25, "VERY_UNLIKELY": 0.05, "UNKNOWN": 0.0,
}


def google_faces(payload: Dict[str, Any], width: int, height: int) -> List[Box]:
    """Parse Vision images:annotate FACE_DETECTION.

    Vision gives two polygons per face. `boundingPoly` is generous and includes
    hair and headwear; `fdBoundingPoly` ("face detection") is tight to the skin.
    We take boundingPoly, because for redaction the generous one is the right
    one -- hair and headwear are identifying, and the padding we add afterwards
    is measured relative to whichever box we started from.
    """
    responses = payload.get("responses") or []
    if not responses:
        return []
    boxes: List[Box] = []
    for face in responses[0].get("faceAnnotations") or []:
        confidence = float(face.get("detectionConfidence", 0) or 0)
        if confidence < FACE_MIN_CONFIDENCE:
            continue
        poly = face.get("boundingPoly") or face.get("fdBoundingPoly") or {}
        box = _from_vertices(poly.get("vertices") or [], width, height, "face", confidence)
        if box and box.area > 0:
            boxes.append(box)
    return boxes


def rekognition_faces(payload: Dict[str, Any]) -> List[Box]:
    """Parse Rekognition DetectFaces. BoundingBox is already fractional."""
    boxes: List[Box] = []
    for face in payload.get("FaceDetails") or []:
        confidence = float(face.get("Confidence", 0) or 0) / 100.0
        if confidence < FACE_MIN_CONFIDENCE:
            continue
        bb = face.get("BoundingBox") or {}
        left = float(bb.get("Left", 0) or 0)
        top = float(bb.get("Top", 0) or 0)
        box = Box(left, top, left + float(bb.get("Width", 0) or 0),
                  top + float(bb.get("Height", 0) or 0), "face", confidence).clamped()
        if box.area > 0:
            boxes.append(box)
    return boxes


def azure_faces(payload: List[Dict[str, Any]], width: int, height: int) -> List[Box]:
    """Parse Azure AI Face detect. Pixel rectangles, and no confidence score --
    the API only returns faces it is already sure about, so we record 1.0 rather
    than inventing a number."""
    if width <= 0 or height <= 0:
        return []
    boxes: List[Box] = []
    for face in payload or []:
        rect = face.get("faceRectangle") or {}
        left = float(rect.get("left", 0) or 0)
        top = float(rect.get("top", 0) or 0)
        box = Box(left / width, top / height,
                  (left + float(rect.get("width", 0) or 0)) / width,
                  (top + float(rect.get("height", 0) or 0)) / height,
                  "face", 1.0).clamped()
        if box.area > 0:
            boxes.append(box)
    return boxes


# ---- plates ---------------------------------------------------------------

# Signs and markings that OCR reads off a streetscape and that satisfy a naive
# "short uppercase string" test. Not exhaustive and cannot be -- it is a
# backstop under the structural rules in plate_like, not the primary filter.
_NOT_A_PLATE = {
    "STOP", "YIELD", "ONE WAY", "ONEWAY", "NO PARKING", "EXIT", "SPEED LIMIT",
    "SLOW", "AHEAD", "DETOUR", "SCHOOL", "BUS", "TAXI", "POLICE", "FIRE",
    "AMBULANCE", "OPEN", "CLOSED", "SALE", "FOR SALE", "FOR RENT", "MPH",
    "KEEP RIGHT", "KEEP LEFT", "DO NOT ENTER", "PED XING", "RR XING",
}

_PLATE_CHARS = re.compile(r"[^A-Z0-9]")


def plate_like(text: str, box: Box) -> float:
    """How likely an OCR text region is a licence plate, 0..1.

    There is no honest way to make this good. Google, AWS and Azure all sell
    face detection and none of them sell plate detection, so what is actually
    available is "here is some text and where it was", and the plate has to be
    inferred from the shape of the string and the shape of the box.

    The structural signals that survive contact with real photos:

      * 4-8 characters after stripping punctuation. US plates are 5-7; the
        window is widened by one at each end for OCR dropping or splitting a
        character.
      * Both a letter and a digit. This is the single strongest rule -- it
        eliminates nearly every road sign in one go, because signs are words.
      * A wide box. Plate text runs 2:1 to 5:1. A tall or square region is a
        house number or a shopfront.
      * Lower in the frame. A plate is on a vehicle at ground level; overhead
        signage is not. Weak on its own, useful as a tiebreak.

    All-numeric strings are scored but scored down, because they are equally
    likely to be a house number, and a blurred house number in a pothole report
    destroys the one thing that made the report actionable.

    Deliberately not attempted: reading the plate to check it against a state
    format. That means retaining the plate number to decide whether to hide the
    plate number.
    """
    raw = (text or "").strip().upper()
    if not raw or raw in _NOT_A_PLATE:
        return 0.0
    cleaned = _PLATE_CHARS.sub("", raw)
    if not (4 <= len(cleaned) <= 8):
        return 0.0
    # A multi-word string is a sign. Plates are one token (the state name sits
    # in its own OCR region, above or below).
    if len(raw.split()) > 2:
        return 0.0

    has_alpha = any(c.isalpha() for c in cleaned)
    has_digit = any(c.isdigit() for c in cleaned)
    if not has_digit:
        return 0.0

    score = 0.55 if (has_alpha and has_digit) else 0.3

    if box.height > 0:
        aspect = box.width / box.height
        if 1.8 <= aspect <= 5.5:
            score += 0.25
        elif 1.2 <= aspect < 1.8 or 5.5 < aspect <= 8.0:
            score += 0.1
        else:
            score -= 0.2

    # Vertical centre of the box. Below the midline is where vehicles are.
    centre = (box.top + box.bottom) / 2
    if centre >= 0.45:
        score += 0.12

    # A plate occupies a small part of a streetscape photo. A text region
    # covering a third of the frame is a shopfront or a banner.
    if box.area > 0.25:
        score -= 0.3

    return max(0.0, min(1.0, score))


def google_text_plates(payload: Dict[str, Any], width: int, height: int) -> List[Box]:
    """Parse Vision TEXT_DETECTION and keep the regions that look like plates.

    textAnnotations[0] is the whole-image aggregate, so it is skipped -- keeping
    it would blur the entire photo the moment any plate-shaped string appeared.
    """
    responses = payload.get("responses") or []
    if not responses:
        return []
    annotations = responses[0].get("textAnnotations") or []
    boxes: List[Box] = []
    for ann in annotations[1:]:
        poly = ann.get("boundingPoly") or {}
        box = _from_vertices(poly.get("vertices") or [], width, height, "plate", 0.0)
        if not box or box.area <= 0:
            continue
        score = plate_like(ann.get("description", ""), box)
        if score >= PLATE_MIN_CONFIDENCE:
            boxes.append(Box(box.left, box.top, box.right, box.bottom, "plate", score))
    return boxes


def rekognition_text_plates(payload: Dict[str, Any]) -> List[Box]:
    """Parse Rekognition DetectText.

    Only WORD detections, not LINE: a LINE glues the plate to the state name
    and the dealer frame, which wrecks both the aspect-ratio test and the
    character-count test.
    """
    boxes: List[Box] = []
    for det in payload.get("TextDetections") or []:
        if det.get("Type") != "WORD":
            continue
        bb = (det.get("Geometry") or {}).get("BoundingBox") or {}
        left = float(bb.get("Left", 0) or 0)
        top = float(bb.get("Top", 0) or 0)
        box = Box(left, top, left + float(bb.get("Width", 0) or 0),
                  top + float(bb.get("Height", 0) or 0), "plate", 0.0).clamped()
        if box.area <= 0:
            continue
        score = plate_like(det.get("DetectedText", ""), box)
        # Rekognition's own OCR confidence gates the geometry score, so a
        # half-read smudge cannot reach the threshold on shape alone.
        ocr = float(det.get("Confidence", 0) or 0) / 100.0
        score *= max(0.0, min(1.0, ocr))
        if score >= PLATE_MIN_CONFIDENCE:
            boxes.append(Box(box.left, box.top, box.right, box.bottom, "plate", score))
    return boxes


def azure_text_plates(payload: Dict[str, Any], width: int, height: int) -> List[Box]:
    """Parse Azure AI Vision Image Analysis 4.0 `read` output.

    Azure returns a polygon as a flat list of {x, y} points in pixels, per word.
    """
    if width <= 0 or height <= 0:
        return []
    read = payload.get("readResult") or {}
    boxes: List[Box] = []
    for block in read.get("blocks") or []:
        for line in block.get("lines") or []:
            for word in line.get("words") or []:
                box = _from_vertices(word.get("boundingPolygon") or [], width, height, "plate", 0.0)
                if not box or box.area <= 0:
                    continue
                score = plate_like(word.get("text", ""), box)
                score *= max(0.0, min(1.0, float(word.get("confidence", 1) or 0)))
                if score >= PLATE_MIN_CONFIDENCE:
                    boxes.append(Box(box.left, box.top, box.right, box.bottom, "plate", score))
    return boxes


# --------------------------------------------------------------------------
# blurring
# --------------------------------------------------------------------------

@dataclass
class RedactionResult:
    """What came back from redacting one photo."""

    media: str                                   # the redacted data URI, or the input unchanged
    faces: int = 0
    plates: int = 0
    changed: bool = False
    skipped_reason: str = ""                     # why nothing happened, for the audit trail

    @property
    def total(self) -> int:
        return self.faces + self.plates


@dataclass
class BatchResult:
    media: List[str] = field(default_factory=list)
    faces: int = 0
    plates: int = 0
    skipped: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.faces or self.plates)


def _decode(media: str) -> Optional[Tuple[bytes, str]]:
    """Raw bytes plus the data-URI mime type, or None.

    http(s) URLs are deliberately not fetched. Following a resident-supplied URL
    server-side is an SSRF hole, and it is the same reason cloud_moderation
    skips them. It also means an externally-hosted photo cannot be redacted at
    all, which the caller has to surface rather than silently accept.
    """
    if not media or not isinstance(media, str):
        return None
    s = media.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return None
    mime = "image/jpeg"
    if s.startswith("data:"):
        header, _, s = s.partition(",")
        if ";" in header and ":" in header:
            candidate = header.split(":", 1)[1].split(";", 1)[0]
            if candidate.startswith("image/"):
                mime = candidate
    try:
        return base64.b64decode(s, validate=False), mime
    except Exception:
        return None


def _encode(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def blur_regions(raw: bytes, boxes: Sequence[Box]) -> Optional[bytes]:
    """Gaussian-blur each box into the image and re-encode it.

    Gaussian rather than pixelation, with the radius scaled to the box. A fixed
    radius looks fine on a 60px face and leaves a 400px one recognisable, and
    small-block pixelation is reversible in ways a wide Gaussian is not.

    The alpha channel is dropped and the result is written as JPEG: PNG carries
    the EXIF the phone attached, which for a resident photo usually includes GPS
    to a few metres and sometimes the device serial. Stripping it here is a
    second privacy win that falls out of the re-encode for free.
    """
    if not boxes:
        return None
    try:
        import io

        from PIL import Image, ImageFilter
    except Exception:  # pragma: no cover - Pillow not installed
        logger.warning("[Redaction] Pillow unavailable; cannot blur")
        return None

    try:
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGB")
        width, height = image.size
        applied = 0
        for box in boxes:
            left, top, right, bottom = box.to_pixels(width, height)
            if right - left < 2 or bottom - top < 2:
                continue
            region = image.crop((left, top, right, bottom))
            radius = max(MIN_BLUR_RADIUS, int(min(right - left, bottom - top) * BLUR_RADIUS_RATIO))
            image.paste(region.filter(ImageFilter.GaussianBlur(radius)), (left, top))
            applied += 1
        if not applied:
            return None
        out = io.BytesIO()
        # No exif= argument, so the original EXIF block is not carried over.
        image.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue()
    except Exception:
        logger.warning("[Redaction] blur failed", exc_info=True)
        return None


def strip_exif(raw: bytes) -> Optional[bytes]:
    """Re-encode without the camera metadata block.

    Worth doing on its own, independently of any face. A phone photo of a
    pothole normally carries GPS coordinates accurate to a few metres -- which
    for a report filed from a driveway is the reporter's home address, arriving
    with none of the protection the address field gets -- plus the capture
    timestamp and often a device identifier. All of it currently flows straight
    into media_urls and back out through the Open311 API and the research
    export.

    That is a larger privacy exposure than the faces, and unlike the faces it
    costs nothing: no provider, no credentials, no per-image fee and no false
    positives. So it runs whenever the bytes are decodable, including when no
    detector found anything.

    Returns None if the image cannot be re-encoded, in which case the caller
    keeps the original rather than dropping the photo.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=88, optimize=True)
            return out.getvalue()
    except Exception:
        return None


def exif_only(media: str) -> str:
    """One photo with its metadata block removed and nothing else done to it.

    The off paths use this. A town switching redaction off is making a decision
    about faces and plates -- the visible, sometimes-wrong blurring -- not about
    the GPS block, which strip_exif removes with no detector, no credentials and
    no false positives. Before this existed, the early returns handed the bytes
    back untouched, so switching the blur off also published the reporter's
    coordinates.

    Anything that cannot be decoded or re-encoded passes through unchanged,
    matching strip_exif's own contract of never dropping a photo.
    """
    decoded = _decode(media)
    if not decoded:
        return media
    cleaned = strip_exif(decoded[0])
    return _encode(cleaned, "image/jpeg") if cleaned is not None else media


def image_size(raw: bytes) -> Tuple[int, int]:
    """(width, height), or (0, 0) if the bytes are not a readable image.

    Needed before the detector runs, because Vision and Azure answer in pixels
    and Box is fractional.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            return image.size
    except Exception:
        return (0, 0)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

async def _flag(key: str, default: bool) -> bool:
    from app.services.secret_manager import get_secret
    raw = await get_secret(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "enabled")


async def resolve_provider() -> Optional[str]:
    """Which detector to use, or None only when a town has switched it off.

    Falls through to the moderation provider and then the AI provider, so a town
    that has already pasted one set of cloud credentials gets redaction without
    configuring a second thing.

    `local` used to be excluded from that fall-through, on the reasoning that
    OpenCV quality is low enough to deserve a decision rather than a default.
    The reasoning compares local against a cloud detector. The actual
    alternative, on a deployment with no cloud credentials, is **no detection at
    all** -- and against that, imperfect detection wins every time.

    It also contradicted `settings()` twelve lines below, which turns faces and
    plates on by default and says why: "publishing a stranger's face on a
    municipal website is a harm a town incurs by doing nothing, and the default
    should not be the harmful one." Both toggles defaulted on, and then the
    provider resolved to None and neither did anything. A fresh install stored
    every resident photo unmodified while the Photo Redaction card displayed
    Google Cloud Vision, because the catalog default said so.

    So local is now the floor. Off is still reachable -- explicitly, by choosing
    it -- which is the difference between a town deciding not to blur and a town
    not knowing it wasn't.
    """
    from app.services.secret_manager import get_secret

    override = ((await get_secret(REDACTION_PROVIDER_KEY)) or "").strip().lower()
    if override in PROVIDERS:
        return override
    if override in ("none", "off", "disabled"):
        return None

    moderation = ((await get_secret("MODERATION_PROVIDER")) or "").strip().lower()
    if moderation in ("google", "azure", "aws"):
        return moderation
    if moderation in ("none", "off", "disabled"):
        return None

    ai = ((await get_secret("AI_PROVIDER")) or "").strip().lower()
    derived = {"vertex": "google", "azure": "azure", "bedrock": "aws"}.get(ai)
    # Nothing indicates a cloud. Detection on this server needs no account and
    # no key, and sends no photo anywhere, so there is no reason for the answer
    # to be "none".
    return derived or "local"


async def settings() -> Tuple[Optional[str], bool, bool]:
    """(provider, redact_faces, redact_plates).

    Faces default on once a provider is resolvable: publishing a stranger's face
    on a municipal website is a harm a town incurs by doing nothing, and the
    default should not be the harmful one.

    Plates now default on too, for the same reason as faces: a plate published
    on a municipal website is a harm the town causes by leaving a setting alone,
    and the privacy-protecting state should be the one you get without acting.

    This is a deliberate reversal. Plates were off because the detector guesses
    (see `plate_like`) and its failure is a blurred house number on an otherwise
    useful report -- a real cost, but one a clerk can see and correct, whereas
    the published plate is one nobody notices until it matters. A town that
    would rather have the house numbers can switch plates off on the Photo
    Redaction card, which is reachable now that the card exists.
    """
    # The town's switch comes first, and it is not the same as choosing the
    # `none` provider. `resolve_provider()` deliberately floors at on-server
    # detection so a town cannot end up with no blurring by accident -- which
    # means the only way to have no blurring is to say so, and until the switch
    # existed there was nowhere to say it.
    from app.services import capability_switches

    if not await capability_switches.enabled("redaction"):
        return (None, False, False)

    provider = await resolve_provider()
    if not provider:
        return (None, False, False)
    return (provider, await _flag(REDACT_FACES_KEY, True), await _flag(REDACT_PLATES_KEY, True))


# --------------------------------------------------------------------------
# detection, per provider
# --------------------------------------------------------------------------

async def detect(provider: str, raw: bytes, width: int, height: int,
                 faces: bool, plates: bool) -> Optional[List[Box]]:
    """Every detection this provider can make on one image. Never raises.

    `[]` and `None` mean different things, and conflating them is how unblurred
    faces got published:

        []    the detector answered, and there is nobody in this photo
        None  the detector could not answer

    This used to return `[]` for both. `_usable()` catches *missing* credentials,
    but only Google's check reaches the vendor -- AWS and Azure are satisfied by
    the strings being present -- so credentials that are present and rejected
    (expired, rotated, revoked, wrong region, over quota, vendor outage) got past
    it. The call then raised, was caught here, and became "no faces found": the
    photo was stored as submitted, `skipped_reason` said `no-detections`, and the
    Test button stayed green. A town's Azure key could lapse on a Tuesday and
    every photo after it published a resident's face.

    The caller decides what to do about `None`; this only stops pretending it did
    not happen. Logged at warning rather than info for the same reason -- a
    detector that cannot answer is not routine.
    """
    found: List[Box] = []
    try:
        if provider == "google":
            found = await _google_detect(raw, width, height, faces, plates)
        elif provider == "aws":
            found = await _aws_detect(raw, faces, plates)
        elif provider == "azure":
            found = await _azure_detect(raw, width, height, faces, plates)
        elif provider == "local":
            found = await _local_detect(raw, width, height, faces, plates)
        else:
            return None
    except Exception as exc:
        from app.core.sanitize import sanitize_for_log
        logger.warning("[Redaction] %s could not detect: %s", provider, sanitize_for_log(str(exc)))
        # A cloud detector over its quota is exactly the failure the caller's
        # degrade-to-local path was built for -- the photo still gets blurred,
        # which is why nobody in the portal ever notices. The health note is
        # the only trace: the town selected cloud detection and is silently
        # getting the weaker on-server kind. Local has no quota to hit.
        if provider != "local":
            from app.services import connector_health
            await connector_health.note_quota_failure("redaction", exc, provider=provider)
        return None
    return found


# ---- Google Vision --------------------------------------------------------

def google_features(faces: bool, plates: bool, safesearch: bool = False) -> List[Dict[str, Any]]:
    """The feature list for one Vision annotate call."""
    features: List[Dict[str, Any]] = []
    if safesearch:
        features.append({"type": "SAFE_SEARCH_DETECTION"})
    if faces:
        features.append({"type": "FACE_DETECTION", "maxResults": 50})
    if plates:
        features.append({"type": "TEXT_DETECTION"})
    return features


def boxes_from_vision(payload: Dict[str, Any], width: int, height: int,
                      faces: bool, plates: bool) -> List[Box]:
    """Both detection kinds out of one shared annotate response."""
    out: List[Box] = []
    if faces:
        out += google_faces(payload, width, height)
    if plates:
        out += google_text_plates(payload, width, height)
    return out


async def _google_detect(raw: bytes, width: int, height: int,
                         faces: bool, plates: bool) -> List[Box]:
    from app.services.cloud_moderation import vision_annotate

    features = google_features(faces, plates)
    if not features:
        return []
    return boxes_from_vision(await vision_annotate(raw, features), width, height, faces, plates)


# ---- AWS Rekognition ------------------------------------------------------

async def _aws_detect(raw: bytes, faces: bool, plates: bool) -> List[Box]:
    from app.services.cloud_moderation import _aws_kwargs

    kwargs = await _aws_kwargs()
    if not kwargs:
        return []

    def _sync() -> List[Box]:
        import boto3
        client = boto3.client("rekognition", **kwargs)
        out: List[Box] = []
        if faces:
            out += rekognition_faces(client.detect_faces(Image={"Bytes": raw}, Attributes=["DEFAULT"]))
        if plates:
            out += rekognition_text_plates(client.detect_text(Image={"Bytes": raw}))
        return out

    return await asyncio.get_event_loop().run_in_executor(None, _sync)


# ---- Azure ----------------------------------------------------------------
#
# Azure needs two separate resources: AI Face for faces and AI Vision for the
# text read. They are not the same endpoint and not the same key.
#
# Face carries an access caveat the other providers do not. Microsoft placed the
# Face API behind a Limited Access registration under its Responsible AI
# Standard; detection (returning rectangles) is the least restricted operation,
# but a subscription still has to be approved before the resource will serve
# traffic. A town that cannot get through that form should pick another
# provider, and the admin UI says so rather than letting them discover it from
# a 403.

async def _azure_face_creds():
    from app.services.secret_manager import get_secret
    endpoint = ((await get_secret("AZURE_FACE_ENDPOINT")) or "").rstrip("/")
    key = await get_secret("AZURE_FACE_KEY")
    return endpoint, key


async def _azure_vision_creds():
    from app.services.secret_manager import get_secret
    endpoint = ((await get_secret("AZURE_VISION_ENDPOINT")) or "").rstrip("/")
    key = await get_secret("AZURE_VISION_KEY")
    return endpoint, key


async def _azure_detect(raw: bytes, width: int, height: int,
                        faces: bool, plates: bool) -> List[Box]:
    import httpx

    out: List[Box] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=6.0)) as client:
        if faces:
            endpoint, key = await _azure_face_creds()
            if endpoint and key:
                resp = await client.post(
                    f"{endpoint}/face/v1.0/detect",
                    params={"returnFaceId": "false", "detectionModel": "detection_03"},
                    headers={"Ocp-Apim-Subscription-Key": key,
                             "Content-Type": "application/octet-stream"},
                    content=raw,
                )
                resp.raise_for_status()
                out += azure_faces(resp.json(), width, height)
        if plates:
            endpoint, key = await _azure_vision_creds()
            if endpoint and key:
                resp = await client.post(
                    f"{endpoint}/computervision/imageanalysis:analyze",
                    params={"api-version": "2024-02-01", "features": "read"},
                    headers={"Ocp-Apim-Subscription-Key": key,
                             "Content-Type": "application/octet-stream"},
                    content=raw,
                )
                resp.raise_for_status()
                out += azure_text_plates(resp.json(), width, height)
    return out


# ---- local (OpenCV, offline) ----------------------------------------------
#
# The free path. Both cascades ship inside opencv-python itself (Apache 2.0),
# so there is no model download, no licence question, and no per-image cost.
#
# The quality gap is real and worth stating plainly: Haar cascades were state of
# the art in 2001. Frontal faces at a reasonable size are found; profiles,
# tilted heads and small faces are missed at a rate the cloud detectors do not
# approach. The plate cascade was trained on Russian plates and fires on the
# rectangular high-contrast shape rather than on anything US-specific, so it
# finds many US plates and also many rectangular signs.
#
# It is here because "the photo never leaves the building" is worth a lot to
# some deployments, and because a town with no cloud account at all should still
# be able to turn something on.

# Confidence for a plate both signals agree on, and for one that only the text
# heuristic found. A cascade hit alone is not enough to blur -- see
# combine_plate_signals.
PLATE_BOTH_CONFIDENCE = 0.9
CASCADE_ONLY_CONFIDENCE = 0.0


def combine_plate_signals(cascade: Sequence[Box],
                          words: Sequence[Tuple[str, Box, float]]) -> List[Box]:
    """Require a plate-shaped region *and* plate-shaped text inside it.

    Choosing the free plate detector came down to which failure to accept.

    The Haar cascade alone fires on any bright rectangle with dark structure
    inside: road signs, house-number plaques, mailboxes, the back of a truck. On
    a streetscape photo that is unusable -- it smears the context that made the
    report actionable.

    Tesseract alone (Apache 2.0, and the only OCR engine with a licence that can
    ship inside an MIT product without dragging AGPL in behind it -- which is
    what disqualifies OpenALPR and every Ultralytics YOLO plate model) fires on
    any short alphanumeric string. House numbers, bus routes, phone numbers on a
    contractor's van.

    The two are wrong about different things, so the intersection is much
    stronger than either: a region that is plate-shaped *and* contains
    plate-shaped text is very rarely anything else. That is the accepted
    detection, at high confidence.

    A plate-like word with no cascade region around it is still taken, on the
    text heuristic alone, because the cascade misses angled and shadowed plates
    badly and recall matters. A cascade region with no plate-like text inside it
    is dropped outright -- that is the road-sign case, and it is the whole reason
    for the AND.
    """
    out: List[Box] = []
    scored = [(text, box, ocr) for text, box, ocr in words
              if plate_like(text, box) * max(0.0, min(1.0, ocr)) > 0]

    for region in cascade:
        inside = [(t, b, c) for t, b, c in scored if _overlap(region, b) >= 0.5]
        if inside:
            out.append(Box(region.left, region.top, region.right, region.bottom,
                           "plate", PLATE_BOTH_CONFIDENCE))

    for text, box, ocr in scored:
        if any(_overlap(region, box) >= 0.5 for region in cascade):
            continue  # already covered by the region box, which is the better shape
        score = plate_like(text, box) * max(0.0, min(1.0, ocr))
        if score >= PLATE_MIN_CONFIDENCE:
            out.append(Box(box.left, box.top, box.right, box.bottom, "plate", score))

    return out


def _tesseract_words(image, width: int, height: int) -> List[Tuple[str, Box, float]]:
    """OCR one image into (text, fractional box, confidence) triples.

    Returns [] rather than raising when Tesseract is not installed -- it is a
    system binary, not a pip package, so a deployment can perfectly well be
    missing it, and that must degrade to "no plates found" rather than breaking
    intake.
    """
    try:
        import pytesseract
    except Exception:
        return []
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception:
        # Almost always the binary being absent from PATH.
        logger.info("[Redaction] tesseract unavailable; plate text signal off")
        return []

    words: List[Tuple[str, Box, float]] = []
    for i, text in enumerate(data.get("text", [])):
        if not (text or "").strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if conf < 0:            # Tesseract uses -1 for non-word rows
            continue
        left, top = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]
        if w <= 0 or h <= 0:
            continue
        box = Box(left / width, top / height,
                  (left + w) / width, (top + h) / height, "plate", 0.0).clamped()
        words.append((text, box, conf / 100.0))
    return words


def _cascade(name: str):
    import cv2
    path = cv2.data.haarcascades + name
    classifier = cv2.CascadeClassifier(path)
    return None if classifier.empty() else classifier


def _detect_local_sync(raw: bytes, faces: bool, plates: bool) -> List[Box]:
    import cv2
    import numpy as np

    buffer = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        return []
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        return []

    # Haar is O(pixels) with a large constant. Downscaling to 1024px wide keeps
    # a typical phone photo under a second on one core, and the boxes come back
    # fractional anyway so the scale factor never has to be undone.
    scale = min(1.0, 1024.0 / width)
    if scale < 1.0:
        image = cv2.resize(image, (int(width * scale), int(height * scale)))
    small_h, small_w = image.shape[:2]
    grey = cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))

    out: List[Box] = []

    def run(classifier, kind: str, min_size: Tuple[int, int], confidence: float):
        if classifier is None:
            return
        for (x, y, w, h) in classifier.detectMultiScale(
            grey, scaleFactor=1.1, minNeighbors=5, minSize=min_size
        ):
            out.append(Box(x / small_w, y / small_h,
                           (x + w) / small_w, (y + h) / small_h, kind, confidence).clamped())

    if faces:
        # Frontal first, then profile, and merge_boxes collapses the overlap for
        # a three-quarter view that both happen to catch.
        run(_cascade("haarcascade_frontalface_default.xml"), "face", (24, 24), 0.6)
        run(_cascade("haarcascade_profileface.xml"), "face", (24, 24), 0.5)

    if plates:
        # The cascade proposes plate-shaped regions and Tesseract confirms there
        # is plate-shaped text in them. Neither alone is usable; see
        # combine_plate_signals for why the AND is the whole design.
        regions: List[Box] = []
        classifier = _cascade("haarcascade_russian_plate_number.xml")
        if classifier is not None:
            for (x, y, w, h) in classifier.detectMultiScale(
                grey, scaleFactor=1.1, minNeighbors=5, minSize=(40, 14)
            ):
                regions.append(Box(x / small_w, y / small_h,
                                   (x + w) / small_w, (y + h) / small_h,
                                   "plate", 0.0).clamped())
        # OCR the equalised grey image: Tesseract does markedly better on high
        # contrast than on a raw colour photo, and it is already computed.
        out += combine_plate_signals(regions, _tesseract_words(grey, small_w, small_h))

    return out


async def _local_detect(raw: bytes, width: int, height: int,
                        faces: bool, plates: bool) -> List[Box]:
    if not (faces or plates):
        return []
    return await asyncio.get_event_loop().run_in_executor(
        None, _detect_local_sync, raw, faces, plates)


# --------------------------------------------------------------------------
# is the chosen detector actually able to answer?
# --------------------------------------------------------------------------
#
# Every cloud detector returns an empty result when it has no credentials, and
# an empty result is also what a photo of an empty street returns. Callers
# cannot tell them apart -- `vision_annotate` says so in its own docstring:
# "Returns {} when Google is not configured, so callers can treat 'no
# credentials' the same as 'nothing detected'."
#
# For content moderation that conflation is tolerable. For redaction it is the
# difference between a photo with nobody in it and a photo of somebody's face
# published on a municipal website. `redact_image` already named this in its
# docstring -- "a town whose Vision credentials quietly expired will publish
# unblurred faces and only find out from the admin console" -- and concluded the
# honest fix was to surface it loudly.
#
# There is a better answer than surfacing it, which that note did not consider:
# fall back to the detector that needs no credentials. Local detection finds
# less than the cloud, but it runs anywhere with nothing configured, so the
# choice is no longer between publishing an unblurred face and refusing the
# resident's report. Degrade, blur what we can, and say so.


async def _usable(provider: str) -> bool:
    """Whether this detector has what it needs to answer. Never raises."""
    try:
        if provider == "local":
            import cv2  # noqa: F401
            return True
        if provider == "google":
            from app.services.cloud_moderation import _google_token_and_project
            token, _ = await _google_token_and_project()
            return bool(token)
        if provider == "aws":
            from app.services.cloud_moderation import _aws_kwargs
            return bool(await _aws_kwargs())
        if provider == "azure":
            face_endpoint, face_key = await _azure_face_creds()
            vision_endpoint, vision_key = await _azure_vision_creds()
            # Either half is enough to be worth calling: Face covers faces,
            # Vision covers plates, and a town may deliberately have only one
            # because Face is behind Microsoft's Limited Access review.
            return bool((face_endpoint and face_key) or (vision_endpoint and vision_key))
    except Exception as exc:
        from app.core.sanitize import sanitize_for_log
        logger.info("[Redaction] could not check %s credentials: %s",
                    provider, sanitize_for_log(str(exc)))
    return False


async def effective_provider(provider: str) -> Tuple[str, Optional[str]]:
    """(detector to actually use, the one it replaced or None).

    A cloud detector that cannot answer becomes local rather than nothing. The
    second element is what the caller records and what the health check reports,
    because a town that selected Azure and is silently running on OpenCV needs
    to be told -- it is getting worse detection than it thinks it paid for.
    """
    if await _usable(provider):
        return provider, None
    if provider != "local" and await _usable("local"):
        logger.warning(
            "[Redaction] %s has no usable credentials; falling back to on-server "
            "detection so photos are still blurred. Fix the credentials or change "
            "the provider on the Photo Redaction card.", provider,
        )
        return "local", provider
    # Local is unavailable too -- OpenCV missing from the image. Nothing can
    # blur, and that must not look like "no faces in this photo".
    logger.error(
        "[Redaction] no detector is usable (selected %s, and on-server detection "
        "is unavailable). Photos are being stored WITHOUT redaction.", provider,
    )
    return provider, provider


async def redact_image(media: str, provider: str, faces: bool, plates: bool) -> RedactionResult:
    """Redact one photo. Never raises.

    Fails *open* -- an unredactable photo is stored as submitted rather than
    dropped, because refusing the report punishes the resident for the town's
    misconfiguration.

    But "unredactable" is now much rarer than it was. A cloud detector with no
    usable credentials falls back to on-server detection rather than returning
    nothing, so the case this docstring used to describe -- "a town whose Vision
    credentials quietly expired will publish unblurred faces" -- no longer
    happens while OpenCV is present. When even that is unavailable the photo is
    stored unredacted, and `skipped_reason` says `no-detector` rather than
    `no-detections`, so the two are distinguishable by everything downstream.
    """
    decoded = _decode(media)
    if not decoded:
        return RedactionResult(media, skipped_reason="not-inline-image")
    raw, mime = decoded

    width, height = image_size(raw)
    if width <= 0 or height <= 0:
        return RedactionResult(media, skipped_reason="unreadable-image")

    provider, degraded_from = await effective_provider(provider)
    if degraded_from == provider:
        # Nothing can blur. Keep the photo -- dropping it helps nobody -- but do
        # not let this be reported as "we looked and there was no one there".
        cleaned = strip_exif(raw)
        payload = _encode(cleaned, "image/jpeg") if cleaned is not None else media
        return RedactionResult(payload, changed=False, skipped_reason="no-detector")

    found = await detect(provider, raw, width, height, faces, plates)

    # The detector failed *during* the call, which `effective_provider` above
    # cannot predict -- it can only see whether credentials are present. Try the
    # on-server detector before giving up, the same way a missing credential
    # would have.
    if found is None and provider != "local" and await _usable("local"):
        logger.warning(
            "[Redaction] %s failed on this photo; retrying with on-server "
            "detection so it is still blurred. If this repeats, the credentials "
            "are being rejected -- check the Photo Redaction card.", provider,
        )
        found = await detect("local", raw, width, height, faces, plates)

    if found is None:
        # Nothing could look at it. Keep the photo, but do not let this be
        # recorded as "we looked and there was nobody there".
        cleaned = strip_exif(raw)
        payload = _encode(cleaned, "image/jpeg") if cleaned is not None else media
        return RedactionResult(payload, changed=False, skipped_reason="no-detector")

    if not found:
        # No faces, but the EXIF is still worth removing -- see strip_exif.
        cleaned = strip_exif(raw)
        if cleaned is not None:
            return RedactionResult(_encode(cleaned, "image/jpeg"), changed=False,
                                   skipped_reason="no-detections")
        return RedactionResult(media, skipped_reason="no-detections")

    padded = [b.padded(FACE_PADDING if b.kind == "face" else PLATE_PADDING) for b in found]
    boxes = merge_boxes(padded)

    blurred = blur_regions(raw, boxes)
    if blurred is None:
        return RedactionResult(media, skipped_reason="blur-failed")

    return RedactionResult(
        _encode(blurred, "image/jpeg" if mime == "image/png" else mime),
        faces=sum(1 for b in boxes if b.kind == "face"),
        plates=sum(1 for b in boxes if b.kind == "plate"),
        changed=True,
    )


async def redact_media(media: Optional[List[str]]) -> BatchResult:
    """Redact a submission's photos before they are persisted.

    Called from the intake path, so it is on the resident's latency budget. The
    photos are processed concurrently for that reason -- three sequential Vision
    round trips is a visible pause on a phone, three parallel ones is one round
    trip's worth.
    """
    items = [m for m in (media or []) if isinstance(m, str)][:MAX_IMAGES]
    if not items:
        return BatchResult(media=list(media or []))

    provider, faces, plates = await settings()
    if not provider or not (faces or plates):
        # No detection, but the metadata block still goes -- see exif_only. The
        # off switch is about blurring, not about publishing GPS coordinates.
        return BatchResult(media=[exif_only(m) for m in items]
                           + list((media or [])[len(items):]))

    results = await asyncio.gather(
        *(redact_image(m, provider, faces, plates) for m in items),
        return_exceptions=True,
    )

    out = BatchResult()
    for original, result in zip(items, results):
        if isinstance(result, Exception) or not isinstance(result, RedactionResult):
            logger.warning("[Redaction] image failed", exc_info=isinstance(result, Exception))
            out.media.append(original)
            out.skipped.append("error")
            continue
        out.media.append(result.media)
        out.faces += result.faces
        out.plates += result.plates
        if not result.changed and result.skipped_reason not in ("", "no-detections"):
            out.skipped.append(result.skipped_reason)

    # Anything past MAX_IMAGES was never a candidate; carry it through unchanged
    # so this function cannot silently drop a photo.
    out.media.extend(m for m in (media or [])[len(items):])
    return out


async def _google_screen_and_redact_one(media: str, faces: bool, plates: bool):
    """SafeSearch and the detections for one photo, in a single Vision call.

    This is the whole point of the combined path. Moderation and redaction both
    need Vision to look at the same bytes, and asking twice doubles the round
    trips a resident waits through for no benefit -- Vision bills per feature,
    not per request, so the batched call costs exactly the same.
    """
    from app.services.cloud_moderation import safesearch_from_payload, vision_annotate

    decoded = _decode(media)
    if not decoded:
        return ModerationResult(), RedactionResult(media, skipped_reason="not-inline-image")
    raw, mime = decoded

    width, height = image_size(raw)
    if width <= 0 or height <= 0:
        return ModerationResult(), RedactionResult(media, skipped_reason="unreadable-image")

    try:
        payload = await vision_annotate(raw, google_features(faces, plates, safesearch=True))
    except Exception as exc:
        from app.core.sanitize import sanitize_for_log
        logger.info("[Redaction] vision unavailable: %s", sanitize_for_log(str(exc)))
        # Same flag as detect(): this combined moderation+redaction call is the
        # other runtime path into Vision, and a 429 here is the same exhausted
        # quota an administrator needs to hear about exactly once per window.
        from app.services import connector_health
        await connector_health.note_quota_failure("redaction", exc, provider="google")
        return ModerationResult(), RedactionResult(media, skipped_reason="provider-error")

    verdict = safesearch_from_payload(payload)
    found = boxes_from_vision(payload, width, height, faces, plates)

    # An image about to be rejected as explicit is not worth blurring; the
    # caller raises before anything is stored.
    if verdict.should_block:
        return verdict, RedactionResult(media, skipped_reason="blocked")

    if not found:
        cleaned = strip_exif(raw)
        if cleaned is not None:
            return verdict, RedactionResult(_encode(cleaned, "image/jpeg"),
                                            skipped_reason="no-detections")
        return verdict, RedactionResult(media, skipped_reason="no-detections")

    boxes = merge_boxes([b.padded(FACE_PADDING if b.kind == "face" else PLATE_PADDING)
                         for b in found])
    blurred = blur_regions(raw, boxes)
    if blurred is None:
        return verdict, RedactionResult(media, skipped_reason="blur-failed")

    return verdict, RedactionResult(
        _encode(blurred, "image/jpeg" if mime == "image/png" else mime),
        faces=sum(1 for b in boxes if b.kind == "face"),
        plates=sum(1 for b in boxes if b.kind == "plate"),
        changed=True,
    )


async def screen_and_redact(media: Optional[List[str]]) -> Tuple[ModerationResult, BatchResult]:
    """The single photo-intake entry point: moderate and redact together.

    Google is folded into one request per photo because Vision does SafeSearch,
    faces and text as features of the same annotate call. AWS and Azure cannot
    be folded -- Rekognition's DetectModerationLabels, DetectFaces and DetectText
    are three separate APIs, as are Azure's Content Safety, Face and Vision
    resources -- so those providers run the existing screen-then-redact pair
    unchanged. Same behaviour on every provider, one fewer round trip on the one
    that supports it.

    Returns the strongest moderation verdict across the photos alongside the
    redacted media. The caller must check `should_block` and raise *before*
    using the media, because a blocked submission is never stored.
    """
    from app.services.content_moderation import screen_images

    items = [m for m in (media or []) if isinstance(m, str)][:MAX_IMAGES]
    provider, faces, plates = await settings()

    # Either half being off means there is nothing to share, so take the plain
    # path for whichever half is still on. The EXIF strip is not part of either
    # half: it needs no detector, so the off switch must not carry it away --
    # see exif_only.
    if not items or not provider or not (faces or plates):
        stripped = BatchResult(media=[exif_only(m) for m in items]
                               + list((media or [])[len(items):]))
        return await screen_images(media or []), stripped

    if provider != "google":
        return await screen_images(media or []), await redact_media(media)

    results = await asyncio.gather(
        *(_google_screen_and_redact_one(m, faces, plates) for m in items),
        return_exceptions=True,
    )

    rank = {"none": 0, "mild": 1, "severe": 2}
    strongest = ModerationResult()
    out = BatchResult()
    for original, result in zip(items, results):
        if isinstance(result, Exception) or not isinstance(result, tuple):
            logger.warning("[Redaction] image failed", exc_info=isinstance(result, Exception))
            out.media.append(original)
            out.skipped.append("error")
            continue
        verdict, redaction = result
        if rank[verdict.severity] > rank[strongest.severity]:
            strongest = verdict
        out.media.append(redaction.media)
        out.faces += redaction.faces
        out.plates += redaction.plates
        if not redaction.changed and redaction.skipped_reason not in ("", "no-detections", "blocked"):
            out.skipped.append(redaction.skipped_reason)

    out.media.extend(m for m in (media or [])[len(items):])
    return strongest, out
