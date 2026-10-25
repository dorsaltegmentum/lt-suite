#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Annotate an image offline for an upload-and-annotate panel.

Pillow, numpy and scipy are required. anchor_report runs on the render path.

Labels occupy added margins. Leaders connect labels to source coordinates.

    --point    "structure A:400,300"
    --bracket  "alpha:300,200:300,400"
    --ring     "beta:400,300:100,80"

Append @left or @right to choose a margin.
Append ^up, ^down, ^left or ^right to choose bracket arm direction.

Coordinates use input pixels. Margin offsets are applied during rendering.
Marks use dark casing and bright ink. Text stays outside the source image.
Brackets identify spans. Rings identify whole round structures.
Leaders stop before their text boxes. Font size follows image width.

--probe DIR writes crops around anchors and bracket ends without rendering.
Check these crops before rendering the finished image.
Anchors read from a downsampled view are the largest source of wrong labels.
--help-rules prints configured label naming rules.
"""

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402
ltcore.bootstrap(["PIL", "numpy", "scipy"])   # re-runs inside <data>/venv when one exists and a package is missing
from ltcore import config, palettes  # noqa: E402
import shutil

import argparse
import json
import math
import os

SS = 2  # Supersample the vector overlay.


def resolve_font(cfg, size, weight):
    from ltcore import fonts
    return fonts.label_font(cfg, size, weight)


class _FontOverride:
    """Forward config access while preserving the --font override."""

    def __init__(self, cfg, path):
        self._cfg = cfg
        self._path = path

    def get(self, key, *args, **kwargs):
        if key == "annotate.font_path":
            return self._path
        return self._cfg.get(key, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cfg, name)


def _split_flags(s: str):
    """Peel trailing @side and ^dir flags off an annotation spec."""
    side = arm = None
    while True:
        for sep, setter in (("@", "side"), ("^", "arm")):
            i = s.rfind(sep)
            if i > 0 and sep not in s[i + 1:]:
                val = s[i + 1:]
                if sep == "@" and val in ("left", "right"):
                    side, s = val, s[:i]
                    break
                if sep == "^" and val in ("up", "down", "left", "right"):
                    arm, s = val, s[:i]
                    break
        else:
            return s, side, arm


def parse_point(s: str):
    body, side, _ = _split_flags(s)
    try:
        text, coord = body.rsplit(":", 1)
        x, y = (int(v) for v in coord.split(","))
    except ValueError:
        raise SystemExit(f"bad --point {s!r}; want text:x,y[@side]")
    return {"kind": "point", "text": text, "p": (x, y), "side": side}


def parse_bracket(s: str):
    body, side, arm = _split_flags(s)
    try:
        text, c1, c2 = body.rsplit(":", 2)
        x1, y1 = (int(v) for v in c1.split(","))
        x2, y2 = (int(v) for v in c2.split(","))
    except ValueError:
        raise SystemExit(f"bad --bracket {s!r}; want text:x1,y1:x2,y2[@side][^dir]")
    if (x1, y1) == (x2, y2):
        raise SystemExit(f"bad --bracket {s!r}; the two ends must differ")
    return {"kind": "bracket", "text": text, "p1": (x1, y1), "p2": (x2, y2),
            "side": side, "arm": arm}


def parse_ring(s: str):
    body, side, _ = _split_flags(s)
    try:
        text, c, r = body.rsplit(":", 2)
        cx, cy = (int(v) for v in c.split(","))
        rx, ry = (int(v) for v in r.split(","))
    except ValueError:
        raise SystemExit(f"bad --ring {s!r}; want text:cx,cy:rx,ry[@side]")
    if rx <= 0 or ry <= 0:
        raise SystemExit(f"bad --ring {s!r}; radii must be positive")
    return {"kind": "ring", "text": text, "c": (cx, cy), "r": (rx, ry), "side": side}


def parse_inset(s: str):
    """Parse x,y,w,h@corner[:scale][:text]."""
    text = None
    parts = s.split(":")
    geom = parts[0]
    scale = float(parts[1]) if len(parts) > 1 and parts[1] else 2.0
    if len(parts) > 2:
        text = ":".join(parts[2:])
    corner = "topright"
    if "@" in geom:
        geom, corner = geom.rsplit("@", 1)
    try:
        x, y, w, h = (int(v) for v in geom.split(","))
    except ValueError:
        raise SystemExit(f"bad --inset {s!r}; want x,y,w,h[@corner][:scale][:text]")
    if corner not in ("topleft", "topright", "bottomleft", "bottomright"):
        raise SystemExit(f"bad --inset corner {corner!r}")
    if w <= 0 or h <= 0:
        raise SystemExit(f"bad --inset {s!r}; width and height must be positive")
    return {"box": (x, y, w, h), "corner": corner, "scale": scale, "text": text}


def bracket_geometry(a, cy_ref, tick, margin_side):
    """Return arm points, spine and stub tip in the supplied coordinate space.

    cy_ref breaks ties when both normals point equally toward the margin.
    """
    (x1, y1), (x2, y2) = a["p1"], a["p2"]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    cands = [(-uy, ux), (uy, -ux)]

    if a.get("arm"):
        want = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[a["arm"]]
        n = max(cands, key=lambda c: c[0] * want[0] + c[1] * want[1])
    else:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        # Prefer the normal pointing toward the label's margin.
        toward = (-1.0, 0.0) if margin_side == "left" else (1.0, 0.0)
        scored = [(c[0] * toward[0] + c[1] * toward[1], c) for c in cands]
        best, second = max(scored)[0], min(scored)[0]
        if abs(best - second) < 1e-6:
            # Break equal outward scores using the image's vertical centre.
            want = (0, -1) if my < cy_ref else (0, 1)
            n = max(cands, key=lambda c: c[0] * want[0] + c[1] * want[1])
        else:
            n = max(scored)[1]

    ax, ay = x1 + n[0] * tick, y1 + n[1] * tick
    bx, by = x2 + n[0] * tick, y2 + n[1] * tick
    smx, smy = (ax + bx) / 2, (ay + by) / 2
    stub = (smx + n[0] * tick * 0.9, smy + n[1] * tick * 0.9)
    return [(x1, y1), (ax, ay), (bx, by), (x2, y2)], ((ax, ay), (bx, by)), stub


def _seg_seg_distance(p1, p2, q1, q2):
    """Return minimum segment distance, or 0.0 when segments cross."""
    def sub(a, b): return (a[0] - b[0], a[1] - b[1])
    def cross(a, b): return a[0] * b[1] - a[1] * b[0]
    def dot(a, b): return a[0] * b[0] + a[1] * b[1]

    r, s = sub(p2, p1), sub(q2, q1)
    denom = cross(r, s)
    if abs(denom) > 1e-12:
        t = cross(sub(q1, p1), s) / denom
        u = cross(sub(q1, p1), r) / denom
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.0

    def pt_seg(p, a, b):
        ab = sub(b, a)
        L2 = dot(ab, ab)
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, dot(sub(p, a), ab) / L2))
        c = (a[0] + ab[0] * t, a[1] + ab[1] * t)
        return math.hypot(p[0] - c[0], p[1] - c[1])

    return min(pt_seg(p1, q1, q2), pt_seg(p2, q1, q2),
               pt_seg(q1, p1, p2), pt_seg(q2, p1, p2))


def leader_conflicts(anns, min_sep):
    """Find leaders that cross or run closer than min_sep.

    Label arrows must never overlap.
    Measure segment distances to detect crowded leaders.
    """
    segs = []
    for x in anns:
        end = {"point": lambda: x["p"], "ring": lambda: x["edge"],
               "bracket": lambda: x["stub"]}[x["kind"]]()
        segs.append((x["text"], x["lead_end"], end))
    out = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            d = _seg_seg_distance(segs[i][1], segs[i][2], segs[j][1], segs[j][2])
            if d < min_sep:
                out.append((segs[i][0], segs[j][0], round(d, 1)))
    return out


def wrap_text(text, width_of, limit):
    """Wrap labels greedily to limit margin width."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and width_of(trial) > limit:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [text]


def assign_slots(items, top, bottom, pad):
    """Place labels near their anchors and separate overlapping blocks."""
    if not items:
        return
    items.sort(key=lambda it: it["tie"][1])
    n = len(items)
    need = sum(it["h"] for it in items) + pad * (n - 1)
    span = bottom - top
    if need > span:
        y = top + items[0]["h"] / 2
        step = (span - need) / max(1, n - 1) if n > 1 else 0
        for i, it in enumerate(items):
            it["ty"] = y
            if i + 1 < n:
                y += it["h"] / 2 + pad + step + items[i + 1]["h"] / 2
        return
    ys = [min(max(it["tie"][1], top + it["h"] / 2), bottom - it["h"] / 2) for it in items]
    for i in range(1, n):
        ys[i] = max(ys[i], ys[i - 1] + items[i - 1]["h"] / 2 + pad + items[i]["h"] / 2)
    if ys[-1] > bottom - items[-1]["h"] / 2:
        ys[-1] = bottom - items[-1]["h"] / 2
        for i in range(n - 2, -1, -1):
            ys[i] = min(ys[i], ys[i + 1] - items[i + 1]["h"] / 2 - pad - items[i]["h"] / 2)
    for it, y in zip(items, ys):
        it["ty"] = y


def tissue_mask(im):
    """Separate saturated or dark regions from a near-white background."""
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    m = (sat > 0.18) | (mx < 0.55)
    m = ndimage.binary_closing(m, np.ones((7, 7)))
    m = ndimage.binary_opening(m, np.ones((5, 5)))
    return m


def anchor_report(im, pts):
    """Report mask membership and distance for each anchor.

    Background anchors produce warnings. Check their intent before rendering.
    Greyscale images return unknown membership because saturation cannot separate regions.
    """
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    mx, mn = a.max(axis=2), a.min(axis=2)
    if float(((mx - mn) / np.maximum(mx, 1e-6)).mean()) < 0.06:
        return [{"label": lbl, "anchor": [int(x), int(y)], "on_tissue": None,
                 "px_to_tissue": None, "local_tissue_pct": None}
                for _, (x, y), lbl in pts]

    m = tissue_mask(im)
    dist = ndimage.distance_transform_edt(~m)
    H, W = m.shape
    out = []
    for name, (x, y), label in pts:
        xi, yi = int(min(max(x, 0), W - 1)), int(min(max(y, 0), H - 1))
        on = bool(m[yi, xi])
        # Measure nearby mask coverage to distinguish holes from distant background.
        r = 24
        win = m[max(0, yi - r):yi + r, max(0, xi - r):xi + r]
        out.append({"label": label, "anchor": [int(x), int(y)], "on_tissue": on,
                    "px_to_tissue": 0 if on else round(float(dist[yi, xi]), 1),
                    "local_tissue_pct": round(float(win.mean()) * 100, 1)})
    return out


def anchor_points(anns):
    """List points, ring centres and both bracket ends."""
    pts = []
    for i, a in enumerate(anns):
        if a["kind"] == "point":
            pts.append((f"{i:02d}-point-{_slug(a['text'])}", a["p"], a["text"]))
        elif a["kind"] == "ring":
            pts.append((f"{i:02d}-ring-{_slug(a['text'])}", a["c"], a["text"] + " [ring centre]"))
        else:
            pts.append((f"{i:02d}-bracketA-{_slug(a['text'])}", a["p1"], a["text"] + " [end A]"))
            pts.append((f"{i:02d}-bracketB-{_slug(a['text'])}", a["p2"], a["text"] + " [end B]"))
    return pts


def write_probe(src, anns, outdir, size=320):
    os.makedirs(outdir, exist_ok=True)
    im = Image.open(src).convert("RGB")
    W, H = im.size
    manifest = []
    pts = anchor_points(anns)
    report = {r["label"]: r for r in anchor_report(im, pts)}
    for name, (x, y), label in pts:
        h = size // 2
        box = (max(0, x - h), max(0, y - h), min(W, x + h), min(H, y + h))
        crop = im.crop(box)
        d = ImageDraw.Draw(crop)
        cx, cy = x - box[0], y - box[1]
        for r, col in ((9, CASING), (7, INK)):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=3 if col == CASING else 2)
        d.line([cx - 22, cy, cx - 12, cy], fill=INK, width=2)
        d.line([cx + 12, cy, cx + 22, cy], fill=INK, width=2)
        p = os.path.join(outdir, name + ".png")
        crop.save(p)
        manifest.append(dict(report[label], file=p))
    print(f"{src} {W}x{H} -> {len(manifest)} probe crops in {outdir}\n")
    print(f"{'anchor':<52} {'on tissue':<10} {'px off':>7}  {'local %':>7}")
    for r in manifest:
        mark = {True: "yes", False: "NO", None: "n/a (greyscale)"}[r["on_tissue"]]
        # Greyscale inputs carry unknown distance and local coverage.
        px = "-" if r["px_to_tissue"] is None else r["px_to_tissue"]
        pct = "-" if r["local_tissue_pct"] is None else r["local_tissue_pct"]
        print(f"{r['label'][:50]:<52} {mark:<10} {px:>7}  {pct:>7}")
    off = [r for r in manifest if r["on_tissue"] is False]
    if off:
        print(f"\n{len(off)} anchor(s) off stained tissue. Intended for a lumen or the "
              f"slide background; a mistake for anything else. Check the crops.")
    print("\n" + json.dumps({"image": src, "size": [W, H], "probes": manifest}, indent=2))


def _slug(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower())[:40].strip("-")


def make_layout(a, cfg):
    """Compute every source and label coordinate once, before any palette paint."""
    anns = ([parse_point(s) for s in a.point] + [parse_bracket(s) for s in a.bracket]
            + [parse_ring(s) for s in a.ring])
    im = Image.open(a.src).convert("RGB")
    W, H = im.size

    # Check anchors on every render so background placement produces a warning.
    for r in anchor_report(im, anchor_points(anns)):
        if r["on_tissue"] is False:
            print(f"WARNING: {r['label']!r} at {tuple(r['anchor'])} is off stained "
                  f"tissue ({r['px_to_tissue']}px to the nearest tissue, "
                  f"{r['local_tissue_pct']}% tissue nearby) - intended only for a "
                  f"lumen or the slide background", file=sys.stderr)

    ps = a.pointsize or max(22, round(W / 46))
    font_cfg = _FontOverride(cfg, a.font) if a.font is not None else cfg
    font, _ = resolve_font(font_cfg, ps, 600)
    cap_ps = max(18, round(ps * 0.85))
    cap_font, _ = resolve_font(font_cfg, cap_ps, 600)

    gap = round(ps * 0.7)
    tick = round(ps * 0.8)
    line_h = round(ps * 1.55)
    dot_r = max(4, round(ps * 0.17))

    meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def tw(s, f=font):
        b = meas.textbbox((0, 0), s, font=f)
        return b[2] - b[0]

    # Assign sides and tie points in source coordinates.
    for ann in anns:
        if ann["kind"] == "point":
            ann["tie"] = ann["p"]
        elif ann["kind"] == "ring":
            ann["tie"] = ann["c"]
        else:
            ann["tie"] = ((ann["p1"][0] + ann["p2"][0]) / 2, (ann["p1"][1] + ann["p2"][1]) / 2)
        if not ann["side"]:
            ann["side"] = "left" if ann["tie"][0] < W / 2 else "right"
        ann["lines"] = wrap_text(ann["text"], tw, a.wrap * W)
        ann["w"] = max(tw(l) for l in ann["lines"])
        ann["h"] = len(ann["lines"]) * line_h

    left = [x for x in anns if x["side"] == "left"]
    right = [x for x in anns if x["side"] == "right"]
    ML = (max((x["w"] for x in left), default=0) + gap * 2) if left else 0
    MR = (max((x["w"] for x in right), default=0) + gap * 2) if right else 0
    MB = round(cap_ps * 1.9) if a.caption else 0
    MT = round(ps * 0.5)

    W2, H2 = ML + W + MR, MT + H + MB

    # Shift each source coordinate into canvas space once.
    for ann in anns:
        ann["tie"] = (ann["tie"][0] + ML, ann["tie"][1] + MT)
        if ann["kind"] == "point":
            ann["p"] = (ann["p"][0] + ML, ann["p"][1] + MT)
        elif ann["kind"] == "ring":
            ann["c"] = (ann["c"][0] + ML, ann["c"][1] + MT)
            # Stop the leader at the ring's edge.
            sgn = -1 if ann["side"] == "left" else 1
            ann["edge"] = (ann["c"][0] + sgn * ann["r"][0], ann["c"][1])
        else:
            ann["p1"] = (ann["p1"][0] + ML, ann["p1"][1] + MT)
            ann["p2"] = (ann["p2"][0] + ML, ann["p2"][1] + MT)

    # Use the bracket stub for label placement and collision measurements.
    for ann in anns:
        if ann["kind"] != "bracket":
            continue
        span = math.dist(ann["p1"], ann["p2"])
        t = min(max(span * 0.30, ps * 0.35), ps * 0.85)
        ann["arms"], spine, ann["stub"] = bracket_geometry(ann, MT + H / 2, t, ann["side"])
        mid = ((spine[0][0] + spine[1][0]) / 2, (spine[0][1] + spine[1][1]) / 2)
        nx, ny = ann["stub"][0] - mid[0], ann["stub"][1] - mid[1]
        nl = math.hypot(nx, ny) or 1.0
        ann["n"] = (nx / nl, ny / nl)
        ann["tie"] = ann["stub"]

    # Separate nearby bracket stubs along their own normals.
    for side in ("left", "right"):
        g = sorted([x for x in anns if x["side"] == side and x["kind"] == "bracket"],
                   key=lambda x: x["tie"][1])
        for i in range(1, len(g)):
            need = line_h * 0.95
            dy = abs(g[i]["tie"][1] - g[i - 1]["tie"][1])
            if dy < need and abs(g[i]["n"][1]) > 0.3:
                push = (need - dy) / abs(g[i]["n"][1])
                g[i]["stub"] = (g[i]["stub"][0] + g[i]["n"][0] * push,
                                g[i]["stub"][1] + g[i]["n"][1] * push)
                g[i]["tie"] = g[i]["stub"]

    assign_slots(left, MT, MT + H, round(ps * 0.55))
    assign_slots(right, MT, MT + H, round(ps * 0.55))

    for ann in anns:
        # Keep the leader outside its text box.
        if ann["side"] == "left":
            ann["tedge"], ann["anchor"] = ML - gap, "rm"
            ann["lead_end"] = (ML - gap + round(ps * 0.25), ann["ty"])
        else:
            ann["tedge"], ann["anchor"] = ML + W + gap, "lm"
            ann["lead_end"] = (ML + W + gap - round(ps * 0.25), ann["ty"])

    clashes = leader_conflicts(anns, ps * 0.55)
    for t1, t2, dsep in clashes:
        how = "CROSS" if dsep == 0.0 else f"run {dsep}px apart"
        print(f"WARNING: leaders for {t1!r} and {t2!r} {how} - move an anchor, force a "
              f"side with @left/@right, or stagger the brackets", file=sys.stderr)

    def render(colours=None, key=False):
        canvas = Image.new("RGB", (W2, H2), colours["margin"] if colours else a.margin_fill)
        canvas.paste(im, (ML, MT))
        # Draw insets before leaders so leaders remain visible.
        ov = Image.new("RGBA", (W2 * SS, H2 * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)

        def line(p, q, width, colour):
            d.line([p[0] * SS, p[1] * SS, q[0] * SS, q[1] * SS], fill=colour,
                   width=max(1, round(width * SS)), joint="curve")

        def rect(box, width, colour):
            d.rectangle([box[0] * SS, box[1] * SS, box[2] * SS, box[3] * SS],
                        outline=colour, width=max(1, round(width * SS)))

        insets = [parse_inset(s) for s in a.inset]
        placed = []
        for ins in insets:
            x, y, w, h = ins["box"]
            x, y = max(0, x), max(0, y)
            w, h = min(w, W - x), min(h, H - y)
            crop = im.crop((x, y, x + w, y + h))
            sw, sh = round(w * ins["scale"]), round(h * ins["scale"])
            sw, sh = min(sw, W - 2 * gap), min(sh, H - 2 * gap)
            crop = crop.resize((sw, sh), Image.LANCZOS)
            pad = round(ps * 0.6)
            px = ML + pad if "left" in ins["corner"] else ML + W - sw - pad
            py = MT + pad if "top" in ins["corner"] else MT + H - sh - pad
            canvas.paste(crop, (px, py))
            placed.append({"src": (ML + x, MT + y, ML + x + w, MT + y + h),
                           "dst": (px, py, px + sw, py + sh), "text": ins["text"]})

        bw = max(2, round(ps * 0.09))
        cas = max(2, round(ps * 0.09))
        for width, colour in ((bw + cas, colours["casing"] if colours else a.casing), (bw, colours["inset"] if colours else a.ink)):
            for pl in placed:
                rect(pl["src"], width, colour)
                rect(pl["dst"], width, colour)
                sx0, sy0, sx1, sy1 = pl["src"]
                dx0, dy0, dx1, dy1 = pl["dst"]
                # Join the nearest corner pairs to identify the magnified region.
                if dx0 > sx1:
                    line((sx1, sy0), (dx0, dy0), width, colour)
                    line((sx1, sy1), (dx0, dy1), width, colour)
                elif dx1 < sx0:
                    line((sx0, sy0), (dx1, dy0), width, colour)
                    line((sx0, sy1), (dx1, dy1), width, colour)

        lw = max(2, round(ps * 0.085))
        for width, colour in ((lw + max(2, round(ps * 0.09)), colours["casing"] if colours else a.casing), (lw, None)):
            for ann in anns:
                colour = (colours[ann["kind"]] if colours else a.ink) if width == lw else colour
                if ann["kind"] == "point":
                    line(ann["lead_end"], ann["p"], width, colour)
                    r = dot_r
                    d.ellipse([(ann["p"][0] - r) * SS, (ann["p"][1] - r) * SS,
                               (ann["p"][0] + r) * SS, (ann["p"][1] + r) * SS],
                              outline=colour, width=max(1, round(width * SS)))
                elif ann["kind"] == "ring":
                    line(ann["lead_end"], ann["edge"], width, colour)
                    (cx, cy), (rx, ry) = ann["c"], ann["r"]
                    d.ellipse([(cx - rx) * SS, (cy - ry) * SS,
                               (cx + rx) * SS, (cy + ry) * SS],
                              outline=colour, width=max(1, round(width * 1.25 * SS)))
                else:
                    # Draw bracket spans heavier than their leaders.
                    for p, q in zip(ann["arms"], ann["arms"][1:]):
                        line(p, q, width * 1.7, colour)
                    line(ann["stub"], ann["lead_end"], width, colour)

        ov = ov.resize((W2, H2), Image.LANCZOS)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), ov).convert("RGB")

        # Draw text last at full resolution.
        dt = ImageDraw.Draw(canvas)
        stroke = max(2, round(ps * 0.13))
        for ann in anns:
            y0 = ann["ty"] - (len(ann["lines"]) - 1) * line_h / 2
            for i, ln in enumerate(ann["lines"]):
                dt.text((ann["tedge"], y0 + i * line_h), ln, font=font, fill=colours[ann["kind"]] if colours else a.ink,
                        anchor=ann["anchor"], stroke_width=stroke, stroke_fill=colours["casing"] if colours else a.casing)
        for pl in placed:
            if pl["text"]:
                dt.text((pl["dst"][0] + gap // 2, pl["dst"][1] + gap // 2), pl["text"],
                        font=font, fill=colours["inset"] if colours else a.ink, anchor="lt",
                        stroke_width=stroke, stroke_fill=colours["casing"] if colours else a.casing)
        if a.caption:
            dt.text((gap, MT + H + MB / 2), a.caption, font=cap_font, fill=colours["caption"] if colours else "#FFFFFF",
                    anchor="lm", stroke_width=max(2, stroke - 1), stroke_fill=colours["casing"] if colours else a.casing)

        if key and colours:
            canvas = paint_key(canvas, anns, a.caption, cap_font, gap, MT + H, MB, colours)
        return canvas
    return {"paint": render, "width": W2, "height": H2, "annotations": anns,
            "source": (W, H), "margins": (ML, MR, MT, MB), "pointsize": ps}


def paint(layout, colours=None, key=False):
    return layout["paint"](colours, key)


def paint_key(canvas, anns, caption, font, gap, image_bottom, margin_bottom, colours):
    kinds = [k for k in ("point", "ring", "bracket") if any(a["kind"] == k for a in anns)]
    if len(kinds) < 2:
        return canvas
    labels = {"point": "point: structure", "ring": "ring: whole round structure", "bracket": "bracket: layer or span"}
    draw = ImageDraw.Draw(canvas)
    row = max(26, round(getattr(font, "size", 18) * 1.9))
    widths = [round(draw.textlength(labels[k], font=font)) + row + gap for k in kinds]
    caption_width = round(draw.textlength(caption or "", font=font)) + 2 * gap
    inline = margin_bottom >= row and caption_width + sum(widths) + gap <= canvas.width
    if inline:
        x, y = canvas.width - sum(widths) - gap, image_bottom + margin_bottom / 2
    else:
        # Wrap the key into rows of its own. Only palette copies grow.
        rows, width = 1, gap
        for size in widths:
            if width + size > canvas.width and width > gap:
                rows, width = rows + 1, gap
            width += size
        extended = Image.new("RGB", (canvas.width, canvas.height + row * rows), colours["margin"])
        extended.paste(canvas, (0, 0))
        x, y = gap, canvas.height + row / 2
        canvas, draw = extended, ImageDraw.Draw(extended)
    for kind, size in zip(kinds, widths):
        if not inline and x + size > canvas.width and x > gap:
            x, y = gap, y + row
        cx, radius = x + row / 3, max(3, row / 7)
        ink = colours[kind]
        if kind == "point":
            draw.ellipse((cx-radius-2, y-radius-2, cx+radius+2, y+radius+2), fill=colours["casing"])
            draw.ellipse((cx-radius, y-radius, cx+radius, y+radius), fill=ink)
        elif kind == "ring":
            draw.ellipse((cx-radius-2, y-radius-2, cx+radius+2, y+radius+2), outline=colours["casing"], width=6)
            draw.ellipse((cx-radius, y-radius, cx+radius, y+radius), outline=ink, width=2)
        else:
            points = [(cx+radius,y-radius),(cx-radius,y-radius),(cx-radius,y+radius),(cx+radius,y+radius)]
            draw.line(points, fill=colours["casing"], width=6)
            draw.line(points, fill=ink, width=2)
        draw.text((x+row, y), labels[kind], font=font, fill=colours["caption"], anchor="lm",
                  stroke_width=2, stroke_fill=colours["casing"])
        x += size
    return canvas


def owns_copy(path, manifest):
    if not path.exists():
        return True
    if path.is_symlink():
        return False
    for record in (manifest or {}).get("copies", {}).values():
        if record.get("file") == path.name and record.get("sha256") == palettes.file_sha256(path):
            return True
    return False


def write_manifest(path, value):
    # Replacing the manifest invalidates any old sibling that was skipped.
    import tempfile
    manifest = palettes.copies_manifest(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=manifest.parent,
                                     prefix=".lt-copies-", suffix=".tmp", delete=False, newline="\n") as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, indent=2) + "\n")
    try:
        temporary.replace(manifest)
    finally:
        temporary.unlink(missing_ok=True)


def file_copies(source, destination, overwrite=False):
    manifest = palettes.read_copies(source)
    if not manifest or not source.is_file() or palettes.file_sha256(source) != manifest.get("upload", {}).get("sha256"):
        print("WARNING: --file-copies source upload does not match its manifest", file=sys.stderr)
        return 1
    previous = palettes.read_copies(destination)
    result = {"upload": manifest["upload"], "media_sha256": palettes.file_sha256(destination) if destination.is_file() else None,
              "copies": {}}
    for pid, record in manifest["copies"].items():
        if not palettes.valid_palette_id(pid):
            print("WARNING: --file-copies refused invalid palette id", file=sys.stderr)
            continue
        sibling = palettes.bound_copy(source, pid)
        if sibling is None:
            print(f"WARNING: --file-copies {pid}: copy does not match its manifest", file=sys.stderr)
            continue
        target = destination.with_name(f"{destination.stem}-{pid}{destination.suffix}")
        if target.is_symlink() or (not overwrite and not owns_copy(target, previous)):
            print(f"WARNING: --file-copies kept unowned {target.name}; pass --overwrite", file=sys.stderr)
            continue
        if sibling.resolve() != target.resolve():
            shutil.copyfile(sibling, target)
        result["copies"][pid] = {"file": target.name, "sha256": palettes.file_sha256(target), "print": bool(record.get("print"))}
    write_manifest(destination, result)
    return 0


def main() -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()

    global Image, ImageDraw, ImageFont, np, ndimage, CASING, INK, MARGIN_FILL

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--config", metavar="PATH")
    ap.add_argument("--help-rules", action="store_true",
                    help="print configured label naming rules and exit")
    ap.add_argument("--point", action="append", default=[])
    ap.add_argument("--bracket", action="append", default=[])
    ap.add_argument("--ring", action="append", default=[],
                    help="text:cx,cy:rx,ry - outline a whole round structure")
    ap.add_argument("--inset", action="append", default=[])
    ap.add_argument("--caption", default=None)
    ap.add_argument("--pointsize", type=int, default=None,
                    help="override the automatic size (image width / 46)")
    ap.add_argument("--wrap", type=float, default=0.30, metavar="FRAC",
                    help="wrap labels beyond this fraction of the image width "
                         "(default 0.30)")
    ap.add_argument("--ink", default=None,
                    help="override annotate.ink")
    ap.add_argument("--casing", default=None,
                    help="override annotate.casing")
    ap.add_argument("--margin-fill", default=None,
                    help="override annotate.margin_fill")
    ap.add_argument("--font", default=None,
                    help="override annotate.font_path")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--probe", metavar="DIR",
                    help="write a crop around every anchor and render nothing")
    ap.add_argument("--palette", help="comma-separated lt and palette ids")
    ap.add_argument("--copy-max", type=int, default=1920)
    ap.add_argument("--file-copies", action="store_true", help="file verified copies beside captured media; render nothing")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 3

    if args.help_rules:
        rules = cfg.get("annotate.naming_rules")
        if rules:
            for rule in rules:
                print(rule)
        else:
            print("no naming rules configured")
        return 0

    if not args.src:
        ap.error("src is required unless --help-rules is given")

    if args.file_copies:
        if not args.dst:
            ap.error("--file-copies requires src and dst")
        return file_copies(Path(args.src), Path(args.dst), args.overwrite)
    if args.copy_max < 0:
        ap.error("--copy-max must be nonnegative")

    missing = []
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        missing.append("PIL")
    try:
        import numpy as np
    except ImportError:
        missing.append("numpy")
    try:
        from scipy import ndimage
    except ImportError:
        missing.append("scipy")
    if missing:
        print(ltcore.missing_hint(missing), file=sys.stderr)
        return 3

    CASING = cfg.get("annotate.casing")
    INK = cfg.get("annotate.ink")
    MARGIN_FILL = cfg.get("annotate.margin_fill")
    a = args
    if a.ink is None:
        a.ink = INK
    if a.casing is None:
        a.casing = CASING
    if a.margin_fill is None:
        a.margin_fill = MARGIN_FILL

    anns = ([parse_point(s) for s in a.point]
            + [parse_bracket(s) for s in a.bracket]
            + [parse_ring(s) for s in a.ring])
    if not anns and not a.inset:
        raise SystemExit("nothing to draw: pass --point, --bracket or --inset")

    if a.probe:
        write_probe(a.src, anns, a.probe)
        return 0
    if not a.dst:
        raise SystemExit("dst is required unless --probe is given")

    layout = make_layout(a, cfg)
    destination = Path(a.dst)
    previous = palettes.read_copies(destination)
    canvas = paint(layout)
    canvas.save(destination, quality=a.quality, subsampling=0)
    manifest = {"upload": {"file": destination.name, "sha256": palettes.file_sha256(destination)},
                "media_sha256": None, "copies": {}}
    ids = a.palette.split(",") if a.palette is not None else (
        ["lt"] + palettes.pdf_palette_ids(cfg) if cfg.get("annotate.palette_copies", True) else ["lt"])
    printp = cfg.get("style.print_palette") or next(iter(palettes.pdf_palette_ids(cfg)), "")
    for pid in dict.fromkeys(ids):
        if pid == "lt":
            continue
        try:
            colours = palettes.micrograph_colours(pid, cfg, print_copy=pid == printp)
            target = destination.with_name(f"{destination.stem}-{pid}{destination.suffix}")
            if target.is_symlink() or (not a.overwrite and not owns_copy(target, previous)):
                print(f"WARNING: kept unowned {target.name}; pass --overwrite", file=sys.stderr)
                continue
            copy = paint(layout, colours, key=True)
            if a.copy_max and max(copy.size) > a.copy_max:
                copy.thumbnail((a.copy_max, a.copy_max), Image.Resampling.LANCZOS)
            copy.save(target, quality=a.quality, subsampling=0)
            manifest["copies"][pid] = {"file": target.name, "sha256": palettes.file_sha256(target), "print": pid == printp}
        except (palettes.PaletteError, OSError, ValueError) as exc:
            print(f"WARNING: palette {pid}: {exc}", file=sys.stderr)
    leftovers = set()
    for pid in palettes.pdf_palette_ids(cfg):
        if pid in manifest["copies"]:
            continue
        name = f"{destination.stem}-{pid}{destination.suffix}"
        if Path(name).name == name and (destination.parent / name).exists():
            leftovers.add(name)
    for pid, record in (previous or {}).get("copies", {}).items():
        name = record.get("file", "")
        if pid not in manifest["copies"] and name and Path(name).name == name and (destination.parent / name).exists():
            leftovers.add(name)
    for name in sorted(leftovers):
        print(f"WARNING: {name} left unbound; use --file-copies after rendering", file=sys.stderr)
    write_manifest(destination, manifest)
    print(f"{a.dst} {canvas.width}x{canvas.height} pointsize {layout['pointsize']} {destination.stat().st_size}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
