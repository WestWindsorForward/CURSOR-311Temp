"""Tests for face / licence-plate redaction.

The network calls and the Pillow blur are not the interesting part. What is
worth pinning is the layer between them: four providers that describe a
rectangle four different ways, and a plate heuristic that decides whether to
destroy part of a public record.

Two properties carry most of the weight:

  * a detection must never come back outside the image, because the padding
    step deliberately pushes boxes past the edge and PIL will not crop there;
  * `plate_like` must not fire on street signage, because a blurred STOP sign
    or house number can make an otherwise actionable report useless.
"""

import pytest

from app.services.image_redaction import (
    Box,
    azure_faces,
    azure_text_plates,
    google_faces,
    google_text_plates,
    merge_boxes,
    plate_like,
    rekognition_faces,
    rekognition_text_plates,
)

WIDE = Box(0.4, 0.6, 0.5, 0.63, "plate")      # ~3.3:1, lower half — plate-shaped
TALL = Box(0.4, 0.1, 0.44, 0.3, "plate")      # taller than wide, upper frame


# ---- geometry ---------------------------------------------------------------

def test_a_box_is_clamped_into_the_image():
    box = Box(-0.4, -0.2, 1.9, 1.3).clamped()
    assert (box.left, box.top, box.right, box.bottom) == (0.0, 0.0, 1.0, 1.0)


def test_a_reversed_box_is_straightened_rather_than_producing_negative_area():
    """Right-to-left coordinates come back from at least one provider when a
    face touches the frame edge. Negative width silently blurs nothing."""
    box = Box(0.8, 0.9, 0.2, 0.1).clamped()
    assert box.right > box.left and box.bottom > box.top
    assert box.area > 0


def test_padding_grows_the_box_but_cannot_escape_the_image():
    """Padding exists because a face box is drawn tight and leaves hairline and
    jaw legible. It must still be croppable afterwards."""
    padded = Box(0.02, 0.02, 0.1, 0.1).padded(2.0)
    assert padded.area > Box(0.02, 0.02, 0.1, 0.1).area
    assert padded.left >= 0 and padded.top >= 0
    assert padded.right <= 1 and padded.bottom <= 1


def test_pixel_conversion_never_produces_a_zero_width_crop():
    """A sliver box rounds both edges to the same pixel, and PIL raises on a
    zero-area crop."""
    left, top, right, bottom = Box(0.5, 0.5, 0.5001, 0.5001).to_pixels(100, 100)
    assert right > left and bottom > top


def test_overlapping_boxes_merge_into_their_union():
    merged = merge_boxes([Box(0.1, 0.1, 0.5, 0.5), Box(0.2, 0.2, 0.6, 0.6)])
    assert len(merged) == 1
    assert merged[0].left == pytest.approx(0.1)
    assert merged[0].right == pytest.approx(0.6)


def test_a_contained_box_merges_even_though_its_iou_is_low():
    """The reason _overlap divides by the smaller box rather than the union: a
    small face box inside a large one is the same face, and IoU would keep
    both, blurring the region twice."""
    merged = merge_boxes([Box(0.0, 0.0, 1.0, 1.0), Box(0.45, 0.45, 0.5, 0.5)])
    assert len(merged) == 1


def test_separate_faces_stay_separate():
    """Two people in one photo must count as two, or the number shown to staff
    is wrong."""
    merged = merge_boxes([Box(0.0, 0.0, 0.2, 0.2), Box(0.7, 0.7, 0.9, 0.9)])
    assert len(merged) == 2


def test_a_face_and_a_plate_in_the_same_place_are_not_merged():
    """They carry different padding and are counted separately; collapsing them
    would report one as the other."""
    merged = merge_boxes([Box(0.1, 0.1, 0.5, 0.5, "face"), Box(0.1, 0.1, 0.5, 0.5, "plate")])
    assert len(merged) == 2


# ---- face parsers -----------------------------------------------------------

def test_vision_faces_are_normalised_out_of_pixels():
    payload = {"responses": [{"faceAnnotations": [{
        "detectionConfidence": 0.98,
        "boundingPoly": {"vertices": [{"x": 100, "y": 50}, {"x": 300, "y": 50},
                                      {"x": 300, "y": 250}, {"x": 100, "y": 250}]},
    }]}]}
    box = google_faces(payload, 400, 400)[0]
    assert box.left == pytest.approx(0.25)
    assert box.top == pytest.approx(0.125)
    assert box.right == pytest.approx(0.75)


def test_vision_treats_a_missing_vertex_coordinate_as_zero():
    """Vision omits `x`/`y` entirely when the value is 0. Reading that as a
    missing box would drop every face touching the left or top edge."""
    payload = {"responses": [{"faceAnnotations": [{
        "detectionConfidence": 0.9,
        "boundingPoly": {"vertices": [{"y": 10}, {"x": 80, "y": 10}, {"x": 80, "y": 90}, {"y": 90}]},
    }]}]}
    box = google_faces(payload, 100, 100)[0]
    assert box.left == pytest.approx(0.0)
    assert box.right == pytest.approx(0.8)


def test_a_low_confidence_face_is_still_blurred():
    """The threshold is deliberately low. A false blur costs a slightly worse
    photo; a missed face publishes someone's biometrics on a town website."""
    payload = {"responses": [{"faceAnnotations": [{
        "detectionConfidence": 0.4,
        "boundingPoly": {"vertices": [{"x": 0, "y": 0}, {"x": 50, "y": 0},
                                      {"x": 50, "y": 50}, {"y": 50}]},
    }]}]}
    assert len(google_faces(payload, 100, 100)) == 1


def test_rekognition_boxes_are_already_fractional():
    payload = {"FaceDetails": [{"Confidence": 99.4, "BoundingBox": {
        "Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.4}}]}
    box = rekognition_faces(payload)[0]
    assert box.right == pytest.approx(0.4)
    assert box.bottom == pytest.approx(0.6)


def test_azure_face_rectangles_are_normalised():
    box = azure_faces([{"faceRectangle": {"left": 50, "top": 25, "width": 100, "height": 100}}],
                      200, 200)[0]
    assert box.left == pytest.approx(0.25)
    assert box.bottom == pytest.approx(0.625)


@pytest.mark.parametrize("parse,args", [
    (google_faces, ({}, 100, 100)),
    (google_faces, ({"responses": []}, 100, 100)),
    (google_faces, ({"responses": [{}]}, 100, 100)),
    (rekognition_faces, ({},)),
    (rekognition_faces, ({"FaceDetails": None},)),
    (azure_faces, ([], 100, 100)),
    (azure_faces, (None, 100, 100)),
])
def test_an_empty_or_malformed_response_yields_no_boxes_rather_than_raising(parse, args):
    """A provider hiccup must not take down intake."""
    assert parse(*args) == []


def test_zero_dimensions_do_not_divide_by_zero():
    assert azure_faces([{"faceRectangle": {"left": 1, "top": 1, "width": 1, "height": 1}}], 0, 0) == []


# ---- the plate heuristic ----------------------------------------------------

@pytest.mark.parametrize("text", [
    "STOP", "YIELD", "ONE WAY", "SCHOOL", "DETOUR", "NO PARKING", "EXIT",
])
def test_common_signage_is_not_a_plate(text):
    """The failure that makes this feature unusable: smearing every sign in the
    photo, so staff cannot tell where the report is."""
    assert plate_like(text, WIDE) == 0.0


@pytest.mark.parametrize("text", ["MAIN", "ELM STREET", "HELLO", "PARK"])
def test_a_word_with_no_digits_is_not_a_plate(text):
    """The single strongest rule -- signs are words, plates are not."""
    assert plate_like(text, WIDE) == 0.0


@pytest.mark.parametrize("text", ["AB", "123", "SUPERLONGPLATE", "A1B2C3D4E5"])
def test_wrong_length_strings_are_rejected(text):
    """4-8 characters after stripping punctuation. US plates run 5-7; the window
    is one wider at each end for OCR dropping or splitting a character."""
    assert plate_like(text, WIDE) == 0.0


def test_an_eight_character_alphanumeric_is_accepted():
    """"A1B 2C3D9" strips to eight characters and is plate-plausible -- I first
    wrote this as a rejection case and the heuristic was right, not the test."""
    from app.services.image_redaction import PLATE_MIN_CONFIDENCE
    assert plate_like("A1B 2C3D9", WIDE) >= PLATE_MIN_CONFIDENCE


@pytest.mark.parametrize("text", ["ABC1234", "A12BCD", "XYZ 890", "M45KLT"])
def test_a_plate_shaped_string_in_a_plate_shaped_box_scores(text):
    from app.services.image_redaction import PLATE_MIN_CONFIDENCE
    assert plate_like(text, WIDE) >= PLATE_MIN_CONFIDENCE


def test_geometry_can_veto_a_plausible_string():
    """Same characters, wrong shape and wrong place in the frame. Overhead
    signage and shopfront numbering land here."""
    assert plate_like("ABC1234", TALL) < plate_like("ABC1234", WIDE)


def test_an_all_numeric_string_scores_below_an_alphanumeric_one():
    """A bare number is as likely a house number, and blurring the house number
    out of a pothole report destroys the thing that made it actionable."""
    assert plate_like("12345", WIDE) < plate_like("ABC1234", WIDE)


def test_a_text_region_covering_the_frame_is_not_a_plate():
    """A banner or shopfront. A plate is small in a streetscape photo."""
    banner = Box(0.05, 0.4, 0.95, 0.8, "plate")
    assert plate_like("ABC1234", banner) < plate_like("ABC1234", WIDE)


def test_a_multi_word_string_is_a_sign_not_a_plate():
    assert plate_like("ROUTE 22 EAST", WIDE) == 0.0


def test_the_score_is_always_a_probability():
    for text in ["", "ABC1234", "STOP", "!!!!", "1", "A" * 40]:
        for box in (WIDE, TALL, Box(0, 0, 1, 1, "plate")):
            assert 0.0 <= plate_like(text, box) <= 1.0


# ---- plate parsers ----------------------------------------------------------

def _vision_text(description, vertices):
    return {"responses": [{"textAnnotations": [
        # [0] is the whole-image aggregate that Vision always prepends.
        {"description": "ABC1234 STOP", "boundingPoly": {"vertices": [
            {"x": 0, "y": 0}, {"x": 1000, "y": 0}, {"x": 1000, "y": 1000}, {"x": 0, "y": 1000}]}},
        {"description": description, "boundingPoly": {"vertices": vertices}},
    ]}]}


def test_visions_whole_image_aggregate_is_skipped():
    """textAnnotations[0] spans the entire photo. Treating it as a detection
    blurs the whole image the moment any plate-shaped string appears."""
    payload = _vision_text("ABC1234", [{"x": 400, "y": 700}, {"x": 600, "y": 700},
                                       {"x": 600, "y": 760}, {"x": 400, "y": 760}])
    boxes = google_text_plates(payload, 1000, 1000)
    assert len(boxes) == 1
    assert boxes[0].area < 0.1


def test_vision_signage_produces_no_plate_box():
    payload = _vision_text("STOP", [{"x": 400, "y": 700}, {"x": 600, "y": 700},
                                    {"x": 600, "y": 760}, {"x": 400, "y": 760}])
    assert google_text_plates(payload, 1000, 1000) == []


def test_rekognition_uses_words_not_lines():
    """A LINE glues the plate to the state name and the dealer frame, breaking
    both the character-count and aspect-ratio tests."""
    geometry = {"BoundingBox": {"Left": 0.4, "Top": 0.7, "Width": 0.2, "Height": 0.06}}
    payload = {"TextDetections": [
        {"Type": "LINE", "DetectedText": "NEW JERSEY ABC1234", "Confidence": 99, "Geometry": geometry},
        {"Type": "WORD", "DetectedText": "ABC1234", "Confidence": 99, "Geometry": geometry},
    ]}
    assert len(rekognition_text_plates(payload)) == 1


def test_low_ocr_confidence_gates_the_geometry_score():
    """Otherwise a half-read smudge in a plate-shaped box passes on shape
    alone."""
    geometry = {"BoundingBox": {"Left": 0.4, "Top": 0.7, "Width": 0.2, "Height": 0.06}}
    payload = {"TextDetections": [
        {"Type": "WORD", "DetectedText": "ABC1234", "Confidence": 20, "Geometry": geometry}]}
    assert rekognition_text_plates(payload) == []


def test_azure_read_words_are_parsed():
    payload = {"readResult": {"blocks": [{"lines": [{"words": [
        {"text": "ABC1234", "confidence": 0.97, "boundingPolygon": [
            {"x": 400, "y": 700}, {"x": 600, "y": 700},
            {"x": 600, "y": 760}, {"x": 400, "y": 760}]},
    ]}]}]}}
    assert len(azure_text_plates(payload, 1000, 1000)) == 1


@pytest.mark.parametrize("parse,args", [
    (google_text_plates, ({}, 100, 100)),
    (rekognition_text_plates, ({},)),
    (azure_text_plates, ({}, 100, 100)),
    (azure_text_plates, ({"readResult": {"blocks": None}}, 100, 100)),
])
def test_plate_parsers_tolerate_junk(parse, args):
    assert parse(*args) == []


# ---- the free plate detector: cascade AND text ------------------------------
#
# The design question was which failure to accept. The cascade fires on any
# bright rectangle (road signs, house plaques, the back of a truck); Tesseract
# fires on any short alphanumeric string (house numbers, bus routes). They are
# wrong about different things, so the intersection is much stronger than
# either, and the road-sign case is what the AND exists to kill.

from app.services.image_redaction import (  # noqa: E402
    CASCADE_ONLY_CONFIDENCE,
    PLATE_BOTH_CONFIDENCE,
    combine_plate_signals,
    google_features,
)

PLATE_REGION = Box(0.38, 0.58, 0.52, 0.65, "plate")
FAR_AWAY = Box(0.05, 0.05, 0.15, 0.09, "plate")


def test_a_region_with_plate_text_inside_it_is_the_strong_case():
    inside = Box(0.40, 0.60, 0.50, 0.635, "plate")
    boxes = combine_plate_signals([PLATE_REGION], [("ABC1234", inside, 0.92)])
    assert len(boxes) == 1
    assert boxes[0].confidence == PLATE_BOTH_CONFIDENCE
    # The region box is kept, not the tighter text box: it covers the whole
    # plate rather than just the characters.
    assert boxes[0].left == pytest.approx(PLATE_REGION.left)


def test_a_cascade_region_with_no_plate_text_is_dropped():
    """The road sign. This is the entire reason for requiring both signals --
    the cascade alone would smear every rectangular sign in the photo."""
    sign_text = Box(0.40, 0.60, 0.50, 0.635, "plate")
    assert combine_plate_signals([PLATE_REGION], [("STOP", sign_text, 0.99)]) == []
    assert CASCADE_ONLY_CONFIDENCE == 0.0


def test_a_cascade_region_with_no_text_at_all_is_dropped():
    assert combine_plate_signals([PLATE_REGION], []) == []


def test_plate_text_with_no_cascade_region_is_still_taken():
    """Recall matters: the cascade misses angled and shadowed plates badly, so
    the text heuristic alone is allowed to fire -- at its own lower score."""
    boxes = combine_plate_signals([], [("ABC1234", WIDE, 0.95)])
    assert len(boxes) == 1
    assert boxes[0].confidence < PLATE_BOTH_CONFIDENCE


def test_text_inside_a_region_is_not_also_emitted_separately():
    """Otherwise the same plate is counted twice and blurred twice."""
    inside = Box(0.40, 0.60, 0.50, 0.635, "plate")
    assert len(combine_plate_signals([PLATE_REGION], [("ABC1234", inside, 0.92)])) == 1


def test_plate_text_elsewhere_in_the_frame_is_not_absorbed_by_a_region():
    """Two vehicles. The region confirms one; the other must survive on text."""
    inside = Box(0.40, 0.60, 0.50, 0.635, "plate")
    # A genuinely separate box -- WIDE sits inside PLATE_REGION, so using it
    # here tested absorption rather than survival.
    other_vehicle = Box(0.70, 0.60, 0.82, 0.635, "plate")
    boxes = combine_plate_signals([PLATE_REGION], [("ABC1234", inside, 0.92),
                                                   ("XYZ890", other_vehicle, 0.93)])
    assert len(boxes) == 2


def test_low_ocr_confidence_cannot_confirm_a_region():
    inside = Box(0.40, 0.60, 0.50, 0.635, "plate")
    assert combine_plate_signals([PLATE_REGION], [("ABC1234", inside, 0.0)]) == []


def test_no_signals_at_all_yields_nothing():
    assert combine_plate_signals([], []) == []


# ---- folding the Vision call ------------------------------------------------

def test_safesearch_rides_along_with_the_detection_features():
    """Vision bills per feature but charges one round trip for the batch, so
    moderation and redaction must not issue two separate annotate calls."""
    types = [f["type"] for f in google_features(True, True, safesearch=True)]
    assert types == ["SAFE_SEARCH_DETECTION", "FACE_DETECTION", "TEXT_DETECTION"]


def test_only_the_requested_features_are_asked_for():
    """Each feature is billed, so plates being off must not silently pay for
    TEXT_DETECTION."""
    assert [f["type"] for f in google_features(True, False)] == ["FACE_DETECTION"]
    assert [f["type"] for f in google_features(False, True)] == ["TEXT_DETECTION"]
    assert google_features(False, False) == []


def test_the_moderation_half_reads_out_of_the_shared_response():
    """The folded call has to yield the same verdict the standalone SafeSearch
    call did, or folding changed behaviour."""
    from app.services.cloud_moderation import safesearch_from_payload
    payload = {"responses": [{"safeSearchAnnotation": {"adult": "VERY_LIKELY"},
                              "faceAnnotations": []}]}
    assert safesearch_from_payload(payload).should_block


def test_a_response_with_no_safesearch_block_is_unflagged():
    from app.services.cloud_moderation import safesearch_from_payload
    assert not safesearch_from_payload({}).flagged
    assert not safesearch_from_payload({"responses": [{}]}).flagged


# ---- provider dispatch ------------------------------------------------------
#
# Folding Google's two calls into one is only safe if AWS and Azure, which
# genuinely cannot be folded (Rekognition's DetectModerationLabels/DetectFaces/
# DetectText are three APIs, as are Azure's Content Safety / Face / Vision
# resources), still take the sequential path and behave identically.

import app.services.image_redaction as ir  # noqa: E402


@pytest.fixture
def spy(monkeypatch):
    calls = {"vision": 0, "screen_images": 0, "redact_media": 0}

    async def fake_screen_images(media):
        calls["screen_images"] += 1
        from app.services.content_moderation import ModerationResult
        return ModerationResult()

    async def fake_redact_media(media):
        calls["redact_media"] += 1
        return ir.BatchResult(media=list(media or []), faces=1)

    async def fake_annotate(raw, features):
        calls["vision"] += 1
        calls["features"] = [f["type"] for f in features]
        return {"responses": [{"safeSearchAnnotation": {}, "faceAnnotations": []}]}

    import app.services.content_moderation as cm
    monkeypatch.setattr(cm, "screen_images", fake_screen_images)
    monkeypatch.setattr(ir, "redact_media", fake_redact_media)
    monkeypatch.setattr("app.services.cloud_moderation.vision_annotate", fake_annotate)
    monkeypatch.setattr(ir, "image_size", lambda raw: (800, 600))
    monkeypatch.setattr(ir, "strip_exif", lambda raw: b"stripped")
    return calls


def _configure(monkeypatch, provider, faces=True, plates=False):
    async def fake_settings():
        return (provider, faces, plates)
    monkeypatch.setattr(ir, "settings", fake_settings)


PHOTO = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


@pytest.mark.asyncio
async def test_google_screens_and_redacts_in_one_call(spy, monkeypatch):
    _configure(monkeypatch, "google")
    await ir.screen_and_redact([PHOTO])
    assert spy["vision"] == 1, "moderation and redaction must share one annotate call"
    assert spy["screen_images"] == 0, "the separate moderation call must not also fire"
    assert spy["features"] == ["SAFE_SEARCH_DETECTION", "FACE_DETECTION"]


@pytest.mark.parametrize("provider", ["aws", "azure"])
@pytest.mark.asyncio
async def test_aws_and_azure_keep_the_sequential_path(spy, monkeypatch, provider):
    _configure(monkeypatch, provider)
    verdict, batch = await ir.screen_and_redact([PHOTO])
    assert spy["screen_images"] == 1
    assert spy["redact_media"] == 1
    assert spy["vision"] == 0, "non-Google providers must not touch Vision"
    assert batch.faces == 1


@pytest.mark.asyncio
async def test_redaction_off_still_moderates(spy, monkeypatch):
    """Turning the blur off must not turn image moderation off with it."""
    _configure(monkeypatch, "google", faces=False, plates=False)
    await ir.screen_and_redact([PHOTO])
    assert spy["screen_images"] == 1
    assert spy["vision"] == 0


@pytest.mark.asyncio
async def test_no_provider_still_moderates(spy, monkeypatch):
    _configure(monkeypatch, None)
    await ir.screen_and_redact([PHOTO])
    assert spy["screen_images"] == 1


@pytest.mark.asyncio
async def test_an_explicit_photo_blocks_and_is_not_blurred(monkeypatch, spy):
    """Blurring an image the caller is about to reject is wasted work, and the
    verdict must survive the fold."""
    async def explicit(raw, features):
        return {"responses": [{"safeSearchAnnotation": {"adult": "VERY_LIKELY"}}]}
    monkeypatch.setattr("app.services.cloud_moderation.vision_annotate", explicit)
    _configure(monkeypatch, "google")
    verdict, batch = await ir.screen_and_redact([PHOTO])
    assert verdict.should_block
    assert batch.media == [PHOTO]


@pytest.mark.asyncio
async def test_a_vision_outage_withholds_the_photo_rather_than_publishing_it(monkeypatch, spy):
    """The failure direction that matters.

    This used to fail *open*: Vision times out, the original bytes go straight
    into media_urls, a note lands in `skipped` where nobody reads it, and a
    municipal website publishes an unblurred face. Nothing ever looked at the
    photo, so "no detections" was never established -- the only honest thing to
    say is that we do not know.

    A photo we could not screen is now withheld instead: absent from `media`,
    present in `withheld` with its reason, and parked by the caller for staff.
    The report itself still goes through, which is the half that must not
    regress -- a Google outage must not cost a resident their pothole report.
    """
    async def boom(raw, features):
        raise RuntimeError("vision down")
    monkeypatch.setattr("app.services.cloud_moderation.vision_annotate", boom)
    _configure(monkeypatch, "google")
    verdict, batch = await ir.screen_and_redact([PHOTO])
    assert not verdict.should_block
    assert batch.media == []
    assert "provider-error" in batch.skipped
    assert batch.withheld == [{"media": PHOTO, "reason": "provider-error"}]


@pytest.mark.asyncio
async def test_exif_is_stripped_even_when_no_face_is_found(monkeypatch, spy):
    """The unconditional half: GPS to a few metres is on almost every phone
    photo, and removing it needs no detection at all."""
    _configure(monkeypatch, "google")
    _, batch = await ir.screen_and_redact([PHOTO])
    assert batch.media[0] != PHOTO
    assert batch.media[0].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_photos_past_the_limit_are_carried_through_not_dropped(monkeypatch, spy):
    _configure(monkeypatch, "google")
    _, batch = await ir.screen_and_redact([PHOTO] * 5)
    assert len(batch.media) == 5


@pytest.mark.asyncio
async def test_switching_redaction_off_does_not_switch_off_exif_stripping(monkeypatch):
    """The town's switch is about blurring. The GPS block is a separate
    exposure that needs no detector to remove -- see strip_exif -- so the off
    path must still re-encode the photo rather than hand it back untouched."""
    monkeypatch.setattr(ir, "strip_exif", lambda raw: b"stripped")
    _configure(monkeypatch, None)  # what settings() returns when the switch is off
    batch = await ir.redact_media([PHOTO])
    assert batch.media[0] != PHOTO
    assert batch.media[0].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_the_combined_path_strips_exif_when_redaction_is_off(spy, monkeypatch):
    _configure(monkeypatch, None)
    _, batch = await ir.screen_and_redact([PHOTO])
    assert spy["screen_images"] == 1, "moderation must still run"
    assert batch.media[0] != PHOTO
    assert batch.media[0].startswith("data:image/jpeg;base64,")
