# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.palettes: theme derivation, the contrast gate, colour-vision reporting,
custom palettes and the theme cache.
"""
from __future__ import annotations

import json
import re

import pytest

from ltcore import palettes as P

CATALOG = P.catalog()
HEX = r"#[0-9a-f]{6}"


@pytest.mark.parametrize("pid", sorted(CATALOG))
def test_every_catalog_palette_derives_or_names_the_failure(pid):
    """Each entry gives a theme that passes the gate, or a PaletteError naming the
    colour pair with its measured value, or the accent count that is too small."""
    try:
        th = P.derive_theme(pid, use_cache=False)
    except P.PaletteError as exc:
        msg = str(exc)
        pair = re.search(rf"{HEX}.*{HEX}.*\d+\.\d+", msg)
        count = re.search(r"needs 6 accents .* has \d+", msg)
        assert pair or count, f"{pid}: error names no pair or count: {msg}"
        return
    assert set(th["roles"]) == set(P.ROLES)
    assert set(th["edges"]) == set(P.EDGE_KINDS)
    assert th["checks"]["text_min"] >= P.TEXT_MIN
    assert th["checks"]["nontext_min"] >= P.NONTEXT_MIN


@pytest.mark.parametrize("pid", sorted(CATALOG))
def test_muted_reaches_4_5_on_canvas_and_container_fill(pid):
    try:
        th = P.derive_theme(pid, use_cache=False)
    except P.PaletteError:
        pytest.skip("not derivable")
    assert P.contrast(th["muted"], th["canvas"]) >= 4.5
    assert P.contrast(th["muted"], th["cl_fill"]) >= 4.5
    assert P.contrast(th["label"], th["canvas"]) >= 4.5


def test_known_failures_are_the_expected_ones():
    """Palettes that cannot give a diagram theme, and why. A new failure is a regression."""
    bad = {}
    for pid in CATALOG:
        try:
            P.derive_theme(pid, use_cache=False)
        except P.PaletteError as exc:
            bad[pid] = str(exc)
    assert "solarized-light" in bad and "4.13:1" in bad["solarized-light"]   # body text fails AA
    assert "tol-high-contrast" in bad and "has 3" in bad["tol-high-contrast"]
    for pid in ("tol-medium-contrast", "tol-muted", "okabe-ito", "petroff-10", "govuk",
                "modus-vivendi-tritanopia", "catppuccin-latte", "catppuccin-frappe",
                "everforest-dark-medium"):
        assert pid not in bad, bad.get(pid)


def test_derivation_keeps_designed_separation_of_tol_medium_contrast():
    """The old engine derived every stroke at one Oklch L and C, which collapsed this
    palette's light/dark pairs (deuteranopia dE00 0.06). Strokes now keep each
    accent's own lightness: no pair collapses under any simulation."""
    th = P.derive_theme("tol-medium-contrast", use_cache=False)
    st = th["checks"]["strokes"]
    for s in P.SIMS:
        assert st[s]["de_ok"] >= P.CVD_MIN, (s, st[s])
        assert st[s]["de00"] >= 5.0, (s, st[s])


def test_strokes_keep_the_accent_when_it_already_passes():
    th = P.derive_theme("tol-medium-contrast", use_cache=False)
    used = th["derivation"]["role_accents"]
    roles = CATALOG["tol-medium-contrast"]["roles"]
    # #994455 (dark red) reaches 3:1 on white, its fill and the container fill unchanged
    sign_key = used["sign"]
    assert roles[sign_key] == "#994455"
    assert th["roles"]["sign"][1] == "#994455"


def test_fills_keep_the_accents_lightness_order():
    entry = CATALOG["tol-medium-contrast"]
    th = P.derive_theme("tol-medium-contrast", use_cache=False)
    used = {r: k for r, k in th["derivation"]["role_accents"].items() if k != "neutral"}
    acc_l = {r: P.oklch(entry["roles"][k])[0] for r, k in used.items()}
    fill_l = {r: P.oklch(th["roles"][r][0])[0] for r in used}
    order_acc = sorted(used, key=lambda r: acc_l[r])
    order_fill = sorted(used, key=lambda r: fill_l[r])
    assert order_acc == order_fill


def test_text_on_every_fill_targets_7_to_1_when_reachable():
    th = P.derive_theme("tol-medium-contrast", use_cache=False)
    for r in P.ROLES:
        f, s, t = th["roles"][r]
        assert P.contrast(t, f) >= P.TEXT_TARGET
        assert P.contrast(s, f) >= P.NONTEXT_MIN
        assert P.contrast(s, th["canvas"]) >= P.NONTEXT_MIN


def test_roles_follow_hue_targets():
    th = P.derive_theme("okabe-ito", use_cache=False)
    used = th["derivation"]["role_accents"]
    roles = CATALOG["okabe-ito"]["roles"]
    assert roles[used["sign"]] == "#d55e00"     # vermillion takes sign (red, 25 degrees)
    assert roles[used["inv"]] == "#0072b2"      # blue takes investigation (260 degrees)
    assert used["anat"] == "neutral"


def test_dark_palette_derives_light_strokes():
    th = P.derive_theme("modus-vivendi-tritanopia", use_cache=False)
    assert th["mode"] == "dark"
    for r in P.ROLES:
        f, s, t = th["roles"][r]
        assert P.luminance(s) > P.luminance(f)
        assert P.contrast(s, th["canvas"]) >= 3


def test_contrast_failure_names_pair_and_ratio():
    entry = {"id": "low-text", "mode": "light", "roles": {
        "background": "#ffffff", "text": "#999999",
        **{f"accent_{i}": c for i, c in enumerate(
            ["#cc6677", "#332288", "#ddcc77", "#117733", "#88ccee", "#882255"], 1)}}}
    with pytest.raises(P.PaletteError, match=r"#999999 on background #ffffff is 2\.85:1"):
        P.derive_theme(entry, use_cache=False)


def test_normal_vision_collapse_is_a_failure():
    th = P.derive_theme("tol-medium-contrast", use_cache=False)
    th = dict(th, roles=dict(th["roles"]))
    th["roles"]["dx"] = th["roles"]["risk"]
    chk = P.check_theme(th)
    assert any("risk and dx strokes" in f and "normal vision" in f for f in chk["failures"])


def test_cvd_check_mode_turns_reports_into_failures():
    entry = dict(CATALOG["modus-operandi"])
    th = P.derive_theme(entry, use_cache=False)
    assert th["checks"]["reported"], "modus-operandi collapses under deuteranopia"
    entry["cvd"] = "check"
    with pytest.raises(P.PaletteError, match=r"under (protan|deutan|tritan) simulation, below 0\.05"):
        P.derive_theme(entry, use_cache=False)


def test_five_accent_palette_borrows_positive_and_negative():
    entry = {"id": "five", "mode": "light", "roles": {
        "background": "#ffffff", "text": "#000000", "positive": "#117733",
        **{f"accent_{i}": c for i, c in enumerate(["#cc6677", "#332288", "#ddcc77", "#88ccee", "#882255"], 1)}}}
    th = P.derive_theme(entry, use_cache=False)
    assert "positive" in th["derivation"]["role_accents"].values()


def test_accent_names_map_by_hue():
    th = P.derive_theme("okabe-ito", use_cache=False)
    assert set(th["accents"]) == set(P.ACCENT_NAMES)
    assert th["accents"]["blue"] == "#0072b2"


def test_custom_palettes_file(tmp_path):
    f = tmp_path / "mine.toml"
    f.write_text('''
[[palette]]
id = "my-tol"
name = "My Tol"
mode = "light"
[palette.roles]
background = "#ffffff"
text = "#111111"
accent_1 = "#004488"
accent_2 = "#ddaa33"
accent_3 = "#bb5566"
accent_4 = "#117733"
accent_5 = "#44aa99"
accent_6 = "#882255"
''', encoding="utf-8", newline="\n")

    class Cfg(dict):
        def get(self, key, default=None):
            return {"style.custom_palettes_file": str(f)}.get(key, default)

    cfg = Cfg()
    assert "my-tol" in P.available(cfg)
    th = P.derive_theme("my-tol", cfg, use_cache=False)
    assert th["name"] == "My Tol" and th["source"] == "derived"
    with pytest.raises(P.PaletteError, match="did you mean my-tol"):
        P.get("my-tl", cfg)


def test_custom_palette_errors_are_clear(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text('[[palette]]\nid = "Bad Id"\n[palette.roles]\nbackground = "#fff"\ntext = "#000"\n', encoding="utf-8", newline="\n")
    with pytest.raises(P.PaletteError, match="must match"):
        P.load_custom(f)
    f.write_text('[[palette]]\nid = "x"\n[palette.roles]\nbackground = "white"\ntext = "#000"\n', encoding="utf-8", newline="\n")
    with pytest.raises(P.PaletteError, match="not a colour"):
        P.load_custom(f)


def test_theme_cache_round_trip(tmp_path):
    a = P.derive_theme("tol-muted", cache_dir=tmp_path)
    files = list(tmp_path.glob("tol-muted-*.json"))
    assert len(files) == 1 and files[0].name == f"tol-muted-{a['digest']}.json"
    b = P.derive_theme("tol-muted", cache_dir=tmp_path)
    assert a == b
    assert isinstance(b["roles"]["risk"], tuple)
    files[0].write_text("{broken", encoding="utf-8", newline="\n")
    assert P.derive_theme("tol-muted", cache_dir=tmp_path) == a
    cached = json.loads(files[0].read_text(encoding="utf-8"))
    assert cached["checks"]["strokes"]


def test_cache_key_changes_with_roles(tmp_path):
    entry = dict(CATALOG["tol-muted"])
    a = P.derive_theme(entry, cache_dir=tmp_path)
    entry["roles"] = dict(entry["roles"], accent_1="#cc6678")
    b = P.derive_theme(entry, cache_dir=tmp_path)
    assert a["digest"] != b["digest"]


def test_derivation_is_deterministic():
    a = P.derive_theme("petroff-10", use_cache=False)
    b = P.derive_theme("petroff-10", use_cache=False)
    assert a == b


def test_themes_and_print_theme_follow_config():
    class Cfg:
        def get(self, key, default=None):
            return {"style.palettes": ["tol-muted", "catppuccin-frappe"],
                    "style.print_palette": "tol-muted"}.get(key, default)

    ths = P.themes(Cfg(), use_cache=False)
    assert list(ths) == ["tol-muted", "catppuccin-frappe"]
    assert P.print_theme(Cfg(), use_cache=False)["id"] == "tol-muted"
    assert list(P.themes(None, use_cache=False)) == [P.default_palette()]


def test_theme_carries_dark_flag_and_name():
    light = P.derive_theme("tol-medium-contrast", use_cache=False)
    dark = P.derive_theme("modus-vivendi-tritanopia", use_cache=False)
    frappe = P.derive_theme("catppuccin-frappe", use_cache=False)
    assert light["dark"] is False and dark["dark"] is True and frappe["dark"] is True
    assert light["name"] == CATALOG["tol-medium-contrast"]["name"]


def test_fills_reach_7_to_1_or_keep_apart_at_4_5():
    """7:1 wherever the text reaches 7:1 on the canvas, unless the fills would fall
    within one just-noticeable difference; then 4.5:1 with every fill pair apart."""
    for pid in sorted(CATALOG):
        try:
            th = P.derive_theme(pid, use_cache=False)
        except P.PaletteError:
            continue
        if th["source"] != "derived":
            continue
        fills = [th["roles"][r][0] for r in P.ROLES]
        worst = min(P.contrast(th["ink"], f) for f in fills)
        apart = min(P.de_ok(a, b) for i, a in enumerate(fills) for b in fills[i + 1:])
        assert worst >= P.TEXT_MIN and apart >= P.JND_OK, pid
        if P.contrast(th["ink"], th["canvas"]) >= P.TEXT_TARGET and worst < P.TEXT_TARGET:
            # 7:1 was given up: one step paler would merge two fills or fail 4.5:1
            assert th["derivation"]["fill_t"] > P.T_MIN, pid


def test_load_catalog_adds_custom_palettes(tmp_path):
    f = tmp_path / "mine.toml"
    f.write_text('[[palette]]\nid = "mine"\nname = "Mine"\nmode = "light"\n[palette.roles]\n'
                 'background = "#ffffff"\ntext = "#000000"\n', encoding="utf-8", newline="\n")

    class Cfg(dict):
        def get(self, key, default=None):
            return {"style.custom_palettes_file": str(f)}.get(key, default)

    cat = P.load_catalog(Cfg())
    assert "mine" in cat and cat["mine"]["name"] == "Mine"
    assert set(P.catalog()) < set(cat)
    assert P.load_catalog() == P.available(None)
    assert P.catalog(Cfg()) == P.catalog()      # a config where a path goes is ignored


@pytest.mark.parametrize("pid", ["tol-medium-contrast", "modus-vivendi-tritanopia", "catppuccin-latte",
                                 "catppuccin-frappe", "everforest-dark-medium", "govuk", "okabe-ito"])
def test_pdf_theme_keys_and_text_contrast(pid):
    t = P.pdf_theme(pid, use_cache=False)
    th = P.derive_theme(pid, use_cache=False)
    assert set(P.PDF_KEYS) <= set(t) and isinstance(t["dark"], bool)
    for k in P.PDF_KEYS:
        assert re.fullmatch(HEX, t[k]), (k, t[k])
    assert t["base"] == th["canvas"] and t["mantle"] == th["cl_fill"] and t["text"] == th["ink"]
    for k in P.PDF_TEXT_KEYS:
        for g in ("base", "mantle"):
            assert P.contrast(t[k], t[g]) >= P.TEXT_MIN, (k, g)
    assert P.contrast(t["text"], t["surface"]) >= P.TEXT_MIN
    assert P.contrast(t["yellow"], t["base"]) >= P.NONTEXT_MIN
    assert t["dark"] == th["dark"]


def test_pdf_theme_steps_an_accent_that_fails_and_keeps_its_hue():
    t = P.pdf_theme("catppuccin-latte", use_cache=False)
    raw = P.derive_theme("catppuccin-latte", use_cache=False)["accents"]["blue"]
    assert P.contrast(raw, t["base"]) < P.TEXT_MIN       # Catppuccin's blue is 4.2:1 on Latte's base
    assert t["blue"] != raw and P.hue_gap(P.oklch(t["blue"])[2], P.oklch(raw)[2]) < 3.0
