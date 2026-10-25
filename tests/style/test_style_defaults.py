# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""shared/style.defaults.toml: valid against the config schema, merged by
ltcore.config, and every value a real, resolvable choice (no placeholder).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ltcore import config, fonts, palettes

REPO = Path(__file__).resolve().parents[2]
STYLE = REPO / "shared" / "style.defaults.toml"


def test_style_defaults_load_through_config(tmp_path):
    cfg = config.load(cwd=tmp_path)
    assert cfg.where("style.palettes") == "style default"
    assert cfg.where("fonts.sans") == "style default"
    doc = tomllib.loads(STYLE.read_text(encoding="utf-8"))
    assert set(doc) == {"style", "fonts"}
    assert cfg.get("style.palettes") == doc["style"]["palettes"]
    assert cfg.errors == []


def test_style_default_values_resolve(tmp_path):
    cfg = config.load(cwd=tmp_path)
    for pid in cfg.get("style.palettes") + [cfg.get("style.print_palette")]:
        th = palettes.derive_theme(pid, cfg, cache_dir=tmp_path / "themes")
        assert th["checks"]["text_min"] >= palettes.TEXT_MIN
    assert palettes.derive_theme(cfg.get("style.print_palette"), cfg, use_cache=False)["mode"] == "light"
    for role in fonts.ROLES:
        f = fonts.resolve(role, cfg, data_dir=tmp_path)
        assert f.source == "bundled" and not f.fallback
    meta = fonts.catalog_meta()["defaults"]
    assert cfg.get("fonts.sans") == fonts.catalog()[meta["sans"]]["family"]
    assert cfg.get("fonts.mono") == fonts.catalog()[meta["mono"]]["family"]


def test_no_placeholder_in_shipped_style_files():
    paths = [STYLE, *sorted((REPO / "shared" / "palettes").rglob("*")),
             *sorted((REPO / "shared" / "fonts").rglob("*"))]
    for p in paths:
        if p.is_file() and p.suffix in (".toml", ".json", ".md", ".txt"):
            assert not re.search(r"\bTBD\b", p.read_text(encoding="utf-8")), p


def test_user_config_overrides_style_defaults(tmp_path):
    f = tmp_path / "lt-suite.toml"
    f.write_text('[style]\npalettes = ["catppuccin-latte"]\n[fonts]\nsans = "Atkinson Hyperlegible Next"\n',
                 encoding="utf-8", newline="\n")
    cfg = config.load(f)
    assert cfg.get("style.palettes") == ["catppuccin-latte"]
    assert cfg.get("style.print_palette") == palettes.default_print_palette()
    assert cfg.where("fonts.sans").startswith("config")
