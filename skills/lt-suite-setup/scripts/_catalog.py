# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Palette and font catalog adapters for the setup skill.

setup.py (doctor, fetch-font) and preview.py (palette and font previews) read the
catalogs only through this module, which calls ltcore.palettes and ltcore.fonts. A
change in those modules touches this file alone.

Every function returns plain dicts and tuples. Nothing here draws or downloads.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The palettes and fonts the setup flow names as options next to the measured top
# of the ranking. They stay selectable catalog entries; none is a default.
ORIGINAL_PALETTES = ("catppuccin-latte", "catppuccin-frappe", "everforest-dark-medium")
ORIGINAL_FONTS = ("adwaita-sans", "adwaita-mono")
LOW_VISION_FONTS = ("atkinson-hyperlegible-next", "atkinson-hyperlegible-mono")

SAMPLE_TEXT = "Ca²⁺ IL-1β → ≤ ≥  Il1 O0  The quick brown fox"


def shared_dir():
    """The folder that holds ltcore/: scripts/_shared in an installed skill, shared/ in a
    source checkout."""
    for base in (HERE / "_shared", HERE.parent.parent.parent / "shared"):
        if (base / "ltcore").is_dir():
            return base
    return None


def _ensure_path():
    base = shared_dir()
    if base is not None and str(base) not in sys.path:
        sys.path.insert(0, str(base))


def palettes_module():
    _ensure_path()
    try:
        from ltcore import palettes
    except ImportError:
        return None
    return palettes


def fonts_module():
    _ensure_path()
    try:
        from ltcore import fonts
    except ImportError:
        return None
    return fonts


# ------------------------------------------------------------------------ palettes

def _licence(entry):
    lic = entry.get("licence")
    if isinstance(lic, dict):
        return lic.get("spdx") or lic.get("ref") or ""
    return str(lic or "")


def palette_choices(cfg=None, top=4, also=ORIGINAL_PALETTES, ids=None, mode=None):
    """Rows for the palette preview, best first.

    Each row: id, name, family, mode, rank (position inside its light or dark group, or
    None), rank_of (group size), licence, origin, default_eligible, theme (derived theme
    dict or None), error. ``ids`` picks exactly those palettes. Otherwise the top ``top``
    default-eligible palettes of the light group and of the dark group come first (``mode``
    limits it to one group), then the ``also`` options and any custom palettes that are
    not in the top already. Rank positions are counted inside each group, as the catalog
    stores them.
    """
    pal = palettes_module()
    if pal is None:
        raise RuntimeError("ltcore.palettes is missing from this install")
    entries = pal.available(cfg)

    def rank(e):
        rk = e.get("rank")
        return rk if isinstance(rk, dict) else {}

    def row(pid):
        e = entries[pid]
        rk = rank(e)
        r = {"id": pid, "name": e.get("name", pid), "family": e.get("family", ""),
             "mode": rk.get("mode") or e.get("mode", ""),
             "rank": rk.get("position") if isinstance(rk.get("position"), int) else None,
             "rank_of": rk.get("of"), "licence": _licence(e),
             "origin": e.get("origin", "catalog"), "default_eligible": rk.get("default_eligible", True),
             "theme": None, "error": ""}
        try:
            r["theme"] = pal.derive_theme(pid, cfg)
            r["mode"] = r["theme"].get("mode", r["mode"])
        except pal.PaletteError as exc:
            r["error"] = str(exc)
        return r

    if ids:
        missing = [i for i in ids if i not in entries]
        if missing:
            raise ValueError("unknown palette id(s): " + ", ".join(missing))
        return [row(i) for i in ids]
    rows, seen = [], set()
    for group in ((mode,) if mode else ("light", "dark")):
        cands = sorted((k for k, e in entries.items()
                        if rank(e).get("derivable") and rank(e).get("mode") == group
                        and rank(e).get("default_eligible", True)),
                       key=lambda k: (rank(entries[k]).get("position", 10 ** 6), k))
        for pid in cands[:top]:
            rows.append(row(pid))
            seen.add(pid)
    custom = [k for k, e in entries.items() if e.get("origin", "catalog") != "catalog"]
    for pid in list(also) + custom:
        if pid in entries and pid not in seen:
            r = row(pid)
            if not mode or r["mode"] == mode:
                rows.append(r)
                seen.add(pid)
    return rows


def check_palettes(cfg=None):
    """Doctor lines for the palettes the config asks for: [(status, text)]."""
    pal = palettes_module()
    if pal is None:
        return [("FAIL", "ltcore.palettes is missing from this install; reinstall the skill")]
    try:
        ids = list((cfg.get("style.palettes", []) if cfg is not None else []) or [])
        print_id = (cfg.get("style.print_palette", "") if cfg is not None else "") or ""
        if not ids:
            ids = [pal.default_palette()]
        if not print_id:
            print_id = pal.default_print_palette()
    except Exception as exc:
        return [("FAIL", "no default palette is defined: %s" % exc)]
    out = []
    for pid in list(dict.fromkeys(ids + [print_id])):
        try:
            th = pal.derive_theme(pid, cfg)
        except pal.PaletteError as exc:
            out.append(("FAIL", "%s: %s. Choose another with: setup.py set style.palettes "
                        "'[\"<id>\"]'" % (pid, exc)))
            continue
        except Exception as exc:
            out.append(("FAIL", "%s: cannot be derived: %s" % (pid, exc)))
            continue
        chk = th.get("checks", {})
        role = "print palette" if pid == print_id else "export palette"
        out.append(("PASS", "%s (%s, %s): text %.1f:1 minimum, non-text %.1f:1 minimum"
                    % (pid, role, th.get("mode", "?"), chk.get("text_min", 0), chk.get("nontext_min", 0))))
    return out


# --------------------------------------------------------------------------- fonts

def _font_role(entry):
    return entry.get("role", "")


def font_choices(cfg=None, data_dir=None, top=6, ids=None):
    """Rows for the font preview: dict(id, family, role, licence, state, path, note, packages).

    The configured (or default) sans and mono come first, then the low-vision options,
    the Adwaita options, and the rest by how widely the family is packaged and by
    measured I/l/1 separation. Only sans and mono fonts are listed. ``state`` is
    bundled, fetched, installed, downloadable or unavailable; ``path`` is a file that a
    sample can be drawn from, or None."""
    fonts = fonts_module()
    if fonts is None:
        raise RuntimeError("ltcore.fonts is missing from this install")
    cat = fonts.catalog()
    meta = fonts.catalog_meta().get("defaults", {})
    if ids:
        missing = [i for i in ids if i not in cat]
        if missing:
            raise ValueError("unknown font id(s): " + ", ".join(missing))
        chosen = [cat[i] for i in ids]
    else:
        wanted = set()
        if cfg is not None:
            for role in ("sans", "mono"):
                v = str(cfg.get("fonts." + role, "") or "").strip().lower()
                if v:
                    wanted.add(v)
        for role in ("sans", "mono"):
            if meta.get(role):
                wanted.add(meta[role])
        low = set(LOW_VISION_FONTS) | {v for v in (meta.get("low_vision") or {}).values() if isinstance(v, str) and v in cat}

        def key(e):
            is_default = e["id"] in wanted or e["family"].lower() in wanted
            return (not is_default, e["id"] not in low, e["id"] not in ORIGINAL_FONTS,
                    -(e.get("package_score") or 0),
                    -((e.get("measured") or {}).get("il1_min_distance") or 0), e["id"])
        chosen = []
        for role in ("sans", "mono"):
            group = sorted((e for e in cat.values() if _font_role(e) == role), key=key)
            chosen.extend(group[:top])
        for fid in list(ORIGINAL_FONTS) + list(LOW_VISION_FONTS):
            if fid in cat and cat[fid] not in chosen:
                chosen.append(cat[fid])
    rows = []
    for e in chosen:
        state = fonts.state(e, cfg, data_dir)
        path = None
        if state in ("bundled", "fetched", "installed") and _font_role(e) in ("sans", "mono"):
            try:
                path = fonts.resolve(_font_role(e), cfg=cfg, family=e["family"], data_dir=data_dir,
                                     fallback=False)
            except Exception:
                path = None
        note = ""
        if state == "downloadable":
            note = "fetch with: setup.py fetch-font %s --yes" % e["id"]
        elif state == "unavailable":
            note = "install from your package manager: %s" % (e.get("packages") or "see upstream")
        lic = e.get("licence") or {}
        rows.append({"id": e["id"], "family": e["family"], "role": _font_role(e),
                     "licence": (lic.get("spdx") if isinstance(lic, dict) else str(lic)) or "unknown",
                     "state": state, "path": path, "note": note, "packages": e.get("packages", "")})
    return rows


def fetch_plan(font_id):
    """What fetch-font would download: (entry, [(url, sha256, file)], licence label), or
    None when the id is unknown. Entries without a pinned download return an empty list."""
    fonts = fonts_module()
    if fonts is None:
        raise RuntimeError("ltcore.fonts is missing from this install")
    entry = fonts.catalog().get(font_id) or fonts.find_entry(font_id)
    if entry is None:
        return None
    spec = entry.get("fetch") or {}
    items = list(spec.get("files", [])) + ([spec["licence"]] if spec.get("licence") else [])
    lic = entry.get("licence") or {}
    return entry, [(i["url"], i["sha256"], i["file"]) for i in items], \
        (lic.get("spdx") if isinstance(lic, dict) else str(lic)) or "unknown"


def check_fonts(cfg=None):
    """Doctor lines for the sans and mono fonts: [(status, text)]."""
    fonts = fonts_module()
    if fonts is None:
        return [("FAIL", "ltcore.fonts is missing from this install; reinstall the skill")]
    out = []
    for output, roles in (("pdf", ("sans", "mono")), ("diagram", ("sans", "mono")), ("labels", ("sans",))):
        for role in roles:
            title = f"{output} {role}"
            try:
                if output == "labels" and cfg and cfg.get("annotate.font_path"):
                    try:
                        f = fonts.find(role, cfg, family=cfg.get("annotate.font_path"), output="labels", os_fallback=True)
                    except fonts.FontError as exc:
                        out.append(("WARN", f"labels sans: annotate.font_path: {exc}; continuing the label chain"))
                        f = fonts.find(role, cfg, output="labels", os_fallback=True)
                else:
                    f = fonts.find(role, cfg, weight=400, output=output, os_fallback=output != "diagram")
                status = "WARN" if f.fallback or f.os_fallback else "PASS"
                out.append((status, f"{title}: {f.family} from {f.source}"))
                if f.variable and fonts.licence_class(f) != "modify":
                    out.append(("WARN", f"{title}: variable font used as is; default weight may be used"))
                if output == "diagram" and cfg and cfg.get("fonts.page_fonts") == "google":
                    entry = fonts.find_entry(f.family) or {}
                    urls = [v.get("url", "") for v in (entry.get("fetch") or {}).get("files", [])]
                    if not any("google/fonts" in u for u in urls):
                        out.append(("WARN", f"{title}: {f.family} is not Google-hosted; use fonts.page_fonts = local"))
            except Exception as exc:
                out.append(("FAIL", f"{title} font does not resolve: {exc}"))
    if cfg and cfg.get("annotate.font_path"):
        out.append(("WARN", "annotate.font_path wins over fonts.labels.sans; fonts.labels.sans also takes a family name"))
    return out
