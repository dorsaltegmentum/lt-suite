#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prepare a built diagram for the rubric grade-through (references/grade-through.md).

    python3 grade_prep.py <case_dir> <out_dir> [--config FILE]

Writes into <out_dir>:
  diagram-dump.md            every box (id, role, stage, centre, size, rendered lines) and every arrow
                             (source -> target, kind, label), read from <case_dir>/geometry.json
  full.png                   the print palette's render at full size (copied)
  overview-half.png          the print palette's render at half size
  overview-<palette>-half.png every other palette's render at half size
  tile-r{0..2}c{0..3}.png    twelve overlapping full-resolution tiles of the print palette's render

The graders read these instead of the PDF: a dump gives them exact text and line breaks, and the tiles
let them look at every arrow at full resolution without loading a 4000-px image whole.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)
from _legacy import read_legacy_text
from PIL import Image

ROWS, COLS, OVERLAP = 3, 4, 0.12


def find_png(case, flavour):
    for name in (f"final-{flavour}.png",):
        if (case / name).exists():
            return case / name
    hits = sorted((case / "deliver").glob(f"*{flavour}*.png")) if (case / "deliver").exists() else []
    if hits:
        return hits[0]
    raise SystemExit(f"no {flavour} PNG in {case} (build the case first)")


def dump(case, out):
    g = json.loads(read_legacy_text(case / "geometry.json"))
    titles = {}
    cj = case / "case.json"
    if cj.exists():
        titles = json.loads(read_legacy_text(cj)).get("stage_titles", {})
    lines = ["# Diagram dump", "", f"{len(g['nodes'])} boxes, {len(g['edges'])} arrows, "
             f"{len(g['clusters'])} stages. Coordinates are the box centre in canvas px; the PNGs are 1.5x.", ""]
    by_cluster = {}
    for nid, n in g["nodes"].items():
        by_cluster.setdefault(n.get("cluster"), []).append((nid, n))
    for cl, items in by_cluster.items():
        rect = g["clusters"].get(cl)
        head = f"## Stage `{cl}`" + (f": {titles[cl]}" if cl in titles else "")
        lines += [head, f"container {rect}" if rect else "", ""]
        for nid, n in sorted(items, key=lambda t: (t[1]["y"], t[1]["x"])):
            text = " | ".join(n["lines"])
            lines.append(f"- `{nid}` ({n['role']}{', bold' if n.get('bold') else ''}) at ({n['x']:.0f}, {n['y']:.0f}) "
                         f"{n['w']:.0f}x{n['h']:.0f}: {text}")
        lines.append("")
    lines += ["## Arrows", ""]
    for e in g["edges"]:
        lab = f' "{e["label"]}"' if e.get("label") else ""
        flag = "" if e.get("ok", True) else "  (ROUTING PROBLEM)"
        lines.append(f"- `{e['a']}` -> `{e['b']}` [{e['kind']}]{lab}{flag}")
    (out / "diagram-dump.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def tiles(png, out):
    im = Image.open(png)
    W, H = im.size
    tw, th = W / COLS, H / ROWS
    ox, oy = tw * OVERLAP, th * OVERLAP
    for r in range(ROWS):
        for c in range(COLS):
            box = (int(max(0, c * tw - ox)), int(max(0, r * th - oy)), int(min(W, (c + 1) * tw + ox)), int(min(H, (r + 1) * th + oy)))
            im.crop(box).save(out / f"tile-r{r}c{c}.png", optimize=True)


def main(argv=None):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Prepare a built diagram for the rubric grade-through.")
    ap.add_argument("case_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    a = ap.parse_args(argv)
    import style
    import themes
    try:
        style.configure(a.config)
        pp, pals = themes.print_palette(), themes.final_palettes()
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    case, out = Path(a.case_dir).resolve(), Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    dump(case, out)
    main_png = find_png(case, pp)
    shutil.copy2(main_png, out / "full.png")
    jobs = [(main_png, "overview-half.png")] + [(case / f"final-{fl}.png", f"overview-{fl}-half.png")
                                                for fl in pals if fl != pp and (case / f"final-{fl}.png").exists()]
    for src, name in jobs:
        im = Image.open(src)
        im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(out / name, optimize=True)
    tiles(main_png, out)
    print(f"grade prep written to {out}")


if __name__ == "__main__":
    _bootstrap.entry()
    main()
