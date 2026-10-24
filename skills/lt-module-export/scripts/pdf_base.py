# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ReportLab base for the module PDFs: fonts, themes, page frame and paragraph styles.

``module_pdf.py`` builds on this file. It holds the page frame (A4, a 0.25 inch margin,
a running title and page number on a filled background), the paragraph styles, the
small Markdown-to-ReportLab markup and the font and theme lookups. It writes no PDF
Author unless ``Doc(..., author=...)`` is given one; ``module_pdf.py`` passes
``pdf.author`` from the config, which is empty by default.

Fonts come from ``ltcore.fonts`` (``fonts.sans`` and ``fonts.mono`` in the config; the
bundled libre defaults when unset). Face names registered with ReportLab: ``Sans``,
``Sans-B``, ``Sans-I``, ``Sans-BI``, ``Sans-SB`` (semibold), ``Mono``, ``Mono-B`` and
``Mono-I``. A face the family does not have falls back to its upright or bold file.
Themes come from ``ltcore.palettes.derive_theme`` for each palette id in ``style.palettes``;
``LIGHT`` and ``DARK`` are the built-in fallbacks when the palette module is absent.

``module_pdf.py`` overrides four behaviours of the plain template this file defines:

1. A paragraph never breaks across a page. It moves whole unless it is taller than 90
   percent of a page.
2. Heading glue always attaches the next content unit, including oversized units.
3. A "continued" label lands on the page it labels, inside the unit it labels, so the
   label cannot stay at the foot of the previous page.
4. A table breaks only between rows, and the next page repeats its header row.

Public names: ``MARGIN``, ``LIGHT``, ``DARK`` (also ``LATTE`` and ``FRAPPE``, aliases
kept for code written against the original template), ``to_pdf_markup``, ``plain``,
``styles``, ``Doc`` and ``_REG`` (true once ``register_fonts`` has run).
"""
from __future__ import annotations

import html
import re

try:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
    MISSING_DEPENDENCY = ""
except ModuleNotFoundError as _exc:       # lets --help and the import itself work
    MISSING_DEPENDENCY = _exc.name or "reportlab"
    HexColor = ParagraphStyle = TTFont = Frame = PageTemplate = None
    pdfmetrics = None
    TA_LEFT = 0
    A4 = (595.2755905511812, 841.8897637795277)
    inch = 72.0

    class BaseDocTemplate:               # stand-in; Doc cannot be built without ReportLab
        pass

MARGIN = 0.25 * inch
_REG = False

# How an exported page names itself in a citation. module_pdf.py's parser treats a
# parenthesised group that opens with this text as provenance.
SELF_REFERENCE_TEXT = "this module"

SANS, SANS_B, SANS_I, SANS_BI, SANS_SB = "Sans", "Sans-B", "Sans-I", "Sans-BI", "Sans-SB"
MONO, MONO_B, MONO_I = "Mono", "Mono-B", "Mono-I"

# face name -> (role, weight, italic)
FACE_SPEC = {
    SANS: ("sans", 400, False), SANS_B: ("sans", 700, False), SANS_I: ("sans", 400, True),
    SANS_BI: ("sans", 700, True), SANS_SB: ("sans", 600, False),
    MONO: ("mono", 400, False), MONO_B: ("mono", 700, False), MONO_I: ("mono", 400, True),
}
# when a face resolves to nothing, use the first of these that did
FALLBACK = {
    SANS_SB: (SANS_B, SANS), SANS_BI: (SANS_B, SANS_I, SANS), SANS_I: (SANS,),
    SANS_B: (SANS,), MONO_B: (MONO,), MONO_I: (MONO,),
}
REQUIRED_FACES = (SANS, MONO)


class FontUnavailable(RuntimeError):
    """No usable font file for a required face. The message says what was tried."""


def require_reportlab() -> None:
    """Exit 3 with the install hint when ReportLab is missing."""
    if MISSING_DEPENDENCY:
        try:
            import ltcore
            hint = ltcore.missing_hint([MISSING_DEPENDENCY])
        except Exception:
            hint = f"lt-suite: missing Python package: {MISSING_DEPENDENCY}"
        raise SystemExit(f"{hint}\nPDF export needs reportlab, Pillow, fontTools and pypdfium2.")


# ------------------------------------------------------------------ fonts
def _call_resolve(role: str, weight: int, italic: bool, cfg):
    """One call into ltcore.fonts: the static TrueType file for a role, weight and
    italic flag (a variable font is cut into a static instance). Returns a path string,
    or None when italic was asked for and the family has no italic file."""
    from ltcore import fonts
    path, font = fonts.face_file(role, cfg, weight=weight, italic=italic, output="pdf", os_fallback=True)
    if italic and not getattr(font, "italic", False):
        return None
    return str(path)


def resolve_faces(cfg=None) -> tuple[dict[str, tuple[str, int]], list[str]]:
    """({face name: (file, sub-font index)}, notes). Raises FontUnavailable when the
    regular sans or mono cannot be found, or only a font ReportLab cannot read exists
    (ReportLab reads TrueType outlines, not CFF)."""
    try:
        from ltcore import fonts  # noqa: F401  (also pulls in the bundled font files)
    except ImportError as exc:
        raise FontUnavailable(f"ltcore.fonts is not available ({exc})") from None
    found: dict[str, tuple[str, int]] = {}
    notes: list[str] = []
    errors: dict[str, str] = {}
    for face, (role, weight, italic) in FACE_SPEC.items():
        try:
            path = _call_resolve(role, weight, italic, cfg)
        except Exception as exc:         # any resolver failure: try the fallback face
            path, errors[face] = None, f"{type(exc).__name__}: {exc}"
        if path and _readable(path):
            allowed, reason = fonts.pdf_embeddable(path)
            if allowed:
                found[face] = (path, 0)
                resolved = fonts.find(role, cfg, weight=weight, italic=italic, output="pdf", os_fallback=True)
                if resolved.fallback or resolved.os_fallback:
                    notes.append(f"{face}: {resolved.source}")
                if resolved.variable and fonts.licence_class(resolved) != "modify":
                    notes.append(f"{face}: variable font used as is; default weight for every face")
            else:
                errors[face] = reason
        elif path:
            errors[face] = f"{path} has no TrueType outlines"
    for face in FACE_SPEC:
        if face in found:
            continue
        for alt in FALLBACK.get(face, ()):
            if alt in found:
                found[face] = found[alt]
                notes.append(f"{face} not found; using the {alt} file")
                break
    for face in REQUIRED_FACES:
        if face not in found:
            raise FontUnavailable(
                f"no usable {FACE_SPEC[face][0]} font for PDF export ({errors.get(face, 'not found')}). "
                f"Set fonts.pdf.{FACE_SPEC[face][0]} in the config, "
                f"or run the lt-suite-setup skill.")
    return found, notes


def _readable(path: str) -> bool:
    """True when the file has a glyf table (TrueType outlines)."""
    try:
        from fontTools.ttLib import TTFont as FTFont
        with FTFont(path, lazy=True, fontNumber=0) as font:
            return "glyf" in font
    except Exception:
        return False


def register_fonts(faces: dict[str, tuple[str, int]] | None = None, cfg=None) -> list[str]:
    """Register the eight faces and their families with ReportLab. Returns notes."""
    global _REG
    require_reportlab()
    notes: list[str] = []
    if faces is None:
        faces, notes = resolve_faces(cfg)
    from ltcore import fonts
    registrations = {}
    for name, (path, idx) in faces.items():
        allowed, reason = fonts.pdf_embeddable(path)
        if not allowed:
            raise FontUnavailable(f"{name}: {reason}")
        registrations[name] = TTFont(name, path, subfontIndex=idx)
    # ReportLab ignores a dynamic registration when its alias already exists.
    # A second export in this process must use its own config and source bytes.
    for name, font in registrations.items():
        pdfmetrics._fonts.pop(name, None)
        pdfmetrics._dynFaceNames.pop(font.face.name, None)
    for font in registrations.values():
        pdfmetrics.registerFont(font)
    pdfmetrics.registerFontFamily(SANS, normal=SANS, bold=SANS_B, italic=SANS_I, boldItalic=SANS_BI)
    pdfmetrics.registerFontFamily(MONO, normal=MONO, bold=MONO_B, italic=MONO_I, boldItalic=MONO_I)
    for face in (SANS_B, SANS_SB, SANS_I, SANS_BI):
        pdfmetrics.registerFontFamily(face, normal=face, bold=SANS_B, italic=SANS_I, boldItalic=SANS_BI)
    _REG = True
    return notes


# Glyph fallback. The default sans font lacks some characters that module pages use
# (the warning sign, arrows). A character the sans faces lack is drawn from the mono
# font, then from an installed symbol font, then replaced by an ASCII stand-in. The
# plan is global because Paragraph markup is built far from where fonts are registered.
SYMBOL = "Sym"
SUBSTITUTES = {"⚠": "!", "→": "->", "←": "<-", "↔": "<->", "↑": "^", "↓": "v", "▲": "^",
               "●": "*", "✓": "ok", "⇄": "<->", "⇌": "<=>", "⇒": "=>"}
SYMBOL_FAMILIES = ("Noto Sans Symbols 2", "DejaVu Sans")
GLYPH_RUNS: dict[str, str] = {}      # character -> face that draws it
GLYPH_SUBS: dict[str, str] = {}      # character -> replacement text
_TAG = re.compile(r"(<[^>]*>)")


def _cmap(path: str, index: int = 0) -> set[int]:
    from fontTools.ttLib import TTFont as FTFont
    with FTFont(path, fontNumber=index, lazy=True) as font:
        return set().union(*(table.cmap for table in font["cmap"].tables))


def find_symbol_font(chars: set[str], cfg=None) -> str | None:
    """Resolve a libre symbol family through ltcore and require complete character coverage."""
    from ltcore import fonts
    for family in SYMBOL_FAMILIES:
        try:
            path = str(fonts.resolve("sans", cfg=cfg, family=family, fallback=False))
            if fonts.pdf_embeddable(path)[0] and _readable(path) and all(ord(c) in _cmap(path) for c in chars):
                return path
        except (fonts.FontError, OSError):
            continue
    return None


def plan_glyphs(chars: set[str], faces: dict[str, tuple[str, int]], cfg=None) -> dict:
    """Decide how every character of the page is drawn and register the symbol font.

    Returns ``{"runs": {char: face}, "subs": {char: text}, "missing": "chars"}`` and sets
    the module's GLYPH_RUNS and GLYPH_SUBS. A character in neither list nor in
    ``missing`` is covered by the sans faces. ``missing`` is non-empty only when no
    font and no stand-in covers a character; the caller refuses to build then."""
    GLYPH_RUNS.clear()
    GLYPH_SUBS.clear()
    sans: set[int] | None = None
    for face in (SANS, SANS_B, SANS_I, SANS_SB):
        cm = _cmap(*faces[face])
        sans = cm if sans is None else sans & cm
    need = {c for c in chars if ord(c) not in sans}
    mono = _cmap(*faces[MONO])
    for c in sorted(need):
        if ord(c) in mono:
            GLYPH_RUNS[c] = MONO
    rest = {c for c in need if c not in GLYPH_RUNS}
    symbol = find_symbol_font(rest, cfg) if rest else None
    if symbol:
        require_reportlab()
        pdfmetrics.registerFont(TTFont(SYMBOL, symbol))
        have = _cmap(symbol)
        for c in sorted(rest):
            if ord(c) in have:
                GLYPH_RUNS[c] = SYMBOL
        rest = {c for c in rest if c not in GLYPH_RUNS}
    for c in sorted(rest):
        if c in SUBSTITUTES:
            GLYPH_SUBS[c] = SUBSTITUTES[c]
    missing = "".join(sorted(c for c in rest if c not in GLYPH_SUBS))
    return {"runs": dict(GLYPH_RUNS), "subs": dict(GLYPH_SUBS), "missing": missing}


def fallback(markup: str) -> str:
    """ReportLab markup with each character the sans faces lack drawn from its fallback
    face, or replaced by its stand-in. Tags are left alone; ``markup`` must already be
    escaped, so a "<" inside a text node never occurs."""
    if not (GLYPH_RUNS or GLYPH_SUBS):
        return markup
    out: list[str] = []
    for part in _TAG.split(markup):
        if part.startswith("<") and part.endswith(">"):
            out.append(part)
            continue
        face = None
        for ch in part:
            ch = GLYPH_SUBS.get(ch, ch)
            want = GLYPH_RUNS.get(ch) if len(ch) == 1 else None
            if want != face:
                out.append("</font>" if face else "")
                out.append(f'<font face="{want}">' if want else "")
                face = want
            out.append(ch)
        if face:
            out.append("</font>")
    return "".join(out)


def draw_text(canv, x: float, y: float, text: str, face: str, size: float) -> None:
    """Draw one line with the glyph plan applied: stand-ins replace characters no font
    has, and characters in GLYPH_RUNS are drawn from their fallback face."""
    text = apply_subs(text)
    run, run_face = "", face
    for ch in text + "\0":
        want = GLYPH_RUNS.get(ch, face) if ch != "\0" else None
        if want != run_face and run:
            canv.setFont(run_face, size)
            canv.drawString(x, y, run)
            x += pdfmetrics.stringWidth(run, run_face, size)
            run = ""
        run_face = want
        if ch != "\0":
            run += ch


def apply_subs(text: str, subs: dict[str, str] | None = None) -> str:
    """Plain text with the stand-ins applied, to compare with the text a PDF carries."""
    subs = GLYPH_SUBS if subs is None else subs
    return "".join(subs.get(c, c) for c in text) if subs else text


# ------------------------------------------------------------------ themes
# Keys: base (page fill), mantle (banded rows), text, sub (secondary text), over (faint
# text), surface (grid lines and header fill), blue (links, headings), mauve (block
# headings), peach (code spans), red, green, yellow (the warning sign), dark.
LIGHT = dict(name="light", base="#ffffff", mantle="#f4f5f7", text="#1b1f24", sub="#4a5159",
             over="#59616a", surface="#d5d9de", blue="#0b57c4", mauve="#6e32b0",
             peach="#a34a00", red="#b3261e", green="#2c6b2f", yellow="#946200", dark=False)
DARK = dict(name="dark", base="#1e2127", mantle="#181b20", text="#e6e8eb", sub="#b4bac2",
            over="#9aa1ab", surface="#343a43", blue="#8ab4f8", mauve="#c4a2f0",
            peach="#f4a261", red="#f28b82", green="#8fd19a", yellow="#f2c94c", dark=True)
LATTE, FRAPPE = LIGHT, DARK            # names of the original template's two themes

THEME_KEYS = ("base", "mantle", "text", "sub", "over", "surface", "blue", "mauve", "peach",
              "red", "green", "yellow")
TEXT_KEYS = ("text", "sub", "over", "blue", "mauve", "peach", "red", "green", "yellow")
MIN_TEXT_CONTRAST = 4.5


def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(c) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in c)


def luminance(h: str) -> float:
    def lin(v):
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio of two #rrggbb colours."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """Colour a moved fraction t of the way to colour b."""
    return _hex(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))))


def ensure_legible(theme: dict) -> tuple[dict, list[str]]:
    """A copy of ``theme`` whose text colours reach 4.5:1 on both ``base`` and ``mantle``.
    A failing colour moves toward ``text`` in 10 percent steps; ``text`` itself is
    checked last and may fall back to black or white. Returns (theme, adjusted keys)."""
    t = dict(theme)
    adjusted: list[str] = []
    for key in ("text",) + tuple(k for k in TEXT_KEYS if k != "text"):
        c = t[key]
        ok = lambda col: min(contrast(col, t["base"]), contrast(col, t["mantle"])) >= MIN_TEXT_CONTRAST
        if key == "text" and not ok(c):
            c = "#000000" if luminance(t["base"]) > 0.4 else "#ffffff"
        else:
            for _ in range(30):
                if ok(c):
                    break
                c = mix(c, t["text"], 0.1)
        if c != t[key]:
            adjusted.append(key)
            t[key] = c
    for _ in range(30):                  # table header text sits on `surface`
        if contrast(t["text"], t["surface"]) >= MIN_TEXT_CONTRAST:
            break
        t["surface"] = mix(t["surface"], t["base"], 0.2)
        if "surface" not in adjusted:
            adjusted.append("surface")
    return t, adjusted


class ThemeUnavailable(RuntimeError):
    """A configured palette cannot be turned into a PDF theme. The message names why."""


def _theme_from_palette(palette_id: str, cfg):
    """The PDF theme for a palette id, from ltcore.palettes.derive_theme, or None when
    ltcore.palettes cannot be imported.

    Structure: canvas -> base, cl_fill -> mantle, ink -> text, muted -> sub. The faint
    colour is muted moved a fifth toward the page, the grid and header colour is the
    fill moved a quarter toward the border. Accent colours are the derived theme's
    accents of the same names (blue, mauve, peach, red, green, yellow)."""
    try:
        from ltcore import palettes
    except ImportError:
        return None
    try:
        d = palettes.derive_theme(palette_id, cfg)
    except palettes.PaletteError as exc:
        raise ThemeUnavailable(str(exc)) from None
    acc = d.get("accents") or {}
    base, mantle, text, sub = d["canvas"], d["cl_fill"], d["ink"], d["muted"]
    theme = dict(name=palette_id, base=base, mantle=mantle, text=text, sub=sub,
                 over=mix(sub, base, 0.2), surface=mix(mantle, d["cl_stroke"], 0.25))
    for key in ("blue", "mauve", "peach", "red", "green", "yellow"):
        theme[key] = acc.get(key) or text
    theme["dark"] = luminance(base) < 0.4
    return theme


def print_variant(theme: dict) -> dict:
    from ltcore.palettes import print_variant as variant
    return variant(theme)


def pdf_themes(cfg) -> tuple[list[tuple[str, str, dict]], list[str]]:
    """[(palette id, "print" | "screen", theme)] for every PDF to make, print palette
    first, plus notes. The set is ``style.palettes`` plus ``style.print_palette``.
    Without ltcore.palettes the built-in light (print) and dark (screen) themes are used."""
    get = (lambda k, d=None: cfg.get(k, d)) if cfg is not None else (lambda k, d=None: d)
    from ltcore.palettes import pdf_palette_ids
    ids = pdf_palette_ids(cfg)
    printp = get("style.print_palette", "") or ""
    notes: list[str] = []
    out: list[tuple[str, str, dict]] = []
    if ids:
        printp = printp or ids[0]
        for pid in [printp] + [p for p in ids if p != printp]:
            theme = _theme_from_palette(pid, cfg)       # ThemeUnavailable propagates
            if theme is None:
                break
            theme, adj = ensure_legible(theme)
            if adj:
                notes.append(f"{pid}: moved {', '.join(adj)} toward the text colour to reach "
                             f"{MIN_TEXT_CONTRAST}:1")
            if pid == printp:
                theme = print_variant(theme)
            out.append((pid, "print" if pid == printp else "screen", theme))
        else:
            return out, notes
    notes.append("palette themes unavailable; using the built-in light and dark themes")
    return [("light", "print", dict(LIGHT)), ("dark", "screen", dict(DARK))], notes


# ------------------------------------------------------------------ markup
INLINE = re.compile(r'(\*\*.+?\*\*|\*[^*]+?\*|`[^`]*`)')


def to_pdf_markup(t: str, theme: dict) -> str:
    """The small Markdown subset (bold, italic, code) as ReportLab inline markup.
    Recurses, so code nested inside bold is still code."""
    out = []
    for part in INLINE.split(t):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append(f'<b>{to_pdf_markup(part[2:-2], theme)}</b>')
        elif part.startswith("`") and part.endswith("`"):
            inner = part[1:-1]
            face = MONO if inner.isascii() else SANS_SB
            out.append(f'<font face="{face}" color="{theme["peach"]}">{html.escape(inner)}</font>')
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append(f'<i>{to_pdf_markup(part[1:-1], theme)}</i>')
        else:
            out.append(html.escape(part))
    return "".join(out)


def plain(t: str) -> str:
    """The same Markdown with its markers removed."""
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'\*([^*]+?)\*', r'\1', t)
    return re.sub(r'`([^`]*)`', r'\1', t)


# ------------------------------------------------------------------ page
class Doc(BaseDocTemplate):
    """A4 page with a themed background, a running title and a page number.

    ``afterFlowable`` records ``(group, index, page)`` for every tagged flowable in
    ``self.placed``, which the "continued" pass of ``module_pdf.py`` reads. ``author``
    defaults to empty; ``**kw`` goes to ReportLab (subject, keywords, creator)."""

    def __init__(self, path, theme, title, author="", **kw):
        require_reportlab()
        self.theme, self.doc_title, self.placed = theme, title, []
        BaseDocTemplate.__init__(self, path, pagesize=A4, leftMargin=MARGIN,
                                 rightMargin=MARGIN, topMargin=MARGIN,
                                 bottomMargin=MARGIN + 10, title=title, author=author, **kw)
        frame = Frame(MARGIN, MARGIN + 10, A4[0] - 2 * MARGIN, A4[1] - 2 * MARGIN - 10,
                      id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=self._deco)])

    def afterFlowable(self, flowable):
        g = getattr(flowable, "_grp", None)
        if g is not None:
            self.placed.append((g, getattr(flowable, "_oidx", -1), self.page))

    def _deco(self, canv, doc):
        t = self.theme
        canv.saveState()
        canv.setFillColor(HexColor(t["base"]))
        canv.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canv.setFillColor(HexColor(t["over"]))
        canv.setFont(MONO, 7.5)
        canv.drawRightString(A4[0] - MARGIN, MARGIN + 1, f"{doc.page}")
        draw_text(canv, MARGIN, MARGIN + 1, self.doc_title, SANS, 7.5)
        canv.restoreState()


def styles(theme: dict) -> dict:
    """The paragraph styles, one per node kind, coloured from ``theme``."""
    require_reportlab()
    t = theme

    def S(name, size, colour, font=SANS, lead=None, space_b=0, space_a=0, indent=0, bullet=None):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=lead or size * 1.32,
                              textColor=HexColor(colour), alignment=TA_LEFT,
                              spaceBefore=space_b, spaceAfter=space_a, leftIndent=indent,
                              bulletIndent=max(0, indent - 11), bulletFontName=SANS,
                              bulletFontSize=size, allowWidows=0, allowOrphans=0)
    return dict(
        block=S("block", 16, t["mauve"], SANS_B, space_b=13, space_a=5),
        week=S("week", 12, t["blue"], SANS_SB, space_b=9, space_a=3, indent=4),
        group=S("group", 11, t["green"], SANS_SB, space_b=6, space_a=2, indent=8),
        item=S("item", 10, t["text"], SANS, space_b=1.2, space_a=0, indent=26),
        due=S("due", 8.5, t["peach"], MONO, space_b=0, space_a=1, indent=38),
        due_p=S("due_p", 8.5, t["peach"], SANS, space_b=0, space_a=1, indent=38),
        detail=S("detail", 10, t["sub"], SANS, space_b=0, space_a=1, indent=38),
        warn=S("warn", 10, t["yellow"], SANS, space_b=1.2, space_a=1, indent=26),
        title=S("title", 20, t["text"], SANS_B, space_a=2),
        subtitle=S("subtitle", 9.5, t["sub"], SANS, space_a=10),
        cont=S("cont", 9.5, t["over"], SANS_I, space_b=0, space_a=3, indent=8),
    )
