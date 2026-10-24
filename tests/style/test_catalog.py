# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The palette catalog, its ranking and the defaults chosen from it.

- ranking.json and the rank fields in catalog.json equal a fresh ``rank()``
- the ranking reads colours only: changing or removing licence fields changes nothing
- style.defaults.toml, catalog.json and ranking.json name the same winners
- every entry ships under an SPDX licence or LicenseRef-palette-attribution with credit
- the corrections from the catalog review are in place
"""
from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path

import pytest

from ltcore import palettes as P

REPO = Path(__file__).resolve().parents[2]
PAL = REPO / "shared" / "palettes"
CAT = json.loads((PAL / "catalog.json").read_text(encoding="utf-8"))
RANKING = json.loads((PAL / "ranking.json").read_text(encoding="utf-8"))
STYLE = tomllib.loads((REPO / "shared" / "style.defaults.toml").read_text(encoding="utf-8"))
SPDX_OK = {"MIT", "BSD-3-Clause", "Apache-2.0", "GPL-3.0-or-later", "CC0-1.0", "CC-BY-4.0"}


@pytest.fixture(scope="module")
def fresh():
    return P.rank(P.catalog())


def test_catalog_entries_are_well_formed():
    ids = [e["id"] for e in CAT["palettes"]]
    assert len(ids) == len(set(ids)) == 42
    for e in CAT["palettes"]:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", e["id"])
        assert e["mode"] in ("light", "dark", "categorical-only", "sequential")
        for k, v in e["roles"].items():
            assert re.fullmatch(r"#[0-9a-f]{6}", v), (e["id"], k, v)
        assert {"background", "text"} <= set(e["roles"])
        assert set(e["roles"]) <= set(e["role_sources"])


def test_every_entry_ships_with_a_licence_or_attribution():
    for e in CAT["palettes"]:
        lic = e["licence"]
        if "spdx" in lic:
            assert all(part in SPDX_OK for part in lic["spdx"].split(" AND ")), e["id"]
            assert lic.get("copyright"), e["id"]
        else:
            assert lic["ref"] == "LicenseRef-palette-attribution", e["id"]
            assert len(lic["attribution"]) > 40, e["id"]
    refs = sorted(e["id"] for e in CAT["palettes"] if "ref" in e["licence"])
    assert refs == ["cividis", "okabe-ito"]


def test_attribution_file_names_every_family_and_licence():
    text = (PAL / "ATTRIBUTION.md").read_text(encoding="utf-8")
    for e in CAT["palettes"]:
        assert f"`{e['id']}`" in text, e["id"]
    assert "## LicenseRef-palette-attribution" in text
    assert "Battelle Memorial Institute" in text and "Masataka Okabe and Kei Ito" in text


def test_ranking_file_matches_a_fresh_ranking(fresh):
    assert RANKING == json.loads(json.dumps(fresh)), \
        "shared/palettes/ranking.json is stale: python3 -m ltcore.palettes rank --write it"


def test_catalog_rank_fields_match_the_ranking(fresh):
    rows = {r["id"]: r for r in fresh["entries"]}
    prim = fresh["primary_weighting"]
    for e in CAT["palettes"]:
        r, rk = rows[e["id"]], e["rank"]
        assert rk["derivable"] == r["derivable"], e["id"]
        if r["derivable"]:
            assert rk["score"] == r["scores"][prim]
            if r["default_eligible"]:
                assert rk["position"] == fresh["order"][prim][r["mode"]].index(e["id"]) + 1
            else:
                assert "position" not in rk
            assert rk["default_eligible"] == r["default_eligible"]
    assert CAT["defaults"] == fresh["defaults"]


def test_ranking_is_independent_of_licence_form(fresh):
    """Swap every entry's licence for an attribution reference, drop it, or mark it
    unlicensed: scores and order stay the same."""
    base = P.catalog()
    variants = []
    for mode in ("ref", "drop", "spdx-swap"):
        cat = copy.deepcopy(base)
        for e in cat.values():
            if mode == "ref":
                e["licence"] = {"ref": "LicenseRef-palette-attribution", "attribution": "x"}
            elif mode == "drop":
                e.pop("licence", None)
            else:
                e["licence"] = {"spdx": "CC0-1.0"}
        variants.append(P.rank(cat))
    for v in variants:
        assert v["order"] == fresh["order"]
        assert v["defaults"] == fresh["defaults"]
        assert [r.get("scores") for r in v["entries"]] == [r.get("scores") for r in fresh["entries"]]


def test_defaults_agree_across_files(fresh):
    d = fresh["defaults"]
    assert STYLE["style"]["palettes"] == d["palettes"]
    assert STYLE["style"]["print_palette"] == d["print_palette"]
    assert P.default_palette() == d["palettes"][0]
    assert P.default_print_palette() == d["print_palette"]


def test_defaults_are_top_ranked_and_eligible(fresh):
    prim = fresh["primary_weighting"]
    rows = {r["id"]: r for r in fresh["entries"]}
    light, dark = fresh["defaults"]["palettes"]
    assert rows[light]["mode"] == "light" and rows[dark]["mode"] == "dark"
    for pid, mode in ((light, "light"), (dark, "dark")):
        assert rows[pid]["default_eligible"]
        better = [i for i in fresh["order"][prim][mode] if i != pid and
                  rows[i]["scores"][prim] > rows[pid]["scores"][prim]]
        assert all(not rows[i]["default_eligible"] for i in better), better


def test_owner_style_overrides_and_ramps_are_never_defaults(fresh):
    rows = {r["id"]: r for r in fresh["entries"]}
    for pid in ("catppuccin-latte", "catppuccin-frappe", "everforest-dark-medium", "viridis", "cividis"):
        assert rows[pid]["derivable"] and not rows[pid]["default_eligible"], pid


def test_robustness_record_has_at_least_three_weightings(fresh):
    assert len(fresh["weightings"]) >= 3
    for w, weights in fresh["weightings"].items():
        assert sum(weights.values()) == 100, w
    light = fresh["defaults"]["palettes"][0]
    wins = sum(1 for w in fresh["winners"].values() if w["light"] == light)
    assert wins >= len(fresh["weightings"]) / 2, fresh["winners"]


def test_review_corrections_are_applied():
    cat = P.catalog()
    civ = cat["cividis"]["roles"]
    assert civ["accent_1"] == "#00204d" and civ["accent_7"] == "#ffea46"
    for pid in ("modus-vivendi-deuteranopia", "modus-operandi-deuteranopia"):
        acc = [v for k, v in cat[pid]["roles"].items() if k.startswith("accent_")]
        base = cat[pid.replace("-deuteranopia", "")]["roles"]
        assert base["accent_1"] not in acc or base["accent_2"] not in acc, pid  # not the base red and green
        assert all("rainbow" in cat[pid]["role_sources"][k] for k in cat[pid]["roles"] if k.startswith("accent_"))
    for pid in ("petroff-6", "petroff-8", "petroff-10"):
        assert cat[pid]["licence"]["spdx"] == "CC0-1.0"


def test_ranking_prints_and_writes(tmp_path, run_py):
    out = tmp_path / "r.json"
    r = run_py("-m", "ltcore.palettes", "rank", "--write", str(out))
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == RANKING


def test_cli_check_and_show(run_py):
    r = run_py("-m", "ltcore.palettes", "check", "tol-medium-contrast", "catppuccin-latte")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("OK ") == 2
    r = run_py("-m", "ltcore.palettes", "check", "solarized-light")
    assert r.returncode == 1 and "FAIL" in r.stdout
    r = run_py("-m", "ltcore.palettes", "show", "catppuccin-frappe")
    assert r.returncode == 0 and "override" in r.stdout


def test_catalog_file_is_the_ranking_applied(fresh):
    """catalog.json equals itself with rank and defaults rewritten from a fresh ranking
    (python3 -m ltcore.palettes rank --update-catalog writes exactly this)."""
    assert P.apply_ranking(CAT, fresh) == json.loads(json.dumps(CAT))
    text = (PAL / "catalog.json").read_text(encoding="utf-8")
    assert text == json.dumps(CAT, indent=1, ensure_ascii=False) + "\n"
