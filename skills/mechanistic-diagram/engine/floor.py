#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every diagram must meet the quality minimums in floor.json.

    python3 floor.py <case_dir> [--floor FILE] [--config FILE]     # exit 1 on a breach

floor.json (beside this file, or the file in diagram.floor_path) holds scale-free minimums set from
the diagram grammar and the accessibility checks; it is edited by hand, never measured from a
diagram. Page size limits come from the config (diagram.page.max_w, diagram.page.max_h). A check
works from a build's outputs alone (geometry.json, final-<palette>.svg/.pdf, the .drawio, deliver/,
the Artifact page) and never runs the case folder's Python.

Each row is FAIL (exit 1), WARN or ok:
  page size                the page (SVG canvas, and every box and container in geometry.json) fits
                           diagram.page.max_w x diagram.page.max_h                                  FAIL
  smallest text            every <text> in every final-<palette>.svg is at least text_px_min px,
                           and box text (geometry.json fs) at least body_text_px_min px              FAIL
  label overlaps           no two <text> runs of the print palette's SVG overlap (their boxes from
                           x, y, size, anchor and the measured width)                                FAIL
  box gap                  every two boxes keep at least box_gap_min px apart                        FAIL
  edge clearance           every connector segment keeps edge_clearance_min px clear of every box
                           it does not start or end at                                               FAIL
  layout problems, crossings, unrouted edges   0 (the build passes layout_engine's own result;
                           standalone, core.check() and the container-side and text-width checks
                           are recomputed from geometry.json)                                        FAIL
  gutters                  every gap between neighbouring stage containers (the key included)
                           equals the floor's gutter within its tolerance                            FAIL
  PDF pages                every final-*.pdf and deliver/*.pdf has one page                          FAIL
  box aspect               every box of two or more lines that is not a bar is at least
                           box_aspect_min wide per unit of height                                    FAIL
  box count                above box_count_warn reads as crowded                                     WARN only
  text face and radii      geometry measured in the configured sans (fonts.sans) at the floor's
                           weights; box rects rx 7, stage containers rx 12                           FAIL
  images: SVG, .drawio, Artifact   every <image> (or nested <svg>) is rounded: by a clip-path whose
                           rect has rx > 0 and sits inside the image, or in its own data (an SVG with
                           a rounded clip; a bitmap, or an SVG rasterised by rsvg-convert, whose four
                           corner pixels are transparent); a .drawio image cell by
                           clipPath=inset(... round ...) or by its data. An image of 40 px or less
                           each way with transparent corners is a glyph (an icon has no corners to
                           round). No rounding: FAIL; a picture's displayed radius outside
                           image_rx_range (boxes 7, containers 12): WARN
  contrast                 access.py: no failure (shortfalls listed in floor.json
                           contrast_exceptions are reported as KNOWN, not failed)                    FAIL
  draw.io verify           <case_dir>/verify-result.json (the object verify.js returns) has
                           fontOk, edgesBad 0, labelsBad 0, and matches this build (its
                           drawio_sha256, else its edge and label counts); absent or stale: WARN
"""
import argparse
import base64
import hashlib
import io
import json
import math
import pathlib
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent
FLOOR_JSON = ENGINE / "floor.json"
SVG_NS = "{http://www.w3.org/2000/svg}"
BAR_W = 300                  # without layout.py, a box wider than this is taken as a bar
IMAGE_RX = (5.0, 14.0)       # displayed corner radius of an image: boxes rx 7, containers rx 12
GLYPH_PX = 40                # an image no larger than this each way with transparent corners is a glyph, not a picture


# ------------------------------------------------------------------------------------------ a build's outputs
class Build:
    """The outputs of a build folder (an engine case)."""

    def __init__(self, d):
        self.dir = pathlib.Path(d).resolve()
        if not (self.dir / "geometry.json").exists():
            raise SystemExit(f"{self.dir}: no geometry.json (build the case first)")
        self.geo = json.loads((self.dir / "geometry.json").read_text())
        cj = self.dir / "case.json"
        self.meta = json.loads(cj.read_text()) if cj.exists() else {}
        drawios = sorted(self.dir.glob("*.drawio"))
        self.slug = self.meta.get("slug") or (drawios[0].stem if len(drawios) == 1 else None)
        self.drawio = self.dir / f"{self.slug}.drawio" if self.slug else None
        self.html = self._artifact()
        self._pos = self._layout_pos()

    def svg(self, fl):
        return self.dir / f"final-{fl}.svg"

    def palettes(self):
        """The palette ids this build rendered (final-<id>.svg), print palette first."""
        import themes
        try:
            want = list(themes.final_palettes())
        except Exception:  # noqa: BLE001  (no config: judge what is on disk)
            want = []
        have = sorted(p.stem[len("final-"):] for p in self.dir.glob("final-*.svg")
                      if not p.stem.startswith(("final-core", "final-detail")) and ".static" not in p.name)
        out = [fl for fl in want if fl in have] + [fl for fl in have if fl not in want and not want]
        return out

    def _artifact(self):
        if not (self.dir / "artifact_content.py").exists():
            return None
        m = re.search(r'^OUT_NAME\s*=\s*["\']([^"\']+)["\']', (self.dir / "artifact_content.py").read_text(), re.M)
        p = self.dir / (m.group(1) if m else f"{self.slug}.html")
        return p if p.exists() else None

    def _layout_pos(self):
        """Which boxes are bars, from geometry.json ("bar"); the floor never runs a case's Python."""
        return None

    def is_bar(self, nid, b):
        if b.get("bar") is not None:
            return bool(b["bar"])
        return b["w"] > BAR_W


def sha256(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def canvas(svg_path):
    root = ET.parse(svg_path).getroot()
    return float(root.get("width")), float(root.get("height"))


# ------------------------------------------------------------------------------------------ scale-free measures
def floor_path():
    """diagram.floor_path from the config, else floor.json beside this file."""
    try:
        import style
        p = style.get("diagram.floor_path")
    except Exception:  # noqa: BLE001  (no config: the engine's own floor)
        p = None
    return pathlib.Path(p) if p else FLOOR_JSON


def page_limits():
    """(max_w, max_h) from the config: diagram.page.max_w and diagram.page.max_h."""
    try:
        import style
        return int(style.get("diagram.page.max_w", 4000)), int(style.get("diagram.page.max_h", 3000))
    except Exception:  # noqa: BLE001
        return 4000, 3000


def extent(build, fl):
    """The page's width and height: the SVG canvas, or further when a box or container in geometry.json
    reaches past it."""
    W, H = canvas(build.svg(fl)) if build.svg(fl).exists() else (0.0, 0.0)
    g = build.geo
    for b in g["nodes"].values():
        W, H = max(W, b["x"] + b["w"] / 2), max(H, b["y"] + b["h"] / 2)
    for r in panels(g).values():
        W, H = max(W, r[2]), max(H, r[3])
    return W, H


def _float(v, default=0.0):
    try:
        return float(str(v).replace("px", ""))
    except (TypeError, ValueError):
        return default


def svg_texts(svg_path):
    """Every <text> of an SVG: dict(text, x, y, fs, anchor, bold, italic, mono, family)."""
    root = ET.parse(svg_path).getroot()
    out = []

    def walk(el, inherited):
        attrs = dict(inherited)
        for k in ("font-family", "font-size", "font-weight", "font-style", "text-anchor"):
            if el.get(k) is not None:
                attrs[k] = el.get(k)
        if el.tag == SVG_NS + "text":
            text = "".join(el.itertext())
            if text.strip():
                fam = attrs.get("font-family", "")
                out.append(dict(text=text, x=_float(el.get("x")), y=_float(el.get("y")),
                                fs=_float(attrs.get("font-size"), 16.0), anchor=attrs.get("text-anchor", "start"),
                                bold=_float(attrs.get("font-weight"), 400) >= 600,
                                italic=attrs.get("font-style") == "italic", family=fam))
            return
        for ch in el:
            walk(ch, attrs)
    walk(root, {})
    return out


def rendered_labels(svg_path, geo):
    """Missing, extra, duplicate or mismatched node labels compared with the geometry's text lines."""
    root = ET.parse(svg_path).getroot()
    found, bad = {}, []
    for group in root.iter(SVG_NS + "g"):
        if "node" not in group.get("class", "").split():
            continue
        nid = group.get("data-node")
        if nid in found:
            bad.append(f"duplicate {nid}")
        found[nid] = ["".join(t.itertext()) for t in group.iter(SVG_NS + "text")]
    norm = lambda t: " ".join(t.split())
    for nid, node in geo["nodes"].items():
        want = [norm(t) for t in node.get("lines", [])]
        got = [norm(t) for t in found.get(nid, [])]
        if not want or got != want:
            bad.append(f"{nid}: labels differ or missing")
    bad.extend(f"unknown {nid}" for nid in found if nid not in geo["nodes"])
    if not geo["nodes"]:
        bad.append("geometry has no nodes")
    return bad


def text_boxes(texts, mono_family=None):
    """(x0, y0, x1, y1) of each text run: the measured width (the configured sans, or 0.6 em per character
    for the mono), and a band from the ascent to the descent around the baseline."""
    import core as E
    out = []
    for t in texts:
        if mono_family and mono_family in t["family"]:
            w = 0.6 * t["fs"] * len(t["text"])
        else:
            w = E.text_w(t["text"], t["bold"], size=t["fs"], italic=t["italic"])
        x0 = t["x"] - (w / 2 if t["anchor"] == "middle" else w if t["anchor"] == "end" else 0)
        out.append((x0, t["y"] - 0.74 * t["fs"], x0 + w, t["y"] + 0.2 * t["fs"]))
    return out


def label_overlaps(texts, mono_family=None, tol=0.5):
    """Pairs of text runs whose boxes overlap by more than tol px each way."""
    boxes = text_boxes(texts, mono_family)
    bad = []
    for i in range(len(boxes)):
        ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            bx0, by0, bx1, by1 = boxes[j]
            if min(ax1, bx1) - max(ax0, bx0) > tol and min(ay1, by1) - max(ay0, by0) > tol:
                bad.append((texts[i]["text"], texts[j]["text"]))
    return bad


def _rect(b):
    return (b["x"] - b["w"] / 2, b["y"] - b["h"] / 2, b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)


def box_gaps(geo):
    """[(gap, a, b)] for every pair of boxes, smallest first: the clear distance between their rects
    (negative when they overlap)."""
    items = [(k, _rect(v)) for k, v in geo["nodes"].items()]
    out = []
    for i in range(len(items)):
        a, (ax0, ay0, ax1, ay1) = items[i]
        for j in range(i + 1, len(items)):
            b, (bx0, by0, bx1, by1) = items[j]
            dx = max(bx0 - ax1, ax0 - bx1)
            dy = max(by0 - ay1, ay0 - by1)
            gap = max(dx, dy) if (dx > 0 or dy > 0) else max(dx, dy)
            out.append((gap, a, b))
    return sorted(out)


def _seg_rect_dist(p, q, r):
    """Clear distance from an axis-parallel segment p-q to a rect (0 when they touch or cross)."""
    (x0, y0), (x1, y1) = p, q
    rx0, ry0, rx1, ry1 = r
    sx0, sx1, sy0, sy1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    dx = max(rx0 - sx1, sx0 - rx1, 0)
    dy = max(ry0 - sy1, sy0 - ry1, 0)
    return math.hypot(dx, dy)


def edge_clearances(geo):
    """[(distance, edge, box)] for every connector segment and every box it does not start or end at."""
    rects = {k: _rect(v) for k, v in geo["nodes"].items()}
    out = []
    for ed in geo["edges"]:
        pts = ed["pts"]
        for k, r in rects.items():
            if k in (ed["a"], ed["b"]):
                continue
            d = min(_seg_rect_dist(p, q, r) for p, q in zip(pts, pts[1:]))
            out.append((d, f"{ed['a']}->{ed['b']}", k))
    return sorted(out)


def _fonts(geo):
    """core, measuring in the configured sans at the weights geometry.json was laid out at."""
    import core as E
    E.use_configured_fonts(geo.get("wght") or (500, 700))
    return E


def geometry_problems(geo):
    """layout_engine's checks that need only geometry.json: (problems, crossings, unrouted)."""
    E = _fonts(geo)
    boxes = {k: E.Box(id=k, x=v["x"], y=v["y"], w=v["w"], h=v["h"], role=v["role"], lines=v["lines"],
                      bold=v["bold"], cluster=v["cluster"]) for k, v in geo["nodes"].items()}
    probs, warns, crossings = E.check(boxes, geo["edges"], geo["clusters"])
    rects = panels(geo)
    for ed in geo["edges"]:
        for (x0, y0), (x1, y1) in zip(ed["pts"], ed["pts"][1:]):
            for cid, (cx0, cy0, cx1, cy1) in rects.items():
                if x0 == x1 and min(abs(x0 - cx0), abs(x0 - cx1)) < 8 and min(y0, y1) < cy1 and max(y0, y1) > cy0:
                    probs.append(f"{ed['a']}->{ed['b']} runs along the side of {cid}")
                if y0 == y1 and min(abs(y0 - cy0), abs(y0 - cy1)) < 8 and min(x0, x1) < cx1 and max(x0, x1) > cx0:
                    probs.append(f"{ed['a']}->{ed['b']} runs along the top or bottom of {cid}")
    for b in boxes.values():
        mw = max(E.text_w(l, b.bold) for l in b.lines)
        if mw > b.w - 2 * E.PAD_X:
            probs.append(f"text too wide in {b.id}: {mw:.0f} > {b.w - 2 * E.PAD_X}")
    unrouted = [f"{e['a']}->{e['b']}" for e in geo["edges"] if not e.get("ok", True)]
    return probs, crossings, unrouted


def panels(geo):
    """Every container on the grid: the stages, the key and, when the case has one, the inset."""
    allr = dict(geo["clusters"], key=geo["key"])
    if isinstance(geo.get("inset"), dict) and geo["inset"].get("rect"):
        allr["inset"] = geo["inset"]["rect"]
    return allr


def gutters(geo):
    """Every gap between neighbouring containers (key and inset included): [(a, b, axis, gap)]. Two
    containers are neighbours when they overlap across the gap and no third container sits between them."""
    allr = panels(geo)
    out = []
    for a, (ax0, ay0, ax1, ay1) in allr.items():
        for b, (bx0, by0, bx1, by1) in allr.items():
            if bx0 > ax1 and not (by1 <= ay0 or by0 >= ay1):
                if not any(ox0 > ax1 and ox1 < bx0 and not (oy1 <= ay0 or oy0 >= ay1) for ox0, oy0, ox1, oy1 in allr.values()):
                    out.append((a, b, "x", bx0 - ax1))
            if by0 > ay1 and not (bx1 <= ax0 or bx0 >= ax1):
                if not any(oy0 > ay1 and oy1 < by0 and not (ox1 <= ax0 or ox0 >= ax1) for ox0, oy0, ox1, oy1 in allr.values()):
                    out.append((a, b, "y", by0 - ay1))
    return out


def pdf_pages(p):
    info = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    return int(m.group(1)) if m else 0


def aspects(build):
    """[(w/h, id, w, h)] of the boxes of two or more lines that are not bars, squarest first."""
    out = [(b["w"] / b["h"], nid, b["w"], b["h"]) for nid, b in build.geo["nodes"].items()
           if len(b["lines"]) >= 2 and not build.is_bar(nid, b)]
    return sorted(out)


def radii(build, fl):
    """rx of the SVG rects drawn at each box's and each container's geometry: ({rx: n} boxes, {rx: n} containers, unmatched)."""
    root = ET.parse(build.svg(fl)).getroot()
    rects = {}
    for el in root.iter(SVG_NS + "rect"):
        try:
            k = (round(float(el.get("x", 0)), 1), round(float(el.get("y", 0)), 1),
                 round(float(el.get("width", 0)), 1), round(float(el.get("height", 0)), 1))
        except ValueError:
            continue
        rects.setdefault(k, float(el.get("rx") or 0))
    boxes, conts, miss = {}, {}, []
    for nid, b in build.geo["nodes"].items():
        k = (round(b["x"] - b["w"] / 2, 1), round(b["y"] - b["h"] / 2, 1), round(b["w"], 1), round(b["h"], 1))
        if k in rects:
            boxes[rects[k]] = boxes.get(rects[k], 0) + 1
        else:
            miss.append(nid)
    for cid, (x0, y0, x1, y1) in panels(build.geo).items():
        k = (round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1))
        if k in rects:
            conts[rects[k]] = conts.get(rects[k], 0) + 1
        else:
            miss.append(cid)
    return boxes, conts, miss


# ------------------------------------------------------------------------------------------ images
def _affine(t):
    """An SVG transform attribute as a matrix (a, b, c, d, e, f)."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "translate":
            n = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            n = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "matrix":
            n = tuple(v)
        elif name == "rotate":
            a = math.radians(v[0])
            n = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
        else:
            continue
        m = _mul(m, n)
    return m


def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _bbox(m, x, y, w, h):
    pts = [_apply(m, x + dx, y + dy) for dx in (0, w) for dy in (0, h)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _f(v, default=0.0):
    try:
        return float(re.match(r"[-\d.e]+", str(v)).group(0))
    except (AttributeError, ValueError, TypeError):
        return default


def _rounded_clip(clip, ids, scale):
    """(rx in the clip's own units, (x0, y0, x1, y1)) of a clipPath that is one rounded rect, else None."""
    if clip is None:
        return None
    local = lambda e: e.tag.rsplit("}", 1)[-1]
    kids = [k for k in clip if local(k) in ("rect", "use", "path", "circle", "ellipse", "polygon")]
    if len(kids) != 1:
        return None
    k = kids[0]
    if local(k) == "use":
        ref = (k.get("href") or k.get("{http://www.w3.org/1999/xlink}href") or "").lstrip("#")
        k = ids.get(ref)
        if k is None:
            return None
    if local(k) != "rect":
        return None
    rx = _f(k.get("rx") or k.get("ry"))
    x, y, w, h = _f(k.get("x")), _f(k.get("y")), _f(k.get("width")), _f(k.get("height"))
    m = _affine(k.get("transform"))
    return rx * scale, _bbox(m, x, y, w, h)


_RASTER = {}


def _corners(png_bytes):
    """(transparent corners?, radius as a share of the width) of a bitmap."""
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes))
    if im.mode not in ("RGBA", "LA", "P", "PA"):
        return False, None
    a = im.convert("RGBA").getchannel("A")
    w, h = im.size
    if max(a.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))) > 16:
        return False, None
    d = 0
    while d < min(w, h) // 2 and a.getpixel((d, d)) < 128:
        d += 1
    return True, d / (1 - 1 / math.sqrt(2)) / w


def _svg_corners(svg_bytes):
    """_corners() of an SVG document, rasterised once by rsvg-convert at 96 px wide (cached)."""
    key = hashlib.sha1(svg_bytes).hexdigest()
    if key not in _RASTER:
        r = subprocess.run(["rsvg-convert", "-w", "96", "-f", "png"], input=svg_bytes, capture_output=True)
        _RASTER[key] = _corners(r.stdout) if r.returncode == 0 and r.stdout else (False, None)
    return _RASTER[key]


def _data_rounding(href, base_dir):
    """How an image's own data is rounded: (kind, radius as a share of its width) or (None, None).
    kind: "SVG with a rounded clip", or "transparent corners" (a bitmap, or an SVG drawn to one, whose
    four corner pixels are transparent: rounded in the data, or a glyph with no corners at all)."""
    raw = None
    if href.startswith("data:"):
        head, _, body = href.partition(",")
        try:
            raw = base64.b64decode(body) if ";base64" in head or re.fullmatch(r"[A-Za-z0-9+/=\s]+", body) else \
                urllib.parse.unquote(body).encode()
        except ValueError:
            raw = urllib.parse.unquote(body).encode()
    elif href and base_dir is not None and (base_dir / href).exists():
        raw = (base_dir / href).read_bytes()
    if raw is None:
        return None, None
    if raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:3] == b"\xff\xd8\xff" or raw[:4] == b"RIFF":
        ok, share = _corners(raw)
        return ("transparent corners", share) if ok else (None, None)
    text = raw.decode("utf-8", "replace")
    if "svg" in text[:4096]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None, None
        if root.tag.rsplit("}", 1)[-1] != "svg":
            return None, None
        vb = [float(v) for v in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if v] or \
            [0, 0, _f(root.get("width"), 1), _f(root.get("height"), 1)]
        ids = {e.get("id"): e for e in root.iter() if e.get("id")}
        for el in [root] + list(root):
            cp = re.match(r"url\(#([^)]+)\)", el.get("clip-path") or "")
            got = _rounded_clip(ids.get(cp.group(1)) if cp else None, ids, 1.0)
            if got and got[0] > 0:
                x0, y0, x1, y1 = got[1]
                if x0 <= vb[0] + 1 and y0 <= vb[1] + 1 and x1 >= vb[0] + vb[2] - 1 and y1 >= vb[1] + vb[3] - 1:
                    return "SVG with a rounded clip", got[0] / vb[2]
        ok, share = _svg_corners(text.encode("utf-8"))
        if ok:
            return "transparent corners", share
    return None, None


def svg_images(root, base_dir=None, where=""):
    """Every <image> and nested <svg> under root: [dict(where, what, how, rx, ok, note)]."""
    ids = {e.get("id"): e for e in root.iter() if e.get("id")}
    out = []

    def walk(el, ctm, clips):
        tag = el.tag.replace(SVG_NS, "")
        if tag in ("defs", "clipPath", "mask", "pattern", "symbol"):
            return
        m = _mul(ctm, _affine(el.get("transform")))
        cp = re.match(r"url\(#([^)]+)\)", el.get("clip-path") or "")
        here = clips + ([(m, ids.get(cp.group(1)))] if cp else [])
        nested = tag == "svg" and el is not root
        if tag == "image" or nested:
            x, y, w, h = _f(el.get("x")), _f(el.get("y")), _f(el.get("width")), _f(el.get("height"))
            box = _bbox(m, x, y, w, h)
            label = f"{where}{tag} at ({box[0]:.0f}, {box[1]:.0f}) {box[2] - box[0]:.0f}x{box[3] - box[1]:.0f}"
            res = dict(where=label, how=None, rx=None, ok=False, note="")
            for cm, clip in reversed(here):
                sc = math.sqrt(abs(cm[0] * cm[3] - cm[1] * cm[2])) or 1.0
                got = _rounded_clip(clip, ids, sc)
                if got and got[0] > 0:
                    cb = _bbox(cm, *got[1][:2], got[1][2] - got[1][0], got[1][3] - got[1][1])
                    inside = cb[0] >= box[0] - 1 and cb[1] >= box[1] - 1 and cb[2] <= box[2] + 1 and cb[3] <= box[3] + 1
                    if inside:
                        res.update(how="clip-path rounded rect", rx=got[0], ok=True)
                    else:
                        res.update(how="clip-path rounded rect", rx=got[0], ok=False,
                                   note="the clip is larger than the image, so its corners stay square")
                    break
            if not res["how"]:
                if nested:
                    inner = ET.tostring(el, encoding="unicode")
                    kind, share = _data_rounding("data:image/svg+xml," + urllib.parse.quote(inner), None)
                else:
                    href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
                    kind, share = _data_rounding(href, base_dir)
                if kind == "transparent corners" and max(box[2] - box[0], box[3] - box[1]) <= GLYPH_PX:
                    res.update(how="glyph (no square corners)", ok=True)
                elif kind:
                    res.update(how=kind, rx=share * (box[2] - box[0]), ok=True)
                else:
                    res["note"] = "no rounded clip-path and no rounding in its own data"
            if res["ok"] and res["rx"] is not None and not IMAGE_RX[0] <= res["rx"] <= IMAGE_RX[1]:
                res["note"] = f"displayed radius {res['rx']:.1f} px is outside {IMAGE_RX[0]:g}-{IMAGE_RX[1]:g} (boxes 7, containers 12)"
            out.append(res)
            if nested:
                return
        for ch in el:
            walk(ch, m, here)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), [])
    return out


def drawio_images(path):
    """Image cells of a .drawio: [dict(where, how, rx, ok, note)]."""
    root = ET.parse(path).getroot()
    models = []
    for dg in root.iter("diagram"):
        inner = dg.find("mxGraphModel")
        if inner is not None:
            models.append((dg.get("name"), inner))
        elif (dg.text or "").strip():
            try:
                xml = urllib.parse.unquote(zlib.decompress(base64.b64decode(dg.text.strip()), -15).decode())
                models.append((dg.get("name"), ET.fromstring(xml)))
            except Exception:
                pass
    out = []
    for name, model in models:
        for cell in model.iter("mxCell"):
            style = cell.get("style") or ""
            kv = dict(p.split("=", 1) for p in style.split(";") if "=" in p)
            flags = {p for p in style.split(";") if p and "=" not in p}
            if not ("image" in kv or kv.get("shape") == "image" or "image" in flags):
                continue
            g = cell.find("mxGeometry")
            w = _f(g.get("width"), 0) if g is not None else 0
            h = _f(g.get("height"), 0) if g is not None else 0
            res = dict(where=f"{path.name} cell {cell.get('id')} ({w:.0f} px wide)", how=None, rx=None, ok=False, note="")
            clip = kv.get("clipPath", "")
            m = re.search(r"round\s+([\d.]+)(px|%)?", clip)
            if m:
                rx = float(m.group(1)) * (w / 100 if m.group(2) == "%" else 1)
                res.update(how="clipPath=inset(... round ...)", rx=rx, ok=rx > 0)
            else:
                href = kv.get("image", "")
                if href.startswith("data:") and ";base64" not in href.split(",", 1)[0]:
                    head, body = href.split(",", 1)
                    href = head + ";base64," + body if re.fullmatch(r"[A-Za-z0-9+/=]+", body) else href
                kind, share = _data_rounding(href, path.parent)
                if kind == "transparent corners" and max(w, h) <= GLYPH_PX:
                    res.update(how="glyph (no square corners)", ok=True)
                elif kind:
                    res.update(how=kind, rx=share * w, ok=True)
                elif kv.get("rounded") == "1":
                    res.update(how="rounded=1", ok=True, note="rounded=1 rounds the cell's frame; check the picture itself in draw.io")
                else:
                    res["note"] = "no clipPath round, no rounding in its data"
            if res["ok"] and res["rx"] is not None and not IMAGE_RX[0] <= res["rx"] <= IMAGE_RX[1]:
                res["note"] = f"displayed radius {res['rx']:.1f} px is outside {IMAGE_RX[0]:g}-{IMAGE_RX[1]:g}"
            out.append(res)
    return out


def html_images(path):
    """Images of the Artifact page: <image>/<svg> inside its inline SVGs, and <img> tags (border-radius)."""
    text = path.read_text()
    out = []
    # top-level inline SVGs
    depth, start = 0, None
    for m in re.finditer(r"<svg\b|</svg>", text):
        if m.group(0) == "<svg":
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                frag = text[start:m.end()]
                if "xmlns=" not in frag[:200]:
                    frag = frag.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                try:
                    out += svg_images(ET.fromstring(frag), path.parent, where=f"{path.name} figure: ")
                except ET.ParseError as ex:
                    out.append(dict(where=f"{path.name} inline SVG", how=None, rx=None, ok=False, note=f"not parseable: {ex}"))
    css = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S))
    img_rule = re.search(r"img[^{]*\{[^}]*border-radius\s*:\s*([\d.]+)px", css)
    for m in re.finditer(r"<img\b[^>]*>", text):
        tag = m.group(0)
        st = re.search(r"border-radius\s*:\s*([\d.]+)px", tag)
        rx = float(st.group(1)) if st else float(img_rule.group(1)) if img_rule else None
        out.append(dict(where=f"{path.name} <img> at offset {m.start()}", how="CSS border-radius" if rx else None, rx=rx,
                        ok=bool(rx), note="" if rx else "no border-radius on the <img> or an img rule"))
    return out


def verify_result(build):
    """(status, detail): status 'ok', 'WARN' or 'FAIL'."""
    p = build.dir / "verify-result.json"
    if not p.exists():
        return "WARN", "no verify-result.json (run verify.js in draw.io and save what it returns there)"
    try:
        r = json.loads(p.read_text())
    except ValueError as ex:
        return "WARN", f"verify-result.json is not JSON: {ex}"
    n_edges, n_nodes = len(build.geo["edges"]), len(build.geo["nodes"])
    stale = None
    if r.get("drawio_sha256") and build.drawio and build.drawio.exists():
        if r["drawio_sha256"] != sha256(build.drawio):
            stale = "it verified another .drawio (drawio_sha256 differs)"
    elif (r.get("edgesOk", 0) + r.get("edgesBad", 0), r.get("labelsOk", 0) + r.get("labelsBad", 0)) != (n_edges, n_nodes):
        stale = f"its counts ({r.get('edgesOk', 0) + r.get('edgesBad', 0)} edges, " \
                f"{r.get('labelsOk', 0) + r.get('labelsBad', 0)} labels) are not this build's ({n_edges}, {n_nodes})"
    if stale:
        return "WARN", f"stale: {stale}; run verify.js again"
    bad = []
    if not r.get("fontOk"):
        bad.append("fonts did not load")
    if r.get("edgesBad"):
        bad.append(f"{r['edgesBad']} connectors off their route")
    if r.get("labelsBad"):
        bad.append(f"{r['labelsBad']} labels wrap differently")
    if bad:
        return "FAIL", "; ".join(bad)
    tied = "" if r.get("drawio_sha256") else " (tied by counts; add drawio_sha256 to tie it to the file)"
    return "ok", f"{r.get('edgesOk')} connectors and {r.get('labelsOk')} labels match, {r.get('family', '?')} {r.get('weight', '?')}{tied}"


# ------------------------------------------------------------------------------------------ check
def check(case_dir, layout=None, access_result=None, floor=None, quiet=False):
    """Rows (check, floor, this build, status) and ok. layout: layout_engine.run()'s result (else
    recomputed from geometry.json); access_result: access.run()'s (else run here, without images)."""
    if floor is None:
        fp = floor_path()
        if not fp.exists():
            raise SystemExit(f"no floor file {fp} (diagram.floor_path)")
        floor = json.loads(fp.read_text())
    F = floor
    b = Build(case_dir)
    pals = b.palettes()
    if not pals:
        raise SystemExit(f"{b.dir}: no final-<palette>.svg (build the case first)")
    rows = []

    def row(name, want, got, status):
        rows.append((name, want, got, status))

    max_w, max_h = page_limits()
    W, H = (max(v) for v in zip(*(extent(b, fl) for fl in pals)))
    row("page size", f"<= {max_w} x {max_h} px (diagram.page)", f"{W:g} x {H:g}",
        "ok" if W <= max_w + 1e-9 and H <= max_h + 1e-9 else "FAIL")
    fs = b.geo.get("fs", 14)
    E = _fonts(b.geo)
    mono = None
    try:
        import style
        mono = style.font_family("mono")
    except Exception:  # noqa: BLE001
        mono = None
    smallest = None
    for fl in pals:
        for tx in svg_texts(b.svg(fl)):
            if smallest is None or tx["fs"] < smallest[0]:
                smallest = (tx["fs"], fl, tx["text"][:30])
    ok_txt = smallest is not None and smallest[0] >= F["text_px_min"] - 1e-9
    ok_body = fs >= F["body_text_px_min"]
    row("smallest text", f">= {F['text_px_min']:g} px; box text >= {F['body_text_px_min']:g} px",
        (f"{smallest[0]:g} px ({smallest[1]}: {smallest[2]!r})" if smallest else "no text") + f"; box text {fs:g} px",
        "ok" if ok_txt and ok_body else "FAIL")
    for fl in pals:
        missing = rendered_labels(b.svg(fl), b.geo)
        row(f"rendered labels ({fl})", "every geometry node has its text lines", "; ".join(missing[:4]) or "all match",
            "FAIL" if missing else "ok")
        texts = svg_texts(b.svg(fl))
        over = label_overlaps(texts, mono)
        row(f"label overlaps ({fl})", f"{F['label_overlaps']}", f"{len(over)} in {len(texts)} text runs"
            + (": " + "; ".join(f"{a[:20]!r} / {c[:20]!r}" for a, c in over[:3]) if over else ""),
            "ok" if len(over) <= F["label_overlaps"] else "FAIL")
    gaps = box_gaps(b.geo)
    if gaps:
        g0 = gaps[0]
        row("box gap", f">= {F['box_gap_min']:g} px", f"{g0[0]:.1f} px ({g0[1]} / {g0[2]})",
            "ok" if g0[0] >= F["box_gap_min"] - 1e-9 else "FAIL")
    clear = edge_clearances(b.geo)
    if clear:
        c0 = clear[0]
        row("edge clearance", f">= {F['edge_clearance_min']:g} px", f"{c0[0]:.1f} px ({c0[1]} by {c0[2]})",
            "ok" if c0[0] >= F["edge_clearance_min"] - 1e-9 else "FAIL")
    if layout is not None:
        probs, cross, unrouted = layout["problems"], layout["crossings"], layout["unrouted"]
        src = "layout_engine"
    else:
        probs, cross, unrouted = geometry_problems(b.geo)
        src = "from geometry.json"
    for name, items, key in (("layout problems", probs, "problems"), ("crossings", cross, "crossings"),
                             ("unrouted edges", unrouted, "unrouted")):
        row(name, f"{F['layout'][key]}", f"{len(items)} ({src})" + (f": {items[0]}" if items else ""),
            "ok" if len(items) <= F["layout"][key] else "FAIL")
    gaps = gutters(b.geo)
    off = [(a, c, ax, g) for a, c, ax, g in gaps if abs(g - F["gutter"]) > F["gutter_tolerance"]]
    row("gutters", f"all {F['gutter']:g} px", f"{len(gaps)} gaps" + (", all equal" if not off else
        ", " + "; ".join(f"{a}|{c} {g:.1f}" for a, c, ax, g in off[:4])), "ok" if not off else "FAIL")
    pdfs = sorted(b.dir.glob("final-*.pdf")) + sorted((b.dir / "deliver").glob("*.pdf"))
    pages = {p.relative_to(b.dir).as_posix(): pdf_pages(p) for p in pdfs}
    badp = {k: v for k, v in pages.items() if v != F["pdf_pages"]}
    row("PDF pages", "1 each", f"{len(pages)} PDFs" + (", one page each" if not badp else
        ", " + ", ".join(f"{k}: {v}" for k, v in badp.items())), "ok" if pages and not badp else "FAIL")
    asp = aspects(b)
    if asp:
        bad = [a for a in asp if a[0] < F["box_aspect_min"] - 1e-9]
        row("box aspect (2+ lines)", f">= {F['box_aspect_min']:.4f}",
            f"{asp[0][0]:.4f} ({asp[0][1]} {asp[0][2]:g} x {asp[0][3]:g})" + (f"; {len(bad)} below" if bad else ""),
            "ok" if not bad else "FAIL")
    n = len(b.geo["nodes"])
    row("box count", f"warn above {F['box_count_warn']} (the limit in floor.json)", str(n),
        "WARN" if n > F["box_count_warn"] else "ok")
    hs = F["house"]
    got = (b.geo.get("font"), list(b.geo.get("wght") or []))
    row("text face", f"{E.FONT} (fonts.sans) at {hs['wght'][0]}/{hs['wght'][1]}",
        f"{got[0]} at {'/'.join(map(str, got[1]))}",
        "ok" if got == (E.FONT, list(hs["wght"])) else "FAIL")
    for fl in pals:
        if not b.svg(fl).exists():
            continue
        bx, cx, miss = radii(b, fl)
        good = set(bx) == {hs["box_rx"]} and set(cx) == {hs["container_rx"]} and not miss
        row(f"corner radii ({fl})", f"boxes rx {hs['box_rx']:g}, containers rx {hs['container_rx']:g}",
            "boxes rx " + "/".join(f"{k:g}" for k in sorted(bx)) + ", containers rx " + "/".join(f"{k:g}" for k in sorted(cx))
            + (f"; {len(miss)} without a rect at their geometry ({', '.join(miss[:3])})" if miss else ""),
            "ok" if good else "FAIL")
    imgs = {}
    for fl in pals:
        if b.svg(fl).exists():
            imgs[f"SVG {fl}"] = svg_images(ET.parse(b.svg(fl)).getroot(), b.dir, where=f"final-{fl}.svg ")
    if b.drawio and b.drawio.exists():
        imgs[".drawio"] = drawio_images(b.drawio)
        if (b.dir / "deliver" / b.drawio.name).exists():
            imgs["deliver .drawio"] = drawio_images(b.dir / "deliver" / b.drawio.name)
    if b.html:
        imgs["Artifact"] = html_images(b.html)
    for where, found in imgs.items():
        if not found:
            row(f"images rounded ({where})", "every image", "no images", "ok")
            continue
        bad = [i for i in found if not i["ok"]]
        warn = [i for i in found if i["ok"] and i["note"]]
        detail = f"{len(found)} image(s), " + ", ".join(sorted({i['how'] or 'none' for i in found}))
        rxs = [i["rx"] for i in found if i["rx"] is not None]
        if rxs:
            detail += f", radius {min(rxs):.1f}-{max(rxs):.1f} px"
        for i in (bad + warn)[:3]:
            detail += f"; {i['where']}: {i['note']}"
        row(f"images rounded ({where})", f"every image, radius {F['image_rx_range'][0]:g}-{F['image_rx_range'][1]:g} px", detail,
            "FAIL" if bad else "WARN" if warn else "ok")
    if access_result is None:
        import access
        access_result = access.run(b.dir, sims=False, quiet=True, flavours=pals,
                                   exceptions=F.get("contrast_exceptions", []))
    a = access_result
    row("contrast (access.py)", f"text >= {F['contrast']['text_min']:g}, arrows >= {F['contrast']['arrow_min']:g}",
        f"lowest text {a['min_text']:.2f}, arrows {a['min_arrow']:.2f}; {len(a['fails'])} failure(s)"
        + (f", {len(a['known'])} known shortfall(s) of the configured palettes" if a.get("known") else ""),
        "ok" if a["ok"] and not a.get("known") else "WARN" if a["ok"] else "FAIL")
    vs, vd = verify_result(b)
    row("draw.io verify", "connectors and labels OK", vd, vs)
    ok = not any(r[3] == "FAIL" for r in rows)
    if not quiet:
        print(table(rows, F, b))
    return rows, ok


def table(rows, F, b):
    w0 = max(len(r[0]) for r in rows)
    w1 = min(42, max(len(r[1]) for r in rows))
    out = [f"FLOOR  {F.get('source', 'floor.json')} minimums  ->  {b.dir}"]
    for name, want, got, status in rows:
        out.append(f"  {status:4s}  {name:<{w0}}  {want:<{w1}}  {got}")
    fails = [r for r in rows if r[3] == "FAIL"]
    warns = [r for r in rows if r[3] == "WARN"]
    out.append(f"FLOOR {'BREACHED' if fails else 'HELD'}: {len(fails)} breach(es), {len(warns)} warning(s)"
               + (" (" + ", ".join(r[0] for r in fails) + ")" if fails else ""))
    return "\n".join(out)


def main(argv):
    ap = argparse.ArgumentParser(description="Check a build against the quality minimums in floor.json.")
    ap.add_argument("case_dir")
    ap.add_argument("--floor", help="the floor file (default: diagram.floor_path, else engine/floor.json)")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
        fl = json.loads(pathlib.Path(a.floor).read_text()) if a.floor else None
        _, ok = check(a.case_dir, floor=fl)
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    return 0 if ok else 1


if __name__ == "__main__":
    _bootstrap.entry()
    sys.exit(main(sys.argv[1:]))
