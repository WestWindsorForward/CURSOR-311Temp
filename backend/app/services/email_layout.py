"""One layout for every email this system sends.

Every outbound message -- resident confirmations, staff assignments, the weekly
digest, connector alerts, road-feed alerts, health escalations -- is described
here as a list of *content blocks* and rendered twice: once into email-safe
HTML and once into readable plain text. Callers never write a document shell,
never pick a colour, and never hand-roll a `<table>`. That is the whole point:
the reason the old emails had no family resemblance is that five modules each
grew their own markup.

Why the HTML looks the way it does
----------------------------------
Email clients are not browsers. Outlook on Windows renders with Word, which
silently drops `border-radius`, `box-shadow`, `linear-gradient`, `rgba()`,
flexbox and grid -- so an email whose structure depends on any of those does
not degrade, it collapses. Nothing in this module emits them. Layout is
tables, spacing is padding and explicit spacer rows, and the whole document is
a fixed 600px column that Word can measure.

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
# like in a dark one. Deliberately no #ffffff and no #000000.
# ---------------------------------------------------------------------------

LIGHT = {
    "page": "#eef1f6",
    "surface": "#fbfcfe",
    "panel": "#f2f5f9",
    "text": "#1f2733",
    "muted": "#5b6675",
    "border": "#d7dde7",
    "link": "#2c4fa8",
}

DARK = {
    "page": "#15191f",
    "surface": "#1e242d",
    "panel": "#262e39",
    "text": "#e7ecf3",
    "muted": "#a8b2c1",
    "border": "#39424f",
    "link": "#9dbcff",
}

# Severity tones. Each carries a *word*, so the meaning survives a greyscale
# print, a colour-blind reader and a screen reader; the colour is decoration on
# top of the word, never instead of it.
TONES: Dict[str, Dict[str, str]] = {
    "neutral":  {"word": "",         "bg": "#f2f5f9", "fg": "#1f2733", "edge": "#c9d2e0",
                 "dbg": "#262e39", "dfg": "#e7ecf3", "dedge": "#414b59"},
    "info":     {"word": "Update",   "bg": "#e8f0fd", "fg": "#1b3c76", "edge": "#a9c4ef",
                 "dbg": "#1f2c44", "dfg": "#cfdffb", "dedge": "#3c5583"},
    "success":  {"word": "Resolved", "bg": "#e6f5ec", "fg": "#17492f", "edge": "#a5d3ba",
                 "dbg": "#1c3327", "dfg": "#bfe4cd", "dedge": "#37624a"},
    "warning":  {"word": "Warning",  "bg": "#fdf1de", "fg": "#6b4406", "edge": "#e8c68a",
                 "dbg": "#3a2f16", "dfg": "#f3d9a8", "dedge": "#6d5726"},
    "critical": {"word": "Critical", "bg": "#fceaea", "fg": "#7c1f1f", "edge": "#eeb0b0",
                 "dbg": "#3b1f21", "dfg": "#f5c6c6", "dedge": "#6f3436"},
}

DEFAULT_BRAND = "#3f5b9c"

MAX_WIDTH = 600

FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")

RTL_LANGUAGES = {"ar", "he", "fa", "ur", "yi", "ps", "dv", "ku", "sd"}


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


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def on_color(background: str) -> str:
    """Readable foreground for text sitting on an arbitrary brand colour.

    Towns choose their own primary colour and some choose pale yellow. White
    button text on that is unreadable, so the decision is made from the actual
    luminance rather than assumed.
    """
    return "#12181f" if _luminance(safe_color(background)) > 0.45 else "#ffffff"


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
    """A call to action. Rendered as a bordered, padded table cell -- the
    'bulletproof' shape -- and always followed by the bare URL so the
    destination is visible to text readers and to clients that strip the
    button."""
    return {"type": "button", "label": str(label), "url": url}


def link(label: str, url: str) -> Dict[str, Any]:
    """An inline link whose text names its destination."""
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
    which is what made the digest's number row stack wrongly in Outlook."""
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

def _td(content: str, *, cls: str = "", style: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<td{c} style="{style}">{content}</td>'


def _row(inner: str) -> str:
    return f"<tr>{inner}</tr>"


def _base_text_style(color: str, size: int = 15, weight: str = "400",
                     line: str = "1.6") -> str:
    return (f"margin:0;font-family:{FONT};font-size:{size}px;line-height:{line};"
            f"font-weight:{weight};color:{color};")


def _block_html(block: Dict[str, Any], brand: str) -> str:
    kind = block["type"]

    if kind == "heading":
        level = block["level"]
        size = {1: 23, 2: 19, 3: 16}[level]
        return _row(_td(
            f'<h{level} class="es-text" style="{_base_text_style(LIGHT["text"], size, "700", "1.35")}">'
            f'{esc(block["text"])}</h{level}>',
            style="padding:0 0 10px 0;"))

    if kind == "paragraph":
        color = LIGHT["muted"] if block["muted"] else LIGHT["text"]
        cls = "es-muted" if block["muted"] else "es-text"
        return _row(_td(
            f'<p class="{cls}" style="{_base_text_style(color)}">{esc(block["text"])}</p>',
            style="padding:0 0 12px 0;"))

    if kind == "note":
        return _row(_td(
            f'<p class="es-muted" style="{_base_text_style(LIGHT["muted"], 13)}">'
            f'{esc(block["text"])}</p>',
            style="padding:0 0 10px 0;"))

    if kind == "divider":
        return _row(_td(
            '<div class="es-rule" style="height:1px;font-size:0;line-height:0;'
            f'background-color:{LIGHT["border"]};color:{LIGHT["border"]};">&nbsp;</div>',
            style="padding:6px 0 18px 0;"))

    if kind == "fields":
        inner = []
        if block.get("title"):
            inner.append(_row(_td(
                f'<p class="es-muted" style="{_base_text_style(LIGHT["muted"], 12, "700")}'
                f'text-transform:uppercase;letter-spacing:.6px;">{esc(block["title"])}</p>',
                style="padding:0 0 10px 0;")))
        last = len(block["rows"]) - 1
        for i, (label, value) in enumerate(block["rows"]):
            border = ("" if i == last else
                      f"border-bottom:1px solid {LIGHT['border']};")
            inner.append(_row(_td(
                f'<p class="es-muted" style="{_base_text_style(LIGHT["muted"], 12, "700")}'
                f'text-transform:uppercase;letter-spacing:.6px;">{esc(label)}</p>'
                f'<p class="es-text" style="{_base_text_style(LIGHT["text"], 15)}padding-top:3px;">'
                f'{esc(value)}</p>',
                cls="es-rule-b" if border else "",
                style=f"padding:10px 0;{border}")))
        table_html = ('<table role="presentation" cellpadding="0" cellspacing="0" '
                      'border="0" width="100%">{rows}</table>').format(rows="".join(inner))
        panel_style = (
            "padding:6px 18px;"
            "background-color:{bg};border:1px solid {edge};color:{fg};".format(
                bg=LIGHT["panel"], edge=LIGHT["border"], fg=LIGHT["text"]))
        panel = _td(table_html, cls="es-panel", style=panel_style)
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'width="100%"><tr>{cell}</tr></table>'.format(cell=panel),
            style="padding:0 0 18px 0;"))

    if kind == "button":
        url = safe_url(block["url"])
        label = esc(block["label"])
        if not url:
            return _row(_td(
                f'<p class="es-text" style="{_base_text_style(LIGHT["text"], 15, "700")}">{label}</p>',
                style="padding:0 0 14px 0;"))
        fg = on_color(brand)
        btn = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="background-color:{brand};color:{fg};padding:13px 26px;">'
            f'<a href="{esc(url)}" style="font-family:{FONT};font-size:15px;font-weight:700;'
            f'color:{fg};text-decoration:none;display:inline-block;">{label}</a>'
            '</td></tr></table>'
        )
        follow = (f'<p class="es-muted" style="{_base_text_style(LIGHT["muted"], 13)}padding-top:9px;">'
                  f'<a class="es-link" href="{esc(url)}" style="color:{LIGHT["link"]};">'
                  f'{esc(url)}</a></p>')
        return _row(_td(btn + follow, style="padding:4px 0 18px 0;"))

    if kind == "link":
        url = safe_url(block["url"])
        if not url:
            return ""
        return _row(_td(
            f'<p class="es-text" style="{_base_text_style(LIGHT["text"])}">'
            f'<a class="es-link" href="{esc(url)}" style="color:{LIGHT["link"]};'
            f'text-decoration:underline;">{esc(block["label"])}</a></p>',
            style="padding:0 0 12px 0;"))

    if kind in ("callout", "status"):
        tone = TONES[block["tone"]]
        word = tone["word"]
        if kind == "status":
            label = esc(block["label"])
            value = esc(block["value"])
            body_html = (
                f'<p style="{_base_text_style(tone["fg"], 12, "700")}'
                f'text-transform:uppercase;letter-spacing:.8px;">{label}</p>'
                f'<p style="{_base_text_style(tone["fg"], 21, "700")}padding-top:5px;">{value}</p>'
            )
        else:
            title = block.get("title") or word
            head = (f'<p style="{_base_text_style(tone["fg"], 13, "700")}'
                    f'text-transform:uppercase;letter-spacing:.6px;">{esc(title)}</p>'
                    if title else "")
            body_html = (head +
                         f'<p style="{_base_text_style(tone["fg"], 15)}'
                         f'{"padding-top:5px;" if title else ""}">{esc(block["body"])}</p>')
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<tr><td class="es-tone-{block["tone"]}" style="background-color:{tone["bg"]};'
            f'color:{tone["fg"]};border:1px solid {tone["edge"]};padding:16px 18px;">'
            f'{body_html}</td></tr></table>',
            style="padding:0 0 18px 0;"))

    if kind == "bullets":
        items = []
        for main, sub in block["items"]:
            sub_html = (f'<br><span class="es-muted" style="color:{LIGHT["muted"]};'
                        f'font-size:13px;">{esc(sub)}</span>') if sub else ""
            items.append(
                f'<li style="{_base_text_style(LIGHT["text"])}padding-bottom:8px;">'
                f'{esc(main)}{sub_html}</li>')
        return _row(_td(
            f'<ul class="es-text" style="margin:0 0 12px 0;padding-left:20px;'
            f'color:{LIGHT["text"]};">{"".join(items)}</ul>',
            style="padding:0;"))

    if kind == "table":
        head_cells = "".join(
            f'<th scope="col" class="es-panel" style="background-color:{LIGHT["panel"]};'
            f'color:{LIGHT["text"]};font-family:{FONT};font-size:12px;font-weight:700;'
            f'text-align:left;text-transform:uppercase;letter-spacing:.5px;'
            f'padding:10px 12px;border-bottom:1px solid {LIGHT["border"]};">{esc(h)}</th>'
            for h in block["headers"])
        body_rows = ""
        for r in block["rows"]:
            body_rows += "<tr>" + "".join(
                f'<td class="es-cell" style="background-color:{LIGHT["surface"]};'
                f'color:{LIGHT["text"]};font-family:{FONT};font-size:14px;'
                f'padding:10px 12px;border-bottom:1px solid {LIGHT["border"]};">{esc(c)}</td>'
                for c in r) + "</tr>"
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%" style="border:1px solid {LIGHT["border"]};">'
            f'<thead><tr>{head_cells}</tr></thead><tbody>{body_rows}</tbody></table>',
            style="padding:0 0 18px 0;"))

    if kind == "stats":
        items = block["items"]
        if not items:
            return ""
        width = int(100 / len(items))
        cells = "".join(
            f'<td class="es-panel" width="{width}%" style="background-color:{LIGHT["panel"]};'
            f'color:{LIGHT["text"]};border:1px solid {LIGHT["border"]};padding:14px 8px;'
            f'text-align:center;">'
            f'<p class="es-text" style="{_base_text_style(LIGHT["text"], 22, "700", "1.2")}">'
            f'{esc(v)}</p>'
            f'<p class="es-muted" style="{_base_text_style(LIGHT["muted"], 12)}padding-top:4px;">'
            f'{esc(k)}</p></td>'
            for k, v in items)
        return _row(_td(
            '<table role="presentation" cellpadding="0" cellspacing="4" border="0" '
            f'width="100%"><tr>{cells}</tr></table>',
            style="padding:0 0 18px 0;"))

    if kind == "image":
        url = safe_url(block["url"])
        if not url:
            return ""
        w = min(int(block["width"]), MAX_WIDTH - 72)
        return _row(_td(
            f'<img src="{esc(url)}" alt="{esc(block["alt"])}" width="{w}" '
            f'style="display:block;width:100%;max-width:{w}px;height:auto;'
            f'border:1px solid {LIGHT["border"]};">',
            style="padding:0 0 18px 0;"))

    return ""


def _dark_css() -> str:
    """The dark palette, handed to clients that honour `prefers-color-scheme`.

    `!important` throughout because inline styles win otherwise, and the inline
    styles have to be there for the clients that support nothing else. The
    `[data-ogsc]` duplicates are Gmail's dark transform, which rewrites the
    attribute rather than matching the media query.
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
      {p} .es-cell {{ background-color:{DARK['surface']} !important; color:{DARK['text']} !important; border-color:{DARK['border']} !important; }}
      {p} .es-rule {{ background-color:{DARK['border']} !important; }}
      {p} .es-rule-b {{ border-color:{DARK['border']} !important; }}
      {p} .es-link, {p} .es-link * {{ color:{DARK['link']} !important; }}
""" + "".join(
            f"      {p} .es-tone-{name} {{ background-color:{t['dbg']} !important;"
            f" color:{t['dfg']} !important; border-color:{t['dedge']} !important; }}\n"
            f"      {p} .es-tone-{name} p {{ color:{t['dfg']} !important; }}\n"
            for name, t in TONES.items())

    rules.append("@media (prefers-color-scheme: dark) {" + block("") + "}")
    rules.append(block("[data-ogsc]"))
    return "\n".join(rules)


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
) -> str:
    brand = safe_color(primary_color)
    brand_fg = on_color(brand)
    logo = safe_url(logo_url)
    dir_attr = ' dir="rtl"' if (language or "en").split("-")[0] in RTL_LANGUAGES else ""
    town = esc(township_name or "311")

    body = "".join(_block_html(b, brand) for b in blocks if b)

    logo_html = ""
    if logo:
        logo_html = (f'<img src="{esc(logo)}" alt="{town} logo" width="140" height="44" '
                     f'style="display:block;margin:0 auto 12px auto;width:auto;height:44px;'
                     f'max-width:140px;border:0;">')

    footer_html = "".join(
        f'<p class="es-muted" style="{_base_text_style(LIGHT["muted"], 12, "400", "1.5")}'
        f'padding-bottom:5px;">{esc(line)}</p>'
        for line in footer_lines if line)

    pre = ""
    if preheader:
        pre = (f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
               f'max-width:0;opacity:0;overflow:hidden;">{esc(preheader)}'
               f'{"&#8199;&#65279;&#847; " * 30}</div>')

    return f"""<!DOCTYPE html>
<html lang="{esc((language or 'en').split('-')[0])}"{dir_attr} xmlns="http://www.w3.org/1999/xhtml">
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
    table {{ border-collapse:collapse; }}
    img {{ -ms-interpolation-mode:bicubic; }}
    @media only screen and (max-width: 620px) {{
      .es-shell {{ width:100% !important; }}
      .es-pad {{ padding-left:18px !important; padding-right:18px !important; }}
    }}
{_dark_css()}
  </style>
</head>
<body class="es-page" style="margin:0;padding:0;width:100%;background-color:{LIGHT['page']};color:{LIGHT['text']};font-family:{FONT};">
{pre}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" class="es-page" style="background-color:{LIGHT['page']};color:{LIGHT['text']};">
  <tr>
    <td align="center" style="padding:28px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{MAX_WIDTH}" class="es-shell" style="width:{MAX_WIDTH}px;max-width:{MAX_WIDTH}px;">
        <tr>
          <td align="center" style="background-color:{brand};color:{brand_fg};padding:26px 24px;">
            {logo_html}
            <h1 style="{_base_text_style(brand_fg, 21, '700', '1.3')}">{town}</h1>
            <p style="{_base_text_style(brand_fg, 13)}padding-top:5px;">311 Service Portal</p>
          </td>
        </tr>
        <tr>
          <td class="es-card es-pad" style="background-color:{LIGHT['surface']};color:{LIGHT['text']};border:1px solid {LIGHT['border']};border-top:0;padding:26px 28px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              {body}
            </table>
          </td>
        </tr>
        <tr>
          <td align="center" class="es-page" style="background-color:{LIGHT['page']};color:{LIGHT['muted']};padding:18px 20px 0 20px;">
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

    if kind in ("paragraph", "note"):
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
) -> str:
    lines: List[str] = []
    header = f"{township_name} 311"
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
    no_reply_note: str = "This is an automated message. Please do not reply directly to it.",
) -> Dict[str, str]:
    """Render one email. Returns `{"subject", "html", "text"}`.

    This is the only place an outbound HTML document is produced. Anything that
    wants to send mail describes its content as blocks and calls this; nothing
    hand-rolls a shell any more.
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
        ),
        "text": render_text(
            township_name=town,
            title=subject,
            blocks=blocks,
            footer_lines=footer,
        ),
    }
