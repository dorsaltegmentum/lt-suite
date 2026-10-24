# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Config, palettes, fonts and cleaners for the diagram engine: the one module that calls ltcore.

    style.configure("path/to/config.toml")   # or None: LT_SUITE_CONFIG, ./lt-suite.toml, the platform file
    style.get("diagram.page.max_w")
    style.palette_ids(), style.print_palette(), style.raw_theme(pid), style.palette_name(pid)
    style.font_path("sans", 500), style.font_family("mono")
    style.cache_dir("font-cache"), style.cleaners("diagram"), style.run_cleaner(entry, {"in": ..., "out": ...})

Every entry script calls configure() with its --config flag. A module that needs a value before
that loads the config the usual way.
"""
from pathlib import Path

import _bootstrap  # noqa: F401  (puts ltcore on sys.path)

_CFG = None


class StyleError(Exception):
    """A config, palette or font problem; the message says what to fix."""


def configure(path=None, cfg=None):
    """Load the lt-suite config (or take a loaded one). Returns it."""
    global _CFG
    if cfg is not None:
        _CFG = cfg
        return _CFG
    from ltcore import config as _config
    try:
        _CFG = _config.load(path)
    except _config.ConfigError as ex:
        raise StyleError(f"config: {ex}") from ex
    return _CFG


def cfg():
    return _CFG if _CFG is not None else configure(None)


def config_path():
    """The config file in use, or None (built-in defaults only)."""
    loc = getattr(cfg(), "location", None)
    p = getattr(loc, "path", None)
    return Path(p) if p else None


def get(key, default=None):
    """A config value; default when the key is unknown or unset ("" or [])."""
    try:
        v = cfg().get(key)
    except (KeyError, AttributeError):
        return default
    if v is None or v == "" or v == []:
        return default
    return v


def where(key):
    try:
        return cfg().where(key)
    except Exception:  # noqa: BLE001  (a message helper must not fail)
        return "default"


# ------------------------------------------------------------------ folders
def store():
    from ltcore import store as _store
    return _store.Store(cfg())


def cache_dir(*parts):
    """A folder under the lt-suite cache folder, created."""
    st = store()
    base = Path(st.cache_dir)
    d = base.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir():
    return Path(store().data_dir)


# ------------------------------------------------------------------ palettes
def _palettes():
    try:
        from ltcore import palettes
    except ImportError as ex:
        raise StyleError("ltcore.palettes is missing from the shared core (scripts/_shared/ltcore/palettes.py); "
                         "reinstall the skill or run tools/sync_shared.py") from ex
    return palettes


def palette_ids():
    """The palettes every final export is made in (style.palettes), print palette included."""
    ids = [str(p) for p in (get("style.palettes") or [])]
    pp = get("style.print_palette")
    if pp and pp not in ids:
        ids.insert(0, pp)
    if not ids:
        raise StyleError("style.palettes is empty: set at least one palette id (run the lt-suite-setup skill)")
    return tuple(dict.fromkeys(ids))


def print_palette():
    """The palette of the submission copy (style.print_palette); the first export palette when unset."""
    return get("style.print_palette") or palette_ids()[0]


def catalog_entry(pid):
    """A palette's catalog (or style.custom_palettes_file) entry, or None."""
    return _palettes().load_catalog(cfg=cfg()).get(pid)


def raw_theme(pid):
    """ltcore.palettes.derive_theme() for one palette id (cached under the lt-suite cache folder)."""
    pal = _palettes()
    try:
        return pal.derive_theme(pid, cfg=cfg())
    except pal.PaletteError as ex:
        raise StyleError(f"palette {pid!r} cannot be used ({where('style.palettes')}): {ex}") from ex


def palette_name(pid, theme=None):
    """A palette's display name for the interactive page's switch."""
    if theme and theme.get("name"):
        return str(theme["name"])
    try:
        entry = catalog_entry(pid)
    except StyleError:
        entry = None
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    return pid.replace("-", " ").title()


# ------------------------------------------------------------------ fonts
_FONT_KEYS = {"sans": ("fonts.diagram.sans", "fonts.sans_path"),
              "mono": ("fonts.diagram.mono", "fonts.mono_path"),
              "mono_bold": ("fonts.diagram.mono", "fonts.mono_bold_path")}


def _fonts():
    try:
        from ltcore import fonts
    except ImportError as ex:
        raise StyleError("ltcore.fonts is missing from the shared core (scripts/_shared/ltcore/fonts.py); "
                         "reinstall the skill or run tools/sync_shared.py") from ex
    return fonts


def font_path(role, weight=400, italic=False):
    """The font file for a role ("sans", "mono", "mono_bold") at a weight, through ltcore.fonts (an explicit
    fonts.*_path, the bundled defaults, fonts the setup skill fetched, then installed fonts). A variable
    file comes back variable; the engine cuts its own static instances (fontset.py)."""
    fonts = _fonts()
    try:
        return Path(fonts.resolve(role, weight=weight, italic=italic, cfg=cfg(), output="diagram"))
    except fonts.FontError as ex:
        fam_key, path_key = _FONT_KEYS[role]
        raise StyleError(f"no font file for {role} (weight {weight}{', italic' if italic else ''}): {ex}. "
                         f"Set {fam_key} or {path_key}, or run the lt-suite-setup skill") from ex


def font_family(role):
    """The family name a role's text is set in (the name the SVG and the .drawio carry)."""
    fonts = _fonts()
    try:
        return str(fonts.family("mono" if role == "mono_bold" else role, cfg=cfg(), output="diagram"))
    except fonts.FontError as ex:
        raise StyleError(f"no font for {role}: {ex}") from ex


def family_of(path):
    """The typographic family name in a font file's name table."""
    from fontTools.ttLib import TTFont
    t = TTFont(str(path), lazy=True)
    name = t["name"]
    for nid in (16, 1):
        v = name.getDebugName(nid)
        if v:
            return v
    return Path(path).stem


def text_px():
    return int(get("fonts.text_px", 14))


def page_fonts():
    """'local' embeds the fonts in the .drawio and the interactive page; 'google' links Google Fonts."""
    v = get("fonts.page_fonts", "local")
    return v if v in ("local", "google") else "local"


# ------------------------------------------------------------------ cleaners
def cleaners(stage):
    """The [[text_cleaners]] entries for one stage, in config order."""
    from ltcore import cleaners as _cl
    return _cl.cleaners_for(stage, cfg=cfg())


def run_cleaner(entry, mapping):
    """Run one [[text_cleaners]] entry on the caller's files: argv list only, no shell, placeholders {in} {out}
    {pdf}. Raises ltcore.cleaners.CleanerError on a failure."""
    from ltcore import cleaners as _cl
    return _cl.run_one(entry, mapping, timeout_s=int(entry.get("timeout_s", 60)))
