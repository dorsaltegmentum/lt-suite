#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Overlay a labelled coordinate grid on an image.

    python3 gridref.py input.jpg grid.jpg --step 200

Open the output in your image viewer and read coordinates from the grid.
Check anchors with annotate_micrograph.py --probe before rendering.
Anchors read from a downsampled view are the largest source of wrong labels.

Grid coordinates use source pixels, matching --point and --bracket arguments.
"""

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402
ltcore.bootstrap(["PIL", "numpy", "scipy"])   # re-runs inside <data>/venv when one exists and a package is missing
from ltcore import config  # noqa: E402

import argparse


def resolve_font(cfg, size, weight):
    """Resolve a configured font, fonts.sans, or Pillow's default."""
    font = None
    source_label = "pillow-default"
    source_path = "builtin"
    reasons = []
    configured = cfg.get("annotate.font_path")

    if configured:
        try:
            path = Path(configured).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"{path} does not exist")
            font = ImageFont.truetype(str(path), size)
            source_label = "config annotate.font_path"
            source_path = str(path)
        except Exception as exc:
            reasons.append(f"annotate.font_path: {exc}")
    else:
        reasons.append("annotate.font_path is empty")

    if font is None:
        try:
            from ltcore import fonts

            resolved = fonts.resolve("sans", weight=weight, cfg=cfg)
            path = getattr(resolved, "path", resolved)
            font = ImageFont.truetype(str(path), size)
            source_label = "fonts.sans"
            source_path = str(path)
        except Exception as exc:
            reasons.append(f"fonts.sans: {exc}")

    if font is None:
        font = ImageFont.load_default()
        print(
            "WARNING: using pillow-default; " + "; ".join(reasons),
            file=sys.stderr,
        )

    # A variable font draws at its default weight unless its wght axis is set.
    try:
        axes = font.get_variation_axes()
        values = []
        for axis in axes:
            name = axis.get("name")
            name = name.decode() if isinstance(name, bytes) else str(name)
            lo, hi, default = axis.get("minimum"), axis.get("maximum"), axis.get("default")
            values.append(min(max(weight, lo), hi) if name.lower() == "weight" else default)
        if values:
            font.set_variation_by_axes(values)
    except Exception:
        pass  # a static font, or Pillow's bitmap default

    print(f"font: {source_label} {source_path}", file=sys.stderr)
    return font, source_label


def main() -> int:
    global Image, ImageDraw, ImageFont

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--config", metavar="PATH")
    ap.add_argument("--help-rules", action="store_true",
                    help="print configured label naming rules and exit")
    ap.add_argument("--step", type=int, default=None,
                    help="grid spacing in source pixels (default: ~12 columns)")
    ap.add_argument("--opacity", type=int, default=110, help="grid line alpha 0-255")
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

    if not args.src or not args.dst:
        ap.error("src and dst are required unless --help-rules is given")

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print(ltcore.missing_hint(["PIL"]), file=sys.stderr)
        return 3

    a = args
    im = Image.open(a.src).convert("RGB")
    W, H = im.size
    step = a.step or max(50, round(W / 12 / 50) * 50)

    ps = max(15, round(W / 75))
    font, _ = resolve_font(cfg, ps, 600)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for x in range(0, W + 1, step):
        major = (x // step) % 5 == 0
        d.line([x, 0, x, H], fill=(0, 255, 255, a.opacity + (70 if major else 0)),
               width=3 if major else 1)
    for y in range(0, H + 1, step):
        major = (y // step) % 5 == 0
        d.line([0, y, W, y], fill=(0, 255, 255, a.opacity + (70 if major else 0)),
               width=3 if major else 1)

    out = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    dt = ImageDraw.Draw(out)
    for x in range(0, W + 1, step):
        for y in range(0, H + 1, step):
            if (x // step) % 2 or (y // step) % 2:
                continue
            dt.text((x + 4, y + 2), f"{x},{y}", font=font, fill="#00FFFF",
                    anchor="lt", stroke_width=max(2, ps // 7), stroke_fill="#000000")
    out.save(a.dst, quality=90)
    print(f"{a.dst} {W}x{H} grid step {step}px; labels every {step * 2}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
