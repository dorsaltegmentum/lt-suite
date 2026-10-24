# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typeset one Logseq module page into A4 PDFs for configured palettes.

Run QC after building, then clean and verify final PDFs unless --draft applies.

CLI:
    module_pdf.py PAGE --out DIR [--assets DIR ...] [--render DIR]
                  [--no-qc] [--draft] [--config FILE] [--data-dir DIR]

Exit codes:
    0  Built and QC passed, and finalised unless --draft.
    1  QC failed, a rail refused, a glyph is missing, or the final pass failed
       or changed what a PDF shows.
    3  Usage, file, font or dependency error.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
SELF = Path(__file__).resolve().parent
sys.path.insert(0, str(SELF / "_shared"))
sys.path.insert(0, str(SELF))
import ltcore; _MISSING = ltcore.bootstrap()

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile

import pdf_base as PW
from ltcore import config as config_mod

try:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph, Table, TableStyle, KeepTogether, Flowable
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from PIL import Image as PILImage, ImageChops
    import fontTools.ttLib  # noqa: F401  (probe: PDF export needs fontTools)
    MISSING_DEPENDENCY = ""
except ModuleNotFoundError as e:
    MISSING_DEPENDENCY = e.name
    Flowable = Paragraph = Table = object
    A4 = PW.A4

AVAIL_W = A4[0] - 2 * PW.MARGIN
AVAIL_H = A4[1] - 2 * PW.MARGIN - 10
CAP = AVAIL_H * 0.45
IMG_MAX_H = AVAIL_H * 0.6
IMG_MIN_DPI = 150
IMG_MIN_SCALE = 0.75
IMG_RADIUS_FRAC = 0.03
IMG_RADIUS_MIN, IMG_RADIUS_MAX = 4.0, 10.0
NOSPLIT = 0.9
STEP = 12
PASSES = 8

IMAGE_DIRS: list[str] = []
IMAGE_CACHE: str = ""
_CONT = None
CFG = None
_SVG_NOTE = False


def expand(p):
    return Path(os.path.expanduser(str(p)))


class FitImage(Flowable):
    """Keep each image and caption together, shrinking within the configured limit."""

    def __init__(self, path, w, h, cap, vector=False):
        Flowable.__init__(self)
        self.path, self.w0, self.h0, self.cap, self.s = path, w, h, cap, 1.0
        self.vector = vector
        self.hAlign = "CENTER"

    def _cap_h(self, aW):
        return (self.cap.wrap(aW, 1e6)[1] + self.cap.getSpaceBefore()
                + self.cap.getSpaceAfter())

    def wrap(self, aW, aH):
        ch = self._cap_h(aW)
        self.s = 1.0
        if self.h0 + ch > aH:
            s = (aH - ch) / self.h0
            if s >= IMG_MIN_SCALE:
                self.s = s
        self.width, self.height = aW, self.h0 * self.s + ch
        return self.width, self.height

    def draw(self):
        ch = self._cap_h(self.width)
        w, h = self.w0 * self.s, self.h0 * self.s
        x, c = (self.width - w) / 2, self.canv
        if self.vector:
            a, b, cc, d, e, f = c._currentMatrix
            c._doctemplate.vectors.append(dict(
                page=c.getPageNumber() - 1, x=a * x + cc * ch + e, y=b * x + d * ch + f,
                w=a * w, h=d * h, r=img_radius(w, h), svg=self.path))
            self.cap.wrap(self.width, 1e6)
            self.cap.drawOn(self.canv, 0, self.cap.getSpaceAfter())
            return
        c.saveState()
        p = c.beginPath()
        p.roundRect(x, ch, w, h, img_radius(w, h))
        c.clipPath(p, stroke=0, fill=0)
        c.drawImage(self.path, x, ch, w, h)
        c.restoreState()
        self.cap.wrap(self.width, 1e6)
        self.cap.drawOn(self.canv, 0, self.cap.getSpaceAfter())


def img_radius(w: float, h: float) -> float:
    """Clamp the image corner radius using its shorter displayed side."""
    return max(IMG_RADIUS_MIN, min(IMG_RADIUS_MAX, IMG_RADIUS_FRAC * min(w, h)))


PROV_START = (PW.SELF_REFERENCE_TEXT, "this practical", "**", "[")
NODE = re.compile(r"^(\t*)-(?: (.*))?$")
PROP = re.compile(r"^([\w-]+)::\s*(.*)$")
IMG = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)(?:\{[^}]*\})?\s*(.*)$")
CELL = re.compile(r"^\*\*(?:row )?(\d+)\.? · (.+?)\*\* — (.*)$")
CELL_ANY = re.compile(r"^\*\*(.+?) · (.+?)\*\* — (.*)$")
ILO = re.compile(r"^(\d+) :: (.*)$")
HEAD = re.compile(r"^(#{2,3})(?: (.*))?$")
TIER = re.compile(r"^Tier \d → \d:")
TAG = re.compile(r"^T[1-4]:")
PIPE_SEP = re.compile(r"^\|?(\s*:?-{2,}:?\s*\|)+\s*(:?-{2,}:?\s*)?$")
ITALIC = re.compile(r"^\*([^*].*[^*]|[^*])\*$")
# Link targets accept one balanced parenthesis level for DOI addresses.
LINK = re.compile(r"\[\[([^\]]+)\]\]|\[([^\]]+)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)")


def peel(text):
    """Separate recognised trailing provenance groups while preserving ordinary claim parentheses."""
    provs, t = [], text.rstrip()
    while t and t[-1] in ")]":
        close = t[-1]
        open_ = "(" if close == ")" else "["
        depth, j = 0, len(t) - 1
        while j >= 0:
            if t[j] == close:
                depth += 1
            elif t[j] == open_:
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j <= 0 or t[j - 1] != " ":
            break
        inner = t[j + 1:-1]
        if not (inner.startswith(PROV_START) if close == ")" else TAG.match(inner)):
            break
        provs.insert(0, inner)
        t = t[:j].rstrip()
    return t, provs


def display_heading(h):
    """Shorten truncated headings at a colon, comma, or whole word."""
    if not h.endswith("…"):
        return h
    body = h[:-1]
    if ": " in body:
        return body.split(": ", 1)[0]
    if ", " in body:
        return body.rsplit(", ", 1)[0] + "…"
    return body.rsplit(" ", 1)[0] + "…"


class Node:
    __slots__ = ("depth", "text", "props", "body", "kids")

    def __init__(self, depth, text):
        self.depth, self.text, self.props, self.body, self.kids = depth, text, {}, [], []


def outline(path):
    """Read Logseq blocks, initial properties, and continuation bodies into a tree."""
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    props, i = {}, 0
    while i < len(lines) and not lines[i].startswith("-"):
        m = PROP.match(lines[i])
        if m:
            props[m[1]] = m[2]
        i += 1
    root, last = Node(-1, ""), None
    stack = [root]
    for ln in lines[i:]:
        m = NODE.match(ln)
        if m:
            n = Node(len(m[1]), (m[2] or "").rstrip())
            while stack[-1].depth >= n.depth:
                stack.pop()
            stack[-1].kids.append(n)
            stack.append(n)
            last = n
            continue
        if last is None:
            continue
        s = ln.lstrip("\t")
        s = s[2:] if s.startswith("  ") else s.strip()
        if not s.strip():
            last.body.append("")
            continue
        pm = PROP.match(s)
        if pm and not any(last.body):
            last.props[pm[1]] = pm[2]
            continue
        last.body.append(s.rstrip())
    todo = [root]
    while todo:
        n = todo.pop()
        todo += n.kids
        while n.body and not n.body[-1]:
            n.body.pop()
        while n.body and not n.body[0]:
            n.body.pop(0)
    return props, root


def parse(path):
    """Group outline nodes beneath headings while retaining properties and front matter."""
    props, root = outline(path)
    front, blocks, blk = [], [], None
    for n in root.kids:
        if n.text.startswith("# "):
            blk = {"title": n.text[2:], "kids": list(n.kids)}
            blocks.append(blk)
        elif n.text in ("", "---") and not n.kids and not n.body:
            continue
        elif blk is None:
            assert not n.kids and not n.body, f"front line with children: {n.text!r}"
            front.append(n.text)
        else:
            blk["kids"].append(n)
    return props, front, blocks


def classify(n):
    t = n.text
    if IMG.match(t):
        return "image"
    if t.startswith("Fill:"):
        return "fill"
    if t.startswith("⚠"):
        return "warn"
    if TIER.match(t):
        return "descent"
    if t.startswith("**Table as drafted**"):
        return "tcap"
    if CELL.match(t):
        return "cell"
    if n.depth == 0 and ILO.match(t):
        return "ilo"
    if t.startswith("Labelled:"):
        return "label"
    return "claim"


def prop_meta(props):
    parts = [props[k] for k in ("item", "lt-type") if props.get(k)]
    if props.get("confidence"):
        parts.append(f"confidence {props['confidence']}")
    parts += [f"{k} {v}" for k, v in props.items()
              if k not in ("item", "lt-type", "confidence")]
    return " · ".join(parts)


def item_parts(n):
    """Extract panel headings, metadata, body lines, and remaining child nodes."""
    title = (HEAD.match(n.text)[2] or "").strip()
    head = display_heading(title) or "Untitled panel"
    metas, rest = [], []
    if n.props:
        metas.append(prop_meta(n.props))
    for b in n.body:
        mm = ITALIC.match(b.strip())
        if mm and not rest and len(metas) < 2:
            metas.append(mm[1])
        else:
            rest.append(b)
    kids = []
    for k in n.kids:
        if k.text.startswith("Fill:") and not k.kids and not k.body:
            metas.append(plain(k.text))
        else:
            kids.append(k)
    return title, head, " · ".join(m for m in metas if m), rest, kids


def flavour_variant(path: str, name: str | None) -> str:
    """Prefer a palette sibling, checking its original extension before alternatives."""
    if not name:
        return path
    p = Path(path)
    for ext in dict.fromkeys([p.suffix, ".svg", ".png"]):
        v = p.with_name(f"{p.stem}-{name}{ext}")
        if v.is_file():
            return str(v)
    return path


def image_parts(t, page_dir, flavour=None):
    """Resolve image references and palette variants only inside approved asset roots."""
    m = IMG.match(t)
    note = m[3].strip()
    if note.startswith("(") and note.endswith(")"):
        note = note[1:-1]
    from urllib.parse import unquote
    reference = unquote(m[2])
    roots = [Path(page_dir) / "assets"] + [Path(d) for d in IMAGE_DIRS]
    roots = [root.resolve() for root in roots]

    def approved(candidate):
        path = Path(candidate).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("image path is outside approved asset directories")
        return str(path)

    direct = Path(page_dir) / reference
    cands = [direct] + [root / Path(reference).name for root in roots]
    path = None
    for candidate in cands:
        if candidate.is_file() or candidate.is_symlink():
            path = approved(candidate)
            break
    if path is None:
        raise FileNotFoundError(f"image {m[2]} not found; tried {cands}")
    return m[1], approved(flavour_variant(path, flavour)), note


def mk(t, theme):
    """Paragraph markup for a line of page text, with the glyph fallback applied."""
    return PW.fallback(_mk(t, theme))


def _mk(t, theme):
    """Decode captured entities and convert Logseq links into paragraph markup."""
    t = html.unescape(t)
    out, pos = [], 0
    for m in LINK.finditer(t):
        out.append(PW.to_pdf_markup(t[pos:m.start()], theme))
        if m[1] is not None:
            out.append(f"<i>{PW.to_pdf_markup(m[1], theme)}</i>")
        else:
            out.append(f'<a href="{html.escape(m[3], quote=True)}" color="{theme["blue"]}">'
                       f"{PW.to_pdf_markup(m[2], theme)}</a>")
        pos = m.end()
    out.append(PW.to_pdf_markup(t[pos:], theme))
    return "".join(out)


def mono_runs(t):
    """Use the mono face for ASCII runs and sans elsewhere."""
    return PW.fallback("".join(
        f'<font face="{PW.MONO if r.isascii() else PW.SANS}">{html.escape(r)}</font>'
        for r in re.findall(r"[\x00-\x7f]+|[^\x00-\x7f]+", html.unescape(t))))


def plain(t):
    t = LINK.sub(lambda m: m[1] or m[2], html.unescape(t))
    return PW.plain(t)


def xstyles(theme):
    st, t = PW.styles(theme), theme
    P = lambda name, parent, **kw: ParagraphStyle(name, parent=parent, **kw)
    st["claim"] = P("claim", st["item"], spaceBefore=3, leftIndent=26, bulletIndent=15)
    st["label"] = P("label", st["detail"], spaceBefore=3, leftIndent=26, bulletIndent=15)
    st["prov"] = P("prov", st["detail"], fontSize=7.5, leading=9.8, leftIndent=26,
                   bulletIndent=15, spaceBefore=0.5, spaceAfter=1.5,
                   textColor=HexColor(t["over"]))
    st["warnx"] = P("warnx", st["detail"], spaceBefore=4, spaceAfter=1, leftIndent=26,
                    bulletIndent=15)
    st["descent"] = P("descent", st["detail"], fontSize=9, leading=11.9)
    st["meta"] = P("meta", st["due"], leftIndent=4, spaceBefore=0, spaceAfter=2)
    st["caption"] = P("caption", st["prov"], alignment=TA_CENTER, leftIndent=0,
                      spaceBefore=2, spaceAfter=5)
    st["cell"] = P("cell", st["item"], fontSize=9, leading=11.9, leftIndent=0,
                   bulletIndent=0, spaceBefore=0, spaceAfter=0)
    st["front"] = P("front", st["warnx"], leftIndent=0, bulletIndent=0, spaceAfter=6)
    st["bodyp"] = P("bodyp", st["claim"], spaceBefore=2)
    st["th"] = P("th", st["cell"], fontName=PW.SANS_SB)
    return st


def lv(st, key, level):
    """Cache paragraph styles indented by STEP for each nesting level."""
    if level == 0:
        return st[key]
    name = f"{key}@{level}"
    if name not in st:
        b = st[key]
        st[name] = ParagraphStyle(name, parent=b, leftIndent=b.leftIndent + STEP * level,
                                  bulletIndent=b.bulletIndent + STEP * level)
    return st[name]


def warn_markup(text, theme):
    m = re.match(r"^⚠\s*\*\*(.+?)\*\*\s*(.*)$", text)
    if not m:
        # Restrict the warning accent to its sign for readable paragraph text.
        rest = re.sub(r"^⚠\s*", "", text)
        return PW.fallback(f'<font color="{theme["yellow"]}">⚠</font> {_mk(rest, theme)}')
    return PW.fallback(
        f'<font color="{theme["yellow"]}"><b>⚠ {html.escape(html.unescape(m[1]))}</b>'
        f"</font> {_mk(m[2], theme)}")


def labelled(parts, grp, oidx):
    """Attach continuation labels inside split units so labels follow their content."""
    if len(parts) < 2 or grp is None:
        return parts
    for i, p in enumerate(parts):
        p._grp, p._oidx, p._frag = grp, oidx, i == 0
    cp = _CONT(f"{grp[1]} — continued")
    cp._split_cont = grp
    return [parts[0], KeepTogether([cp] + parts[1:])]


class Para(Paragraph):
    """Move paragraphs whole unless their height exceeds the configured page fraction."""

    def split(self, aW, aH):
        if self.wrap(aW, AVAIL_H)[1] <= NOSPLIT * AVAIL_H:
            return []
        return labelled(Paragraph.split(self, aW, aH),
                        getattr(self, "_grp", None), getattr(self, "_oidx", -1))


class GTable(Table):
    """Split tables between rows, preserving repeated headers and continuation labels."""

    def split(self, aW, aH):
        return labelled(Table.split(self, aW, aH),
                        getattr(self, "_grp", None), getattr(self, "_oidx", -1))


class Captioned(Flowable):
    """Keep introductory flowables with their grid while allowing table row breaks."""

    def __init__(self, lead, tbl):
        Flowable.__init__(self)
        self.lead, self.tbl = list(lead), tbl

    def _lead_h(self, aW):
        h = 0
        for i, f in enumerate(self.lead):
            h += f.wrap(aW, AVAIL_H)[1] + (f.getSpaceBefore() if i else 0) + f.getSpaceAfter()
        return h + self.tbl.getSpaceBefore()

    def getSpaceBefore(self):
        return self.lead[0].getSpaceBefore() if self.lead else 0

    def getSpaceAfter(self):
        return self.tbl.getSpaceAfter()

    def wrap(self, aW, aH):
        lh = self._lead_h(aW)
        _tw, th = self.tbl.wrap(aW, max(aH - lh, 1))
        self.width, self.height = aW, lh + th
        return self.width, self.height

    def split(self, aW, aH):
        for a in ("_grp", "_oidx"):
            if hasattr(self, a):
                setattr(self.tbl, a, getattr(self, a))
        parts = self.tbl.split(aW, aH - self._lead_h(aW))
        if not parts:
            return []
        first = Captioned(self.lead, parts[0])
        for a in ("_grp", "_oidx", "_kind", "_txt"):
            if hasattr(self, a):
                setattr(first, a, getattr(self, a))
        if hasattr(parts[0], "_frag"):
            first._frag = parts[0]._frag
        return [first] + parts[1:]

    def draw(self):
        y = self.height
        for i, f in enumerate(self.lead):
            if i:
                y -= f.getSpaceBefore()
            y -= f.wrap(self.width, AVAIL_H)[1]
            f.drawOn(self.canv, 0, y)
            y -= f.getSpaceAfter()
        y -= self.tbl.getSpaceBefore()
        tw, th = self.tbl.wrap(self.width, AVAIL_H)
        self.tbl.drawOn(self.canv, self.width - tw, y - th)


def K(f, kind, txt=""):
    f._kind, f._txt = kind, txt
    return f


def opaque(path):
    """Composite transparent images onto white so every palette displays them consistently."""
    im = PILImage.open(path)
    if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
        return path
    out = os.path.join(IMAGE_CACHE, hashlib.sha1(path.encode()).hexdigest()[:16] + ".png")
    if not os.path.exists(out):
        rgba = im.convert("RGBA")
        flat = PILImage.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        flat.save(out)
    return out


def svg_size(path):
    """Read SVG dimensions from its viewBox or explicit size attributes."""
    head = re.search(r"<svg\b[^>]*>", Path(path).read_text(encoding="utf-8"))[0]
    vb = re.search(r'viewBox="([^"]+)"', head)
    if vb:
        return [float(v) for v in re.split(r"[\s,]+", vb[1].strip())]
    num = lambda k: float(re.search(rf'{k}="([\d.]+)', head)[1])
    return [0.0, 0.0, num("width"), num("height")]


def _svg_to_pdf(src, out):
    """One-page vector PDF of an SVG, in the fonts lt-suite resolves."""
    from ltcore import raster
    for warning in raster.convert(src, out, fmt="pdf", cfg=CFG)[1]:
        print(f"note: {warning}")


def _svg_to_png(svg, out_png, width_px):
    """PNG of an SVG at the given pixel width, in the fonts lt-suite resolves."""
    from ltcore import raster
    for warning in raster.convert(svg, out_png, fmt="png", width=width_px, cfg=CFG)[1]:
        print(f"note: {warning}")


def svg_pdf(svg, radius_pt, w_pt):
    """Cache a vector PDF after adding a rounded SVG clipping path."""
    text = Path(svg).read_text(encoding="utf-8")
    x0, y0, vw, vh = svg_size(svg)
    r = radius_pt * vw / w_pt
    clip = (f'<defs><clipPath id="module-pdf-round"><rect x="{x0}" y="{y0}" width="{vw}" '
            f'height="{vh}" rx="{r:.2f}"/></clipPath></defs><g clip-path="url(#module-pdf-round)">')
    head = re.search(r"<svg\b[^>]*>", text)
    tail = text.rindex("</svg>")
    text = text[:head.end()] + clip + text[head.end():tail] + "</g>" + text[tail:]
    key = hashlib.sha1(text.encode()).hexdigest()[:16]
    src, out = (os.path.join(IMAGE_CACHE, f"{key}{ext}") for ext in (".svg", ".pdf"))
    if not os.path.exists(out):
        Path(src).write_text(text, encoding="utf-8")
        _svg_to_pdf(src, out)
    return out


def have_vector_backend():
    """Return whether either supported PyMuPDF import name can be loaded."""
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        try:
            import fitz  # noqa: F401
        except ImportError:
            return False
    return True


def place_vectors(path, vectors):
    """Place recorded SVG figures into the spaces reserved during layout."""
    if not vectors:
        return
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(path)
    srcs = {}
    for v in vectors:
        pdf = svg_pdf(v["svg"], v["r"], v["w"])
        if pdf not in srcs:
            srcs[pdf] = fitz.open(pdf)
        page = doc[v["page"]]
        H = page.rect.height
        page.show_pdf_page(fitz.Rect(v["x"], H - v["y"] - v["h"], v["x"] + v["w"], H - v["y"]),
                           srcs[pdf], 0, keep_proportion=False)
    tmp = path + ".vec"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, path)


def image_flow(alt, path, note, st):
    global _SVG_NOTE
    is_svg = path.lower().endswith(".svg")
    vector = is_svg and have_vector_backend()
    if is_svg and not vector:
        if not _SVG_NOTE:
            print("note: PyMuPDF is not installed; SVG figures are embedded as PNG")
            _SVG_NOTE = True
        svg_width = svg_size(path)[2]
        width_px = int(svg_width * 2)
        key = hashlib.sha1(
            Path(path).read_bytes() + str(width_px).encode()).hexdigest()[:16]
        out_png = os.path.join(IMAGE_CACHE, key + ".png")
        if not os.path.exists(out_png):
            _svg_to_png(path, out_png, width_px)
        path = out_png
    if vector:
        w, h = svg_size(path)[2:]
    else:
        path = opaque(path)
        w, h = PILImage.open(path).size
    width = min(AVAIL_W, w * 72 / IMG_MIN_DPI)
    height = width * h / w
    if height > IMG_MAX_H:
        height, width = IMG_MAX_H, IMG_MAX_H * w / h
    cap = html.unescape(" · ".join(filter(None, [alt, note])))
    return K(FitImage(path, width, height, Paragraph(PW.fallback(html.escape(cap)), st["caption"]), vector),
             "image", cap)


def grid(rows, header, level, theme, st):
    """Lay out Markdown cells with themed rules, banding, and optional headers."""
    ncol = max(len(r) for r in rows + ([header] if header else []))
    pad = lambda r: list(r) + [""] * (ncol - len(r))
    rows = [pad(r) for r in rows]
    W = AVAIL_W - 26 - STEP * level
    avg = [sum(len(r[c]) for r in rows) / len(rows) for c in range(ncol)]
    if ncol == 2 and avg[0] < 0.4 * avg[1]:
        widths = [W * 0.28, W * 0.72]
    elif ncol <= 2:
        widths = [W / ncol] * ncol
    else:
        wt = [max(a, 8) ** 0.5 for a in avg]
        widths = [W * x / sum(wt) for x in wt]

    # Pin narrow columns to word widths and redistribute remaining space proportionally.
    def longest(c):
        words = [w for r in rows + ([header] if header else []) for w in plain(r[c]).split()]
        return max((stringWidth(w, PW.SANS_B, 9) for w in words), default=0) + 11
    mins = [min(longest(c), 0.3 * W) for c in range(ncol)]
    pinned: set[int] = set()
    while True:
        short = {c for c in range(ncol) if c not in pinned and widths[c] < mins[c] - 0.01}
        free = [c for c in range(ncol) if c not in pinned | short]
        if not short or not free:
            break
        pinned |= short
        room = W - sum(mins[c] for c in pinned)
        tot = sum(widths[c] for c in free)
        widths = [mins[c] if c in pinned else widths[c] * room / tot for c in range(ncol)]
    data = [[Paragraph(mk(t, theme), st["cell"]) for t in r] for r in rows]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor(theme["surface"])),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1),
         [HexColor(theme["base"]), HexColor(theme["mantle"])]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        data.insert(0, [Paragraph(mk(t, theme), st["th"]) for t in pad(header)])
        style[1] = ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                    [HexColor(theme["base"]), HexColor(theme["mantle"])])
        style.append(("BACKGROUND", (0, 0), (-1, 0), HexColor(theme["surface"])))
    tbl = GTable(data, colWidths=widths, hAlign="RIGHT", repeatRows=1 if header else 0)
    tbl.setStyle(TableStyle(style))
    return K(tbl, "table")


def cell_table(cap, cells, level, theme, st):
    """Convert exported cells into grids with named headers and optional captions."""
    rows = list(dict.fromkeys(r for r, _, _ in cells))
    cols = list(dict.fromkeys(c for _, c, _ in cells))
    numbered = all(re.fullmatch(r"column \d+", c) for c in cols)
    if numbered:
        cols.sort(key=lambda c: int(c.split()[1]))
    named_rows = not all(re.fullmatch(r"(?:row )?\d+\.?", r) for r in rows)
    at = {(r, c): t for r, c, t in cells}
    body = [([f"**{r}**"] if named_rows else []) + [at.get((r, c), "") for c in cols]
            for r in rows]
    header = None if numbered else ([""] if named_rows else []) + cols
    g = grid(body, header, level, theme, st)
    if not cap:
        return [g]
    c = K(Para(mk(cap, theme), lv(st, "label", level), bulletText="–"), "label", cap)
    return [K(Captioned([c], g), "table", cap)]


def body_flows(lines, level, theme, st):
    """Convert continuation lines into paragraphs and grids while retaining line breaks."""
    out, para, rows = [], [], []

    def flush_para():
        if para:
            out.append(K(Para("<br/>".join(mk(x, theme) for x in para),
                              lv(st, "bodyp", level)), "body", " ".join(para)))
            para.clear()

    def flush_rows():
        if rows:
            cells = [[c.strip() for c in r.strip().strip("|").split("|")]
                     for r in rows if not PIPE_SEP.match(r.strip())]
            has_header = len(rows) > 1 and PIPE_SEP.match(rows[1].strip())
            header = cells.pop(0) if has_header else None
            g = grid(cells, header, level, theme, st)
            if out and getattr(out[-1], "_kind", "") == "body":
                g = K(Captioned([out.pop()], g), "table")
            out.append(g)
            rows.clear()

    for x in lines:
        s = x.strip()
        if s.startswith("|"):
            flush_para()
            rows.append(s)
        else:
            flush_rows()
            if s:
                para.append(s)
            else:
                flush_para()
    flush_para()
    flush_rows()
    return out


def node_flows(n, k, level, theme, st, page_dir):
    t = n.text
    if k == "image":
        use = theme["use"]
        out = [image_flow(
            *image_parts(t, page_dir, None if use == "print" else theme["name"]), st)]
    elif k == "ilo":
        num, text = ILO.match(t).groups()
        out = [K(Para(mk(text, theme), lv(st, "claim", level), bulletText=f"{num}."),
                 "ilo", text)]
    elif k == "warn":
        out = [K(Para(warn_markup(t, theme), lv(st, "warnx", level)), "warn", t)]
    elif k == "descent":
        out = [K(Para(mk(t, theme), lv(st, "descent", level), bulletText="–"),
                 "descent", t)]
    else:
        k = "label" if k in ("label", "fill", "tcap", "cell") else "claim"
        claim, provs = peel(t)
        out = []
        if claim:
            out.append(K(Para(mk(claim, theme), lv(st, k, level), bulletText="–"),
                         k, claim))
        if provs:
            out.append(K(Para("  ·  ".join(mk(p, theme) for p in provs),
                              lv(st, "prov", level)), "prov"))
    return out + body_flows(n.body, level, theme, st)


class MDoc(PW.Doc):
    """Record flowable placement, split pages, continuation labels, and vector image positions."""

    def __init__(self, *a, **kw):
        PW.Doc.__init__(self, *a, **kw)
        self.kinds, self.splits, self.split_conts = [], [], set()
        self.vectors = []

    def afterFlowable(self, f):
        if isinstance(f, Captioned):
            for g in f.lead:
                g._grp, g._oidx = getattr(f, "_grp", None), getattr(f, "_oidx", -1)
                self.afterFlowable(g)
        sc = getattr(f, "_split_cont", None)
        if sc is not None:
            self.split_conts.add((sc, self.page))
            self.kinds.append(("cont", self.page, f._txt))
            return
        PW.Doc.afterFlowable(self, f)
        if getattr(f, "_frag", False):
            self.splits.append(("paragraph" if isinstance(f, Paragraph) else "table",
                                self.page))
        k = getattr(f, "_kind", None)
        if k and not hasattr(f, "_frag"):
            self.kinds.append((k, self.page, getattr(f, "_txt", "")))


def make_story(model, theme, st, conts, glue):
    """Walk the outline while keeping headings and metadata with their first content."""
    props, front, blocks, title, subtitle, page_dir = model
    global _CONT
    _CONT = lambda label: K(Paragraph(mk(label, theme), st["cont"]), "cont", label)
    story = [K(Paragraph(PW.fallback(html.escape(title)), st["title"]), "title", title),
             K(Paragraph(subtitle, st["subtitle"]), "subtitle")]
    for f in front:
        story.append(K(Para(warn_markup(f, theme), st["front"]), "front", f))
    ctx = {"grp": None, "n": 0, "gid": 0, "pend": []}

    def stamp(flows, grp, oidx):
        for f in flows:
            f._grp = grp
            f._oidx = oidx
            inner = getattr(f, "_content", None)
            if inner:
                stamp(inner, grp, oidx)

    def tag(flows):
        out = []
        for f in flows:
            f._grp = ctx["grp"]
            f._oidx = ctx["n"]
            ctx["n"] += 1
            inner = getattr(f, "_content", None)
            if inner:
                stamp(inner, f._grp, f._oidx)
            if conts.get(f._oidx):
                # Place continuation labels inside units so labels follow their content.
                cp = _CONT(conts[f._oidx])
                if isinstance(f, KeepTogether):
                    f._content.insert(0, cp)
                elif isinstance(f, Captioned):
                    f.lead.insert(0, cp)
                else:
                    f = KeepTogether([cp, f])
                    f._grp, f._oidx = cp._grp, cp._oidx = (ctx["grp"], ctx["n"] - 1)
            out.append(f)
        return out

    def content(flows):
        nhead = len(ctx["pend"])
        flow = ctx["pend"] + flows
        ctx["pend"] = []
        story.extend(tag(glue(flow, nhead)))

    def flush():
        if ctx["pend"]:
            story.extend(tag(ctx["pend"]))
            ctx["pend"] = []

    def group(label):
        ctx["gid"] += 1
        return (ctx["gid"], plain(label)[:90])

    def item(n):
        _title, head, meta, rest, kids = item_parts(n)
        prev, ctx["grp"] = ctx["grp"], group(head)
        ctx["pend"].append(K(Paragraph(mk(head, theme), st["week"]), "heading", head))
        if meta:
            ctx["pend"].append(K(Paragraph(mono_runs(meta), st["meta"]), "meta", meta))
        if rest:
            content(body_flows(rest, 0, theme, st))
        nodes(kids, 0)
        flush()
        ctx["grp"] = prev

    def nodes(ns, level):
        i = 0
        while i < len(ns):
            n = ns[i]
            if HEAD.match(n.text):
                item(n)
                i += 1
                continue
            k = classify(n)
            if k in ("tcap", "cell"):
                # Captions permit named rows while bare cell runs require numbers.
                pat = CELL_ANY if k == "tcap" else CELL
                j, cells = i + (k == "tcap"), []
                while j < len(ns) and pat.match(ns[j].text):
                    assert not ns[j].kids and not ns[j].body, \
                        f"cell with children: {ns[j].text!r}"
                    m = pat.match(ns[j].text)
                    cells.append((m[1], m[2], m[3]))
                    j += 1
                if cells:
                    assert not n.kids and not n.body, \
                        f"table caption with children: {n.text!r}"
                    content(cell_table(n.text if k == "tcap" else None, cells, level,
                                       theme, st))
                    i = j
                    continue
            if not n.text and not n.body:
                nodes(n.kids, level)
                i += 1
                continue
            flows = node_flows(n, k, level, theme, st, page_dir)
            if n.kids:
                # Queue parent content with its first child to prevent stranded questions.
                ctx["pend"].extend(flows)
            else:
                content(flows)
            nodes(n.kids, level + 1)
            i += 1

    for blk in blocks:
        ctx["grp"] = group(blk["title"])
        ctx["pend"].append(K(Paragraph(mk(blk["title"], theme), st["block"]), "heading",
                             blk["title"]))
        nodes(blk["kids"], 0)
        flush()
    return story


def build(path, model, theme, meta_kw):
    st = xstyles(theme)

    def height(flows):
        h = 0
        for f in flows:
            inner = getattr(f, "_content", None)
            if inner:
                h += height(inner)
                continue
            try:
                h += f.wrap(AVAIL_W, AVAIL_H)[1] + f.getSpaceBefore() + f.getSpaceAfter()
            except Exception:
                h += 12
        return h

    def glue(flows, nhead=0):
        if len(flows) <= 1:
            return flows
        if height(flows) <= CAP:
            return [KeepTogether(flows)]
        k = max(nhead + 2, 3)
        head, tail = flows[:k], flows[k:]
        if height(head) <= CAP:
            return [KeepTogether(head)] + tail
        # Keep headings with first content when the regular grouping cap fails.
        if nhead:
            first = flows[nhead]
            if isinstance(first, Captioned):
                first.lead[:0] = flows[:nhead]
                return flows[nhead:]
            if isinstance(first, GTable):
                return [K(Captioned(flows[:nhead], first), "table")] + flows[nhead + 1:]
            return [KeepTogether(flows[:nhead + 1])] + flows[nhead + 1:]
        return flows

    title = model[3]
    conts, converged = {}, False
    for _pass in range(PASSES):
        doc = MDoc(path, theme, title, **meta_kw)
        doc.build(make_story(model, theme, st, conts, glue))
        first_on_page, seen, page_first_group = {}, {}, {}
        for grp, oidx, page in doc.placed:
            if grp is None:
                continue
            seen.setdefault(grp, set()).add(page)
            first_on_page.setdefault((grp, page), oidx)
            page_first_group.setdefault(page, grp)
        want = {}
        for grp, pages in seen.items():
            for p in sorted(pages)[1:]:
                if page_first_group[p] != grp:
                    continue
                if (grp, p) in doc.split_conts:
                    continue
                want[first_on_page[(grp, p)]] = f"{grp[1]} — continued"
        if want == conts:
            converged = True
            break
        conts = want
    else:
        doc = MDoc(path, theme, title, **meta_kw)
        doc.build(make_story(model, theme, st, conts, glue))
    # A resumed outer group halfway down a page needs no additional page-top label.
    seen, page_first_group = {}, {}
    for grp, _oidx, page in doc.placed:
        if grp is not None:
            seen.setdefault(grp, set()).add(page)
            page_first_group.setdefault(page, grp)
    need = {(f"{g[1]} — continued", p) for g, ps in seen.items() for p in sorted(ps)[1:]
            if page_first_group[p] == g}
    have = {(t, p) for k, p, t in doc.kinds if k == "cont"}
    doc.label_audit = {"missing": sorted(need - have, key=lambda x: x[1]),
                       "stray": sorted(have - need, key=lambda x: x[1])}
    return doc, _pass + 1, converged


def safe_stem(name: str, limit: int = 120) -> str:
    """A file-name stem from page text: path separators, reserved characters and control
    characters become "-", leading dots and spaces go, and the length is capped. The
    title comes from the captured module, so it is never trusted as a path."""
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", name).strip(" .")
    return stem[:limit].rstrip(" .") or "module"


def render_page(page: Path, out: Path, cfg) -> dict:
    """Build configured palette PDFs and write the placement report for QC."""
    props, front, blocks = parse(page)
    ns = cfg.get("notes.page_namespace")
    raw_title = props["title"]
    if ns:
        raw_title = re.sub(rf"^{re.escape(ns)}/", "", raw_title)
    title = html.unescape(raw_title)
    stamp = cfg.get("notes.stamp_property")
    src = next((f for f in front if f.startswith("Source:")), None)
    front = [f for f in front if f is not src]
    sub_md = plain(props.get("type", "")) + (" · " + src if src else "")
    meta_kw = dict(
        subject=f"Lt module {props.get(stamp)} · run {props.get('lt-run','')} · captured {props.get('lt-captured','')} · exported {props.get('lt-exported','')} by {props.get('lt-exported-by','')}",
        keywords=plain(props.get("tags", "")),
        creator="lt-suite",
        author=cfg.get("pdf.author", ""))
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    palette_themes, notes = PW.pdf_themes(cfg)
    for note in notes:
        print(f"note: {note}")
    for palette_id, use, theme in palette_themes:
        theme = dict(theme, use=use)
        subtitle = mk(sub_md, theme)
        model = (props, front, blocks, title, subtitle, str(page.parent))
        path = out / f"{safe_stem(title)} ({safe_stem(palette_id)}, {use}).pdf"
        doc, passes, converged = build(str(path), model, theme, meta_kw)
        place_vectors(str(path), doc.vectors)
        report[palette_id] = {
            "path": str(path), "pages": doc.page, "passes": passes,
            "converged": converged, "labels": doc.label_audit,
            "splits": doc.splits, "kinds": doc.kinds, "glyph_subs": dict(PW.GLYPH_SUBS)}
    (out / ".build.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return report


def _pages_and_renders(data: bytes) -> list:
    """Render each PDF page into an RGB image for visual comparison."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        return [
            doc[i].render(scale=0.3).to_pil().convert("RGB")
            for i in range(len(doc))]
    finally:
        doc.close()


def _same_render(before: list, after: list) -> bool:
    """True when two page-image lists have equal page counts, sizes and pixels."""
    return len(before) == len(after) and all(
        x.size == y.size and ImageChops.difference(x, y).getbbox() is None
        for x, y in zip(before, after))


def finalise(report: dict, out: Path, cfg) -> bool:
    """Run the configured pdf-stage cleaners, strip metadata, then check every PDF.

    ``ltcore.cleaners.finalize`` runs each ``[[text_cleaners]]`` entry with stage "pdf" in
    config order, then the built-in metadata strip when ``pdf.strip_metadata`` is on.
    Failed commands, timeouts and unavailable tools prevent a successful final pass.
    Verification requires equal page counts, image dimensions and rendered RGB pixels.
    Writes ``<out>/.final.json``. On False the folder may mix cleaned and draft PDFs."""
    try:
        from ltcore import cleaners
    except ImportError as exc:
        print(f"FATAL: ltcore.cleaners is not available ({exc}); no PDF was cleaned",
              file=sys.stderr)
        return False
    strip = bool(cfg.get("pdf.strip_metadata", True))
    if not cleaners.cleaners_for("pdf", cfg=cfg) and not strip:
        print("final: no cleaners configured and pdf.strip_metadata is off; the PDFs are unchanged")
        return True
    paths = [Path(r["path"]) for r in report.values()]
    drafts = {p: _pages_and_renders(p.read_bytes()) for p in paths}
    results = cleaners.finalize(paths, stage="pdf", cfg=cfg, strip_metadata=strip)
    rows = [{"file": r.path, "step": r.name, "status": r.status, "detail": r.detail}
            for r in results]
    bad = [row for row in rows if row["status"] in ("failed", "timeout", "skipped")]
    for p in paths:
        after = _pages_and_renders(p.read_bytes())
        same = _same_render(drafts[p], after)
        print(f"final check {p.name}: {len(after)} page(s), "
              + ("renders as the draft did" if same
                 else f"DIFFERS from the draft ({len(drafts[p])} page(s) before)"))
        if not same:
            bad.append({"file": str(p), "step": "verify", "status": "verify",
                        "detail": "cleaning changed what the file shows"})
    (out / ".final.json").write_text(json.dumps(rows + [b for b in bad if b["step"] == "verify"],
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    if bad:
        print("FATAL: final pass: "
              + "; ".join(f"{Path(r['file']).name} {r['step']} {r['status']}" for r in bad)
              + f". {out} may mix cleaned and draft PDFs: rebuild before handing any over.",
              file=sys.stderr)
        return False
    cleaned = {r["file"] for r in rows if r["status"] == "cleaned"}
    print(f"final: {len(cleaned)} cleaned, {len(paths) - len(cleaned)} already clean; "
          f"the PDFs in {out} are final")
    return True


def _dependency_error(name):
    """Report missing packages consistently and return the dependency error code."""
    try:
        hint = ltcore.missing_hint([name])
    except (AttributeError, ImportError):
        hint = f"missing Python package: {name}"
    print(f"FATAL: {hint}", file=sys.stderr)
    return 3


def build_pdfs(page, out, cfg, *, assets=(), render=None, qc=True, final=True) -> int:
    """Build module PDFs, run requested checks, and optionally finalise verified outputs."""
    global IMAGE_CACHE, CFG, _SVG_NOTE
    CFG = cfg
    _SVG_NOTE = False
    if not qc and final:
        print(
            "FATAL: a PDF is finalised only after it passed QC; add --draft",
            file=sys.stderr)
        return 3
    missing = MISSING_DEPENDENCY or PW.MISSING_DEPENDENCY
    if missing:
        return _dependency_error(missing)

    page = expand(page).resolve()
    out = expand(out).resolve()
    if not page.is_file():
        print(f"FATAL: {page} does not exist", file=sys.stderr)
        return 3
    for key in ("curriculum.root", "notes.graph_path"):
        configured = cfg.get(key)
        if not configured:
            continue
        root = expand(configured).resolve()
        if out == root or root in out.parents:
            print(
                f"FATAL: --out {out} is inside {root}. PDFs go to the run folder, "
                "never into the notes graph or the course material.",
                file=sys.stderr)
            return 1

    try:
        faces, notes = PW.resolve_faces(cfg)
    except PW.FontUnavailable as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    except ModuleNotFoundError as e:
        return _dependency_error(e.name)
    for note in notes:
        print(f"note: {note}")
    try:
        PW.register_fonts(faces)
        text = page.read_text(encoding="utf-8")
        plan = PW.plan_glyphs(
            {c for c in html.unescape(text) if ord(c) > 127} | set("⚠—–·"), faces, cfg)
    except ModuleNotFoundError as e:
        return _dependency_error(e.name)
    except (OSError, UnicodeError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    if plan["missing"]:
        print(
            f"FATAL: no installed font has these characters: {plan['missing']}. "
            "They would print as empty boxes. Set fonts.sans to a font that has them.",
            file=sys.stderr)
        return 1
    for ch, face in sorted(plan["runs"].items()):
        print(f"note: {ch} is not in the sans font; drawn from the {face} font")
    for ch, text_ in sorted(plan["subs"].items()):
        print(f"note: no installed font has {ch}; printed as {text_!r}")

    IMAGE_DIRS[:] = [str(page.parent / "assets")] + [str(expand(d)) for d in assets]
    try:
        with tempfile.TemporaryDirectory(prefix="module_pdf-") as tmp:
            IMAGE_CACHE = tmp
            report = render_page(page, out, cfg)
    except ModuleNotFoundError as e:
        return _dependency_error(e.name)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    for palette_id, r in report.items():
        sp = [f"{k}@p{p}" for k, p in r["splits"]]
        print(f"{palette_id:10} {r['pages']:3} pages  {r['passes']} pass(es)"
              f"{'' if r['converged'] else ' NOT CONVERGED'}  breaks {sp or 'none'}  "
              f"{os.path.basename(r['path'])}")
    if not qc:
        print("QC skipped (--no-qc): these PDFs have not been checked.")
        return 0
    try:
        import module_pdf_qc
        ok = module_pdf_qc.check(
            page, out, report, render=expand(render) if render is not None else None,
            namespace=cfg.get("notes.page_namespace"),
            stamp_property=cfg.get("notes.stamp_property"))
        if not ok:
            return 1
        if final:
            return 0 if finalise(report, out, cfg) else 1
    except ModuleNotFoundError as e:
        return _dependency_error(e.name)
    except OSError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "page", type=Path,
        help="a Logseq page: an Lt___*.md in the graph, "
             "or an export's <out-dir>/module-page.md")
    ap.add_argument(
        "--out", type=Path, required=True,
        help="folder for the PDFs, normally runs/<module_id>/pdf")
    ap.add_argument(
        "--assets", type=Path, action="append", default=[],
        help="another folder to look an image up in by file name")
    ap.add_argument(
        "--render", type=Path,
        help="also write a PNG of every page here, for looking at")
    ap.add_argument("--no-qc", action="store_true", help="build only; do not check")
    ap.add_argument(
        "--draft", action="store_true",
        help="skip final cleaning; PDFs keep their metadata")
    ap.add_argument("--config", type=Path, help="configuration file")
    ap.add_argument("--data-dir", type=Path, help="accepted for CLI symmetry")
    args = ap.parse_args()
    if args.no_qc and not args.draft:
        ap.error("a PDF is finalised only after it passed QC; add --draft")

    try:
        PW.require_reportlab()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 3
    if MISSING_DEPENDENCY:
        return _dependency_error(MISSING_DEPENDENCY)
    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    return build_pdfs(
        args.page, args.out, cfg, assets=args.assets, render=args.render,
        qc=not args.no_qc, final=not args.draft)


if __name__ == "__main__":
    raise SystemExit(main())
