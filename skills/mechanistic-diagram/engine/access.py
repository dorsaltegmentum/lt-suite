#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Accessibility checks of the configured palettes (style.palettes) and rendered SVGs: WCAG contrast, colour-vision
simulation, and the arrow kinds' non-colour cues.

    python3 access.py <case_dir> [--no-sims] [--quiet] [--strict]

Exits 1 only on a contrast failure; everything else is a report. A pair below its minimum fails
unless floor.json "contrast_exceptions" records the pair as a known shortfall.
The ratio must be no lower than recorded. Rendered text must be no smaller than min_px,
the smallest recorded text size for that pair. These shortfalls are reported as KNOWN.
Any new, worse or smaller use fails.
--strict fails on the recorded ones too.
The recorded exceptions keep known contrast shortfalls visible during checks.
Changing an exception requires a palette configuration change.

1. Contrast (WCAG 2.2 relative luminance), in each palette the build renders
   from the configured palettes (style.palettes):
     text pairs   role text on each role fill, stage title on container fill, key heading and key text
                  on the key panel, header text and muted text on the canvas         need >= 4.5 (1.4.3)
     arrows       each arrow kind's colour on the canvas and on the container fill   need >= 3.0 (1.4.11)
     info only    box border on container fill, and role fill on container fill (how a box separates
                  from its stage); reported, never gating
   and the rendered final-<flavour>.svg itself: every <text> is measured against what is painted
   under it (the rects, circles and ellipses drawn before it, composited in order, with their
   opacity), so a layer added later is checked without a new table entry. Normal text needs 4.5,
   large text (24 px, or 18.66 px at weight 700 or more) 3.0. Text over an <image> or an embedded
   <svg> is listed as not measured.
2. Colour-vision simulation: Machado, Oliveira and Fernandes (2009) at severity 1.0 for protanopia,
   deuteranopia and tritanopia, applied in linear sRGB, plus greyscale (Rec. 709 luminance). Every
   pair of role fills and every pair of arrow colours is compared in OKLab (deltaE_OK). A pair is
   "indistinguishable" below 0.02, the just-noticeable difference CSS Color 4 uses for OKLab, and
   "collapses" below 0.05 (2.5 JND): boxes of two roles are compared across the page, not side by
   side, so they need a margin of several JNDs to be told apart. Box borders are reported beside
   the fills, since a role is drawn as fill plus border.
3. Arrow cues: two arrow kinds are told apart without colour only if their dash pattern or head
   differs (themes.THEMES edges: colour, dashed, head). Kinds that share both are listed with their
   colour difference under each simulation and with the context the diagram gives them (the roles
   their arrows start from, from geometry.json).
4. Simulated images: <case_dir>/checks/cvd/final-<print palette>-{protan,deutan,tritan,grey}.png,
   rewritten only when that PNG changes (checks/cvd/source.sha256).
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent

TEXT_MIN, LARGE_MIN, ARROW_MIN = 4.5, 3.0, 3.0
JND_OK, COLLAPSE_OK = 0.02, 0.05
SIMS = ("protan", "deutan", "tritan", "grey")

# Machado, Oliveira & Fernandes (2009), IEEE TVCG 15(6), severity 1.0; rows act on linear RGB
MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}
NAMED = {"white": "#ffffff", "black": "#000000", "none": None, "transparent": None}


# ------------------------------------------------------------------------------------------ colour maths
def hex_rgb(h):
    """'#rrggbb', '#rgb' or 'rgb(r, g, b)' -> (r, g, b) in 0..1; None for none/transparent/unknown."""
    if h is None:
        return None
    h = NAMED.get(h.strip().lower(), h.strip())
    if h is None:
        return None
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", h)
    if m:
        return tuple(float(m.group(i)) / 255 for i in (1, 2, 3))
    if not h.startswith("#"):
        return None
    h = h[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def to_hex(rgb):
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02x}" for c in rgb)


def lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def unlin(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def luminance(rgb):
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def oklab(rgb):
    r, g, b = (lin(c) for c in rgb)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def de_ok(a, b):
    return sum((x - y) ** 2 for x, y in zip(oklab(a), oklab(b))) ** 0.5


def simulate(rgb, kind):
    """The colour as seen with the given deficiency ('none' returns it unchanged)."""
    if kind == "none":
        return tuple(rgb)
    r, g, b = (lin(c) for c in rgb)
    if kind == "grey":
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return (unlin(y),) * 3
    M = MACHADO[kind]
    return tuple(unlin(M[i][0] * r + M[i][1] * g + M[i][2] * b) for i in range(3))


def over(fg, bg, alpha):
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))


# ------------------------------------------------------------------------------------------ theme pairs
def theme_pairs(T, role_name=None):
    """[(group, label, fg hex, bg hex, need or None)] for one theme."""
    names = role_name or {}
    out = []
    for r, (fill, border, text) in T["roles"].items():
        out.append(("text", f"box text on {names.get(r, r)} fill", text, fill, TEXT_MIN))
    out += [("text", "stage title on container fill", T["cl_text"], T["cl_fill"], TEXT_MIN),
            ("text", "key heading on key panel", T["cl_text"], T["key_fill"], TEXT_MIN),
            ("text", "key text on key panel", T["ink"], T["key_fill"], TEXT_MIN),
            ("text", "header text on canvas", T["ink"], T["canvas"], TEXT_MIN),
            ("text", "muted text on canvas", T["muted"], T["canvas"], TEXT_MIN)]
    for kind, (colour, dashed, head) in T["edges"].items():
        out.append(("arrow", f"{kind} arrow on canvas", colour, T["canvas"], ARROW_MIN))
        out.append(("arrow", f"{kind} arrow on container fill", colour, T["cl_fill"], ARROW_MIN))
    for r, (fill, border, text) in T["roles"].items():
        out.append(("info", f"{names.get(r, r)} border on container fill", border, T["cl_fill"], None))
    return out


def load_exceptions(path=None):
    """The configured palettes' known shortfalls, from floor.json ("contrast_exceptions"): a list of
    dict(theme, fg, bg, ratio, what). A pair below its WCAG minimum that is listed here, at no lower
    a ratio than recorded, is reported as KNOWN instead of failing; any other shortfall fails."""
    if path is None:
        import floor
        path = floor.floor_path()
    p = pathlib.Path(path)
    try:
        return json.loads(p.read_text()).get("contrast_exceptions", [])
    except (OSError, ValueError):
        return []


def _known(exceptions, theme, fg, bg, ratio, px=None):
    """Does this shortfall match a recorded pair with no lower a ratio and, for
    rendered text, no smaller a size than the smallest recorded text for that pair?"""
    fg, bg = (fg or "").lower(), (bg or "").lower()
    return any(x["theme"] == theme and x["fg"].lower() == fg and x["bg"].lower() == bg and ratio >= x["ratio"] - 0.005
               and (px is None or not x.get("min_px") or px >= x["min_px"] - 0.01)
               for x in exceptions)


def check_theme(T, role_name=None):
    rows = []
    for group, label, fg, bg, need in theme_pairs(T, role_name):
        ratio = contrast(hex_rgb(fg), hex_rgb(bg))
        rows.append(dict(group=group, label=label, fg=fg, bg=bg, ratio=ratio, need=need,
                         ok=True if need is None else ratio >= need))
    return rows


# ------------------------------------------------------------------------------------------ SVG scan
SVG_NS = "{http://www.w3.org/2000/svg}"
NOT_PAINTED = {"defs", "clipPath", "mask", "pattern", "symbol", "linearGradient", "radialGradient", "filter",
               "marker", "title", "desc", "metadata", "style"}


def _style(el):
    st = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    for k in ("fill", "fill-opacity", "opacity", "font-size", "font-weight", "font-family", "text-anchor",
              "visibility", "display"):
        if el.get(k) is not None and k not in st:
            st[k] = el.get(k)
    return st


def _translate(el):
    t = el.get("transform") or ""
    if not t:
        return 0.0, 0.0, True
    m = re.fullmatch(r"\s*translate\(\s*([-\d.e]+)(?:[\s,]+([-\d.e]+))?\s*\)\s*", t)
    if m:
        return float(m.group(1)), float(m.group(2) or 0), True
    return 0.0, 0.0, False        # a transform this scan does not model: the element is skipped


def _num(v, default=0.0):
    try:
        return float(re.match(r"[-\d.e]+", str(v)).group(0))
    except (AttributeError, ValueError, TypeError):
        return default


def scan_svg(svg_path):
    """Every <text> in the SVG with the colour painted under it: [dict(text, x, y, fs, weight, fg, bg,
    ratio, need, status)]; status 'ok', 'FAIL' or 'not measured (...)'."""
    root = ET.parse(svg_path).getroot()
    W, H = _num(root.get("width"), 0), _num(root.get("height"), 0)
    painted = []          # (x0, y0, x1, y1, rgb or None for an image, alpha)
    texts = []

    def walk(el, inh, dx, dy):
        tag = el.tag.replace(SVG_NS, "")
        if tag in NOT_PAINTED:
            return
        st = dict(inh)
        own = _style(el)
        if own.get("display") == "none" or own.get("visibility") == "hidden":
            return
        st.update({k: v for k, v in own.items() if k != "opacity"})
        st["opacity"] = inh.get("opacity", 1.0) * _num(own.get("opacity", 1), 1.0)
        tx, ty, simple = _translate(el)
        if not simple:
            return
        dx, dy = dx + tx, dy + ty
        if tag == "svg" and el is not root or tag == "image":
            x, y = _num(el.get("x")) + dx, _num(el.get("y")) + dy
            painted.append((x, y, x + _num(el.get("width")), y + _num(el.get("height")), None, 1.0))
            return
        if tag in ("rect", "circle", "ellipse"):
            rgb = hex_rgb(st.get("fill", "#000000"))
            if rgb is not None:
                if tag == "rect":
                    x, y = _num(el.get("x")) + dx, _num(el.get("y")) + dy
                    box = (x, y, x + _num(el.get("width")), y + _num(el.get("height")))
                else:
                    cx, cy = _num(el.get("cx")) + dx, _num(el.get("cy")) + dy
                    rx = _num(el.get("r") or el.get("rx"))
                    ry = _num(el.get("r") or el.get("ry"))
                    box = (cx - rx * 0.7, cy - ry * 0.7, cx + rx * 0.7, cy + ry * 0.7)    # the inscribed square
                alpha = st["opacity"] * _num(st.get("fill-opacity", 1), 1.0)
                painted.append(box + (rgb, alpha))
            return
        if tag == "text":
            content = "".join(el.itertext()).strip()
            if content:
                fs = _num(st.get("font-size", 16), 16)
                texts.append(dict(text=content, x=_num(el.get("x")) + dx, y=_num(el.get("y")) + dy, fs=fs,
                                  weight=int(_num(st.get("font-weight", 400) if str(st.get("font-weight")) != "bold" else 700, 400)),
                                  mono="mono" in (st.get("font-family") or "").lower(),
                                  anchor=st.get("text-anchor", "start"), fill=st.get("fill", "#000000"),
                                  alpha=st["opacity"] * _num(st.get("fill-opacity", 1), 1.0), under=len(painted)))
            return
        for ch in el:
            walk(ch, st, dx, dy)

    walk(root, {"opacity": 1.0}, 0.0, 0.0)
    out = []
    for t in texts:
        w = (0.6 if t["mono"] else 0.55) * t["fs"] * len(t["text"])
        x0 = t["x"] - (w / 2 if t["anchor"] == "middle" else w if t["anchor"] == "end" else 0)
        ym = t["y"] - 0.35 * t["fs"]
        fg = hex_rgb(t["fill"])
        large = t["fs"] >= 24 or (t["fs"] >= 18.66 and t["weight"] >= 700)
        need = LARGE_MIN if large else TEXT_MIN
        rec = dict(text=t["text"], x=t["x"], y=t["y"], fs=t["fs"], weight=t["weight"], fg=t["fill"], need=need)
        if fg is None:
            out.append(dict(rec, bg=None, ratio=None, status="not measured (no fill colour)"))
            continue
        worst = None
        for px in (x0 + 0.1 * w, x0 + 0.5 * w, x0 + 0.9 * w):
            bg, image = (1.0, 1.0, 1.0), False
            for (a, b, c, d, rgb, alpha) in painted[:t["under"]]:
                if a <= px <= c and b <= ym <= d:
                    if rgb is None:
                        image = True
                    else:
                        bg = over(rgb, bg, alpha)
                        image = image and alpha < 1
            if image:
                worst = ("image", None, None)
                break
            col = over(fg, bg, t["alpha"])
            r = contrast(col, bg)
            if worst is None or r < worst[1]:
                worst = ("ok", r, bg)
        if worst[0] == "image":
            out.append(dict(rec, bg=None, ratio=None, status="not measured (over an image)"))
        else:
            out.append(dict(rec, bg=to_hex(worst[2]), ratio=worst[1], status="ok" if worst[1] >= need else "FAIL"))
    return out, (W, H)


# ------------------------------------------------------------------------------------------ CVD
def _pairs(items):
    keys = list(items)
    return [(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]]


def cvd(T):
    """{'roles': [...], 'arrows': [...]} with deltaE_OK of every pair under each simulation."""
    fills = {r: hex_rgb(v[0]) for r, v in T["roles"].items()}
    borders = {r: hex_rgb(v[1]) for r, v in T["roles"].items()}
    arrows = {}
    for kind, (colour, dashed, head) in T["edges"].items():
        arrows.setdefault(colour, []).append(kind)
    acol = {"/".join(kinds): hex_rgb(c) for c, kinds in arrows.items()}
    res = {"roles": [], "arrows": []}
    for a, b in _pairs(fills):
        row = dict(a=a, b=b)
        for s in ("none",) + SIMS:
            row[s] = (de_ok(simulate(fills[a], s), simulate(fills[b], s)),
                      de_ok(simulate(borders[a], s), simulate(borders[b], s)))
        res["roles"].append(row)
    for a, b in _pairs(acol):
        row = dict(a=a, b=b)
        for s in ("none",) + SIMS:
            row[s] = de_ok(simulate(acol[a], s), simulate(acol[b], s))
        res["arrows"].append(row)
    return res


def arrow_cues(T, geo=None):
    """Arrow kinds that share dash pattern and head, so only colour (or context) tells them apart."""
    kinds = T["edges"]
    out = []
    for a, b in _pairs(kinds):
        ca, cb = kinds[a], kinds[b]
        if (ca[1], ca[2]) == (cb[1], cb[2]):
            row = dict(a=a, b=b, cue=f"{'dashed' if ca[1] else 'solid'}, {ca[2]} head", same_colour=ca[0] == cb[0])
            row["de"] = {s: de_ok(simulate(hex_rgb(ca[0]), s), simulate(hex_rgb(cb[0]), s)) for s in ("none",) + SIMS}
            if geo:
                ctx = {}
                for k in (a, b):
                    src = sorted({(geo["nodes"][e["a"]]["role"], geo["nodes"][e["a"]]["cluster"])
                                  for e in geo["edges"] if e["kind"] == k})
                    ctx[k] = src
                row["context"] = ctx
            out.append(row)
    return out


def _cued(T, row):
    """Do the arrow kinds of a colour pair (cvd()['arrows'] keys such as 'stim/excl') all differ in dash or head?"""
    cue = lambda k: tuple(T["edges"][k][1:])
    return all(cue(a) != cue(b) for a in row["a"].split("/") for b in row["b"].split("/"))


# ------------------------------------------------------------------------------------------ images
def simulate_png(src, out_dir):
    """The print palette's PNG as seen with each deficiency and in greyscale; skipped when unchanged."""
    import numpy as np
    from PIL import Image
    src, out_dir = pathlib.Path(src), pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    stamp = out_dir / "source.sha256"
    outs = [out_dir / f"{src.stem}-{s}.png" for s in SIMS]
    if stamp.exists() and stamp.read_text().split()[0] == digest and all(p.exists() for p in outs):
        return outs, False
    rgba = np.asarray(Image.open(src).convert("RGBA"))        # the page PNG may have transparent rounded corners
    im, alpha = rgba[..., :3], rgba[..., 3:]
    lut = np.array([lin(i / 255) for i in range(256)], dtype=np.float32)
    enc = np.array([round(unlin(i / 4095) * 255) for i in range(4096)], dtype=np.uint8)
    L = lut[im].reshape(-1, 3)
    for s, p in zip(SIMS, outs):
        if s == "grey":
            y = L @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
            sim = np.repeat(y[:, None], 3, axis=1)
        else:
            sim = L @ np.array(MACHADO[s], dtype=np.float32).T
        q = np.clip(sim * 4095 + 0.5, 0, 4095).astype(np.int32)
        Image.fromarray(np.concatenate([enc[q].reshape(im.shape), alpha], axis=2), "RGBA").save(p, compress_level=3)
    stamp.write_text(f"{digest}  {src.name}\n")
    return outs, True


# ------------------------------------------------------------------------------------------ run
def run(case_dir, sims=True, quiet=False, flavours=None, strict=False, exceptions=None):
    """All checks. Returns dict(ok, fails, known, min_text, min_arrow, lines, shortfalls). ok is False only
    on a contrast failure: a pair below its minimum that is not one of the configured palettes' recorded
    shortfalls (floor.json), or is lower than recorded. strict=True fails on every shortfall."""
    import themes
    from themes import THEMES, ROLE_NAME
    flavours = tuple(flavours or themes.final_palettes())
    d = pathlib.Path(case_dir).resolve()
    exc = [] if strict else (load_exceptions() if exceptions is None else exceptions)
    lines, fails, known, shortfalls = [], [], [], []
    geo_p = d / "geometry.json"
    geo = json.loads(geo_p.read_text()) if geo_p.exists() else None
    min_text = min_arrow = None
    say = lines.append
    short = {"risk": "risk", "mech": "mech", "anat": "anat", "sign": "sign", "inv": "inv", "dx": "dx", "tx": "tx"}

    def judge(fl, what, fg, bg, ratio, need, px=None):
        shortfalls.append(dict(theme=fl, fg=fg, bg=bg, ratio=round(ratio, 4), what=what, need=need, px=px))
        if _known(exc, fl, fg, bg, ratio, px):
            known.append(f"{fl}: {what} {ratio:.2f} < {need} (configured palette, recorded in floor.json)")
            return "KNOWN"
        fails.append(f"{fl}: {what} {ratio:.2f} < {need}")
        return "FAIL"

    for fl in flavours:
        T = THEMES[fl]
        say(f"contrast, {fl} palette:")
        for r in check_theme(T, ROLE_NAME):
            if r["need"] is None:
                flag = "info, under 3" if r["ratio"] < 3 else "info"
            elif r["ok"]:
                flag = "ok"
            else:
                flag = judge(fl, r["label"], r["fg"], r["bg"], r["ratio"], r["need"])
            need = f">= {r['need']}" if r["need"] else ""
            say(f"  {r['label']:46s} {r['fg']} on {r['bg']}  {r['ratio']:5.2f}  {need:6s} {flag}")
            if r["group"] == "text":
                min_text = r["ratio"] if min_text is None else min(min_text, r["ratio"])
            if r["group"] == "arrow":
                min_arrow = r["ratio"] if min_arrow is None else min(min_arrow, r["ratio"])
        svg = d / f"final-{fl}.svg"
        if svg.exists():
            texts, _ = scan_svg(svg)
            meas = [t for t in texts if t["ratio"] is not None]
            unmeasured = [t for t in texts if t["ratio"] is None]
            low = min(meas, key=lambda t: t["ratio"]) if meas else None
            below = [t for t in meas if t["ratio"] < t["need"]]
            verdicts = [judge(fl, f"rendered text {t['text'][:40]!r} ({t['fs']:g} px)", t["fg"], t["bg"], t["ratio"],
                              t["need"], t["fs"]) for t in below]
            say(f"  rendered {svg.name}: {len(texts)} text runs, {len(meas)} measured, lowest "
                + (f"{low['ratio']:.2f} ({low['text'][:40]!r}, {low['fg']} on {low['bg']})" if low else "n/a")
                + f"; {verdicts.count('FAIL')} fail, {verdicts.count('KNOWN')} known"
                + (f", {len(unmeasured)} not measured" if unmeasured else ""))
            for t, v in zip(below, verdicts):
                say(f"    {v} {t['ratio']:.2f} < {t['need']}: {t['text'][:60]!r} at ({t['x']:.0f}, {t['y']:.0f}) "
                    f"{t['fg']} on {t['bg']}, {t['fs']:g} px")
            for t in unmeasured[:10]:
                say(f"    {t['status']}: {t['text'][:60]!r} at ({t['x']:.0f}, {t['y']:.0f})")
            if low:
                min_text = min(min_text, low["ratio"])
        res = cvd(T)
        say(f"colour vision, {fl} (deltaE_OK of fill / border; collapses under {COLLAPSE_OK}, "
            f"indistinguishable under {JND_OK}):")
        for s in SIMS:
            coll = sorted((r for r in res["roles"] if r[s][0] < COLLAPSE_OK), key=lambda r: r[s][0])
            both = [r for r in coll if r[s][1] < COLLAPSE_OK]
            say(f"  {s:7s} role fills: {len(coll)} of {len(res['roles'])} pairs collapse, {len(both)} with their borders too"
                + (": " + ", ".join(f"{short[r['a']]}/{short[r['b']]} {r[s][0]:.3f}/{r[s][1]:.3f}" for r in coll) if coll else ""))
            acoll = [r for r in res["arrows"] if r[s] < COLLAPSE_OK]
            say(f"  {s:7s} arrow colours: " + ("collapse for " + ", ".join(
                f"{r['a']} / {r['b']} {r[s]:.3f}" + (" (dash or head still tells them apart)" if _cued(T, r) else
                                                      " (NO other cue)") for r in acoll)
                if acoll else f"every pair at least {COLLAPSE_OK}"))
        for c in arrow_cues(T, geo):
            de = ", ".join(f"{s} {v:.3f}" for s, v in c["de"].items())
            say(f"  arrow cue: {c['a']} and {c['b']} share their non-colour cue ({c['cue']}); "
                + ("same colour" if c["same_colour"] else f"colour difference {de}"))
            for k, src in (c.get("context") or {}).items():
                say(f"    {k} arrows start at: " + ", ".join(f"{ROLE_NAME.get(r, r)} ({cl})" for r, cl in src))
    if sims:
        png = d / f"final-{themes.print_palette()}.png"
        if png.exists():
            outs, wrote = simulate_png(png, d / "checks" / "cvd")
            say(f"simulated images ({'written' if wrote else 'unchanged'}): " + ", ".join(str(p.relative_to(d)) for p in outs))
    ok = not fails
    say(f"ACCESS {'PASSED' if ok else 'FAILED'}: lowest text contrast {min_text:.2f} (WCAG {TEXT_MIN}), lowest arrow "
        f"contrast {min_arrow:.2f} (WCAG {ARROW_MIN}); {len(fails)} failure(s)"
        + (f", {len(known)} known shortfall(s) of the configured palettes" if known else ""))
    if not quiet:
        print("\n".join(lines))
    return dict(ok=ok, fails=fails, known=known, min_text=min_text, min_arrow=min_arrow, lines=lines,
                shortfalls=shortfalls)


def main(argv):
    ap = argparse.ArgumentParser(description="WCAG contrast and colour-vision checks of a case's palettes and SVGs.")
    ap.add_argument("case_dir")
    ap.add_argument("--no-sims", action="store_true", help="do not write the simulated PNGs")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict line")
    ap.add_argument("--strict", action="store_true", help="fail on every shortfall, the recorded ones too")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
        res = run(a.case_dir, sims=not a.no_sims, quiet=a.quiet, strict=a.strict)
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    if a.quiet:
        print(res["lines"][-1])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    _bootstrap.entry()
    sys.exit(main(sys.argv[1:]))
