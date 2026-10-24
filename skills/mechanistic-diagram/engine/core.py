# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text measurement, box proportions, orthogonal routing, checks and SVG pieces: the core of the
mechanistic-diagram engine.

Geometry is decided once (layout_engine.py) and written twice: to an SVG (-> PDF/PNG with
rsvg-convert, render.py) and to a .drawio file (drawio.py), so the submission copy and the editable
draw.io master show the same picture.

Text is measured through PIL in the configured sans (fonts.sans). use_fonts() switches the face.
Text wraps into balanced lines and uses explicit
line breaks so draw.io never re-wraps.

text_w(italic=True) and baseline() handle the edge labels and the inset's labels (decor.py).

rich()/node_svg() colour the ↑/↓ marks of abnormal values; Box.foot keeps the text
above range bars; dash_attr() lets an edge kind carry its own dash pattern (the dotted "argues against");
cluster_svg() takes the title's inset (after a stage's number badge and glyph). See decor.py.

Arrowheads use triangles with rounded corners (arrow_head_d).
They stop ARROW_GAP short of their box.
T-bars stop TEE_GAP short of their box.
Every line and dash has round caps. round_dash shortens dash patterns by the
stroke width so a dash with round ends keeps its length and gap.
end_gap leaves about 5 px of clear space between a box's border and the head or bar.
Bold boxes add half the extra border width to the gap.

Italic terms (binomial names such as "X. fictus") are case content: the case's content.py lists them in
ITALIC and the engine registers them here with set_italic() when it loads the case.
"""
import html, math, pathlib, re
from PIL import ImageFont

FONT = None        # the family text is measured in; set by use_fonts() / use_configured_fonts()
FS = 14            # node font size (px): fonts.text_px
LH = 16.8          # line height = draw.io's 1.2 x font size
WGHT = (500, 700)  # weights of regular and bold text
OPSZ = None        # optical size a variable face is set to (FS when the face has the axis)
FILES = {}         # (weight, italic) -> the font file measured; render.py cuts static instances from them
_FR = _FB = None   # the measuring faces (loaded on first use)
BASE_OFF = (LH - 1.362 * FS) / 2 + 1.069 * FS    # baseline below the top of a line box; use_fonts() recomputes it
ENGINE_VERSION = "1.0"        # stamped into the .drawio as its agent (drawio.py)
ITALIC = ()                   # terms set in italic; the case registers its own (set_italic)
_FI = _FBI = None             # italic faces for those runs (None: measured in the upright face)


def _face(path, weight, opsz):
    """A PIL face at FS: a variable file is set to the weight (and optical size); a static file is used as it is."""
    from fontTools.ttLib import TTFont
    f = ImageFont.truetype(str(path), FS)
    t = TTFont(str(path), lazy=True)
    if "fvar" in t:
        axes = [a for a in t["fvar"].axes]
        vals = []
        for a in axes:
            if a.axisTag == "wght":
                vals.append(max(a.minValue, min(a.maxValue, weight)))
            elif a.axisTag == "opsz":
                vals.append(max(a.minValue, min(a.maxValue, opsz or FS)))
            else:
                vals.append(a.defaultValue)
        f.set_variation_by_axes(vals)
    return f


def set_size(px):
    """Box text size in px (fonts.text_px); the line height stays 1.2 x the size."""
    global FS, LH
    FS = int(px)
    LH = round(1.2 * FS, 2)


def use_fonts(regular, family, bold=None, wght=None, opsz=None, italic=None, bold_italic=None):
    """Measure and set text in a face. regular/bold/italic/bold_italic are font files (a variable file may
    serve every weight: it is set to the weights in `wght` and the optical size `opsz`). The baseline offset
    is recomputed from the face's typo metrics."""
    global _FR, _FB, _FI, _FBI, FONT, BASE_OFF, WGHT, OPSZ, FILES
    from fontTools.ttLib import TTFont
    WGHT = tuple(wght) if wght else WGHT
    bold = bold or regular
    if italic is None:
        cand = pathlib.Path(str(regular).replace("-Regular.", "-Italic."))
        italic = cand if str(cand) != str(regular) and cand.exists() else None
    if bold_italic is None:
        bold_italic = italic
    t = TTFont(str(regular), lazy=True)
    OPSZ = (opsz or FS) if "fvar" in t and any(a.axisTag == "opsz" for a in t["fvar"].axes) else None
    _FR, _FB = _face(regular, WGHT[0], OPSZ), _face(bold, WGHT[1], OPSZ)
    _FI = _face(italic, WGHT[0], OPSZ) if italic else None
    _FBI = _face(bold_italic, WGHT[1], OPSZ) if bold_italic else None
    FILES = {(WGHT[0], False): pathlib.Path(regular), (WGHT[1], False): pathlib.Path(bold)}
    if italic:
        FILES[(WGHT[0], True)] = pathlib.Path(italic)
    if bold_italic:
        FILES[(WGHT[1], True)] = pathlib.Path(bold_italic)
    FONT = family
    upm, os2 = t["head"].unitsPerEm, t["OS/2"]
    asc, desc = os2.sTypoAscender / upm * FS, -os2.sTypoDescender / upm * FS
    BASE_OFF = (LH - (asc + desc)) / 2 + asc


def use_configured_fonts(wght=None):
    """Measure in the configured sans (fonts.sans) at the configured size (fonts.text_px)."""
    import style
    set_size(style.text_px())
    w = tuple(wght) if wght else (500, 700)

    def find(weight, italic):
        try:
            return style.font_path("sans", weight, italic)
        except style.StyleError:
            if italic:
                return None
            raise
    use_fonts(find(w[0], False), style.font_family("sans"), bold=find(w[1], False), wght=w,
              italic=find(w[0], True), bold_italic=find(w[1], True))


def ensure_fonts():
    if _FR is None:
        use_configured_fonts()


def fonts_for_geometry(geo):
    """Measure in the face a geometry.json was laid out in; it must be the configured sans."""
    ensure_fonts()
    fam = geo.get("font")
    if fam and fam != FONT:
        raise ValueError(f"geometry.json was measured in {fam}, but the config sets text in {FONT} (fonts.sans): "
                         f"run layout_engine.py (or build.py) again")
    if geo.get("wght") and tuple(geo["wght"]) != tuple(WGHT):
        use_configured_fonts(geo["wght"])
    if geo.get("fs") and int(geo["fs"]) != FS:
        raise ValueError(f"geometry.json was laid out at {geo['fs']} px text, the config sets {FS} px "
                         f"(fonts.text_px): run layout_engine.py again")


PAD_X, PAD_Y = 12, 9
ASPECT = 2.4       # target width:height of a box of two or more lines
ASPECT_FLOOR = 1.9166  # minimum width:height ratio: the limit in floor.json (box_aspect_min)
                       # A box whose range bars add height gets wider (layout_engine.build).
                       # tests/diagram/test_floor.py keeps it at or above the floor.
ONE_LINE = 150     # text up to this wide stays on one line
W_MIN, W_MAX, W_STEP = 96, 220, 10
INK, MUTED = "#232836", "#5A6070"
CL_FILL, CL_STROKE, CL_TEXT = "#F6F7F9", "#D5D9E1", "#3A4050"


def set_italic(terms):
    """Register the case's italic terms (content.ITALIC), as bind() has already joined them (NBSP)."""
    global ITALIC
    ITALIC = tuple(terms)


def runs(s):
    """Split a line into (text, italic) runs at the ITALIC terms."""
    if not ITALIC:      # an empty alternation would split between every character
        return [(s, False)] if s else []
    out, pat = [], "(" + "|".join(re.escape(t) for t in ITALIC) + ")"
    for i, part in enumerate(re.split(pat, s)):
        if part:
            out.append((part, i % 2 == 1))
    return out


def text_w(s, bold=False, size=None, italic=False):
    """Width of s in px at `size` (default FS); italic=True measures the whole string in the italic face
    (edge labels), otherwise only the ITALIC terms are italic."""
    if _FR is None:
        ensure_fonts()
    w = 0.0
    for part, ital in runs(s):
        f = (_FBI if bold else _FI) if (ital or italic) and _FI is not None else (_FB if bold else _FR)
        w += f.getlength(part)
    return w * (size or FS) / FS


def baseline(cy, size=None):
    """The baseline that centres one line of text of `size` px on cy (the face's typo metrics, as BASE_OFF)."""
    if _FR is None:
        ensure_fonts()
    return cy + (BASE_OFF - LH / 2) * (size or FS) / FS


def rich(s, marks=None):
    """SVG text content: escaped, with the ITALIC terms in an italic tspan. marks maps a marker character
    (the ↑/↓ that decor.mark_text puts after an abnormal value) to its colour."""
    out = []
    for p, it in runs(s):
        t = esc(p)
        if marks:
            t = re.sub("|".join(map(re.escape, marks)), lambda m: f'<tspan fill="{marks[m.group(0)]}">{m.group(0)}</tspan>', t)
        out.append(f'<tspan font-style="italic">{t}</tspan>' if it else t)
    return "".join(out)


def wrap(text, maxw, bold=False):
    """Fewest lines that fit maxw, then the split that minimises the widest line."""
    words = [w for w in text.split(" ") if w]
    nw = len(words)
    width = lambda i, j: text_w(" ".join(words[i:j]), bold)
    # greedy line count
    lines, cur = 1, words[0]
    for w in words[1:]:
        if text_w(cur + " " + w, bold) <= maxw:
            cur += " " + w
        else:
            lines += 1
            cur = w
    best = None
    # DP over k lines minimising (max width, sum of squares)
    from functools import lru_cache

    @lru_cache(None)
    def dp(i, k):
        if k == 1:
            wv = width(i, nw)
            return (wv, wv * wv, (nw,)) if wv <= maxw or i == nw - 1 else (1e9, 1e18, (nw,))
        res = (1e9, 1e18, ())
        for j in range(i + 1, nw - k + 2):
            wv = width(i, j)
            if wv > maxw and j - i > 1:
                break
            sub = dp(j, k - 1)
            cand = (max(wv, sub[0]), wv * wv + sub[1], (j,) + sub[2])
            if (cand[0], cand[1]) < (res[0], res[1]):
                res = cand
        return res
    for k in range(lines, lines + 2):
        r = dp(0, k)
        if r[0] < 1e8:
            best = r
            break
    cuts = (0,) + best[2]
    return [" ".join(words[cuts[i]:cuts[i + 1]]) for i in range(len(cuts) - 1)]


class Box:
    __slots__ = ("id", "x", "y", "w", "h", "role", "lines", "bold", "cluster", "text", "foot")   # foot: px under the text (range bars)

    def __init__(self, **k):
        for a in self.__slots__:
            setattr(self, a, k.get(a))

    @property
    def x0(self): return self.x - self.w / 2
    @property
    def x1(self): return self.x + self.w / 2
    @property
    def y0(self): return self.y - self.h / 2
    @property
    def y1(self): return self.y + self.h / 2


def balanced(text, k, bold=False):
    """Split text into exactly k lines, minimising the widest line (then the sum of squares)."""
    words = [w for w in text.split(" ") if w]
    nw = len(words)
    if k > nw:
        return None
    from functools import lru_cache
    width = lambda i, j: text_w(" ".join(words[i:j]), bold)

    @lru_cache(None)
    def dp(i, kk):
        if kk == 1:
            wv = width(i, nw)
            return (wv, wv * wv, (nw,))
        best = None
        for j in range(i + 1, nw - kk + 2):
            wv = width(i, j)
            sub = dp(j, kk - 1)
            cand = (max(wv, sub[0]), wv * wv + sub[1], (j,) + sub[2])
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        return best
    mw, _, cuts = dp(0, k)
    cuts = (0,) + cuts
    return [" ".join(words[cuts[i]:cuts[i + 1]]) for i in range(k)], mw


def box_h(k):
    h = k * LH + 2 * PAD_Y
    return 2 * math.ceil(h / 2)


def size_node(nd, w=None, w_max=None):
    """Lines and box size. With w given (bars that span columns) the text wraps to w; otherwise the
    box takes the proportions of its text: one line when short, else the line count whose box is
    closest to ASPECT, widths on a W_STEP grid. A box squarer than ASPECT is penalised more than a
    wider one (a tall narrow card reads worse than a long one)."""
    bold, text = nd["bold"], nd["text"]
    if w is not None:
        lines = wrap(text, w - 2 * PAD_X - 4, bold)
        return lines, w, box_h(len(lines))
    one = text_w(text, bold)
    if one <= ONE_LINE:
        w = max(W_MIN, W_STEP * math.ceil((one + 2 * PAD_X + 4) / W_STEP))
        return [text], w, box_h(1)
    best = None
    for k in range(2, 7):
        r = balanced(text, k, bold)
        if r is None:
            break
        lines, mw = r
        w = max(W_MIN, W_STEP * math.ceil((mw + 2 * PAD_X + 4) / W_STEP))
        if w > (w_max or W_MAX):
            continue
        a = w / box_h(k)
        score = math.log(a / ASPECT) if a >= ASPECT else 1.8 * math.log(ASPECT / a)
        if best is None or score < best[0]:
            best = (score, lines, w, box_h(k))
    return best[1], best[2], best[3]


# ------------------------------------------------------------------------------------------ routing
def seg_hits_box(p, q, b, m=5):
    """Does axis-parallel segment p-q pass through box b (grown by m)?"""
    x0, x1 = min(p[0], q[0]), max(p[0], q[0])
    y0, y1 = min(p[1], q[1]), max(p[1], q[1])
    return not (x1 < b.x0 - m or x0 > b.x1 + m or y1 < b.y0 - m or y0 > b.y1 + m)


def port(b, side, t=0.5):
    if side == "T": return (b.x0 + t * b.w, b.y0)
    if side == "B": return (b.x0 + t * b.w, b.y1)
    if side == "L": return (b.x0, b.y0 + t * b.h)
    if side == "R": return (b.x1, b.y0 + t * b.h)


def side_of(b, pt):
    x, y = pt
    if abs(y - b.y0) < 0.6: return "T"
    if abs(y - b.y1) < 0.6: return "B"
    if abs(x - b.x0) < 0.6: return "L"
    return "R"


def simplify(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) < 0.01 and abs(p[1] - out[-1][1]) < 0.01:
            continue
        out.append(p)
    # drop collinear middle points
    res = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = res[-1], out[i], out[i + 1]
        if (abs(a[0] - b[0]) < 0.01 and abs(b[0] - c[0]) < 0.01) or (abs(a[1] - b[1]) < 0.01 and abs(b[1] - c[1]) < 0.01):
            continue
        res.append(b)
    res.append(out[-1])
    return res


def auto_route(S, D, spec, boxes):
    """Candidate orthogonal routes S->D; spec may force shape/ports. Returns list of point lists."""
    cands = []
    sx = spec.get("sx"); tx = spec.get("tx")  # fractional port positions
    shape = spec.get("shape")

    def ok(pts):
        for i in range(len(pts) - 1):
            p, q = pts[i], pts[i + 1]
            if abs(p[0] - q[0]) > 0.01 and abs(p[1] - q[1]) > 0.01:
                return False
            for b in boxes:
                if b is S or b is D:
                    continue
                if seg_hits_box(p, q, b):
                    return False
        # first/last segments must leave/enter outward
        return True

    # vertical straight
    ox0, ox1 = max(S.x0, D.x0), min(S.x1, D.x1)
    if shape in (None, "V") and ox1 - ox0 >= 24:
        x = spec.get("x")
        if x is None:
            x = S.x if D.x0 + 12 <= S.x <= D.x1 - 12 else (D.x if S.x0 + 12 <= D.x <= S.x1 - 12 else (ox0 + ox1) / 2)
        if D.y0 >= S.y1:
            cands.append([(x, S.y1), (x, D.y0)])
        elif S.y0 >= D.y1:
            cands.append([(x, S.y0), (x, D.y1)])
    oy0, oy1 = max(S.y0, D.y0), min(S.y1, D.y1)
    if shape in (None, "H") and oy1 - oy0 >= 12:
        y = spec.get("y")
        if y is None:
            y = S.y if D.y0 + 8 <= S.y <= D.y1 - 8 else (D.y if S.y0 + 8 <= D.y <= S.y1 - 8 else (oy0 + oy1) / 2)
        if D.x0 >= S.x1:
            cands.append([(S.x1, y), (D.x0, y)])
        elif S.x0 >= D.x1:
            cands.append([(S.x0, y), (D.x1, y)])
    # L shapes
    if shape in (None, "LV"):   # leave vertically, enter horizontally
        p0 = port(S, "B" if D.y > S.y else "T", sx if sx is not None else 0.5)
        ty = D.y0 + (tx if tx is not None else 0.5) * D.h
        side = "L" if D.x > p0[0] else "R"
        if (side == "L" and D.x0 > p0[0] + 8) or (side == "R" and D.x1 < p0[0] - 8):
            if (D.y > S.y and ty > S.y1 + 8) or (D.y < S.y and ty < S.y0 - 8):
                cands.append([p0, (p0[0], ty), port(D, side, tx if tx is not None else 0.5)])
    if shape in (None, "LH"):   # leave horizontally, enter vertically
        side = "R" if D.x > S.x else "L"
        p0 = port(S, side, sx if sx is not None else 0.5)
        tx_ = D.x0 + (tx if tx is not None else 0.5) * D.w
        tside = "T" if D.y > p0[1] else "B"
        if (tside == "T" and D.y0 > p0[1] + 8) or (tside == "B" and D.y1 < p0[1] - 8):
            if (side == "R" and tx_ > S.x1 + 8) or (side == "L" and tx_ < S.x0 - 8):
                cands.append([p0, (tx_, p0[1]), port(D, tside, tx if tx is not None else 0.5)])
    # Z shapes
    if shape in (None, "ZV") and (D.y0 > S.y1 + 10 or S.y0 > D.y1 + 10):
        down = D.y0 > S.y1
        p0 = port(S, "B" if down else "T", sx if sx is not None else 0.5)
        p3 = port(D, "T" if down else "B", tx if tx is not None else 0.5)
        chans = [spec["ch"]] if "ch" in spec else [(p0[1] + p3[1]) / 2, p0[1] + (16 if down else -16), p3[1] - (16 if down else -16)]
        for ym in chans:
            cands.append([p0, (p0[0], ym), (p3[0], ym), p3])
    if shape in (None, "ZH") and (D.x0 > S.x1 + 10 or S.x0 > D.x1 + 10):
        right = D.x0 > S.x1
        p0 = port(S, "R" if right else "L", sx if sx is not None else 0.5)
        p3 = port(D, "L" if right else "R", tx if tx is not None else 0.5)
        chans = [spec["ch"]] if "ch" in spec else [(p0[0] + p3[0]) / 2, p0[0] + (14 if right else -14), p3[0] - (14 if right else -14)]
        for xm in chans:
            cands.append([p0, (xm, p0[1]), (xm, p3[1]), p3])
    if shape == "PTS":
        cands.append(spec["pts"](S, D))
    good = [simplify(c) for c in cands if ok(simplify(c))]
    return good, [simplify(c) for c in cands]


def route_len(pts):
    return sum(abs(pts[i][0] - pts[i + 1][0]) + abs(pts[i][1] - pts[i + 1][1]) for i in range(len(pts) - 1))


# ------------------------------------------------------------------------------------------ checks
def segs(pts):
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def _inter(s, t):
    (a, b), (c, d) = s, t
    ah = abs(a[1] - b[1]) < 0.01
    ch = abs(c[1] - d[1]) < 0.01
    if ah == ch:
        return None
    h, v = (s, t) if ah else (t, s)
    (hx0, hy), (hx1, _) = sorted([h[0], h[1]])
    (vx, vy0), (_, vy1) = sorted([v[0], v[1]], key=lambda p: p[1])
    if min(hx0, hx1) + 0.5 < vx < max(hx0, hx1) - 0.5 and vy0 + 0.5 < hy < vy1 - 0.5:
        return (vx, hy)
    return None


def _overlap(s, t, tol=3.0):
    (a, b), (c, d) = s, t
    if abs(a[1] - b[1]) < 0.01 and abs(c[1] - d[1]) < 0.01 and abs(a[1] - c[1]) < tol:
        lo, hi = max(min(a[0], b[0]), min(c[0], d[0])), min(max(a[0], b[0]), max(c[0], d[0]))
        return hi - lo if hi - lo > 0.5 else 0
    if abs(a[0] - b[0]) < 0.01 and abs(c[0] - d[0]) < 0.01 and abs(a[0] - c[0]) < tol:
        lo, hi = max(min(a[1], b[1]), min(c[1], d[1])), min(max(a[1], b[1]), max(c[1], d[1]))
        return hi - lo if hi - lo > 0.5 else 0
    return 0


def _pt_on_seg(p, s, tol=3.0):
    (a, b) = s
    if abs(a[1] - b[1]) < 0.01:
        return abs(p[1] - a[1]) < tol and min(a[0], b[0]) + tol < p[0] < max(a[0], b[0]) - tol
    return abs(p[0] - a[0]) < tol and min(a[1], b[1]) + tol < p[1] < max(a[1], b[1]) - tol


def check(boxes, edges, clusters, labels=()):
    probs, warns = [], []
    bl = list(boxes.values())
    for i in range(len(bl)):
        for j in range(i + 1, len(bl)):
            a, b = bl[i], bl[j]
            if not (a.x1 + 10 <= b.x0 or b.x1 + 10 <= a.x0 or a.y1 + 10 <= b.y0 or b.y1 + 10 <= a.y0):
                probs.append(f"node overlap/too close: {a.id} {b.id}")
    for cid, (x0, y0, x1, y1) in clusters.items():
        for b in bl:
            if b.cluster == cid and not (x0 + 6 <= b.x0 and b.x1 <= x1 - 6 and y0 + 22 <= b.y0 and b.y1 <= y1 - 6):
                probs.append(f"{b.id} sticks out of cluster {cid}")
            if b.cluster != cid and not (b.x1 < x0 or b.x0 > x1 or b.y1 < y0 or b.y0 > y1):
                probs.append(f"{b.id} intrudes into cluster {cid}")
    cl = list(clusters.items())
    for i in range(len(cl)):
        for j in range(i + 1, len(cl)):
            (ia, a), (ib, b) = cl[i], cl[j]
            if not (a[2] + 8 <= b[0] or b[2] + 8 <= a[0] or a[3] + 8 <= b[1] or b[3] + 8 <= a[1]):
                probs.append(f"clusters overlap/too close: {ia} {ib}")
    for ed in edges:
        pts = ed["pts"]
        for p, q in segs(pts):
            if abs(p[0] - q[0]) > 0.01 and abs(p[1] - q[1]) > 0.01:
                probs.append(f"diagonal segment in {ed['a']}->{ed['b']}")
            for b in bl:
                if b.id in (ed["a"], ed["b"]):
                    continue
                if seg_hits_box(p, q, b, 4):
                    probs.append(f"edge {ed['a']}->{ed['b']} runs through {b.id}")
        # cluster title bands
        for cid, (x0, y0, x1, y1) in clusters.items():
            for p, q in segs(pts):
                if seg_hits_box(p, q, Box(id="t", x=(x0 + x1) / 2, y=y0 + 12, w=x1 - x0 - 4, h=20), 0) and \
                        abs(p[1] - q[1]) < 0.01:
                    warns.append(f"edge {ed['a']}->{ed['b']} runs along the title band of {cid}")
    crossings = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            A, B = edges[i], edges[j]
            related = A["a"] == B["a"] or A["b"] == B["b"]
            for s in segs(A["pts"]):
                for t in segs(B["pts"]):
                    ov = _overlap(s, t)
                    if ov:
                        (warns if related else probs).append(
                            f"{'shared trunk' if related else 'OVERLAP'} {A['a']}->{A['b']} / {B['a']}->{B['b']} ({ov:.0f}px)")
                    x = _inter(s, t)
                    if x:
                        crossings.append((A["a"] + "->" + A["b"], B["a"] + "->" + B["b"], x))
            if not related:
                for p in A["pts"]:
                    for t in segs(B["pts"]):
                        if _pt_on_seg(p, t):
                            probs.append(f"false continuation: point of {A['a']}->{A['b']} on {B['a']}->{B['b']}")
                for p in B["pts"]:
                    for s in segs(A["pts"]):
                        if _pt_on_seg(p, s):
                            probs.append(f"false continuation: point of {B['a']}->{B['b']} on {A['a']}->{A['b']}")
    return probs, warns, crossings


# ------------------------------------------------------------------------------------------ SVG
def esc(s):
    return html.escape(s, quote=True)


def rounded_path(pts, r=6.0):
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        l1 = math.hypot(p1[0] - p0[0], p1[1] - p0[1]); l2 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        rr = min(r, l1 / 2, l2 / 2)
        a = (p1[0] + (p0[0] - p1[0]) * rr / l1, p1[1] + (p0[1] - p1[1]) * rr / l1)
        b = (p1[0] + (p2[0] - p1[0]) * rr / l2, p1[1] + (p2[1] - p1[1]) * rr / l2)
        d.append(f"L{a[0]:.1f},{a[1]:.1f} Q{p1[0]:.1f},{p1[1]:.1f} {b[0]:.1f},{b[1]:.1f}")
    d.append(f"L{pts[-1][0]:.1f},{pts[-1][1]:.1f}")
    return " ".join(d)


ARROW_L, ARROW_HW = 9.0, 4.3
ARROW_GAP = 6.0              # the arrowhead's rounded tip stops this far short of the box it points at
ARROW_ROUND = (3.2, 2.0)     # how far each corner's rounding reaches along the sides: tip, base corners
TEE_GAP, TEE_LEN, TEE_W = 7.0, 13.0, 2.5
EDGE_W = 1.5
NODE_SW, NODE_SW_BOLD = 1.5, 2.2   # box border widths (node_svg); end_gap() keeps the same clear space on both


def end_gap(head, bold=False):
    """How far a connector's head stops short of its end point (the middle of the target's border): about 5 px
    of clear space between the border and the nearest painted point of the arrowhead or T-bar, whether the
    border is plain or bold."""
    return (ARROW_GAP if head == "arrow" else TEE_GAP) + ((NODE_SW_BOLD - NODE_SW) / 2 if bold else 0)
DEP_DASH = (5.0, 3.5)        # the "depends on" dash and the normal-anatomy box border, as they read on the
ANAT_DASH = (6.0, 3.5)       #   page (dash, gap); round_dash() turns them into a dasharray for round caps


def round_dash(dash, width):
    """A dasharray whose dashes, with their round caps, read as dash[0] long with dash[1] gaps."""
    on, off = dash
    return f"{max(0.01, on - width):g} {off + width:g}"


def anat_dash_attr(width=1.5):
    """The normal-anatomy box border: dashed, every dash a pill."""
    return f' stroke-dasharray="{round_dash(ANAT_DASH, width)}" stroke-linecap="round"'


def dash_attr(dashed, width=EDGE_W):
    """dashed: False, True (the "depends on" dash) or a dash pattern string (dotted). Every dash has round caps."""
    if not dashed:
        return ""
    if dashed is True:
        return f' stroke-dasharray="{round_dash(DEP_DASH, width)}"'
    return f' stroke-dasharray="{dashed}"'


def arrow_head_d(tip, ux, uy, length=ARROW_L, half=ARROW_HW, round_=ARROW_ROUND):
    """An arrowhead with rounded corners pointing along (ux, uy), its rounded tip exactly at `tip`. Each corner
    is a quadratic curve from round_ px before the corner to round_ px after it, so the shape keeps its size."""
    t_tip, t_base = round_
    # a rounded corner's apex sits t * cos(half-angle) / 2 behind the sharp corner: move the sharp tip forward
    cos_h = length / math.hypot(length, half)
    vx, vy = tip[0] + ux * t_tip * cos_h / 2, tip[1] + uy * t_tip * cos_h / 2
    bx, by = vx - ux * length, vy - uy * length
    corners = [((vx, vy), t_tip), ((bx - uy * half, by + ux * half), t_base), ((bx + uy * half, by - ux * half), t_base)]
    segs = []
    for i, (v, t) in enumerate(corners):
        prev, nxt = corners[i - 1][0], corners[(i + 1) % 3][0]
        def toward(q, t=t, v=v):
            d = math.hypot(q[0] - v[0], q[1] - v[1])
            return (v[0] + (q[0] - v[0]) * t / d, v[1] + (q[1] - v[1]) * t / d)
        segs.append((toward(prev), v, toward(nxt)))
    d = [f"M{segs[0][2][0]:.2f},{segs[0][2][1]:.2f}"]
    for a, v, b in segs[1:] + segs[:1]:
        d.append(f"L{a[0]:.2f},{a[1]:.2f} Q{v[0]:.2f},{v[1]:.2f} {b[0]:.2f},{b[1]:.2f}")
    return " ".join(d) + " Z"


def edge_svg(pts, colour, dashed, head, width=None, gap=None):
    """A connector: round-capped line with rounded bends, ending in a rounded arrowhead ARROW_GAP short of
    its end point (gap overrides: 0 for a free-standing sample in the key) or in a T-bar TEE_GAP short."""
    width = width or EDGE_W
    pts = [tuple(p) for p in pts]
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    L = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    out = []
    if head == "arrow":
        g = ARROW_GAP if gap is None else gap
        tip = (x2 - ux * g, y2 - uy * g)
        line = pts[:-1] + [(tip[0] - ux * (ARROW_L - 1.5), tip[1] - uy * (ARROW_L - 1.5))]
        out.append(f'<path d="{rounded_path(line)}" fill="none" stroke="{colour}" stroke-width="{width}"{dash_attr(dashed, width)} '
                   f'stroke-linecap="round" stroke-linejoin="round"/>')
        out.append(f'<path d="{arrow_head_d(tip, ux, uy)}" fill="{colour}"/>')
    else:
        g = TEE_GAP if gap is None else gap
        end = (x2 - ux * g, y2 - uy * g)
        line = pts[:-1] + [end]
        out.append(f'<path d="{rounded_path(line)}" fill="none" stroke="{colour}" stroke-width="{width + 0.15}"{dash_attr(dashed, width + 0.15)} '
                   f'stroke-linecap="round" stroke-linejoin="round"/>')
        pa = (end[0] - uy * TEE_LEN / 2, end[1] + ux * TEE_LEN / 2)
        pb = (end[0] + uy * TEE_LEN / 2, end[1] - ux * TEE_LEN / 2)
        out.append(f'<line x1="{pa[0]:.1f}" y1="{pa[1]:.1f}" x2="{pb[0]:.1f}" y2="{pb[1]:.1f}" stroke="{colour}" stroke-width="{TEE_W}" stroke-linecap="round"/>')
    return out


def node_svg(b, role_style, marks=None):
    """A box and its text lines; the text centres in the box above b.foot (range bars), and marks colours
    the abnormal-value markers (see rich)."""
    fill, border, text = role_style
    sw = NODE_SW_BOLD if b.bold else NODE_SW
    dash = anat_dash_attr(sw) if b.role == "anat" else ""
    out = [f'<rect x="{b.x0:.1f}" y="{b.y0:.1f}" width="{b.w:.1f}" height="{b.h:.1f}" rx="7" ry="7" fill="{fill}" stroke="{border}" stroke-width="{sw}"{dash}/>']
    top = b.y - (b.foot or 0) / 2 - len(b.lines) * LH / 2
    wt = f' font-weight="{WGHT[1] if b.bold else WGHT[0]}"'
    for i, ln in enumerate(b.lines):
        out.append(f'<text x="{b.x:.1f}" y="{top + i * LH + BASE_OFF:.1f}" text-anchor="middle" font-family="{FONT}" font-size="{FS}" fill="{text}"{wt}>{rich(ln, marks)}</text>')
    return out


CL_FS = 14         # stage header size
CL_TOP = 40        # header band: first box sits this far below the cluster's top edge


def cluster_svg(cid, rect, title, fill=None, stroke=None, text=None, font=None, title_x=14):
    """A stage container and its title (title_x: the title's inset, wider when a number badge and glyph lead it)."""
    x0, y0, x1, y1 = rect
    return [f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="12" ry="12" fill="{fill or CL_FILL}" stroke="{stroke or CL_STROKE}" stroke-width="1.2"/>',
            f'<text x="{x0 + title_x:.1f}" y="{y0 + 24:.1f}" font-family="{font or FONT}" font-size="{CL_FS}" font-weight="700" fill="{text or CL_TEXT}">{esc(title)}</text>']


def label_svg(lb):
    x, y, text = lb["x"], lb["y"], lb["text"]
    w = text_w(text, False, 10) + 8
    return [f'<rect x="{x - w / 2:.1f}" y="{y - 8:.1f}" width="{w:.1f}" height="15" rx="3" fill="#FFFFFF"/>',
            f'<text x="{x:.1f}" y="{y + 3.5:.1f}" text-anchor="middle" font-family="{FONT}" font-size="10" font-style="italic" fill="{MUTED}">{esc(text)}</text>']


# ------------------------------------------------------------------------------------------ draw.io
def _num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def drawio_label(b):
    body = "<br>".join(html.escape(l, quote=False) for l in b.lines)
    return body
