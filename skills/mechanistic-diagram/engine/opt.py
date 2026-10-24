# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Simulated-annealing grid placement for one cluster (or a group of clusters).

Nodes sit in grid cells (c, r). Each edge takes the cheapest orthogonal route that avoids other
nodes: straight, L (one bend) or Z (two bends through the channel/gutter next to either end).
The placement cost is route length + bends + direction penalties + crossings + collinear overlaps.
The result is a starting point for the hand layout, not the layout itself.
"""
import math, random

HW, HH = 0.42, 0.34      # half box size in grid units (cells are 1 x 1)


def _box_hit(p, q, c, r, m=0.04):
    x0, x1 = min(p[0], q[0]), max(p[0], q[0])
    y0, y1 = min(p[1], q[1]), max(p[1], q[1])
    return not (x1 < c - HW - m or x0 > c + HW + m or y1 < r - HH - m or y0 > r + HH + m)


def candidates(a, b):
    (c1, r1), (c2, r2) = a, b
    out = []
    if c1 == c2 and r1 != r2:
        s = 1 if r2 > r1 else -1
        out.append(("V", [(c1, r1 + s * HH), (c1, r2 - s * HH)], 0))
    if r1 == r2 and c1 != c2:
        s = 1 if c2 > c1 else -1
        out.append(("H", [(c1 + s * HW, r1), (c2 - s * HW, r1)], 0))
    if c1 != c2 and r1 != r2:
        sv = 1 if r2 > r1 else -1
        sh = 1 if c2 > c1 else -1
        out.append(("LV", [(c1, r1 + sv * HH), (c1, r2), (c2 - sh * HW, r2)], 1))
        out.append(("LH", [(c1 + sh * HW, r1), (c2, r1), (c2, r2 - sv * HH)], 1))
        if abs(r2 - r1) >= 1:
            for rm in {r1 + sv * 0.5, r2 - sv * 0.5}:
                out.append(("ZV", [(c1, r1 + sv * HH), (c1, rm), (c2, rm), (c2, r2 - sv * HH)], 2))
        if abs(c2 - c1) >= 1:
            for cm in {c1 + sh * 0.5, c2 - sh * 0.5}:
                out.append(("ZH", [(c1 + sh * HW, r1), (cm, r1), (cm, r2), (c2 - sh * HW, r2)], 2))
    if c1 == c2 and abs(r2 - r1) > 1:   # detour around blockers in the column
        sv = 1 if r2 > r1 else -1
        for side in (-1, 1):
            cm = c1 + side * 0.5
            out.append(("DV", [(c1 + side * HW, r1), (cm, r1), (cm, r2), (c1 + side * HW, r2)], 2))
    if r1 == r2 and abs(c2 - c1) > 1:
        sh = 1 if c2 > c1 else -1
        for side in (-1, 1):
            rm = r1 + side * 0.5
            out.append(("DH", [(c1, r1 + side * HH), (c1, rm), (c2, rm), (c2, r1 + side * HH)], 2))
    return out


def seglen(pts):
    return sum(abs(pts[i][0] - pts[i + 1][0]) + abs(pts[i][1] - pts[i + 1][1]) for i in range(len(pts) - 1))


def _cross(s, t):
    (a, b), (c, d) = s, t
    ah = abs(a[1] - b[1]) < 1e-6
    ch = abs(c[1] - d[1]) < 1e-6
    if ah == ch:
        return False
    h, v = (s, t) if ah else (t, s)
    hx0, hx1 = sorted([h[0][0], h[1][0]]); hy = h[0][1]
    vy0, vy1 = sorted([v[0][1], v[1][1]]); vx = v[0][0]
    return hx0 + 1e-6 < vx < hx1 - 1e-6 and vy0 + 1e-6 < hy < vy1 - 1e-6


def _ovl(s, t):
    (a, b), (c, d) = s, t
    if abs(a[1] - b[1]) < 1e-6 and abs(c[1] - d[1]) < 1e-6 and abs(a[1] - c[1]) < 1e-6:
        lo = max(min(a[0], b[0]), min(c[0], d[0])); hi = min(max(a[0], b[0]), max(c[0], d[0]))
        return hi - lo > 1e-6
    if abs(a[0] - b[0]) < 1e-6 and abs(c[0] - d[0]) < 1e-6 and abs(a[0] - c[0]) < 1e-6:
        lo = max(min(a[1], b[1]), min(c[1], d[1])); hi = min(max(a[1], b[1]), max(c[1], d[1]))
        return hi - lo > 1e-6
    return False


class Problem:
    def __init__(self, C, R, nodes, edges, fixed=None, allowed=None, virtual=None, weights=None, flow="TB",
                 drops=(), order=()):
        self.drops = list(drops)           # nodes that need an empty column below them (to a bar under the grid)
        self.order = list(order)           # (a, b): column of a must be < column of b
        self.C, self.R = C, R
        self.nodes = list(nodes)
        self.edges = edges                 # (a, b, w)
        self.fixed = fixed or {}
        self.allowed = allowed or {}       # id -> set of cells
        self.virtual = virtual or {}       # id -> (c, r) outside the grid, fixed, never blocks
        self.flow = flow
        self.W = dict(len=1.0, bend=0.9, up=1.6, left=0.6, cross=6.0, ovl=9.0, fail=40.0, shared=1.2)
        if weights:
            self.W.update(weights)

    def cells(self):
        return [(c, r) for c in range(self.C) for r in range(self.R)]

    def evaluate(self, pos, detail=False):
        occ = {p: n for n, p in pos.items()}
        W = self.W
        total = 0.0
        routes = []
        for (a, b, w) in self.edges:
            pa = pos.get(a, self.virtual.get(a)); pb = pos.get(b, self.virtual.get(b))
            best = None
            for kind, pts, bends in candidates(pa, pb):
                okr = True
                for i in range(len(pts) - 1):
                    p, q = pts[i], pts[i + 1]
                    for cell, nid in occ.items():
                        if nid in (a, b):
                            continue
                        if _box_hit(p, q, cell[0], cell[1]):
                            okr = False
                            break
                    if not okr:
                        break
                if not okr:
                    continue
                cost = seglen(pts) * W["len"] + bends * W["bend"]
                dy = pb[1] - pa[1]; dx = pb[0] - pa[0]
                if self.flow == "TB":
                    if dy < 0: cost += W["up"]
                    if dy == 0 and dx < 0: cost += W["left"]
                else:
                    if dx < 0: cost += W["up"]
                    if dx == 0 and dy < 0: cost += W["left"]
                if best is None or cost < best[0]:
                    best = (cost, pts, kind)
            if best is None:
                total += W["fail"] * w
                routes.append((a, b, None, None))
            else:
                total += best[0] * w
                routes.append((a, b, best[1], best[2]))
        for nid in self.drops:
            c, r = pos[nid]
            for rr in range(r + 1, self.R):
                if (c, rr) in occ:
                    total += W["fail"]
            if r < self.R - 1:
                total += 0.6 * (self.R - 1 - r)
            # the drop itself is a line to the bar under the grid: it can be crossed
            routes.append((nid, "__bar_" + nid, [(c, r + HH), (c, self.R + 0.5)], "DROP"))
        for a, b in self.order:
            if pos[a][0] >= pos[b][0]:
                total += W["fail"]
        # crossings / overlaps
        for i in range(len(routes)):
            ai, bi, pi, _ = routes[i]
            if pi is None:
                continue
            for j in range(i + 1, len(routes)):
                aj, bj, pj, _ = routes[j]
                if pj is None:
                    continue
                rel = ai == aj or bi == bj
                for s in zip(pi, pi[1:]):
                    for t in zip(pj, pj[1:]):
                        if _cross(s, t) and not (ai in (aj, bj) or bi in (aj, bj)):
                            total += W["cross"]
                        elif _ovl(s, t):
                            total += W["shared"] if rel else W["ovl"]
        return (total, routes) if detail else total

    def random_pos(self, rng):
        pos = dict(self.fixed)
        free = [c for c in self.cells() if c not in pos.values()]
        rng.shuffle(free)
        for nid in self.nodes:
            if nid in pos:
                continue
            opts = [c for c in free if (nid not in self.allowed or c in self.allowed[nid])]
            c = opts[0]
            pos[nid] = c
            free.remove(c)
        return pos

    def anneal(self, iters=20000, seed=0, T0=6.0, T1=0.05, start=None):
        rng = random.Random(seed)
        pos = dict(start) if start else self.random_pos(rng)
        cur = self.evaluate(pos)
        best, bestpos = cur, dict(pos)
        movable = [n for n in self.nodes if n not in self.fixed]
        allcells = self.cells()
        for it in range(iters):
            T = T0 * (T1 / T0) ** (it / iters)
            nid = rng.choice(movable)
            occ = {p: n for n, p in pos.items()}
            if rng.random() < 0.5:
                cell = rng.choice(allcells)
            else:
                c, r = pos[nid]
                cell = (min(self.C - 1, max(0, c + rng.choice((-1, 0, 1)))), min(self.R - 1, max(0, r + rng.choice((-1, 0, 1)))))
            if cell == pos[nid]:
                continue
            if nid in self.allowed and cell not in self.allowed[nid]:
                continue
            other = occ.get(cell)
            if other is not None:
                if other in self.fixed or (other in self.allowed and pos[nid] not in self.allowed[other]):
                    continue
                pos[other], pos[nid] = pos[nid], cell
            else:
                old = pos[nid]
                pos[nid] = cell
            new = self.evaluate(pos)
            if new <= cur or rng.random() < math.exp((cur - new) / T):
                cur = new
                if cur < best:
                    best, bestpos = cur, dict(pos)
            else:
                if other is not None:
                    pos[other], pos[nid] = pos[nid], pos[other]
                else:
                    pos[nid] = old
        return best, bestpos

    def show(self, pos, width=10):
        grid = [["." * width for _ in range(self.C)] for _ in range(self.R)]
        for n, (c, r) in pos.items():
            if 0 <= r < self.R and 0 <= c < self.C:
                grid[r][c] = n[:width].ljust(width)
        return "\n".join(" ".join(row) for row in grid)
