# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grid layout, routing and checks: a case's layout.py data -> geometry.json.

    python3 layout_engine.py <case_dir> [--config FILE] [--trust-case]   # prints problems/warnings/crossings,
                                                                          # writes geometry.json

The case's layout.py holds only data; this module holds the machinery:

Grid    A column is as wide as its widest box; boxes keep their own proportions (core.size_node).
        POS gives each node (col, row); a fractional col or row sits between two centres, and
        ((c0, c1), row) is a bar that spans columns c0..c1 and wraps its text to that width.
GROUPS  Column groups (first, last). In the rows GROUPS apply to (GROUPS_ABOVE_ROW), the inner columns
        of a group are evenly spaced between its outer columns, so every stage has equal steps
        between its columns; the outer columns, and so the containers, stay put.
Gutters Stage containers sit on one grid with equal gutters.
        A container's edges are its first and last column's (and row's) outer edges plus PAD (CL_TOP
        above, for the title), never its own boxes' extent, so containers in one column group share
        both edges. The extra space before the first column or row of a container (GUTTER_COLS,
        GUTTER_ROWS; derived from SPAN and KEY_SPAN when the case leaves them out) makes every gap
        between neighbouring containers exactly GUTTER: COL_EXTRA = 2 PAD + GUTTER - GAP_X and
        ROW_EXTRA = CL_TOP + PAD + GUTTER - ROW_GAP.
Pairs   RULED OUT (content.PAIRS): blocks of evidence -> diagnosis pairs, one block per evidence column,
        evenly spaced across the stage from its first to its last column edge; in each block the
        evidence is right-aligned and the diagnosis left-aligned on one gutter PAIR_GAP wide. The
        diagnosis of a pair sits in the column after its evidence.
Routing Orthogonal routes (core.auto_route); HINT forces a shape and ports per edge:
        shape V H LV LH ZV ZH; sx/tx = fractional port position on the side used;
        x/y: 'sxF' = source x0 + F*w, 'syF' = source y0 + F*h, 'tF' = target y0 + F*h;
        ch = channel of a Z: a number, "trunk" (one channel for every branch of a fan-out to one row),
        ("gutter", a, b) the middle of the gap between stages a and b,
        ("mid", a, b) the middle of the gap between boxes a and b.
Checks  see check_layout(): every PROBLEM, crossing or unrouted edge stops the build.
Glyphs  (decor.py) An abnormal lab value gets its ↑/↓ in the text before the box is measured,
        so wrapping and widths include it; a box with a value and a range grows by decor.BAR_H per range
        bar (Box.foot). After routing, every box gets its role glyph badge and its flag badges at the
        first corner slot clear of arrows (and so their ports), other boxes, other badges, its container's
        edge and the stage titles; a badge with no clear slot is a PROBLEM. With a time-course ribbon
        (case.json "stages" with "when"), Y0 grows by decor.TL_BAND unless layout.py sets Y0. geometry.json
        gains (never renames): nodes[].foot, .bars, .badges, .flags, .lab; "stages"; "timeline".
Inset   (decor.py) An illustration panel on the grid: layout.py INSET_SPAN (like KEY_SPAN) with
        case.json "inset" (image, title, labels anchored as fractions of the image, credit). The build recolours the
        image into each flavour first (decor.inset_prepare, cached in the case's assets/), so the panel knows the
        image's shape. The panel takes part in the gutter checks; no box or connector may enter it.
Rows    ROW_GROUPS [((c0, c1), (r0, r1))]: in columns c0..c1 the rows r0..r1 get equal gaps between
        the bottom of row r0 and the top of row r1 (both stay put), so a gutter row that another stage needs beside
        them (GUTTER_ROWS) spreads over every gap of these stages instead of opening one. A stage's number and glyph
        keep decor.HEADER_CLEAR from the badges below them: run() grows the header band of a row whose first boxes
        would bring a badge closer (every panel starting at that row, so the gutters stay equal) and builds again.
Labels  A connector's label (content.py e(..., label="migrates")) sits on a pill on its longest clear straight
        segment (decor.place_edge_labels), placed after the badges; check_layout re-checks every placed pill against
        boxes, connectors, container borders, badges, stage titles and other labels. geometry.json gains "inset",
        "credit" and edges[].label_at.
"""
import json, math, pathlib, sys

import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent
import core as E
import decor

# defaults a case's layout.py may override
DEFAULTS = dict(
    GAP_X=28,            # gap between the widest boxes of neighbouring columns
    X0=60, Y0=176,       # left edge of column 0 / top of the row 0 area (below the header)
    ROW_GAP=26,
    PAD=18, GUTTER=28,   # container padding round its columns and rows; the gap between containers
    PAIR_GAP=56,         # RULED OUT: the gutter between evidence and diagnosis in a block of pairs
    W_MAX={}, MIN_H={}, NUDGE_X={}, HINT={}, GROUPS=[],
    GROUPS_ABOVE_ROW=None,               # GROUPS apply to rows r < this (None: every row)
    GUTTER_COLS=None, GUTTER_ROWS=None,  # None: the first column / row of every SPAN and of KEY_SPAN, bar 0
    GLYPHS="corner",     # where a box's role glyph sits: "corner" (straddling its top-left corner) or "tab" (on its top edge)
    INSET_SPAN=None,     # the illustration panel's (first col, last col, first row, last row), with case.json "inset"
    ROW_GROUPS=[],       # [((first col, last col), (first row, last row)), ...]: in those columns, the rows between the
                         # first and the last are spaced with equal gaps (see build())
)
TEXT_WGHT = (500, 700)   # the configured sans (fonts.sans): Medium for text, Bold for key boxes


class LayoutError(Exception):
    """A layout that breaks the container grid (a box out of its padding, an unequal gutter)."""


class Params:
    """The case's layout data with the defaults filled in."""

    def __init__(self, case):
        L = case.layout
        for k, v in DEFAULTS.items():
            setattr(self, k, getattr(L, k, v))
        self.POS, self.SPAN, self.KEY_SPAN = L.POS, L.SPAN, L.KEY_SPAN
        spans = list(self.SPAN.values()) + [self.KEY_SPAN] + ([self.INSET_SPAN] if self.INSET_SPAN else [])
        cols = self.GUTTER_COLS if self.GUTTER_COLS is not None else sorted({s[0] for s in spans} - {0})
        rows = self.GUTTER_ROWS if self.GUTTER_ROWS is not None else sorted({s[2] for s in spans} - {0})
        self.COL_EXTRA = {c: 2 * self.PAD + self.GUTTER - self.GAP_X for c in cols}
        self.ROW_EXTRA = {r: E.CL_TOP + self.PAD + self.GUTTER - self.ROW_GAP for r in rows}
        self.HEADER_EXTRA = {}   # row -> px added to the header band of every panel starting at that row (run())
        self.NODES, self.EDGES, self.CLUSTERS = case.content.NODES, case.content.EDGES, case.content.CLUSTERS
        self.PAIRS = list(getattr(case.content, "PAIRS", []))
        self.case = case
        # the illustration panel (decor.py): case.json "inset" and layout.py INSET_SPAN go together
        try:
            self.INSET = decor.inset_spec(case)
        except ValueError as ex:
            raise LayoutError(str(ex))
        if bool(self.INSET) != bool(self.INSET_SPAN):
            raise LayoutError("case.json \"inset\" and layout.py INSET_SPAN go together: give both or neither")
        # glyph layers (decor.py): stage numbers and glyphs, the time-course ribbon, lab marks and range bars
        self.STAGES = decor.stage_meta(case)
        if decor.timeline_stages(self.STAGES) and not hasattr(L, "Y0"):
            self.Y0 += decor.TL_BAND          # the ribbon takes a band between the header and the stage grid
        self.TEXT, self.FOOT, self.BARS = {}, {}, {}
        for nid, nd in self.NODES.items():
            try:
                entries = decor.labs(nd)
                self.TEXT[nid] = decor.mark_text(nid, nd["text"], entries)
                decor.flags_of(nd)
            except ValueError as ex:
                raise LayoutError(f"node {nid}: {ex}" if not str(ex).startswith("node") else str(ex))
            self.BARS[nid] = decor.bars_of(dict(nd, attrs=dict(nd.get("attrs") or {}, lab=entries)))
            self.FOOT[nid] = len(self.BARS[nid]) * decor.BAR_H


def build(P):
    boxes = {}
    POS, NODES = P.POS, P.NODES
    # sizes first: each box takes its own proportions; the bars wrap to the columns they span
    for nid, nd in NODES.items():
        if not isinstance(POS[nid][0], tuple):
            lines, w, h = E.size_node(dict(nd, text=P.TEXT[nid]), w_max=P.W_MAX.get(nid))
            h = max(h + P.FOOT[nid], P.MIN_H.get(nid, 0))
            if len(lines) > 1 and w / h < E.ASPECT_FLOOR:      # range bars add height: widen to the floor's aspect
                w = E.W_STEP * math.ceil(h * E.ASPECT_FLOOR / E.W_STEP)
            boxes[nid] = E.Box(id=nid, w=w, h=h, role=nd["role"], lines=lines, bold=nd["bold"], cluster=nd["cluster"],
                               text=nd["text"], foot=P.FOOT[nid])
    ncol = 1 + max(int(p[0][1] if isinstance(p[0], tuple) else math.ceil(p[0])) for p in POS.values())
    colw = [max([boxes[n].w for n in boxes if POS[n][0] == c] or [0]) for c in range(ncol)]
    cx, x = [], P.X0
    for c in range(ncol):
        x += P.COL_EXTRA.get(c, 0) if c else 0
        cx.append(x + colw[c] / 2)
        x += colw[c] + P.GAP_X
    # in the GROUPS rows the columns of each group are evenly spaced between the group's first and last column
    cx_top = list(cx)
    for g0, g1 in P.GROUPS:
        if not 0 <= g0 <= g1 < ncol:
            raise LayoutError(f"GROUPS ({g0}, {g1}): a group runs from a column to the same or a later one, within columns "
                              f"0..{ncol - 1} (the last column a box sits in)")
        for c in range(g0 + 1, g1):
            cx_top[c] = cx[g0] + (c - g0) * (cx[g1] - cx[g0]) / (g1 - g0)

    def at(c, row):
        xs = cx_top if P.GROUPS_ABOVE_ROW is None or row < P.GROUPS_ABOVE_ROW else cx
        return xs[int(c)] if c == int(c) else xs[int(c)] + (c - int(c)) * (xs[int(c) + 1] - xs[int(c)])
    for nid, nd in NODES.items():
        p = POS[nid]
        if isinstance(p[0], tuple):
            c0, c1 = p[0]
            x0, x1 = cx[c0] - colw[c0] / 2, cx[c1] + colw[c1] / 2
            lines, w, h = E.size_node(dict(nd, text=P.TEXT[nid]), x1 - x0)
            boxes[nid] = E.Box(id=nid, w=w, h=h + P.FOOT[nid], role=nd["role"], lines=lines, bold=nd["bold"],
                               cluster=nd["cluster"], text=nd["text"], foot=P.FOOT[nid])
            boxes[nid].x = (x0 + x1) / 2
        else:
            boxes[nid].x = at(p[0], p[1]) + P.NUDGE_X.get(nid, 0)
    # RULED OUT: blocks of pairs, evenly spaced across the stage from its first to its last column edge
    # in each block the evidence is right-aligned and the diagnosis left-aligned on one gutter
    if P.PAIRS:
        PAIR_GAP = P.PAIR_GAP
        pcols = sorted({POS[ev][0] for ev, *_ in P.PAIRS})
        blocks = []
        for c in pcols:
            col = [(boxes[ev], boxes[dx]) for ev, _, _, dx, _ in P.PAIRS if POS[ev][0] == c]
            blocks.append((col, max(e.w for e, _ in col), max(x.w for _, x in col)))
        left, right = cx[pcols[0]] - colw[pcols[0]] / 2, cx[pcols[-1] + 1] + colw[pcols[-1] + 1] / 2
        gap = (right - left - sum(we + PAIR_GAP + wx for _, we, wx in blocks)) / (len(blocks) - 1) if len(blocks) > 1 else 0
        for col, we, wx in blocks:
            gutter = left + we + PAIR_GAP / 2
            for e, x in col:
                e.x, x.x = gutter - PAIR_GAP / 2 - e.w / 2, gutter + PAIR_GAP / 2 + x.w / 2
            left += we + PAIR_GAP + wx + gap
    rows = sorted({POS[n][1] for n in NODES if POS[n][1] == int(POS[n][1])})
    rh = {r: max(boxes[n].h for n in NODES if POS[n][1] == r) for r in rows}
    ry, y = {}, P.Y0
    for r in range(0, max(rows) + 1):
        y += P.ROW_EXTRA.get(r, 0)
        h = rh.get(r, 0)
        ry[r] = y + h / 2
        y += h + P.ROW_GAP
    # ROW_GROUPS: in a group's columns, the rows from its first to its last are spaced with equal gaps between
    # the first row's bottom and the last row's top (both stay, so no container moves). It spreads a gutter row's
    # extra space (another stage starting beside them) over every gap instead of leaving it in one.
    groups = []
    for (gc0, gc1), (gr0, gr1) in P.ROW_GROUPS:
        incol = lambda n_, gc0=gc0, gc1=gc1: (not isinstance(POS[n_][0], tuple)) and gc0 <= POS[n_][0] <= gc1
        spans = dict(P.SPAN, key=P.KEY_SPAN, **({"inset": P.INSET_SPAN} if P.INSET_SPAN else {}))
        for cid, (c0, c1, r0, r1) in spans.items():
            if c1 >= gc0 and c0 <= gc1 and (gr0 < r0 <= gr1 or gr0 <= r1 < gr1):
                raise LayoutError(f"ROW_GROUPS rows {gr0}-{gr1}: stage {cid} starts or ends inside them; its edge would move")
        grows = sorted({POS[n_][1] for n_ in NODES if incol(n_) and gr0 <= POS[n_][1] <= gr1 and POS[n_][1] == int(POS[n_][1])})
        if len(grows) < 3:
            continue
        gh = {r: max(boxes[n_].h for n_ in NODES if incol(n_) and POS[n_][1] == r) for r in grows}
        top, bot = ry[grows[0]] + gh[grows[0]] / 2, ry[grows[-1]] - gh[grows[-1]] / 2
        gap = (bot - top - sum(gh[r] for r in grows[1:-1])) / (len(grows) - 1)
        gy, yy = dict(ry), top + gap
        for r in grows[1:-1]:
            gy[r] = yy + gh[r] / 2
            yy += gh[r] + gap
        groups.append((incol, gr0, gr1, gy))

    def y_at(nid):
        r = POS[nid][1]
        ys = ry
        for incol, gr0, gr1, gy in groups:
            if incol(nid) and gr0 <= r <= gr1:
                ys = gy
        return ys[r] if r == int(r) else ys[int(r)] + (r - int(r)) * (ys[int(r) + 1] - ys[int(r)])
    for nid in NODES:
        boxes[nid].y = y_at(nid)
    grid = dict(left=[cx[c] - colw[c] / 2 for c in range(ncol)], right=[cx[c] + colw[c] / 2 for c in range(ncol)],
                top={r: ry[r] - rh.get(r, 0) / 2 for r in ry}, bottom={r: ry[r] + rh.get(r, 0) / 2 for r in ry})
    return boxes, grid


def span_rect(P, grid, c0, c1, r0, r1):
    if not (0 <= c0 <= c1 < len(grid["left"]) and r0 in grid["top"] and r1 in grid["bottom"] and r0 <= r1):
        raise LayoutError(f"span ({c0}, {c1}, {r0}, {r1}) is outside the grid: columns 0..{len(grid['left']) - 1} and "
                          f"rows 0..{max(grid['top'])} hold boxes; a panel's columns and rows need a box somewhere in them")
    return [grid["left"][c0] - P.PAD, grid["top"][r0] - E.CL_TOP - P.HEADER_EXTRA.get(r0, 0), grid["right"][c1] + P.PAD,
            grid["bottom"][r1] + P.PAD]


def clusters(P, boxes, grid):
    """Stage containers on the column/row grid (see SPAN). Fails if a box leaves its container's
    padding or two neighbouring containers are not exactly one GUTTER apart."""
    if set(P.SPAN) != set(P.CLUSTERS):
        raise LayoutError("SPAN and CLUSTERS name different stages")
    PAD, GUTTER = P.PAD, P.GUTTER
    rects = {cid: span_rect(P, grid, *P.SPAN[cid]) for cid in P.CLUSTERS}
    for cid, (x0, y0, x1, y1) in rects.items():
        for b in boxes.values():
            if b.cluster == cid:
                if not (x0 + PAD - 0.5 <= b.x0 and b.x1 <= x1 - PAD + 0.5 and y0 + E.CL_TOP - 0.5 <= b.y0
                        and b.y1 <= y1 - PAD + 0.5):
                    raise LayoutError(f"{b.id} leaves the padding of {cid}")
    allr = panels(P, grid, rects)
    for a, (ax0, ay0, ax1, ay1) in allr.items():
        for b, (bx0, by0, bx1, by1) in allr.items():
            if bx0 > ax1 and not (by1 <= ay0 or by0 >= ay1):
                if not (any(ox0 > ax1 and ox1 < bx0 and not (oy1 <= ay0 or oy0 >= ay1)
                            for ox0, oy0, ox1, oy1 in allr.values()) or abs(bx0 - ax1 - GUTTER) < 0.5):
                    raise LayoutError(f"gap {a}|{b} is {bx0 - ax1:.1f}, not {GUTTER}")
            if by0 > ay1 and not (bx1 <= ax0 or bx0 >= ax1):
                if not (any(oy0 > ay1 and oy1 < by0 and not (ox1 <= ax0 or ox0 >= ax1)
                            for ox0, oy0, ox1, oy1 in allr.values()) or abs(by0 - ay1 - GUTTER) < 0.5):
                    raise LayoutError(f"gap {a}/{b} is {by0 - ay1:.1f}, not {GUTTER}")
    return rects


def panels(P, grid, rects):
    """Every container on the grid: the stages, the key and the inset (when the case has one)."""
    out = dict(rects, key=span_rect(P, grid, *P.KEY_SPAN))
    if P.INSET_SPAN:
        out["inset"] = span_rect(P, grid, *P.INSET_SPAN)
    return out


def route_all(P, boxes, rects):
    out = []
    for ed in P.EDGES:
        S, D = boxes[ed["a"]], boxes[ed["b"]]
        spec = dict(P.HINT.get((ed["a"], ed["b"]), {}))
        for k in ("x", "y"):
            v = spec.get(k)
            if isinstance(v, str):
                if v.startswith("sx"):
                    spec[k] = S.x0 + float(v[2:]) * S.w
                elif v.startswith("sy"):
                    spec[k] = S.y0 + float(v[2:]) * S.h
                elif v.startswith("t"):
                    spec[k] = D.y0 + float(v[1:]) * D.h
            elif v is None:
                spec.pop(k, None)
        if isinstance(spec.get("ch"), tuple) and spec["ch"][0] == "gutter":   # the middle of the gap between two stages
            spec["ch"] = (rects[spec["ch"][1]][2] + rects[spec["ch"][2]][0]) / 2
        elif isinstance(spec.get("ch"), tuple) and spec["ch"][0] == "mid":    # the middle of the gap between two boxes
            spec["ch"] = (boxes[spec["ch"][1]].x1 + boxes[spec["ch"][2]].x0) / 2
        if spec.get("ch") == "trunk":   # one channel for every branch of a fan-out: a single trunk, then branches
            kids = [boxes[x["b"]] for x in P.EDGES if x["a"] == ed["a"] and P.POS[x["b"]][1] == P.POS[ed["b"]][1]]
            spec["ch"] = (S.y1 + min(k.y0 for k in kids)) / 2
        good, allc = E.auto_route(S, D, spec, list(boxes.values()))
        if good:
            pts = min(good, key=lambda p: (len(p), E.route_len(p)))
            ok = True
        else:
            pts = allc[0] if allc else [(S.x, S.y), (D.x, D.y)]
            ok = False
        out.append(dict(a=ed["a"], b=ed["b"], kind=ed["kind"], label=ed.get("label"), pts=[list(p) for p in pts], ok=ok))
    return out


def check_layout(P, boxes, grid, rects, edges, labels=None, badges=(), titles=()):
    """core.check (overlaps, boxes out of or into containers, diagonal segments, edges through boxes,
    unrelated edges running together, false continuations, crossings) plus the grid's own rules; with
    labels, every placed edge label against boxes, connectors, borders, badges, titles and other labels."""
    probs, warns, crossings = E.check(boxes, edges, rects)
    allp = panels(P, grid, rects)
    # the key and the inset hold no boxes and no connector crosses them
    for name in ("key", "inset"):
        if name not in allp:
            continue
        r = allp[name]
        for b in boxes.values():
            if decor._rects_overlap((b.x0, b.y0, b.x1, b.y1), r, 0):
                probs.append(f"{b.id} intrudes into the {name}")
        for ed in edges:
            if any(decor._seg_hits_rect(p, q, r) for p, q in zip(ed["pts"], ed["pts"][1:])):
                probs.append(f"{ed['a']}->{ed['b']} crosses the {name}")
    # no line may run along a container's border (it would read as part of the outline)
    for ed in edges:
        for (x0, y0), (x1, y1) in zip(ed["pts"], ed["pts"][1:]):
            for cid, (cx0, cy0, cx1, cy1) in allp.items():
                if x0 == x1 and min(abs(x0 - cx0), abs(x0 - cx1)) < 8 and min(y0, y1) < cy1 and max(y0, y1) > cy0:
                    probs.append(f"{ed['a']}->{ed['b']} runs along the side of {cid}")
                if y0 == y1 and min(abs(y0 - cy0), abs(y0 - cy1)) < 8 and min(x0, x1) < cx1 and max(x0, x1) > cx0:
                    probs.append(f"{ed['a']}->{ed['b']} runs along the top or bottom of {cid}")
    # neighbours in a row keep at least GAP_X - 4 between them after the even spacing
    for r in {P.POS[n][1] for n in P.NODES}:
        row = sorted((b for n, b in boxes.items() if P.POS[n][1] == r and not isinstance(P.POS[n][0], tuple)),
                     key=lambda b: b.x)
        for a, b in zip(row, row[1:]):
            if a.cluster == b.cluster and b.x0 - a.x1 < P.GAP_X - 4:
                probs.append(f"{a.id} and {b.id} are {b.x0 - a.x1:.0f} apart")
    for b in boxes.values():
        mw = max(E.text_w(l, b.bold) for l in b.lines)
        if mw > b.w - 2 * E.PAD_X:
            probs.append(f"text too wide in {b.id}: {mw:.0f} > {b.w - 2 * E.PAD_X}")
    # edge labels: each placed pill is clear of boxes, other connectors, container borders, badges, stage
    # titles and the other labels (the same rules decor.place_edge_labels places them by, checked again here)
    if labels:
        rects_l = [(i, (lb["x"] - lb["w"] / 2, lb["y"] - lb["h"] / 2, lb["x"] + lb["w"] / 2, lb["y"] + lb["h"] / 2))
                   for i, lb in labels.items()]
        for i, r in rects_l:
            ed = edges[i]
            if not any(decor._seg_hits_rect(p, q, (labels[i]["x"], labels[i]["y"], labels[i]["x"], labels[i]["y"]), 0.6)
                       for p, q in zip(ed["pts"], ed["pts"][1:])):
                probs.append(f"label of {ed['a']}->{ed['b']} is off its connector")
            why = decor.label_collisions(i, r, edges, boxes, allp, badges, titles, rects_l)
            if why:
                probs.append(f"label of {ed['a']}->{ed['b']} collides with {', '.join(sorted(set(why)))}")
    return probs, warns, crossings


def header_room(P, boxes, rects):
    """{row: px}: how much more header band each first row of a panel needs so that no box's first-choice badge
    comes within decor.HEADER_CLEAR of its stage's number or glyph (decor.header_room)."""
    marks = decor.header_marks(rects, P.STAGES)
    show_roles = decor.roles_on(P.case)
    need = {}
    for b in boxes.values():
        if b.cluster not in marks:
            continue
        nd = P.NODES[b.id]
        px = decor.header_room(b, marks[b.cluster], role=show_roles and bool(decor.role_icon(P.case, b.role)),
                               flags=bool(decor.flags_of(nd)), placement=P.GLYPHS)
        if px > 0:
            r0 = P.SPAN[b.cluster][2]
            need[r0] = max(need.get(r0, 0), math.ceil(px))
    return need


def run(case, quiet=False):
    """Lay the case out, check it and write geometry.json in the configured sans (fonts.sans).
    Returns dict(unrouted, problems, warnings, crossings, geometry=path). Raises LayoutError."""
    geometry = "geometry.json"
    try:
        E.use_configured_fonts(TEXT_WGHT)
    except Exception as ex:  # noqa: BLE001  (a font that cannot load stops the layout with its reason)
        raise LayoutError(f"fonts: {ex}")
    P = Params(case)
    try:     # the inset's images first: the panel needs the image's shape (cached; decor.inset_prepare)
        prep = decor.inset_prepare(case) if P.INSET else None
    except ValueError as ex:
        raise LayoutError(str(ex))
    boxes, grid = build(P)
    rects = clusters(P, boxes, grid)
    # a stage's number and glyph keep decor.HEADER_CLEAR from the badges of the boxes below them: a row whose first
    # boxes would bring a badge closer gets that much more header band (every panel starting at the row; the gutters
    # stay equal), and the grid is built again
    room = header_room(P, boxes, rects)
    if room:
        for r, px in room.items():
            P.HEADER_EXTRA[r] = px
            P.ROW_EXTRA[r] = P.ROW_EXTRA.get(r, 0) + px
        boxes, grid = build(P)
        rects = clusters(P, boxes, grid)
        if not quiet:
            print("header band grown for the stage marks:", ", ".join(f"row {r} +{px} px" for r, px in sorted(room.items())))
    edges = route_all(P, boxes, rects)
    bad = [f"{e['a']}->{e['b']}" for e in edges if not e["ok"]]
    # glyph layers: badges on the boxes (placed clear of arrows, ports, boxes and titles: a collision is a PROBLEM),
    # range bars, the ribbon
    titles = decor.title_boxes(rects, P.STAGES, P.CLUSTERS)
    marks = decor.header_marks(rects, P.STAGES)
    badges, bprobs = decor.place_badges(case, boxes, edges, rects, titles, P.GLYPHS, marks.values())
    allbadges = [bd for bl in badges.values() for bd in bl]
    # edge labels, after the badges; then every check, the labels included
    elabels, lprobs = decor.place_edge_labels(edges, boxes, panels(P, grid, rects), allbadges, titles)
    probs, warns, crossings = check_layout(P, boxes, grid, rects, edges, elabels, allbadges, titles)
    probs += bprobs + lprobs
    inset = None
    if P.INSET:
        rect = span_rect(P, grid, *P.INSET_SPAN)
        inset, iprobs = decor.inset_geom(P.INSET, rect, prep, case.dir, decor.inset_title_x(P.INSET))
        probs += iprobs
    bars = {nid: decor.bar_geom(boxes[nid], P.BARS[nid]) for nid in boxes if P.BARS[nid]}
    timeline = decor.timeline_geom(case, P.STAGES, rects)
    if not quiet:
        print("UNROUTED:", bad)
        print(f"PROBLEMS ({len(probs)}):"); [print("  ", p) for p in probs]
        print(f"WARNINGS ({len(warns)}):"); [print("  ", w) for w in warns]
        print(f"CROSSINGS ({len(crossings)}):"); [print("  ", c) for c in crossings]
    geo = dict(nodes={k: dict(x=b.x, y=b.y, w=b.w, h=b.h, role=b.role, lines=b.lines, bold=b.bold, cluster=b.cluster)
                      for k, b in boxes.items()},
               clusters=rects, key=span_rect(P, grid, *P.KEY_SPAN), edges=edges)
    # glyph layers: per node "foot" (px of range bars under the text), "bars", "badges", "flags";
    # "stages" (number, icon, accent, time per stage) and "timeline" (the ribbon) at the top level
    for k, b in boxes.items():
        nd = geo["nodes"][k]
        nd["bar"] = isinstance(P.POS[k][0], tuple)     # a bar across columns: floor.py exempts it from the box aspect
        if b.foot:
            nd["foot"] = b.foot
            nd["bars"] = bars[k]
        if k in badges:
            nd["badges"] = badges[k]
        fl = decor.flags_of(P.NODES[k])
        if fl:
            nd["flags"] = fl
        lab = decor.labs(P.NODES[k])
        if lab:
            nd["lab"] = [{kk: v for kk, v in e.items() if kk != "at"} for e in lab]
    geo["stages"] = P.STAGES
    geo["timeline"] = timeline
    # the illustration panel, its credit line, and edge labels
    if inset:
        geo["inset"] = inset
        if P.INSET.get("credit"):
            geo["credit"] = P.INSET["credit"]
    for i, lb in elabels.items():
        geo["edges"][i]["label_at"] = lb
    geo.update(font=E.FONT, wght=list(E.WGHT), fs=E.FS, lh=E.LH)
    path = case.dir / geometry
    path.write_text(json.dumps(geo, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return dict(unrouted=bad, problems=probs, warnings=warns, crossings=crossings, geometry=path)


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    import argparse
    from case import Case, CaseError
    ap = argparse.ArgumentParser(description="Lay a case out and check it; writes geometry.json (no other outputs).")
    ap.add_argument("case_dir")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
        case = Case(a.case_dir, trust=a.trust_case)
        pre = case.preflight(need_meta=False)
        if pre:
            raise CaseError("\n  - ".join(["case cannot be laid out yet:"] + pre))
        run(case)
    except (CaseError, LayoutError, style.StyleError) as ex:
        raise SystemExit(str(ex))


if __name__ == "__main__":
    main(sys.argv[1:])
