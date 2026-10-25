#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What changed since the accepted build: a text summary and an overlay picture.

    python3 diffviz.py <case_dir>                        # compare the build with <case_dir>/accepted/
    python3 diffviz.py <case_dir> --accept               # snapshot the build as accepted
    python3 diffviz.py <case_dir> --accept --from DIR    # snapshot another build (read-only) as accepted

accepted/ holds geometry.json, every final-<palette>.png and accepted.json (when, from where,
the files' sha256). The comparison reads geometry.json on both sides:
  canvas     width and height
  boxes      added, removed, moved (centre shifted by more than 0.5 px), resized, text changed (word
             by word), role, stage or weight changed; a shift that at least 5 boxes (and a tenth of
             them) share is reported once, as a band that moved together (a taller header, an
             inserted panel), and the arrows and containers that moved with it are not "rerouted"
  arrows     added, removed, kind changed, rerouted (keyed by source and target)
  stages     added, removed, moved or resized (the key included)
and writes <case_dir>/checks/diff-overlay.png: the print palette's new render, washed out, with the accepted
boxes outlined in dashed orange and the new ones in solid blue (Okabe-Ito colours, which stay apart
for every common colour-vision deficiency, and a dash so they differ without colour too). Changed
items are drawn thick: an added box gets a blue tint, a removed box an orange one, a box whose words
changed a "T" tag, a rerouted arrow both routes, and a moved box a thin line from its old top-left
corner to its new one (clear of its words). Where the old and new renders have the same size, the
pixels that differ outside the outlined changes are tinted magenta: they show what the geometry
does not, such as a colour, a stroke or a new glyph. A band above the picture holds the legend and
the counts. build.py prints the summary whenever accepted/ exists.
"""
from _legacy import read_legacy_text
import argparse
import datetime
import difflib
import hashlib
import json
import pathlib
import shutil
import sys

ENGINE = pathlib.Path(__file__).resolve().parent
ACCEPTED = "accepted"


def snapshot_files(d):
    """What an accepted/ snapshot holds: geometry.json and every final-<palette>.png of the full case."""
    pngs = sorted(p.name for p in pathlib.Path(d).glob("final-*.png") if ".static" not in p.name)
    return ("geometry.json", *pngs)


def print_png_name():
    try:
        import themes
        return f"final-{themes.print_palette()}.png"
    except Exception:  # noqa: BLE001  (no config: the first PNG on disk)
        return None
OLD, NEW, TEXT, PIX = (213, 94, 0), (0, 114, 178), (204, 121, 167), (230, 0, 160)    # Okabe-Ito
LIGHT = lambda c: tuple(int(v * 0.45 + 255 * 0.55) for v in c)   # the thin outlines of boxes that did not change on their own
TOL = 0.5
BAND = 76


def sha256(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------ accept
def accept(case_dir, src=None):
    d = pathlib.Path(case_dir).resolve()
    s = pathlib.Path(src).resolve() if src else d
    FILES = snapshot_files(s)
    missing = [f for f in FILES if not (s / f).exists()]
    if missing or len(FILES) < 2:
        raise SystemExit(f"cannot accept {s}: missing {', '.join(missing) or 'final-<palette>.png'} (build it first)")
    a = d / ACCEPTED
    a.mkdir(exist_ok=True)
    for f in FILES:
        shutil.copyfile(s / f, a / f)
    meta = {"accepted": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "from": str(s),
            "sha256": {f: sha256(a / f) for f in FILES}}
    (a / "accepted.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"accepted {s} -> {a} ({', '.join(FILES)})")
    return a


# ------------------------------------------------------------------------------------------ compare
def panels(g):
    """The stage containers, the key and, when there is one, the inset."""
    out = dict(g["clusters"], key=g["key"])
    if isinstance(g.get("inset"), dict) and g["inset"].get("rect"):
        out["inset"] = g["inset"]["rect"]
    return out


def canvas(g):
    """The page size render.py gives the geometry: symmetric side margins, 40 px below the lowest container."""
    rs = [r for k, r in panels(g).items() if k != "key"]
    xs = [r[0] for r in rs] + [r[2] for r in rs]
    ys = [r[3] for r in rs]
    return max(xs) + min(xs), max(ys) + 40


def text_of(b):
    return " ".join(b["lines"])


def word_diff(a, b):
    wa, wb = a.split(), b.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb).get_opcodes():
        if op == "equal":
            seg = wa[i1:i2]
            out.append(" ".join(seg) if len(seg) <= 6 else " ".join(seg[:2] + ["…"] + seg[-2:]))
        else:
            if i2 > i1:
                out.append("[-" + " ".join(wa[i1:i2]) + "-]")
            if j2 > j1:
                out.append("{+" + " ".join(wb[j1:j2]) + "+}")
    return " ".join(out).replace("\u00a0", " ")


def compare(old, new):
    """Every difference between two geometries, as a dict of lists."""
    on, nn = old["nodes"], new["nodes"]
    ch = {k: [] for k in ("added", "removed", "moved", "resized", "text", "role", "stage", "bold",
                          "edge_added", "edge_removed", "edge_kind", "rerouted", "stage_changed", "stage_added", "stage_removed")}
    for k in nn:
        if k not in on:
            ch["added"].append(k)
    for k in on:
        if k not in nn:
            ch["removed"].append(k)
            continue
        if k not in nn:
            continue
        a, b = on[k], nn[k]
        if abs(a["x"] - b["x"]) > TOL or abs(a["y"] - b["y"]) > TOL:
            ch["moved"].append((k, b["x"] - a["x"], b["y"] - a["y"]))
        if abs(a["w"] - b["w"]) > TOL or abs(a["h"] - b["h"]) > TOL:
            ch["resized"].append((k, f"{a['w']:g}x{a['h']:g} -> {b['w']:g}x{b['h']:g}"))
        if text_of(a) != text_of(b):
            ch["text"].append((k, word_diff(text_of(a), text_of(b))))
        for f, key in (("role", "role"), ("cluster", "stage"), ("bold", "bold")):
            if a.get(f) != b.get(f):
                ch[key].append((k, f"{a.get(f)} -> {b.get(f)}"))
    oe = {(e["a"], e["b"]): e for e in old["edges"]}
    ne = {(e["a"], e["b"]): e for e in new["edges"]}
    for k, e in ne.items():
        if k not in oe:
            ch["edge_added"].append(k)
            continue
        o = oe[k]
        if o["kind"] != e["kind"]:
            ch["edge_kind"].append((k, f"{o['kind']} -> {e['kind']}"))
        if len(o["pts"]) != len(e["pts"]) or any(abs(p - q) > TOL for P, Q in zip(o["pts"], e["pts"]) for p, q in zip(P, Q)):
            ch["rerouted"].append(k)
    ch["edge_removed"] = [k for k in oe if k not in ne]
    oc, nc = panels(old), panels(new)
    ch["stage_added"] = [k for k in nc if k not in oc]
    ch["stage_removed"] = [k for k in oc if k not in nc]
    for k in nc:
        if k in oc and any(abs(p - q) > TOL for p, q in zip(oc[k], nc[k])):
            ch["stage_changed"].append((k, "[" + ", ".join(f"{v:g}" for v in oc[k]) + "] -> ["
                                        + ", ".join(f"{v:g}" for v in nc[k]) + "]"))
    ch["canvas"] = (canvas(old), canvas(new))
    group_shifts(ch, old, new)
    return ch


def group_shifts(ch, old, new):
    """Separate the moves that whole bands share (a taller header, an inserted panel) from the rest:
    a shift vector shared by at least 5 boxes (and a tenth of them) is a band shift. Arrows whose every
    point moved by one band shift, and stages that moved by one at their old size, moved with their band."""
    from collections import Counter
    vec = lambda dx, dy: (round(dx, 1), round(dy, 1))
    counts = Counter(vec(dx, dy) for _, dx, dy in ch["moved"])
    need = max(5, len(new["nodes"]) / 10)
    bands = {v for v, n in counts.items() if n >= need}
    ch["bands"] = [(v, [k for k, dx, dy in ch["moved"] if vec(dx, dy) == v]) for v, _ in counts.most_common() if v in bands]
    ch["moved_alone"] = [m for m in ch["moved"] if vec(m[1], m[2]) not in bands]
    near = lambda d, v: abs(d[0] - v[0]) <= TOL and abs(d[1] - v[1]) <= TOL
    oe = {(e["a"], e["b"]): e for e in old["edges"]}
    ne = {(e["a"], e["b"]): e for e in new["edges"]}
    ch["edge_shifted"] = []
    for k in list(ch["rerouted"]):
        o, n = oe[k]["pts"], ne[k]["pts"]
        if len(o) == len(n):
            ds = [(q[0] - p[0], q[1] - p[1]) for p, q in zip(o, n)]
            if any(all(near(d, v) for d in ds) for v in bands):
                ch["rerouted"].remove(k)
                ch["edge_shifted"].append(k)
    oc, nc = panels(old), panels(new)
    ch["stage_shifted"] = []
    for k, desc in list(ch["stage_changed"]):
        a, b = oc[k], nc[k]
        d0, d1 = (b[0] - a[0], b[1] - a[1]), (b[2] - a[2], b[3] - a[3])
        if any(near(d0, v) and near(d1, v) for v in bands):
            ch["stage_changed"].remove((k, desc))
            ch["stage_shifted"].append(k)


def summary(ch, meta=None, overlay=None):
    (ow, oh), (nw, nh) = ch["canvas"]
    head = "DIFF against accepted/" + (f" (accepted {meta.get('accepted', '?')} from {meta.get('from', '?')})" if meta else "")
    out = [head]
    out.append(f"  canvas {ow:g}x{oh:g} -> {nw:g}x{nh:g}" + ("" if (ow, oh) == (nw, nh) else
                                                             f" ({nw - ow:+g} px wide, {nh - oh:+g} px tall)"))
    nb = sum(len(ch[k]) for k in ("added", "removed", "moved", "resized", "text", "role", "stage", "bold"))
    nb += len(ch.get("stage_shifted", [])) + len(ch.get("edge_shifted", []))
    ne = sum(len(ch[k]) for k in ("edge_added", "edge_removed", "edge_kind", "rerouted"))
    ns = sum(len(ch[k]) for k in ("stage_added", "stage_removed", "stage_changed"))
    if not (nb or ne or ns) and (ow, oh) == (nw, nh):
        out.append("  no changes in the geometry")
    names = lambda xs: ", ".join(x if isinstance(x, str) else x[0] if isinstance(x[0], str) else f"{x[0]}->{x[1]}" for x in xs)
    ek = lambda xs: ", ".join(f"{a}->{b}" for a, b in xs)
    for key, label in (("added", "boxes added"), ("removed", "boxes removed")):
        if ch[key]:
            out.append(f"  {label} ({len(ch[key])}): {names(ch[key])}")
    for (dx, dy), ids in ch.get("bands", []):
        out.append(f"  moved together by ({dx:+g}, {dy:+g}): {len(ids)} boxes ({', '.join(ids[:6])}{' …' if len(ids) > 6 else ''})")
    alone = ch.get("moved_alone", ch["moved"])
    if alone:
        out.append(f"  boxes moved {'on their own ' if ch.get('bands') else ''}({len(alone)}): "
                   + ", ".join(f"{k} ({dx:+.0f}, {dy:+.0f})" for k, dx, dy in alone[:12]) + (" …" if len(alone) > 12 else ""))
    if ch.get("edge_shifted") or ch.get("stage_shifted"):
        out.append(f"  moved with their band: {len(ch.get('edge_shifted', []))} arrows, {len(ch.get('stage_shifted', []))} "
                   f"containers ({', '.join(ch.get('stage_shifted', []))})")
    if ch["resized"]:
        out.append(f"  boxes resized ({len(ch['resized'])}): " + ", ".join(f"{k} {v}" for k, v in ch["resized"][:12])
                   + (" …" if len(ch["resized"]) > 12 else ""))
    for key, label in (("role", "role changed"), ("stage", "stage changed"), ("bold", "weight changed")):
        if ch[key]:
            out.append(f"  {label}: " + ", ".join(f"{k} {v}" for k, v in ch[key]))
    if ch["edge_added"]:
        out.append(f"  arrows added ({len(ch['edge_added'])}): {ek(ch['edge_added'])}")
    if ch["edge_removed"]:
        out.append(f"  arrows removed ({len(ch['edge_removed'])}): {ek(ch['edge_removed'])}")
    if ch["edge_kind"]:
        out.append("  arrow kind changed: " + ", ".join(f"{a}->{b} {v}" for (a, b), v in ch["edge_kind"]))
    if ch["rerouted"]:
        out.append(f"  arrows rerouted ({len(ch['rerouted'])}): {ek(ch['rerouted'][:12])}" + (" …" if len(ch["rerouted"]) > 12 else ""))
    for key, label in (("stage_added", "stages added"), ("stage_removed", "stages removed")):
        if ch[key]:
            out.append(f"  {label}: {', '.join(ch[key])}")
    if ch["stage_changed"]:
        out.append(f"  stages moved or resized ({len(ch['stage_changed'])}): " + "; ".join(f"{k} {v}" for k, v in ch["stage_changed"]))
    if ch["text"]:
        out.append(f"  text changes ({len(ch['text'])}):")
        out += [f"    {k}: {d}" for k, d in ch["text"]]
    if overlay:
        out.append(f"  overlay: {overlay}")
    return out


# ------------------------------------------------------------------------------------------ overlay
def _font(size):
    from PIL import ImageFont
    try:
        import style
        return ImageFont.truetype(str(style.font_path("sans", 500)), size)
    except Exception:  # noqa: BLE001  (no configured font: Pillow's own)
        return ImageFont.load_default()


def _dashed_rect(dr, box, colour, width, dash=7, gap=5):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        L = max(abs(bx - ax), abs(by - ay))
        n = int(L // (dash + gap)) + 1
        for i in range(n):
            t0, t1 = i * (dash + gap) / L if L else 0, min(1, (i * (dash + gap) + dash) / L) if L else 1
            dr.line((ax + (bx - ax) * t0, ay + (by - ay) * t0, ax + (bx - ax) * t1, ay + (by - ay) * t1), fill=colour, width=width)


def _dashed_poly(dr, pts, colour, width, dash=8, gap=6):
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        L = max(abs(bx - ax), abs(by - ay)) or 1
        t = 0.0
        while t < L:
            t1 = min(L, t + dash)
            dr.line((ax + (bx - ax) * t / L, ay + (by - ay) * t / L, ax + (bx - ax) * t1 / L, ay + (by - ay) * t1 / L),
                    fill=colour, width=width)
            t += dash + gap


def _flat(im):
    """An RGB copy on white (the page PNGs may have transparent rounded corners)."""
    from PIL import Image
    im = im.convert("RGBA")
    out = Image.new("RGB", im.size, (255, 255, 255))
    out.paste(im, mask=im.getchannel("A"))
    return out


def _rect(b):
    return (b["x"] - b["w"] / 2, b["y"] - b["h"] / 2, b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)


def overlay(case_dir, old, new, ch, out_path):
    """Draw the overlay picture (see the module docstring). Returns its path."""
    import numpy as np
    from PIL import Image, ImageDraw
    d = pathlib.Path(case_dir)
    (ow, oh), (nw, nh) = ch["canvas"]
    W, H = int(max(ow, nw)), int(max(oh, nh))
    img = Image.new("RGB", (W, H + BAND), (255, 255, 255))
    name = print_png_name() or next(iter(snapshot_files(d)[1:]), "final.png")
    new_png, old_png = d / name, d / ACCEPTED / name
    npix = npix_left = None
    if new_png.exists():
        base = _flat(Image.open(new_png))
        small = base.resize((int(nw), int(nh)), Image.LANCZOS)
        wash = Image.blend(small, Image.new("RGB", small.size, (255, 255, 255)), 0.55)
        img.paste(wash, (0, BAND))
        if old_png.exists():
            ob = _flat(Image.open(old_png))
            if ob.size == base.size:
                diff = np.any(np.asarray(ob, dtype=np.int16) != np.asarray(base, dtype=np.int16), axis=2)
                npix = int(diff.sum())
                # pixels the outlined changes already explain are not tinted, so the tint shows only what
                # the geometry does not (a colour, a stroke, a glyph)
                known = Image.new("L", base.size, 0)
                kd = ImageDraw.Draw(known)
                s15 = base.size[0] / nw
                grow = lambda r, g: (r[0] * s15 - g, r[1] * s15 - g, r[2] * s15 + g, r[3] * s15 + g)
                changed = {k for k, *_ in ch["moved"]} | {k for k, _ in ch["resized"]} | {k for k, _ in ch["text"]} | \
                    set(ch["added"]) | set(ch["removed"]) | {k for k, _ in ch["role"]} | {k for k, _ in ch["bold"]}
                for g in (old, new):
                    for k in changed:
                        if k in g["nodes"]:
                            kd.rectangle(grow(_rect(g["nodes"][k]), 6), fill=255)
                    for e in g["edges"]:
                        k = (e["a"], e["b"])
                        if k in ch["rerouted"] or k in ch["edge_added"] or k in ch["edge_removed"] or \
                                k in [x for x, _ in ch["edge_kind"]]:
                            kd.line([(x * s15, y * s15) for x, y in e["pts"]], fill=255, width=int(14 * s15))
                diff &= np.asarray(known) == 0
                npix_left = int(diff.sum())
                if npix_left:
                    mask = Image.fromarray((diff * 255).astype(np.uint8)).resize((int(nw), int(nh)), Image.BOX)
                    mask = mask.point(lambda v: 150 if v > 0 else 0)
                    tint = Image.new("RGB", mask.size, PIX)
                    region = img.crop((0, BAND, int(nw), BAND + int(nh)))
                    img.paste(Image.composite(tint, region, mask), (0, BAND))
    dr = ImageDraw.Draw(img)
    off = lambda r: (r[0], r[1] + BAND, r[2], r[3] + BAND)
    pt = lambda p: (p[0], p[1] + BAND)
    shifted = {k for k, *_ in ch.get("moved_alone", ch["moved"])}
    moved = shifted | {k for k, _ in ch["resized"]}
    texts = {k for k, _ in ch["text"]}
    for k, b in new["nodes"].items():
        r = off(_rect(b))
        if k in ch["added"]:
            dr.rectangle(r, fill=tuple(int(c * 0.25 + 255 * 0.75) for c in NEW))
        loud = k in moved or k in ch["added"]
        dr.rectangle(r, outline=NEW if loud else LIGHT(NEW), width=4 if loud else 1)
        if k in shifted and k in old["nodes"]:
            # the move, drawn between the old and new top-left corners, clear of the words
            o = _rect(old["nodes"][k])
            a, c = pt((o[0], o[1])), pt((r[0], r[1] - BAND))
            dr.line((*a, *c), fill=NEW, width=2)
            dr.ellipse((c[0] - 3, c[1] - 3, c[0] + 3, c[1] + 3), fill=NEW)
        if k in texts:
            tx, ty = r[2] - 6, r[1] - 12
            dr.rounded_rectangle((tx - 12, ty, tx + 12, ty + 22), radius=6, fill=TEXT)
            dr.text((tx, ty + 11), "T", fill=(255, 255, 255), font=_font(16), anchor="mm")
    for k, b in old["nodes"].items():
        thick = k in moved or k in ch["removed"]
        if k in ch["removed"]:
            r = off(_rect(b))
            dr.rectangle(r, fill=tuple(int(c * 0.25 + 255 * 0.75) for c in OLD))
        _dashed_rect(dr, off(_rect(b)), OLD if thick else LIGHT(OLD), 4 if thick else 1)
    oe = {(e["a"], e["b"]): e for e in old["edges"]}
    ne = {(e["a"], e["b"]): e for e in new["edges"]}
    for k in ch["rerouted"] + ch["edge_removed"] + [k for k, _ in ch["edge_kind"]]:
        if k in oe:
            _dashed_poly(dr, [pt(p) for p in oe[k]["pts"]], OLD, 3)
    for k in ch["rerouted"] + ch["edge_added"] + [k for k, _ in ch["edge_kind"]]:
        if k in ne:
            dr.line([pt(p) for p in ne[k]["pts"]], fill=NEW, width=3, joint="curve")
    for k, _ in ch["stage_changed"]:
        if k in panels(new):
            _dashed_rect(dr, off(panels(old)[k]), OLD, 3, dash=14, gap=8)
            dr.rectangle(off(panels(new)[k]), outline=NEW, width=3)
    # the band: legend and counts
    f, fs = _font(18), _font(15)
    dr.rectangle((0, 0, W, BAND - 1), fill=(246, 247, 249))
    dr.line((0, BAND - 1, W, BAND - 1), fill=(200, 204, 212), width=1)
    x, y = 24, 14
    _dashed_rect(dr, (x, y, x + 34, y + 20), OLD, 3)
    dr.text((x + 44, y + 10), "accepted (dashed)", fill=(40, 40, 40), font=f, anchor="lm")
    x += 230
    dr.rectangle((x, y, x + 34, y + 20), outline=NEW, width=3)
    dr.text((x + 44, y + 10), "this build (solid)", fill=(40, 40, 40), font=f, anchor="lm")
    x += 220
    dr.rounded_rectangle((x, y - 1, x + 24, y + 21), radius=6, fill=TEXT)
    dr.text((x + 12, y + 10), "T", fill=(255, 255, 255), font=_font(16), anchor="mm")
    dr.text((x + 34, y + 10), "words changed", fill=(40, 40, 40), font=f, anchor="lm")
    x += 200
    dr.rectangle((x, y, x + 34, y + 20), fill=PIX)
    dr.text((x + 44, y + 10), "other pixels that differ", fill=(40, 40, 40), font=f, anchor="lm")
    counts = (f"{len(ch['added'])} added, {len(ch['removed'])} removed, {len(ch.get('moved_alone', ch['moved']))} moved"
              + (f" (+{len(ch['moved']) - len(ch['moved_alone'])} with their band)" if ch.get("bands") else "")
              + f", {len(ch['resized'])} resized, "
              f"{len(ch['text'])} reworded boxes;  {len(ch['edge_added'])} added, {len(ch['edge_removed'])} removed, "
              f"{len(ch['rerouted'])} rerouted arrows;  {len(ch['stage_changed'])} stages moved;  canvas {ow:g}x{oh:g} -> "
              f"{nw:g}x{nh:g};  " + (f"{npix} pixels differ at 1.5x, {npix_left} outside the outlined changes" if npix is not None
                                    else "renders of different sizes: no pixel comparison"))
    dr.text((24, 52), counts, fill=(60, 60, 60), font=fs, anchor="lm")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, compress_level=3)
    return out_path


# ------------------------------------------------------------------------------------------ main
def run(case_dir, draw=True, quiet=False):
    """Compare with accepted/; returns the summary lines, or None when there is no accepted/ snapshot."""
    d = pathlib.Path(case_dir).resolve()
    a = d / ACCEPTED
    if not (a / "geometry.json").exists():
        return None
    old = json.loads(read_legacy_text(a / "geometry.json"))
    new = json.loads(read_legacy_text(d / "geometry.json"))
    meta = json.loads(read_legacy_text(a / "accepted.json")) if (a / "accepted.json").exists() else None
    ch = compare(old, new)
    out = None
    if draw:
        out = overlay(d, old, new, ch, d / "checks" / "diff-overlay.png").relative_to(d)
    lines = summary(ch, meta, out)
    if not quiet:
        print("\n".join(lines))
    return lines


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Compare a build with its accepted snapshot, or take one.")
    ap.add_argument("case_dir")
    ap.add_argument("--accept", action="store_true", help="snapshot the build (or --from DIR) as accepted/")
    ap.add_argument("--from", dest="src", help="with --accept: the build folder to snapshot (read-only)")
    ap.add_argument("--no-image", action="store_true", help="summary only")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    if a.accept:
        accept(a.case_dir, a.src)
        return 0
    if run(a.case_dir, draw=not a.no_image) is None:
        print(f"no {ACCEPTED}/ snapshot in {a.case_dir}: take one with --accept")
    return 0


if __name__ == "__main__":
    import _bootstrap
    _bootstrap.entry()
    sys.exit(main(sys.argv[1:]))
