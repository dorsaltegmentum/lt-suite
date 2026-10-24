#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Draw palette and font previews for the setup questions.

    python3 preview.py palettes [--top 4] [--out DIR] [--mode light|dark] [--ids a,b]
    python3 preview.py fonts    [--top 4] [--out DIR] [--ids a,b]

palettes ranks the palette catalog by the measured rank and draws one PNG per palette
with Pillow: the 7 role swatches with sample text, the 4 edge strokes, and the canvas,
text, muted and label colours, all taken from the palette's derived theme (the colours
the diagrams and PDFs use). It prints a contrast table and writes palettes.json and
palettes.md beside the PNGs. The three original options (Catppuccin Latte, Catppuccin
Frappe, Everforest Dark Medium) are listed after the top of the ranking.

fonts lists the font catalog (the configured defaults, the low-vision options, then by
availability) with licence and state (bundled, installed, downloaded, downloadable) and
draws a sample PNG for every font whose file is on this machine.

Neither command needs rsvg-convert, a diagram engine or a network. Pillow is the only
third-party package. Output goes to <cache>/setup/ unless --out is given.
Exit codes: 0 ok, 1 error, 3 Pillow is not installed.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
for _base in (HERE / "_shared", HERE.parent.parent.parent / "shared"):
    if (_base / "ltcore").is_dir():
        sys.path.insert(0, str(_base))
        break
sys.path.insert(0, str(HERE))

import ltcore  # noqa: E402

MISSING = ltcore.bootstrap(["PIL"])

import argparse  # noqa: E402
import json  # noqa: E402

import _catalog  # noqa: E402
from ltcore import config, store  # noqa: E402

EDGE_LABELS = {"stim": "activates", "inh": "inhibits", "excl": "argues against", "req": "depends on"}
ROLE_ORDER = ("risk", "mech", "anat", "sign", "inv", "dx", "tx")
WIDTH = 1280


# ------------------------------------------------------------------------- drawing

def need_pillow():
    if MISSING:
        print(ltcore.missing_hint(MISSING), file=sys.stderr)
        raise SystemExit(3)
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


def load_font(size, cfg=None, bold=False):
    """A TrueType font for labels: the configured sans, then a common system face,
    then Pillow's built-in one. Returns (font, source)."""
    _Image, _Draw, ImageFont = need_pillow()
    fonts = _catalog.fonts_module()
    if fonts is not None:
        # A variable font draws at its default weight unless a static instance is cut
        # (fontTools, cached under <cache>/font-cache/); fall back to the plain file.
        for static in (True, False):
            try:
                path = fonts.resolve("sans", weight=700 if bold else 400, cfg=cfg, static=static, os_fallback=True)
                return ImageFont.truetype(str(path), size), "ltcore.fonts"
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size), "pillow default"
    except TypeError:  # Pillow older than 10.1 has no sized default
        return ImageFont.load_default(), "pillow default"


def text_w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def text_h(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def draw_edge(draw, kind, spec, x1, x2, y, canvas, font):
    """One edge stroke with its dash style and head, labelled."""
    colour, dash, head = spec
    width = 4
    if dash is True:
        pattern = (14, 8)
    elif isinstance(dash, str) and dash:
        pattern = (2, 8)  # the dotted style of "argues against"
    else:
        pattern = None
    if pattern is None:
        draw.line([(x1, y), (x2 - 10, y)], fill=colour, width=width)
    else:
        x = x1
        while x < x2 - 10:
            draw.line([(x, y), (min(x + pattern[0], x2 - 10), y)], fill=colour, width=width)
            x += pattern[0] + pattern[1]
    if head == "tee":
        draw.line([(x2 - 6, y - 12), (x2 - 6, y + 12)], fill=colour, width=width + 1)
    else:
        draw.polygon([(x2, y), (x2 - 16, y - 9), (x2 - 16, y + 9)], fill=colour)
    draw.text((x1, y + 12), EDGE_LABELS.get(kind, kind), fill=colour, font=font)


def rank_label(row):
    """'light 3 of 19', 'unranked', or 'option' for an entry kept out of the defaults."""
    if row.get("rank") is None:
        return "unranked"
    label = "%s %s" % (row.get("mode") or "?", row["rank"])
    if row.get("rank_of"):
        label += " of %s" % row["rank_of"]
    if row.get("default_eligible") is False:
        label += " (option, not a default)"
    return label


def palette_png(row, cfg, out_path, pal):
    """Draw one palette sheet. Returns the contrast rows for the table."""
    Image, ImageDraw, _F = need_pillow()
    th = row["theme"]
    f_small, _src = load_font(15, cfg)
    f_text, _s = load_font(18, cfg)
    f_bold, _s = load_font(22, cfg, bold=True)
    f_title, _s = load_font(30, cfg, bold=True)
    pad = 28
    sw_w, sw_h, gap = 154, 112, 14
    n_rows = len(ROLE_ORDER)
    table_rows = []
    for r in ROLE_ORDER:
        fill, stroke, text = th["roles"][r]
        table_rows.append({"role": r, "text_on_fill": round(pal.contrast(text, fill), 2),
                           "stroke_on_fill": round(pal.contrast(stroke, fill), 2),
                           "stroke_on_canvas": round(pal.contrast(stroke, th["canvas"]), 2)})
    height = pad * 2 + 70 + (sw_h + 18) + 150 + 110 + 30 + (n_rows + 2) * 28 + 40
    img = Image.new("RGB", (WIDTH, height), th["canvas"])
    d = ImageDraw.Draw(img)
    y = pad
    d.text((pad, y), row["name"], fill=th["ink"], font=f_title)
    y += 40
    meta = "%s  |  %s  |  %s  |  licence %s" % (
        row["id"], row["mode"] or "?", rank_label(row), row["licence"] or "see catalog")
    d.text((pad, y), meta, fill=th["muted"], font=f_text)
    y += 40
    # 7 role swatches: fill, stroke and text colour, with the measured text contrast
    for i, r in enumerate(ROLE_ORDER):
        fill, stroke, text = th["roles"][r]
        x = pad + i * (sw_w + gap)
        d.rounded_rectangle([x, y, x + sw_w, y + sw_h], radius=14, fill=fill, outline=stroke, width=4)
        d.text((x + 12, y + 10), r, fill=text, font=f_bold)
        d.text((x + 12, y + 44), "Ca²⁺ IL-1β", fill=text, font=f_text)
        d.text((x + 12, y + 76), "%.1f:1" % table_rows[i]["text_on_fill"], fill=text, font=f_small)
    y += sw_h + 22
    # a container (stage) in the container colours
    d.rounded_rectangle([pad, y, WIDTH - pad, y + 64], radius=12, fill=th["cl_fill"],
                        outline=th["cl_stroke"], width=3)
    d.text((pad + 16, y + 20), "Stage title on the container fill", fill=th["cl_text"], font=f_bold)
    y += 90
    # 4 edge strokes
    col_w = (WIDTH - 2 * pad) // 4
    for i, kind in enumerate(("stim", "inh", "excl", "req")):
        draw_edge(d, kind, th["edges"][kind], pad + i * col_w + 4, pad + (i + 1) * col_w - 40, y, th["canvas"], f_text)
    y += 64
    # canvas, text, muted and label colours
    for i, (name, colour) in enumerate((("canvas", th["canvas"]), ("ink", th["ink"]),
                                        ("muted", th["muted"]), ("label", th["label"]))):
        x = pad + i * col_w
        d.rectangle([x, y, x + 44, y + 44], fill=colour, outline=th["ink"], width=2)
        d.text((x + 56, y + 2), name, fill=th["ink"], font=f_text)
        d.text((x + 56, y + 24), colour, fill=th["muted"], font=f_small)
    y += 70
    # contrast table
    cols = (pad, pad + 150, pad + 400, pad + 650)
    for c, label in zip(cols, ("role", "text on fill (7 target, 4.5 min)", "stroke on fill (3 min)",
                               "stroke on canvas (3 min)")):
        d.text((c, y), label, fill=th["ink"], font=f_small)
    y += 28
    for t in table_rows:
        d.text((cols[0], y), t["role"], fill=th["ink"], font=f_small)
        for c, key, need in ((cols[1], "text_on_fill", 4.5), (cols[2], "stroke_on_fill", 3.0),
                             (cols[3], "stroke_on_canvas", 3.0)):
            mark = "ok" if t[key] >= need else "LOW"
            d.text((c, y), "%.2f:1  %s" % (t[key], mark), fill=th["ink"], font=f_small)
        y += 28
    chk = th.get("checks", {})
    worst = min((chk.get("strokes", {}).get(s, {}).get("de_ok", 0) for s in ("none", "protan", "deutan", "tritan")),
                default=0)
    d.text((pad, y + 6), "worst stroke pair, OKLab distance over normal, protan, deutan, tritan: %.3f"
           "   greyscale lightness gap: %.3f" % (worst, chk.get("strokes", {}).get("grey", {}).get("dl", 0)),
           fill=th["muted"], font=f_small)
    img.save(str(out_path), "PNG", optimize=True)
    return table_rows


# --------------------------------------------------------------------- palettes cmd

def default_out(cfg, data_dir=None):
    st = store.Store(cfg, data_dir=data_dir)
    return st.cache_dir / "setup"


def cmd_palettes(a):
    need_pillow()
    pal = _catalog.palettes_module()
    if pal is None:
        print("error: ltcore.palettes is missing from this install", file=sys.stderr)
        return 1
    cfg = config.load(a.config, validate=False)
    out = Path(a.out) if a.out else default_out(cfg, a.data_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids = [x for x in a.ids.split(",") if x] if a.ids else None
    try:
        rows = _catalog.palette_choices(
            cfg, top=a.top, also=() if a.no_also else _catalog.ORIGINAL_PALETTES, ids=ids, mode=a.mode)
    except (ValueError, RuntimeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    summary = []
    for r in rows:
        item = {"id": r["id"], "name": r["name"], "mode": r["mode"], "rank": r["rank"],
                "rank_of": r["rank_of"], "rank_label": rank_label(r),
                "default_eligible": r["default_eligible"], "licence": r["licence"],
                "origin": r["origin"], "png": None, "error": r["error"]}
        if r["theme"] is not None:
            png = out / ("palette-%s.png" % r["id"])
            item["contrast"] = palette_png(r, cfg, png, pal)
            item["png"] = str(png)
            chk = r["theme"]["checks"]
            item["text_min"] = chk["text_min"]
            item["nontext_min"] = chk["nontext_min"]
            item["worst_stroke_de_ok"] = min(chk["strokes"][s]["de_ok"] for s in ("none", "protan", "deutan", "tritan"))
            item["grey_dl"] = chk["strokes"]["grey"]["dl"]
        summary.append(item)
    (out / "palettes.json").write_text(json.dumps({"palettes": summary}, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    table = render_palette_table(summary)
    (out / "palettes.md").write_text(table, encoding="utf-8")
    if a.json:
        print(json.dumps({"out": str(out), "palettes": summary}, indent=1, ensure_ascii=False))
    else:
        print(table, end="")
        print("images in %s" % out)
    return 0


def render_palette_table(summary):
    lines = ["| rank | id | text min | non-text min | worst CVD stroke gap | grey gap | licence | image |",
             "|---|---|---|---|---|---|---|---|"]
    for s in summary:
        if s.get("error"):
            lines.append("| %s | %s | cannot derive: %s | | | | %s | |" % (
                s["rank_label"], s["id"], s["error"].replace("|", "/"), s["licence"]))
            continue
        lines.append("| %s | %s | %.1f:1 | %.1f:1 | %.3f | %.3f | %s | %s |" % (
            s["rank_label"], s["id"], s["text_min"], s["nontext_min"], s["worst_stroke_de_ok"],
            s["grey_dl"], s["licence"] or "-", Path(s["png"]).name))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------ fonts cmd

def font_png(row, cfg, out_path):
    Image, ImageDraw, ImageFont = need_pillow()
    pad = 24
    sizes = (44, 28, 18)
    fonts = []
    for s in sizes:
        fonts.append(ImageFont.truetype(str(row["path"]), s))
    f_cap, _src = load_font(15, cfg)
    img = Image.new("RGB", (1100, 28 + sum(int(s * 1.7) for s in sizes) + 60), "#ffffff")
    d = ImageDraw.Draw(img)
    y = pad
    d.text((pad, y), "%s  |  %s  |  %s  |  %s" % (row["family"], row["role"], row["licence"], row["state"]),
           fill="#333333", font=f_cap)
    y += 30
    for f, s in zip(fonts, sizes):
        d.text((pad, y), _catalog.SAMPLE_TEXT, fill="#000000", font=f)
        y += int(s * 1.7)
    d.text((pad, img.height - 22), "Samples use this font alone; boxes indicate missing glyphs.",
           fill="#333333", font=f_cap)
    img.save(str(out_path), "PNG", optimize=True)


def cmd_fonts(a):
    need_pillow()
    cfg = config.load(a.config, validate=False)
    st = store.Store(cfg, data_dir=a.data_dir)
    out = Path(a.out) if a.out else st.cache_dir / "setup"
    out.mkdir(parents=True, exist_ok=True)
    ids = [x for x in a.ids.split(",") if x] if a.ids else None
    try:
        rows = _catalog.font_choices(cfg, st.data_dir, top=a.top, ids=ids)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    result = []
    for r in rows:
        item = {"id": r["id"], "family": r["family"], "role": r["role"], "licence": r["licence"],
                "state": r["state"], "file": str(r["path"]) if r["path"] else None, "png": None,
                "note": r["note"]}
        if r["path"]:
            png = out / ("font-%s.png" % r["id"])
            try:
                font_png(r, cfg, png)
                item["png"] = str(png)
            except OSError as exc:
                item["note"] = "sample not drawn: %s" % exc
        result.append(item)
    (out / "fonts.json").write_text(json.dumps({"fonts": result}, indent=1, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    if a.json:
        print(json.dumps({"out": str(out), "fonts": result}, indent=1, ensure_ascii=False))
        return 0
    print("%-26s %-5s %-14s %-12s %s" % ("id", "role", "licence", "state", "sample"))
    for it in result:
        print("%-26s %-5s %-14s %-12s %s" % (it["id"], it["role"], it["licence"][:14], it["state"],
                                             Path(it["png"]).name if it["png"] else (it["note"] or "-")))
    print("states: bundled = ships with lt-suite; installed = found on this machine; downloaded = "
          "fetched by setup; downloadable = 'setup.py fetch-font <id> --yes' fetches it with a "
          "pinned sha256.")
    print("images in %s" % out)
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="config file (else LT_SUITE_CONFIG, ./lt-suite.toml, "
                        "the platform file)")
    common.add_argument("--data-dir", help="data folder")
    common.add_argument("--out", help="output folder (default: <cache>/setup)")
    common.add_argument("--top", type=int, default=4, help="how many ranked entries to show (per "
                        "light and dark group for palettes, per role for fonts)")
    common.add_argument("--ids", help="comma-separated ids to show instead of the top")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    ap = argparse.ArgumentParser(prog="preview.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("palettes", parents=[common], help="draw palette previews")
    p.add_argument("--mode", choices=("light", "dark"), help="show only light or dark palettes")
    p.add_argument("--no-also", action="store_true", help="omit the three original options")
    p = sub.add_parser("fonts", parents=[common], help="list fonts and draw samples")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return {"palettes": cmd_palettes, "fonts": cmd_fonts}[a.cmd](a)
    except config.ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
