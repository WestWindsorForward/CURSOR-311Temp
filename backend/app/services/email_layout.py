"""One layout for every email this system sends.

Every outbound message -- resident confirmations, staff assignments, the weekly
digest, connector alerts, road-feed alerts, health escalations -- is described
here as a list of *content blocks* and rendered twice: once into email-safe
HTML and once into readable plain text. Callers never write a document shell,
never pick a colour, and never hand-roll a `<table>`. That is the whole point:
the reason the old emails had no family resemblance is that five modules each
grew their own markup.

Looking like the rest of the product
------------------------------------
The admin console is a deep indigo surface with 16-20px radii, an indigo-violet
accent on primary actions, Inter for type, uppercase micro labels and a spacing
rhythm on an 8px grid (`frontend/src/index.css`, the `premium-card` /
`setup-panel` idiom). The emails now speak that language: rounded cards, a
branded masthead that carries a gradient rather than a flat bar, a hairline
accent under it, micro labels above every value, and one obviously primary
action.

What email can and cannot borrow from it
----------------------------------------
Outlook on Windows renders with Word. The distinction that matters is not
"modern versus safe", it is whether the Word fallback looks *deliberate* or
looks *broken*:

* `border-radius` -- used freely. Word ignores it and draws square corners,
  which is a different but finished look.
* `linear-gradient` -- used, but only ever as a `background-image` layered over
  a real `background-color` that stands on its own. Word takes the solid. It
  appears in exactly three places, all of them the town's own brand colour:
  the masthead, the accent hairline beneath it, and the primary button.
  Everything else is a flat colour, because a gradient on a content panel buys
  nothing and costs an exact dark-mode override.
* `rgba()` -- never. Word drops the declaration and takes the colour with it,
  so a panel defined in rgba renders as *nothing*. Gradient stops are mixed to
  hex here instead.
* `box-shadow` -- deliberately not used. It degrades cleanly in Word but
  renders inconsistently and muddily elsewhere, and depth in email reads better
  as a hairline border than as a blur.
* flexbox, grid, floats, external stylesheets, webfonts -- never. Each of these
  collapses rather than simplifies. `Inter` is *named* in the font stack, not
  loaded: a machine that has it uses it, a machine that does not falls through
  to the system stack, and nothing is fetched.

Why every cell states both colours
----------------------------------
Gmail, Apple Mail and Outlook.com all apply their own dark-mode transform to
messages that do not declare a colour scheme. Their heuristic is per-element:
a cell with a light background and *inherited* text gets its background
darkened while the text is left alone, which is exactly how "the colors are
off" happens -- dark grey on near-black. So every cell that sets one colour
sets both, the document declares `color-scheme` and `supported-color-schemes`,
and a `prefers-color-scheme` block hands the clients that honour it a palette
we chose instead of one they computed. The palette also stays off pure
`#ffffff` / `#000000`, the two values that trigger the most aggressive
inversion.

Translation
-----------
Nothing in this module writes a user-visible sentence. Callers pass whole
translated strings; the layout only decides where they sit. Two consequences
are designed for here rather than hoped for:

* **Expansion.** German, Spanish and Finnish run 30-40% longer than English.
  Nothing is sized to its English text: buttons wrap instead of overflowing,
  stat tiles are laid out at most three to a row and spill onto a second row
  beyond that, table cells break long words, and no cell has a fixed height.
* **Direction.** The app offers Arabic (`LanguageSelector`, and
  `TranslationContext` sets `document.documentElement.dir`), so `dir="rtl"`
  here is a real path, not a gesture. It mirrors alignment, list indents and
  the callout's accent keyline rather than only setting an attribute.

Accessibility
-------------
Headings are real `<h1>`/`<h2>`/`<h3>` in document order, links carry their
destination as their text (never "click here"), every image carries alt text
and explicit dimensions, and severity is always spelled out in words as well
as shown in colour -- an alert that is only red is not an alert to a
screen-reader user or to the 8% of men who cannot see the red.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Palette
#
# Two columns: what the mail looks like in a light client, and what it looks
# like in a dark one. Tuned to the console's indigo family rather than to a
# neutral grey, so an email and the dashboard it links to look related.
# Deliberately no #ffffff and no #000000.
# ---------------------------------------------------------------------------

LIGHT = {
    "page": "#eceef7",
    "surface": "#fbfcfe",
    "panel": "#f2f4fb",
    "text": "#1b2130",
    "muted": "#565e77",
    "border": "#dde1ee",
    "hairline": "#e7eaf4",
    "link": "#4338ca",
}

DARK = {
    "page": "#111420",
    "surface": "#1b2030",
    "panel": "#232939",
    "text": "#e9ecf6",
    "muted": "#a6afc8",
    "border": "#343b52",
    "hairline": "#2b3145",
    "link": "#a9b8fc",
}

# Severity tones. Each carries a *word*, so the meaning survives a greyscale
# print, a colour-blind reader and a screen reader; the colour is decoration on
# top of the word, never instead of it. `edge` doubles as the accent keyline
# down the leading edge of the panel.
TONES: Dict[str, Dict[str, str]] = {
    "neutral":  {"word": "",         "bg": "#f2f4fb", "fg": "#1b2130", "edge": "#c4cbdf",
                 "dbg": "#232939", "dfg": "#e9ecf6", "dedge": "#414a63"},
    "info":     {"word": "Update",   "bg": "#e9eefd", "fg": "#26307a", "edge": "#a5b4fc",
                 "dbg": "#1e2444", "dfg": "#c7d2fe", "dedge": "#4c5aa8"},
    "success":  {"word": "Resolved", "bg": "#e4f5ea", "fg": "#14492e", "edge": "#8fd0ac",
                 "dbg": "#152f22", "dfg": "#b7e5c8", "dedge": "#2f6446"},
    "warning":  {"word": "Warning",  "bg": "#fdf0da", "fg": "#6a4205", "edge": "#e6bd77",
                 "dbg": "#382c12", "dfg": "#f4d8a3", "dedge": "#6d5623"},
    "critical": {"word": "Critical", "bg": "#fce9e9", "fg": "#7a1d1d", "edge": "#eda9a9",
                 "dbg": "#391d1f", "dfg": "#f6c4c4", "dedge": "#713134"},
}

# The console's own `--primary`. A town that has not chosen a colour gets the
# product's, not a generic blue.
DEFAULT_BRAND = "#6366f1"

MAX_WIDTH = 600

# Inter is *named*, never fetched -- see the module docstring. Everything after
# it is present on the machine already.
FONT = ("Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")

RTL_LANGUAGES = {"ar", "he", "fa", "ur", "yi", "ps", "dv", "ku", "sd", "ug"}

# Corner radii, in the console's proportions. Word squares all of them.
R_CARD = 18
R_PANEL = 12
R_BUTTON = 10

# The most stat tiles that still read at +40% text expansion on a 544px
# content column. Beyond this they wrap onto another row rather than shrinking.
STATS_PER_ROW = 3


# ---------------------------------------------------------------------------
# Small safety helpers
# ---------------------------------------------------------------------------

def esc(value: Any) -> str:
    """Escape a value for interpolation into email HTML.

    Block helpers escape their own arguments, so callers pass plain text and
    cannot accidentally ship a resident's description as markup.
    """
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def safe_color(value: Optional[str], fallback: str = DEFAULT_BRAND) -> str:
    """A town-supplied brand colour, or the fallback.

    Anything that is not a plain hex literal is rejected outright -- this value
    lands inside a `style` attribute, so `red; background:url(...)` has to be
    impossible.
    """
    v = (value or "").strip()
    if _HEX.match(v):
        if len(v) == 4:  # #abc -> #aabbcc
            v = "#" + "".join(c * 2 for c in v[1:])
        return v.lower()
    return fallback


def safe_url(value: Optional[str]) -> Optional[str]:
    """Only http(s) survives into an href/src. Drops javascript:, data:, etc."""
    if not value:
        return None
    v = str(value).strip()
    return v if v.lower().startswith(("http://", "https://")) else None


def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = safe_color(hex_color).lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def mix(a: str, b: str, weight: float) -> str:
    """Blend two hex colours, `weight` of the way from `a` to `b`.

    Gradient stops are computed here rather than written as `rgba()` overlays,
    because Word drops an rgba declaration entirely and would take the whole
    background with it.
    """
    weight = max(0.0, min(1.0, weight))
    ar, ag, ab = _rgb(a)
    br, bg, bb = _rgb(b)
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * weight),
        round(ag + (bg - ag) * weight),
        round(ab + (bb - ab) * weight),
    )


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG relative contrast ratio."""
    a, b = _luminance(safe_color(fg)), _luminance(safe_color(bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def on_color(background: str) -> str:
    """Readable foreground for text sitting on an arbitrary brand colour.

    Towns choose their own primary colour and some choose pale yellow. White
    button text on that is unreadable, so the two candidates are *measured*
    against the surface and the better one wins. A luminance threshold gets
    this right for most colours and wrong near the crossover, which is where
    the mid-tone indigos and teals that towns actually pick all sit.
    """
    bg = safe_color(background)
    dark, light = "#12181f", "#ffffff"
    return dark if contrast(dark, bg) > contrast(light, bg) else light


def brand_stops(brand: str) -> Tuple[str, str]:
    """The two ends of the masthead / button gradient, derived from the town's
    own colour.

    Shaded from `brand` rather than picked, so a town with a maroon brand gets
    a maroon gradient rather than the product's indigo.

    The direction is chosen so the gradient can never cost the surface its
    legibility. On a dark brand carrying white text both stops run *darker*
    than the brand; on a pale brand carrying dark text both run *lighter*.
    Fading in the other direction is what an even 135-degree light-to-dark
    sweep does, and it is why the product's own `#6366f1` -- which sits at
    4.47:1 against white, just under AA before anything is done to it -- came
    out flat when the stops were symmetrical: every tint of it was worse than
    the colour the town already chose. Every stop below is at least as
    readable as the brand itself.
    """
    brand = safe_color(brand)
    fg = on_color(brand)
    toward = "#0d1030" if fg == "#ffffff" else "#ffffff"
    stops = (mix(brand, toward, 0.30), mix(brand, toward, 0.02))
    floor = contrast(fg, brand)
    if min(contrast(fg, s) for s in stops) < floor - 0.01:  # pragma: no cover
        return brand, brand
    return stops


def _clip(text: str, limit: Optional[int]) -> str:
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


# ---------------------------------------------------------------------------
# Blocks
#
# A block is a plain dict. Callers build a list of them; `render` turns the
# same list into HTML and into text, which is why the two halves of every
# message cannot drift apart.
# ---------------------------------------------------------------------------

def heading(text: str, level: int = 2) -> Dict[str, Any]:
    """A real heading. Levels run 1..3; the layout already owns the page <h1>
    unless a caller asks for one, so most content starts at 2."""
    return {"type": "heading", "text": str(text), "level": max(1, min(3, int(level)))}


def paragraph(text: str, muted: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
    return {"type": "paragraph", "text": _clip(str(text), limit), "muted": bool(muted)}


def lede(text: str) -> Dict[str, Any]:
    """The one sentence the message is about, set a step larger than body copy.

    Purely a type-scale device: it carries no content a paragraph could not,
    it just stops the opening line competing with everything under it.
    """
    return {"type": "lede", "text": str(text)}


def note(text: str) -> Dict[str, Any]:
    """Small print. Still meets contrast in both schemes."""
    return {"type": "note", "text": str(text)}


def fields(rows: Sequence[Tuple[str, Any]], title: Optional[str] = None) -> Dict[str, Any]:
    """Label/value pairs -- request id, category, address. Rendered as a
    two-row-per-item table rather than a definition list, because Word does not
    lay out `<dl>` reliably."""
    clean = [(str(k), "" if v is None else str(v)) for k, v in rows if v not in (None, "")]
    return {"type": "fields", "rows": clean, "title": title}


def button(label: str, url: str) -> Dict[str, Any]:
    """The one call to action. Rendered as a padded, rounded, brand-coloured
    table cell -- the 'bulletproof' shape -- and always followed by the bare URL
    so the destination is visible to text readers and to clients that strip the
    button."""
    return {"type": "button", "label": str(label), "url": url}


def link(label: str, url: str) -> Dict[str, Any]:
    """A secondary action. Clearly subordinate to the button: link-coloured
    text at body size, not a second thing shaped like a button."""
    return {"type": "link", "label": str(label), "url": url}


def callout(body: str, tone: str = "info", title: Optional[str] = None) -> Dict[str, Any]:
    """A panel that carries a severity. The tone's word is printed, so the
    severity does not live in the colour alone."""
    return {"type": "callout", "body": str(body), "tone": tone if tone in TONES else "info",
            "title": title}


def status_panel(label: str, value: str, tone: str = "info") -> Dict[str, Any]:
    """The one big fact -- 'Current status: Resolved'."""
    return {"type": "status", "label": str(label), "value": str(value),
            "tone": tone if tone in TONES else "info"}


def bullets(items: Sequence[Any]) -> Dict[str, Any]:
    """Each item is either a string or a (main, sub) pair."""
    norm: List[Tuple[str, str]] = []
    for it in items:
        if isinstance(it, (tuple, list)):
            norm.append((str(it[0]), str(it[1]) if len(it) > 1 and it[1] else ""))
        else:
            norm.append((str(it), ""))
    return {"type": "bullets", "items": norm}


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """A small data table. Header cells are `<th scope="col">` so a screen
    reader can associate them."""
    return {"type": "table",
            "headers": [str(h) for h in headers],
            "rows": [[("" if c is None else str(c)) for c in r] for r in rows]}


def stats(items: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    """A row of counts. Laid out as table cells of equal width -- never flex,
    which is what made the digest's number row stack wrongly in Outlook -- and
    wrapped onto further rows past `STATS_PER_ROW` so a translated label has
    somewhere to go."""
    return {"type": "stats", "items": [(str(k), str(v)) for k, v in items]}


def image(url: str, alt: str, width: int = 520) -> Dict[str, Any]:
    """An image with mandatory alt text and an explicit width, so the layout
    holds before the image loads and reads correctly when it never does."""
    return {"type": "image", "url": url, "alt": str(alt), "width": int(width)}


def divider() -> Dict[str, Any]:
    return {"type": "divider"}


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _td(content: str, *, cls: str = "", style: str = "", extra: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    e = f" {extra}" if extra else ""
    return f'<td{c}{e} style="{style}">{content}</td>'


def _row(inner: str) -> str:
    return f"<tr>{inner}</tr>"


def _text(color: str, size: int = 15, weight: str = "400", line: str = "1.65",
          spacing: Optional[str] = None) -> str:
    ls = f"letter-spacing:{spacing};" if spacing else ""
    return (f"margin:0;font-family:{FONT};font-size:{size}px;line-height:{line};"
            f"font-weight:{weight};color:{color};{ls}")


def _micro(color: str) -> str:
    """The uppercase micro label the console puts above every value."""
    return _text(color, 11, "700", "1.45", ".8px") + "text-transform:uppercase;"


# A gradient is only ever a *layer* over a solid that stands on its own.
def _gradient(solid: str, css: str) -> str:
    return f"background-color:{solid};background-image:{css};"


# Long unbroken input -- a pasted URL in a resident's description, a German
# compound in a translated header -- must break rather than push the column
# wider than 600px.
_BREAK = "word-break:break-word;overflow-wrap:break-word;"


def _block_html(block: Dict[str, Any], brand: str, rtl: bool) -> str:
    kind = block["type"]
    start = "right" if rtl else "left"
    end = "left" if rtl else "right"
    align = f"text-align:{start};"

    if kind == "heading":
        level = block["level"]
        # A deliberate scale rather than three similar sizes: the old headings
        # were 23/19/16 and every one of them competed with the next.
        size, weight, ls = {1: (26, "700", "-.3px"),
                            2: (21, "700", "-.2px"),
                            3: (16, "700", "0")}[level]
        return _row(_td(
            f'<h{level} class="es-text" style="{_text(LIGHT["text"], size, weight, "1.3", ls)}'
            f'{align}{_BREAK}">{esc(block["text"])}</h{level}>',
            style="padding:0 0 12px 0;"))

    if kind == "lede":
        return _row(_td(
            f'<p class="es-text" style="{_text(LIGHT["text"], 17, "400", "1.55")}{align}{_BREAK}">'
            f'{esc(block["text"])}</p>',
            style="padding:0 0 18px 0;"))

    if kind == "paragraph":
        color = LIGHT["muted"] if block["muted"] else LIGHT["text"]
        cls = "es-muted" if block["muted"] else "es-text"
        return _row(_td(
            f'<p class="{cls}" style="{_text(color)}{align}{_BREAK}">{esc(block["text"])}</p>',
            style="padding:0 0 16px 0;"))

    if kind == "note":
        return _row(_td(
            f'<p class="es-muted" style="{_text(LIGHT["muted"], 13, "400", "1.55")}{align}{_BREAK}">'
            f'{esc(block["text"])}</p>',
            style="padding:0 0 12px 0;"))

    if kind == "divider":
        return _row(_td(
            '<div class="es-rule" style="height:1px;font-size:0;line-height:0;'
            f'background-color:{LIGHT["border"]};color:{LIGHT["border"]};">&nbsp;</div>',
            style="padding:8px 0 24px 0;"))

    if kind == "fields":
        inner = []
        if block.get("title"):
            inner.append(_row(_td(
                f'<p class="es-muted" style="{_micro(LIGHT["muted"])}{align}">'
                f'{esc(block["title"])}</p>',
                style="padding:2px 0 12px 0;")))
        last = len(block["rows"]) - 1
        for i, (label, value) in enumerate(block["rows"]):
            border = "" if i == last else f"border-bottom:1px solid {LIGHT['hairline']};"
            inner.append(_row(_td(
                f'<p class="es-muted" style="{_micro(LIGHT["muted"])}{align}">{esc(label)}</p>'
                f'<p class="es-text" style="{_text(LIGHT["text"], 15, "500", "1.5")}'
                f'padding-top:4px;{align}{_BREAK}">{esc(value)}</p>',
                cls="es-hair-b" if border else "",
                style=f"padding:12px 0;{border}")))
        inner_table = ('<table role="presentation" cellpadding="0" cellspacing="0" '
                       'border="0" width="100%">{rows}</table>').format(rows="".join(inner))
        panel = _td(inner_table, cls="es-panel", style=(
            f"padding:6px 20px;background-color:{LIGHT['panel']};color:{LIGHT['text']};"
            f"border:1px solid {LIGHT['border']};border-radius:{R_PANEL}px;"))
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%"><tr>{panel}</tr></table>',
            style="padding:0 0 20px 0;"))

    if kind == "button":
        url = safe_url(block["url"])
        label = esc(block["label"])
        if not url:
            return _row(_td(
                f'<p class="es-text" style="{_text(LIGHT["text"], 15, "700")}{align}">{label}</p>',
                style="padding:0 0 16px 0;"))
        fg = on_color(brand)
        dark, light = brand_stops(brand)
        grad = f"linear-gradient(135deg,{dark} 0%,{brand} 55%,{light} 100%)"
        # The cell has no fixed width, so a label 40% longer than its English
        # wraps inside the button instead of pushing past the column.
        btn = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="max-width:100%;"><tr>'
            f'<td align="center" style="{_gradient(brand, grad)}'
            f'color:{fg};padding:15px 30px;border-radius:{R_BUTTON}px;">'
            f'<a href="{esc(url)}" style="font-family:{FONT};font-size:15px;font-weight:700;'
            f'line-height:1.35;letter-spacing:.2px;color:{fg};text-decoration:none;'
            f'display:inline-block;max-width:100%;{_BREAK}">{label}</a>'
            '</td></tr></table>'
        )
        follow = (f'<p class="es-muted" style="{_text(LIGHT["muted"], 12, "400", "1.5")}'
                  f'padding-top:10px;{align}{_BREAK}">'
                  f'<a class="es-link" href="{esc(url)}" style="color:{LIGHT["link"]};">'
                  f'{esc(url)}</a></p>')
        return _row(_td(btn + follow, style="padding:6px 0 22px 0;", extra=f'align="{start}"'))

    if kind == "link":
        url = safe_url(block["url"])
        if not url:
            return ""
        return _row(_td(
            f'<p class="es-text" style="{_text(LIGHT["text"], 14)}{align}{_BREAK}">'
            f'<a class="es-link" href="{esc(url)}" style="color:{LIGHT["link"]};'
            f'font-weight:600;text-decoration:underline;">{esc(block["label"])}</a></p>',
            style="padding:0 0 14px 0;"))

    if kind in ("callout", "status"):
        tone = TONES[block["tone"]]
        word = tone["word"]
        if kind == "status":
            body_html = (
                f'<p style="{_micro(tone["fg"])}{align}">{esc(block["label"])}</p>'
                f'<p style="{_text(tone["fg"], 24, "700", "1.25", "-.2px")}'
                f'padding-top:6px;{align}{_BREAK}">{esc(block["value"])}</p>'
            )
        else:
            title = block.get("title") or word
            head = (f'<p style="{_micro(tone["fg"])}{align}{_BREAK}">{esc(title)}</p>'
                    if title else "")
            body_html = (head +
                         f'<p style="{_text(tone["fg"], 15)}'
                         f'{"padding-top:6px;" if title else ""}{align}{_BREAK}">'
                         f'{esc(block["body"])}</p>')
        # The keyline runs down the *leading* edge, which is the right-hand one
        # in an RTL message.
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<tr><td class="es-tone-{block["tone"]}" style="background-color:{tone["bg"]};'
            f'color:{tone["fg"]};border:1px solid {tone["edge"]};'
            f'border-{start}:4px solid {tone["edge"]};'
            f'border-radius:{R_PANEL}px;padding:18px 20px;">'
            f'{body_html}</td></tr></table>',
            style="padding:0 0 20px 0;"))

    if kind == "bullets":
        items = []
        for main, sub in block["items"]:
            sub_html = (f'<br><span class="es-muted" style="color:{LIGHT["muted"]};'
                        f'font-size:13px;line-height:1.5;">{esc(sub)}</span>') if sub else ""
            items.append(
                f'<li style="{_text(LIGHT["text"])}padding-bottom:10px;{_BREAK}">'
                f'{esc(main)}{sub_html}</li>')
        return _row(_td(
            f'<ul class="es-text" style="margin:0 0 16px 0;padding-{start}:22px;'
            f'padding-{end}:0;color:{LIGHT["text"]};{align}">{"".join(items)}</ul>',
            style="padding:0;"))

    if kind == "table":
        head_cells = "".join(
            f'<th scope="col" class="es-panel" style="background-color:{LIGHT["panel"]};'
            f'color:{LIGHT["text"]};font-family:{FONT};font-size:11px;font-weight:700;'
            f'text-align:{start};text-transform:uppercase;letter-spacing:.8px;'
            f'padding:12px 14px;border-bottom:1px solid {LIGHT["border"]};{_BREAK}">{esc(h)}</th>'
            for h in block["headers"])
        body_rows = ""
        for r in block["rows"]:
            body_rows += "<tr>" + "".join(
                f'<td class="es-cell" style="background-color:{LIGHT["surface"]};'
                f'color:{LIGHT["text"]};font-family:{FONT};font-size:14px;line-height:1.5;'
                f'text-align:{start};padding:12px 14px;'
                f'border-bottom:1px solid {LIGHT["hairline"]};{_BREAK}">{esc(c)}</td>'
                for c in r) + "</tr>"
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border:1px solid {LIGHT["border"]};'
            f'border-radius:{R_PANEL}px;">'
            f'<thead><tr>{head_cells}</tr></thead><tbody>{body_rows}</tbody></table>',
            style="padding:0 0 20px 0;"))

    if kind == "stats":
        items = block["items"]
        if not items:
            return ""
        # Three to a row at most. A fourth tile at 25% is 118px wide, which a
        # translated label ("Vencidas (7+ dias)") cannot survive.
        chunks = [items[i:i + STATS_PER_ROW] for i in range(0, len(items), STATS_PER_ROW)]
        rows_html = []
        for chunk in chunks:
            width = int(100 / len(chunk))
            cells = "".join(
                f'<td class="es-panel" width="{width}%" valign="top" '
                f'style="background-color:{LIGHT["panel"]};color:{LIGHT["text"]};'
                f'border:1px solid {LIGHT["border"]};border-radius:{R_PANEL}px;'
                f'padding:16px 10px;text-align:center;">'
                f'<p class="es-text" style="{_text(LIGHT["text"], 26, "700", "1.15", "-.5px")}">'
                f'{esc(v)}</p>'
                f'<p class="es-muted" style="{_text(LIGHT["muted"], 12, "500", "1.4")}'
                f'padding-top:6px;{_BREAK}">{esc(k)}</p></td>'
                for k, v in chunk)
            rows_html.append(f"<tr>{cells}</tr>")
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border-spacing:6px;">{"".join(rows_html)}</table>',
            style="padding:0 0 20px 0;"))

    if kind == "image":
        url = safe_url(block["url"])
        if not url:
            return ""
        w = min(int(block["width"]), MAX_WIDTH - 72)
        return _row(_td(
            f'<img src="{esc(url)}" alt="{esc(block["alt"])}" width="{w}" '
            f'style="display:block;width:100%;max-width:{w}px;height:auto;'
            f'border:1px solid {LIGHT["border"]};border-radius:{R_PANEL}px;">',
            style="padding:0 0 20px 0;"))

    return ""


def _dark_css() -> str:
    """The dark palette, handed to clients that honour `prefers-color-scheme`.

    `!important` throughout because inline styles win otherwise, and the inline
    styles have to be there for the clients that support nothing else. The
    `[data-ogsc]` duplicates are Gmail's dark transform, which rewrites the
    attribute rather than matching the media query.

    Note what is *not* here: the masthead, the accent hairline and the button
    are the town's brand colour and stay the town's brand colour in dark mode,
    which is why they are the only three surfaces allowed a gradient. Every
    class below is a flat colour with an exact dark counterpart.
    """
    rules = []

    def block(prefix: str) -> str:
        p = prefix
        return f"""
      {p} .es-page {{ background-color:{DARK['page']} !important; }}
      {p} .es-card {{ background-color:{DARK['surface']} !important; border-color:{DARK['border']} !important; }}
      {p} .es-text, {p} .es-text * {{ color:{DARK['text']} !important; }}
      {p} .es-muted, {p} .es-muted * {{ color:{DARK['muted']} !important; }}
      {p} .es-panel {{ background-color:{DARK['panel']} !important; color:{DARK['text']} !important; border-color:{DARK['border']} !important; }}
      {p} .es-cell {{ background-color:{DARK['surface']} !important; color:{DARK['text']} !important; border-color:{DARK['hairline']} !important; }}
      {p} .es-rule {{ background-color:{DARK['border']} !important; }}
      {p} .es-hair-b {{ border-color:{DARK['hairline']} !important; }}
      {p} .es-link, {p} .es-link * {{ color:{DARK['link']} !important; }}
""" + "".join(
            f"      {p} .es-tone-{name} {{ background-color:{t['dbg']} !important;"
            f" color:{t['dfg']} !important; border-color:{t['dedge']} !important; }}\n"
            f"      {p} .es-tone-{name} p {{ color:{t['dfg']} !important; }}\n"
            for name, t in TONES.items())

    rules.append("@media (prefers-color-scheme: dark) {" + block("") + "}")
    rules.append(block("[data-ogsc]"))
    return "\n".join(rules)


DEFAULT_TAGLINE = "311 Service Portal"


def render_html(
    *,
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    title: str,
    blocks: Sequence[Dict[str, Any]],
    footer_lines: Sequence[str] = (),
    language: str = "en",
    preheader: str = "",
    tagline: str = DEFAULT_TAGLINE,
) -> str:
    brand = safe_color(primary_color)
    brand_fg = on_color(brand)
    grad_dark, grad_light = brand_stops(brand)
    logo = safe_url(logo_url)
    lang = (language or "en").split("-")[0].lower()
    rtl = lang in RTL_LANGUAGES
    dir_attr = ' dir="rtl"' if rtl else ""
    town = esc(township_name or "311")

    body = "".join(_block_html(b, brand, rtl) for b in blocks if b)

    logo_html = ""
    if logo:
        # No text is baked into this image -- the town name is set in real type
        # below it, so a logo that fails to load costs branding, not meaning.
        logo_html = (f'<img src="{esc(logo)}" alt="{town}" width="132" height="40" '
                     f'style="display:block;margin:0 auto 14px auto;width:auto;height:40px;'
                     f'max-width:132px;border:0;">')

    footer_html = "".join(
        f'<p class="es-muted" style="{_text(LIGHT["muted"], 12, "400", "1.6")}'
        f'padding-bottom:6px;{_BREAK}">{esc(line)}</p>'
        for line in footer_lines if line)

    pre = ""
    if preheader:
        pre = (f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
               f'max-width:0;opacity:0;overflow:hidden;">{esc(preheader)}'
               f'{"&#8199;&#65279;&#847; " * 30}</div>')

    # The masthead's gradient sits on top of a solid `brand`; the hairline
    # beneath it is the console's `setup-panel::before` accent, and it too has a
    # solid underneath. Word takes both solids and the header still looks made
    # on purpose.
    masthead_bg = _gradient(
        brand, f"linear-gradient(135deg,{grad_dark} 0%,{brand} 52%,{grad_light} 100%)")
    hair_solid = mix(brand, "#ffffff", 0.42)
    hair_bg = _gradient(
        hair_solid,
        f"linear-gradient(90deg,{brand} 0%,{mix(brand, '#ffffff', 0.62)} 50%,{brand} 100%)")
    tag_html = (f'<p style="{_text(brand_fg, 11, "700", "1.5", "1.1px")}padding-top:8px;'
                f'text-transform:uppercase;{_BREAK}">{esc(tagline)}</p>') if tagline else ""

    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}"{dir_attr} xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{esc(title or township_name)}</title>
  <style>
    :root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
    body, table, td, p, h1, h2, h3, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
    img {{ -ms-interpolation-mode:bicubic; }}
    @media only screen and (max-width: 620px) {{
      .es-shell {{ width:100% !important; }}
      .es-pad {{ padding-left:20px !important; padding-right:20px !important; }}
      .es-head {{ padding-left:20px !important; padding-right:20px !important; }}
    }}
{_dark_css()}
  </style>
</head>
<body class="es-page"{dir_attr} style="margin:0;padding:0;width:100%;background-color:{LIGHT['page']};color:{LIGHT['text']};font-family:{FONT};">
{pre}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" class="es-page" style="background-color:{LIGHT['page']};color:{LIGHT['text']};">
  <tr>
    <td align="center" style="padding:32px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{MAX_WIDTH}"{dir_attr} class="es-shell" style="width:{MAX_WIDTH}px;max-width:{MAX_WIDTH}px;">
        <tr>
          <td align="center" class="es-head" style="{masthead_bg}color:{brand_fg};padding:34px 28px 30px 28px;border-radius:{R_CARD}px {R_CARD}px 0 0;">
            {logo_html}
            <h1 style="{_text(brand_fg, 26, '700', '1.25', '-.3px')}{_BREAK}">{town}</h1>
            {tag_html}
          </td>
        </tr>
        <tr>
          <td style="{hair_bg}color:{hair_solid};height:4px;line-height:4px;font-size:0;">&nbsp;</td>
        </tr>
        <tr>
          <td class="es-card es-pad" style="background-color:{LIGHT['surface']};color:{LIGHT['text']};border:1px solid {LIGHT['border']};border-top:0;border-radius:0 0 {R_CARD}px {R_CARD}px;padding:32px 28px 14px 28px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              {body}
            </table>
          </td>
        </tr>
        <tr>
          <td align="center" class="es-page" style="background-color:{LIGHT['page']};color:{LIGHT['muted']};padding:22px 24px 0 24px;">
            {footer_html}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Plain-text rendering
#
# Generated from the same blocks, so the text part cannot fall behind the HTML
# one. Every email is sent multipart/alternative; a text part that said less
# than the HTML would be worse than none.
# ---------------------------------------------------------------------------

def _block_text(block: Dict[str, Any]) -> List[str]:
    kind = block["type"]

    if kind == "heading":
        text = block["text"]
        if block["level"] == 1:
            return [text, "=" * min(len(text), 60), ""]
        return [text, "-" * min(len(text), 60), ""]

    if kind in ("paragraph", "note", "lede"):
        return [block["text"], ""]

    if kind == "divider":
        return ["-" * 40, ""]

    if kind == "fields":
        out = []
        if block.get("title"):
            out.append(f"{block['title']}:")
        out += [f"  {label}: {value}" for label, value in block["rows"]]
        out.append("")
        return out

    if kind == "button":
        url = safe_url(block["url"])
        return ([f"{block['label']}: {url}", ""] if url else [block["label"], ""])

    if kind == "link":
        url = safe_url(block["url"])
        return ([f"{block['label']}: {url}", ""] if url else [])

    if kind == "status":
        word = TONES[block["tone"]]["word"]
        suffix = f" ({word})" if word and word.lower() != block["value"].lower() else ""
        return [f"{block['label']}: {block['value']}{suffix}", ""]

    if kind == "callout":
        title = block.get("title") or TONES[block["tone"]]["word"]
        return ([f"{title}: {block['body']}", ""] if title else [block["body"], ""])

    if kind == "bullets":
        out = []
        for main, sub in block["items"]:
            out.append(f"  * {main}")
            if sub:
                out.append(f"    {sub}")
        out.append("")
        return out

    if kind == "table":
        out = [" | ".join(block["headers"])]
        out.append("-" * min(len(out[0]), 60))
        out += [" | ".join(r) for r in block["rows"]]
        out.append("")
        return out

    if kind == "stats":
        return ["  ".join(f"{k}: {v}" for k, v in block["items"]), ""]

    if kind == "image":
        url = safe_url(block["url"])
        return ([f"[{block['alt']}] {url}", ""] if url else [])

    return []


def render_text(
    *,
    township_name: str,
    title: str,
    blocks: Sequence[Dict[str, Any]],
    footer_lines: Sequence[str] = (),
    tagline: str = DEFAULT_TAGLINE,
) -> str:
    lines: List[str] = []
    # The masthead, in text: the town, then what this is. Assembled from two
    # independently translated strings joined by punctuation rather than by a
    # sentence, so no word order is assumed.
    header = f"{township_name} — {tagline}" if tagline else str(township_name)
    lines += [header, "=" * min(len(header), 60), ""]
    for b in blocks:
        if b:
            lines += _block_text(b)
    tail = [line for line in footer_lines if line]
    if tail:
        lines += ["-" * 40] + list(tail)
    # Collapse runs of blank lines so the text part reads like prose.
    out: List[str] = []
    for line in lines:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


_TAG = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")


def html_to_text(html_body: str) -> str:
    """A last-resort text part, recovered from a rendered HTML body.

    Every email built through `build_email` carries a text part composed from
    the same blocks; this exists only so that the send path can guarantee
    multipart/alternative even for a caller that hands it raw HTML. An HTML-only
    message costs deliverability with every spam filter and gives a
    screen-reader or text-client user nothing at all, so no send is allowed to
    go out without a text half.
    """
    if not html_body:
        return ""
    body = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html_body)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"(?i)</(p|div|tr|h1|h2|h3|li|table)>", "\n", body)
    body = re.sub(r"(?i)<li[^>]*>", "  * ", body)
    body = _TAG.sub("", body)
    body = _html.unescape(body)
    body = "\n".join(line.strip() for line in body.split("\n"))
    return _BLANKS.sub("\n\n", body).strip()


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------

def build_email(
    *,
    subject: str,
    township_name: str,
    blocks: Sequence[Dict[str, Any]],
    logo_url: Optional[str] = None,
    primary_color: str = DEFAULT_BRAND,
    footer_lines: Optional[Sequence[str]] = None,
    language: str = "en",
    preheader: str = "",
    tagline: str = DEFAULT_TAGLINE,
    no_reply_note: str = "Please do not reply directly to this email.",
) -> Dict[str, str]:
    """Render one email. Returns `{"subject", "html", "text"}`.

    This is the only place an outbound HTML document is produced. Anything that
    wants to send mail describes its content as blocks and calls this; nothing
    hand-rolls a shell any more.

    `tagline` is the line under the town name in the masthead. It is a
    parameter rather than a literal because it is a *user-visible sentence*, and
    every one of those has to be able to arrive in the resident's language --
    `email_templates` passes the translated `service_portal` string.
    """
    town = township_name or "311"
    footer = list(footer_lines or [])
    if no_reply_note:
        footer.append(no_reply_note)
    blocks = [b for b in blocks if b]
    return {
        "subject": subject,
        "html": render_html(
            township_name=town,
            logo_url=logo_url,
            primary_color=primary_color,
            title=subject,
            blocks=blocks,
            footer_lines=footer,
            language=language,
            preheader=preheader or "",
            tagline=tagline,
        ),
        "text": render_text(
            township_name=town,
            title=subject,
            blocks=blocks,
            footer_lines=footer,
            tagline=tagline,
        ),
    }
