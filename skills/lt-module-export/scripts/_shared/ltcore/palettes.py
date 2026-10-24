# Generated from shared/ltcore/palettes.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Palette catalog, theme derivation and the colour checks behind them.

A catalog entry (palettes/catalog.json, or a user file named by
``style.custom_palettes_file``) gives colour roles: background, surface, text,
text_muted, border, accent_1..N, positive, negative. ``derive_theme()`` turns
it into the theme dict the diagram engine draws with:

    id, name, mode ("light" or "dark"), dark (bool), source ("derived" or "override"),
    canvas, cl_fill, cl_stroke, cl_text, key_fill, key_stroke, ink, muted, label,
    roles  {risk, mech, anat, sign, inv, dx, tx: (fill, stroke, text)},
    edges  {stim, inh, excl, req: (colour, dash, head)},
    accents {rosewater .. lavender: hex}, glyph_lc (L, C), checks {...}

API: ``derive_theme(id_or_entry, cfg)``, ``pdf_theme(id, cfg=)`` (page colours for
PDFs from the same theme), ``load_catalog(cfg)`` (catalog plus custom palettes),
``catalog()``, ``themes(cfg)``, ``print_theme(cfg)``, ``check_theme(theme)``,
``rank()``, ``PaletteError``.

Derivation rule (light themes; dark themes mirror it):
  1. Structure: canvas = background, cl_fill = surface (or the background
     stepped 0.035 darker in Oklch L), ink = cl_text = text, muted = text_muted
     stepped toward the text colour until it reaches 4.5:1 on canvas and on
     cl_fill, label = muted, cl_stroke = border.
  2. Accents: palettes with more than 6 accents use the 6 whose derived strokes
     stay furthest apart under normal vision and the 3 simulated colour-vision
     deficiencies; the 6 are then matched to the role hues (risk 60, dx 140,
     sign 25, inv 260, mech 195, tx 300 degrees) by the assignment with the
     smallest total hue distance. "anat" is a neutral.
  3. Strokes keep the accent's own Oklch lightness and chroma, so a palette's
     designed light/dark pairs stay apart. A stroke steps darker in 0.01 L steps
     only until it reaches 3:1 on canvas, on cl_fill and on its own fill.
  4. Fills move the accent toward the background by one shared factor t (chroma
     by min(1, 1.5 t)), so hue and the order of lightness are kept. t starts at
     0.40 and steps down by 0.01 until text reaches 7:1 on every fill (4.5:1 when
     the text reaches less than 7:1 on the background itself). When 7:1 needs
     fills so pale that two fall within one just-noticeable difference (OKLab
     0.02), t steps back up to the palest value that keeps them apart and 4.5:1 is
     accepted. Below 4.5:1 at t 0.04 the palette fails.
     When two of these fills sit closer than 0.03 in greyscale lightness, the
     fills are re-spaced on a ladder 0.035 apart in greyscale lightness, starting
     beside the background, keeping each fill's hue and chroma and the accents'
     lightness order. The ladder is kept only when text still reaches the target
     on every fill.
  5. The neutral "anat" stroke takes the lightness furthest from the other
     strokes, so it also separates in greyscale. Strokes then step darker (light)
     or lighter (dark) until they reach 3:1 on their final fill.
  6. Edges: stim and excl use muted (dotted for excl), inh the tx stroke, req
     the anat stroke (dashed).

Checks (``check_theme``). Hard gates, which always raise PaletteError naming the
pair and the measured value: WCAG 2.x contrast (text on fills 4.5, target 7;
muted on canvas and cl_fill 4.5; label on canvas 4.5; strokes on canvas, cl_fill
and own fill 3; edges on canvas and cl_fill 3), and two role strokes or fills
closer than one just-noticeable difference (OKLab 0.02) in normal vision.

Accessibility gates: every pair of role strokes at least OKLab 0.05 apart under
the protan, deutan and tritan simulations, and every pair of role fills at least
0.03 apart in greyscale lightness. A theme that fails one is never a default:
``rank()`` records its measurements and excludes it from every weighted order.
With ``cvd = "report"`` (catalog default) it stays selectable and the failures
are listed in ``checks.reported``; with ``cvd = "check"`` derive_theme raises.

Derived themes are cached in <cache>/themes/<id>-<sha8>.json. The key covers the
roles, the override, the effective cvd policy, the mode, the algorithm version
and the thresholds.

    python3 -m ltcore.palettes list [--config F]
    python3 -m ltcore.palettes show <id> [--config F] [--json]
    python3 -m ltcore.palettes check [<id> ...] [--config F]
    python3 -m ltcore.palettes rank [--write FILE] [--weights NAME]
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import copy  # noqa: E402
import difflib  # noqa: E402
import hashlib  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import tomllib  # noqa: E402
from typing import Any, Iterable, Mapping  # noqa: E402

NEEDS = ["palettes/catalog.json", "palettes/overrides/", "palettes/ATTRIBUTION.md"]

SHARED = Path(__file__).resolve().parent.parent
CATALOG_FILE = SHARED / "palettes" / "catalog.json"
OVERRIDES_DIR = SHARED / "palettes" / "overrides"

ALGO_VERSION = "3"          # part of the cache key; bump when derivation changes

ROLES = ("risk", "mech", "anat", "sign", "inv", "dx", "tx")
ROLE_HUES = {"risk": 60.0, "dx": 140.0, "sign": 25.0, "inv": 260.0, "mech": 195.0, "tx": 300.0}
HUE_ROLES = tuple(ROLE_HUES)
EDGE_KINDS = ("stim", "inh", "excl", "req")
EXCL_DASH = "1.6 3.4"       # dotted "argues against", so it never reads as "blocks"

TEXT_MIN, TEXT_TARGET, NONTEXT_MIN = 4.5, 7.0, 3.0
T_START, T_MIN = 0.40, 0.04   # fill factor toward the accent: first try, palest allowed
JND_OK = 0.02               # OKLab just-noticeable difference (CSS Color 4 deltaEOK)
CVD_MIN = 0.05              # role strokes closer than this under a simulation "collapse" (2.5 JND)
GREY_MIN = 0.03             # minimum greyscale lightness gap between role fills
SIMS = ("none", "protan", "deutan", "tritan")

# Catppuccin accent names: the engine's glyph layers address accents by these names.
ACCENT_NAMES = ("rosewater", "flamingo", "pink", "mauve", "red", "maroon", "peach", "yellow",
                "green", "teal", "sky", "sapphire", "blue", "lavender")
# Oklch hue of each name in Catppuccin Latte; another palette maps each name to its
# accent nearest in hue.
ACCENT_NAME_HUES = {"rosewater": 33.9, "flamingo": 20.6, "pink": 342.3, "mauve": 304.8,
                    "red": 19.6, "maroon": 21.1, "peach": 44.6, "yellow": 70.4,
                    "green": 139.9, "teal": 193.6, "sky": 228.2, "sapphire": 215.7,
                    "blue": 263.1, "lavender": 275.5}


class PaletteError(Exception):
    """A palette that cannot be loaded or cannot pass the checks. The message names
    the palette, the colour pair and the measured value."""


# =========================================================================== colour maths
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def hex_rgb(h: str) -> tuple[float, float, float]:
    """'#rrggbb' or '#rgb' -> (r, g, b) in 0..1."""
    m = _HEX.match(h.strip()) if isinstance(h, str) else None
    if not m:
        raise PaletteError(f"not a colour: {h!r} (use #rrggbb)")
    s = m.group(1)
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_hex(rgb: Iterable[float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def norm_hex(h: str) -> str:
    return rgb_hex(hex_rgb(h))


def lin(c: float) -> float:
    """sRGB transfer to linear light (WCAG 2.2 threshold 0.04045)."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def unlin(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def luminance(h: str) -> float:
    """WCAG 2.x relative luminance."""
    r, g, b = (lin(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio, 1 to 21."""
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1 / 3), x)


def oklab_linear(rgb_lin: Iterable[float]) -> tuple[float, float, float]:
    """Oklab (Ottosson 2020) of a linear-light sRGB triple."""
    r, g, b = rgb_lin
    l_ = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab(h: str) -> tuple[float, float, float]:
    return oklab_linear([lin(c) for c in hex_rgb(h)])


def oklch(h: str) -> tuple[float, float, float]:
    """(L, C, hue in degrees) in Oklch."""
    L, a, b = oklab(h)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _oklab_to_linear(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def _in_gamut(rgb: Iterable[float], eps: float = 1e-6) -> bool:
    return all(-eps <= c <= 1 + eps for c in rgb)


def oklch_hex(L: float, C: float, h: float) -> str:
    """#rrggbb for an Oklch colour; chroma is reduced (bisection) until the colour
    fits in sRGB, so lightness and hue are kept."""
    L = max(0.0, min(1.0, L))
    hr = math.radians(h)

    def at(c: float) -> tuple[float, float, float]:
        return _oklab_to_linear(L, c * math.cos(hr), c * math.sin(hr))

    rgb = at(C)
    if not _in_gamut(rgb):
        lo, hi = 0.0, C
        for _ in range(40):
            mid = (lo + hi) / 2
            if _in_gamut(at(mid)):
                lo = mid
            else:
                hi = mid
        rgb = at(lo)
    return rgb_hex(unlin(c) for c in rgb)


def mix(c1: str, c2: str, t: float) -> str:
    """c1 moved toward c2 by t (0..1) in Oklab."""
    a, b = oklab(c1), oklab(c2)
    L, A, B = (x + (y - x) * t for x, y in zip(a, b))
    return rgb_hex(unlin(c) for c in _oklab_to_linear(L, A, B))


# -- CIELAB and CIEDE2000 (Sharma, Wu and Dalal 2005), for the ranking --------------
_M_XYZ = ((0.4124564, 0.3575761, 0.1804375),
          (0.2126729, 0.7151522, 0.0721750),
          (0.0193339, 0.1191920, 0.9503041))
_WHITE = tuple(math.fsum(row) for row in _M_XYZ)   # fsum: the same bits on every Python
_EPS, _KAPPA = 216 / 24389, 24389 / 27


def _lab_f(t: float) -> float:
    return _cbrt(t) if t > _EPS else (_KAPPA * t + 16) / 116


def lab_linear(rgb_lin: Iterable[float]) -> tuple[float, float, float]:
    r, g, b = rgb_lin
    x, y, z = ((row[0] * r + row[1] * g + row[2] * b) / w for row, w in zip(_M_XYZ, _WHITE))
    fx, fy, fz = _lab_f(x), _lab_f(y), _lab_f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab(h: str) -> tuple[float, float, float]:
    return lab_linear([lin(c) for c in hex_rgb(h)])


def ciede2000(lab1: Iterable[float], lab2: Iterable[float]) -> float:
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c7 = ((C1 + C2) / 2) ** 7
    G = 0.5 * (1 - math.sqrt(c7 / (c7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = 0.0 if a1p == 0 and b1 == 0 else math.degrees(math.atan2(b1, a1p)) % 360
    h2p = 0.0 if a2p == 0 and b2 == 0 else math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dh = h2p - h1p
        dhp = dh - 360 if dh > 180 else dh + 360 if dh < -180 else dh
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    if C1p * C2p == 0:
        hbp = hs
    elif abs(h1p - h2p) <= 180:
        hbp = hs / 2
    else:
        hbp = (hs + 360) / 2 if hs < 360 else (hs - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    RC = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    SL = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    SC, SH = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2 * dtheta)) * RC
    tL, tC, tH = dLp / SL, dCp / SC, dHp / SH
    return math.sqrt(tL * tL + tC * tC + tH * tH + RT * tC * tH)


# -- colour-vision simulation (Machado, Oliveira and Fernandes 2009, severity 1.0) --
MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}


def simulate_linear(h: str, kind: str) -> tuple[float, float, float]:
    """Linear-light sRGB of ``h`` as seen with ``kind`` (none, protan, deutan, tritan,
    grey), clipped to 0..1. Greyscale is Rec. 709 luminance."""
    r, g, b = (lin(c) for c in hex_rgb(h))
    if kind == "none":
        return r, g, b
    if kind == "grey":
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return y, y, y
    M = MACHADO[kind]
    return tuple(max(0.0, min(1.0, M[i][0] * r + M[i][1] * g + M[i][2] * b))  # type: ignore[return-value]
                 for i in range(3))


def simulate(h: str, kind: str) -> str:
    return rgb_hex(unlin(c) for c in simulate_linear(h, kind))


def de_ok(a: str, b: str, kind: str = "none") -> float:
    """OKLab distance between two colours as seen with ``kind``."""
    return math.dist(oklab_linear(simulate_linear(a, kind)), oklab_linear(simulate_linear(b, kind)))


def de00(a: str, b: str, kind: str = "none") -> float:
    """CIEDE2000 between two colours as seen with ``kind``."""
    return ciede2000(lab_linear(simulate_linear(a, kind)), lab_linear(simulate_linear(b, kind)))


def grey_l(h: str) -> float:
    """OKLab lightness of the colour's greyscale (Rec. 709 luminance) version."""
    r, g, b = (lin(c) for c in hex_rgb(h))
    return _cbrt(0.2126 * r + 0.7152 * g + 0.0722 * b)


def hue_gap(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % 360
    return min(d, 360 - d)


# =========================================================================== catalog
def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _catalog_file(path: Any) -> Path:
    """A catalog path; anything that is not a path (a config, None) means the shipped file."""
    return Path(path) if isinstance(path, (str, os.PathLike)) else CATALOG_FILE


def catalog(path: Any = None) -> dict[str, dict]:
    """Shipped catalog entries by id, in catalog order. ``path`` picks another catalog
    file; a config object there is ignored. ``load_catalog(cfg)`` adds the user's
    custom palettes."""
    data = _read_json(_catalog_file(path))
    return {p["id"]: dict(p, origin="catalog") for p in data["palettes"]}


def catalog_meta(path: Any = None) -> dict:
    data = _read_json(_catalog_file(path))
    return {k: v for k, v in data.items() if k != "palettes"}


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ROLE_RE = re.compile(r"^(background|surface|text|text_muted|border|positive|negative|accent_[1-9][0-9]?)$")


def _validate_entry(e: Mapping, where: str) -> dict:
    if not isinstance(e, Mapping):
        raise PaletteError(f"{where}: a palette must be a table")
    pid = e.get("id")
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise PaletteError(f"{where}: id {pid!r} must match {_ID_RE.pattern}")
    roles = e.get("roles")
    override = e.get("override")
    if not isinstance(roles, Mapping) and not isinstance(override, Mapping):
        raise PaletteError(f"{where}: palette {pid} needs a roles table")
    out = dict(e)
    if isinstance(roles, Mapping):
        clean = {}
        for k, v in roles.items():
            if not _ROLE_RE.match(k):
                raise PaletteError(f"{where}: palette {pid}: unknown role {k!r}")
            clean[k] = norm_hex(v)
        for need in ("background", "text"):
            if need not in clean:
                raise PaletteError(f"{where}: palette {pid} has no {need} role")
        out["roles"] = clean
    mode = out.get("mode", "auto")
    if mode not in ("light", "dark", "categorical-only", "sequential", "auto"):
        raise PaletteError(f"{where}: palette {pid}: mode {mode!r} is not light, dark, "
                           f"categorical-only, sequential or auto")
    if out.get("cvd", "report") not in ("check", "report"):
        raise PaletteError(f"{where}: palette {pid}: cvd must be \"check\" or \"report\"")
    out.setdefault("name", pid)
    out["origin"] = e.get("origin", where) if where == "palette" else where
    return out


def load_custom(path: str | os.PathLike) -> dict[str, dict]:
    """User palettes from a TOML file: ``[[palette]]`` tables with id, name, mode,
    optional cvd ("check" or "report") and a ``[palette.roles]`` table."""
    p = Path(path)
    try:
        with open(p, "rb") as fh:
            doc = tomllib.load(fh)
    except OSError as exc:
        raise PaletteError(f"{p}: cannot read custom palettes: {exc.strerror or exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise PaletteError(f"{p}: not valid TOML: {exc}") from None
    out: dict[str, dict] = {}
    for i, e in enumerate(doc.get("palette", [])):
        entry = _validate_entry(e, f"{p} palette[{i}]")
        out[entry["id"]] = entry
    return out


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    if cfg is None:
        return default
    try:
        return cfg.get(key, default)
    except TypeError:
        return default


def available(cfg: Any = None) -> dict[str, dict]:
    """Catalog entries plus the user's custom palettes (a custom id replaces a
    catalog id of the same name)."""
    out = {k: dict(v, origin="catalog") for k, v in catalog().items()}
    custom = _cfg_get(cfg, "style.custom_palettes_file", "")
    if custom:
        out.update(load_custom(custom))
    return out


def load_catalog(cfg: Any = None) -> dict[str, dict]:
    """Every palette a config can name, by id: the catalog plus ``style.custom_palettes_file``.
    Each entry has ``id``, ``name``, ``mode``, ``roles`` and, for catalog entries,
    ``licence`` and ``rank``."""
    return available(cfg)


def get(palette_id: str, cfg: Any = None) -> dict:
    pals = available(cfg)
    if palette_id not in pals:
        near = difflib.get_close_matches(palette_id, list(pals), n=3)
        hint = f"; did you mean {', '.join(near)}?" if near else ""
        raise PaletteError(f"unknown palette {palette_id!r}{hint} "
                           f"(python3 -m ltcore.palettes list shows them)")
    return pals[palette_id]


def override_path(palette_id: str) -> Path:
    return OVERRIDES_DIR / f"{palette_id}.toml"


def load_override(palette_id: str) -> dict | None:
    """The hand-tuned theme for a palette id, or None."""
    p = override_path(palette_id)
    if not p.is_file():
        return None
    with open(p, "rb") as fh:
        return tomllib.load(fh)


# =========================================================================== derivation
def is_light(entry_or_theme: Mapping) -> bool:
    if "canvas" in entry_or_theme:
        return luminance(entry_or_theme["canvas"]) > luminance(entry_or_theme["ink"])
    mode = entry_or_theme.get("mode")
    if mode == "light":
        return True
    if mode == "dark":
        return False
    r = entry_or_theme["roles"]
    return luminance(r["background"]) > luminance(r["text"])


def accents_of(roles: Mapping[str, str]) -> list[tuple[str, str]]:
    keys = sorted((k for k in roles if k.startswith("accent_")), key=lambda k: int(k.split("_")[1]))
    return [(k, roles[k]) for k in keys]


def _step_until(colour: str, ok, darker: bool, step: float = 0.01, limit: int = 120) -> str | None:
    """Step ``colour`` in Oklch L (hue and chroma kept, chroma clamped to sRGB) until
    ``ok(hex)`` holds. None when L runs out."""
    L, C, h = oklch(colour)
    cur = colour
    for _ in range(limit):
        if ok(cur):
            return cur
        L += -step if darker else step
        if L < 0 or L > 1:
            return None
        cur = oklch_hex(L, C, h)
    return cur if ok(cur) else None


def _stroke(accent: str, light: bool, grounds: list[str]) -> str | None:
    return _step_until(accent, lambda c: all(contrast(c, g) >= NONTEXT_MIN for g in grounds),
                       darker=light)


def _fill(accent_l: float, accent_c: float, hue: float, canvas: str, t: float) -> str:
    """The accent moved toward the canvas: lightness by t, chroma by min(1, 1.5 t), so a
    pale fill keeps enough colour to name its hue."""
    Lc = oklch(canvas)[0]
    return oklch_hex(Lc + (accent_l - Lc) * t, accent_c * min(1.0, 1.5 * t), hue)


def _assign_roles(cands: list[dict]) -> dict[str, dict]:
    """Match 6 candidates to the 6 hue roles with the smallest total hue distance."""
    best, best_cost = None, None
    for perm in itertools.permutations(range(len(cands)), len(HUE_ROLES)):
        cost = math.fsum(hue_gap(cands[i]["hue"], ROLE_HUES[r]) for i, r in zip(perm, HUE_ROLES))
        if best_cost is None or cost < best_cost - 1e-9:
            best, best_cost = perm, cost
    return {r: cands[i] for i, r in zip(best, HUE_ROLES)}


def _pick_accents(cands: list[dict]) -> list[dict]:
    """The 6 candidates whose strokes stay furthest apart: the subset with the
    largest worst-pair separation, where a pair's separation is the smallest of its
    OKLab distances under normal vision and the 3 simulations and its greyscale
    lightness gap; ties go to the larger worst normal-vision distance."""
    if len(cands) <= 6:
        return cands
    n = len(cands)
    labs = [{s: oklab_linear(simulate_linear(c["stroke"], s)) for s in SIMS} for c in cands]
    grey = [grey_l(c["stroke"]) for c in cands]
    sep = [[0.0] * n for _ in range(n)]
    nor = [[0.0] * n for _ in range(n)]
    for i, j in itertools.combinations(range(n), 2):
        d = min(math.dist(labs[i][s], labs[j][s]) for s in SIMS)
        sep[i][j] = sep[j][i] = min(d, abs(grey[i] - grey[j]))
        nor[i][j] = nor[j][i] = math.dist(labs[i]["none"], labs[j]["none"])
    best, best_key = None, None
    for combo in itertools.combinations(range(n), 6):
        pairs = list(itertools.combinations(combo, 2))
        key = (min(sep[i][j] for i, j in pairs), min(nor[i][j] for i, j in pairs))
        if best_key is None or key > best_key:
            best, best_key = combo, key
    return [cands[i] for i in best]


def _neutral_stroke(light: bool, hue: float, grounds: list[str], others: list[str]) -> str | None:
    """A low-chroma stroke whose greyscale lightness sits furthest from ``others``
    among the lightnesses that reach 3:1 on every ground, searched within 0.08 of
    the others' Oklch L range (a near-black or near-white stroke would read as text)."""
    best, best_gap = None, -1.0
    others_l = [grey_l(o) for o in others]
    ok_l = [oklch(o)[0] for o in others] or [0.5]
    lo, hi = max(0.05, min(ok_l) - 0.08), min(0.95, max(ok_l) + 0.08)
    for i in range(round(lo * 100), round(hi * 100) + 1):
        c = oklch_hex(i / 100, 0.02, hue)
        if not all(contrast(c, g) >= NONTEXT_MIN for g in grounds):
            continue
        gap = min(abs(grey_l(c) - x) for x in others_l) if others_l else 1.0
        if gap > best_gap + 1e-9:
            best, best_gap = c, gap
    return best


def _separate_fills(fills: Mapping[str, str], parts: Mapping[str, Mapping],
                    text: str, canvas: str, light: bool) -> dict[str, str]:
    """Try a greyscale ladder, keeping each fill's hue and accent lightness order.
    Keep the original fills if a ladder cannot retain the text contrast target.
    Strokes are clamped against the resulting fills by the caller.
    """
    if separation(fills)["grey"]["dl"] >= GREY_MIN:
        return dict(fills)
    ordered = sorted(fills, key=lambda role: (parts[role]["L"], role), reverse=light)
    result = {}
    # Start beside the background and move toward the ink. The 0.005 margin
    # absorbs 8-bit sRGB rounding, which can shrink a nominal 0.03 step.
    anchor = grey_l(canvas) - 0.004 if light else grey_l(canvas) + 0.004
    for index, role in enumerate(ordered):
        target = anchor + (-1 if light else 1) * index * (GREY_MIN + 0.005)
        _, chroma, hue = oklch(fills[role])
        lo, hi = 0.0, 1.0
        for _ in range(24):
            mid = (lo + hi) / 2
            if grey_l(oklch_hex(mid, chroma, hue)) < target:
                lo = mid
            else:
                hi = mid
        result[role] = oklch_hex((lo + hi) / 2, chroma, hue)
    target = TEXT_TARGET if contrast(text, canvas) >= TEXT_TARGET else TEXT_MIN
    if (min(contrast(text, f) for f in result.values()) >= target and
            separation(result)["grey"]["dl"] >= GREY_MIN):
        return result
    return dict(fills)


def _derive(entry: Mapping) -> dict:
    pid = entry["id"]
    r = entry["roles"]
    light = is_light(entry)
    canvas, text = r["background"], r["text"]
    if contrast(text, canvas) < TEXT_TARGET:
        if contrast(text, canvas) < TEXT_MIN:
            raise PaletteError(f"{pid}: text {text} on background {canvas} is "
                               f"{contrast(text, canvas):.2f}:1, below {TEXT_MIN}")
    surface = r.get("surface")
    if not surface or contrast(surface, canvas) < 1.03:
        Lb, Cb, hb = oklch(canvas)
        surface = oklch_hex(Lb - 0.035 if light else Lb + 0.045, Cb, hb)
    cl_fill = surface
    border = r.get("border") or mix(canvas, text, 0.3)

    def muted_ok(c: str) -> bool:
        return contrast(c, canvas) >= TEXT_MIN and contrast(c, cl_fill) >= TEXT_MIN

    muted0 = r.get("text_muted") or mix(text, canvas, 0.35)
    muted = muted0 if muted_ok(muted0) else None
    if muted is None:
        for i in range(1, 101):
            cand = mix(muted0, text, i / 100)
            if muted_ok(cand):
                muted = cand
                break
    if muted is None:
        best = min(contrast(text, canvas), contrast(text, cl_fill))
        raise PaletteError(f"{pid}: muted text cannot reach {TEXT_MIN}:1 on canvas {canvas} and "
                           f"container fill {cl_fill}; even the text colour {text} reaches only "
                           f"{best:.2f}:1")

    accents = accents_of(r)
    if len(accents) < 6:
        # a 5-accent set may borrow its distinct positive and negative colours
        extra = [(k, r[k]) for k in ("negative", "positive")
                 if r.get(k) and r[k] not in [h for _, h in accents]]
        accents += extra[:6 - len(accents)]
    if len(accents) < 6:
        raise PaletteError(f"{pid}: needs 6 accents for the 6 coloured roles, has {len(accents)}")
    cands = []
    for key, hx in accents:
        L, C, h = oklch(hx)
        st = _stroke(hx, light, [canvas, cl_fill])
        if st is None:
            continue
        cands.append({"key": key, "hex": hx, "L": L, "C": C, "hue": h, "stroke": st})
    if len(cands) < 6:
        raise PaletteError(f"{pid}: only {len(cands)} accents can reach {NONTEXT_MIN}:1 "
                           f"on canvas {canvas} and container fill {cl_fill}")
    chosen = _assign_roles(_pick_accents(cands))

    # anat: a neutral at the text hue (or warm grey when the text has no hue)
    tL, tC, th = oklch(text)
    n_hue = th if tC > 0.01 else 60.0
    anat_stroke = _neutral_stroke(light, n_hue, [canvas, cl_fill],
                                  [c["stroke"] for c in chosen.values()])
    if anat_stroke is None:
        raise PaletteError(f"{pid}: no neutral stroke reaches {NONTEXT_MIN}:1 on canvas {canvas}")
    # its fill sits at the median accent lightness, so it does not set the fill factor
    accent_ls = sorted(c["L"] for c in chosen.values())
    parts = dict(chosen)
    parts["anat"] = {"key": "neutral", "hex": anat_stroke,
                     "L": (accent_ls[2] + accent_ls[3]) / 2, "C": 0.02, "hue": n_hue,
                     "stroke": anat_stroke}

    # fills: one shared factor t toward the accent. t steps down from T_START until
    # text reaches 7:1 (AAA) on every fill. When the text reaches less than 7:1 on the
    # canvas itself, no fill can do better, so 4.5:1 (AA) is the target.
    # If 7:1 needs fills so pale that two of them fall within one just-noticeable
    # difference, t steps back up to the palest value that keeps every pair apart, and
    # 4.5:1 is accepted.
    def fills_at(tt: float) -> dict[str, str]:
        return {role: _fill(p["L"], p["C"], p["hue"], canvas, tt) for role, p in parts.items()}

    def apart(fs: dict[str, str]) -> bool:
        return all(de_ok(a, b) >= JND_OK for a, b in itertools.combinations(fs.values(), 2))

    target = TEXT_TARGET if contrast(text, canvas) >= TEXT_TARGET else TEXT_MIN
    t, fills = T_START, {}
    while True:
        fills = fills_at(t)
        worst = min(contrast(text, f) for f in fills.values())
        if worst >= target or t <= T_MIN + 1e-9:
            break
        t = round(t - 0.01, 4)
    if target > TEXT_MIN and not apart(fills):
        tt = t
        while tt < T_START - 1e-9:
            tt = round(tt + 0.01, 4)
            f2 = fills_at(tt)
            if apart(f2) and min(contrast(text, f) for f in f2.values()) >= TEXT_MIN:
                t, fills = tt, f2
                break
    worst_role = min(fills, key=lambda k: contrast(text, fills[k]))
    if contrast(text, fills[worst_role]) < TEXT_MIN:
        raise PaletteError(f"{pid}: text {text} on the {worst_role} fill {fills[worst_role]} is "
                           f"{contrast(text, fills[worst_role]):.2f}:1, below {TEXT_MIN}")

    fills = _separate_fills(fills, parts, text, canvas, light)
    roles: dict[str, tuple[str, str, str]] = {}
    sources: dict[str, str] = {}
    for role in ROLES:
        p = parts[role]
        fill = fills[role]
        st = _stroke(p["stroke"], light, [canvas, cl_fill, fill])
        if st is None:
            raise PaletteError(f"{pid}: the {role} stroke cannot reach {NONTEXT_MIN}:1 on its fill {fill}")
        roles[role] = (fill, st, text)
        sources[role] = p["key"]

    edge = muted
    theme = {
        "id": pid, "name": entry.get("name", pid), "mode": "light" if light else "dark",
        "dark": not light, "source": "derived",
        "canvas": canvas, "cl_fill": cl_fill, "cl_stroke": border, "cl_text": text,
        "key_fill": canvas, "key_stroke": border,
        "ink": text, "muted": muted, "label": muted,
        "roles": roles,
        "edges": {"stim": (edge, False, "arrow"), "inh": (roles["tx"][1], False, "tee"),
                  "excl": (edge, EXCL_DASH, "tee"), "req": (roles["anat"][1], True, "arrow")},
        "accents": _accent_names(r),
        "glyph_lc": _glyph_lc(roles),
        "derivation": {"fill_t": t, "role_accents": sources,
                       "muted_from": r.get("text_muted") and "text_muted" or "text"},
    }
    return theme


def _accent_names(roles: Mapping[str, str]) -> dict[str, str]:
    """Each Catppuccin accent name mapped to the palette's accent nearest in hue."""
    acc = [(hx, oklch(hx)) for _, hx in accents_of(roles)] or [(roles["text"], oklch(roles["text"]))]
    out = {}
    for name in ACCENT_NAMES:
        target = ACCENT_NAME_HUES[name]
        out[name] = min(acc, key=lambda a: (hue_gap(a[1][2], target), -a[1][1]))[0]
    return out


def _glyph_lc(roles: Mapping[str, tuple]) -> tuple[float, float]:
    """Median Oklch (L, C) of the coloured role strokes: stage glyphs are drawn at an
    accent's hue with this lightness and chroma."""
    lc = sorted(oklch(roles[r][1])[:2] for r in HUE_ROLES)
    Ls = sorted(x[0] for x in lc)
    Cs = sorted(x[1] for x in lc)
    return round((Ls[2] + Ls[3]) / 2, 3), round((Cs[2] + Cs[3]) / 2, 3)


def _from_override(entry: Mapping, ov: Mapping) -> dict:
    t = ov["theme"]
    roles = {r: tuple(norm_hex(x) for x in t["roles"][r]) for r in ROLES}
    edges = {}
    for k in EDGE_KINDS:
        e = t["edges"][k]
        dash = e.get("dash", False)
        edges[k] = (norm_hex(e["colour"]), dash, e["head"])
    theme = {
        "id": entry["id"], "name": ov.get("name", entry.get("name", entry["id"])),
        "mode": ov.get("mode") or ("light" if luminance(t["canvas"]) > luminance(t["ink"]) else "dark"),
        "source": "override",
    }
    theme["dark"] = theme["mode"] == "dark"
    for k in ("canvas", "cl_fill", "cl_stroke", "cl_text", "key_fill", "key_stroke", "ink", "muted", "label"):
        theme[k] = norm_hex(t[k])
    theme["roles"] = roles
    theme["edges"] = edges
    theme["accents"] = {k: norm_hex(v) for k, v in ov.get("accents", {}).items()} or \
        (_accent_names(entry["roles"]) if entry.get("roles") else {})
    gl = ov.get("glyph_lc")
    theme["glyph_lc"] = tuple(gl) if gl else _glyph_lc(roles)
    if ov.get("tokens"):
        theme["tokens"] = {k: norm_hex(v) for k, v in ov["tokens"].items()}
    return theme


# =========================================================================== checks
def contrast_pairs(theme: Mapping) -> list[tuple[str, str, str, float]]:
    """(label, fg, bg, minimum) for every pair the engine draws."""
    out = []
    for r in ROLES:
        fill, stroke, text = theme["roles"][r]
        out.append((f"{r} text on {r} fill", text, fill, TEXT_MIN))
        out.append((f"{r} stroke on {r} fill", stroke, fill, NONTEXT_MIN))
        out.append((f"{r} stroke on canvas", stroke, theme["canvas"], NONTEXT_MIN))
        out.append((f"{r} stroke on container fill", stroke, theme["cl_fill"], NONTEXT_MIN))
    out += [("text on canvas", theme["ink"], theme["canvas"], TEXT_MIN),
            ("stage title on container fill", theme["cl_text"], theme["cl_fill"], TEXT_MIN),
            ("key heading on key panel", theme["cl_text"], theme["key_fill"], TEXT_MIN),
            ("key text on key panel", theme["ink"], theme["key_fill"], TEXT_MIN),
            ("muted text on canvas", theme["muted"], theme["canvas"], TEXT_MIN),
            ("muted text on container fill", theme["muted"], theme["cl_fill"], TEXT_MIN),
            ("label on canvas", theme["label"], theme["canvas"], TEXT_MIN)]
    seen = set()
    for k in EDGE_KINDS:
        c = theme["edges"][k][0]
        for g, gname in ((theme["canvas"], "canvas"), (theme["cl_fill"], "container fill")):
            if (c, g) in seen:
                continue
            seen.add((c, g))
            out.append((f"{k} arrow on {gname}", c, g, NONTEXT_MIN))
    return out


def separation(colours: Mapping[str, str]) -> dict:
    """Worst pair per view: OKLab distance under none/protan/deutan/tritan and the
    greyscale lightness gap, plus CIEDE2000 for the ranking."""
    names = list(colours)
    res: dict[str, Any] = {}
    for s in SIMS:
        d, a, b = min((de_ok(colours[a], colours[b], s), a, b) for a, b in itertools.combinations(names, 2))
        e = min(de00(colours[a], colours[b], s) for a, b in itertools.combinations(names, 2))
        res[s] = {"de_ok": round(d, 4), "pair": [a, b], "de00": round(e, 2)}
    g, a, b = min((abs(grey_l(colours[a]) - grey_l(colours[b])), a, b)
                  for a, b in itertools.combinations(names, 2))
    res["grey"] = {"dl": round(g, 4), "pair": [a, b]}
    return res


def check_theme(theme: Mapping, *, cvd: str = "report") -> dict:
    """Measure a theme. Returns {"contrast": [...], "strokes": {...}, "fills": {...},
    "failures": [...], "reported": [...], "eligibility_failures": [...], "cvd_gate",
    "text_min", "nontext_min", "text_on_fill_min"}.
    Contrast shortfalls and normal-vision collapses below one JND are failures.
    Accessibility gate misses (role strokes under OKLab 0.05 apart in a protan,
    deutan or tritan simulation; role fills under 0.03 apart in greyscale
    lightness) go to "failures" when ``cvd`` is "check" and to "reported" when it
    is "report". "eligibility_failures" holds every failure and every gate miss
    whatever ``cvd`` is; a theme with any is never a default."""
    if cvd not in ("check", "report"):
        raise PaletteError(f"cvd must be check or report, got {cvd!r}")
    rows, failures, reported = [], [], []
    for label, fg, bg, need in contrast_pairs(theme):
        ratio = contrast(fg, bg)
        ok = ratio >= need - 1e-9
        rows.append({"what": label, "fg": fg, "bg": bg, "ratio": round(ratio, 2), "need": need, "ok": ok})
        if not ok:
            failures.append(f"{label}: {fg} on {bg} is {ratio:.2f}:1, below {need}")
    strokes = {r: theme["roles"][r][1] for r in ROLES}
    fills = {r: theme["roles"][r][0] for r in ROLES}
    st, fi = separation(strokes), separation(fills)
    for kind, sep, cols in (("strokes", st, strokes), ("fills", fi, fills)):
        if sep["none"]["de_ok"] < JND_OK:
            a, b = sep["none"]["pair"]
            failures.append(f"{a} and {b} {kind} ({cols[a]}, {cols[b]}) differ by OKLab "
                            f"{sep['none']['de_ok']:.4f} in normal vision, below {JND_OK}")
    cvd_msgs = []
    for s in SIMS[1:]:
        if min(de_ok(a, b, s) for a, b in itertools.combinations(strokes.values(), 2)) < CVD_MIN:
            a, b = st[s]["pair"]
            cvd_msgs.append(f"{a} and {b} strokes ({strokes[a]}, {strokes[b]}) differ by OKLab "
                            f"{st[s]['de_ok']:.3f} under {s} simulation, below {CVD_MIN}")
    if min(abs(grey_l(a) - grey_l(b)) for a, b in itertools.combinations(fills.values(), 2)) < GREY_MIN:
        a, b = fi["grey"]["pair"]
        cvd_msgs.append(f"{a} and {b} fills ({fills[a]}, {fills[b]}) differ by "
                        f"{fi['grey']['dl']:.3f} in greyscale lightness, below {GREY_MIN}")
    (failures if cvd == "check" else reported).extend(cvd_msgs)
    text_rows = [x["ratio"] for x in rows if x["need"] == TEXT_MIN]
    nontext_rows = [x["ratio"] for x in rows if x["need"] == NONTEXT_MIN]
    return {"contrast": rows, "strokes": st, "fills": fi, "failures": failures,
            "reported": reported, "cvd_gate": cvd,
            "eligibility_failures": list(dict.fromkeys(failures + cvd_msgs)),
            "text_min": min(text_rows), "nontext_min": min(nontext_rows),
            "text_on_fill_min": round(min(contrast(theme["roles"][r][2], theme["roles"][r][0])
                                          for r in ROLES), 2)}


# =========================================================================== public API
def _effective_gate(entry: Mapping, ov: Mapping | None) -> str:
    """The cvd policy derive_theme applies: the override's, else the entry's, else report."""
    if ov is not None and "cvd" in ov:
        return ov["cvd"]
    return entry.get("cvd", "report")


def _roles_digest(entry: Mapping, ov: Mapping | None, gate: str | None = None) -> str:
    """Cache key: everything that changes the derived theme or its checks."""
    blob = json.dumps({"roles": entry.get("roles"), "override": ov,
                       "cvd": gate if gate is not None else _effective_gate(entry, ov),
                       "mode": entry.get("mode", "auto"), "algo": ALGO_VERSION,
                       "min": [TEXT_MIN, TEXT_TARGET, NONTEXT_MIN, JND_OK, CVD_MIN, GREY_MIN]},
                      sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


def _tuples(theme: dict) -> dict:
    theme["roles"] = {k: tuple(v) for k, v in theme["roles"].items()}
    theme["edges"] = {k: tuple(v) for k, v in theme["edges"].items()}
    if "glyph_lc" in theme:
        theme["glyph_lc"] = tuple(theme["glyph_lc"])
    return theme


def _cache_dir(cfg: Any, cache_dir: str | os.PathLike | None) -> Path | None:
    if cache_dir is not None:
        return Path(cache_dir)
    try:
        from ltcore import store
        return store.Store(cfg).themes_cache_dir
    except Exception:  # an unusable config or cache folder only costs the cache
        return None


def derive_theme(palette: str | Mapping, cfg: Any = None, *, cache_dir: str | os.PathLike | None = None,
                 use_cache: bool = True) -> dict:
    """The engine theme for a palette id or entry. An explicit ``override`` table in
    the entry wins; a shipped override (palettes/overrides/<id>.toml) applies only to
    catalog entries, so a custom palette that reuses a catalog id is derived from its
    own roles. Raises PaletteError when a check fails. Results are cached under
    <cache>/themes/."""
    entry = get(palette, cfg) if isinstance(palette, str) else _validate_entry(palette, "palette")
    ov = entry.get("override")
    if ov is None and entry.get("origin") == "catalog":
        ov = load_override(entry["id"])
    gate = _effective_gate(entry, ov)
    digest = _roles_digest(entry, ov, gate)
    cdir = _cache_dir(cfg, cache_dir) if use_cache else None
    cpath = cdir / f"{entry['id']}-{digest}.json" if cdir else None
    if cpath is not None and cpath.is_file():
        try:
            return _tuples(json.loads(cpath.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    theme = _from_override(entry, ov) if ov is not None else _derive(entry)
    chk = check_theme(theme, cvd=gate)
    if chk["failures"]:
        raise PaletteError(f"palette {entry['id']}: {chk['failures'][0]}"
                           + (f" (and {len(chk['failures']) - 1} more)" if len(chk["failures"]) > 1 else ""))
    theme["checks"] = {k: chk[k] for k in ("strokes", "fills", "reported", "cvd_gate",
                                           "eligibility_failures", "text_min", "nontext_min",
                                           "text_on_fill_min")}
    theme["digest"] = digest
    if cpath is not None:
        try:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = cpath.with_suffix(".tmp")
            tmp.write_text(json.dumps(theme, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cpath)
        except OSError:
            pass
    return _tuples(json.loads(json.dumps(theme)))


def themes(cfg: Any = None, ids: Iterable[str] | None = None, **kw: Any) -> dict[str, dict]:
    """{palette_id: theme} for ``style.palettes`` (or ``ids``), in order."""
    if ids is None:
        ids = _cfg_get(cfg, "style.palettes", None) or [default_palette()]
    return {pid: derive_theme(pid, cfg, **kw) for pid in ids}


def print_theme(cfg: Any = None, **kw: Any) -> dict:
    """The theme for ``style.print_palette`` (light, for print and PDF submission)."""
    pid = _cfg_get(cfg, "style.print_palette", None) or default_print_palette()
    return derive_theme(pid, cfg, **kw)


PDF_KEYS = ("base", "mantle", "text", "sub", "over", "surface", "blue", "mauve", "peach", "red", "green",
            "yellow")
PDF_TEXT_KEYS = ("text", "sub", "over", "blue", "mauve", "peach", "red", "green")


def _reach(colour: str, grounds: list[str], need: float, light: bool) -> str | None:
    """``colour`` stepped in Oklch L (hue and chroma kept) until it reaches ``need``
    on every ground: darker on a light page, lighter on a dark one."""
    return _step_until(colour, lambda c: all(contrast(c, g) >= need - 1e-9 for g in grounds), darker=light)


def _toward(start: str, goal: str, ok, t_max: float) -> str:
    """``start`` moved toward ``goal`` by the largest t in 0.01 steps up to ``t_max``
    for which ``ok`` holds (t 0 is ``start`` itself)."""
    for i in range(round(t_max * 100), 0, -1):
        c = mix(start, goal, i / 100)
        if ok(c):
            return c
    return start


def pdf_theme(palette_id: str | Mapping, *, cfg: Any = None, **kw: Any) -> dict:
    """Colours for PDF pages, taken from the palette's diagram theme so a PDF and a
    diagram in one palette agree. Returns hex strings for the keys in PDF_KEYS plus
    ``id``, ``name`` and ``dark`` (bool):

      base     page fill (the theme's canvas)
      mantle   banded-row fill (the container fill)
      text     body text (ink); sub: secondary text (muted)
      over     faint text (footer): muted moved up to 0.2 toward the page
      surface  grid lines and table header fill: the container fill moved up to 0.25
               toward the border while text keeps 4.5:1 on it
      blue, mauve, peach, red, green: the theme's accents of those names (links and
               headings, block headings, code spans, spare)
      yellow   the warning sign (non-text, 3:1 on base)

    Every text key (PDF_TEXT_KEYS) reaches 4.5:1 on base and on mantle: an accent
    that does not is stepped in Oklch L, hue and chroma kept. Raises PaletteError like
    ``derive_theme``."""
    th = derive_theme(palette_id, cfg, **kw)
    light = not th["dark"]
    base, mantle, text, sub = th["canvas"], th["cl_fill"], th["ink"], th["muted"]
    grounds = [base, mantle]

    def readable(c: str) -> bool:
        return all(contrast(c, g) >= TEXT_MIN - 1e-9 for g in grounds)

    out: dict[str, Any] = {"id": th["id"], "name": th.get("name", th["id"]), "dark": th["dark"],
                           "base": base, "mantle": mantle}
    out["text"] = text if readable(text) else (_reach(text, grounds, TEXT_MIN, light) or text)
    out["sub"] = sub if readable(sub) else (_reach(sub, grounds, TEXT_MIN, light) or out["text"])
    out["over"] = _toward(out["sub"], base, readable, 0.2)
    out["surface"] = _toward(mantle, th["cl_stroke"],
                             lambda c: contrast(out["text"], c) >= TEXT_MIN - 1e-9, 0.25)
    acc = th.get("accents") or {}
    for key in ("blue", "mauve", "peach", "red", "green"):
        c = acc.get(key) or out["text"]
        out[key] = c if readable(c) else (_reach(c, grounds, TEXT_MIN, light) or out["text"])
    y = acc.get("yellow") or acc.get("peach") or out["text"]
    out["yellow"] = y if contrast(y, base) >= NONTEXT_MIN else (_reach(y, [base], NONTEXT_MIN, light) or out["text"])
    bad = [k for k in PDF_TEXT_KEYS if not readable(out[k])]
    if bad:
        k = bad[0]
        raise PaletteError(f"palette {th['id']}: PDF {k} colour {out[k]} reaches only "
                           f"{min(contrast(out[k], g) for g in grounds):.2f}:1 on {base} or {mantle}")
    return out


def default_palette() -> str:
    """Rank 1 of the catalog (style.defaults.toml carries the same value)."""
    return catalog_meta()["defaults"]["palettes"][0]


def default_print_palette() -> str:
    return catalog_meta()["defaults"]["print_palette"]


def _needs_probe() -> None:
    catalog()
    for p in sorted(OVERRIDES_DIR.glob("*.toml")):
        load_override(p.stem)


# =========================================================================== ranking
WEIGHTINGS = {
    # name: weights of the derived-theme components (each sums to 100). Colour-vision
    # credit for strokes takes the worse of protanopia and deuteranopia (together about
    # 8% of men), so a palette that collapses for one of them cannot buy it back with
    # the other; tritanopia (rare) has its own smaller weight.
    "balanced": {"text": 20, "nontext": 10, "stroke_normal": 10, "stroke_red_green": 22,
                 "stroke_tritan": 8, "stroke_grey": 15, "fill_cvd": 8, "fill_grey": 7},
    "cvd-first": {"text": 15, "nontext": 5, "stroke_normal": 5, "stroke_red_green": 35,
                  "stroke_tritan": 15, "stroke_grey": 10, "fill_cvd": 10, "fill_grey": 5},
    "print-first": {"text": 20, "nontext": 10, "stroke_normal": 10, "stroke_red_green": 10,
                    "stroke_tritan": 5, "stroke_grey": 30, "fill_cvd": 5, "fill_grey": 10},
    "text-first": {"text": 35, "nontext": 15, "stroke_normal": 10, "stroke_red_green": 15,
                   "stroke_tritan": 5, "stroke_grey": 10, "fill_cvd": 5, "fill_grey": 5},
}
PRIMARY = "balanced"         # the weighting that picks the defaults; the others check robustness
# full credit at: text 7:1 (AAA), non-text 4.5:1, stroke dE00 20, stroke grey dL 0.10,
# fill dE00 10 (fills are pale by design), fill grey dL 0.05
FULL = {"text": 7.0, "nontext": 4.5, "stroke_de00": 20.0, "stroke_grey": 0.10, "fill_de00": 10.0,
        "fill_grey": 0.05}


def _ramp(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def components(theme: Mapping) -> dict[str, float]:
    """0..1 credit per component, measured on the derived theme. text: body text on
    the canvas and on every role fill (half credit at 4.5:1, full at 7:1, log scale);
    nontext: the weakest stroke or arrow pair (half at 3:1, full at 4.5:1); strokes:
    worst-pair CIEDE2000 under normal vision, under the worse of protanopia and
    deuteranopia, and under tritanopia (full at 20), and
    greyscale lightness gap (full at 0.10); fills: worst-pair CIEDE2000 over the 4
    views (full at 10) and greyscale gap (full at 0.05)."""
    chk = theme.get("checks") or check_theme(theme, cvd="report")
    st, fi = chk["strokes"], chk["fills"]
    lg = math.log
    body = min(contrast(theme["ink"], theme["canvas"]), chk["text_on_fill_min"])
    return {
        "text": _ramp(lg(body), lg(TEXT_MIN), lg(FULL["text"])) * 0.5 + 0.5
        if body >= TEXT_MIN else 0.0,
        "nontext": _ramp(lg(chk["nontext_min"]), lg(NONTEXT_MIN), lg(FULL["nontext"])) * 0.5 + 0.5
        if chk["nontext_min"] >= NONTEXT_MIN else 0.0,
        "stroke_normal": _ramp(st["none"]["de00"], 0, FULL["stroke_de00"]),
        "stroke_red_green": _ramp(min(st["protan"]["de00"], st["deutan"]["de00"]), 0, FULL["stroke_de00"]),
        "stroke_tritan": _ramp(st["tritan"]["de00"], 0, FULL["stroke_de00"]),
        "stroke_grey": _ramp(st["grey"]["dl"], 0, FULL["stroke_grey"]),
        "fill_cvd": _ramp(min(fi[s]["de00"] for s in SIMS), 0, FULL["fill_de00"]),
        "fill_grey": _ramp(fi["grey"]["dl"], 0, FULL["fill_grey"]),
    }


def score(theme: Mapping, weighting: str = "balanced") -> float:
    w = WEIGHTINGS[weighting]
    c = components(theme)
    return round(math.fsum(w[k] * c[k] for k in w), 2)   # sum() rounds differently from 3.12 on


COMPOSITE_ACCENTS = ("tol-medium-contrast", "tol-bright", "tol-muted", "petroff-6",
                     "petroff-8", "petroff-10", "okabe-ito", "ibm-colourblind")
COMPOSITE_NEUTRALS = ("primer-dark-high-contrast", "modus-vivendi", "catppuccin-mocha",
                      "catppuccin-macchiato", "everforest-dark-medium")


def dark_composites(entries: Mapping[str, Mapping]) -> dict[str, dict]:
    """Dark candidates built from two catalog entries: the accents (and positive,
    negative) of each COMPOSITE_ACCENTS palette on the neutral roles (background,
    surface, text, text_muted, border) of each COMPOSITE_NEUTRALS palette. No accent
    of the dark palette is kept. Ids are ``<accents>-on-<neutrals>``; licence fields
    are not read."""
    out = {}
    neutral_keys = ("background", "surface", "text", "text_muted", "border")
    for accent_id in COMPOSITE_ACCENTS:
        for neutral_id in COMPOSITE_NEUTRALS:
            if accent_id not in entries or neutral_id not in entries:
                continue
            accent, neutral = entries[accent_id], entries[neutral_id]
            pid = f"{accent_id}-on-{neutral_id}"
            roles = {k: v for k, v in neutral["roles"].items() if k in neutral_keys}
            roles.update({k: v for k, v in accent["roles"].items()
                          if k.startswith("accent_") or k in ("positive", "negative")})
            sources = {}
            for k in roles:
                donor_id, donor = (neutral_id, neutral) if k in neutral_keys else (accent_id, accent)
                sources[k] = f"{donor_id}: {donor.get('role_sources', {}).get(k, k)}"
            out[pid] = {"id": pid, "name": f"{accent['name']} on {neutral['name']}",
                        "family": "Composite", "mode": "dark", "origin": "composite",
                        "composite_sources": [accent_id, neutral_id], "roles": roles,
                        "role_sources": sources}
    return out


def rank(entries: Mapping[str, Mapping] | None = None, *, cache_dir: Any = None) -> dict:
    """Derive and score every entry plus the dark composites (``dark_composites``).
    Licence fields are not read: the ranking is a measurement of the colours only.
    Returns {"weightings", "full_credit", "entries": [{id, mode, derivable, error,
    default_eligible, scores{weighting: x}, ...}], "order": {weighting: {"light":
    [...], "dark": [...]}}, "winners", "defaults", ...}. Each weighted order contains
    only default-eligible themes. Failed themes retain their measured scores in
    entries, without a rank position. A mode with no eligible
    theme has no winner and no default; ``defaults.palettes`` then holds the light
    winner alone."""
    entries = dict(catalog() if entries is None else entries)
    entries.update(dark_composites(entries))
    rows = []
    for pid, e in entries.items():
        row: dict[str, Any] = {"id": pid, "family": e.get("family", ""), "catalog_mode": e.get("mode")}
        if e.get("composite_sources"):
            row["composite_sources"] = list(e["composite_sources"])
        try:
            th = derive_theme(_strip_licence(e), cache_dir=cache_dir, use_cache=cache_dir is not None)
        except PaletteError as exc:
            row.update(derivable=False, error=str(exc))
            rows.append(row)
            continue
        ch = th["checks"]
        eligible = not ch["eligibility_failures"]
        why = "; ".join(ch["eligibility_failures"])
        if th["source"] == "override":
            eligible, why = False, "hand-tuned override, kept as an option"
        elif e.get("mode") == "sequential":
            eligible, why = False, "sequential ramp: its colours imply an order the roles do not have"
        row.update(derivable=True, error="", mode=th["mode"], source=th["source"],
                   default_eligible=eligible, not_eligible_because=why,
                   scores={w: score(th, w) for w in WEIGHTINGS},
                   components={k: round(v, 3) for k, v in components(th).items()},
                   text_min=ch["text_min"], nontext_min=ch["nontext_min"],
                   text_on_fill_min=ch["text_on_fill_min"],
                   stroke_de_ok={s: ch["strokes"][s]["de_ok"] for s in SIMS},
                   stroke_de00={s: ch["strokes"][s]["de00"] for s in SIMS},
                   stroke_grey_dl=ch["strokes"]["grey"]["dl"],
                   fill_de00={s: ch["fills"][s]["de00"] for s in SIMS},
                   fill_grey_dl=ch["fills"]["grey"]["dl"],
                   role_accents=(th.get("derivation") or {}).get("role_accents"),
                   reported=ch["reported"])
        rows.append(row)
    order, winners = {}, {}
    for w in WEIGHTINGS:
        order[w], winners[w] = {}, {}
        for mode in ("light", "dark"):
            ok = [r for r in rows if r["derivable"] and r["mode"] == mode and r["default_eligible"]]
            ok.sort(key=lambda r: (-r["scores"][w], r["id"]))
            order[w][mode] = [r["id"] for r in ok]
            winners[w][mode] = ok[0]["id"] if ok else None
    eligible = {mode: sum(1 for r in rows if r.get("mode") == mode and r.get("default_eligible"))
                for mode in ("light", "dark")}
    return {"weightings": WEIGHTINGS, "primary_weighting": PRIMARY, "full_credit": FULL,
            "winners": winners,
            "defaults": {"palettes": [pid for pid in (winners[PRIMARY]["light"], winners[PRIMARY]["dark"])
                                      if pid],
                         "print_palette": winners[PRIMARY]["light"]},
            "thresholds":
            {"text": TEXT_MIN, "text_target": TEXT_TARGET, "nontext": NONTEXT_MIN,
             "jnd_de_ok": JND_OK, "cvd_collapse_de_ok": CVD_MIN, "grey_dl": GREY_MIN},
            "eligible": eligible,
            "catalog_dark_eligible": sum(1 for r in rows if r.get("mode") == "dark"
                                         and not r.get("composite_sources") and r.get("default_eligible")),
            "entries": rows, "order": order}


def apply_ranking(doc: Mapping, ranking: Mapping) -> dict:
    """A copy of a catalog document (catalog.json as loaded) with each entry's
    ``rank`` block and the ``defaults`` block taken from ``rank()``'s result."""
    out = copy.deepcopy(dict(doc))
    prim = ranking["primary_weighting"]
    rows = {r["id"]: r for r in ranking["entries"]}
    for e in out["palettes"]:
        r = rows[e["id"]]
        if not r["derivable"]:
            e["rank"] = {"derivable": False, "reason": r["error"]}
            continue
        order = ranking["order"][prim][r["mode"]]
        rk = {"derivable": True, "mode": r["mode"], "of": len(order),
              "score": r["scores"][prim], "weighting": prim, "default_eligible": r["default_eligible"]}
        if r["default_eligible"]:
            rk["position"] = order.index(e["id"]) + 1
        else:
            rk["not_default_because"] = r["not_eligible_because"]
        e["rank"] = rk
    out["defaults"] = copy.deepcopy(ranking["defaults"])
    return out


def _strip_licence(e: Mapping) -> dict:
    """The entry without licence or attribution fields (the ranking never sees them)."""
    return {k: v for k, v in e.items() if k not in ("licence", "attribution", "ships", "rank", "score")}


# =========================================================================== CLI
def _load_cfg(path: str | None):
    from ltcore import config
    return config.load(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.palettes", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="catalog and custom palettes with rank and licence")
    p.add_argument("--config")
    p = sub.add_parser("show", help="print the derived theme of one palette")
    p.add_argument("id")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check", help="derive and check palettes (default: style.palettes)")
    p.add_argument("ids", nargs="*")
    p.add_argument("--config")
    p.add_argument("--all", action="store_true", help="every catalog and custom palette")
    p = sub.add_parser("rank", help="rank the catalog by measurement of the derived themes")
    p.add_argument("--write", help="write the ranking JSON here")
    p.add_argument("--update-catalog", action="store_true",
                   help="maintainers: rewrite the rank and defaults blocks of palettes/catalog.json")
    a = ap.parse_args(argv)
    try:
        cfg = _load_cfg(getattr(a, "config", None)) if a.cmd != "rank" else None
    except Exception as exc:  # ConfigError
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        if a.cmd == "list":
            for pid, e in available(cfg).items():
                lic = (e.get("licence") or {}).get("spdx") or (e.get("licence") or {}).get("ref") or "custom"
                rk = e.get("rank") or {}
                print(f"{pid:32s} {e.get('mode', ''):17s} rank {rk.get('position', '-')!s:>3}  {lic}")
            return 0
        if a.cmd == "show":
            th = derive_theme(a.id, cfg)
            if a.json:
                print(json.dumps(th, indent=1, ensure_ascii=False))
            else:
                print(f"{th['id']} ({th['mode']}, {th['source']})")
                for k in ("canvas", "cl_fill", "cl_stroke", "ink", "muted", "label"):
                    print(f"  {k:10s} {th[k]}")
                for r in ROLES:
                    f, s, t = th["roles"][r]
                    print(f"  {r:10s} fill {f} stroke {s} text {t}  "
                          f"text/fill {contrast(t, f):.2f}  stroke/fill {contrast(s, f):.2f}")
                for k, (c, d, h) in th["edges"].items():
                    print(f"  edge {k:5s} {c} dash={d} head={h}")
                for msg in th["checks"]["reported"]:
                    print(f"  reported: {msg}")
            return 0
        if a.cmd == "check":
            ids = list(available(cfg)) if a.all else (a.ids or _cfg_get(cfg, "style.palettes", []))
            bad = 0
            for pid in ids:
                try:
                    th = derive_theme(pid, cfg)
                    print(f"OK   {pid}: text {th['checks']['text_min']:.2f}, non-text "
                          f"{th['checks']['nontext_min']:.2f}, strokes worst OKLab "
                          f"{min(th['checks']['strokes'][s]['de_ok'] for s in SIMS):.3f}")
                except PaletteError as exc:
                    bad += 1
                    print(f"FAIL {exc}")
            return 1 if bad else 0
        if a.cmd == "rank":
            res = rank()
            text = json.dumps(res, indent=1, ensure_ascii=False) + "\n"
            if a.update_catalog:
                doc = apply_ranking(_read_json(CATALOG_FILE), res)
                CATALOG_FILE.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                print(f"wrote {CATALOG_FILE}")
            if a.write:
                Path(a.write).write_text(text, encoding="utf-8")
                print(f"wrote {a.write}")
            if not (a.write or a.update_catalog):
                for w, modes in res["order"].items():
                    print(f"{w}: light {', '.join(modes['light'][:5])}; dark {', '.join(modes['dark'][:3])}")
            return 0
    except PaletteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
