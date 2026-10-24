# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Palettes, faces and the role vocabulary for render.py, drawio.py and the checks.

A theme is built at run time for every palette id in the config's style.palettes: ltcore.palettes
derives the role fills, strokes and text (in Oklch at each role's hue, checked for WCAG contrast,
colour-vision separation and greyscale separation), and this module adds what the engine needs on
top: the configured sans and mono family names, the key panel colours, the edge styles (colour, dash,
head) and named accents for the glyph layers.

    THEMES[pid]              the engine theme of one palette (derived on first use, then cached)
    final_palettes()         style.palettes, print palette included: every final export is made in each
    print_palette()          style.print_palette: the submission copy, whose files carry no suffix
    out_name(stem, pid, ext) "<stem>.<ext>" for the print palette, "<stem>-<pid>.<ext>" for the others

ROLE_NAME is the key's wording for each role; a case may override it with ROLE_NAME in content.py.
"""
import math

import style

ROLE_NAME = {"risk": "Risk factor", "mech": "Mechanism", "anat": "Normal anatomy or physiology", "sign": "Sign or symptom",
             "inv": "Investigation", "dx": "Diagnosis", "tx": "Treatment"}
ROLES = ("risk", "mech", "sign", "inv", "dx", "tx", "anat")     # the roles every theme defines
EDGE_KINDS = ("stim", "inh", "excl", "req")                      # the connector kinds every theme defines

EXCL_DASH = "1.6 3.4"     # "evidence argues against it": a dotted line (round caps), so it never reads as "blocks"
# connector kind -> (dash, head): solid arrow, solid T-bar, dotted T-bar, dashed arrow ("depends on")
EDGE_STYLE = {"stim": (False, "arrow"), "inh": (False, "tee"), "excl": (EXCL_DASH, "tee"), "req": (True, "arrow")}

# Named accents for the glyph layers (flags, abnormal-value marks, opt-in stage glyph colours). A palette
# that names its accents keeps them; otherwise each name is drawn at its Oklch hue with the lightness and
# chroma of the palette's own role strokes, so it reads like the strokes on that palette's ground.
ACCENT_HUE = {"rosewater": 33, "flamingo": 21, "pink": 338, "mauve": 297, "red": 20, "maroon": 20, "peach": 42,
              "yellow": 68, "green": 140, "teal": 201, "sky": 235, "sapphire": 213, "blue": 262, "lavender": 273}
ACCENT_NAMES = tuple(ACCENT_HUE)

# one glyph per role (Material Design Icons names, icons.py); a case may override them in case.json "role_icons"
ROLE_ICON = {"risk": "dice_5", "mech": "cog", "anat": "human", "sign": "stethoscope", "inv": "test_tube",
             "dx": "clipboard_pulse", "tx": "medical_bag"}
# flags a box may carry (content.py: n(..., flags=("pitfall",))): icon, accent name, the key's wording.
# A flag's icon is drawn in whichever of the palette's ink and ground contrasts more with its disc
# (decor.flag_icon_colour). Pitfall is yellow so it never sits beside urgent's red as one colour.
FLAGS = {
    "urgent":     ("alarm_light", "red", "Urgent: a time-critical red flag"),
    "pitfall":    ("alert", "yellow", "Pitfall: a common trap"),
    "high_yield": ("star", "lavender", "High-yield: a key concept"),
}
FLAG_ORDER = ("urgent", "pitfall", "high_yield")
LAB_ACCENT = {"high": "red", "low": "blue"}      # the arrow after an abnormal value, and its range-bar dot
# the marks take the role strokes unless the palette names its own accents: high the sign stroke, low the
# investigation stroke (decor.lab_colour); a raw accent can fall under 3:1 on the investigation fill
LAB_ROLE_DARK = {"high": "sign", "low": "inv"}
LAB_MARK = {"high": "↑", "low": "↓"}


# ------------------------------------------------------------------ colour maths
def _hex(c):
    c = str(c).strip()
    if not c.startswith("#"):
        c = "#" + c
    if len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c.lower()


def is_dark(T):
    """A theme on a dark ground."""
    if "dark" in T and T["dark"] is not None:
        return bool(T["dark"])
    return luminance(T["canvas"]) < 0.18


def luminance(hexs):
    """WCAG relative luminance of #rrggbb."""
    c = [int(hexs[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def srgb_to_oklch(hexs):
    r, g, b = (int(hexs[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    l = 0.4122214708 * lin[0] + 0.5363325363 * lin[1] + 0.0514459929 * lin[2]
    m = 0.2119034982 * lin[0] + 0.6806995451 * lin[1] + 0.1073969566 * lin[2]
    s = 0.0883024619 * lin[0] + 0.2817188376 * lin[1] + 0.6299787005 * lin[2]
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def oklch_to_hex(L, C, h):
    for _ in range(60):          # reduce chroma until the colour is inside sRGB
        a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
        l_ = L + 0.3963377774 * a + 0.2158037573 * b
        m_ = L - 0.1055613458 * a - 0.0638541728 * b
        s_ = L - 0.0894841775 * a - 1.2914855480 * b
        l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
        rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
               -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
               -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
        if all(-1e-4 <= c <= 1 + 1e-4 for c in rgb):
            break
        C *= 0.95
    out = []
    for c in rgb:
        c = min(1.0, max(0.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        out.append(round(c * 255))
    return "#" + "".join(f"{v:02x}" for v in out)


def mix(c1, c2, t):
    """c1 mixed toward c2 by t (0..1), in sRGB: solid colours for the range bars (no opacity in the PDF or draw.io)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# ------------------------------------------------------------------ building a theme
def _edge(kind, v, roles, fallback):
    """One connector style (colour, dash, head) from what the palette gives."""
    dash, head = EDGE_STYLE[kind]
    if isinstance(v, (list, tuple)):
        col = v[0]
        if len(v) >= 3:
            dash, head = v[1], v[2]
    elif v:
        col = v
    else:
        col = fallback
    return (_hex(col), dash, head)


def build(pid, raw, sans, mono):
    """The engine theme of one palette from ltcore.palettes.derive_theme()'s output."""
    roles = {}
    for r in ROLES:
        f, s, t = (list(raw["roles"][r]) + [raw.get("ink")])[:3]
        roles[r] = (_hex(f), _hex(s), _hex(t or raw["ink"]))
    ink = _hex(raw.get("ink") or raw.get("text"))
    muted = _hex(raw.get("muted") or raw.get("text_muted") or ink)
    cl_stroke = _hex(raw.get("cl_stroke") or raw.get("border"))
    edges_raw = raw.get("edges") or {}
    edges = {"stim": _edge("stim", edges_raw.get("stim"), roles, muted),
             "inh": _edge("inh", edges_raw.get("inh"), roles, roles["tx"][1]),
             "excl": _edge("excl", edges_raw.get("excl"), roles, muted),
             "req": _edge("req", edges_raw.get("req"), roles, roles["anat"][1])}
    T = {
        "flavour": pid, "sans": sans, "mono": mono,
        "canvas": _hex(raw["canvas"]), "cl_fill": _hex(raw.get("cl_fill") or raw.get("surface")),
        "cl_stroke": cl_stroke, "cl_text": _hex(raw.get("cl_text") or ink),
        "key_fill": _hex(raw.get("key_fill") or raw["canvas"]), "key_stroke": _hex(raw.get("key_stroke") or cl_stroke),
        "ink": ink, "muted": muted, "label": _hex(raw.get("label") or muted),
        "roles": roles, "edges": edges,
        "dark": raw.get("dark"),
        "name": raw.get("name") or raw.get("label_name"),
    }
    acc = raw.get("accents")
    T["accents"] = {k: _hex(v) for k, v in acc.items() if k in ACCENT_HUE} if isinstance(acc, dict) else {}
    Ls, Cs = zip(*(srgb_to_oklch(roles[r][1])[:2] for r in ROLES if r != "anat"))
    T["glyph_lc"] = (round(sum(Ls) / len(Ls), 3), round(sum(Cs) / len(Cs), 3))
    if T["dark"] is None:
        T["dark"] = is_dark(T)
    return T


class _Themes(dict):
    """palette id -> engine theme, derived on first use."""

    def __missing__(self, pid):
        sans, mono = style.font_family("sans"), style.font_family("mono")
        T = build(pid, style.raw_theme(pid), sans, mono)
        self[pid] = T
        return T


THEMES = _Themes()


def final_palettes():
    """Every palette a final export is made in (style.palettes), the print palette included."""
    return style.palette_ids()


def print_palette():
    return style.print_palette()


def label(pid):
    return style.palette_name(pid, THEMES[pid])


def out_name(stem, pid, ext):
    """A deliverable's file name: no suffix for the print palette, -<pid> for the others."""
    return f"{stem}.{ext}" if pid == print_palette() else f"{stem}-{pid}.{ext}"


def reset():
    """Forget derived themes (after style.configure() loads another config)."""
    THEMES.clear()


# ------------------------------------------------------------------ glyph accents
def _theme(theme):
    return theme if isinstance(theme, dict) else THEMES[theme]


def accent(name, theme):
    """A named accent of the theme (flags and lab markers); "#rrggbb" passes through."""
    if name.startswith("#"):
        return name
    T = _theme(theme)
    if name in T.get("accents", {}):
        return T["accents"][name]
    if name not in ACCENT_HUE:
        raise ValueError(f"no accent {name!r}: use one of {', '.join(ACCENT_NAMES)} or #rrggbb")
    L, C = T["glyph_lc"]
    return oklch_to_hex(L, C, ACCENT_HUE[name])


def glyph_colour(name, theme):
    """A stage glyph's colour: the accent's hue at the role strokes' lightness and chroma for the palette.
    An accent may also be a literal "#rrggbb"."""
    if name.startswith("#"):
        return name
    T = _theme(theme)
    if name not in ACCENT_HUE:
        raise ValueError(f"no accent {name!r}: use one of {', '.join(ACCENT_NAMES)} or #rrggbb")
    hue = srgb_to_oklch(T["accents"][name])[2] if name in T.get("accents", {}) else ACCENT_HUE[name]
    L, C = T["glyph_lc"]
    return oklch_to_hex(L, C, hue)


def has_named_accents(theme):
    return bool(_theme(theme).get("accents"))
