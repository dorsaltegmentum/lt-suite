# Generated from shared/ltcore/raster.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SVG to PNG and PDF with rsvg-convert, in the fonts lt-suite resolves.

rsvg-convert finds fonts through fontconfig, so a font that lt-suite carries
(bundled in shared/fonts/ or fetched into <data>/fonts/) but the system does not
have would silently fall back to another face. ``prepare()`` reads the
font-family and font-weight values of an SVG, resolves each family with
ltcore.fonts, and writes a private fonts.conf that adds those folders.

cairo's PDF surface can embed a variable font's default instance for a run that
goes through its CID subset (curly quotes, "µ", "°"), so bold text can come out
regular. For a variable family, ``prepare()`` therefore cuts static instances at
the weights the SVG uses (ltcore.fonts.static_instance, renamed <Family>Static)
and rewrites the family name in a temporary copy of the SVG. The copy is a hidden
``.lt-raster-*.svg`` file beside the source, deleted after the conversion, so
relative links (images, stylesheets) resolve as they do from the source. The
source SVG is never changed. When its folder is read-only, the source renders
with its own family names and a warning says so. Without fontTools the variable
file is used as it is and a warning says so.

    python3 -m ltcore.raster IN.svg OUT.png|OUT.pdf [--width PX] [--zoom Z] [--config F]

Exit codes: 0 written, 1 rsvg-convert failed or is missing, 2 usage.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import ExitStack  # noqa: E402
import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Callable  # noqa: E402

from ltcore import fonts  # noqa: E402

NEEDS: list[str] = []

FORMATS = ("png", "pdf", "svg")
_GENERIC = {"sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "inherit"}
_FAMILY_ATTR = re.compile(r'font-family\s*=\s*"([^"]*)"|font-family\s*=\s*\'([^\']*)\'')
_FAMILY_CSS = re.compile(r"font-family\s*:\s*([^;\"}]+)")
_WEIGHT = re.compile(r'font-weight\s*[=:]\s*["\']?\s*(bold|normal|\d{3})')


class RasterError(Exception):
    """rsvg-convert is missing or failed. The message carries its stderr."""


def available() -> bool:
    return shutil.which("rsvg-convert") is not None


@dataclass
class Prepared:
    """An SVG text ready for rsvg-convert, the environment to run it in, and notes."""
    svg: str
    env: dict = field(default_factory=dict)
    families: dict = field(default_factory=dict)     # family in the SVG -> family written
    warnings: list = field(default_factory=list)


def _families(svg: str) -> list[str]:
    found: list[str] = []
    for m in _FAMILY_ATTR.finditer(svg):
        found.append(m.group(1) if m.group(1) is not None else m.group(2))
    found += [m.group(1) for m in _FAMILY_CSS.finditer(svg)]
    out: list[str] = []
    for value in found:
        first = value.strip()
        if first and first.lower() not in _GENERIC and first not in out:
            out.append(first)
    return out


def _weights(svg: str) -> list[int]:
    ws = {400}
    for m in _WEIGHT.finditer(svg):
        v = m.group(1)
        ws.add(700 if v == "bold" else 400 if v == "normal" else int(v))
    return sorted(ws)


def _role_for(family: str) -> str:
    e = fonts.find_entry(family)
    if e is not None and e.get("role") in fonts.ROLES:
        return e["role"]
    return "mono" if "mono" in family.lower() else "sans"


def prepare(svg_text: str, cfg: Any = None, *, cache_dir: str | os.PathLike | None = None,
            static: bool = True, use_system: bool = True) -> Prepared:
    """Resolve every font family the SVG names; return the SVG text to render (static
    family names substituted) and an environment whose FONTCONFIG_FILE adds the
    resolved font folders."""
    if cache_dir is None:
        from ltcore import store
        cache_dir = store.Store(cfg).font_cache_dir
    cdir = Path(cache_dir)
    out = Prepared(svg=svg_text, env=dict(os.environ))
    weights = _weights(svg_text)
    italic_used = "italic" in svg_text
    dirs: set[str] = set()
    for fam in _families(svg_text):
        stack = fam
        names = [v.strip().strip("'\"").strip() for v in stack.split(",")]
        role = _role_for(names[0])
        base = None
        for name in names:
            if name.lower() in _GENERIC:
                continue
            try:
                base = fonts.find(role, cfg, family=name, use_system=use_system, fallback=False)
                fam = name
                break
            except fonts.FontError:
                continue
        if base is None:
            base = fonts.find(role, cfg, family=fonts.default_family(role), use_system=use_system)
            fam = base.family
            out.warnings.append(f"font {stack!r} not found; drawn in {fam}")
        def replace_stack(match):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            return 'font-family="' + fam + '"' if value.strip() == stack else match.group(0)
        out.svg = _FAMILY_ATTR.sub(replace_stack, out.svg)
        out.svg = _FAMILY_CSS.sub(lambda m: "font-family:" + fam if m.group(1).strip() == stack else m.group(0), out.svg)
        if base.source in ("fc-match", "platform font folder") and not base.variable:
            continue                       # installed static fonts need nothing
        # Resolve the entire required face set before modifying any family member.
        # Renaming only the regular face would also hide an unmodifiable italic.
        required = []
        try:
            for w in weights:
                for it in ((False, True) if italic_used else (False,)):
                    face = fonts.find(role, cfg, family=fam, weight=w, italic=it,
                                      use_system=use_system, fallback=False)
                    if it and not face.italic:
                        continue           # cairo slants the upright face when no italic exists
                    required.append(face)
        except fonts.FontError as exc:
            out.warnings.append(f"{fam}: {exc}; original family used as it is")
            dirs.add(str(base.path.parent))
            continue
        may_modify = all(fonts.licence_class(face) == "modify" for face in required)
        if not base.variable or not static or not may_modify:
            dirs.update(str(face.path.parent) for face in required)
            if any(face.variable for face in required):
                reason = "font licence prevents modification; " if not may_modify else ""
                out.warnings.append(f"{fam}: {reason}variable font used as it is; PDF text may lose its weight")
            continue
        try:
            new_family = fonts.static_family_name(base.family)
            for face in required:
                p = fonts.static_instance(face.path, weight=face.weight, italic=face.italic,
                                          family=base.family, cache_dir=cdir)
                dirs.add(str(p.parent))
        except fonts.FontError as exc:
            out.warnings.append(f"{fam}: {exc}; variable font used as it is")
            dirs.update(str(face.path.parent) for face in required)
            continue
        out.families[fam] = new_family
        pat = re.compile(r"(font-family\s*[=:]\s*[\"']?\s*)(['\"]?)" + re.escape(fam) + r"\2(?=\s*[,\"';}]|\s*$)")
        out.svg = pat.sub(lambda m: m.group(1) + m.group(2) + new_family + m.group(2), out.svg)
    if dirs:
        out.env["FONTCONFIG_FILE"] = str(fonts.fontconfig_file(sorted(dirs), cdir))
    return out


def convert(svg: str | os.PathLike, out: str | os.PathLike, *, fmt: str | None = None,
            width: int | None = None, height: int | None = None, zoom: float | None = None,
            dpi: float | None = None, background: str | None = None, cfg: Any = None,
            cache_dir: str | os.PathLike | None = None, prepare_fonts: bool = True,
            timeout: int = 180, runner: Callable = subprocess.run) -> tuple[Path, list[str]]:
    """Render ``svg`` to ``out`` (format from ``fmt`` or the suffix). Returns (path,
    warnings). Raises RasterError when rsvg-convert is missing or fails."""
    out = Path(out)
    fmt = (fmt or out.suffix.lstrip(".")).lower()
    if fmt not in FORMATS:
        raise RasterError(f"unknown output format {fmt!r}; use one of {', '.join(FORMATS)}")
    exe = shutil.which("rsvg-convert")
    if exe is None:
        raise RasterError("rsvg-convert not found; install librsvg (Debian/Ubuntu: librsvg2-bin, "
                          "Fedora: librsvg2-tools, Homebrew: librsvg)")
    src = Path(svg).resolve()
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise RasterError(f"{src}: cannot read: {exc.strerror or exc}") from None
    prep = prepare(text, cfg, cache_dir=cache_dir) if prepare_fonts else Prepared(text, dict(os.environ))
    argv = [exe, "-f", fmt]
    if width:
        argv += ["-w", str(int(width))]
    if height:
        argv += ["-h", str(int(height))]
    if zoom:
        argv += ["-z", f"{zoom:g}"]
    if dpi:
        argv += ["-d", f"{dpi:g}", "-p", f"{dpi:g}"]
    if background:
        argv += ["-b", background]
    out.parent.mkdir(parents=True, exist_ok=True)
    warnings = list(prep.warnings)
    with ExitStack() as stack:
        td = stack.enter_context(tempfile.TemporaryDirectory(prefix="lt-raster-"))
        tmp_svg = src
        if prep.svg != text:
            # librsvg resolves relative hrefs (linked images, stylesheets) against the
            # SVG file's own location and loads only files in that folder tree. The
            # prepared copy is written beside the source under a hidden name, so its
            # base is the source's base and every linked file still loads.
            try:
                fd, name = tempfile.mkstemp(dir=src.parent, prefix=".lt-raster-", suffix=".svg")
            except OSError as exc:
                warnings.append(f"{src.parent}: cannot write the font-prepared copy beside the "
                                f"source ({exc.strerror or exc}); rendered the source with the "
                                f"original family names")
            else:
                tmp_svg = Path(name)
                stack.callback(tmp_svg.unlink, missing_ok=True)
                with os.fdopen(fd, "w", encoding="utf-8") as prepared:
                    prepared.write(prep.svg)
        tmp_out = Path(td) / f"out.{fmt}"
        try:
            r = runner(argv + ["-o", str(tmp_out), str(tmp_svg)], env=prep.env, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout, cwd=str(src.parent.resolve()))
        except subprocess.TimeoutExpired:
            raise RasterError(f"rsvg-convert took longer than {timeout} s on {src}") from None
        if r.returncode != 0 or not tmp_out.is_file():
            raise RasterError(f"rsvg-convert failed on {src} (exit {r.returncode}): {r.stderr.strip()}")
        shutil.move(str(tmp_out), str(out))
    return out, warnings


def svg_to_pdf(svg: str | os.PathLike, out: str | os.PathLike, *, cfg: Any = None, **kw: Any) -> Path:
    """One-page vector PDF of ``svg``; see ``convert``."""
    return convert(svg, out, fmt="pdf", cfg=cfg, **kw)[0]


def svg_to_png(svg: str | os.PathLike, out: str | os.PathLike, *, width_px: int | None = None,
               cfg: Any = None, **kw: Any) -> Path:
    """PNG of ``svg``, ``width_px`` wide when given; see ``convert``."""
    return convert(svg, out, fmt="png", width=width_px, cfg=cfg, **kw)[0]


def render(svg: str | os.PathLike, out_dir: str | os.PathLike | None = None, stem: str | None = None,
           *, formats: tuple[str, ...] = ("png", "pdf"), png_width: int | None = None,
           png_zoom: float | None = None, cfg: Any = None, cache_dir: str | os.PathLike | None = None,
           **kw: Any) -> dict[str, Path]:
    """Render one SVG to several formats beside it (or in ``out_dir``)."""
    src = Path(svg)
    od = Path(out_dir) if out_dir is not None else src.parent
    stem = stem or src.stem
    res: dict[str, Path] = {}
    for fmt in formats:
        extra = {"width": png_width, "zoom": png_zoom} if fmt == "png" else {}
        res[fmt], _ = convert(src, od / f"{stem}.{fmt}", fmt=fmt, cfg=cfg, cache_dir=cache_dir,
                              **extra, **kw)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.raster", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("svg")
    ap.add_argument("out")
    ap.add_argument("--width", type=int)
    ap.add_argument("--zoom", type=float)
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    from ltcore import config
    try:
        cfg = config.load(a.config)
        out, warnings = convert(a.svg, a.out, width=a.width, zoom=a.zoom, cfg=cfg)
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RasterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
