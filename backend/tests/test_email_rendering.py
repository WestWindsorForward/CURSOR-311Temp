"""What has to stay true about every email this system sends.

None of this can prove an email *looks* right -- for that there is
`backend/scripts/preview_emails.py`, which writes every message to a file a
human can open in both colour schemes. What these tests hold is the set of
properties whose absence is what produced the original complaint: five modules
each hand-rolling a document shell, markup that Outlook's Word engine cannot
render, palettes with no declared colour scheme for Gmail and Apple Mail to
respect, and HTML-only messages with nothing for a text client to show.

Each of those is a rule that can be checked mechanically, so each is checked
here, across every email at once rather than one template at a time -- the
inventory is enumerated by the preview script, so a new email that skips the
shared layout fails these tests the day it is added.
"""

import os
import re
import sys

import pytest

# A submodule, deliberately: `app` is a namespace package here, so
# importorskip("app") would succeed in an environment with none of the
# dependencies installed and the failures would surface as confusing
# AttributeErrors twenty lines later. See tests/test_migrate.py.
pytest.importorskip("fastapi.routing")

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_BACKEND, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from app.services import email_layout as L  # noqa: E402

import preview_emails  # noqa: E402


# Every outbound email, built with representative content. The preview script
# is the single inventory: one list, used by the human-facing preview and by
# the tests, so the two cannot disagree about what the system sends.
MESSAGES = preview_emails.samples()


def _ids():
    return [slug for slug, _, _ in MESSAGES]


def _htmls():
    return [(slug, message["html"]) for slug, _, message in MESSAGES]


# ---------------------------------------------------------------------------
# There is one layout, and everything goes through it
# ---------------------------------------------------------------------------

SENDING_MODULES = [
    "app/services/email_templates.py",
    "app/services/notifications.py",
    "app/services/connector_alerts.py",
    "app/tasks/service_requests.py",
    "app/tasks/road_data.py",
]


def test_no_module_hand_rolls_its_own_email_document():
    """The original complaint -- "no parity or unity among them" -- was five
    modules each growing their own `<html>` shell. One layout module owns the
    document now; anything else emitting one has re-forked the design."""
    offenders = []
    for rel in SENDING_MODULES:
        src = open(os.path.join(_BACKEND, rel), encoding="utf-8").read()
        for marker in ("<!DOCTYPE", "<html", "<body"):
            if marker.lower() in src.lower():
                offenders.append(f"{rel} contains {marker}")
    assert offenders == [], offenders


def test_every_email_is_the_same_document():
    """Same shell, same width, same header. Parity is the point."""
    for slug, html in _htmls():
        assert html.lstrip().startswith("<!DOCTYPE html>"), slug
        assert f'width="{L.MAX_WIDTH}"' in html, slug
        assert "311 Service Portal" in html, slug


# ---------------------------------------------------------------------------
# Outlook / Word safety
# ---------------------------------------------------------------------------

# Word ignores every one of these silently. An email whose structure or
# legibility depends on one does not degrade in Outlook, it breaks.
FORBIDDEN = [
    ("linear-gradient", "Word renders no gradient; the header would lose its background"),
    ("radial-gradient", "as above"),
    ("box-shadow", "dropped by Word"),
    ("rgba(", "dropped by Word, taking the colour with it"),
    ("display:flex", "no flexbox in Word; columns collapse into a stack"),
    ("display: flex", "no flexbox in Word"),
    ("display:grid", "no grid in Word"),
    ("display: grid", "no grid in Word"),
    ("position:absolute", "unsupported positioning"),
    ("float:", "unreliable across clients"),
]


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_no_email_depends_on_css_outlook_drops(slug, html):
    for token, why in FORBIDDEN:
        assert token not in html, f"{slug}: {token!r} -- {why}"


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_layout_is_tables_not_divs(slug, html):
    """Word measures tables. It does not lay out a stack of styled divs."""
    assert html.count("<table") >= 2, slug
    assert 'role="presentation"' in html, slug


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_nothing_is_loaded_from_the_network_but_images(slug, html):
    """No external stylesheet, no webfont. Most clients strip `<link>`, and a
    font that fails to load silently reflows the whole message."""
    assert "<link" not in html, slug
    assert "@import" not in html, slug
    assert "fonts.googleapis" not in html, slug
    assert "fonts.gstatic" not in html, slug


# ---------------------------------------------------------------------------
# Dark mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_every_email_declares_a_colour_scheme(slug, html):
    """Undeclared, the client computes its own inversion -- which is what "the
    colors are off" was."""
    assert 'name="color-scheme"' in html, slug
    assert 'name="supported-color-schemes"' in html, slug
    assert "color-scheme: light dark" in html, slug
    assert "@media (prefers-color-scheme: dark)" in html, slug
    assert "[data-ogsc]" in html, slug  # Gmail rewrites the attribute instead


_STYLE = re.compile(r'style="([^"]*)"')
_COLOR = re.compile(r"(?<![-a-z])color\s*:")


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_nothing_sets_a_background_without_setting_a_foreground(slug, html):
    """The single most common way a mail turns unreadable in dark mode: the
    client darkens a background it can see and leaves inherited text alone."""
    for style in _STYLE.findall(html):
        if "background-color" in style:
            assert _COLOR.search(style), f"{slug}: background with no colour -- {style}"


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_the_palette_avoids_the_values_that_trigger_forced_inversion(slug, html):
    """Pure white surfaces and pure black text are what the aggressive clients
    reach for first. The palette stays inside those extremes; white only
    survives as button text on a saturated brand colour, where contrast wins."""
    extremes = {"#ffffff", "#fff", "#000000", "#000", "white", "black"}
    for style in _STYLE.findall(html):
        for value in re.findall(r"background-color\s*:\s*([^;]+)", style):
            assert value.strip().lower() not in extremes, \
                f"{slug}: extreme background {value!r} in {style}"


def test_both_palettes_carry_the_same_keys():
    assert set(L.LIGHT) == set(L.DARK)


# ---------------------------------------------------------------------------
# Contrast
# ---------------------------------------------------------------------------

def _contrast(fg: str, bg: str) -> float:
    a, b = L._luminance(fg), L._luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_body_text_meets_wcag_aa_in_both_schemes():
    for scheme in (L.LIGHT, L.DARK):
        for surface in ("surface", "panel", "page"):
            assert _contrast(scheme["text"], scheme[surface]) >= 4.5, (scheme, surface)
            # Muted text is small print; AA for normal text still applies.
            assert _contrast(scheme["muted"], scheme[surface]) >= 4.5, (scheme, surface)
        assert _contrast(scheme["link"], scheme["surface"]) >= 4.5, scheme


def test_every_severity_tone_is_readable_in_both_schemes():
    for name, tone in L.TONES.items():
        assert _contrast(tone["fg"], tone["bg"]) >= 4.5, f"{name} light"
        assert _contrast(tone["dfg"], tone["dbg"]) >= 4.5, f"{name} dark"


def test_button_text_is_chosen_against_the_town_colour_not_assumed():
    """Towns pick their own primary colour, and some pick a pale one. White on
    pale yellow is unreadable, so the foreground follows the luminance."""
    assert L.on_color("#fde047") == "#12181f"   # pale yellow -> dark text
    assert L.on_color("#1e3a8a") == "#ffffff"   # navy -> light text
    for brand in ("#fde047", "#1e3a8a", "#3f5b9c", "#e11d48"):
        assert _contrast(L.on_color(brand), brand) >= 4.5, brand


# ---------------------------------------------------------------------------
# Accessibility -- an NVDA audit is imminent
# ---------------------------------------------------------------------------

_IMG = re.compile(r"<img\b[^>]*>", re.I)


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_every_image_has_alt_text_and_a_declared_width(slug, html):
    for tag in _IMG.findall(html):
        assert re.search(r'alt="[^"]+"', tag), f"{slug}: image with no alt -- {tag}"
        assert re.search(r'width="\d+"', tag), f"{slug}: image with no width -- {tag}"


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_link_text_says_where_it_goes(slug, html):
    """"Click here" is the canonical screen-reader failure: a link list of
    nothing but "click here" tells the user nothing."""
    for phrase in ("click here", "Click here", "read more", "learn more"):
        assert phrase not in html, slug


@pytest.mark.parametrize("slug,html", _htmls(), ids=_ids())
def test_headings_are_real_and_in_order(slug, html):
    levels = [int(m) for m in re.findall(r"<h([123])\b", html)]
    assert levels and levels[0] == 1, f"{slug}: the town name is the page h1"
    for prev, nxt in zip(levels, levels[1:]):
        assert nxt <= prev + 1, f"{slug}: heading jumped from h{prev} to h{nxt}"


def test_severity_is_never_carried_by_colour_alone():
    """A red panel is not an alert to a colour-blind reader or a screen reader.
    Every tone carries a word, and the alerting emails print it."""
    for name, tone in L.TONES.items():
        if name != "neutral":
            assert tone["word"], name
    health = dict((slug, m) for slug, _, m in MESSAGES)["admin-health-escalation"]
    assert "CRITICAL" in health["text"]
    assert "WARNING" in health["text"]


# ---------------------------------------------------------------------------
# The plain-text half
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug,message", [(s, m) for s, _, m in MESSAGES], ids=_ids())
def test_every_email_has_a_substantial_text_alternative(slug, message):
    text = message["text"]
    assert text.strip(), slug
    assert "<" not in text and "&nbsp;" not in text, f"{slug}: markup leaked into the text part"
    assert len(text.split()) >= 12, f"{slug}: text part is a stub"


@pytest.mark.parametrize("slug,message", [(s, m) for s, _, m in MESSAGES], ids=_ids())
def test_the_text_part_keeps_the_links(slug, message):
    """A text reader who cannot see the button still has to be able to act."""
    urls = re.findall(r'href="(https?://[^"]+)"', message["html"])
    for url in set(urls):
        assert url in message["text"], f"{slug}: {url} is missing from the text part"


def test_the_text_part_escapes_nothing_and_the_html_part_escapes_everything():
    """Resident text is escaped exactly once. Escaping in the caller *and* the
    renderer is how an apostrophe reaches an inbox as `&amp;#39;`."""
    from app.services.email_templates import build_confirmation_email

    message = build_confirmation_email(
        township_name="T", logo_url=None, primary_color="#3f5b9c",
        request_id="1", service_name="Pothole",
        description="<script>alert(1)</script> tires & rims",
        address=None, portal_url="https://t.gov")
    assert "<script>" not in message["html"]
    assert "&lt;script&gt;" in message["html"]
    assert "&amp;amp;" not in message["html"]
    assert "<script>alert(1)</script> tires & rims" in message["text"]


def test_html_to_text_is_a_usable_floor_for_a_caller_that_forgot():
    text = L.html_to_text("<html><body><h2>Hi</h2><p>One</p><p>Two &amp; three</p>"
                          "<style>p{color:red}</style></body></html>")
    assert "Hi" in text and "One" in text and "Two & three" in text
    assert "color:red" not in text
    assert "<" not in text


# ---------------------------------------------------------------------------
# multipart/alternative, per provider
#
# The three providers take the body in three different shapes. A text part
# wired for SMTP only would leave two thirds of the deployments HTML-only, so
# each is checked against its own transport.
# ---------------------------------------------------------------------------

def _service_with(provider):
    from app.services.notifications import NotificationService

    svc = NotificationService()
    svc._email_provider = provider
    svc._email_provider_name = "test"
    return svc


def test_smtp_sends_multipart_alternative_with_a_text_part(monkeypatch):
    from app.services import notifications as N

    captured = {}

    class FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr(N.smtplib, "SMTP", FakeSMTP)
    provider = N.EmailProvider("host", 587, "u", "p", "from@t.gov")
    assert provider.send_email("to@t.gov", "S", "<p>Body text here</p>", "Body text here")

    msg = captured["msg"]
    assert msg.get_content_type() == "multipart/alternative"
    parts = {p.get_content_type(): p.get_payload(decode=True).decode() for p in msg.walk()
             if p.get_content_type() != "multipart/alternative"}
    assert set(parts) == {"text/plain", "text/html"}
    assert parts["text/plain"].strip()
    # multipart/alternative is ordered worst-to-best; a client that shows the
    # first part it understands must not land on the HTML.
    assert msg.get_payload(0).get_content_type() == "text/plain"


def test_ses_sends_a_text_body_as_well_as_html():
    from app.services.notifications import SESEmailProvider

    captured = {}

    class FakeSES:
        def send_email(self, **kw):
            captured.update(kw)
            return {"MessageId": "x"}

    provider = SESEmailProvider(region="us-gov-west-1", from_email="from@t.gov")
    provider._client = lambda: FakeSES()
    assert provider.send_email("to@t.gov", "S", "<p>Body</p>", "Body")
    body = captured["Message"]["Body"]
    assert body["Text"]["Data"].strip()
    assert body["Html"]["Data"].strip()


def test_acs_sends_a_plaintext_body_as_well_as_html(monkeypatch):
    import json

    from app.services import notifications as N

    captured = {}

    class FakeResponse:
        is_success = True
        status_code = 202
        text = ""

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, content=None, headers=None):
            captured["payload"] = json.loads(content.decode())
            return FakeResponse()

    monkeypatch.setattr(N.httpx, "Client", FakeClient)
    import base64

    provider = N.ACSEmailProvider(
        endpoint="https://acs.example.com",
        access_key=base64.b64encode(b"k" * 32).decode(),
        from_email="from@t.gov")
    assert provider.send_email("to@t.gov", "S", "<p>Body</p>", "Body")
    content = captured["payload"]["content"]
    assert content["plainText"].strip()
    assert content["html"].strip()


@pytest.mark.parametrize("provider_kind", ["smtp", "ses", "acs"])
def test_a_caller_that_forgets_the_text_part_still_sends_one(provider_kind, monkeypatch):
    """The guarantee lives in the send path, not in each caller's discipline --
    the old staff and digest emails passed HTML only, and that is precisely the
    mistake that must stop being possible."""
    seen = {}

    class Recorder:
        def send_email(self, to, subject, body_html, body_text=None, from_name=None):
            seen["text"] = body_text
            return True

    svc = _service_with(Recorder())
    svc._record_health_sync = lambda *a, **kw: None
    svc.send_email("to@t.gov", "S", "<html><body><p>Only HTML was passed here</p></body></html>")
    assert seen["text"] and seen["text"].strip()
    assert "Only HTML was passed here" in seen["text"]


# ---------------------------------------------------------------------------
# Branding and consistency
# ---------------------------------------------------------------------------

def test_the_town_colour_is_validated_before_it_reaches_a_style_attribute():
    assert L.safe_color("#abc") == "#aabbcc"
    assert L.safe_color("red; background:url(http://x)") == L.DEFAULT_BRAND
    assert L.safe_color(None) == L.DEFAULT_BRAND
    html = L.render_html(township_name="T", logo_url=None,
                         primary_color="red;}</style><script>x()</script>",
                         title="t", blocks=[L.paragraph("hi")])
    assert "<script>" not in html


def test_only_http_urls_survive_into_hrefs_and_srcs():
    html = L.render_html(
        township_name="T", logo_url="javascript:alert(1)", primary_color="#3f5b9c",
        title="t", blocks=[L.button("Go", "javascript:alert(1)"),
                           L.image("data:image/png;base64,AAAA", "x")])
    assert "javascript:" not in html
    assert "data:image" not in html


def test_every_email_carries_the_town_name_and_the_same_footer_treatment():
    for slug, _, message in MESSAGES:
        assert message["subject"], slug
        # The town names itself in every message -- in the branded header of
        # the HTML, and in the subject or the body of the text half.
        town = preview_emails.TOWN
        if slug != "legacy-confirmation":  # the unbranded fallback has no town
            assert town in message["html"], slug
            assert town in message["subject"] or town in message["text"], slug
        assert "311" in message["html"], slug


def test_rtl_languages_get_a_direction():
    html = L.render_html(township_name="T", logo_url=None, primary_color="#3f5b9c",
                         title="t", blocks=[L.paragraph("hi")], language="ar")
    assert 'dir="rtl"' in html
    ltr = L.render_html(township_name="T", logo_url=None, primary_color="#3f5b9c",
                        title="t", blocks=[L.paragraph("hi")], language="en")
    assert "dir=" not in ltr
