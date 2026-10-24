# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Glyph layers of the mechanistic-diagram engine: numbered stages with a glyph, the
time-course ribbon, a role glyph on every box, flag badges (pitfall, urgent, high-yield), abnormal-result
markers and range bars, and the key that explains them. layout_engine.py decides where everything sits
(geometry.json); render.py and drawio.py draw it from there, so the PDF and the master match.

Case data (all optional; with none, a case draws as before apart from the role glyphs and stage numbers):

  case.json  "stages": [{"id": "cause", "n": 1, "icon": "dice_multiple", "when": "over years"}, ...]
                 n: the stage's number badge in story order (null or left out: no badge; number only the stages
                 on the ribbon, so the numbers read in order); icon: an icons.py name, drawn in the stage-title
                 ink; when: its point on the time-course ribbon (stages without one stay off it). Without
                 "stages": numbers in CLUSTERS order, no ribbon.
                 accent: an accent name from the configured palettes (style.palettes), or "#rrggbb", honoured only with
                 "glyphs": {"stage_accents": true}. Stage glyphs use title ink by default.
                 Accent glyphs can compete with role colours and repeat their hues.
             "timeline": {"label": "TIME COURSE"}           the ribbon's heading
             "role_icons": {"risk": "dice_multiple", ...}   other role glyphs
             "glyphs": {"roles": false}                     no role glyphs
  content.py n(..., flags=("pitfall", "urgent", "high_yield"))
             n(..., lab=[dict(value=48, lo=2, hi=10, at="48")])
                 at: the text the marker follows; lo/hi: the reference range (a range bar is drawn when both are
                 given); flag="high"/"low" marks a value whose range the case does not state (no bar).

Every glyph is a Material Design Icons path (icons.py, from tools/extract_icons.py).
"""
import html, math, re

import core as E
import themes
from icons import ICONS

# ------------------------------------------------------------------ sizes (px)
STAGE_BADGE_R = 9.0          # stage number badge in a container's header band
STAGE_ICON = 16.0            # stage glyph beside it
TITLE_X = 14.0               # header text inset without a badge (core.cluster_svg)
BADGE_FS = 11                # number size in a badge (the configured mono (fonts.mono), bold)
ROLE_R = 8.5                 # role glyph badge on a box corner
ROLE_ICON_PX = 12.0
FLAG_R = 8.0                 # flag badge
FLAG_ICON_PX = 10.5
BADGE_CLEAR = 2.0            # a badge keeps this far from any arrow, other box or other badge
BAR_H = 12                   # height a range bar adds to its box
BAR_W = 84                   # track length
TL_BAND = 44                 # header height the time-course ribbon adds above the stage grid
TL_BADGE_R = 10.0
TL_BADGE_FS = 12             # number size on the ribbon
TL_GAP = 8                   # the ribbon's line stops this far from each marker (after its time, before its badge)
HEADER_CLEAR = 8.0           # a box's badge keeps this far from a stage's number and glyph (layout_engine grows the
                             # header band of a row whose first boxes would bring a badge closer)
HEADER_BOTTOM = 106          # below the subtitle's descenders (render.header: subtitle baseline at y 100)


# ------------------------------------------------------------------ icons
def icon_svg(name, cx, cy, size, colour, extra=""):
    """An icons.py glyph centred on (cx, cy), size px square."""
    s = size / 24.0
    return (f'<path transform="translate({cx - size / 2:.2f},{cy - size / 2:.2f}) scale({s:.4f})" '
            f'd="{ICONS[name]}" fill="{colour}"{extra}/>')


def icon_doc(name, colour, size=24, disk=None, ring=None, ring_w=0.0, icon_px=None):
    """A standalone SVG document of one glyph (for draw.io image cells), optionally on a disk with a ring."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">']
    if disk:
        r = size / 2 - ring_w / 2
        stroke = f' stroke="{ring}" stroke-width="{ring_w}"' if ring else ""
        out.append(f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.2f}" fill="{disk}"{stroke}/>')
    out.append(icon_svg(name, size / 2, size / 2, icon_px or size, colour))
    out.append("</svg>")
    return "".join(out)


def check_icons(names):
    missing = sorted({n for n in names if n and n not in ICONS})
    if missing:
        raise ValueError(f"no icon {', '.join(missing)} in icons.py: add it to tools/extract_icons.py NAMES and run it")


# ------------------------------------------------------------------ stages and the ribbon
def stage_meta(case):
    """Stage id -> dict(n, icon, accent, when), in CLUSTERS order (see the module doc)."""
    clusters = list(case.content.CLUSTERS)
    meta = case.meta
    if "stages" not in meta:
        return {cid: dict(n=i + 1, icon=None, accent=None, when=None) for i, cid in enumerate(clusters)}
    given = {}
    for s in meta["stages"]:
        if s.get("id") not in clusters:
            raise ValueError(f"case.json stages: {s.get('id')!r} is not a stage in content.py CLUSTERS")
        given[s["id"]] = s
    accents = bool(meta.get("glyphs", {}).get("stage_accents", False))
    out = {cid: dict(n=given.get(cid, {}).get("n"), icon=given.get(cid, {}).get("icon"),
                     accent=given.get(cid, {}).get("accent") if accents else None,
                     when=given.get(cid, {}).get("when")) for cid in clusters}
    check_icons(s["icon"] for s in out.values())
    nums = [s["n"] for s in out.values() if s["n"] is not None]
    if len(nums) != len(set(nums)):
        raise ValueError("case.json stages: two stages share a number")
    for cid, s in out.items():
        if s["n"] is not None and not (isinstance(s["n"], int) and s["n"] >= 0):
            raise ValueError(f"case.json stages: {cid} n {s['n']!r} is not a whole number (or null for no badge)")
        if s["accent"] and not (s["accent"].startswith("#") or s["accent"] in themes.ACCENT_NAMES):
            raise ValueError(f"case.json stages: {cid} accent {s['accent']!r} is not one of "
                             f"{', '.join(themes.ACCENT_NAMES)} or #rrggbb")
    return out


def timeline_stages(meta):
    """The stages on the ribbon, in number order: those with both a number and a time."""
    on = [(s["n"], cid) for cid, s in meta.items() if s["when"] and s["n"] is not None]
    return [cid for _, cid in sorted(on)] if len(on) >= 2 else []


def tl_label(case):
    return case.meta.get("timeline", {}).get("label", "TIME COURSE")


def mono_w(text, size):
    """Width of the configured mono (fonts.mono), assuming 0.6 em per character."""
    return 0.6 * size * len(text)


def timeline_geom(case, meta, rects):
    """The ribbon above the stage grid: its line, heading and one marker per stage, evenly spaced."""
    order = timeline_stages(meta)
    if not order:
        return None
    x0 = min(r[0] for r in rects.values())
    x1 = max(r[2] for r in rects.values())
    top = min(r[1] for r in rects.values())
    y = (HEADER_BOTTOM + top) / 2
    label = tl_label(case)
    widths = [marker_w(meta[c]) for c in order]
    bws = [badge_w(meta[c]["n"], TL_BADGE_R, TL_BADGE_FS) for c in order]
    a, b = x0 + mono_w(label, E.CL_FS) + 18 + bws[0] / 2, x1 - 30 - (widths[-1] - bws[-1] / 2)
    xs = [a + i * (b - a) / (len(order) - 1) for i in range(len(order))]
    line0 = xs[0]           # the line starts at the first stage and ends in an arrowhead at the right
    marks = [dict(stage=c, n=meta[c]["n"], icon=meta[c]["icon"], accent=meta[c]["accent"], when=meta[c]["when"], x=x,
                  w=w, bw=bw) for c, x, w, bw in zip(order, xs, widths, bws)]
    return dict(y=y, x0=x0, x1=x1, line0=line0, label=label, markers=marks)


def marker_w(s):
    """A ribbon marker's width from its badge's left edge: badge, glyph, time."""
    w = badge_w(s["n"], TL_BADGE_R, TL_BADGE_FS) + 6
    if s["icon"]:
        w += STAGE_ICON + 5
    return w + E.text_w(s["when"])


# ------------------------------------------------------------------ lab values
def labs(nd):
    """A node's lab entries with their direction ("high", "low" or None) worked out."""
    out = []
    for lab in (nd.get("attrs") or {}).get("lab", ()):
        lab = dict(lab)
        v, lo, hi = lab.get("value"), lab.get("lo"), lab.get("hi")
        d = lab.get("flag")
        if d is None and v is not None:
            if hi is not None and v > hi:
                d = "high"
            elif lo is not None and v < lo:
                d = "low"
        if d not in (None, "high", "low"):
            raise ValueError(f"lab flag {d!r}: use 'high' or 'low'")
        lab["dir"] = d
        out.append(lab)
    return out


def bars_of(nd):
    return [lab for lab in labs(nd) if lab.get("lo") is not None and lab.get("hi") is not None and lab.get("value") is not None]


def mark_text(nid, text, entries):
    """The node text with ↑ or ↓ after each abnormal value (after its "at" text; spaces in "at" match NBSP)."""
    for lab in entries:
        if not lab["dir"]:
            continue
        at = lab.get("at") or str(lab.get("value"))
        pat = re.escape(at).replace(r"\ ", "[ " + E_NBSP + "]")
        m = re.search(pat, text)
        if m is None:
            raise ValueError(f"node {nid}: lab 'at' {at!r} is not in its text")
        text = text[:m.end()] + themes.LAB_MARK[lab["dir"]] + text[m.end():]
    return text


E_NBSP = " "


def bar_domain(v, lo, hi):
    """The track's (start, end) and whether the value is off the scale ("high"/"low"/None).
    The normal band keeps half its width of margin each side (none below a range that starts at 0);
    a value within 2.5 range-widths of the band is shown on the scale, a value further out clamps to
    the track's end with an overflow cue."""
    span = max(hi - lo, 1e-9)
    d0 = lo - 0.5 * span
    if lo >= 0 > d0:
        d0 = 0.0
    d1 = hi + 0.5 * span
    clamp = None
    if v > d1:
        if v <= hi + 2.5 * span:
            d1 = v + 0.25 * span
        else:
            d1, clamp = hi + 1.5 * span, "high"
    if v < d0:
        if v >= lo - 2.5 * span:
            d0 = v - 0.25 * span
        else:
            d0, clamp = lo - 1.5 * span, "low"
    return d0, d1, clamp


def bar_geom(b, entries):
    """Absolute geometry of a box's range bars (track, normal band, value), bottom of the box."""
    out = []
    nb = len(entries)
    for i, lab in enumerate(entries):
        v, lo, hi = lab["value"], lab["lo"], lab["hi"]
        d0, d1, clamp = bar_domain(v, lo, hi)
        w = min(BAR_W, b.w - 2 * E.PAD_X - 12)
        tx0, tx1 = b.x - w / 2, b.x + w / 2
        X = lambda t: tx0 + (t - d0) / (d1 - d0) * w
        y = b.y1 - E.PAD_Y - (nb - i - 0.5) * BAR_H + 1.5
        vx = tx1 if clamp == "high" else tx0 if clamp == "low" else X(v)
        out.append(dict(x0=tx0, x1=tx1, y=y, band=[X(max(lo, d0)), X(min(hi, d1))], vx=vx, clamp=clamp,
                        dir=lab["dir"], value=v, lo=lo, hi=hi))
    return out


# ------------------------------------------------------------------ badges on boxes
def role_icon(case, role):
    icons = dict(themes.ROLE_ICON, **case.meta.get("role_icons", {}))
    return icons.get(role)


def roles_on(case):
    return case.meta.get("glyphs", {}).get("roles", True)


def flags_of(nd):
    fl = list((nd.get("attrs") or {}).get("flags", ()))
    bad = [f for f in fl if f not in themes.FLAGS]
    if bad:
        raise ValueError(f"unknown flag {', '.join(bad)}: use {', '.join(themes.FLAGS)}")
    return [f for f in themes.FLAG_ORDER if f in fl]


# corner slots: the badge's centre relative to the box, (fx, dx, fy, dy) = (x0 + fx*w + dx, y0 + fy*h + dy)
ROLE_SLOTS = {
    "corner": [("tl", 0, 3, 0, 1), ("bl", 0, 3, 1, -1)],                 # straddling the top-left corner
    "tab":    [("tl", 0, 16, 0, 0), ("bl", 0, 16, 1, 0)],                # on the top edge, in from the corner
}
FLAG_STEP = 2 * FLAG_R + 3
FLAG_SLOTS = [("tr", 1, 3, 0, 1), ("br", 1, 3, 1, -1)]   # flags: top-right corner, stepping left; else bottom-right


def slot_xy(b, slot, i=0, step=0.0):
    _, fx, dx, fy, dy = slot
    sgn = -1 if fx == 1 else 1
    return b.x0 + fx * b.w + sgn * (dx + i * step), b.y0 + fy * b.h + dy


def _seg_dist(cx, cy, p, q):
    (x1, y1), (x2, y2) = p, q
    L2 = (x2 - x1) ** 2 + (y2 - y1) ** 2
    t = 0 if L2 == 0 else max(0, min(1, ((cx - x1) * (x2 - x1) + (cy - y1) * (y2 - y1)) / L2))
    return math.hypot(cx - (x1 + t * (x2 - x1)), cy - (y1 + t * (y2 - y1)))


def _rect_dist(cx, cy, x0, y0, x1, y1):
    dx = max(x0 - cx, 0, cx - x1)
    dy = max(y0 - cy, 0, cy - y1)
    return math.hypot(dx, dy)


def collisions(nid, cx, cy, r, boxes, edges, rects, placed, titles=(), marks=()):
    """Why a badge at (cx, cy) radius r would collide: arrows (and their ports), other boxes, badges
    already placed, its container's edge, stage titles, and (HEADER_CLEAR away) a stage's number or
    glyph (marks: header_marks()). Empty when it is clear."""
    why = []
    for ed in edges:
        for p, q in zip(ed["pts"], ed["pts"][1:]):
            if _seg_dist(cx, cy, p, q) < r + BADGE_CLEAR + E.EDGE_W / 2:
                why.append(f"arrow {ed['a']}->{ed['b']}")
                break
    for bid, b in boxes.items():
        if bid != nid and _rect_dist(cx, cy, b.x0, b.y0, b.x1, b.y1) < r + BADGE_CLEAR:
            why.append(f"box {bid}")
    for (oid, ox, oy, orr) in placed:
        if math.hypot(cx - ox, cy - oy) < r + orr + BADGE_CLEAR:
            why.append(f"badge of {oid}")
    b = boxes[nid]
    x0, y0, x1, y1 = rects[b.cluster]
    if not (x0 + r + 4 <= cx <= x1 - r - 4 and y0 + r + 4 <= cy <= y1 - r - 4):
        why.append(f"edge of {b.cluster}")
    for (tx0, ty0, tx1, ty1) in titles:
        if _rect_dist(cx, cy, tx0, ty0, tx1, ty1) < r + BADGE_CLEAR:
            why.append("a stage title")
    for (mx0, my0, mx1, my1) in marks:
        if _rect_dist(cx, cy, mx0, my0, mx1, my1) < r + HEADER_CLEAR:
            why.append("a stage number or glyph")
    return why


def header_marks(rects, meta):
    """{stage: (x0, y0, x1, y1)}: the ink of each stage's number badge and glyph in its header band."""
    out = {}
    for cid, (x0, y0, x1, y1) in rects.items():
        s = meta.get(cid, {})
        if s.get("n") is None and not s.get("icon"):
            continue
        half = max(STAGE_BADGE_R if s.get("n") is not None else 0, STAGE_ICON / 2 if s.get("icon") else 0)
        cy = y0 + HEADER_CY
        out[cid] = (x0 + TITLE_X, cy - half, x0 + title_x(s) - 6, cy + half)
    return out


def header_room(b, mark, role=True, flags=False, placement="corner"):
    """How far (px) box b's first-choice badge slots (its role glyph's, its flags' when it has any) must move
    down to keep HEADER_CLEAR from its stage's header marks (mark: header_marks()[stage]); 0 when they do."""
    mx0, my0, mx1, my1 = mark
    need = 0.0
    slots = ([(ROLE_SLOTS[placement][0], ROLE_R)] if role else []) + ([(FLAG_SLOTS[0], FLAG_R)] if flags else [])
    for slot, r in slots:
        cx, cy = slot_xy(b, slot)
        dx = max(mx0 - cx, 0, cx - mx1)
        if dx >= r + HEADER_CLEAR:
            continue
        need = max(need, my1 + math.sqrt((r + HEADER_CLEAR) ** 2 - dx ** 2) - cy)
    return need


def place_badges(case, boxes, edges, rects, titles, placement="corner", marks=()):
    """Role glyph badges (top-left, else bottom-left) and flag badges (top-right, stepping left; else
    bottom-right): the first slot clear of everything. Returns ({nid: [badge, ...]}, problems)."""
    out, probs, placed = {}, [], []
    marks = list(marks)
    nodes = case.content.NODES
    role_slots = ROLE_SLOTS[placement]
    flag_slots = FLAG_SLOTS
    show_roles = roles_on(case)
    for nid, b in boxes.items():
        badges = []
        if show_roles and role_icon(case, b.role):
            for slot in role_slots:
                cx, cy = slot_xy(b, slot)
                if not collisions(nid, cx, cy, ROLE_R, boxes, edges, rects, placed, titles, marks):
                    break
            else:
                why = collisions(nid, *slot_xy(b, role_slots[0]), ROLE_R, boxes, edges, rects, placed, titles, marks)
                probs.append(f"role glyph of {nid} collides with {', '.join(why)}")
                cx, cy = slot_xy(b, role_slots[0])
                slot = role_slots[0]
            badges.append(dict(kind="role", icon=role_icon(case, b.role), cx=cx, cy=cy, r=ROLE_R, slot=slot[0]))
            placed.append((nid, cx, cy, ROLE_R))
        fl = flags_of(nodes[nid])
        if fl:
            for slot in flag_slots:
                pts = [slot_xy(b, slot, i, FLAG_STEP) for i in range(len(fl))]
                if all(not collisions(nid, x, y, FLAG_R, boxes, edges, rects, placed, titles, marks) for x, y in pts):
                    break
            else:
                slot = flag_slots[0]
                pts = [slot_xy(b, slot, i, FLAG_STEP) for i in range(len(fl))]
                why = sorted({w for x, y in pts for w in collisions(nid, x, y, FLAG_R, boxes, edges, rects, placed, titles, marks)})
                probs.append(f"flag badge of {nid} collides with {', '.join(why)}")
            for f, (x, y) in zip(fl, pts):
                badges.append(dict(kind=f, icon=themes.FLAGS[f][0], cx=x, cy=y, r=FLAG_R, slot=slot[0]))
                placed.append((nid, x, y, FLAG_R))
        if badges:
            out[nid] = badges
    return out, probs


def title_boxes(rects, meta, titles):
    """The ink box of each stage's header (badge, glyph, title) for the collision check."""
    out = []
    for cid, (x0, y0, x1, y1) in rects.items():
        tx = x0 + title_x(meta.get(cid, {}))
        out.append((x0 + 6, y0 + 8, tx + E.text_w(titles[cid], size=E.CL_FS) * 1.1, y0 + 30))
    return out


def title_x(s):
    """Where a stage's title text starts: after its number badge and glyph."""
    x = TITLE_X
    if s.get("n") is not None:
        x += badge_w(s["n"], STAGE_BADGE_R, BADGE_FS) + 6
    if s.get("icon"):
        x += STAGE_ICON + 6
    return x


HEADER_CY = 19.5             # the centre line of a stage's header band (number, glyph, title cap height)


# ------------------------------------------------------------------ SVG pieces
def badge_svg(bd, T, role, cl_fill):
    """A badge on a box: the role glyph on a disk of the box's fill with its border; a flag as a filled disk."""
    if bd["kind"] == "role":
        fill, border, _ = T["roles"][role]
        return [f'<circle cx="{bd["cx"]:.1f}" cy="{bd["cy"]:.1f}" r="{bd["r"]}" fill="{fill}" stroke="{border}" stroke-width="1.2"/>',
                icon_svg(bd["icon"], bd["cx"], bd["cy"], ROLE_ICON_PX, border)]
    col = flag_colour(bd["kind"], T)
    return [f'<circle cx="{bd["cx"]:.1f}" cy="{bd["cy"]:.1f}" r="{bd["r"]}" fill="{col}" stroke="{cl_fill}" stroke-width="1.5"/>',
            icon_svg(bd["icon"], bd["cx"], bd["cy"], FLAG_ICON_PX, flag_icon_colour(bd["kind"], T))]


def flag_colour(kind, T):
    return themes.accent(themes.FLAGS[kind][1], T["flavour"])


def flag_icon_colour(kind, T):
    """A flag's icon: the flavour's ground or its ink, whichever stands out more on the flag's disc
    (a light palette's yellow pitfall disc can take the ink; other discs can take the ground)."""
    disc = flag_colour(kind, T)
    return max((T["canvas"], T["ink"]), key=lambda c: themes.contrast(c, disc))


def lab_colour(d, T):
    """The colour of a high/low mark and its range-bar dot: the raw accent on a light flavour; the sign or
    investigation stroke on a dark one, where the raw accents fall under 3:1 on the investigation fill."""
    if themes.is_dark(T):
        return T["roles"][themes.LAB_ROLE_DARK[d]][1]
    return themes.accent(themes.LAB_ACCENT[d], T["flavour"])


def bar_colours(T, role):
    """(track, band) of a range bar: the box's border faded toward its fill (less on a dark flavour, where the
    border is the light end)."""
    fill, border, _ = T["roles"][role]
    return (themes.mix(border, fill, 0.42), themes.mix(border, fill, 0.45)) if themes.is_dark(T) else \
        (themes.mix(border, fill, 0.55), themes.mix(border, fill, 0.62))


def bar_svg(br, T, role):
    """A range bar: a hairline track, the normal band, the value as a dot (high/low colour); a value off
    the scale sits at the track's end with a chevron beyond it."""
    fill, border, _ = T["roles"][role]
    track, band = bar_colours(T, role)
    col = lab_colour(br["dir"], T) if br["dir"] else border
    y = br["y"]
    out = [f'<line x1="{br["x0"]:.1f}" y1="{y:.1f}" x2="{br["x1"]:.1f}" y2="{y:.1f}" stroke="{track}" stroke-width="1.2" stroke-linecap="round"/>',
           f'<rect x="{br["band"][0]:.1f}" y="{y - 3:.1f}" width="{max(1.0, br["band"][1] - br["band"][0]):.1f}" height="6" rx="3" fill="{band}"/>']
    if br["clamp"]:
        sgn = 1 if br["clamp"] == "high" else -1
        cx = br["vx"] + sgn * 7
        out.append(chevron_svg(cx, y, sgn, col))
    out.append(f'<circle cx="{br["vx"]:.1f}" cy="{y:.1f}" r="3.6" fill="{col}" stroke="{fill}" stroke-width="1.2"/>')
    return out


def chevron_svg(cx, cy, sgn, colour):
    """A small chevron pointing right (sgn 1) or left (-1): the value is beyond the scale."""
    a = 2.6
    return (f'<path d="M{cx - sgn * a:.1f},{cy - 3.4:.1f} L{cx + sgn * a:.1f},{cy:.1f} L{cx - sgn * a:.1f},{cy + 3.4:.1f}" '
            f'fill="none" stroke="{colour}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')


def badge_fs(n, size=BADGE_FS):
    """A number's size in its badge. Two digits keep the size: their badge widens into a pill (badge_w)
    to keep the number readable."""
    return size


def badge_w(n, r, size=BADGE_FS):
    """A number badge's width: a disc of radius r, or a pill (height 2r) wide enough for two or more digits."""
    if n is None:
        return 0.0
    return max(2 * r, round(mono_w(str(n), size) + r, 1))


def number_badge_svg(cx, cy, r, n, T, size=BADGE_FS):
    """A stage number: a filled disc (a pill for two digits) in the header ink, the number in the ground colour."""
    w = badge_w(n, r, size)
    shape = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{T["cl_text"]}"/>' if w <= 2 * r + 0.01 else
             f'<rect x="{cx - w / 2:.1f}" y="{cy - r:.1f}" width="{w:.1f}" height="{2 * r:.1f}" rx="{r}" fill="{T["cl_text"]}"/>')
    return [shape,
            f'<text x="{cx:.1f}" y="{cy + size * 0.36:.1f}" text-anchor="middle" font-family="{T["mono"]}" '
            f'font-size="{size}" font-weight="700" fill="{T["canvas"]}">{n}</text>']


def stage_header_svg(rect, s, T):
    """Number badge and glyph before a stage's title (the title itself is core.cluster_svg's)."""
    x0, y0 = rect[0], rect[1]
    cy = y0 + HEADER_CY
    x = x0 + TITLE_X
    out = []
    if s.get("n") is not None:
        w = badge_w(s["n"], STAGE_BADGE_R, BADGE_FS)
        out += number_badge_svg(x + w / 2, cy, STAGE_BADGE_R, s["n"], T)
        x += w + 6
    if s.get("icon"):
        out.append(icon_svg(s["icon"], x + STAGE_ICON / 2, cy, STAGE_ICON, stage_colour(s, T)))
    return out


def stage_colour(s, T):
    """A stage glyph's colour: the stage-title ink by default; an accent when the case opts in."""
    return themes.glyph_colour(s["accent"], T["flavour"]) if s.get("accent") else T["cl_text"]


def timeline_segments(tl):
    """The ribbon's line, as segments between the markers (TL_GAP after one marker's time to TL_GAP before
    the next badge), the last ending in the arrowhead at the right edge."""
    ms = tl["markers"]
    out = []
    for i, m in enumerate(ms):
        bw = m.get("bw", 2 * TL_BADGE_R)
        a = m["x"] - bw / 2 + m["w"] + TL_GAP
        if i + 1 < len(ms):
            nb = ms[i + 1]
            b = nb["x"] - nb.get("bw", 2 * TL_BADGE_R) / 2 - TL_GAP
        else:
            b = tl["x1"]
        out.append((a, b))
    return out


def timeline_svg(tl, T):
    """The time-course ribbon: heading, a hairline with an arrowhead at "now", a marker per stage."""
    y = tl["y"]
    line = T["muted"]
    out = [f'<text x="{tl["x0"]:.1f}" y="{y + 5:.1f}" font-family="{T["mono"]}" font-size="{E.CL_FS}" font-weight="700" '
           f'fill="{T["cl_text"]}">{html.escape(tl["label"])}</text>']
    segs = timeline_segments(tl)
    ms = tl["markers"]
    # each segment carries the data-stage of the marker it leads to (the closing arrow: the last stage), so the
    # Artifact's Present mode builds the line up with the stages
    for i, (a, b) in enumerate(segs[:-1]):
        out.append(f'<line class="tl-line" data-stage="{ms[i + 1]["stage"]}" x1="{a:.1f}" y1="{y:.1f}" x2="{b:.1f}" y2="{y:.1f}" '
                   f'stroke="{line}" stroke-width="1.2" stroke-linecap="round"/>')
    a, b = segs[-1]
    last = ms[-1]["stage"]
    out += [f'<g class="tl-line" data-stage="{last}"><line x1="{a:.1f}" y1="{y:.1f}" x2="{b - E.ARROW_L + 1.5:.1f}" y2="{y:.1f}" '
            f'stroke="{line}" stroke-width="1.2" stroke-linecap="round"/>',
            f'<path d="{E.arrow_head_d((b, y), 1, 0)}" fill="{line}"/></g>']
    for m in tl["markers"]:
        x = m["x"]
        out.append(f'<g class="tl-marker" data-stage="{m["stage"]}">')
        out += number_badge_svg(x, y, TL_BADGE_R, m["n"], T, size=TL_BADGE_FS)
        tx = x + m.get("bw", 2 * TL_BADGE_R) / 2 + 6
        if m["icon"]:
            out.append(icon_svg(m["icon"], tx + STAGE_ICON / 2, y, STAGE_ICON, stage_colour(m, T)))
            tx += STAGE_ICON + 5
        out.append(f'<text x="{tx:.1f}" y="{y + 5:.1f}" font-family="{T["sans"]}" font-size="{E.FS}" '
                   f'font-weight="{E.WGHT[0]}" fill="{T["ink"]}">{html.escape(m["when"])}</text>')
        out.append('</g>')
    return out


# ------------------------------------------------------------------ the key
def key_items(case, used):
    """What the key lists, as (kind, id, label) rows in two columns: boxes (and stage numbers) on the
    left; arrows, flags and lab marks on the right. `used` says which layers the diagram draws."""
    from render import KEY_ROLES, KEY_ARROWS
    left = [("role", r, case.role_name[r]) for r in case.key_roles(KEY_ROLES)]
    if used.get("stages"):
        left.append(("stage", None, "Sequence number"))
    right = [("arrow", kind, lab) for lab, kind in case.key_arrows(KEY_ARROWS)]
    right += [("flag", f, themes.FLAGS[f][2]) for f in themes.FLAG_ORDER if f in used.get("flags", ())]
    if used.get("marks"):
        right.append(("mark", None, "Raised / reduced result"))
    if used.get("bars"):
        right.append(("bar", None, "Band: reference interval; dot: result; › beyond scale"))
    return left, right


def key_layout(x, y, w, h, case, used):
    """Rows of the key at absolute positions: (kind, id, label, row_x, row_y, label_x, room).
    row_y is the top of a 20 px row."""
    left, right = key_items(case, used)
    col1, top = x + 18, y + 48
    n = max(len(left), len(right))
    step = min(44, (h - 62) / n)
    lab1 = col1 + 46
    w1 = max(E.text_w(lab) for _, _, lab in left)
    col2 = lab1 + w1 + 40
    rows = []
    for i, (k, kid, lab) in enumerate(left):
        rows.append((k, kid, lab, col1, top + i * step, lab1, col2 - 40 - lab1))
    for i, (k, kid, lab) in enumerate(right):
        rows.append((k, kid, lab, col2, top + i * step, col2 + 66, x + w - 16 - (col2 + 66)))
    for k, kid, lab, rx, ry, lx, room in rows:
        if E.text_w(lab) > room + 0.5:
            raise ValueError(f"key text overflows the panel ({lab!r}): widen KEY_SPAN or shorten the label")
    if top + (n - 1) * step + 20 > y + h - 8:
        raise ValueError("key rows overflow the panel: make KEY_SPAN taller")
    return rows, step


def key_sample_mark(T):
    """The key's sample of the lab marks: the two arrows in their colours."""
    hi, lo = lab_colour("high", T), lab_colour("low", T)
    return f'<tspan fill="{hi}">{themes.LAB_MARK["high"]}</tspan>  <tspan fill="{lo}">{themes.LAB_MARK["low"]}</tspan>'


def key_svg_rows(rows, T, case):
    """SVG for the key's rows (the panel and its heading are render.key_block's)."""
    wt = E.WGHT[0]
    out = []
    for k, kid, lab, rx, ry, lx, room in rows:
        cy = ry + 10
        if k == "role":
            fill, border, _ = T["roles"][kid]
            dash = E.anat_dash_attr() if kid == "anat" else ""
            out.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="34" height="20" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.5"{dash}/>')
            ic = role_icon(case, kid)
            if ic and roles_on(case):
                out += badge_svg(dict(kind="role", icon=ic, cx=rx + 3, cy=ry + 1, r=ROLE_R), T, kid, T["key_fill"])
        elif k == "stage":
            out += number_badge_svg(rx + 11, cy, STAGE_BADGE_R, 1, T)
        elif k == "arrow":
            colour, dashed, head = T["edges"][kid]
            out += E.edge_svg([(rx, cy), (rx + 52, cy)], colour, dashed, head, gap=0)
        elif k == "flag":
            out += badge_svg(dict(kind=kid, icon=themes.FLAGS[kid][0], cx=rx + 26, cy=cy, r=FLAG_R), T, None, T["key_fill"])
        elif k == "mark":
            out.append(f'<text x="{rx + 26:.1f}" y="{cy + 5:.1f}" text-anchor="middle" font-family="{T["sans"]}" font-size="{E.FS}" '
                       f'font-weight="{wt}" fill="{T["ink"]}">{key_sample_mark(T)}</text>')
        elif k == "bar":
            fill, border, _ = T["roles"]["inv"]
            out.append(f'<rect x="{rx - 2:.1f}" y="{ry:.1f}" width="56" height="20" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.5"/>')
            out += bar_svg(dict(x0=rx + 6, x1=rx + 38, y=cy, band=[rx + 12, rx + 24], vx=rx + 38, clamp="high", dir="high"), T, "inv")
        out.append(f'<text x="{lx:.1f}" y="{ry + 15:.1f}" font-family="{T["sans"]}" font-size="{E.FS}" font-weight="{wt}" '
                   f'fill="{T["ink"]}">{html.escape(lab)}</text>')
    return out


# ================================================================== the anatomy inset and edge labels
#
# The inset: an illustration recoloured into each palette, on a panel of the stage grid
# (layout.py INSET_SPAN, like KEY_SPAN), with a header like the stage headers and the case's own labels on thin
# leader lines. The panel's fill is the recoloured image's own background (the card colour), so the image melts
# into its panel; the image's corners are rounded (INSET_IMG_RX) in every output: a vector clip in the SVG/PDF/PNG
# (the image itself opaque, its fade blended into the card), a pre-rounded alpha PNG in the .drawio (inset_png).
#
# Configure the inset through case.json. The image field names a file beneath the case directory.
# title supplies the panel heading. icon optionally selects a symbol from icons.py, shown in title ink.
# bg selects a palette token for the surrounding card, usually base or key_fill.
# dark_model chooses how recolour.py remaps brightness for dark palettes. keep preserves the source ranking.
# crop provides source-pixel bounds in x0, y0, x1, y1 order; omission enables automatic cropping.
# fade lists boundaries to blend into the card. Automatic fading follows clipped illustration boundaries.
# credit places a subdued attribution at the lower-right corner; diagram.illustration.credit supplies a fallback.
# mode chooses raster pixels, vector paths traced with potrace, or an existing themed SVG using palette tokens.
# anchors preserves distinct source colours as separate palette accents, even for small focal structures.
# labels holds annotation records. Each record has text and an at position, expressed as fractional source coordinates.
# side chooses left or right placement. bold emphasizes a focal annotation and gives its endpoint an open ring.
# A ring value overrides that endpoint's radius. Omit side to use the anchor's half of the illustration.
#
# The per-flavour images are made by the build (recolour.py, raster mode --scale 2 or vector mode, no rounding) into
# the case's assets/ beside the source, as <stem>-<flavour>.png (.svg) with a <stem>-<flavour>.json sidecar holding recolour.py's
# --meta and a key (a hash of the source, recolour.py and the parameters): a key that still matches is reused.
#
# A "themed" SVG writes its colours as tokens in braces, filled from each palette's theme: {canvas}, {ink}, {muted},
# {label}, {cl_fill}, {cl_stroke}, {cl_text}, {key_fill}, {key_stroke}, and per role {risk.fill}, {risk.stroke},
# {risk.text} (roles risk, mech, anat, sign, inv, dx, tx). Its viewBox must start at 0 0. "bg" names the card's
# theme key (default key_fill). The build writes <stem>-<flavour>.svg beside the source.
import base64 as _b64, hashlib as _hashlib, io as _io, json as _json, pathlib as _pathlib, subprocess as _sp, sys as _sys

INSET_LABEL_FS = 14            # the inset's labels: body size (the configured sans (fonts.sans); bold for the case's focus)
INSET_IMG_PAD = 14             # the image clears the panel's top and bottom edges by this
INSET_IMG_RX = 9.0             # the image's corner radius: an image card inside a container (boxes 7, containers 12)
INSET_LABEL_GAP = 34           # from a label's text to the image's edge (the leader's first run)
INSET_LABEL_STEP = round(E.LH * INSET_LABEL_FS / E.FS + 0.4, 1)   # least distance between two labels' centre lines
                               # on one side: a line height, so a label stays level with its anchor wherever it can
INSET_IMG_RES = 3              # embedded image pixels per canvas px (crisp at the PNG's 1.5x and a zoomed PDF)
INSET_FADE = 28                # an image edge the illustration is cut by fades into the card over this many px
INSET_BG = "base"              # default card token: the key panel's fill in each palette
INSET_DARK_MODEL = "keep"      # Preserve relative source brightness in dark output
LEADER_W = 1.0
LEADER_HALO = 3.4              # a card-coloured line under each leader, so it reads over the organs in both flavours
ANCHOR_R = 2.3
INSET_RING_R = 11.0            # a bold label's open ring round its anchor (the focus of the panel)
INSET_RING_W = 1.6
CREDIT_FS = 11
CREDIT_INSET = 14              # the credit's distance from the panel's right and bottom edges (baseline)


def inset_spec(case):
    """case.json "inset", checked; None when the case has none."""
    spec = case.meta.get("inset")
    if not spec:
        return None
    for k in ("image", "title", "labels"):
        if not spec.get(k):
            raise ValueError(f"case.json inset: {k!r} is missing")
    if spec.get("icon"):
        check_icons([spec["icon"]])
    if spec.get("dark_model", INSET_DARK_MODEL) not in ("keep", "invert", "pastel"):
        raise ValueError("case.json inset: dark_model is 'keep', 'invert' or 'pastel'")
    if spec.get("mode", "raster") not in ("raster", "vector", "themed"):
        raise ValueError("case.json inset: mode is 'raster', 'vector' or 'themed'")
    if spec.get("mode") == "themed" and not str(spec["image"]).lower().endswith(".svg"):
        raise ValueError("case.json inset: a themed image is an SVG file")
    for h in spec.get("anchors", ()):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(h)):
            raise ValueError(f"case.json inset: anchor {h!r} is not a #rrggbb colour")
    if spec.get("fade") is not None and not set(spec["fade"]) <= {"top", "bottom", "left", "right"}:
        raise ValueError("case.json inset: fade lists edges: 'top', 'bottom', 'left', 'right'")
    for i, lab in enumerate(spec["labels"]):
        at = lab.get("at")
        if not lab.get("text") or not (isinstance(at, (list, tuple)) and len(at) == 2 and all(0 <= v <= 1 for v in at)):
            raise ValueError(f"case.json inset label {i}: needs text and at = [x, y] as fractions of the image")
        if lab.get("side") not in (None, "left", "right"):
            raise ValueError(f"case.json inset label {lab['text']!r}: side is 'left' or 'right'")
    if not spec.get("credit"):            # the config's credit line when case.json gives none
        try:
            import style
            credit = style.get("diagram.illustration.credit")
        except Exception:  # noqa: BLE001  (no config: no credit)
            credit = None
        if credit:
            spec = dict(spec, credit=str(credit))
    return spec


_TOKEN = re.compile(r"\{([a-z_]+)(?:\.(fill|stroke|text))?\}")


def sanitise_svg(text):
    """Keep passive SVG drawing primitives and fragment references; discard active content and URLs."""
    import xml.etree.ElementTree as ET
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as ex:
        raise ValueError(f"inset SVG is not valid XML: {ex}") from ex
    allowed = set("svg g defs clipPath mask rect circle ellipse path line polyline polygon text tspan "
                  "title desc linearGradient radialGradient stop use symbol pattern marker".split())
    attrs = set("id x y x1 y1 x2 y2 cx cy r rx ry width height viewBox preserveAspectRatio d points "
                "transform fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap "
                "stroke-linejoin stroke-dasharray stroke-dashoffset stroke-miterlimit opacity clip-path "
                "clip-rule clipPathUnits mask maskUnits maskContentUnits offset stop-color stop-opacity "
                "gradientUnits gradientTransform spreadMethod fx fy fr font-family font-size font-style "
                "font-weight text-anchor dominant-baseline dx dy rotate textLength lengthAdjust href "
                "marker-start marker-mid marker-end markerWidth markerHeight refX refY orient markerUnits "
                "patternUnits patternContentUnits patternTransform".split())
    if root.tag != "{" + ns + "}svg":
        raise ValueError("inset SVG needs an SVG root element")

    def clean(el):
        for key, value in list(el.attrib.items()):
            name = key.rsplit("}", 1)[-1]
            compact = re.sub(r"[\s\x00-\x1f]+", "", value).lower()
            urls = re.findall(r"url\(\s*['\"]?([^)'\"]+)", value, re.I)
            safe = (name in attrs and ("}" not in key or key == "{http://www.w3.org/1999/xlink}href")
                    and not any(v in compact for v in ("javascript:", "vbscript:", "http:", "https:", "file:", "data:", "//"))
                    and all(re.fullmatch(r"#[A-Za-z_][\w.:-]*", url.strip()) for url in urls)
                    and (name != "href" or re.fullmatch(r"#[A-Za-z_][\w.:-]*", value)))
            if not safe:
                del el.attrib[key]
            elif key == "{http://www.w3.org/1999/xlink}href":
                el.set("href", value)
                del el.attrib[key]
        for child in list(el):
            if not isinstance(child.tag, str) or child.tag not in {"{" + ns + "}" + t for t in allowed}:
                el.remove(child)
            else:
                clean(child)
    clean(root)
    return ET.tostring(root, encoding="unicode")


def themed_svg(src_text, T, card_key="key_fill"):
    """A themed inset SVG coloured for one theme, in the shape of a recolour.py vector trace: (svg text, meta)."""
    src_text = sanitise_svg(src_text)
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', src_text)
    if not m:
        raise ValueError("case.json inset: a themed SVG needs viewBox=\"0 0 W H\"")
    w, h = float(m.group(1)), float(m.group(2))

    def colour(mt):
        key, part = mt.group(1), mt.group(2)
        if part:
            if key not in T["roles"]:
                raise ValueError(f"themed inset: no role {key!r}")
            return T["roles"][key][("fill", "stroke", "text").index(part)]
        if key not in T or not isinstance(T[key], str) or not T[key].startswith("#"):
            raise ValueError(f"themed inset: no theme colour {key!r}")
        return T[key]
    body = src_text[src_text.index(">", src_text.index("<svg")) + 1:src_text.rindex("</svg>")]
    body = _TOKEN.sub(colour, body)
    card = T.get(card_key) if isinstance(T.get(card_key), str) else T["key_fill"]
    W, H = (int(w) if w.is_integer() else w), (int(h) if h.is_integer() else h)
    svg = "\n".join([f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
                     f'<defs><clipPath id="c"><rect width="{W}" height="{H}" rx="0"/></clipPath></defs>',
                     f'<g clip-path="url(#c)"><rect width="{W}" height="{H}" fill="{card}"/>', body.strip(), "</g></svg>"])
    meta = dict(src_size=[W, H], crop=[0, 0, W, H], out_size=[W, H], card=card, mode="themed", flavour=T["flavour"])
    return svg + "\n", meta


def inset_prepare(case, flavours=None):
    """Recolour the inset's source into each flavour (cached; see above). Returns {flavour: meta}, where meta is
    recolour.py's --meta (crop, src_size, out_size, card ...) plus "png", the absolute path of the image."""
    spec = inset_spec(case)
    if spec is None:
        return None
    src = (case.dir / spec["image"]).resolve()
    if not src.is_relative_to(case.dir):
        raise ValueError(f"case.json inset: {spec['image']} is outside the case folder; copy it into the case's assets/")
    if not src.exists():
        raise ValueError(f"case.json inset: no image {spec['image']} in the case folder")
    flavours = tuple(flavours or themes.final_palettes())
    tool = _pathlib.Path(__file__).resolve().parent / "recolour.py"
    out = {}
    mode = spec.get("mode", "raster")
    if mode == "themed":
        text = src.read_text()
        for fl in flavours:
            svg, meta = themed_svg(text, themes.THEMES[fl], spec.get("bg", "key_fill"))
            dst = src.with_name(f"{src.stem}-{fl}.svg")
            if not dst.exists() or dst.read_text() != svg:
                dst.write_text(svg)
                print(f"inset: coloured {src.name} for {fl} -> {dst.name}")
            meta["png"] = str(dst)
            out[fl] = meta
        return out
    import style
    cfg_flag = ["--config", str(style.config_path())] if style.config_path() else []
    for fl in flavours:
        params = dict(flavour=fl, bg=spec.get("bg", INSET_BG), mode=mode, scale=2, radius=0, crop=spec.get("crop"),
                      dark_model=spec.get("dark_model", INSET_DARK_MODEL), anchors=list(spec.get("anchors", ())))
        key = _hashlib.sha256(src.read_bytes() + tool.read_bytes() + _json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
        png = src.with_name(f"{src.stem}-{fl}.{'svg' if mode == 'vector' else 'png'}")
        side = png.with_suffix(".json")
        meta = None
        if png.exists() and side.exists():
            try:
                meta = _json.loads(side.read_text())
            except ValueError:
                meta = None
        if not meta or meta.get("key") != key:
            cmd = [_sys.executable, "-B", str(tool), str(src), "--flavour", fl, "--bg", params["bg"], "--mode", mode,
                   "--scale", "2", "--radius", "0", "--dark-model", params["dark_model"], "--out", str(png), "--meta", str(side)]
            cmd += cfg_flag
            for h in params["anchors"]:
                cmd += ["--anchor", h]
            if params["crop"]:
                cmd += ["--crop", ",".join(str(int(v)) for v in params["crop"])]
            r = _sp.run(cmd, capture_output=True, text=True)
            if r.returncode:
                raise ValueError(f"recolour.py failed on {src.name} ({fl}): {r.stderr.strip()[-400:]}")
            meta = _json.loads(side.read_text())
            meta["key"] = key
            side.write_text(_json.dumps(meta, indent=1) + "\n")
            print(f"inset: recoloured {src.name} into {fl} -> {png.name}")
        meta["png"] = str(png)
        out[fl] = meta
    return out


def cut_edges(png, card, band=2, tol=14, share=0.02):
    """The edges of a recoloured image that the illustration runs into (its crop cuts it): more than `share` of
    the outermost `band` px differ from the card colour by more than `tol` (0-255, any channel)."""
    from PIL import Image
    import numpy as np
    im = np.asarray(Image.open(png).convert("RGB")).astype(int)
    c = np.array([int(card[i:i + 2], 16) for i in (1, 3, 5)])
    off = np.abs(im - c).max(-1) > tol
    sides = dict(top=off[:band], bottom=off[-band:], left=off[:, :band], right=off[:, -band:])
    return [k for k in ("top", "bottom", "left", "right") if sides[k].mean() > share]


def _spread(ys, lo, hi, step):
    """Positions as near ys (sorted) as possible, at least `step` apart, within [lo, hi]."""
    out = list(ys)
    for i in range(len(out)):
        out[i] = max(out[i], lo if i == 0 else out[i - 1] + step)
    for i in range(len(out) - 1, -1, -1):
        out[i] = min(out[i], hi if i == len(out) - 1 else out[i + 1] - step)
    return out


def inset_geom(spec, rect, prep, case_dir, title_x=None):
    """Where the inset's image, labels and leaders sit in its panel (rect). prep: inset_prepare()'s result.
    Returns (geometry, problems)."""
    x0, y0, x1, y1 = rect
    m = next(iter(prep.values()))
    cx0, cy0, cx1, cy1 = m["crop"]
    sw, sh = m["src_size"]
    ih = (y1 - y0) - 2 * INSET_IMG_PAD
    iw = ih * (cx1 - cx0) / (cy1 - cy0)
    ix, iy = (x0 + x1) / 2 - iw / 2, y0 + INSET_IMG_PAD
    probs = []
    labs = []
    for lab in spec["labels"]:
        fx, fy = lab["at"]
        ax = ix + (fx * sw - cx0) / (cx1 - cx0) * iw
        ay = iy + (fy * sh - cy0) / (cy1 - cy0) * ih
        if not (ix <= ax <= ix + iw and iy <= ay <= iy + ih):
            probs.append(f"inset label {lab['text']!r}: its anchor is outside the cropped image")
        side = lab.get("side") or ("left" if ax < ix + iw / 2 else "right")
        bold = bool(lab.get("bold"))
        ring = float(lab.get("ring", INSET_RING_R)) if bold else 0.0
        labs.append(dict(text=lab["text"], bold=bold, side=side, ax=ax, ay=ay, ring=ring,
                         w=E.text_w(lab["text"], bold, size=INSET_LABEL_FS)))
    tx = title_x if title_x is not None else TITLE_X
    for side in ("left", "right"):
        group = sorted((l for l in labs if l["side"] == side), key=lambda l: l["ay"])
        lo = y0 + E.CL_TOP + 4 if side == "left" else y0 + INSET_IMG_PAD + 8
        ys = _spread([l["ay"] for l in group], lo, y1 - INSET_IMG_PAD - 6, INSET_LABEL_STEP)
        for l, y in zip(group, ys):
            l["y"] = y
            if side == "left":
                l["x"] = ix - INSET_LABEL_GAP                      # text ends here (right-aligned)
                start = [l["x"] + 6, y]
                if l["x"] - l["w"] < x0 + 16:
                    probs.append(f"inset label {l['text']!r} runs out of the panel on the left")
            else:
                l["x"] = ix + iw + INSET_LABEL_GAP                 # text starts here (left-aligned)
                start = [l["x"] - 6, y]
                if l["x"] + l["w"] > x1 - 16:
                    probs.append(f"inset label {l['text']!r} runs out of the panel on the right")
            end = [l["ax"], l["ay"]]
            if l["ring"]:          # the leader stops at the ring, so nothing crosses the structure it circles
                d = math.hypot(start[0] - end[0], start[1] - end[1])
                if d > l["ring"]:
                    end = [end[0] + (start[0] - end[0]) * l["ring"] / d, end[1] + (start[1] - end[1]) * l["ring"] / d]
            l["lead"] = [start, end]
            if abs(y - l["ay"]) > 0.05:
                l["tilt"] = round(y - l["ay"], 2)          # the label had to move off its anchor's level
    # leaders must not cross one another
    for i, a in enumerate(labs):
        for b in labs[i + 1:]:
            if _cross(a["lead"], b["lead"]):
                probs.append(f"inset leaders of {a['text']!r} and {b['text']!r} cross")
    vector = spec.get("mode", "raster") in ("vector", "themed")
    imgs = {fl: dict(png=_pathlib.Path(mm["png"]).relative_to(case_dir).as_posix(), card=mm["card"], vector=vector)
            for fl, mm in prep.items()}
    if spec.get("fade") is not None:
        fade = list(spec["fade"])
    elif vector:              # judge the cut edges on a rasterised copy of the trace
        r = _sp.run(["rsvg-convert", "-w", "600", m["png"]], capture_output=True, check=True)
        fade = cut_edges(_io.BytesIO(r.stdout), m["card"])
    else:
        fade = cut_edges(m["png"], m["card"])
    geo = dict(rect=list(rect), title=spec["title"], icon=spec.get("icon"), accent=None,
               img=dict(x=ix, y=iy, w=iw, h=ih, rx=INSET_IMG_RX, crop=list(m["crop"]), src=spec["image"],
                        fade=fade, fade_px=INSET_FADE if fade else 0, vector=vector),
               images=imgs, labels=labs, title_x=tx)
    # the panel's header must stay clear of the image (the image starts INSET_IMG_PAD below the panel's top)
    tx_end = x0 + tx + 0.6 * E.CL_FS * len(spec["title"])        # the mono's advance is 0.6 em
    if iy < y0 + 30 and tx_end + 6 > ix:
        probs.append("inset title runs into the image: shorten the title or widen INSET_SPAN")
    if spec.get("credit"):     # inside the panel, bottom right, beside the image it credits
        cw = E.text_w(spec["credit"], size=CREDIT_FS)
        geo["credit"] = dict(text=spec["credit"], x=x1 - CREDIT_INSET, y=y1 - CREDIT_INSET + 2, w=cw, fs=CREDIT_FS)
        cr = (x1 - CREDIT_INSET - cw, y1 - CREDIT_INSET + 2 - CREDIT_FS, x1 - CREDIT_INSET, y1 - CREDIT_INSET + 5)
        if _rects_overlap(cr, (ix, iy, ix + iw, iy + ih), 4):
            probs.append("inset credit runs into the image: shorten it or widen INSET_SPAN")
        for l in labs:
            lr = (l["x"] - (l["w"] if l["side"] == "left" else 0), l["y"] - 9, l["x"] + (0 if l["side"] == "left" else l["w"]), l["y"] + 9)
            if _rects_overlap(cr, lr, 4):
                probs.append(f"inset credit runs into the label {l['text']!r}")
    return geo, probs


def _cross(p, q):
    """Do segments p and q (each [[x, y], [x, y]]) cross properly?"""
    (a, b), (c, d) = p, q
    def orient(p1, p2, p3):
        v = (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])
        return (v > 1e-9) - (v < -1e-9)
    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0


def inset_title_x(spec):
    return TITLE_X + (STAGE_ICON + 6 if spec.get("icon") else 0)


_PNG_CACHE = {}


def inset_png(path, w, h, rx, res=INSET_IMG_RES, fade=(), fade_px=0, card=None):
    """The inset image resized to (w, h) canvas px at `res` pixels per px, with each edge in `fade` ramped over
    fade_px canvas px (smoothstep) so an illustration cut by its crop melts into the card: PNG bytes.

    card None: the ramp and rounded transparent corners (radius rx canvas px) are the PNG's alpha; the .drawio's
    image cell uses this, since draw.io cannot clip an image. card "#rrggbb": an opaque PNG with the ramp blended
    into that colour (the panel's own fill, so it looks the same) and no alpha; the SVG rounds its corners with a
    vector clip instead (inset_svg). cairo stores alpha premultiplied, so a soft-masked PNG in the PDF gets black
    under its transparent corners, which a viewer's interpolation bleeds into a grey fringe."""
    from PIL import Image, ImageDraw
    import numpy as np
    k = (str(path), round(w, 2), round(h, 2), rx, res, tuple(fade), fade_px, card)
    if k in _PNG_CACHE:
        return _PNG_CACHE[k]
    im = Image.open(path).convert("RGB")
    W, H = max(1, round(w * res)), max(1, round(h * res))
    im = im.resize((W, H), Image.LANCZOS)
    keep = np.ones((H, W))
    if fade and fade_px:
        n = fade_px * res

        def ramp(L):
            t = np.clip((np.arange(L) + 0.5) / n, 0, 1)
            return t * t * (3 - 2 * t)
        if "top" in fade:
            keep *= ramp(H)[:, None]
        if "bottom" in fade:
            keep *= ramp(H)[::-1][:, None]
        if "left" in fade:
            keep *= ramp(W)[None, :]
        if "right" in fade:
            keep *= ramp(W)[::-1][None, :]
    if card:
        c = np.array([int(card[i:i + 2], 16) for i in (1, 3, 5)], float)
        rgb = np.asarray(im).astype(float)
        im = Image.fromarray(np.round(rgb * keep[..., None] + c * (1 - keep[..., None])).astype(np.uint8), "RGB")
    else:
        ss = 4
        mask = Image.new("L", (W * ss, H * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W * ss - 1, H * ss - 1), radius=rx * res * ss, fill=255)
        alpha = np.asarray(mask.resize((W, H), Image.LANCZOS)).astype(float) / 255 * keep
        im.putalpha(Image.fromarray(np.round(alpha * 255).astype(np.uint8), "L"))
    buf = _io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    _PNG_CACHE[k] = buf.getvalue()
    return _PNG_CACHE[k]


def inset_image_png(ins, case_dir, flavour, opaque=False):
    """inset_png() for one flavour's image as the geometry places it: with alpha (the .drawio), or opaque on the
    card colour (the SVG, which clips the corners)."""
    im = ins["img"]
    src = ins["images"].get(flavour) or next(iter(ins["images"].values()))
    return inset_png(case_dir / src["png"], im["w"], im["h"], im["rx"], fade=im.get("fade", ()), fade_px=im.get("fade_px", 0),
                     card=src["card"] if opaque else None)


_SVG_CACHE = {}


def vector_body(path):
    """A recolour.py vector trace: (width, height, its drawing as SVG elements without the outer <svg> and its clip)."""
    raw = _pathlib.Path(path).read_bytes()
    k = _hashlib.sha256(raw).hexdigest()
    if k not in _SVG_CACHE:
        t = sanitise_svg(raw.decode("utf-8"))
        w, h = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', t).groups())
        body = t[t.index('<g clip-path="url(#c)">') + len('<g clip-path="url(#c)">'):t.rindex("</g>")]
        _SVG_CACHE[k] = (w, h, body.strip())
    return _SVG_CACHE[k]


def fade_overlays(x, y, w, h, fade, fade_px, card, uid):
    """Card-coloured gradients over the edges an illustration is cut by (smoothstep, as inset_png's ramp)."""
    if not fade or not fade_px:
        return []
    stops = "".join(f'<stop offset="{t}" stop-color="{card}" stop-opacity="{1 - t * t * (3 - 2 * t):.3f}"/>'
                    for t in (0, 0.25, 0.5, 0.75, 1))
    out = []
    for side in fade:
        gid = f"{uid}-{side}"
        vec = {"top": (0, 0, 0, 1), "bottom": (0, 1, 0, 0), "left": (0, 0, 1, 0), "right": (1, 0, 0, 0)}[side]
        rect = {"top": (x, y, w, fade_px), "bottom": (x, y + h - fade_px, w, fade_px),
                "left": (x, y, fade_px, h), "right": (x + w - fade_px, y, fade_px, h)}[side]
        out.append(f'<linearGradient id="{gid}" x1="{vec[0]}" y1="{vec[1]}" x2="{vec[2]}" y2="{vec[3]}">{stops}</linearGradient>')
        out.append(f'<rect x="{rect[0]:.2f}" y="{rect[1]:.2f}" width="{rect[2]:.2f}" height="{rect[3]:.2f}" fill="url(#{gid})"/>')
    return out


def inset_vector_svg(ins, case_dir, flavour, standalone=False):
    """The vector inset image as the geometry places it: the trace scaled into (x, y, w, h), its cut edges faded
    into the card, its corners rounded by a clip. standalone: an SVG document at the image's own size (the
    .drawio's image cell); else elements for the page (ids unique per flavour, as the Artifact inlines all)."""
    im = ins["img"]
    src = ins["images"].get(flavour) or next(iter(ins["images"].values()))
    vw, vh, body = vector_body(case_dir / src["png"])
    x, y = (0.0, 0.0) if standalone else (im["x"], im["y"])
    uid = f"inset-{flavour}"
    out = [f'<clipPath id="{uid}-clip"><rect x="{x:.2f}" y="{y:.2f}" width="{im["w"]:.2f}" height="{im["h"]:.2f}" '
           f'rx="{im["rx"]}" ry="{im["rx"]}"/></clipPath>',
           f'<g clip-path="url(#{uid}-clip)"><g transform="translate({x:.3f},{y:.3f}) scale({im["w"] / vw:.6f},{im["h"] / vh:.6f})">',
           body, '</g>']
    out += fade_overlays(x, y, im["w"], im["h"], im.get("fade", ()), im.get("fade_px", 0), src["card"], f"{uid}-fade")
    out.append('</g>')
    if standalone:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{im["w"]:.2f}" height="{im["h"]:.2f}" '
                f'viewBox="0 0 {im["w"]:.2f} {im["h"]:.2f}">' + "".join(out) + "</svg>")
    return out


def ring_colour(T):
    """A bold inset label's ring: the sign stroke, the page's alerting red at contrast on both grounds."""
    return T["roles"]["sign"][1]


def inset_svg(ins, T, flavour, case_dir):
    """SVG of the inset panel: card, header, image (rounded, as a data URI), leaders and labels."""
    x0, y0, x1, y1 = ins["rect"]
    card = ins["images"][flavour]["card"] if flavour in ins["images"] else T["key_fill"]
    out = [f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="12" ry="12" fill="{card}" '
           f'stroke="{T["cl_stroke"]}" stroke-width="1.2"/>']
    cy = y0 + HEADER_CY
    if ins.get("icon"):
        col = themes.glyph_colour(ins["accent"], T["flavour"]) if ins.get("accent") else T["cl_text"]
        out.append(icon_svg(ins["icon"], x0 + TITLE_X + STAGE_ICON / 2, cy, STAGE_ICON, col))
    out.append(f'<text x="{x0 + ins["title_x"]:.1f}" y="{y0 + 24:.1f}" font-family="{T["mono"]}" font-size="{E.CL_FS}" '
               f'font-weight="700" fill="{T["cl_text"]}">{html.escape(ins["title"])}</text>')
    im = ins["img"]
    if im.get("vector"):
        out += inset_vector_svg(ins, case_dir, flavour)
    else:
        data = _b64.b64encode(inset_image_png(ins, case_dir, flavour, opaque=True)).decode()
        clip = f"inset-image-clip-{flavour}"     # unique per flavour: the Artifact page inlines every SVG in one document
        out.append(f'<clipPath id="{clip}"><rect x="{im["x"]:.2f}" y="{im["y"]:.2f}" width="{im["w"]:.2f}" '
                   f'height="{im["h"]:.2f}" rx="{im["rx"]}" ry="{im["rx"]}"/></clipPath>')
        out.append(f'<image x="{im["x"]:.2f}" y="{im["y"]:.2f}" width="{im["w"]:.2f}" height="{im["h"]:.2f}" '
                   f'preserveAspectRatio="none" clip-path="url(#{clip})" href="data:image/png;base64,{data}"/>')
    out.append('<g class="labels">')
    for l in ins["labels"]:
        (lx, ly), (ax, ay) = l["lead"]
        out.append(f'<line x1="{lx:.1f}" y1="{ly:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" stroke="{card}" stroke-width="{LEADER_HALO}" stroke-linecap="round"/>')
        out.append(f'<line x1="{lx:.1f}" y1="{ly:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" stroke="{T["ink"]}" stroke-width="{LEADER_W}" stroke-linecap="round"/>')
        if l.get("ring"):
            out.append(f'<circle cx="{l["ax"]:.1f}" cy="{l["ay"]:.1f}" r="{l["ring"]}" fill="none" stroke="{card}" '
                       f'stroke-width="{INSET_RING_W + 2 * 1.2}"/>')
            out.append(f'<circle cx="{l["ax"]:.1f}" cy="{l["ay"]:.1f}" r="{l["ring"]}" fill="none" stroke="{ring_colour(T)}" '
                       f'stroke-width="{INSET_RING_W}"/>')
        else:
            out.append(f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="{ANCHOR_R}" fill="{T["ink"]}" stroke="{card}" stroke-width="1.4"/>')
        anchor = "end" if l["side"] == "left" else "start"
        wt = E.WGHT[1] if l["bold"] else E.WGHT[0]
        out.append(f'<text x="{l["x"]:.1f}" y="{E.baseline(l["y"], INSET_LABEL_FS):.1f}" text-anchor="{anchor}" font-family="{T["sans"]}" '
                   f'font-size="{INSET_LABEL_FS}" font-weight="{wt}" fill="{T["ink"]}">{E.rich(l["text"])}</text>')
    out.append('</g>')
    return out


def credit_svg(text, x, y, T, size=CREDIT_FS):
    """The illustration credit, small and muted, right-aligned at (x, baseline y)."""
    return [f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="end" font-family="{T["sans"]}" font-size="{size}" '
            f'font-weight="{E.WGHT[0]}" fill="{T["label"]}">{html.escape(text)}</text>']


# ------------------------------------------------------------------ edge labels
# A connector between two stages may carry a short verb (content.py e(a, b, kind, label="migrates")), only where
# its meaning is not obvious. The label sits on a pill in the canvas colour on the longest straight segment of its
# route (the next longest when that has no clear place), at the clear spot nearest the segment's middle; a pill
# keeps clear of boxes, other connectors, container borders (it is wholly inside or wholly outside each panel),
# badges, stage titles and other labels. A label with no clear place anywhere is a PROBLEM.
EDGE_LABEL_FS = 14         # body size matches the page's default
EDGE_LABEL_PAD_X = 6
EDGE_LABEL_H = 18
LABEL_CLEAR = 3.0          # from boxes, connectors, badges and other labels
LABEL_BORDER_CLEAR = 4.0   # from a container's border
LABEL_END_CLEAR = 8.0      # from a segment's ends (a bend or an arrowhead)


def edge_label_size(text):
    return E.text_w(text, size=EDGE_LABEL_FS, italic=True) + 2 * EDGE_LABEL_PAD_X, EDGE_LABEL_H


def _rects_overlap(a, b, m=0.0):
    return not (a[2] + m <= b[0] or b[2] + m <= a[0] or a[3] + m <= b[1] or b[3] + m <= a[1])


def _seg_hits_rect(p, q, r, m=0.0):
    x0, x1 = min(p[0], q[0]), max(p[0], q[0])
    y0, y1 = min(p[1], q[1]), max(p[1], q[1])
    return not (x1 < r[0] - m or x0 > r[2] + m or y1 < r[1] - m or y0 > r[3] + m)


def label_collisions(i, box, edges, boxes, panels, badges=(), titles=(), others=()):
    """Why a label pill `box` (x0, y0, x1, y1) on edge i would collide: boxes, other connectors, container borders
    (panels: {name: rect}), badges, stage titles, other labels (others: [(edge index, rect)]). Empty when clear."""
    why = []
    for b in boxes.values():
        if _rects_overlap(box, (b.x0, b.y0, b.x1, b.y1), LABEL_CLEAR):
            why.append(f"box {b.id}")
    for j, ed in enumerate(edges):
        if j == i:
            continue
        for p, q in zip(ed["pts"], ed["pts"][1:]):
            if _seg_hits_rect(p, q, box, LABEL_CLEAR + E.EDGE_W / 2):
                why.append(f"connector {ed['a']}->{ed['b']}")
                break
    m = LABEL_BORDER_CLEAR
    for name, (cx0, cy0, cx1, cy1) in panels.items():
        inside = box[0] >= cx0 + m and box[2] <= cx1 - m and box[1] >= cy0 + m and box[3] <= cy1 - m
        outside = not _rects_overlap(box, (cx0, cy0, cx1, cy1), m)
        if not (inside or outside):
            why.append(f"the border of {name}")
    for bd in badges:
        if _rect_dist(bd["cx"], bd["cy"], *box) < bd["r"] + LABEL_CLEAR:
            why.append("a badge")
    for t in titles:
        if _rects_overlap(box, t, LABEL_CLEAR):
            why.append("a stage title")
    for j, r in others:
        if j != i and _rects_overlap(box, r, LABEL_CLEAR):
            why.append(f"the label of connector {j}")
    return why


def place_edge_labels(edges, boxes, panels, badges=(), titles=()):
    """Place every edge's label (see above). Returns ({edge index: label}, problems); a label is
    dict(text, x, y, w, h, seg, vertical) with (x, y) its centre."""
    placed, out, probs = [], {}, []
    for i, ed in enumerate(edges):
        text = ed.get("label")
        if not text:
            continue
        w, h = edge_label_size(text)
        segs = sorted(range(len(ed["pts"]) - 1), key=lambda k: -(abs(ed["pts"][k + 1][0] - ed["pts"][k][0])
                                                                  + abs(ed["pts"][k + 1][1] - ed["pts"][k][1])))
        found = None
        for k in segs:
            (px, py), (qx, qy) = ed["pts"][k], ed["pts"][k + 1]
            vertical = abs(px - qx) < 0.01
            lo, hi = (min(py, qy), max(py, qy)) if vertical else (min(px, qx), max(px, qx))
            half = (h if vertical else w) / 2 + LABEL_END_CLEAR
            if k == len(ed["pts"]) - 2:          # the last segment ends in the arrowhead or tee
                if (qy if vertical else qx) > (py if vertical else px):
                    hi -= E.ARROW_L + E.ARROW_GAP
                else:
                    lo += E.ARROW_L + E.ARROW_GAP
            if hi - lo < 2 * half:
                continue
            mid = (lo + hi) / 2
            ok = []
            t = lo + half
            while t <= hi - half + 1e-6:
                cx, cy = (px, t) if vertical else (t, py)
                r = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                if not label_collisions(i, r, edges, boxes, panels, badges, titles, placed):
                    ok.append(t)
                t += 1.0
            if not ok:
                continue
            runs, cur = [], [ok[0]]
            for t in ok[1:]:
                if t - cur[-1] > 1.01:
                    runs.append(cur)
                    cur = [t]
                else:
                    cur.append(t)
            runs.append(cur)
            best = min(runs, key=lambda r_: 0 if r_[0] <= mid <= r_[-1] else min(abs(r_[0] - mid), abs(r_[-1] - mid)))
            # in a short clear run (a gutter between panels) the pill takes the run's middle; else the point nearest mid
            t = (best[0] + best[-1]) / 2 if best[-1] - best[0] <= 3 * h else min(max(mid, best[0]), best[-1])
            cx, cy = (px, t) if vertical else (t, py)
            found = dict(text=text, x=cx, y=cy, w=w, h=h, seg=k, vertical=vertical)
            break
        if found is None:
            probs.append(f"label {text!r} of {ed['a']}->{ed['b']} has no clear place on its route")
            continue
        out[i] = found
        placed.append((i, (found["x"] - w / 2, found["y"] - h / 2, found["x"] + w / 2, found["y"] + h / 2)))
    return out, probs


def edge_label_svg(lb, T):
    """A label pill: canvas-coloured capsule, the verb in the configured sans (fonts.sans), italic and muted."""
    x, y, w, h = lb["x"], lb["y"], lb["w"], lb["h"]
    return [f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{h / 2:.1f}" fill="{T["canvas"]}"/>',
            f'<text x="{x:.1f}" y="{E.baseline(y, EDGE_LABEL_FS):.1f}" text-anchor="middle" font-family="{T["sans"]}" '
            f'font-size="{EDGE_LABEL_FS}" font-style="italic" font-weight="{E.WGHT[0]}" fill="{T["label"]}">{html.escape(lb["text"])}</text>']
