#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Draft a whole layout.py from a case's content.py alone: stage containers, box cells, the key (and the
inset panel, when case.json has one), the DIAGNOSIS bars, RULED OUT pairs, GROUPS, W_MAX and routing HINTs.

    python3 autoplace.py <case_dir>                          # writes <case_dir>/layout.draft.py
    python3 autoplace.py <case_dir> --out layout.v2.py       # another name; never an existing layout.py or
                                                             #   layout_<variant>.py (preserve the case's existing layout)
    python3 autoplace.py <case_dir> --variant core           # the boxes variants.py keeps -> layout_core.draft.py
    python3 autoplace.py <case_dir> --effort 2 --seed 3      # more search; another random start

The draft is a starting point: copy it to layout.py (or layout_<variant>.py), build, then adjust by hand. The
draft's docstring carries the engine's own verdict (problems, crossings, unrouted edges), anything left to fix
by hand, and the canvas size. Exit status 0 when the draft builds clean, 1 otherwise. Typical run: 15 s for a
small case, 2-5 minutes for a large case, using up to 4 worker processes.

How it places:

1. Measure every box as the engine will (core.size_node, the configured sans (fonts.sans) 500/700 at 14 px); a box whose text
   cannot fit 220 px in six lines gets a W_MAX. Stages are sorted into the main flow (stages joined by
   arrows, in story order: a topological order of the stage graph, ties broken by CLUSTERS order), asides
   (stages with no arrow to the main flow: background, context ...), the DIAGNOSIS bar stage (only dx boxes,
   only incoming arrows) and RULED OUT (content.PAIRS).
2. Shapes: every stage is annealed on its own on grids of C x R cells (opt.py's cell model, with the real
   router's shapes: straight, L, and Z through the three channels core.auto_route tries). The cost is route
   length, bends, causal arrows that run left or up, crossings, lines along each other, arrows into one box
   along one line, boxes in a route's way, a story prior (what a chain leads to sits right of or below its
   cause), and for boxes that feed a DIAGNOSIS bar a clear column below them, in bar order. The result is a
   table: the stage's cost in a slot of c columns x r rows.
3. Arrangement: the main stages, the key (and the inset) tile a grid of column groups x bands (a stage may
   span groups or bands; every band holds a stage), found by beam search: arrows between stages run right or
   down between neighbours, stages keep story order, the bars' feeders sit in the lowest band, the key sits
   top right, asides stay below unless they save real room. Each candidate is sized from the tables (columns
   per group by enumeration, rows per band by descent); the cost adds the page's on-screen size
   max(W, 1.4 H) (smaller is larger text when viewed fit-to-screen), with W <= the page width limit (diagram.page.max_w).
   Below the main bands: one row of DIAGNOSIS bars across the page, then a band of asides and RULED OUT.
4. Slots: each stage is annealed again inside its final slot, in flow order, with arrows to stages already
   placed as fixed endpoints outside its grid and their boxes as obstacles.
5. Real geometry, in a scratch copy of the case (the case folder is never written to): the draft is built
   with layout_engine (Params, build, clusters); columns too wide for the page lose a little width (W_MAX,
   never squarer than 2:1); edges in trouble get HINTs from a local search over shapes, ports and channels
   (layout.py's own vocabulary; relative forms first, a numeric channel only as a last resort). The verdict
   is layout_engine.run() itself, as build.py calls it, plus checks of autoplace's own: no line through a
   stage title, the key or the inset; no arrow and tee ending on one line; the key fits its panel.
6. Clean-up while problems remain: the leading arrangement gets up to three annealing seeds; a stage in
   trouble is annealed again from fresh seeds (keeping what does best in real geometry); two neighbours that
   even spacing presses together get one more line, or their column group natural spacing; then boxes in
   trouble are moved to free cells of their stage, or swapped, while that lowers the count.

Set AUTOPLACE_DEBUG=1 to print each candidate's problems.
"""
import argparse, copy, functools, json, math, os, pathlib, random, re, shutil, sys, tempfile, time, types
from collections import Counter, defaultdict

import numpy as np

sys.dont_write_bytecode = True
import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)
from _legacy import read_legacy_text

ENGINE = pathlib.Path(__file__).resolve().parent
import core as E
import layout_engine as LE
try:                     # the glyph layers (stage numbers, badges, the inset), in engines that have them
    import decor
except ImportError:
    decor = None

PAGE_W_MAX = 4000          # the page width limit: diagram.page.max_w (set by autoplace())
SCREEN = 1.4               # on-screen size = max(W, SCREEN * H): the page is viewed fit-to-screen
HW, HH = 0.42, 0.34        # half a box in cells (opt.py's model: a cell is 1 x 1)
EPS = 1e-6
D = LE.DEFAULTS            # GAP_X 28, X0/Y0 60/176, ROW_GAP 26, PAD 18, GUTTER 28
COL_EXTRA = 2 * D["PAD"] + D["GUTTER"] - D["GAP_X"]
ROW_EXTRA = E.CL_TOP + D["PAD"] + D["GUTTER"] - D["ROW_GAP"]


def log(*a, quiet=False):
    if not quiet:
        print(*a, flush=True)


# =========================================================================== the case, built in a scratch copy
def scratch_case(case):
    """A shallow copy of the Case whose folder is a scratch copy of the case's inputs (content, case.json,
    assets ...), so layout_engine.run() can write its geometry.json without touching the case."""
    from case import scratch_copy
    d = pathlib.Path(tempfile.mkdtemp(prefix="autoplace-", dir=os.environ.get("TMPDIR")))
    scratch_copy(case.dir, d / "case")
    base = copy.copy(case)
    base.dir = d / "case"
    base._scratch = d
    return base


def with_layout(base, data):
    """The scratch case with this draft as its layout.py."""
    fake = copy.copy(base)
    fake.layout = types.SimpleNamespace(**data)
    return fake


def all_panels(P, grid, rects):
    """Every container: the stages, the key and (in engines that have one) the inset."""
    if hasattr(LE, "panels"):
        return LE.panels(P, grid, rects)
    return dict(rects, key=LE.span_rect(P, grid, *P.KEY_SPAN))


def title_rects(P, rects, panels, content):
    """The ink box of every container's title: decor's own measure where the engine numbers its stages."""
    if decor is not None and hasattr(P, "STAGES") and hasattr(decor, "title_boxes"):
        out = list(decor.title_boxes(rects, P.STAGES, P.CLUSTERS))
    else:
        out = [(x0 + 10, y0 + 6, x0 + 18 + mono_w(content.CLUSTERS[cid]), y0 + 32) for cid, (x0, y0, x1, y1) in rects.items()]
    if "key" in panels:
        x0, y0 = panels["key"][:2]
        out.append((x0 + 10, y0 + 6, x0 + 18 + mono_w("KEY"), y0 + 32))
    return out


def title_px(case, cid):
    """How far a stage's title reaches from its container's left edge."""
    t = case.content.CLUSTERS[cid]
    w = max(mono_w(t), E.text_w(t, size=E.CL_FS) * 1.1)
    if decor is not None and hasattr(decor, "stage_meta") and hasattr(decor, "title_x"):
        try:
            return decor.title_x(decor.stage_meta(case).get(cid, {})) + w
        except Exception:
            pass
    return 14 + w


# =========================================================================== measuring
class Measured:
    """Sets the engine's faces (as layout_engine.run does) and memoises text measurement while inside:
    every build in the search measures the same strings."""

    def __enter__(self):
        E.use_configured_fonts(LE.TEXT_WGHT)
        self._tw, self._sn = E.text_w, E.size_node
        E.text_w = functools.lru_cache(maxsize=None)(self._tw)
        cache, orig = {}, self._sn

        def size_node(nd, w=None, w_max=None):
            k = (nd["text"], nd["bold"], w, w_max)
            if k not in cache:
                cache[k] = orig(nd, w, w_max)
            lines, ww, hh = cache[k]
            return list(lines), ww, hh
        E.size_node = size_node
        return self

    def __exit__(self, *exc):
        E.text_w, E.size_node = self._tw, self._sn


def mono_w(text, size=None):
    """Width of a stage title in the configured mono (fonts.mono), bold (as render.py sets it)."""
    import style
    f = _mono_font(size or E.CL_FS, str(style.font_path("mono_bold", 700)))
    return f.getlength(text)


@functools.lru_cache(None)
def _mono_font(size, path):
    from PIL import ImageFont
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_axes([700 if a.get("name", b"") in (b"Weight", "Weight") else a["default"]
                                 for a in f.get_variation_axes()])
    except (OSError, AttributeError, KeyError):
        pass        # a static bold file
    return f


def measure(nodes, bars):
    """(w, h) per box and the W_MAX each box needs (a box whose text cannot fit 220 px in six lines)."""
    size, wmax = {}, {}
    for nid, nd in nodes.items():
        if nid in bars:
            continue
        wm = None
        while True:
            try:
                _, w, h = E.size_node(nd, w_max=wm)
                break
            except TypeError:          # no line count fits: allow a wider box
                wm = (wm or E.W_MAX) + 20
                if wm > 600:
                    raise SystemExit(f"autoplace: box {nid} is too long to lay out; shorten its text")
        size[nid] = (w, h)
        if wm:
            wmax[nid] = wm
    return size, wmax


def _used_guess(case):
    """Which glyph layers the finished diagram will draw (render.used_layers), guessed from the content."""
    used = dict(stages=False, timeline=False, flags=set(), marks=False, bars=False)
    if decor is None:
        return used
    try:
        used["stages"] = any(s.get("n") is not None for s in decor.stage_meta(case).values())
        for nd in case.content.NODES.values():
            used["flags"] |= set(decor.flags_of(nd))
            labs = decor.labs(nd)
            used["marks"] = used["marks"] or any(e.get("dir") for e in labs)
            used["bars"] = used["bars"] or bool(decor.bars_of(nd))
    except Exception:
        pass
    return used


def key_fits(case, w, h, used=None):
    """Does the key fit a panel of w x h px? (the engine's own key layout where it has one)"""
    if decor is not None and hasattr(decor, "key_layout"):
        try:
            decor.key_layout(0, 0, w, h, case, used if used is not None else _used_guess(case))
            return True
        except ValueError:
            return False
    import render
    roles = case.key_roles(render.KEY_ROLES)
    arrows = case.key_arrows(render.KEY_ARROWS)
    col2 = 18 + 46 + max(E.text_w(case.role_name[r]) for r in roles) + 40
    room = w - 16 - (col2 + 66)
    for lab, _ in arrows:
        lines = [lab] if E.text_w(lab) <= room else E.balanced(lab, 2)[0]
        if max(E.text_w(x) for x in lines) > room:
            return False
    return (h - 66) / len(roles) >= 30


def key_need(case):
    """The key panel's minimum (width, height) in px: the narrowest and the shortest panel it fits, with a
    little room to spare (rows of short boxes make a band lower than the estimate)."""
    used = _used_guess(case)
    w = next((w for w in range(300, 1600, 10) if key_fits(case, w, 2000, used)), 1600)
    h = next((h for h in range(120, 1200, 10) if key_fits(case, w + 40, h, used)), 1200)
    # a calm key: rows about 36 px apart, not the tightest the layout allows
    if decor is not None and hasattr(decor, "key_items"):
        try:
            left, right = decor.key_items(case, used)
            h = max(h, 62 + 36 * max(len(left), len(right)))
        except Exception:
            pass
    else:
        import render
        roles, arrows = case.key_roles(render.KEY_ROLES), case.key_arrows(render.KEY_ARROWS)
        h = max(h, 66 + 36 * len(roles))
        col2 = 18 + 46 + max(E.text_w(case.role_name[r]) for r in roles) + 40
        w = max(w, col2 + 66 + max(E.text_w(lab) for lab, _ in arrows) + 16)      # arrow labels on one line
    return w + 10, h


def inset_need(case):
    """The inset panel's minimum (width, height) in px (case.json "inset", engines with decor.py): its image
    about 240 px tall at its own proportions, its labels on both sides (decor.inset_geom's arithmetic)."""
    spec = getattr(case, "meta", {}).get("inset")
    if not spec or decor is None:
        return None
    try:
        from PIL import Image
        with Image.open(case.dir / spec["image"]) as im:
            w, h = im.size
        crop = spec.get("crop")
        if crop:
            w, h = crop[2] - crop[0], crop[3] - crop[1]
        aspect = w / h
    except Exception:
        aspect = 1.0
    ih = 240
    fs = getattr(decor, "INSET_LABEL_FS", 13)
    lab = max((E.text_w(l.get("text", ""), bool(l.get("bold")), size=fs) for l in spec.get("labels", [])), default=0)
    side = getattr(decor, "INSET_LABEL_GAP", 34) + lab + 16 + 8
    pad = getattr(decor, "INSET_IMG_PAD", 14)
    # the image fills the panel's height, so the panel's width follows from its height
    need_w = lambda h: (h - 2 * pad) * aspect + 2 * side
    return need_w(ih + 2 * pad + E.CL_TOP), ih + 2 * pad + E.CL_TOP, need_w


# =========================================================================== analysis
class Analysis:
    """Stages sorted into main flow, asides, the DIAGNOSIS bar stage and RULED OUT; the stage graph."""

    def __init__(self, content):
        N, EDG = content.NODES, content.EDGES
        self.N, self.EDG = N, EDG
        self.titles = dict(content.CLUSTERS)
        self.stages = [s for s in content.CLUSTERS if any(nd["cluster"] == s for nd in N.values())]
        self.members = {s: [n for n in N if N[n]["cluster"] == s] for s in self.stages}
        self.pairs = list(getattr(content, "PAIRS", []))
        pair_ids = {p[0] for p in self.pairs} | {p[3] for p in self.pairs}
        st = lambda n: N[n]["cluster"]
        self.pair_stages = [s for s in self.stages if all(n in pair_ids for n in self.members[s])]
        self.bar_stages = [s for s in self.stages if s not in self.pair_stages
                           and all(N[n]["role"] == "dx" for n in self.members[s])
                           and not any(e["a"] in self.members[s] for e in EDG)
                           and any(e["b"] in self.members[s] for e in EDG)]
        self.bars = [n for s in self.bar_stages for n in self.members[s]]
        flow = [s for s in self.stages if s not in self.pair_stages and s not in self.bar_stages]
        self.sedge = Counter((st(e["a"]), st(e["b"])) for e in EDG if st(e["a"]) != st(e["b"]))
        # connected components over flow + bar stages
        adj = defaultdict(set)
        for (a, b) in self.sedge:
            adj[a].add(b)
            adj[b].add(a)
        comp, seen = [], set()
        for s in flow + self.bar_stages:
            if s in seen:
                continue
            stack, c = [s], set()
            while stack:
                x = stack.pop()
                if x in c:
                    continue
                c.add(x)
                stack += [y for y in adj[x] if y in flow or y in self.bar_stages]
            seen |= c
            comp.append(c)
        with_bar = [c for c in comp if c & set(self.bar_stages)]
        if with_bar:
            mainset = set().union(*with_bar)
        else:
            mainset = max(comp, key=lambda c: sum(len(self.members[s]) for s in c)) if comp else set()
        self.main = [s for s in flow if s in mainset]
        self.asides = [s for s in flow if s not in mainset]
        self.order = self._topo(self.main)
        self.feeders = defaultdict(list)            # flow node -> bars it feeds
        for e in EDG:
            if e["b"] in self.bars and st(e["a"]) in flow:
                self.feeders[e["a"]].append(e["b"])
        self.stage_of = {n: st(n) for n in N}

    def _topo(self, stages):
        """Story order: stages joined by arrows in topological order, ties (and cycles) by CLUSTERS order."""
        idx = {s: i for i, s in enumerate(self.stages)}
        net = Counter()
        for (a, b), w in self.sedge.items():
            if a in stages and b in stages:
                net[(a, b)] += w
        preds = {s: {a for (a, b) in net if b == s and net[(a, b)] > net.get((b, a), 0)} for s in stages}
        out, left = [], list(stages)
        while left:
            ready = [s for s in left if not (preds[s] & set(left))] or [min(left, key=idx.get)]
            s = min(ready, key=idx.get)
            out.append(s)
            left.remove(s)
        return out

    def intra(self, s):
        mem = set(self.members[s])
        return [(e["a"], e["b"], e["kind"]) for e in self.EDG if e["a"] in mem and e["b"] in mem]

    def bar_order_pairs(self, s, bar_rank):
        """(a, b) for feeders in stage s: a feeds a bar left of the one b feeds."""
        f = [(n, min(bar_rank[x] for x in self.feeders[n])) for n in self.members[s] if n in self.feeders]
        return [(a, b) for a, ra in f for b, rb in f if ra < rb]


# =========================================================================== abstract grid placer
def cands(pa, pb):
    """The real router's shapes on the cell grid: V, H, LV, LH, ZV and ZH through the three channels
    core.auto_route tries (the midpoint and next to either end). Returns [(points, bends)]."""
    (c1, r1), (c2, r2) = pa, pb
    out = []
    if c1 == c2 and r1 != r2:
        s = 1 if r2 > r1 else -1
        out.append(([(c1, r1 + s * HH), (c1, r2 - s * HH)], 0))
    if r1 == r2 and c1 != c2:
        s = 1 if c2 > c1 else -1
        out.append(([(c1 + s * HW, r1), (c2 - s * HW, r1)], 0))
    if c1 != c2 and r1 != r2:
        sv = 1 if r2 > r1 else -1
        sh = 1 if c2 > c1 else -1
        out.append(([(c1, r1 + sv * HH), (c1, r2), (c2 - sh * HW, r2)], 1))
        out.append(([(c1 + sh * HW, r1), (c2, r1), (c2, r2 - sv * HH)], 1))
        for rm in sorted({r1 + sv * 0.5, r2 - sv * 0.5, (r1 + r2) / 2}):
            out.append(([(c1, r1 + sv * HH), (c1, rm), (c2, rm), (c2, r2 - sv * HH)], 2))
        for cm in sorted({c1 + sh * 0.5, c2 - sh * 0.5, (c1 + c2) / 2}):
            out.append(([(c1 + sh * HW, r1), (cm, r1), (cm, r2), (c2 - sh * HW, r2)], 2))
    return out


def seg_blocked(p, q, occ, skip, m=0.04):
    """Does the segment p-q pass through an occupied cell (other than those in skip)?"""
    (x0, y0), (x1, y1) = p, q
    if abs(y0 - y1) < EPS:
        r = round(y0)
        if abs(y0 - r) > HH + m:
            return False
        lo, hi = min(x0, x1), max(x0, x1)
        return any((c, r) in occ and (c, r) not in skip
                   for c in range(math.ceil(lo - HW - m), math.floor(hi + HW + m) + 1))
    c = round(x0)
    if abs(x0 - c) > HW + m:
        return False
    lo, hi = min(y0, y1), max(y0, y1)
    return any((c, r) in occ and (c, r) not in skip
               for r in range(math.ceil(lo - HH - m), math.floor(hi + HH + m) + 1))


def _segments(routes):
    H, V = [], []
    for i, pts in enumerate(routes):
        if pts is None:
            continue
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if abs(y0 - y1) < EPS:
                H.append((y0, min(x0, x1), max(x0, x1), i))
            else:
                V.append((x0, min(y0, y1), max(y0, y1), i))
    return np.array(H, float).reshape(-1, 4), np.array(V, float).reshape(-1, 4)


def conflicts(routes, rel_cross, rel_trunk, fan_in=None):
    """(crossings, unrelated overlaps, shared trunks, shared fan-ins) among routes on the cell grid."""
    h, v = _segments(routes)
    nc = 0
    if len(h) and len(v):
        hi, vi = h[:, 3].astype(int), v[:, 3].astype(int)
        cr = ((v[None, :, 0] > h[:, None, 1] + EPS) & (v[None, :, 0] < h[:, None, 2] - EPS)
              & (h[:, None, 0] > v[None, :, 1] + EPS) & (h[:, None, 0] < v[None, :, 2] - EPS))
        cr &= ~rel_cross[np.ix_(hi, vi)]
        nc = int(cr.sum())
    novl = nsh = nfi = 0
    for a in (h, v):
        if len(a) < 2:
            continue
        ids = a[:, 3].astype(int)
        same = np.abs(a[:, None, 0] - a[None, :, 0]) < 0.02
        ov = (np.minimum(a[:, None, 2], a[None, :, 2]) - np.maximum(a[:, None, 1], a[None, :, 1])) > 0.02
        m = np.triu(same & ov & (ids[:, None] != ids[None, :]), 1)
        rel = rel_trunk[np.ix_(ids, ids)]
        nsh += int((m & rel).sum())
        novl += int((m & ~rel).sum())
        if fan_in is not None:
            nfi += int((m & fan_in[np.ix_(ids, ids)]).sum())
    return nc, novl, nsh, nfi


class GridPlacer:
    """Simulated annealing of one stage's boxes on a C x R grid of cells (opt.py's model, extended).

    edges   (a, b, weight); a or b may be a virtual id (an endpoint in another stage: fixed, never blocks)
    drops   boxes that feed a DIAGNOSIS bar below: they need a clear column down to the bottom of the grid
    order   (a, b): a's column must be left of b's (the feeders of a left bar before those of a right bar)
    through local columns that a line from a stage above crosses top to bottom (keep them clear)
    mode    "shape": compact (a cost per used row and column); "slot": fill the given slot evenly
    """
    W = dict(len=1.0, wy=0.6, bend=0.6, left=2.0, up=1.4, cross=11.0, ovl=11.0, shared=0.4, fail=30.0,
             drop_block=30.0, drop_high=0.5, order=15.0, through=30.0, col=1.6, row=1.0, edge_empty=2.0,
             story=0.4, story_v=0.15, fan_in=3.0)

    def __init__(self, nodes, edges, C, R, virtual=None, drops=(), order=(), through=(), mode="shape",
                 weights=None, fixed=None):
        self.nodes = list(nodes)
        self.C, self.R = C, R
        self.virtual = dict(virtual or {})
        self.edges = [(e[0], e[1], e[2]) for e in edges]
        self.dirw = {(e[0], e[1]): (e[3] if len(e) > 3 else 1.0) for e in edges}
        self.drops = [d for d in drops if d in self.nodes]
        self.order = [(a, b) for a, b in order if a in self.nodes and b in self.nodes]
        self.through = sorted(set(c for c in through if 0 <= c < C))
        self.mode = mode
        self.fixed = dict(fixed or {})
        self.W = dict(GridPlacer.W)
        if weights:
            self.W.update(weights)
        ends = [(a, b) for a, b, _ in self.edges] + [(d, "__drop_" + d) for d in self.drops] \
            + [(f"__thr{i}", f"__thr{i}_") for i in range(len(self.through))]
        n = len(ends)
        self.rel_cross = np.zeros((n, n), bool)
        self.rel_trunk = np.zeros((n, n), bool)
        self.fan_in = np.zeros((n, n), bool)
        for i in range(n):
            for j in range(n):
                (ai, bi), (aj, bj) = ends[i], ends[j]
                self.rel_cross[i, j] = i == j or bool({ai, bi} & {aj, bj})
                self.rel_trunk[i, j] = ai == aj or bi == bj
                self.fan_in[i, j] = bi == bj and ai != aj
        self.cache = {}
        # story order: every box a causal chain (stim arrows) leads to should sit right of (or below) its cause
        succ = defaultdict(set)
        for a, b, _ in self.edges:
            if a in self.nodes and b in self.nodes and self.dirw.get((a, b), 1.0) >= 1.0:
                succ[a].add(b)
        self.reach = []
        for a in self.nodes:
            seen, stack = set(), list(succ[a])
            while stack:
                x = stack.pop()
                if x in seen or x == a:
                    continue
                seen.add(x)
                stack += list(succ[x])
            self.reach += [(a, b) for b in seen]

    def best_route(self, pa, pb, occ, skip, dirw=1.0):
        W = self.W
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        pen = dirw * ((W["left"] if dx < 0 else 0.0) + ((W["up"] if dx != 0 else W["up"] * 1.25) if dy < 0 else 0.0))
        best = None
        for pts, bends in cands(pa, pb):
            if any(seg_blocked(p, q, occ, skip) for p, q in zip(pts, pts[1:])):
                continue
            ln = sum(abs(p[0] - q[0]) + W["wy"] * abs(p[1] - q[1]) for p, q in zip(pts, pts[1:]))
            cost = ln * W["len"] + bends * W["bend"] + pen
            if best is None or cost < best[0]:
                best = (cost, pts)
        return best

    def route(self, a, b, pos, occ):
        pa = pos[a] if a in pos else self.virtual[a]
        pb = pos[b] if b in pos else self.virtual[b]
        c0, c1 = min(pa[0], pb[0]) - 1, max(pa[0], pb[0]) + 1
        r0, r1 = min(pa[1], pb[1]) - 1, max(pa[1], pb[1]) + 1
        win = tuple(sorted(c for c in occ if c0 <= c[0] <= c1 and r0 <= c[1] <= r1 and c != pa and c != pb))
        dw = self.dirw.get((a, b), 1.0)
        key = (pa, pb, win, dw)
        r = self.cache.get(key)
        if r is None:
            r = self.best_route(pa, pb, occ, (pa, pb), dw) or (None, None)
            if len(self.cache) > 400000:
                self.cache.clear()
            self.cache[key] = r
        return r

    def evaluate(self, pos, detail=False):
        W = self.W
        occ = {p: n for n, p in pos.items()}
        total, routes, fails = 0.0, [], 0
        for a, b, w in self.edges:
            cost, pts = self.route(a, b, pos, occ)
            if pts is None:
                total += W["fail"] * w
                fails += 1
            else:
                total += cost * w
            routes.append(pts)
        for d in self.drops:
            c, r = pos[d]
            blocked = sum(1 for rr in range(r + 1, self.R) if (c, rr) in occ)
            total += W["drop_block"] * blocked + W["drop_high"] * (self.R - 1 - r)
            routes.append([(c, r + HH), (c, self.R + 0.6)])
        for c in self.through:
            total += W["through"] * sum(1 for rr in range(self.R) if (c, rr) in occ)
            routes.append([(c, -0.6), (c, self.R - 0.4)])
        for a, b in self.order:
            if pos[a][0] >= pos[b][0]:
                total += W["order"] * (pos[a][0] - pos[b][0] + 1)
        for a, b in self.reach:
            (ca, ra), (cb, rb) = pos[a], pos[b]
            if cb < ca:
                total += W["story"]
            elif cb == ca and rb < ra:
                total += W["story_v"]
        nc, novl, nsh, nfi = conflicts(routes, self.rel_cross, self.rel_trunk, self.fan_in)
        total += W["cross"] * nc + W["ovl"] * novl + W["shared"] * nsh + W["fan_in"] * nfi
        own = [pos[n] for n in self.nodes]
        cols = {p[0] for p in own}
        rows = {p[1] for p in own}
        if self.mode == "shape":
            total += W["col"] * (max(cols) - min(cols) + 1) + W["row"] * (max(rows) - min(rows) + 1)
        elif self.mode == "slot":
            for c in {0, self.C - 1}:
                if c not in cols:
                    total += W["edge_empty"]
            if 0 not in rows:
                total += W["edge_empty"]
            if self.R - 1 not in rows and not self.drops:
                total += W["edge_empty"] * 0.5
            # rows and columns are shared with the rest of the page, and an empty one collapses: a stage with
            # enough boxes fills every row and column of its slot
            n = len(self.nodes)
            if n >= self.R:
                total += 0.8 * (self.R - len(rows))
            if n >= self.C:
                total += 0.8 * (self.C - len(cols))
        if detail:
            return total, dict(routes=routes, crossings=nc, overlaps=novl, fails=fails)
        return total

    def initial(self, rng):
        """Layered start: longest-path layers left to right (causes left of effects)."""
        preds = {n: [] for n in self.nodes}
        for a, b, _ in self.edges:
            if a in preds and b in preds:
                preds[b].append(a)
        layer = {}

        def L(n, stack=()):
            if n in layer:
                return layer[n]
            if n in stack:
                return 0
            v = max((L(p, stack + (n,)) + 1 for p in preds[n]), default=0)
            layer[n] = v
            return v
        for n in self.nodes:
            L(n)
        pos = dict(self.fixed)
        taken = set(pos.values())
        nl = max(layer.values()) + 1 if layer else 1
        for n in sorted((n for n in self.nodes if n not in self.fixed), key=lambda n: (layer[n], self.nodes.index(n))):
            c = min(self.C - 1, int(layer[n] * self.C / nl)) if nl > self.C else min(layer[n], self.C - 1)
            free = sorted((cell for cell in ((cc, r) for cc in range(self.C) for r in range(self.R)) if cell not in taken),
                          key=lambda cell: (abs(cell[0] - c) * 2 + cell[1], rng.random()))
            pos[n] = free[0]
            taken.add(free[0])
        return pos

    def anneal(self, iters=None, seed=0, T0=4.0, T1=0.03, start=None):
        rng = random.Random(seed)
        pos = dict(start) if start else self.initial(rng)
        pos.update(self.fixed)
        movable = [n for n in self.nodes if n not in self.fixed]
        if not movable:
            return self.evaluate(pos), pos
        iters = iters or (2500 + 350 * len(movable))
        cur = self.evaluate(pos)
        best, bestpos = cur, dict(pos)
        cells = [(c, r) for c in range(self.C) for r in range(self.R)]
        fixed_cells = set(self.fixed.values())
        for it in range(iters):
            T = T0 * (T1 / T0) ** (it / iters)
            n = rng.choice(movable)
            c, r = pos[n]
            if rng.random() < 0.45:
                cell = rng.choice(cells)
            else:
                cell = (min(self.C - 1, max(0, c + rng.choice((-1, 0, 1)))),
                        min(self.R - 1, max(0, r + rng.choice((-1, 0, 1)))))
            if cell == (c, r) or cell in fixed_cells:
                continue
            other = next((m for m, p in pos.items() if p == cell), None)
            old = pos[n]
            pos[n] = cell
            if other is not None:
                pos[other] = old
            new = self.evaluate(pos)
            if new <= cur or rng.random() < math.exp((cur - new) / T):
                cur = new
                if cur < best - 1e-9:
                    best, bestpos = cur, dict(pos)
            else:
                pos[n] = old
                if other is not None:
                    pos[other] = cell
        return best, bestpos

    def show(self, pos, width=9):
        cs = [p[0] for p in pos.values()]
        rs = [p[1] for p in pos.values()]
        grid = [["." * width for _ in range(min(cs), max(cs) + 1)] for _ in range(min(rs), max(rs) + 1)]
        for n, (c, r) in pos.items():
            grid[r - min(rs)][c - min(cs)] = n[:width].ljust(width)
        return "\n".join(" ".join(row) for row in grid)


def _edge_w(kind):
    return 2.0 if kind in ("inh", "req") else 1.0       # a treatment or an anatomy box sits by what it acts on


def _dir_w(kind):
    return 1.0 if kind == "stim" else 0.3               # causes run right and down; a treatment may come from anywhere


def _stage_edges(A, s):
    return [(a, b, _edge_w(k), _dir_w(k)) for a, b, k in A.intra(s)]


def _iters(n, effort):
    """Annealing steps for a stage of n boxes: the search space grows fast with n."""
    return int((1500 + 90 * n * n) * effort)


def can_fork():
    import multiprocessing as mp
    return "fork" in mp.get_all_start_methods()


def worker_count(requested=None):
    """Worker processes for the searches: --workers, else diagram.layout.workers ("auto": up to 4 forked
    workers where the platform can fork, else 1; "serial": 1; a number: that many). Without fork the
    search runs serially, since the workers read their inputs from the parent's memory."""
    if not can_fork():
        return 1
    if requested:
        return max(1, int(requested))
    try:
        import style
        v = str(style.get("diagram.layout.workers", "auto"))
    except Exception:  # noqa: BLE001
        v = "auto"
    if v == "serial":
        return 1
    if v.isdigit():
        return max(1, int(v))
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _pool_map(fn, jobs, workers):
    """Run fn over jobs in forked worker processes (or in this one when workers == 1 or fork is missing)."""
    if workers <= 1 or len(jobs) <= 1 or not can_fork():
        return [fn(j) for j in jobs]
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=mp.get_context("fork")) as ex:
        return list(ex.map(fn, jobs))


# =========================================================================== pass 1: stage shapes
_CTX = {}      # read by the worker functions (inherited through fork)
INF = float("inf")
CMAX, RMAX = 16, 10        # the largest slot a table covers (columns, rows)


def _shape_job(job):
    s, C, R, seed, iters = job
    A = _CTX["A"]
    mem = A.members[s]
    edges = _stage_edges(A, s)
    drops = [n for n in mem if n in A.feeders]
    P = GridPlacer(mem, edges, C, R, drops=drops, order=A.bar_order_pairs(s, _CTX["bar_rank"]), mode="fit")
    sc, pos = P.anneal(seed=seed, iters=iters)
    return s, C, R, sc, pos


class ShapeTable:
    """A stage's cost on its own in a slot of c columns x r rows (the best layout that fits), from anneals
    on grids of exactly C x R for small C, R; larger slots take the best smaller grid."""

    def __init__(self, runs):
        self.cost = [[INF] * (RMAX + 1) for _ in range(CMAX + 1)]
        self.pos = [[None] * (RMAX + 1) for _ in range(CMAX + 1)]
        for C, R, sc, pos in runs:
            if sc < self.cost[C][R]:
                self.cost[C][R], self.pos[C][R] = sc, pos
        for c in range(1, CMAX + 1):           # prefix minimum: a bigger slot can hold the smaller layout
            for r in range(1, RMAX + 1):
                for cc, rr in ((c - 1, r), (c, r - 1)):
                    if cc >= 1 and rr >= 1 and self.cost[cc][rr] < self.cost[c][r]:
                        self.cost[c][r], self.pos[c][r] = self.cost[cc][rr], self.pos[cc][rr]

    def at(self, c, r):
        return self.cost[min(c, CMAX)][min(r, RMAX)] if c >= 1 and r >= 1 else INF

    def minrows(self, c):
        for r in range(1, RMAX + 1):
            if self.at(c, r) < INF:
                return r
        return None

    def start(self, c, r):
        return self.pos[min(c, CMAX)][min(r, RMAX)]


def shape_tables(A, seed, effort, workers, quiet):
    """Anneal every flow stage alone on grids of C x R cells (C x R just above its box count)."""
    jobs = []
    for s in A.main + A.asides:
        n = len(A.members[s])
        for C in range(1, min(n, 8) + 1):
            r0 = math.ceil(n / C)
            for R in range(max(1, r0), r0 + 3):
                if R > 8 or (C * R > 2.6 * n + 4 and R > r0):
                    continue
                jobs.append((s, C, R, seed, _iters(n, 0.5 * effort)))
    t = time.time()
    res = _pool_map(_shape_job, jobs, workers)
    runs = defaultdict(list)
    for s, C, R, sc, pos in res:
        runs[s].append((C, R, sc, pos))
    tables = {s: ShapeTable(runs[s]) for s in A.main + A.asides}
    log(f"shapes: {len(jobs)} anneals in {time.time() - t:.1f}s", quiet=quiet)
    return tables


# =========================================================================== macro arrangement
class Metrics:
    """Typical column and row sizes (px), from the measured boxes, for estimating the page."""

    def __init__(self, size):
        ws = sorted(w for w, h in size.values()) or [E.W_MAX]
        hs = sorted(h for w, h in size.values()) or [52]
        q = lambda a, f: a[min(len(a) - 1, int(len(a) * f))]
        # a column is as wide as its widest box: the expected largest of k widths (k boxes per column)
        n = len(ws)
        k = max(2.0, n / 11.0)
        self.colpx = sum(w * ((i + 1) ** k - i ** k) / n ** k for i, w in enumerate(ws))
        self.rowpx = q(hs, 0.75) + 12
        self.rowpx_short = q(hs, 0.5)

    def page_w(self, ncols, ngutter):
        return 120 + ncols * self.colpx + (ncols - 1) * D["GAP_X"] + ngutter * COL_EXTRA

    def page_h(self, nrows, ngutter):
        return D["Y0"] + nrows * self.rowpx + (nrows - 1) * D["ROW_GAP"] + ngutter * ROW_EXTRA + D["PAD"] + 40

    def cols_for(self, px):
        """Columns a container needs to be px wide."""
        return max(1, math.ceil((px - 2 * D["PAD"] + D["GAP_X"]) / (self.colpx + D["GAP_X"])))

    def rows_for(self, px, short=False):
        """Rows a container needs to be px tall (short: as if its rows held only the lower boxes)."""
        rp = self.rowpx_short if short else self.rowpx
        return max(1, math.ceil((px - E.CL_TOP - D["PAD"] + D["ROW_GAP"]) / (rp + D["ROW_GAP"])))


def _relation(ra, rb):
    """Cost of an arrow from a stage at rect ra to one at rb (g0, g1, b0, b1): right or down between
    neighbours is cheap; left and up are dear."""
    ag0, ag1, ab0, ab1 = ra
    bg0, bg1, bb0, bb1 = rb
    rows_overlap = not (bb1 < ab0 or bb0 > ab1)
    cols_overlap = not (bg1 < ag0 or bg0 > ag1)
    if rows_overlap and bg0 > ag1:
        return 0.2 if bg0 == ag1 + 1 else 1.0 + 0.6 * (bg0 - ag1 - 1)
    if cols_overlap and bb0 > ab1:
        return 0.3 if bb0 == ab1 + 1 else 1.5 + 0.6 * (bb0 - ab1 - 1)
    if bb0 > ab1:
        return 1.2 + 0.4 * (bb0 - ab1 - 1) if bg0 > ag1 else 2.2 + 0.4 * (bb0 - ab1 - 1)
    if rows_overlap and bg1 < ag0:
        return 4.0
    if bb1 < ab0:
        return 5.0
    return 3.0


W_ROUTE = 0.4          # a unit of a stage's own route cost, against ...
W_SCREEN = 1 / 60.0    # ... a px of the page's on-screen size
W_EMPTY = 0.2          # an empty cell inside a stage's slot


class Macro:
    """Beam search over arrangements of the main stages and the key on a grid of column groups x bands."""

    def __init__(self, A, tables, metrics, keypx, beam=3000, case=None):
        self.A, self.T, self.M = A, tables, metrics
        self.keypx = keypx
        self.beam = beam
        self.nbar = {s: sum(len(A.feeders[n]) for n in A.members[s] if n in A.feeders) for s in A.main + A.asides}
        self.mincols = {s: metrics.cols_for((title_px(case, s) if case is not None else 14 + mono_w(A.titles[s])) + 14)
                        for s in A.stages}
        self.mincols["KEY"] = metrics.cols_for(keypx[0])
        self.keyrows = metrics.rows_for(keypx[1], short=True)
        self.minrows = {"KEY": self.keyrows}
        self.panels = ("KEY",)
        n = {s: len(A.members[s]) for s in A.main + A.asides}
        n["KEY"] = self.mincols["KEY"] * self.keyrows * 0.7
        ins = inset_need(case) if case is not None else None
        self.inset_w = None
        if ins:
            self.panels = ("KEY", "INSET")
            self.mincols["INSET"] = metrics.cols_for(ins[0])
            self.minrows["INSET"] = metrics.rows_for(ins[1])
            self.inset_w = ins[2]
            n["INSET"] = self.mincols["INSET"] * self.minrows["INSET"] * 0.7
        self.n = n
        self.ncmax = int((PAGE_W_MAX - 120 + D["GAP_X"]) // (metrics.colpx + D["GAP_X"])) + 1
        self.strict = True          # bands no taller than their stages fill (relaxed when nothing fits)

    def maxarea(self, s):
        if s in ("KEY", "INSET"):
            return 2
        med = sorted(self.n[x] for x in self.A.main)[len(self.A.main) // 2]
        return 4 if self.n[s] >= 1.8 * med else (3 if self.n[s] >= 1.3 * med else 2)

    def piece_cost(self, p, rect, placed, K, NB, cellcap):
        g0, g1, b0, b1 = rect
        c = 0.0
        A = self.A
        if p == "KEY":
            c += 4.0 * b0 + 2.0 * (K - 1 - g1)
        elif p == "INSET":
            c += 1.5 * b0 + 1.0 * (K - 1 - g1)
        else:
            for q, rq in placed:
                if q in ("KEY", "INSET"):
                    continue
                w1 = min(3, A.sedge.get((p, q), 0))
                w2 = min(3, A.sedge.get((q, p), 0))
                if w1:
                    c += w1 * _relation(rect, rq)
                if w2:
                    c += w2 * _relation(rq, rect)
            c += 6.0 * self.nbar.get(p, 0) * (NB - 1 - b1) / max(1, NB - 1)
            if p in A.asides:
                c += 2.0           # background and context belong below the story unless they save real room
        area = (g1 - g0 + 1) * (b1 - b0 + 1)
        c += 2.0 * abs(math.log(max(0.2, area * cellcap) / max(1.0, self.n[p] * 1.5)))
        return c

    def search(self, K, NB):
        """Arrangements on K column groups x NB bands, best first: (static cost, ((piece, rect), ...))."""
        A = self.A
        main = list(A.order)
        total = K * NB
        if total < len(main) + len(self.panels):
            return []
        cellcap = (sum(self.n[s] for s in main) * 1.5 + sum(self.n[x] for x in self.panels)) / total
        frontier = [(0.0, tuple([None] * total), tuple(main), self.panels, tuple(A.asides), (), 0, 0)]
        done = []
        while frontier:
            nxt = []
            for cost, grid, rem, keyl, asl, placed, inv, nas in frontier:
                try:
                    i0 = grid.index(None)
                except ValueError:
                    if not rem and not keyl:
                        done.append((cost, placed))
                    continue
                b, g = divmod(i0, K)
                left = total - sum(1 for x in grid if x is not None)
                if left < len(rem) + len(keyl):
                    continue
                opts = [(rem[0], inv)] if rem else []
                if len(rem) > 1 and inv < 1:
                    opts.append((rem[1], inv + 1))
                opts += [(x, inv) for x in keyl]
                if nas < 2:
                    opts += [(a, inv) for a in asl]
                for p, inv2 in opts:
                    ma = self.maxarea(p)
                    for w in range(1, K - g + 1):
                        if grid[b * K + g + w - 1] is not None:
                            break
                        for h in range(1, NB - b + 1):
                            if w * h > ma:
                                break
                            if any(grid[(b + h - 1) * K + x] is not None for x in range(g, g + w)):
                                break
                            rect = (g, g + w - 1, b, b + h - 1)
                            pc = self.piece_cost(p, rect, placed, K, NB, cellcap)
                            ng = list(grid)
                            for y in range(b, b + h):
                                for x in range(g, g + w):
                                    ng[y * K + x] = p
                            nxt.append((cost + pc + 1.0 * (inv2 - inv), tuple(ng), tuple(q for q in rem if q != p),
                                        tuple(x for x in keyl if x != p), tuple(q for q in asl if q != p),
                                        placed + ((p, rect),), inv2, nas + (p in asl)))
            nxt.sort(key=lambda st: st[0])
            frontier = nxt[:self.beam]
        done.sort(key=lambda d: d[0])
        return done

    # ---------------------------------------------------------------- sizing an arrangement
    def _pcost(self, p, c, r):
        if p in ("KEY", "INSET"):
            if c < self.mincols[p] or r < self.minrows[p]:
                return INF
            if p == "INSET":
                M = self.M
                h = r * M.rowpx + (r - 1) * D["ROW_GAP"] + E.CL_TOP + D["PAD"]
                w = c * M.colpx + (c - 1) * D["GAP_X"] + 2 * D["PAD"]
                if w < self.inset_w(h):
                    return INF
            return 2.0 * (c - self.mincols[p]) + 1.0 * (r - self.minrows[p])      # an oversized panel wastes room
        return W_ROUTE * self.T[p].at(c, r)

    def size(self, placed, K, NB, bottom):
        """Columns per group and rows per band for an arrangement: every column vector, rows by descent.
        Returns (cost, detail) or None."""
        M = self.M
        best = None
        multi = [(p, r) for p, r in placed if r[3] > r[2]]
        in_main = frozenset(p for p, _ in placed if p in self.A.asides)
        # a row is as tall as its tallest box and a column as wide as its widest: a band or a column group
        # where only the key or the inset sits would collapse
        for bnd in range(NB):
            if not any(r[2] <= bnd <= r[3] for p, r in placed if p not in self.minrows):
                return None
        for grp in range(K):
            if not any(r[0] <= grp <= r[1] for p, r in placed if p not in self.minrows):
                return None
        for kv in _vectors(K, 1, 7, self.ncmax):
            NC = sum(kv)
            if M.page_w(NC, K - 1) > PAGE_W_MAX:
                continue
            cc = {p: sum(kv[g0:g1 + 1]) for p, (g0, g1, b0, b1) in placed}
            if any(cc[p] < self.mincols.get(p, 1) for p in cc):
                continue
            bot = bottom(NC, in_main)
            if bot is None:
                continue
            # rows come from the stages: a row with no box collapses, so the key or the inset cannot make a band
            # taller than its stages fill (their own minimum is checked in _pcost)
            rows = [1] * NB
            ok = True
            for p, (g0, g1, b0, b1) in placed:
                if b0 == b1 and p not in self.minrows:
                    mr = self.T[p].minrows(cc[p])
                    if mr is None:
                        ok = False
                        break
                    rows[b0] = max(rows[b0], mr)
            if not ok:
                continue
            for p, (g0, g1, b0, b1) in multi:
                if p in self.minrows:
                    continue
                mr = self.T[p].minrows(cc[p])
                if mr is None:
                    ok = False
                    break
                have = sum(rows[b0:b1 + 1])
                if have < mr:
                    rows[b1] += mr - have
            if not ok:
                continue
            # no band gets more rows than its stages have boxes to fill (an empty row collapses)
            cap = [max((self.n[q] for q, r in placed if q not in self.minrows and r[2] <= b <= r[3]), default=1)
                   for b in range(NB)] if self.strict else [99] * NB
            if any(rows[b] > cap[b] for b in range(NB)):
                continue
            for p, (g0, g1, b0, b1) in placed:          # a panel fits in rows its band's stages fill
                if p in self.minrows:
                    short = self.minrows[p] - sum(rows[b0:b1 + 1])
                    for b in range(b1, b0 - 1, -1):
                        add = min(short, cap[b] - rows[b])
                        if add > 0:
                            rows[b] += add
                            short -= add
                    if short > 0:
                        ok = False
            if not ok:
                continue

            gut = _gutters(kv, bot, self.A.pair_stages)

            def total(rows):
                c = 0.0
                empty = 0
                for p, (g0, g1, b0, b1) in placed:
                    rc = sum(rows[b0:b1 + 1])
                    pc = self._pcost(p, cc[p], rc)
                    if pc == INF:
                        return INF
                    c += pc
                    if p not in self.minrows:
                        empty += cc[p] * rc - self.n[p]
                W = M.page_w(NC, gut)
                H = M.page_h(sum(rows) + bot["rows"], NB - 1 + bot["bands"])
                c += W_SCREEN * max(W, SCREEN * H) + W_EMPTY * empty + bot["cost"]
                if W > PAGE_W_MAX:
                    c += 20 + (W - PAGE_W_MAX) / 10
                return c
            cur = total(rows)
            for _ in range(8):
                moved = False
                for b in range(NB):
                    for d in (1, -1):
                        if rows[b] + d < 1 or rows[b] + d > cap[b]:
                            continue
                        rows[b] += d
                        t = total(rows)
                        if t < cur - 1e-9:
                            cur, moved = t, True
                        else:
                            rows[b] -= d
                if not moved:
                    break
            if cur == INF:
                continue
            if best is None or cur < best[0]:
                W = M.page_w(NC, gut)
                H = M.page_h(sum(rows) + bot["rows"], NB - 1 + bot["bands"])
                best = (cur, dict(kv=list(kv), rows=list(rows), W=W, H=H, bottom=bot))
        return best


def _gutters(kv, bot, pair_stages=()):
    """Gutter columns: the first column of every group and of every bottom-band stage, column 0 aside."""
    cols = {sum(kv[:g]) for g in range(1, len(kv))}
    if bot["layout"]:
        widths = _bottom_widths(bot["layout"], sum(kv), pair_stages)
        cols |= {sum(widths[:i]) for i in range(1, len(widths))}
    return len(cols)


def _bottom_widths(lay, NC, pair_stages):
    """The bottom band's stage widths filling NC columns: spare columns shared in proportion to the widths
    (RULED OUT's blocks would drift far apart if it took them all); the remainder to RULED OUT, else to the
    widest stage."""
    base = [c for _, c, _ in lay]
    widths = [c + int((NC - sum(base)) * c / sum(base)) for c in base]
    tgt = next((i for i, (s, *_) in enumerate(lay) if s in pair_stages), None)
    if tgt is None:
        tgt = max(range(len(lay)), key=lambda i: widths[i])
    widths[tgt] += NC - sum(widths)
    return widths


def _vectors(K, lo, hi, maxsum):
    if K == 0:
        yield ()
        return
    for v in range(lo, hi + 1):
        if v > maxsum:
            break
        for rest in _vectors(K - 1, lo, hi, maxsum - v):
            yield (v,) + rest


def bottom_band(A, T, M, mincols, NC, in_main=frozenset()):
    """Asides (those not placed among the main stages) and RULED OUT side by side under the bars: the row
    count that fits NC columns at the least cost."""
    pieces = [s for s in A.asides if s not in in_main] + list(A.pair_stages)
    bar = 1 if A.bar_stages else 0
    if not pieces:
        return dict(rows=bar, bands=bar, gutters=0, cost=0.0, layout=[], nc=NC)
    best = None
    for Rb in range(1, 7):
        lay, cost, need, ok = [], 0.0, 0, True
        for s in pieces:
            if s in A.pair_stages:
                npairs = sum(1 for p in A.pairs if A.stage_of[p[0]] == s)
                c = max(2 * math.ceil(npairs / Rb), mincols.get(s, 1))
                lay.append([s, c, min(Rb, npairs)])
                need += c
                continue
            opts = [(c, T[s].at(c, Rb)) for c in range(max(1, mincols.get(s, 1)), NC + 1) if T[s].at(c, Rb) < INF]
            if not opts:
                ok = False
                break
            c, cst = min(opts, key=lambda x: (x[1] + 1.5 * x[0] / W_ROUTE * W_SCREEN * (M.colpx + 28), x[0]))
            lay.append([s, c, Rb])
            need += c
            cost += W_ROUTE * cst
        if not ok or need > NC:
            continue
        rows = Rb + bar
        cand = dict(rows=rows, bands=1 + bar, gutters=max(0, len(pieces) - 1), cost=cost, layout=lay, nc=NC, Rb=Rb)
        sc = cost + W_SCREEN * SCREEN * (Rb * (M.rowpx + D["ROW_GAP"]) + ROW_EXTRA)
        if best is None or sc < best[0]:
            best = (sc, cand)
    return best[1] if best else None


# =========================================================================== pass 2: slots
class Plan:
    """An arrangement made concrete: global columns and rows for every piece."""

    def __init__(self, A, macro, placed, K, NB, sized):
        self.A = A
        self.K, self.NB = K, NB
        kv, rows = sized["kv"], sized["rows"]
        self.kv, self.rows = kv, rows
        self.gstart = [sum(kv[:g]) for g in range(K)]
        self.bstart = [sum(rows[:b]) for b in range(NB)]
        self.NC = sum(kv)
        self.slot = {}       # piece -> (c0, c1, r0, r1) global, inclusive
        for p, (g0, g1, b0, b1) in placed:
            c0 = self.gstart[g0]
            c1 = self.gstart[g1] + kv[g1] - 1
            r0 = self.bstart[b0]
            r1 = self.bstart[b1] + rows[b1] - 1
            self.slot[p] = (c0, c1, r0, r1)
        self.main_rows = sum(rows)
        self.placed = placed
        self.bottom = sized["bottom"]
        self.groups = [(self.gstart[g], self.gstart[g] + kv[g] - 1) for g in range(K)]


def _external_points(A, plan, s, pos_global):
    """Virtual endpoints (local cells) for arrows between stage s and other placed or planned stages."""
    c0, c1, r0, r1 = plan.slot[s]
    virt, edges = {}, []
    for e in A.EDG:
        a, b = e["a"], e["b"]
        sa, sb = A.stage_of[a], A.stage_of[b]
        if (sa == s) == (sb == s):
            continue
        other, mine = (b, a) if sa == s else (a, b)
        so = A.stage_of[other]
        if so not in plan.slot:
            continue
        if other in pos_global:
            gc, gr = pos_global[other]
        else:
            oc0, oc1, or0, or1 = plan.slot[so]
            gc = min(max((c0 + c1) / 2, oc0), oc1)
            gr = min(max((r0 + r1) / 2, or0), or1)
            if oc0 > c1:
                gc = oc0
            elif oc1 < c0:
                gc = oc1
            if or0 > r1:
                gr = or0
            elif or1 < r0:
                gr = or1
            gc, gr = round(gc), round(gr)
        vid = f"__x_{other}"
        virt[vid] = (gc - c0, gr - r0)
        dw = _dir_w(e["kind"])
        edges.append((vid, mine, 1.5, dw) if sa != s else (mine, vid, 1.5, dw))
    return virt, edges


def _slot_job(job):
    s, seed, iters = job
    A, plan, pos_global, bar_rank, through = _CTX["A"], _CTX["plan"], _CTX["pos"], _CTX["bar_rank"], _CTX["through"]
    c0, c1, r0, r1 = plan.slot[s]
    C, R = c1 - c0 + 1, r1 - r0 + 1
    mem = A.members[s]
    edges = _stage_edges(A, s)
    virt, ext = _external_points(A, plan, s, pos_global)
    drops = [n for n in mem if n in A.feeders]
    # boxes of stages already placed next to this slot: fixed obstacles (a route from outside must get past them)
    obst = {f"__o_{n}": (p[0] - c0, p[1] - r0) for n, p in pos_global.items()
            if A.stage_of.get(n) != s and isinstance(p[0], (int, float))
            and c0 - 2 <= p[0] <= c1 + 2 and r0 - 2 <= p[1] <= r1 + 2}
    P = GridPlacer(mem, edges + ext, C, R, virtual=virt, drops=drops, order=A.bar_order_pairs(s, bar_rank),
                   through=[c - c0 for c in through.get(s, ())], mode="slot", fixed=obst)
    start = _CTX.get("start", {}).get(s) if _CTX.get("seed0") is not None and seed == _CTX.get("seed0") else None
    if start and all(0 <= p[0] < C and 0 <= p[1] < R for p in start.values()) and len(set(start.values())) == len(start):
        sc, pos = P.anneal(seed=seed, iters=iters, start=start, T0=2.0)
    else:
        sc, pos = P.anneal(seed=seed, iters=iters)
    return s, sc, {n: (p[0] + c0, p[1] + r0) for n, p in pos.items() if n in mem}


def place_slots(A, plan, tables, bar_rank, seed, effort, workers, quiet):
    """Anneal every flow stage in its slot, in flow order, with arrows to placed stages as fixed points."""
    pos = {}
    through = defaultdict(list)
    order = [p for p in A.order if p in plan.slot] + [p for p in A.asides if p in plan.slot]
    for s in order:
        mem = A.members[s]
        c0, c1, r0, r1 = plan.slot[s]
        C, R = c1 - c0 + 1, r1 - r0 + 1
        start = tables[s].start(C, R) if s in tables else None
        _CTX.update(A=A, plan=plan, pos=dict(pos), bar_rank=bar_rank, through=through, start={s: start} if start else {},
                    seed0=seed)
        iters = _iters(len(mem), effort)
        res = _pool_map(_slot_job, [(s, seed + k, iters) for k in range(max(2, int(4 * effort)))], workers)
        _, sc, best = min(res, key=lambda x: x[1])
        pos.update(best)
        # bar feeders here drop through every stage slot below them
        for nid in mem:
            if nid in A.feeders:
                gc, gr = best[nid]
                for t, (tc0, tc1, tr0, tr1) in plan.slot.items():
                    if t != s and tc0 <= gc <= tc1 and tr0 > gr:
                        through[t].append(gc)
    return pos


# =========================================================================== assembling layout.py data
def assemble(A, plan, pos, bar_rank, wmax):
    """POS, SPAN, KEY_SPAN, GROUPS ... for the plan, with bars, RULED OUT pairs and the bottom band."""
    NC = plan.NC
    POS = dict(pos)
    SPAN = {}
    for p, sl in plan.slot.items():
        if p not in ("KEY", "INSET"):
            SPAN[p] = sl
    key_span = plan.slot["KEY"]
    row = plan.main_rows
    groups_above = None
    if A.bar_stages:
        groups_above = row
        # bars: ordered by bar_rank, column ranges split where the feeders separate
        bars = sorted(A.bars, key=lambda b: bar_rank[b])
        fx = {b: [] for b in bars}
        for n, bl in A.feeders.items():
            if n in POS:
                for b in bl:
                    fx[b].append(POS[n][0])
        cuts = _bar_cuts(bars, fx, NC)
        for s in A.bar_stages:
            SPAN[s] = (0, NC - 1, row, row)
        for b, (a, z) in zip(bars, cuts):
            POS[b] = ((a, z), row)
        row += 1
    bl = plan.bottom["layout"] if plan.bottom else []
    if bl:
        if groups_above is None:
            groups_above = row
        Rb = max(r for _, _, r in bl)
        widths = _bottom_widths(bl, NC, A.pair_stages)
        c = 0
        for (s, _, r), w in zip(bl, widths):
            SPAN[s] = (c, c + w - 1, row, row + Rb - 1)
            if s in A.pair_stages:
                _place_pairs(A, s, POS, c, w, row, Rb)
            c += w
    out = dict(POS=POS, SPAN=SPAN, KEY_SPAN=key_span,
               GROUPS=[g for g in plan.groups if g[1] - g[0] >= 2], GROUPS_ABOVE_ROW=groups_above,
               W_MAX=dict(wmax), HINT={})
    if "INSET" in plan.slot:
        out["INSET_SPAN"] = plan.slot["INSET"]
    return out


def _clip_spans(data):
    """The engine's grid ends at the last row and column a box uses: clip spans that reach past it."""
    rows, cols = [], []
    for p in data["POS"].values():
        rows.append(p[1])
        cols.append(p[0][1] if isinstance(p[0], tuple) else p[0])
    mr, mc = int(math.ceil(max(rows))), int(math.ceil(max(cols)))
    for s, (c0, c1, r0, r1) in list(data["SPAN"].items()):
        data["SPAN"][s] = (c0, min(c1, mc), r0, min(r1, mr))
    for k in ("KEY_SPAN", "INSET_SPAN"):
        if data.get(k):
            c0, c1, r0, r1 = data[k]
            data[k] = (c0, min(c1, mc), r0, min(r1, mr))
    data["GROUPS"] = [(g0, min(g1, mc)) for g0, g1 in data["GROUPS"] if g0 <= mc]


def _bar_cuts(bars, fx, NC):
    """Column ranges for the bars (left to right): split where the fewest feeders land on the wrong side."""
    n = len(bars)
    if n == 1:
        return [(0, NC - 1)]
    best = None

    def rec(i, start, acc, bad):
        nonlocal best
        if i == n - 1:
            if NC - start < 1:
                return
            b = bad + sum(1 for x in fx[bars[i]] if x < start)
            sizes = [z - a + 1 for a, z in acc] + [NC - start]
            spread = max(sizes) - min(sizes)
            if best is None or (b, spread) < best[0]:
                best = ((b, spread), acc + [(start, NC - 1)])
            return
        for end in range(start, NC - (n - 1 - i)):
            b = bad + sum(1 for x in fx[bars[i]] if x < start or x > end)
            rec(i + 1, end + 1, acc + [(start, end)], b)
    rec(0, 0, [], 0)
    return best[1]


def _place_pairs(A, s, POS, c0, w, row, Rb):
    """RULED OUT: evidence -> diagnosis blocks, row by row in PAIRS order. The engine spreads the blocks evenly
    from the first evidence column to the last diagnosis column, so spare columns go to the margins (and at most
    one between blocks) rather than into one wide gap."""
    pairs = [p for p in A.pairs if A.stage_of[p[0]] == s]
    nb = math.ceil(len(pairs) / Rb)
    spare = w - 2 * nb
    g = 1 if nb > 1 and spare >= nb - 1 + 2 else 0
    used = 2 * nb + g * (nb - 1)
    m = max(0, (w - used) // 2)
    evc = [c0 + m + i * (2 + g) for i in range(nb)]
    for i, p in enumerate(pairs):
        b, r = i % nb, i // nb
        POS[p[0]] = (evc[b], row + r)
        POS[p[3]] = (evc[b] + 1, row + r)


# =========================================================================== real geometry
class Real:
    """The draft built by layout_engine itself (Params, build, clusters), with routes searched edge by edge
    and scored by the engine's own checks."""

    def __init__(self, base, data):
        content = base.content
        self.content = content
        self.data = data
        self.fake = with_layout(base, data)
        self.P = LE.Params(self.fake)
        self.boxes, self.grid = LE.build(self.P)
        self.rects = LE.clusters(self.P, self.boxes, self.grid)       # raises LayoutError on a bad grid
        self.allrects = all_panels(self.P, self.grid, self.rects)
        self.edges = list(content.EDGES)
        self.titles = title_rects(self.P, self.rects, self.allrects, content)
        self.route_cache = {}
        self.pair_cache = {}

    # -------------------------------------------------------------- one route (as layout_engine.route_all)
    def route(self, i, spec):
        key = (i, _spec_key(spec))
        if key in self.route_cache:
            return self.route_cache[key]
        ed = self.edges[i]
        S, Dd = self.boxes[ed["a"]], self.boxes[ed["b"]]
        sp = dict(spec)
        for k in ("x", "y"):
            v = sp.get(k)
            if isinstance(v, str):
                if v.startswith("sx"):
                    sp[k] = S.x0 + float(v[2:]) * S.w
                elif v.startswith("sy"):
                    sp[k] = S.y0 + float(v[2:]) * S.h
                elif v.startswith("t"):
                    sp[k] = Dd.y0 + float(v[1:]) * Dd.h
            elif v is None:
                sp.pop(k, None)
        ch = sp.get("ch")
        if isinstance(ch, tuple) and ch[0] == "gutter":
            sp["ch"] = (self.rects[ch[1]][2] + self.rects[ch[2]][0]) / 2
        elif isinstance(ch, tuple) and ch[0] == "mid":
            sp["ch"] = (self.boxes[ch[1]].x1 + self.boxes[ch[2]].x0) / 2
        if sp.get("ch") == "trunk":
            kids = [self.boxes[x["b"]] for x in self.edges
                    if x["a"] == ed["a"] and self.P.POS[x["b"]][1] == self.P.POS[ed["b"]][1]]
            sp["ch"] = (S.y1 + min(k.y0 for k in kids)) / 2
        good, _ = E.auto_route(S, Dd, sp, list(self.boxes.values()))
        pts = tuple(tuple(p) for p in min(good, key=lambda p: (len(p), E.route_len(p)))) if good else None
        self.route_cache[key] = pts
        return pts

    def self_cost(self, i, pts):
        """Problems one route makes on its own: unrouted, along a container's side, through a stage title."""
        if pts is None:
            return 50.0
        c = 0.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            for cid, (cx0, cy0, cx1, cy1) in self.allrects.items():
                if x0 == x1 and min(abs(x0 - cx0), abs(x0 - cx1)) < 8 and min(y0, y1) < cy1 and max(y0, y1) > cy0:
                    c += 10
                if y0 == y1 and min(abs(y0 - cy0), abs(y0 - cy1)) < 8 and min(x0, x1) < cx1 and max(x0, x1) > cx0:
                    c += 10
                if y0 == y1 and cx0 < min(x0, x1) + 2 and max(x0, x1) - 2 < cx1 and cy0 + 2 < y0 < cy0 + 22:
                    c += 0.5                                  # along a title band (a warning in the engine)
            for tx0, ty0, tx1, ty1 in self.titles:
                if not (max(x0, x1) < tx0 or min(x0, x1) > tx1 or max(y0, y1) < ty0 or min(y0, y1) > ty1):
                    c += 10
            for name in ("key", "inset"):           # no connector crosses the key or the inset
                r = self.allrects.get(name)
                if r and not (max(x0, x1) < r[0] or min(x0, x1) > r[2] or max(y0, y1) < r[1] or min(y0, y1) > r[3]):
                    c += 10
        return c

    def pair_cost(self, i, pi, j, pj):
        """Problems two routes make together (core.check): crossings, unrelated overlaps, false continuations."""
        if pi is None or pj is None:
            return 0.0
        key = (i, pi, j, pj) if i < j else (j, pj, i, pi)
        v = self.pair_cache.get(key)
        if v is not None:
            return v
        A, B = self.edges[i], self.edges[j]
        bx = (min(p[0] for p in pi), max(p[0] for p in pi), min(p[1] for p in pi), max(p[1] for p in pi))
        by = (min(p[0] for p in pj), max(p[0] for p in pj), min(p[1] for p in pj), max(p[1] for p in pj))
        if bx[1] + 4 < by[0] or by[1] + 4 < bx[0] or bx[3] + 4 < by[2] or by[3] + 4 < bx[2]:
            self.pair_cache[key] = 0.0
            return 0.0
        related = A["a"] == B["a"] or A["b"] == B["b"]
        fan_in = A["b"] == B["b"] and A["a"] != B["a"]
        c = 0.0
        si, sj = E.segs(list(pi)), E.segs(list(pj))
        for s in si:
            for t in sj:
                if not related and E._overlap(s, t):
                    c += 10
                elif related and E._overlap(s, t):
                    # arrows into one box share their last stretch: the heads merge (a tee and an arrow worst)
                    c += (10 if A["kind"] != B["kind"] else 4) if fan_in else 0.3
                if E._inter(s, t):
                    c += 10
        if not related:
            for p in pi:
                for t in sj:
                    if E._pt_on_seg(p, t):
                        c += 10
            for p in pj:
                for s in si:
                    if E._pt_on_seg(p, s):
                        c += 10
        if len(self.pair_cache) > 2_000_000:
            self.pair_cache.clear()
        self.pair_cache[key] = c
        return c

    # -------------------------------------------------------------- candidate hints
    def specs(self, i):
        ed = self.edges[i]
        a, b = ed["a"], ed["b"]
        S, Dd = self.boxes[a], self.boxes[b]
        out = [{}]
        out += [dict(shape="V"), dict(shape="V", x="sx0.3"), dict(shape="V", x="sx0.7")]
        out += [dict(shape="H"), dict(shape="H", y="sy0.3"), dict(shape="H", y="sy0.7"),
                dict(shape="H", y="t0.3"), dict(shape="H", y="t0.7")]
        for sx in (0.5, 0.25, 0.75, 0.15, 0.85):
            for tx in (0.5, 0.3, 0.7):
                out.append(dict(shape="LV", sx=sx, tx=tx))
        for sx in (0.5, 0.3, 0.7):
            for tx in (0.5, 0.25, 0.75, 0.15, 0.85):
                out.append(dict(shape="LH", sx=sx, tx=tx))
        # ZV: default channels, the trunk, and the gaps between rows (numbers: a last resort)
        if Dd.y0 > S.y1 + 10 or S.y0 > Dd.y1 + 10:
            chans = [None, "trunk"]
            lo, hi = sorted((S.y1, Dd.y0) if Dd.y0 > S.y1 else (Dd.y1, S.y0))
            rows = sorted(self.grid["top"])
            ys = set()
            for r0, r1 in zip(rows, rows[1:]):
                ys.add(round((self.grid["bottom"][r0] + self.grid["top"][r1]) / 2, 1))
            ar = list(self.allrects.values())
            for u in ar:
                for w in ar:
                    if 0 < w[1] - u[3] < 40 and not (u[2] < w[0] or w[2] < u[0]):
                        ys.add(round((u[3] + w[1]) / 2, 1))      # the middle of a gutter between two containers
            ys |= {round(lo + 16, 1), round(hi - 16, 1)}
            for y in sorted(ys):
                if lo + 6 < y < hi - 6:
                    chans.append(y)
            for sx in (0.5, 0.3, 0.7, 0.15, 0.85):
                for tx in (0.5, 0.3, 0.7):
                    for ch in chans:
                        sp = dict(shape="ZV", sx=sx, tx=tx)
                        if ch is not None:
                            sp["ch"] = ch
                        out.append(sp)
        if Dd.x0 > S.x1 + 10 or S.x0 > Dd.x1 + 10:
            chans = [None]
            lo, hi = sorted((S.x1, Dd.x0) if Dd.x0 > S.x1 else (Dd.x1, S.x0))
            rc = self.rects
            for s1 in rc:
                for s2 in rc:
                    if 0 < rc[s2][0] - rc[s1][2] < 40 and not (rc[s1][3] < rc[s2][1] or rc[s2][3] < rc[s1][1]):
                        x = (rc[s1][2] + rc[s2][0]) / 2
                        if lo + 6 < x < hi - 6:
                            chans.append(("gutter", s1, s2))
            seen = set()
            for p in self.boxes.values():
                for q in self.boxes.values():
                    if abs(p.y - q.y) < 1 and 20 < q.x0 - p.x1 < 400:
                        x = (p.x1 + q.x0) / 2
                        k = round(x / 6)
                        if lo + 6 < x < hi - 6 and k not in seen and not any(
                                r.x0 < x < r.x1 for r in self.boxes.values() if abs(r.y - p.y) < 1):
                            seen.add(k)
                            chans.append(("mid", p.id, q.id))
            for sx in (0.5, 0.3, 0.7):
                for tx in (0.5, 0.3, 0.7):
                    for ch in chans[:10]:
                        sp = dict(shape="ZH", sx=sx, tx=tx)
                        if ch is not None:
                            sp["ch"] = ch
                        out.append(sp)
        return out

    @staticmethod
    def pref(spec, pts):
        """A small preference for plain, short routes and few hints."""
        if pts is None:
            return 0.0
        c = 0.35 * (len(pts) - 2) + 0.0006 * E.route_len(pts)
        if spec:
            c += 0.15 + 0.03 * len(spec)
            if isinstance(spec.get("ch"), (int, float)):
                c += 0.4
        return c


def _spec_key(spec):
    return tuple(sorted((k, v) for k, v in spec.items()))


def route_search(R, init=None, rounds=8, seed=0, quiet=True):
    """Choose a hint per edge so that the routes make as few problems as possible (local search)."""
    rng = random.Random(seed)
    n = len(R.edges)
    spec = [dict(init.get((e["a"], e["b"]), {})) if init else {} for e in R.edges]
    pts = [R.route(i, spec[i]) for i in range(n)]
    if any(p is None for p in pts):
        for i in range(n):
            if pts[i] is None and spec[i]:
                spec[i] = {}
                pts[i] = R.route(i, {})
    cands = {}

    def edge_cost(i, sp, p):
        c = R.self_cost(i, p) + R.pref(sp, p)
        for j in range(n):
            if j != i:
                c += R.pair_cost(i, p, j, pts[j])
        return c

    def trouble():
        bad = set()
        for i in range(n):
            if R.self_cost(i, pts[i]) >= 10:
                bad.add(i)
            for j in range(i + 1, n):
                if R.pair_cost(i, pts[i], j, pts[j]) >= 4:
                    bad.add(i)
                    bad.add(j)
        return bad

    best_total, best_spec = None, None
    for rnd in range(rounds):
        bad = trouble()
        total = sum(R.self_cost(i, pts[i]) for i in range(n)) + sum(
            R.pair_cost(i, pts[i], j, pts[j]) for i in range(n) for j in range(i + 1, n))
        if best_total is None or total < best_total:
            best_total, best_spec = total, [dict(s) for s in spec]
        if not bad:
            break
        changed = False
        order = sorted(bad)
        rng.shuffle(order)
        for i in order:
            if i not in cands:
                cl, seen = [], set()
                for sp in R.specs(i):
                    p = R.route(i, sp)
                    if p is None:
                        continue
                    k = tuple(p)
                    if k in seen:
                        continue
                    seen.add(k)
                    cl.append((sp, p))
                cands[i] = cl
            cur = edge_cost(i, spec[i], pts[i])
            scored = sorted(((edge_cost(i, sp, p), sp, p) for sp, p in cands[i]), key=lambda x: x[0])
            if not scored:
                continue
            c, sp, p = scored[0]
            if rnd >= 3 and c >= cur - 1e-9 and len(scored) > 1 and cur >= 10 and rng.random() < 0.3:
                c, sp, p = scored[rng.randrange(min(3, len(scored)))]       # shake a stuck edge
            if c < cur - 1e-9 or (rnd >= 3 and p != pts[i] and c < cur + 5):
                spec[i], pts[i] = sp, p
                changed = True
        if not changed and rnd >= 3:
            break
    total = sum(R.self_cost(i, pts[i]) for i in range(n)) + sum(
        R.pair_cost(i, pts[i], j, pts[j]) for i in range(n) for j in range(i + 1, n))
    if best_total is not None and best_total < total:
        spec = best_spec
    return {(R.edges[i]["a"], R.edges[i]["b"]): spec[i] for i in range(n) if spec[i]}


def engine_verdict(base, data):
    """The engine's own verdict on a draft: layout_engine.run(), exactly as build.py calls it, on the scratch
    copy of the case; plus two readability checks of autoplace's own (a line through a stage title; an arrow
    and a tee that end on one line)."""
    fake = with_layout(base, data)
    res = LE.run(fake, quiet=True)
    g = json.loads(read_legacy_text(pathlib.Path(res["geometry"])))
    edges = g["edges"]
    unrouted = list(res["unrouted"])
    rects = {k: tuple(v) for k, v in g["clusters"].items()}
    panels = dict(rects, key=tuple(g["key"]))
    if g.get("inset"):
        r = g["inset"].get("rect") if isinstance(g["inset"], dict) else None
        if r:
            panels["inset"] = tuple(r)
    P = types.SimpleNamespace(STAGES=g.get("stages") or {}, CLUSTERS=base.content.CLUSTERS) if g.get("stages") else object()
    cut = []
    for tb in title_rects(P, rects, panels, base.content):
        for e in edges:
            for (a0, b0), (a1, b1) in zip(e["pts"], e["pts"][1:]):
                if not (max(a0, a1) < tb[0] or min(a0, a1) > tb[2] or max(b0, b1) < tb[1] or min(b0, b1) > tb[3]):
                    cut.append(f"{e['a']}->{e['b']} cuts a stage title at ({tb[0]:.0f}, {tb[1]:.0f})")
    merged = []          # an arrow and a tee into one box along the same last stretch
    for i, A_ in enumerate(edges):
        for B_ in edges[i + 1:]:
            if A_["b"] == B_["b"] and A_["a"] != B_["a"] and A_["kind"] != B_["kind"]:
                sa, sb = E.segs(A_["pts"])[-1], E.segs(B_["pts"])[-1]
                if E._overlap(sa, sb):
                    merged.append(f"{A_['a']}->{A_['b']} and {B_['a']}->{B_['b']} end on one line")
    probs = list(res["problems"])
    kx0, ky0, kx1, ky1 = panels["key"]
    used = None
    try:
        import render
        if hasattr(render, "used_layers"):
            used = render.used_layers(g)
    except Exception:
        used = None
    if not key_fits(base, kx1 - kx0, ky1 - ky0, used):
        probs.append(f"the key does not fit its panel ({kx1 - kx0:.0f} x {ky1 - ky0:.0f} px): make KEY_SPAN larger")
    img = (g.get("inset") or {}).get("img") if isinstance(g.get("inset"), dict) else None
    if img and img.get("h", 999) < 160:
        probs.append(f"the inset's image is only {img['h']:.0f} px tall: give INSET_SPAN more rows")
    xs = [r[0] for r in panels.values()] + [r[2] for r in panels.values()]
    ys = [r[3] for r in panels.values()]
    W, H = max(xs) + min(xs), max(ys) + 40
    return dict(problems=probs, warnings=list(res["warnings"]), crossings=list(res["crossings"]),
                unrouted=unrouted, titles=cut, merges=merged, W=W, H=H, edges=edges)


def _final_key(v):
    """How the candidates compare at the end: one problem counts as much as 120 px of on-screen size."""
    return score(v) * 120 + max(v["W"], SCREEN * v["H"])


def severity(v):
    """How far a draft is from clean, counting each edge in trouble once (an unrouted edge's fallback line
    reports many problems that one hint or move fixes together)."""
    edges, other = set(), 0
    for p in v["problems"]:
        found = re.findall(r"([A-Za-z_][A-Za-z0-9_]*->[A-Za-z_][A-Za-z0-9_]*)", p)
        if found:
            edges |= set(found)
        else:
            other += 1
    for a, b, _ in v["crossings"]:
        edges |= {a, b}
    edges |= set(v["unrouted"])
    return len(edges) + other + len(v["titles"]) + len(v["merges"]) + (5 if v["W"] > PAGE_W_MAX else 0)


def score(v):
    """Lower is better: problems, crossings and unrouted edges first, then the page's size on screen."""
    return (len(v["problems"]) + len(v["crossings"]) + 3 * len(v["unrouted"]) + len(v["titles"]) + len(v["merges"])
            + (5 if v["W"] > PAGE_W_MAX else 0))


# =========================================================================== width, then repair by moving boxes
def page_size(R):
    xs = [r[0] for r in R.allrects.values()] + [r[2] for r in R.allrects.values()]
    return max(xs) + min(xs), max(r[3] for r in R.allrects.values()) + 40


def fit_width(base, data, quiet=True, min_aspect=2.0):
    """While the page is wider than PAGE_W_MAX, narrow the column that gives the most: every box at its
    widest gets one more line (a lower W_MAX), as long as no box turns squarer than min_aspect : 1."""
    data = dict(data, W_MAX=dict(data["W_MAX"]))
    for _ in range(40):
        R = Real(base, data)
        W, _ = page_size(R)
        if W <= PAGE_W_MAX:
            break
        cols = defaultdict(list)
        for nid, p in data["POS"].items():
            if not isinstance(p[0], tuple) and float(p[0]).is_integer() and nid in R.boxes:
                cols[int(p[0])].append(R.boxes[nid])
        best = None
        for c, bs in cols.items():
            top = max(b.w for b in bs)
            new_w, change = 0, {}
            for b in bs:
                if b.w < top:
                    new_w = max(new_w, b.w)
                    continue
                nd = dict(base.content.NODES[b.id])
                if hasattr(R.P, "TEXT"):
                    nd["text"] = R.P.TEXT[b.id]       # as layout_engine.build measures it (lab marks included)
                done = None
                for wm in range(int(top) - E.W_STEP, int(top) - 6 * E.W_STEP, -E.W_STEP):
                    try:
                        lines, w, h = E.size_node(nd, w_max=wm)
                    except TypeError:
                        continue
                    if w < top and (len(lines) == 1 or w / h >= min_aspect):
                        done = (wm, w)
                        break
                if done is None:
                    new_w = top
                    break
                change[b.id] = done[0]
                new_w = max(new_w, done[1])
            gain = top - new_w
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, change)
        if best is None:
            break
        data["W_MAX"].update(best[1])
    return data


def _involved(A, v):
    """Boxes named by the verdict's problems, crossings, unrouted edges and cut titles, most often first."""
    involved = Counter()
    for p in v["problems"]:
        for nid in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p):
            if nid in A.N:
                involved[nid] += 1
    for a, b, _ in v["crossings"]:
        for x in a.split("->") + b.split("->"):
            involved[x] += 1
    for u in v["unrouted"] + [t.split(" cuts")[0] for t in v["titles"]] + [x for m in v["merges"] for x in m.split(" end")[0].split(" and ")]:
        for x in u.split("->"):
            involved[x] += 1
    return involved


def restage(base, A, plan, data, v, bar_rank, seed, effort, workers, quiet, rounds=2):
    """Anneal a stage in trouble again from fresh seeds, with every other stage where it is, and keep the
    placement that does best in real geometry (routes searched, the engine's verdict)."""
    cur, best = data, (score(v), max(v["W"], SCREEN * v["H"]))
    for rnd in range(rounds):
        if best[0] == 0:
            break
        inv = _involved(A, v)
        stages = Counter()
        for n, k in inv.items():
            st = A.stage_of.get(n)
            if st in plan.slot and st in A.members and st not in A.pair_stages and st not in A.bar_stages:
                stages[st] += k
        improved = False
        for s, _ in stages.most_common(2):
            pos_others = {k: p for k, p in cur["POS"].items() if A.stage_of.get(k) != s and not isinstance(p[0], tuple)}
            _CTX.update(A=A, plan=plan, pos=pos_others, bar_rank=bar_rank, through={}, start={}, seed0=None)
            n = len(A.members[s])
            jobs = [(s, seed + 101 + 17 * rnd + k, _iters(n, effort * 1.3)) for k in range(max(4, int(8 * effort)))]
            res = _pool_map(_slot_job, jobs, workers)
            trials = []
            for _, sc, newpos in sorted(res, key=lambda x: x[1])[:6]:
                POS = dict(cur["POS"])
                POS.update(newpos)
                trial = dict(cur, POS=POS, HINT={k: h for k, h in cur["HINT"].items()
                                                 if A.stage_of[k[0]] != s and A.stage_of[k[1]] != s})
                try:
                    R = Real(base, trial)
                    trial["HINT"] = route_search(R, init=trial["HINT"], seed=seed)
                    tv = engine_verdict(base, trial)
                except LE.LayoutError:
                    continue
                trials.append(((score(tv), max(tv["W"], SCREEN * tv["H"])), trial, tv))
            if trials:
                sc, trial, tv = min(trials, key=lambda x: x[0])
                if sc < best:
                    best, cur, v = sc, trial, tv
                    improved = True
                    log(f"restage: {s} again -> {sc[0]}", quiet=quiet)
        if not improved:
            break
    return cur, v


def fix_spacing(base, data, v, quiet=True):
    """"A and B are N apart" (even spacing pressed two neighbours together): give the wider of the two one
    more line (a lower W_MAX, never squarer than 2:1) while that lowers the count of problems."""
    cur, best = data, v
    for _ in range(6):
        pairs = [re.match(r"(\S+) and (\S+) are -?\d+ apart", p) for p in best["problems"]]
        pairs = [m.groups() for m in pairs if m]
        if not pairs:
            break
        improved = False
        for a, b in pairs:
            R = Real(base, cur)
            for nid in sorted((a, b), key=lambda n: -R.boxes[n].w):
                nd = dict(base.content.NODES[nid])
                if hasattr(R.P, "TEXT"):
                    nd["text"] = R.P.TEXT[nid]
                top = R.boxes[nid].w
                for wm in range(int(top) - E.W_STEP, int(top) - 4 * E.W_STEP, -E.W_STEP):
                    try:
                        lines, w, h = E.size_node(nd, w_max=wm)
                    except TypeError:
                        continue
                    if w >= top or (len(lines) > 1 and w / h < 2.0):
                        continue
                    trial = dict(cur, W_MAX=dict(cur["W_MAX"], **{nid: wm}))
                    try:
                        trial["HINT"] = route_search(Real(base, trial), init=trial["HINT"])
                        tv = engine_verdict(base, trial)
                    except LE.LayoutError:
                        break
                    if score(tv) < score(best):
                        cur, best, improved = trial, tv, True
                        log(f"spacing: {nid} one line more (W_MAX {wm}) -> {score(tv)}", quiet=quiet)
                    break
                if improved:
                    break
            if not improved:
                # still pressed together: the column group they sit in keeps its natural spacing instead of
                # even steps (every gap is then at least GAP_X)
                ca, cb = cur["POS"][a][0], cur["POS"][b][0]
                for g in cur["GROUPS"]:
                    if not isinstance(ca, tuple) and not isinstance(cb, tuple) and g[0] <= min(ca, cb) and max(ca, cb) <= g[1]:
                        trial = dict(cur, GROUPS=[x for x in cur["GROUPS"] if x != g])
                        try:
                            trial["HINT"] = route_search(Real(base, trial), init=trial["HINT"])
                            tv = engine_verdict(base, trial)
                        except LE.LayoutError:
                            break
                        if score(tv) < score(best):
                            cur, best, improved = trial, tv, True
                            log(f"spacing: group {g} spaced naturally -> {score(tv)}", quiet=quiet)
                        break
            if improved:
                break
        if not improved:
            break
    return cur, best


def _neighbours(content, nid):
    return [e["b"] for e in content.EDGES if e["a"] == nid] + [e["a"] for e in content.EDGES if e["b"] == nid]


def repair(base, A, plan, data, quiet, budget_s=120.0, seed=0):
    """Move boxes in trouble to other cells of their stage (or swap them with a stage-mate) while that lowers
    the engine's count of problems; the routes are searched again after every move."""
    rng = random.Random(seed)
    t0 = time.time()
    cur = dict(data)
    v = engine_verdict(base, cur)
    best = (score(v), max(v["W"], SCREEN * v["H"]))
    log(f"repair: start {best[0]}  ({len(v['problems'])} problems, {len(v['crossings'])} crossings, "
        f"{len(v['unrouted'])} unrouted, {len(v['titles'])} titles cut, {len(v['merges'])} merged ends)", quiet=quiet)
    tried = set()
    while best[0] > 0 and time.time() - t0 < budget_s:
        involved = Counter()
        for p in v["problems"]:
            for nid in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p):
                if nid in A.N:
                    involved[nid] += 1
        for a, b, _ in v["crossings"]:
            for x in a.split("->") + b.split("->"):
                involved[x] += 1
        for u in v["unrouted"] + [t.split(" cuts")[0] for t in v["titles"]] + [x for m in v["merges"] for x in m.split(" end")[0].split(" and ")]:
            for x in u.split("->"):
                involved[x] += 1
        movable = [n for n, _ in involved.most_common() if A.stage_of.get(n) in plan.slot and n not in A.bars
                   and not any(n in (pp[0], pp[3]) for pp in A.pairs)]
        improved = False
        for nid in movable[:8]:
            s = A.stage_of[nid]
            c0, c1, r0, r1 = plan.slot[s]
            here = tuple(cur["POS"][nid])
            occ = {tuple(p): k for k, p in cur["POS"].items() if not isinstance(p[0], tuple)}
            nb = [cur["POS"][m] for m in _neighbours(base.content, nid) if m in cur["POS"] and not isinstance(cur["POS"][m][0], tuple)]
            cells = [(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1) if (c, r) != here
                     and (occ.get((c, r)) is None or A.stage_of.get(occ[(c, r)]) == s)]
            cells.sort(key=lambda cell: (sum(abs(cell[0] - p[0]) + abs(cell[1] - p[1]) for p in nb), rng.random()))
            results = []
            for cell in cells[:30]:
                key = (nid, cell, _pos_key(cur["POS"]))
                if key in tried:
                    continue
                tried.add(key)
                POS = dict(cur["POS"])
                other = occ.get(cell)
                POS[nid] = cell
                if other is not None:
                    POS[other] = here
                moved = {nid, other}
                trial = dict(cur, POS=POS, HINT={k: h for k, h in cur["HINT"].items() if not (set(k) & moved)})
                try:
                    R = Real(base, trial)
                    trial["HINT"] = route_search(R, init=trial["HINT"], seed=rng.randrange(1000))
                    tv = engine_verdict(base, trial)
                except LE.LayoutError:
                    continue
                results.append(((score(tv), max(tv["W"], SCREEN * tv["H"])), trial, tv))
                if time.time() - t0 > budget_s:
                    break
            if results:
                sc, trial, tv = min(results, key=lambda x: x[0])
                if sc < best:
                    best, cur, v = sc, trial, tv
                    improved = True
                    log(f"repair: moved {nid} -> {sc[0]}", quiet=quiet)
                    break
            if time.time() - t0 > budget_s:
                break
        if not improved:
            break
    return cur, v


def _pos_key(POS):
    return hash(tuple(sorted((k, str(v)) for k, v in POS.items())))


# =========================================================================== writing the draft
def _fmt_pos(p):
    def f(v):
        return str(int(v)) if float(v).is_integer() else f"{v:g}"
    if isinstance(p[0], tuple):
        return f"(({f(p[0][0])}, {f(p[0][1])}), {f(p[1])})"
    return f"({f(p[0])}, {f(p[1])})"


def _fmt_spec(sp):
    parts = []
    for k in ("shape", "sx", "tx", "x", "y", "ch"):
        if k in sp:
            parts.append(f"{k}={sp[k]!r}")
    return "dict(" + ", ".join(parts) + ")"


def write_draft(path, case_dir, A, data, verdict, plan, variant, seed, effort, elapsed):
    lines = []
    name = "layout.py" if not variant else f"layout_{variant}.py"
    status = (f"{len(verdict['problems'])} problems, {len(verdict['crossings'])} crossings, "
              f"{len(verdict['unrouted'])} unrouted, {len(verdict['titles'])} titles cut, {len(verdict['merges'])} merged ends; "
              f"canvas {verdict['W']:.0f} x {verdict['H']:.0f} px")
    lines.append(f'"""Layout draft by autoplace.py ({time.strftime("%Y-%m-%d")}, seed {seed}, effort {effort:g}, '
                 f'{elapsed:.0f} s) for {pathlib.Path(case_dir).name}'
                 + (f", variant {variant!r}" if variant else "") + ".")
    lines.append("")
    lines.append(f"Engine verdict on this draft: {status}.")
    lines.append(f"Copy it to {name} to build it; then move boxes by hand where the picture asks for it.")
    if verdict["problems"] or verdict["crossings"] or verdict["unrouted"] or verdict["titles"] or verdict["merges"]:
        lines.append("")
        lines.append("Still to fix by hand:")
        for p in verdict["problems"]:
            lines.append(f"  PROBLEM  {p}")
        for a, b, x in verdict["crossings"]:
            lines.append(f"  CROSSING {a} x {b} at ({x[0]:.0f}, {x[1]:.0f})")
        for u in verdict["unrouted"]:
            lines.append(f"  UNROUTED {u}")
        for t in verdict["titles"]:
            lines.append(f"  TITLE    {t}")
        for t in verdict["merges"]:
            lines.append(f"  MERGED   {t}")
    lines.append('"""')
    lines.append("")
    lines.append("# Stage containers on one grid: (first column, last column, first row, last row).")
    lines.append(f"# Column groups {plan.groups}; main bands of rows {plan.rows}.")
    lines.append("SPAN = {")
    for s in A.stages:
        if s in data["SPAN"]:
            lines.append(f"    {s!r}: {tuple(data['SPAN'][s])!r},   # {A.titles[s]}")
    lines.append("}")
    lines.append(f"KEY_SPAN = {tuple(data['KEY_SPAN'])!r}")
    if data.get("INSET_SPAN"):
        lines.append(f"INSET_SPAN = {tuple(data['INSET_SPAN'])!r}   # the illustration panel (case.json \"inset\")")
    lines.append(f"GROUPS = {data['GROUPS']!r}")
    lines.append(f"GROUPS_ABOVE_ROW = {data['GROUPS_ABOVE_ROW']!r}")
    lines.append("")
    lines.append("POS = {")
    for s in A.stages:
        mem = [n for n in A.members[s] if n in data["POS"]]
        if not mem:
            continue
        lines.append(f"    # {A.titles[s]}")
        mem.sort(key=lambda n: (data["POS"][n][1], data["POS"][n][0] if not isinstance(data["POS"][n][0], tuple)
                                else data["POS"][n][0][0]))
        row = []
        for n in mem:
            row.append(f"{n!r}: {_fmt_pos(data['POS'][n])}")
        cur = "   "
        for item in row:
            if len(cur) + len(item) + 2 > 116:
                lines.append(cur.rstrip())
                cur = "   "
            cur += " " + item + ","
        lines.append(cur.rstrip())
    lines.append("}")
    lines.append(f"W_MAX = {data['W_MAX']!r}")
    lines.append("")
    lines.append("HINT = {")
    for (a, b), sp in data["HINT"].items():
        lines.append(f"    ({a!r}, {b!r}): {_fmt_spec(sp)},")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# =========================================================================== the whole run
def autoplace(case_dir, variant=None, out=None, seed=0, effort=1.0, workers=None, quiet=False, candidates=3,
              repair_s=None, trust=False):
    """Draft a layout for the case (or one variant). Returns dict(ok, path, verdict)."""
    from case import Case
    t0 = time.time()
    case_dir = pathlib.Path(case_dir).resolve()
    out = pathlib.Path(out) if out else pathlib.Path("layout.draft.py" if not variant else f"layout_{variant}.draft.py")
    if not out.is_absolute():
        out = case_dir / out
    if out.exists() and re.fullmatch(r"layout(_[a-z0-9_]+)?\.py", out.name):
        raise SystemExit(f"autoplace: {out} exists and is a case layout; it never overwrites one. "
                         f"Write the draft elsewhere (--out layout.draft.py) and compare.")
    global PAGE_W_MAX
    workers = worker_count(workers)
    try:
        import style
        PAGE_W_MAX = int(style.get("diagram.page.max_w", PAGE_W_MAX))
    except Exception:  # noqa: BLE001  (no config: the default)
        pass
    case = Case(case_dir, variant=variant, need_layout=False, need_meta=False, trust=trust)
    base = scratch_case(case)
    try:
        return _autoplace(case, base, case_dir, variant, out, seed, effort, workers, quiet, candidates, repair_s, t0)
    finally:
        shutil.rmtree(base._scratch, ignore_errors=True)


def _autoplace(case, base, case_dir, variant, out, seed, effort, workers, quiet, candidates, repair_s, t0):
    content = case.content
    with Measured():
        A = Analysis(content)
        if not A.main and not A.asides:
            raise SystemExit("autoplace: content.py has no stages with boxes")
        size, wmax = measure(content.NODES, set(A.bars))
        M = Metrics(size)
        keypx = key_need(case)
        bar_rank0 = {b: i for i, b in enumerate(A.bars)}
        _CTX.update(A=A, bar_rank=bar_rank0)
        log(f"autoplace: {len(content.NODES)} boxes, {len(content.EDGES)} arrows; main {A.order}; asides {A.asides}; "
            f"bars {A.bar_stages}; ruled out {A.pair_stages}", quiet=quiet)
        tables = shape_tables(A, seed, effort, workers, quiet)
        macro = Macro(A, tables, M, keypx, beam=int(2500 * effort), case=case)
        mincols = macro.mincols
        cands_ = []
        t = time.time()
        bottom_cache = {}

        def bottom(NC, in_main=frozenset()):
            if (NC, in_main) not in bottom_cache:
                bottom_cache[(NC, in_main)] = bottom_band(A, tables, M, mincols, NC, in_main)
            return bottom_cache[(NC, in_main)]
        searched = []
        for K in range(1, 6):
            for NB in range(1, 5):
                if K * NB < len(A.main) + 1 or K * NB > 2 * (len(A.main) + 1) + 2:
                    continue
                done = macro.search(K, NB)
                searched.append((K, NB, done))
                for c0, placed in done[:int(110 * effort) + 10]:
                    sz = macro.size(placed, K, NB, bottom)
                    if sz is None:
                        continue
                    cands_.append((c0 + sz[0], c0, K, NB, placed, sz[1]))
        if not cands_ and macro.strict:     # a very small case: let the key's band have rows of its own
            macro.strict = False
            for K, NB, done in searched:
                for c0, placed in done[:int(110 * effort) + 10]:
                    sz = macro.size(placed, K, NB, bottom)
                    if sz is not None:
                        cands_.append((c0 + sz[0], c0, K, NB, placed, sz[1]))
        cands_.sort(key=lambda x: x[0])
        log(f"arrangements: {len(cands_)} sized in {time.time() - t:.1f}s", quiet=quiet)
        if not cands_:
            raise SystemExit("autoplace: no arrangement fits the page; split a stage or shorten boxes")
        tried = []
        seen = set()
        for cost, c0, K, NB, placed, sized in cands_:
            sig = tuple(sorted(placed))
            if sig in seen:
                continue
            seen.add(sig)
            tried.append((cost, c0, K, NB, placed, sized))
            if len(tried) >= candidates:
                break
        results = []
        def attempt(rank, plan, sd):
            """Boxes into the plan's slots (annealing seeded sd), routes, the engine's verdict; restage if close."""
            if A.bars:          # the bars, left to right, by where their feeders' stages sit
                mx = {}
                for b in A.bars:
                    xs = [(plan.slot[A.stage_of[n]][0] + plan.slot[A.stage_of[n]][1]) / 2
                          for n, bl in A.feeders.items() if b in bl and A.stage_of[n] in plan.slot]
                    mx[b] = sum(xs) / len(xs) if xs else 1e9
                bar_rank = {b: i for i, b in enumerate(sorted(A.bars, key=lambda b: (mx[b], A.bars.index(b))))}
            else:
                bar_rank = {}
            pos = place_slots(A, plan, tables, bar_rank, sd, effort, workers, quiet)
            data = assemble(A, plan, pos, bar_rank, wmax)
            data["POS"].update(_place_bottom(A, plan, data, tables, bar_rank, sd, effort, workers))
            _clip_spans(data)
            try:
                data = fit_width(base, data)
                if page_size(Real(base, data))[0] > PAGE_W_MAX:
                    data = fit_width(base, data, min_aspect=1.9)
                R = Real(base, data)
            except (LE.LayoutError, IndexError, KeyError) as ex:
                log(f"candidate {rank + 1}: grid error {ex!r}", quiet=quiet)
                return None
            data["HINT"] = route_search(R, seed=sd)
            v = engine_verdict(base, data)
            log(f"candidate {rank + 1} (seed {sd}): {len(v['problems'])} problems, {len(v['crossings'])} crossings, "
                f"{len(v['unrouted'])} unrouted, {len(v['titles'])} titles cut, {len(v['merges'])} merged ends; "
                f"{v['W']:.0f} x {v['H']:.0f}  [{time.time() - t0:.0f}s]", quiet=quiet)
            if os.environ.get("AUTOPLACE_DEBUG"):
                for x in (v["problems"] + [f"{c[0]} x {c[1]}" for c in v["crossings"]] + v["unrouted"])[:12]:
                    log("     ", x, quiet=quiet)
            if 0 < severity(v) <= (15 if rank == 0 else 6):     # close: anneal the stages in trouble again
                data, v = restage(base, A, plan, data, v, bar_rank, sd, effort, workers, quiet)
            return (_final_key(v), rank, plan, data, v, bar_rank)

        clean = False
        for rank, (cost, c0, K, NB, placed, sized) in enumerate(tried):
            plan = Plan(A, macro, placed, K, NB, sized)
            log(f"candidate {rank + 1}: cost {cost:.1f} (arrangement {c0:.1f}); groups {sized['kv']} bands {sized['rows']}; "
                + "  ".join(f"{p}@{plan.slot[p]}" for p, _ in placed), quiet=quiet)
            # the leading arrangement gets up to three annealing seeds (results vary from seed to seed); the
            # others one each
            for k in range(3 if rank == 0 else 1):
                r = attempt(rank, copy.deepcopy(plan) if k else plan, seed + 1000 * k)
                if r is None:
                    break
                results.append(r)
                if score(r[4]) == 0:
                    clean = True
                    break
                if severity(r[4]) > 15:
                    break               # far off: another seed of this arrangement will not save it
            if clean:
                break
        if not results:
            raise SystemExit("autoplace: every arrangement broke the container grid")
        results.sort(key=lambda r: r[0])
        _, rank, plan, data, v, bar_rank = results[0]
        log(f"chosen: candidate {rank + 1}", quiet=quiet)
        if severity(v) > 6:
            data, v = restage(base, A, plan, data, v, bar_rank, seed, effort, workers, quiet)
        if any(" apart" in p for p in v["problems"]):
            data, v = fix_spacing(base, data, v, quiet)
        if score(v) > 0:
            budget = repair_s if repair_s is not None else 60 * effort
            data, v = repair(base, A, plan, data, quiet, budget_s=budget, seed=seed)
        elapsed = time.time() - t0
        write_draft(out, case_dir, A, data, v, plan, variant, seed, effort, elapsed)
    ok = not (v["problems"] or v["crossings"] or v["unrouted"]) and v["W"] <= PAGE_W_MAX
    log(f"autoplace: wrote {out} in {elapsed:.0f}s: {len(v['problems'])} problems, {len(v['crossings'])} crossings, "
        f"{len(v['unrouted'])} unrouted, {len(v['titles'])} titles cut, {len(v['merges'])} merged ends; "
        f"canvas {v['W']:.0f} x {v['H']:.0f}", quiet=quiet)
    for p in v["problems"]:
        log("  PROBLEM ", p, quiet=quiet)
    for a, b, x in v["crossings"]:
        log(f"  CROSSING {a} x {b}", quiet=quiet)
    for u in v["unrouted"]:
        log("  UNROUTED", u, quiet=quiet)
    for t in v["titles"]:
        log("  TITLE   ", t, quiet=quiet)
    for t in v["merges"]:
        log("  MERGED  ", t, quiet=quiet)
    return dict(ok=ok, path=out, verdict={k: v[k] for k in ("problems", "crossings", "unrouted", "titles", "merges", "W", "H")})


def _place_bottom(A, plan, data, tables, bar_rank, seed, effort, workers):
    """Anneal the bottom-band asides in their spans (RULED OUT is placed by _place_pairs)."""
    pos = {}
    for s in A.asides:
        if s in plan.slot or s not in data["SPAN"]:
            continue
        c0, c1, r0, r1 = data["SPAN"][s]
        plan.slot[s] = (c0, c1, r0, r1)
        C, R = c1 - c0 + 1, r1 - r0 + 1
        st = tables[s].start(C, R) if s in tables else None
        _CTX.update(A=A, plan=plan, pos=dict(data["POS"]), bar_rank=bar_rank, through={}, start={s: st} if st else {},
                    seed0=seed)
        res = _pool_map(_slot_job, [(s, seed + k, _iters(len(A.members[s]), effort)) for k in range(max(2, int(4 * effort)))],
                        workers)
        _, sc, best = min(res, key=lambda x: x[1])
        pos.update(best)
    return pos


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Draft a layout.py from content.py alone (see the module docstring).")
    ap.add_argument("case_dir")
    ap.add_argument("--out", help="the draft's file name or path (default layout.draft.py, or layout_<variant>.draft.py)")
    ap.add_argument("--variant", help="lay out a variant from variants.py instead of the full case")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--effort", type=float, default=1.0, help="search effort (1 = default; 2 = about twice as long)")
    ap.add_argument("--candidates", type=int, default=3, help="arrangements carried through to real geometry")
    ap.add_argument("--workers", type=int, default=None,
                    help="worker processes (default: diagram.layout.workers; serial where the platform cannot fork)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    from case import CaseError
    try:
        style.configure(a.config)
        res = autoplace(a.case_dir, variant=a.variant, out=a.out, seed=a.seed, effort=a.effort, workers=a.workers,
                        quiet=a.quiet, candidates=a.candidates, trust=a.trust_case)
    except (CaseError, style.StyleError) as ex:
        raise SystemExit(str(ex))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
