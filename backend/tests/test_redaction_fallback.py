"""A detector that cannot answer must not look like a photo with nobody in it.

Every cloud detector returns an empty result when it has no credentials, and so
does a photo of an empty street. `vision_annotate` says as much in its own
docstring -- "callers can treat 'no credentials' the same as 'nothing
detected'." For content moderation that conflation is tolerable. For redaction
it is the difference between an empty street and somebody's face on a municipal
website.

`redact_image` had already named this: "a town whose Vision credentials quietly
expired will publish unblurred faces and only find out from the admin console",
and concluded the fix was to surface it loudly. Surfacing is not the best
available answer. Falling back to the detector that needs no credentials is --
it blurs less well than the cloud, but it blurs, and the choice is no longer
between publishing a face and refusing the resident's report.
"""

import asyncio

import pytest

pytest.importorskip("cryptography")

from app.services import image_redaction as ir


def _effective(usable):
    """effective_provider() with a given set of working detectors."""
    async def _fake(provider):
        return provider in usable

    original = ir._usable
    ir._usable = _fake
    try:
        return asyncio.run(ir.effective_provider(_effective.selected))
    finally:
        ir._usable = original


def _run(selected, usable):
    _effective.selected = selected
    return _effective(usable)


def test_a_cloud_detector_without_credentials_degrades_rather_than_disappearing():
    """The bug. Azure selected, no keys entered -- previously every photo was
    stored unblurred and the card stayed green."""
    provider, degraded_from = _run("azure", usable={"local"})
    assert provider == "local"
    assert degraded_from == "azure"


@pytest.mark.parametrize("cloud", ["google", "aws", "azure"])
def test_every_cloud_detector_degrades_the_same_way(cloud):
    provider, degraded_from = _run(cloud, usable={"local"})
    assert (provider, degraded_from) == ("local", cloud)


def test_a_working_detector_is_left_alone():
    """The fallback must not shadow a correctly configured cloud detector --
    it finds more than OpenCV does, which is why a town paid for it."""
    provider, degraded_from = _run("google", usable={"google", "local"})
    assert provider == "google"
    assert degraded_from is None


def test_local_selected_and_working_is_not_a_degradation():
    provider, degraded_from = _run("local", usable={"local"})
    assert provider == "local"
    assert degraded_from is None


def test_when_nothing_can_blur_it_is_reported_as_such():
    """OpenCV missing from the image too. The photo is still kept -- dropping it
    punishes the resident for the town's problem -- but this must not be
    reported as "we looked and there was nobody there"."""
    provider, degraded_from = _run("azure", usable=set())
    assert degraded_from == provider, "caller detects total failure by this equality"


def test_redact_image_distinguishes_no_detector_from_no_detections():
    """Everything downstream reads skipped_reason. `no-detections` means we
    looked; `no-detector` means we could not. Collapsing them is how the
    original bug stayed invisible."""
    import inspect
    source = inspect.getsource(ir.redact_image)
    assert '"no-detector"' in source
    assert '"no-detections"' in source


def test_the_fallback_is_actually_used_by_the_redaction_path():
    """A helper nothing calls is a comment."""
    import inspect
    assert "effective_provider(" in inspect.getsource(ir.redact_image)


def test_the_health_check_reports_a_degraded_detector():
    """Degrading contains the harm; it does not make it fine. A town paying for
    Azure and silently running on OpenCV is getting worse detection than it
    thinks, and should be told."""
    from app.services import proactive_health as ph
    import inspect

    source = inspect.getsource(ph._redaction_check)
    assert "degraded_from" in source
    assert '"warning"' in source
    assert '"critical"' in source
    assert "_redaction_check()" in inspect.getsource(ph.collect_checks)


# ---------------------------------------------------------------------------
# Credentials that are present and rejected
#
# `_usable` closes the gap for *missing* credentials, and only Google's check
# reaches the vendor -- AWS and Azure are satisfied by the strings being present.
# So a key that is present and refused (expired, rotated, revoked, wrong region,
# over quota, vendor outage) walked straight past it: the call raised, `detect`
# caught it and returned `[]`, and an unblurred photo was recorded as "we looked
# and there was nobody there". The Test button stayed green throughout.
# ---------------------------------------------------------------------------

def _rejecting():
    """A detector whose credentials are present and refused by the vendor."""
    async def _fail(*_a, **_kw):
        raise RuntimeError("403 InvalidSignatureException: credentials rejected")
    return _fail


def _probe_png() -> bytes:
    # The one-pixel probe lives in the API module next to the button that uses
    # it, so getting hold of it drags in the web stack. The redaction logic
    # under test does not need fastapi; only this fixture does.
    pytest.importorskip("fastapi.routing")
    from app.api.system import _one_pixel_probe_image
    return _one_pixel_probe_image()


def test_detect_separates_could_not_answer_from_found_nothing():
    original = ir._azure_detect
    ir._azure_detect = _rejecting()
    try:
        failed = asyncio.run(ir.detect("azure", _probe_png(), 64, 64, True, True))
    finally:
        ir._azure_detect = original
    assert failed is None, "a rejected credential must not read as an empty street"

    answered = asyncio.run(ir.detect("local", _probe_png(), 64, 64, True, True))
    assert answered == [], "a blank image genuinely has nobody in it"


def test_a_detector_that_fails_mid_call_falls_back_instead_of_publishing():
    """`effective_provider` cannot predict this -- it only sees whether the
    credentials exist. The retry has to happen where the failure happens."""
    import base64

    media = "data:image/png;base64," + base64.b64encode(_probe_png()).decode()

    async def _usable(_p):
        return True

    orig_detect, orig_usable = ir._azure_detect, ir._usable
    ir._azure_detect, ir._usable = _rejecting(), _usable
    try:
        result = asyncio.run(ir.redact_image(media, "azure", True, True))
    finally:
        ir._azure_detect, ir._usable = orig_detect, orig_usable

    # On-server detection looked at it and found nobody, which is the honest
    # answer for a blank image -- the point is that *something* looked.
    assert result.skipped_reason == "no-detections"


def test_when_the_fallback_cannot_answer_either_it_says_no_detector():
    import base64

    media = "data:image/png;base64," + base64.b64encode(_probe_png()).decode()

    async def _usable(_p):
        return True

    async def _fail(*_a, **_kw):
        raise RuntimeError("opencv exploded")

    orig_azure, orig_local, orig_usable = ir._azure_detect, ir._local_detect, ir._usable
    ir._azure_detect, ir._local_detect, ir._usable = _rejecting(), _fail, _usable
    try:
        result = asyncio.run(ir.redact_image(media, "azure", True, True))
    finally:
        ir._azure_detect, ir._local_detect, ir._usable = orig_azure, orig_local, orig_usable

    assert result.skipped_reason == "no-detector", (
        "nothing looked at this photo, and that must not be recorded as having looked"
    )


def test_the_test_button_asks_the_detector_rather_than_the_credential_store():
    """Presence of a key is not evidence the vendor accepts it. The check has to
    make a real detection call, or it reports green on a lapsed subscription."""
    import inspect

    pytest.importorskip("fastapi.routing")
    from app.api import system

    source = inspect.getsource(system._test_redaction)
    assert "detect(" in source, "the check must exercise the detector"
    assert "is None" in source, "and must react to it being unable to answer"
