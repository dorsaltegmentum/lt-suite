# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find font files, read their metrics, cut static instances and fetch catalog fonts.

Roles: "sans" (fonts.sans) and "mono" (fonts.mono). A role resolves to a file in
this order, first hit wins:
  1. an explicit file: fonts.sans_path, fonts.mono_path (fonts.mono_bold_path for
     mono at weight 600 or more), or a fonts.sans / fonts.mono value that is a path
  2. bundled: shared/fonts/<id>/ (the default sans and mono ship with lt-suite)
  3. fetched: <data>/fonts/<id>/ (setup's fetch-font puts catalog fonts here)
  4. installed: fc-match when fontconfig exists, accepted only when it returns the
     requested family, else a scan of the platform font folders
  5. fallback: the bundled default of the role, marked ``fallback=True``
Bundled and fetched copies come first so that renders are the same on every
machine; an installed font is used only for families lt-suite does not carry.

Variable fonts are returned as they are by default: ``pillow_font()`` sets the wght
axis per call, and the diagram engine cuts its own instances. Renderers that cannot
select a variable instance (cairo's PDF surface, ReportLab, a plain
``ImageFont.truetype``) ask for a static instance: ``face_file()`` or
``resolve(..., static=True)`` cuts it with fontTools into <cache>/font-cache/<sha8>/
under a renamed family (<Family>Static, "Noto Sans" -> "NotoSansStatic"), so the
copy never shadows the original family. Fonts with a Reserved Font Name are never
cut. Nothing cut is ever committed.

Italic: a request for an italic face that no file provides returns the upright
file with ``Font.italic`` False (the file's real state) and ``source`` naming the
missing face; renderers then slant the upright outlines themselves.
``static_instance()`` refuses to label an upright file italic.

``metrics()`` reads unitsPerEm and the OS/2 typo metrics with the standard
library, so text layout needs no third-party package.

    python3 -m ltcore.fonts list
    python3 -m ltcore.fonts resolve [sans|mono] [--weight 700] [--static] [--config F]
    python3 -m ltcore.fonts fetch <id> [--config F] [--yes]

API: ``resolve(role, weight=400, italic=False, cfg=, static=False)`` -> FontPath (a
pathlib.Path that also has .font, .family, .source, .fallback); ``find(role, cfg,
weight=)`` -> Font; ``family(role, cfg)``; ``face_file(role, cfg, weight=)`` ->
(static file, Font); ``static_instance(src, weight=, cache_dir=)``;
``pillow_font(role, size, cfg, weight=)``; ``metrics(path, size, line)``;
``fetch(id, cfg)``; ``state(entry, cfg)``; ``catalog()``.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import struct  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import urllib.request  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Callable, Iterable, Mapping  # noqa: E402

NEEDS = ["fonts/"]

SHARED = Path(__file__).resolve().parent.parent
FONTS_DIR = SHARED / "fonts"
CATALOG_FILE = FONTS_DIR / "catalog.json"
ROLES = ("sans", "mono")
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")
FC_WEIGHT = {100: "thin", 200: "extralight", 300: "light", 400: "regular", 500: "medium",
             600: "semibold", 700: "bold", 800: "extrabold", 900: "black"}


class FontError(Exception):
    """A font that cannot be found, read, cut or fetched. The message says which and why."""


# =========================================================================== sfnt reading
@dataclass
class FontInfo:
    """What ``read_info`` learns from a font file's tables."""
    path: Path
    family: str
    subfamily: str
    weight: int
    italic: bool
    units_per_em: int
    ascender: int          # OS/2 sTypoAscender (font units)
    descender: int         # OS/2 sTypoDescender (negative)
    line_gap: int
    x_height: int
    cap_height: int
    axes: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # tag: (min, default, max)

    @property
    def variable(self) -> bool:
        return bool(self.axes)


def _tables(data: bytes, index: int = 0) -> dict[bytes, tuple[int, int]]:
    off = 0
    if data[:4] == b"ttcf":
        n = struct.unpack(">I", data[8:12])[0]
        if index >= n:
            raise FontError(f"font collection has {n} fonts; no index {index}")
        off = struct.unpack(">I", data[12 + 4 * index:16 + 4 * index])[0]
    tag = data[off:off + 4]
    if tag not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise FontError("not an OpenType or TrueType font")
    num = struct.unpack(">H", data[off + 4:off + 6])[0]
    out = {}
    for i in range(num):
        rec = off + 12 + 16 * i
        t, _, o, ln = struct.unpack(">4sIII", data[rec:rec + 16])
        out[t] = (o, ln)
    return out


def _names(data: bytes, tab: tuple[int, int]) -> dict[int, str]:
    o, _ = tab
    _, count, soff = struct.unpack(">HHH", data[o:o + 6])
    best: dict[int, tuple[int, str]] = {}
    for i in range(count):
        pid, eid, lid, nid, ln, so = struct.unpack(">6H", data[o + 6 + 12 * i:o + 18 + 12 * i])
        raw = data[o + soff + so:o + soff + so + ln]
        if pid == 3 or pid == 0:
            text, rank = raw.decode("utf-16-be", "replace"), (0 if lid in (0x409, 0) else 1)
        elif pid == 1 and eid == 0:
            text, rank = raw.decode("latin-1"), 2
        else:
            continue
        if nid not in best or rank < best[nid][0]:
            best[nid] = (rank, text)
    return {k: v[1] for k, v in best.items()}


def read_info(path: str | os.PathLike, index: int = 0) -> FontInfo:
    """Family, weight, italic, typo metrics and variation axes of a font file
    (standard library only)."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise FontError(f"{p}: cannot read: {exc.strerror or exc}") from None
    try:
        t = _tables(data, index)
        head = t[b"head"][0]
        upm = struct.unpack(">H", data[head + 18:head + 20])[0]
        mac_style = struct.unpack(">H", data[head + 44:head + 46])[0]
        names = _names(data, t[b"name"]) if b"name" in t else {}
        weight, italic, asc, desc, gap, xh, ch = 400, bool(mac_style & 2), 0, 0, 0, 0, 0
        if b"OS/2" in t:
            o = t[b"OS/2"][0]
            ver = struct.unpack(">H", data[o:o + 2])[0]
            weight = struct.unpack(">H", data[o + 4:o + 6])[0]
            fs_sel = struct.unpack(">H", data[o + 62:o + 64])[0]
            italic = italic or bool(fs_sel & 1)
            asc, desc, gap = struct.unpack(">hhh", data[o + 68:o + 74])
            if ver >= 2:
                xh, ch = struct.unpack(">hh", data[o + 86:o + 90])
        elif b"hhea" in t:
            o = t[b"hhea"][0]
            asc, desc, gap = struct.unpack(">hhh", data[o + 4:o + 10])
        axes = {}
        if b"fvar" in t:
            o = t[b"fvar"][0]
            aoff, _, count, size = struct.unpack(">HHHH", data[o + 4:o + 12])
            for i in range(count):
                a = o + aoff + i * size
                tag = data[a:a + 4].decode("latin-1")
                mn, df, mx = (v / 65536 for v in struct.unpack(">iii", data[a + 4:a + 16]))
                axes[tag] = (mn, df, mx)
    except (struct.error, KeyError, IndexError) as exc:
        raise FontError(f"{p}: unreadable font tables ({exc})") from None
    family = names.get(16) or names.get(1) or p.stem
    sub = names.get(17) or names.get(2) or ""
    return FontInfo(p, family, sub, weight, italic, upm, asc, desc, gap, xh, ch, axes)


def metrics(path: str | os.PathLike, size_px: float = 1.0, line_px: float | None = None) -> dict:
    """Typo metrics scaled to ``size_px``, and the baseline offset that centres one line
    of that size in a line box of ``line_px`` (default 1.2 x size):
    {"ascender", "descender", "line_gap", "x_height", "cap_height", "baseline_offset", "line_px"}."""
    fi = read_info(path)
    s = size_px / fi.units_per_em
    line = 1.2 * size_px if line_px is None else line_px
    asc, desc = fi.ascender * s, -fi.descender * s
    return {"ascender": asc, "descender": desc, "line_gap": fi.line_gap * s,
            "x_height": fi.x_height * s, "cap_height": fi.cap_height * s,
            "baseline_offset": (line - (asc + desc)) / 2 + asc, "line_px": line}


# =========================================================================== catalog
def catalog() -> dict[str, dict]:
    """Font catalog entries by id (shared/fonts/catalog.json)."""
    with open(CATALOG_FILE, encoding="utf-8") as fh:
        return {f["id"]: f for f in json.load(fh)["fonts"]}


def catalog_meta() -> dict:
    with open(CATALOG_FILE, encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if k != "fonts"}


def default_family(role: str) -> str:
    """The bundled default family of a role ("Noto Sans", "Noto Sans Mono")."""
    return catalog()[catalog_meta()["defaults"][role]]["family"]


def find_entry(name: str) -> dict | None:
    """Catalog entry by id or family name (case and spaces ignored)."""
    key = name.strip().lower().replace(" ", "").replace("-", "")
    for e in catalog().values():
        if key in (e["id"].replace("-", ""), e["family"].lower().replace(" ", "")):
            return e
    return None


def bundled_files(entry: Mapping) -> list[Path]:
    return [FONTS_DIR / f["file"] for f in (entry.get("bundled") or {}).get("files", [])]


def _needs_probe() -> None:
    meta = catalog_meta()
    cat = catalog()
    for role in ROLES:
        for p in bundled_files(cat[meta["defaults"][role]]):
            read_info(p)
        lic = (cat[meta["defaults"][role]].get("bundled") or {}).get("licence_file")
        if lic:
            (FONTS_DIR / lic).read_bytes()


# =========================================================================== resolve
@dataclass
class Font:
    """A resolved font file for one role and weight."""
    role: str
    family: str
    path: Path
    source: str                   # where it came from, for messages and doctor
    weight: int = 400
    italic: bool = False
    variable: bool = False
    axes: dict = field(default_factory=dict)
    font_id: str | None = None
    fallback: bool = False        # True when the requested family was not found
    requested: str = ""


def _cfg_get(cfg: Any, key: str, default: Any = "") -> Any:
    if cfg is None:
        return default
    try:
        return cfg.get(key, default)
    except TypeError:
        return default


def _looks_like_path(v: str) -> bool:
    return any(c in v for c in "/\\") or v.lower().endswith(FONT_SUFFIXES)


def _pick(files: Iterable[Path], weight: int, italic: bool) -> tuple[Path, FontInfo] | None:
    """The file that best serves ``weight`` and ``italic``: a variable font whose wght
    axis covers the weight, else the static face with the nearest weight."""
    best, best_key = None, None
    for p in files:
        try:
            fi = read_info(p)
        except FontError:
            continue
        if fi.italic != italic:
            continue
        w = fi.axes.get("wght")
        dist = 0 if (w and w[0] <= weight <= w[2]) else abs(fi.weight - weight)
        key = (dist, 0 if fi.variable else 1, str(p))
        if best_key is None or key < best_key:
            best, best_key = (p, fi), key
    return best


def _data_fonts_dir(cfg: Any, data_dir: str | os.PathLike | None) -> Path | None:
    if data_dir is not None:
        return Path(data_dir) / "fonts"
    try:
        from ltcore import store
        return store.Store(cfg).fonts_dir
    except Exception:  # an unusable config only costs this source
        return None


def fc_match(family: str, weight: int = 400, italic: bool = False,
             runner: Callable = subprocess.run) -> Path | None:
    """The file fontconfig picks for ``family``, or None when fontconfig is missing or
    substitutes another family."""
    exe = shutil.which("fc-match")
    if exe is None:
        return None
    pattern = f"{family}:weight={FC_WEIGHT.get(round(weight / 100) * 100, 'regular')}"
    if italic:
        pattern += ":slant=italic"
    try:
        r = runner([exe, "-f", "%{file}\n%{family}", pattern], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    lines = r.stdout.split("\n", 1)
    got = [x.strip().lower() for x in (lines[1] if len(lines) > 1 else "").split(",")]
    if family.strip().lower() not in got:
        return None
    p = Path(lines[0].strip())
    return p if p.is_file() else None


def platform_font_dirs(platform: str | None = None, environ: Mapping[str, str] | None = None) -> list[Path]:
    env = os.environ if environ is None else environ
    plat = platform or sys.platform
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if plat.startswith("win"):
        out = [Path(env.get("WINDIR", "C:\\Windows")) / "Fonts"]
        if env.get("LOCALAPPDATA"):
            out.append(Path(env["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
        return out
    if plat == "darwin":
        return [home / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental")]
    xdg = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return [Path(xdg) / "fonts", home / ".fonts", Path("/usr/local/share/fonts"), Path("/usr/share/fonts")]


def scan_family(family: str, dirs: Iterable[Path] | None = None) -> list[Path]:
    """Font files in ``dirs`` whose family name is ``family``. File names are filtered
    first (the family without spaces), then the name table decides."""
    key = family.lower().replace(" ", "")
    out = []
    for d in dirs if dirs is not None else platform_font_dirs():
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() not in FONT_SUFFIXES or key not in p.name.lower().replace(" ", ""):
                continue
            try:
                if read_info(p).family.lower() == family.lower():
                    out.append(p)
            except FontError:
                continue
    return sorted(out)


def _face_note(source: str, wanted_italic: bool, got_italic: bool) -> str:
    """``source`` plus a note naming the face that is missing, when the file found is
    not the face that was asked for."""
    if wanted_italic == got_italic:
        return source
    face = "italic" if wanted_italic else "upright"
    return f"{source} ({face} face missing; the file's own face is used)"


def _explicit(role: str, cfg: Any, weight: int) -> tuple[str, str] | None:
    keys = [f"fonts.{role}_path"]
    if role == "mono" and weight >= 600:
        keys.insert(0, "fonts.mono_bold_path")
    for k in keys:
        v = _cfg_get(cfg, k, "")
        if v:
            return v, f"config {k}"
    v = _cfg_get(cfg, f"fonts.{role}", "")
    if v and _looks_like_path(v):
        base = getattr(cfg, "path", None)
        p = Path(os.path.expanduser(v))
        if not p.is_absolute() and base:
            p = Path(base).parent / p
        return str(p), f"config fonts.{role} (a file)"
    return None


def find(role: str = "sans", cfg: Any = None, *, weight: int = 400, italic: bool = False,
         family: str | None = None, data_dir: str | os.PathLike | None = None,
         use_system: bool = True, fallback: bool = True,
         runner: Callable = subprocess.run) -> Font:
    """Find the font file for a role (or an explicit ``family``), with where it came
    from. See the module docstring for the order. Raises FontError when nothing is
    found and ``fallback`` is False or the bundled default is missing too."""
    if role == "mono_bold":
        role, weight = "mono", max(weight, 700)
    if role not in ROLES:
        raise FontError(f"unknown font role {role!r}; use one of {', '.join(ROLES)}")
    tried: list[str] = []
    if family is None:
        ex = _explicit(role, cfg, weight)
        if ex is not None:
            p = Path(ex[0])
            if not p.is_file():
                raise FontError(f"{p} ({ex[1]}) does not exist")
            fi = read_info(p)
            missing = fi.italic != italic
            return Font(role, fi.family, p, _face_note(ex[1], italic, fi.italic), weight, fi.italic,
                        fi.variable, fi.axes, fallback=missing, requested=str(p))
        family = _cfg_get(cfg, f"fonts.{role}", "") or default_family(role)
    entry = find_entry(family)
    fam = entry["family"] if entry else family
    if entry:
        got = _pick(bundled_files(entry), weight, italic) or (
            _pick(bundled_files(entry), weight, False) if italic else None)
        if got:
            return Font(role, fam, got[0], _face_note("bundled", italic, got[1].italic), weight,
                        got[1].italic, got[1].variable, got[1].axes, entry["id"], requested=family)
        tried.append("bundled")
        ddir = _data_fonts_dir(cfg, data_dir)
        if ddir is not None:
            files = sorted(p for p in (ddir / entry["id"]).glob("*") if p.suffix.lower() in FONT_SUFFIXES)
            got = _pick(files, weight, italic) or (_pick(files, weight, False) if italic else None)
            if got:
                return Font(role, fam, got[0], _face_note(f"data folder {ddir / entry['id']}", italic,
                                                          got[1].italic), weight,
                            got[1].italic, got[1].variable, got[1].axes, entry["id"], requested=family)
            tried.append(f"data folder {ddir / entry['id']}")
    if use_system:
        p = fc_match(fam, weight, italic, runner=runner)
        if p is not None:
            fi = read_info(p)
            return Font(role, fam, p, "fc-match", weight, fi.italic, fi.variable, fi.axes,
                        entry["id"] if entry else None, requested=family)
        tried.append("fc-match")
        got = _pick(scan_family(fam), weight, italic)
        if got:
            return Font(role, fam, got[0], "platform font folder", weight, got[1].italic,
                        got[1].variable, got[1].axes, entry["id"] if entry else None, requested=family)
        tried.append("platform font folders")
    default = default_family(role)
    if fallback and fam != default:
        f = find(role, None, weight=weight, italic=italic, family=default, use_system=False,
                 fallback=False)
        f.fallback, f.requested = True, family
        f.source = f"bundled fallback ({family} not found: {', '.join(tried)})"
        return f
    raise FontError(f"font {family!r} for {role} not found (tried {', '.join(tried) or 'nothing'})")


class FontPath(type(Path())):
    """The file ``resolve()`` returns: a pathlib.Path that also carries the lookup.

    ``.font`` is the ``Font`` record; ``.family``, ``.source``, ``.fallback``,
    ``.requested``, ``.font_id`` and ``.variable`` read from it. ``.path`` is this file
    as a plain Path, so code written for a ``Font`` and code written for a Path both
    work. ``.source_file`` is the font file the lookup found; it differs from this
    file only when a static instance was cut."""

    @property
    def font(self) -> "Font | None":
        return self.__dict__.get("_font")

    @property
    def path(self) -> Path:
        return Path(self)

    @property
    def source_file(self) -> Path:
        f = self.font
        return f.path if f is not None else Path(self)

    def __getattr__(self, name: str) -> Any:
        if name in ("family", "source", "fallback", "requested", "font_id", "variable", "role",
                    "weight", "italic", "axes"):
            f = self.__dict__.get("_font")
            if f is not None:
                return getattr(f, name)
        raise AttributeError(name)


def _font_path(p: Path, font: "Font") -> FontPath:
    out = FontPath(str(p))
    out.__dict__["_font"] = font
    return out


def _resolve_args(args: tuple, cfg: Any, weight: Any, italic: Any) -> tuple[Any, int, bool]:
    """Sort positional arguments: an int is the weight, a bool the italic flag,
    anything else the config, so resolve(role, 700) and resolve(role, cfg) both work."""
    for a in args:
        if isinstance(a, bool):
            italic = a if italic is None else italic
        elif isinstance(a, int):
            weight = a if weight is None else weight
        elif cfg is None:
            cfg = a
        else:
            raise TypeError("resolve() takes one config; pass it once")
    return cfg, 400 if weight is None else int(weight), bool(italic)


def resolve(role: str = "sans", *args: Any, cfg: Any = None, weight: int | None = None,
            italic: bool | None = None, static: bool = False,
            cache_dir: str | os.PathLike | None = None, **kw: Any) -> FontPath:
    """The font file for ``role`` ("sans", "mono" or "mono_bold") at ``weight``:
    ``resolve(role, weight=400, italic=False, cfg=cfg)``; ``resolve(role, cfg,
    weight=...)`` works too. Returns a FontPath (a pathlib.Path with ``.font``,
    ``.family``, ``.source``, ``.fallback``). Raises FontError when nothing resolves.

    By default the file is the font itself; a variable font comes back variable,
    so a caller that instances it (the diagram engine) keeps the family name. With
    ``static=True`` a variable font comes back as a static TrueType instance cut at
    ``weight`` under the family ``<Family>Static``, which Pillow, ReportLab and
    cairo's PDF surface read at the right weight without axis settings. When cutting
    is impossible (no fontTools, unwritable cache, a Reserved Font Name) the
    variable file is returned. ``face_file()`` is the same with ``static=True``.
    Keyword arguments go to ``find()`` (family, data_dir, use_system, fallback)."""
    cfg, weight, italic = _resolve_args(args, cfg, weight, italic)
    font = find(role, cfg, weight=weight, italic=italic, **kw)
    if not (static and font.variable) or reserved_font_names(find_entry(font.family)):
        return _font_path(font.path, font)
    try:
        p = static_instance(font.path, weight=font.weight, italic=font.italic, family=font.family,
                            cache_dir=_font_cache_dir(cfg, cache_dir))
    except (FontError, OSError):
        p = font.path
    return _font_path(p, font)


def family(role: str = "sans", cfg: Any = None, **kw: Any) -> str:
    """The family name a role resolves to ("Noto Sans" by default). This is the name
    of the font file ``resolve()`` returns without ``static``; a static instance is
    named ``static_family_name(family)``."""
    return find(role, cfg, **kw).family


def installed_path(entry_or_id: Mapping | str, cfg: Any = None,
                   data_dir: str | os.PathLike | None = None) -> Path | None:
    """A file for a catalog font outside the bundle: <data>/fonts/<id>/, then the
    system (fc-match, platform folders). None when it is not present."""
    entry = entry_or_id if isinstance(entry_or_id, Mapping) else (
        catalog().get(entry_or_id) or find_entry(entry_or_id))
    if entry is None:
        return None
    ddir = _data_fonts_dir(cfg, data_dir)
    if ddir is not None:
        files = sorted(p for p in (ddir / entry["id"]).glob("*") if p.suffix.lower() in FONT_SUFFIXES)
        if files:
            return files[0]
    p = fc_match(entry["family"])
    if p is not None:
        return p
    found = scan_family(entry["family"])
    return found[0] if found else None


# =========================================================================== static instances
def reserved_font_names(entry: Mapping | None) -> list[str]:
    """The Reserved Font Names of a catalog entry; a font with any is never modified."""
    return list(((entry or {}).get("licence") or {}).get("reserved_font_names") or [])


def static_family_name(family: str) -> str:
    return family.replace(" ", "") + "Static"


def _style_name(weight: int, italic: bool) -> str:
    names = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
             600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
    base = names.get(round(weight / 100) * 100, f"W{weight}")
    if italic:
        return "Italic" if base == "Regular" else f"{base} Italic"
    return base


def _font_cache_dir(cfg: Any, cache_dir: str | os.PathLike | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    from ltcore import store
    return store.Store(cfg).font_cache_dir


def static_instance(src: str | os.PathLike, *, weight: int, italic: bool = False,
                    family: str | None = None, cache_dir: str | os.PathLike,
                    opsz: float | None = None, wdth: float = 100.0) -> Path:
    """A static TrueType instance of a variable font at ``weight`` (opsz defaults to the
    axis default, wdth to 100), named ``<Family>Static``. Cached by source hash and
    spec; needs fontTools."""
    src = Path(src)
    fi = read_info(src)
    if fi.italic != italic:
        face = "italic" if italic else "upright"
        raise FontError(f"{src}: {face} face missing; static instancing cannot change the outlines")
    if not fi.variable:
        return src
    fam = static_family_name(family or fi.family)
    style = _style_name(weight, italic)
    spec = json.dumps([hashlib.sha256(src.read_bytes()).hexdigest(), weight, italic, opsz, wdth, fam])
    out_dir = Path(cache_dir) / hashlib.sha256(spec.encode()).hexdigest()[:8]
    out = out_dir / f"{fam}-{style.replace(' ', '')}.ttf"
    if out.is_file():
        return out
    try:
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont
    except ImportError:
        raise FontError("cutting a static font instance needs fontTools "
                        "(run the setup skill's bootstrap step)") from None
    loc: dict[str, float] = {}
    for tag, (mn, df, mx) in fi.axes.items():
        want = {"wght": weight, "wdth": wdth, "opsz": opsz}.get(tag)
        loc[tag] = df if want is None else max(mn, min(mx, want))
    f = instantiateVariableFont(TTFont(str(src)), loc)
    name = f["name"]
    for nid in (16, 17, 21, 22, 25):
        name.removeNames(nameID=nid)
    ps = f"{fam}-{style.replace(' ', '')}"
    for nid, val in ((1, fam), (2, style), (4, f"{fam} {style}"), (6, ps)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    os2, head = f["OS/2"], f["head"]
    os2.usWeightClass = weight
    if weight >= 700:
        os2.fsSelection = (os2.fsSelection | 0x20) & ~0x40
        head.macStyle |= 1
    else:
        os2.fsSelection &= ~0x20
        head.macStyle &= ~1
    if italic:
        os2.fsSelection = (os2.fsSelection | 0x01) & ~0x40
        head.macStyle |= 2
    elif weight < 700:
        os2.fsSelection |= 0x40
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    os.close(fd)
    try:
        f.save(tmp)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return out


def face_file(role: str = "sans", cfg: Any = None, *, weight: int = 400, italic: bool = False,
              family: str | None = None, cache_dir: str | os.PathLike | None = None,
              **kw: Any) -> tuple[Path, Font]:
    """(static file, resolved Font) for renderers that cannot pick a variable instance
    (ReportLab, cairo's PDF surface). A variable font is cut into the cache; a font
    with a Reserved Font Name is returned as it is."""
    font = find(role, cfg, weight=weight, italic=italic, family=family, **kw)
    if not font.variable:
        return font.path, font
    entry = find_entry(font.family)
    if reserved_font_names(entry):
        return font.path, font
    out = static_instance(font.path, weight=font.weight, italic=font.italic,
                          family=font.family, cache_dir=_font_cache_dir(cfg, cache_dir))
    return out, font


def fontconfig_file(font_dirs: Iterable[str | os.PathLike], cache_dir: str | os.PathLike) -> Path:
    """A fonts.conf that adds ``font_dirs`` to the system configuration, for
    rsvg-convert (set FONTCONFIG_FILE to it)."""
    dirs = sorted({str(Path(d).resolve()) for d in font_dirs})
    cdir = Path(cache_dir)
    key = hashlib.sha256("\n".join(dirs).encode()).hexdigest()[:8]
    conf = cdir / f"fonts-{key}.conf"
    body = ['<?xml version="1.0"?>', '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">',
            "<fontconfig>", '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>']
    body += [f"  <dir>{_xml(d)}</dir>" for d in dirs]
    body += [f"  <cachedir>{_xml(str((cdir / 'fc-cache').resolve()))}</cachedir>", "</fontconfig>", ""]
    text = "\n".join(body)
    if not conf.is_file() or conf.read_text(encoding="utf-8") != text:
        cdir.mkdir(parents=True, exist_ok=True)
        conf.write_text(text, encoding="utf-8")
    return conf


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# =========================================================================== Pillow
def pillow_font(role: str = "sans", size: float = 14, cfg: Any = None, *, weight: int = 400,
                path: str | os.PathLike | None = None, allow_default: bool = True):
    """A Pillow FreeTypeFont for ``role`` at ``size`` px and ``weight`` (variable fonts
    get their wght axis set). ``path`` overrides resolution (annotate.font_path).
    Pillow's built-in bitmap font is returned only when no file resolves and
    ``allow_default``; its ``lt_default`` attribute is then True."""
    try:
        from PIL import ImageFont
    except ImportError:
        raise FontError("drawing text needs Pillow (run the setup skill's bootstrap step)") from None
    try:
        p = Path(path) if path else find(role, cfg, weight=weight).path
        f = ImageFont.truetype(str(p), round(size))
        info = read_info(p)
        if info.variable:
            axes = f.get_variation_axes()
            vals = []
            for a, (tag, (mn, df, mx)) in zip(axes, info.axes.items()):
                want = {"wght": weight, "wdth": 100, "opsz": size}.get(tag, df)
                vals.append(max(mn, min(mx, want)))
            f.set_variation_by_axes(vals)
        f.lt_default = False
        return f
    except (FontError, OSError) as exc:
        if not allow_default:
            raise FontError(f"no TrueType font for {role}: {exc}") from None
        f = ImageFont.load_default(size)
        f.lt_default = True
        return f


# =========================================================================== fetch
def fetch(font_id: str, cfg: Any = None, *, dest_root: str | os.PathLike | None = None,
          allow_download: bool | None = None, opener: Callable = urllib.request.urlopen) -> list[Path]:
    """Download a catalog font into <data>/fonts/<id>/ and check every file against
    its pinned sha256. Refused when fonts.allow_download is false. A mismatch writes
    nothing. Returns the written paths (fonts and licence)."""
    entry = catalog().get(font_id) or find_entry(font_id)
    if entry is None:
        raise FontError(f"unknown font {font_id!r} (python3 -m ltcore.fonts list)")
    allowed = _cfg_get(cfg, "fonts.allow_download", True) if allow_download is None else allow_download
    if not allowed:
        raise FontError("fonts.allow_download is false; set it to true to let setup fetch fonts")
    spec = entry.get("fetch")
    if not spec:
        raise FontError(f"{entry['family']} has no pinned download; install it from your "
                        f"package manager ({entry.get('packages', 'see the font catalog')})")
    root = Path(dest_root) if dest_root is not None else _data_fonts_dir(cfg, None)
    if root is None:
        raise FontError("no data folder for fetched fonts")
    dest = root / entry["id"]
    items = list(spec["files"]) + ([spec["licence"]] if spec.get("licence") else [])
    # Stage the complete set privately, then install only after every pin passes.
    # TemporaryDirectory uses 0700; mkstemp uses 0600, including the licence.
    with tempfile.TemporaryDirectory(prefix="lt-font-fetch-") as directory:
        got: list[tuple[dict, Path]] = []
        for it in items:
            url = it["url"]
            if not url.startswith("https://"):
                raise FontError(f"refusing a non-https font URL: {url}")
            fd, name = tempfile.mkstemp(dir=directory)
            staged = Path(name)
            digest, size = hashlib.sha256(), 0
            try:
                with os.fdopen(fd, "wb") as fh, opener(url, timeout=120) as response:
                    while chunk := response.read(65536):
                        size += len(chunk)
                        if size > 20 * 1024 * 1024:
                            raise FontError(f"download exceeds 20 MiB limit: {url}")
                        digest.update(chunk)
                        fh.write(chunk)
            except FontError:
                raise
            except Exception as exc:  # urllib raises several types
                raise FontError(f"download failed for {url}: {exc}") from None
            if digest.hexdigest() != it["sha256"]:
                raise FontError(f"sha256 mismatch for {url}: got {digest.hexdigest()}, "
                                f"pinned {it['sha256']}; nothing was written")
            got.append((it, staged))
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for it, staged in got:
            target = dest / Path(it["file"]).name
            fd, name = tempfile.mkstemp(dir=dest, suffix=".tmp")
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as fh, staged.open("rb") as source:
                    shutil.copyfileobj(source, fh, length=65536)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            out.append(target)
    return out


def state(entry: Mapping, cfg: Any = None, data_dir: str | os.PathLike | None = None) -> str:
    """bundled, fetched, installed, downloadable or unavailable."""
    if bundled_files(entry) and all(p.is_file() for p in bundled_files(entry)):
        return "bundled"
    ddir = _data_fonts_dir(cfg, data_dir)
    if ddir is not None and any((ddir / entry["id"]).glob("*.[ot]t[fc]")):
        return "fetched"
    if fc_match(entry["family"]) is not None:
        return "installed"
    return "downloadable" if entry.get("fetch") else "unavailable"


# =========================================================================== CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.fonts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="catalog fonts with role, licence and state")
    p.add_argument("--config")
    p = sub.add_parser("resolve", help="print the file a role resolves to")
    p.add_argument("role", nargs="?", default="sans", choices=ROLES + ("mono_bold",))
    p.add_argument("--weight", type=int, default=400)
    p.add_argument("--italic", action="store_true")
    p.add_argument("--static", action="store_true", help="cut a static instance of a variable font")
    p.add_argument("--config")
    p = sub.add_parser("fetch", help="download a catalog font into the data folder (sha256 checked)")
    p.add_argument("id")
    p.add_argument("--config")
    p.add_argument("--yes", action="store_true", help="the user agreed to the download")
    a = ap.parse_args(argv)
    from ltcore import config
    try:
        cfg = config.load(a.config)
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        if a.cmd == "list":
            for e in catalog().values():
                lic = (e.get("licence") or {}).get("spdx", "")
                print(f"{e['id']:30s} {e['role']:5s} {state(e, cfg):12s} {lic}")
            return 0
        if a.cmd == "resolve":
            f = resolve(a.role, cfg=cfg, weight=a.weight, italic=a.italic, static=a.static)
            print(f"{f}\t{f.family}\t{f.source}" + ("\tFALLBACK" if f.fallback else ""))
            return 0
        if a.cmd == "fetch":
            if not a.yes:
                print("error: fetching downloads a font; ask the user, then pass --yes", file=sys.stderr)
                return 2
            for p in fetch(a.id, cfg):
                print(f"wrote {p}")
            return 0
    except FontError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
