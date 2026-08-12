"""Detections made on a downscaled copy must still cover the original.

Why the split exists
--------------------
A phone photo is 3-8MB and 4000px wide, and on a resident's uplink the bytes
going up to Google are most of the wall clock. Vision does not need them: a face
we can act on at 4000px is findable at 1600px. So the detector gets a small copy
and the blur is applied to the full-size original, which keeps the stored
photo's evidentiary resolution intact.

The risk that buys
------------------
The coordinates now come back in a different resolution than the one they get
applied in. The scale is linear and `Box` is fractional, so the arithmetic is
exact -- but the numbers on either side of it are integers. Vision returns
integer vertices measured on the small copy, and `to_pixels` truncates on the
way back out, and each of those rounds the wrong way about half the time. On a
4000px original one small-copy pixel is 2.5 real ones, so a box that comes back
a pixel short is a strip of jaw, or the top of a licence plate, left legible.

An oversized blur box costs a slightly worse photo. An undersized one publishes
part of the face the whole module exists to remove. So these tests pin the
rounding direction: outward, always, and by enough to swallow the integer error
at both ends.
"""

import pytest

pytest.importorskip("PIL.Image")

import io

from PIL import Image, ImageDraw

from app.services import image_redaction as ir


FULL_W, FULL_H = 4000, 3000

# A deliberately awkward rectangle: none of its edges land on a pixel boundary
# in the downscaled copy, so every one of them exercises the rounding rather
# than getting the right answer by luck.
FACE = (1007, 811, 1493, 1289)          # left, top, right, bottom, full-size px


def _photo(width=FULL_W, height=FULL_H):
    """A full-size image with a hard-edged patch where the face is.

    Flat grey everywhere else and pure white in the face rect: a Gaussian blur
    of a flat region is that same flat region, so any pixel that changes value
    after blurring is one the blur box actually reached. That makes "was the
    face covered" a pixel comparison rather than a geometry argument.
    """
    image = Image.new("RGB", (width, height), (120, 120, 120))
    if width >= FACE[2] and height >= FACE[3]:
        ImageDraw.Draw(image).rectangle(
            (FACE[0], FACE[1], FACE[2] - 1, FACE[3] - 1), fill=(255, 255, 255))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95)
    return out.getvalue()


def _vision_face(left, top, right, bottom, confidence=0.9):
    """An annotate response shaped like Vision's, with integer vertices.

    Integer because that is what Vision actually sends -- the truncation this
    test is about happens in Google's serializer, not ours.
    """
    return {"responses": [{"faceAnnotations": [{
        "detectionConfidence": confidence,
        "boundingPoly": {"vertices": [
            {"x": int(left), "y": int(top)},
            {"x": int(right), "y": int(top)},
            {"x": int(right), "y": int(bottom)},
            {"x": int(left), "y": int(bottom)},
        ]},
    }]}]}


def test_the_probe_is_smaller_and_the_original_is_not_touched():
    raw = _photo()
    probe, pw, ph = ir.probe_bytes(raw, FULL_W, FULL_H)

    assert max(pw, ph) == ir.SCREEN_MAX_EDGE
    assert (pw, ph) == (1600, 1200)
    assert len(probe) < len(raw)
    # The caller still holds the full-size bytes; probe_bytes is a copy, not a
    # replacement. If this ever stops holding, the blur starts being applied to
    # a 1600px image and the stored photo silently loses resolution.
    assert ir.image_size(raw) == (FULL_W, FULL_H)


def test_an_already_small_photo_is_sent_as_is():
    """No re-encode, no quality loss, no wasted CPU on the common small case."""
    raw = _photo(1200, 900)
    probe, pw, ph = ir.probe_bytes(raw, 1200, 900)
    assert probe is raw
    assert (pw, ph) == (1200, 900)
    assert ir.downscale(raw) is None


def test_bytes_that_are_not_an_image_fall_through_rather_than_raising():
    """A failure here must cost latency, never correctness."""
    assert ir.downscale(b"not an image") is None
    probe, pw, ph = ir.probe_bytes(b"not an image", 0, 0)
    assert probe == b"not an image"


def _detected_on_the_probe():
    """The face box as Vision would report it, measured on the 1600px copy.

    Truncating toward zero on every edge is the worst case for us: the box comes
    back *inside* the true face on the right and bottom, which is exactly the
    error that leaves a strip uncovered after the scale-up.
    """
    scale = 1600 / FULL_W
    return (int(FACE[0] * scale), int(FACE[1] * scale),
            int(FACE[2] * scale), int(FACE[3] * scale))


def test_a_box_detected_at_1600px_covers_the_same_region_at_full_size():
    """The load-bearing assertion: no part of the face escapes the scale-up."""
    probe_box = _detected_on_the_probe()
    payload = _vision_face(*probe_box)

    parsed = ir.google_faces(payload, 1600, 1200)
    assert len(parsed) == 1

    grown = ir.rescaled(parsed, 1600, 1200, FULL_W, FULL_H)
    left, top, right, bottom = grown[0].to_pixels(FULL_W, FULL_H)

    assert left <= FACE[0], f"blur starts {FACE[0] - left}px inside the face's left edge"
    assert top <= FACE[1], f"blur starts {FACE[1] - top}px inside the face's top edge"
    assert right >= FACE[2], f"blur stops {FACE[2] - right}px short of the right edge"
    assert bottom >= FACE[3], f"blur stops {FACE[3] - bottom}px short of the bottom edge"


def test_the_safety_margin_is_doing_the_work_and_not_decoration():
    """Without the outward rounding, the same box misses.

    If this ever stops failing, either SCALE_SAFETY_PX has become unnecessary or
    -- far more likely -- something upstream started rounding for us and the
    next change to it will silently reintroduce the leak.
    """
    parsed = ir.google_faces(_vision_face(*_detected_on_the_probe()), 1600, 1200)
    naive = parsed[0].to_pixels(FULL_W, FULL_H)
    assert naive[2] < FACE[2] or naive[3] < FACE[3], (
        "the truncated box already covered the face; this test's premise is gone"
    )


def test_the_margin_never_grows_the_box_by_more_than_a_few_pixels():
    """Outward, but not wildly outward.

    Over-covering is the safe direction and still has a cost -- the blur is what
    a resident sees, and a plate detector already over-fires on street signs. A
    couple of full-size pixels per side is the bound; anything approaching the
    FACE_PADDING quarter-box would mean the slack had become a second padding
    pass by accident.
    """
    parsed = ir.google_faces(_vision_face(*_detected_on_the_probe()), 1600, 1200)
    naive = parsed[0].to_pixels(FULL_W, FULL_H)
    grown = ir.rescaled(parsed, 1600, 1200, FULL_W, FULL_H)[0].to_pixels(FULL_W, FULL_H)

    per_side = FULL_W / 1600 * ir.SCALE_SAFETY_PX
    assert 0 < naive[0] - grown[0] <= per_side + 1
    assert 0 < grown[2] - naive[2] <= per_side + 1


def test_the_blur_actually_lands_on_the_face_in_the_full_size_image():
    """End to end, in pixels, on the bytes that would be stored.

    The geometry assertions above could both pass against a Box that never
    reaches `blur_regions` intact. This one blurs the real full-size image with
    the real scaled-up box and reads the result back: every pixel of the face
    must have moved off flat white, and the far corner of the photo must not
    have moved at all.
    """
    raw = _photo()
    parsed = ir.google_faces(_vision_face(*_detected_on_the_probe()), 1600, 1200)
    boxes = ir.merge_boxes([b.padded(ir.FACE_PADDING)
                            for b in ir.rescaled(parsed, 1600, 1200, FULL_W, FULL_H)])

    blurred = ir.blur_regions(raw, boxes)
    assert blurred is not None

    with Image.open(io.BytesIO(blurred)) as image:
        assert image.size == (FULL_W, FULL_H), "the stored photo lost resolution"
        # Corners of the true face rect, plus its centre. JPEG at quality 88
        # moves flat white by a point or two on its own, so "changed" is a real
        # departure rather than any difference at all.
        for x, y in ((FACE[0], FACE[1]), (FACE[2] - 1, FACE[1]),
                     (FACE[0], FACE[3] - 1), (FACE[2] - 1, FACE[3] - 1),
                     ((FACE[0] + FACE[2]) // 2, (FACE[1] + FACE[3]) // 2)):
            assert image.getpixel((x, y))[0] < 240, f"face pixel ({x}, {y}) survived the blur"
        # Nowhere near the face, so nothing should have happened here.
        assert image.getpixel((FULL_W - 5, FULL_H - 5))[0] > 100


def test_detections_are_left_alone_when_nothing_was_downscaled():
    """A photo already under the cap goes to Vision whole, so there is no
    rounding to compensate for and no reason to grow anything."""
    parsed = ir.google_faces(_vision_face(100, 100, 200, 200), 1200, 900)
    same = ir.rescaled(parsed, 1200, 900, 1200, 900)
    assert same[0].to_pixels(1200, 900) == parsed[0].to_pixels(1200, 900)
