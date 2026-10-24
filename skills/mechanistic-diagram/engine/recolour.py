#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recolour an illustration into one of the configured palettes (style.palettes), crop the vendor logo
away, round the corners, and optionally trace it to SVG.

    recolour.py IN --palette ID [--bg TOKEN] [--mode raster|vector] [--out OUT] [--config FILE]
                [--crop X0,Y0,X1,Y1] [--keep-logo] [--radius PX] [--scale N] [--report] [--meta JSON]

ID is a palette id from the catalog or style.custom_palettes_file (--flavour is the same option).
Needs opencv-python-headless and scikit-learn (the optional dependency group), and potrace for --mode vector.

--meta writes what a caller needs to place the result. The engine's anatomy inset, decor.py,
runs this tool from the build and caches its output: the crop in source pixels, the source and output sizes,
the card colour as written, and the logo islands dropped.

Map illustration colours to the output palette, including the background.
Crop the vendor logo from each illustration.
Give each image rounded corners in proportion to the surrounding boxes.

How the colours are mapped:
  1. Paper: the near-white region connected to the image border is the background. It becomes the
     card colour (--bg, a palette token: TOKENS below; mantle by default), so the image sits on a
     card the diagram draws with the same radius as its containers.
  2. Logo: small foreground islands in a corner zone, such as a vendor mark or
     attribution line, are dropped, and the image is cropped to what is left plus a
     margin. --crop overrides the detection; --keep-logo turns it off.
  3. Anchors: foreground colours are clustered in OKLab. A chromatic cluster takes the palette
     accent nearest in hue (clusters of clearly different hue get different accents), and its
     lightness is kept by mixing that accent toward the palette's light end (a pale tint stays a
     tint) or toward its dark end (a shade stays a shade). Grey clusters, outlines included, sit on
     the neutral ramp by lightness. The mix fractions are solved in a light palette, where the paper is
     light as in the source (the print palette when it is light, else the first light palette in
     style.palettes, else a built-in neutral light reference), and reused in a dark palette, so both keep the
     same hierarchy; in a dark palette the light end is "text" and the dark end "crust", so structures read
     as pastel shapes with dark outlines on a dark card.
  4. Raster mode maps every pixel through a smooth radial-basis blend of the anchors, so
     anti-aliased edges and JPEG noise blend instead of flickering between colours; a little of
     each pixel's own lightness detail is kept. Vector mode labels pixels by nearest anchor, cleans
     the labels, traces each colour with potrace and fills it with its anchor colour: crisp at any
     print size.

TOKENS (--bg and the neutral ramp) come from the engine theme of the palette (themes.THEMES):
  canvas, cl_fill, cl_stroke, cl_text, key_fill, key_stroke, ink, muted, label   the theme's own colours
  base = key_fill (the key panel's fill), mantle = cl_fill (the containers' fill), text = ink,
  subtext = muted, crust = the end of the neutral ramp opposite the text
  <role>.fill, <role>.stroke for the seven roles; and the 14 named accents (themes.ACCENT_NAMES)
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)

import numpy as np
from PIL import Image


def require_optional():
    """Load the illustration-only dependencies after command-line help has been handled."""
    global cv2, KMeans
    try:
        import cv2
        from sklearn.cluster import KMeans
    except ImportError as ex:
        sys.exit(f"recolour.py needs opencv-python-headless and scikit-learn: {ex}. "
                 "Install the optional dependency group, or use a themed SVG inset.")


import style
import themes as _themes        # the engine theme of each palette: tokens, accents and role colours

ACCENTS = list(_themes.ACCENT_NAMES)
REF = "reference"               # the built-in light reference palette's name (no light palette configured)


# ---- OKLab -------------------------------------------------------------------------------------
def _lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _gam(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
_M2i = np.linalg.inv(_M2)
_M1i = np.linalg.inv(_M1)


def to_lab(rgb):
    """sRGB in [0, 1], shape (..., 3) -> OKLab."""
    return np.cbrt(_lin(rgb) @ _M1.T) @ _M2.T


def to_rgb(lab):
    return _gam(((lab @ _M2i.T) ** 3) @ _M1i.T)


def hexlab(h):
    h = h.lstrip("#")
    return to_lab(np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]))


def _ref_theme():
    """A neutral light reference: white card, near-black text, each named accent at its hue (Oklch L 0.62, C 0.12)."""
    acc = {n: _themes.oklch_to_hex(0.62, 0.12, h) for n, h in _themes.ACCENT_HUE.items()}
    roles = {r: ("#f2f2f2", "#555555", "#1a1a1a") for r in _themes.ROLES}
    return dict(flavour=REF, canvas="#ffffff", cl_fill="#f4f4f4", cl_stroke="#9e9e9e", cl_text="#1a1a1a",
                key_fill="#ffffff", key_stroke="#9e9e9e", ink="#1a1a1a", muted="#4a4a4a", label="#4a4a4a",
                roles=roles, accents=acc, dark=False, glyph_lc=(0.45, 0.12))


def theme(pid):
    return _ref_theme() if pid == REF else _themes.THEMES[pid]


def is_dark(pid):
    return bool(_themes.is_dark(theme(pid)))


def tokens(pid):
    """Every token name of a palette -> "#rrggbb" (see TOKENS in the module doc)."""
    T = theme(pid)
    out = {k: T[k] for k in ("canvas", "cl_fill", "cl_stroke", "cl_text", "key_fill", "key_stroke", "ink", "muted",
                             "label") if isinstance(T.get(k), str)}
    for r, (f, st, _t) in T["roles"].items():
        out[f"{r}.fill"], out[f"{r}.stroke"] = f, st
    for n in ACCENTS:
        out[n] = T["accents"][n] if n in T.get("accents", {}) else _themes.accent(n, T)
    out.update(base=T["key_fill"], mantle=T["cl_fill"], text=T["ink"], subtext=T["muted"])
    grounds = [T["canvas"], T["cl_fill"], T["key_fill"]]
    lum = [_themes.luminance(g) for g in grounds]
    out["crust"] = grounds[lum.index(min(lum) if is_dark(pid) else max(lum))]
    return out


def palette(pid):
    return {k: hexlab(v) for k, v in tokens(pid).items()}


def ends(pid):
    """(dark end, light end) of a palette's neutral ramp, as token names."""
    return ("crust", "text") if is_dark(pid) else ("text", "base")


def reference():
    """The light palette the mix fractions are solved in: the print palette when it is light, else the first
    light palette in style.palettes, else the built-in reference."""
    try:
        ids = [style.print_palette()] + list(style.palette_ids())
    except style.StyleError:
        ids = []
    for pid in ids:
        try:
            if not is_dark(pid):
                return pid
        except style.StyleError:
            continue
    return REF


def hue(lab):
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360


def chroma(lab):
    return np.hypot(lab[..., 1], lab[..., 2])


# ---- masks -------------------------------------------------------------------------------------
def paper_mask(lab_s):
    """Near-paper pixels connected to the border."""
    h, w = lab_s.shape[:2]
    border = np.concatenate([lab_s[0], lab_s[-1], lab_s[:, 0], lab_s[:, -1]])
    paper = np.median(border, axis=0)
    near = (np.linalg.norm(lab_s - paper, axis=-1) < 0.035).astype(np.uint8)
    n, lab = cv2.connectedComponents(near, connectivity=4)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return np.isin(lab, list(edge)) & (near > 0), paper


def find_logo(fg):
    """Small foreground islands in a corner zone: the vendor's mark. Returns (drop mask, notes)."""
    h, w = fg.shape
    k = max(3, int(0.006 * max(h, w)) | 1)
    joined = cv2.dilate(fg.astype(np.uint8), np.ones((k, k), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(joined, connectivity=8)
    if n <= 2:
        return np.zeros_like(fg), []
    areas = stats[1:, cv2.CC_STAT_AREA]
    main = 1 + int(np.argmax(areas))
    drop, notes = np.zeros_like(fg), []
    for i in range(1, n):
        if i == main:
            continue
        x, y, bw, bh, a = stats[i]
        cx, cy = x + bw / 2, y + bh / 2
        corner = (cx < 0.3 * w or cx > 0.7 * w) and (cy < 0.25 * h or cy > 0.75 * h)
        small = a < 0.04 * stats[main, cv2.CC_STAT_AREA]
        if corner and small:
            drop |= (lab == i) & fg
            notes.append(f"dropped island at x {x}-{x + bw}, y {y}-{y + bh} ({a} px)")
    return drop, notes


# ---- anchors -----------------------------------------------------------------------------------
def anchors(lab_s, fg, k=12, seed=7, pinned=()):
    """Foreground colours clustered in OKLab. pinned: source colours (OKLab) that must keep an anchor of their own
    however few pixels they cover (a small focal structure would otherwise fold into its
    neighbour's colour)."""
    px = lab_s[fg]
    rng = np.random.default_rng(seed)
    if len(px) > 80000:
        px = px[rng.choice(len(px), 80000, replace=False)]
    km = KMeans(n_clusters=k, n_init=4, random_state=seed).fit(px)
    cents, counts = km.cluster_centers_, np.bincount(km.labels_, minlength=k).astype(float)
    merged = True                                   # merge near-duplicates (dE < 0.025)
    while merged and len(cents) > 1:
        merged = False
        d = np.linalg.norm(cents[:, None] - cents[None], axis=-1) + np.eye(len(cents)) * 9
        i, j = np.unravel_index(np.argmin(d), d.shape)
        if d[i, j] < 0.025:
            cents[i] = (cents[i] * counts[i] + cents[j] * counts[j]) / (counts[i] + counts[j])
            counts[i] += counts[j]
            cents, counts = np.delete(cents, j, 0), np.delete(counts, j)
            merged = True
    keep = counts / counts.sum() >= 0.004          # drop tiny clusters: anti-alias blends
    cents, counts = cents[keep], counts[keep]
    for c in pinned:
        near = np.linalg.norm(lab_s - c, axis=-1) < 0.03
        share = float((near & fg).sum())
        if len(cents) and np.linalg.norm(cents - c, axis=-1).min() < 0.02:
            continue                                # already an anchor
        cents, counts = np.vstack([cents, c]), np.append(counts, max(share, 1.0))
    return cents, counts / counts.sum()


def assign(cents, shares):
    """Each anchor -> ("accent", name) or ("neutral", None). Distinct hues get distinct accents."""
    lat = palette(reference())
    acc_h = {a: float(hue(lat[a])) for a in ACCENTS}
    kinds, used = [None] * len(cents), {}
    for i in np.argsort(-shares):
        c = cents[i]
        if chroma(c) < 0.015:                      # true greys only; creams stay warm tints
            kinds[i] = ("neutral", None)
            continue
        h = float(hue(c))
        ranked = sorted(ACCENTS, key=lambda a: min(abs(acc_h[a] - h), 360 - abs(acc_h[a] - h)))
        pick = ranked[0]
        for a in ranked[:3]:                        # prefer a free accent within 35 degrees
            dh = min(abs(acc_h[a] - h), 360 - abs(acc_h[a] - h))
            if dh > 35:
                break
            other = used.get(a)
            if other is None or np.linalg.norm(cents[other] - c) < 0.06:
                pick = a
                break
        used.setdefault(pick, i)
        kinds[i] = ("accent", pick)
    return kinds


def _solve_mix(target_L, a, b):
    """t in [0, 1] with L(mix(a, b, t)) ~= target_L (OKLab mixes are linear in L)."""
    den = b[0] - a[0]
    return float(np.clip((target_L - a[0]) / den, 0, 1)) if abs(den) > 1e-6 else 0.0


def _lch(L, C, h):
    r = np.radians(h)
    return np.array([L, C * np.cos(r), C * np.sin(r)])


VIBRANCE = {"light": 1.0, "dark": 1.25}    # dark grounds grey out tints; give them a little more chroma
CHROMA_GAIN = 1.5      # a mapped colour may be at most this much more chromatic than its source (+0.02)
_vibrance = {}         # palette id -> --vibrance override


def vibrance(pid):
    return _vibrance.get(pid, VIBRANCE["dark" if is_dark(pid) else "light"])


def derivation(pid):
    """The mean Oklch lightness and chroma of the palette's role fills and strokes (the hue roles): what the
    diagram's own role boxes are built from."""
    T = theme(pid)
    fills, strokes = [], []
    for r in _themes.ROLES:
        if r == "anat":
            continue
        f, st, _t = T["roles"][r]
        fills.append(_themes.srgb_to_oklch(f)[:2])
        strokes.append(_themes.srgb_to_oklch(st)[:2])
    mean = lambda xs, i: sum(x[i] for x in xs) / len(xs)
    return dict(fill_l=mean(fills, 0), fill_c=mean(fills, 1), stroke_l=mean(strokes, 0), stroke_c=mean(strokes, 1))


def invert_like_boxes(lat_out, h_out, flavour, ref=None):
    """A light palette colour carried into a dark palette like the diagram's role boxes: light
    fills become dark fills and dark strokes become light strokes. Position u runs 0 at the light palette's
    stroke lightness to 1 at its fill lightness; the dark flavour's lightness and chroma come from
    the same position on its own stroke-to-fill axis (extrapolated a little, clipped to crust..text)."""
    ref = ref or reference()
    dl, df = derivation(ref), derivation(flavour)
    fl = palette(flavour)
    u = (lat_out[0] - dl["stroke_l"]) / (dl["fill_l"] - dl["stroke_l"])
    L = float(np.clip(df["stroke_l"] + u * (df["fill_l"] - df["stroke_l"]), fl["crust"][0], fl["text"][0]))
    c_ref_l = max(dl["stroke_c"] + u * (dl["fill_c"] - dl["stroke_c"]), 0.02)
    c_ref_f = max(df["stroke_c"] + u * (df["fill_c"] - df["stroke_c"]), 0.015)
    C = float(np.clip(chroma(lat_out) / c_ref_l, 0, 1.6) * c_ref_f)
    return _lch(L, min(C, 0.16), h_out)


def accent_chroma(fl, acc, flavour):
    """An accent's chroma in a palette. A palette with fewer accents can map a quiet source accent
    to a stronger output accent. Cap its chroma using the corresponding accent in
    the role strokes' mean chroma to keep the mapping restrained."""
    return min(float(chroma(fl[acc])), max(derivation(flavour)["stroke_c"], 0.06) * 1.6)


def anchor_colours(cents, kinds, flavour, dark_model="invert"):
    """In a light palette, mix the accent toward either end until it has the source lightness, so
    every colour is a tint or shade of the palette. Other palettes keep that structure: lightness is
    carried across by its position in the neutral range (a light palette's text..base onto a dark
    palette's crust..text), chroma is the same fraction of the palette's own accent, and hue is that accent's.
    A mid-tone structure keeps its relative lightness on a dark card."""
    ref = reference()
    light = not is_dark(flavour)
    lat, fl = palette(ref), palette(flavour)
    ld, ll = lat[ends(ref)[0]], lat[ends(ref)[1]]
    fd, flt = fl[ends(flavour)[0]], fl[ends(flavour)[1]]

    def carry(L):
        return fd[0] + (flt[0] - fd[0]) * float(np.clip((L - ld[0]) / (ll[0] - ld[0]), 0, 1))

    out, notes = [], []
    for c, (kind, acc) in zip(cents, kinds):
        L = c[0]
        if kind == "neutral":
            t = _solve_mix(L, ld, ll)
            if light or dark_model == "keep":
                out.append(fd + (flt - fd) * t)
            else:
                out.append(invert_like_boxes(ld + (ll - ld) * t, float(hue(fl["text"])), flavour, ref))
            notes.append(f"neutral L {L:.2f} -> {ends(flavour)[0]}..{ends(flavour)[1]} at {t:.2f}")
            continue
        A = lat[acc]
        end, word = (ll, "tint") if L >= A[0] else (ld, "shade")
        t = _solve_mix(L, A, end)
        lat_out = A + (end - A) * t
        # a muted source colour stays muted: mixing an accent only toward the lightness of, say, a soft shadow on a
        # structure gives a near-raw accent (tan #b7a895 -> orange #e29941); cap chroma at CHROMA_GAIN x the source's
        cap = CHROMA_GAIN * float(chroma(c)) + 0.02
        if float(chroma(lat_out)) > cap:
            lat_out = np.array([lat_out[0], lat_out[1] * cap / float(chroma(lat_out)), lat_out[2] * cap / float(chroma(lat_out))])
        if light and flavour == ref:
            out.append(lat_out)
        elif light:                                 # another light palette: the same tint or shade of its own accent
            Af = fl[acc]
            endf = flt if end is ll else fd
            tf = _solve_mix(L, Af, endf)
            out.append(Af + (endf - Af) * tf)
        elif dark_model == "invert":
            out.append(invert_like_boxes(lat_out, float(hue(fl[acc])), flavour, ref))
        elif dark_model == "pastel":
            Af = fl[acc]
            Lend = (flt if end is ll else fd)[0]
            Lp = Af[0] + (Lend - Af[0]) * t
            out.append(_lch(Lp, accent_chroma(fl, acc, flavour) * (1 - t) ** 0.45 * vibrance(flavour), float(hue(Af))))
        else:
            Af = fl[acc]
            rc = float(chroma(lat_out) / chroma(A))
            out.append(_lch(carry(lat_out[0]), min(rc * accent_chroma(fl, acc, flavour) * vibrance(flavour), 0.2), float(hue(Af))))
        notes.append(f"L {L:.2f} h {float(hue(c)):.0f} -> {acc} {word} {t:.2f}")
    return np.array(out), notes


# ---- rendering ---------------------------------------------------------------------------------
def map_raster(lab, cents, outs, bgmask, card, paper, sigma=0.035, detail=0.5):
    cents, outs = np.vstack([cents, paper]), np.vstack([outs, card])
    d2 = ((lab[..., None, :] - cents[None, None]) ** 2).sum(-1)
    w = np.exp(-(d2 - d2.min(-1, keepdims=True)) / (2 * sigma ** 2))
    w /= w.sum(-1, keepdims=True)
    out = w @ outs
    out[..., 0] += detail * (lab[..., 0] - w @ cents[:, 0])
    m = cv2.GaussianBlur(bgmask.astype(np.float32), (0, 0), 1.2)[..., None]
    return out * (1 - m) + card * m


def round_alpha(h, w, r, ss=4):
    """Anti-aliased rounded-rectangle alpha (supersampled)."""
    big = np.zeros((h * ss, w * ss), np.uint8)
    R = int(r * ss)
    cv2.rectangle(big, (R, 0), (w * ss - 1 - R, h * ss - 1), 255, -1)
    cv2.rectangle(big, (0, R), (w * ss - 1, h * ss - 1 - R), 255, -1)
    for cx, cy in ((R, R), (w * ss - 1 - R, R), (R, h * ss - 1 - R), (w * ss - 1 - R, h * ss - 1 - R)):
        cv2.circle(big, (cx, cy), R, 255, -1)
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA)


def trace(mask, scale, turd=8):
    """potrace one binary mask (upscaled by `scale`) -> list of SVG path d strings in source px."""
    m = cv2.resize(mask.astype(np.uint8) * 255, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    m = cv2.GaussianBlur(m, (0, 0), scale * 0.45) > 127
    with tempfile.TemporaryDirectory() as td:
        pbm = Path(td) / "m.pbm"
        Image.fromarray(~m).convert("1").save(pbm)      # potrace traces black
        svg = subprocess.run(["potrace", "-b", "svg", "-t", str(turd * scale), "-a", "1.1", "-O", "0.3",
                              "--flat", "-o", "-", str(pbm)], capture_output=True, text=True, check=True).stdout
    g = re.search(r'transform="translate\(([-\d.]+),([-\d.]+)\) scale\(([-\d.]+),([-\d.]+)\)"', svg)
    tx, ty, sx, sy = (float(v) for v in g.groups())
    ds = re.findall(r'<path d="([^"]+)"', svg, re.S)
    return [(d.replace("\n", " "), (tx / scale, ty / scale, sx / scale, sy / scale)) for d in ds]


def labels_clean(lab_s, cents, fg, k=5):
    d = np.linalg.norm(lab_s[..., None, :] - cents[None, None], axis=-1)
    lab = np.argmin(d, -1)
    onehot = np.stack([(lab == i) & fg for i in range(len(cents))], -1).astype(np.float32)
    onehot = np.stack([cv2.boxFilter(onehot[..., i], -1, (k, k)) for i in range(len(cents))], -1)
    return np.argmax(onehot, -1)


def hexs(lab):
    r = np.round(to_rgb(lab) * 255).astype(int)
    return "#%02x%02x%02x" % tuple(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("--palette", "--flavour", dest="flavour", required=True,
                    help="palette id (catalog or style.custom_palettes_file)")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--bg", default="mantle", help="palette token for the card behind the image (see TOKENS)")
    ap.add_argument("--vscale", type=int, default=0, help="vector: trace at this many pixels per source pixel "
                                                          "(default: enough for about 2400 px across, 1 to 3)")
    ap.add_argument("--mode", choices=("raster", "vector"), default="raster")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--crop", help="X0,Y0,X1,Y1 in source pixels (overrides logo detection)")
    ap.add_argument("--keep-logo", action="store_true")
    ap.add_argument("--radius", type=float, default=None, help="corner radius in output px (default 3%% of width)")
    ap.add_argument("--scale", type=float, default=1.0, help="raster: upscale factor before mapping")
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--anchor", action="append", default=[], metavar="#RRGGBB",
                    help="a source colour that keeps its own anchor however small its area (repeatable)")
    ap.add_argument("--dark-model", choices=("invert", "keep", "pastel"), default="pastel",
                    help="dark palettes: pastel (default here: light tints on the dark card), invert like the role "
                         "boxes, or keep the light palette's lightness order (the engine's inset uses keep)")
    ap.add_argument("--detail", type=float, default=0.15)
    ap.add_argument("--vibrance", type=float, default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--meta", type=Path, help="write the crop, sizes and card colour as JSON")
    a = ap.parse_args()
    require_optional()
    try:
        style.configure(a.config)
        toks = tokens(a.flavour)
    except style.StyleError as ex:
        raise SystemExit(f"recolour.py: {ex}")
    if a.bg not in toks:
        raise SystemExit(f"recolour.py: --bg {a.bg!r} is not a palette token; use one of {', '.join(sorted(toks))}")

    rgb = np.asarray(Image.open(a.src).convert("RGB"), np.float32) / 255
    smooth = cv2.pyrMeanShiftFiltering((rgb * 255).astype(np.uint8), 6, 16).astype(np.float32) / 255
    den = cv2.fastNlMeansDenoisingColored((rgb * 255).astype(np.uint8), None, 4, 4, 5, 15).astype(np.float32) / 255
    lab, lab_s = to_lab(den), to_lab(smooth)
    bg, paper = paper_mask(lab_s)
    fg = ~bg
    notes = []
    if a.crop:
        x0, y0, x1, y1 = (int(v) for v in a.crop.split(","))
    else:
        if not a.keep_logo:
            drop, notes = find_logo(fg)
            bg |= drop
            fg &= ~drop
        ys, xs = np.nonzero(fg)
        m = int(0.04 * max(np.ptp(xs), np.ptp(ys)))
        x0, y0 = max(0, xs.min() - m), max(0, ys.min() - m)
        x1, y1 = min(rgb.shape[1], xs.max() + m + 1), min(rgb.shape[0], ys.max() + m + 1)
    sl = (slice(y0, y1), slice(x0, x1))
    lab, lab_s, bg, fg = lab[sl], lab_s[sl], bg[sl], fg[sl]

    if a.vibrance is not None:
        _vibrance[a.flavour] = a.vibrance
    cents, shares = anchors(lab_s, fg, k=a.k, pinned=[hexlab(h) for h in a.anchor])
    kinds = assign(cents, shares)
    outs, cnotes = anchor_colours(cents, kinds, a.flavour, a.dark_model)
    fl = palette(a.flavour)
    card = fl[a.bg]
    h, w = lab.shape[:2]
    out = a.out or a.src.with_name(f"{a.src.stem}-{a.flavour}.{'svg' if a.mode == 'vector' else 'png'}")

    if a.mode == "raster":
        if a.scale != 1:
            lab = cv2.resize(lab.astype(np.float32), None, fx=a.scale, fy=a.scale, interpolation=cv2.INTER_CUBIC)
            bg = cv2.resize(bg.astype(np.uint8), None, fx=a.scale, fy=a.scale, interpolation=cv2.INTER_LINEAR) > 0
            h, w = lab.shape[:2]
        res = map_raster(lab, cents, outs, bg, card, paper, detail=a.detail * (-0.8 if is_dark(a.flavour) and a.dark_model == 'invert' else 1))
        r = a.radius if a.radius is not None else 0.03 * w
        alpha = round_alpha(h, w, r)
        img = np.dstack([np.round(to_rgb(res) * 255).astype(np.uint8), alpha])
        Image.fromarray(img, "RGBA").save(out, optimize=True)
    else:
        # Vector: work at 3x so one-pixel outlines survive. Outlines (ink anchors, L < 0.45) are traced
        # as their own layer on top; fills are labelled by nearest non-ink anchor, cleaned by a majority
        # filter, grown under the outlines so no seam shows, and traced underneath.
        S = a.vscale or int(np.clip(round(2400 / max(h, w)), 1, 3))
        up = cv2.resize(lab_s.astype(np.float32), None, fx=S, fy=S, interpolation=cv2.INTER_CUBIC)
        fgu = cv2.resize(fg.astype(np.uint8), None, fx=S, fy=S, interpolation=cv2.INTER_NEAREST) > 0
        cf = cents.astype(np.float32)
        d = np.stack([np.sqrt(((up - c) ** 2).sum(-1)) for c in cf], -1)     # one anchor at a time: a full-size
        is_ink = cents[:, 0] < 0.45                                            # export fits in memory
        ink = is_ink[np.argmin(d, -1)] & fgu
        ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 1), np.uint8)) > 0
        fills = [i for i in range(len(cents)) if not is_ink[i]]
        lab_f = np.array(fills)[np.argmin(d[..., fills], -1)]
        del d
        # pixels next to an outline are anti-alias blends: they don't vote, and take the fill of the region
        # just inside instead (else mid-tone anchors speckle every edge)
        band = cv2.dilate(ink.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        onehot = np.stack([((lab_f == i) & fgu & ~band).astype(np.float32) for i in fills], -1)
        onehot = np.stack([cv2.boxFilter(onehot[..., j], -1, (11, 11)) for j in range(len(fills))], -1)
        grown = np.array(fills)[np.argmax(onehot, -1)]
        # the silhouette: foreground and outlines, closed and hole-filled; fills never leave it
        sil = cv2.morphologyEx((fgu | ink).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        flood = sil.copy()
        cv2.floodFill(flood, np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), np.uint8), (0, 0), 1)
        sil = (sil > 0) | (flood == 0)
        body = cv2.erode(sil.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        r = a.radius if a.radius is not None else 0.03 * w
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
                 f'<defs><clipPath id="c"><rect width="{w}" height="{h}" rx="{r:.1f}"/></clipPath></defs>',
                 f'<g clip-path="url(#c)"><rect width="{w}" height="{h}" fill="{hexs(card)}"/>']
        order = sorted(fills, key=lambda i: -((grown == i) & body).sum())
        for i in order:
            mask = (grown == i) & body
            mask = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            if mask.sum() < 40 * S * S:
                continue
            for dd, (tx, ty, sx, sy) in trace(mask, 1, turd=6 * S):
                parts.append(f'<path fill="{hexs(outs[i])}" transform="translate({tx / S:.3f},{ty / S:.3f}) scale({sx / S:.6f},{sy / S:.6f})" d="{dd}"/>')
        inkc = outs[int(np.argmin(np.where(is_ink, cents[:, 0], 9)))]
        for dd, (tx, ty, sx, sy) in trace(ink, 1, turd=2 * S):
            parts.append(f'<path fill="{hexs(inkc)}" transform="translate({tx / S:.3f},{ty / S:.3f}) scale({sx / S:.6f},{sy / S:.6f})" d="{dd}"/>')
        parts.append("</g></svg>")
        out.write_text("\n".join(parts))

    if a.meta:
        a.meta.write_text(json.dumps(dict(src=a.src.name, src_size=[int(rgb.shape[1]), int(rgb.shape[0])],
                                          crop=[int(x0), int(y0), int(x1), int(y1)], out=out.name, out_size=[int(w), int(h)],
                                          flavour=a.flavour, bg=a.bg, card=hexs(card), mode=a.mode, scale=a.scale,
                                          logo=notes), indent=1))
    if a.report:
        print(f"{a.src.name} -> {out.name}: crop x {x0}-{x1}, y {y0}-{y1}; paper {hexs(paper)}; card {a.bg} {hexs(card)}")
        for n in notes:
            print("  logo:", n)
        for c, s, (kind, acc), o, n in zip(cents, shares, kinds, outs, cnotes):
            print(f"  {hexs(c)} {s:5.1%}  {n:44s} -> {hexs(o)}")
    return 0


if __name__ == "__main__":
    _bootstrap.entry()
    sys.exit(main())
