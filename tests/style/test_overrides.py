# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Golden test: the three hand-tuned overrides reproduce the diagram themes the
engine drew before the catalog existed (golden-themes.json), except the changes
listed, with their contrast, in override-deltas.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import palettes as P

HERE = Path(__file__).resolve().parent
GOLDEN = json.loads((HERE / "golden-themes.json").read_text(encoding="utf-8"))["themes"]
DELTAS = json.loads((HERE / "override-deltas.json").read_text(encoding="utf-8"))["deltas"]
IDS = sorted(GOLDEN)
STRUCT = ("canvas", "cl_fill", "cl_stroke", "cl_text", "key_fill", "key_stroke", "ink", "muted", "label")


def _flatten(theme: dict) -> dict:
    out = {k: theme[k] for k in STRUCT}
    for r, v in theme["roles"].items():
        for i, part in enumerate(("fill", "stroke", "text")):
            out[f"roles.{r}.{part}"] = v[i]
    for k, v in theme["edges"].items():
        out[f"edges.{k}.colour"], out[f"edges.{k}.dash"], out[f"edges.{k}.head"] = v
    for k, v in theme["accents"].items():
        out[f"accents.{k}"] = v
    out["glyph_lc"] = [round(x, 3) for x in theme["glyph_lc"]]
    for k, v in (theme.get("tokens") or {}).items():
        out[f"tokens.{k}"] = v
    return out


@pytest.fixture(scope="module")
def derived(tmp_path_factory):
    cache = tmp_path_factory.mktemp("themes")
    return {pid: P.derive_theme(pid, cache_dir=cache) for pid in IDS}


def test_golden_overrides_exist_for_the_three_palettes():
    assert IDS == ["catppuccin-frappe", "catppuccin-latte", "everforest-dark-medium"]
    for pid in IDS:
        assert P.override_path(pid).is_file()
        assert pid in P.catalog()


@pytest.mark.parametrize("pid", IDS)
def test_golden_override_matches_previous_theme(pid, derived):
    th = derived[pid]
    assert th["source"] == "override"
    want = _flatten(GOLDEN[pid])
    got = _flatten(th)
    listed = {d["key"]: d for d in DELTAS if d["palette"] == pid}
    diffs = {k for k in want if want[k] != got.get(k)}
    assert diffs == set(listed), f"{pid}: unlisted differences {sorted(diffs - set(listed))}"
    for key, d in listed.items():
        assert want[key] == d["old"] and got[key] == d["new"]
    assert set(got) == set(want), f"{pid}: keys differ: {sorted(set(got) ^ set(want))}"


@pytest.mark.parametrize("delta", DELTAS, ids=[f"{d['palette']}.{d['key']}" for d in DELTAS])
def test_golden_deltas_record_their_contrast(delta):
    g = GOLDEN[delta["palette"]]
    for ground, ratio in delta["old_contrast"].items():
        assert round(P.contrast(delta["old"], g[ground]), 2) == ratio
    for ground, ratio in delta["new_contrast"].items():
        assert round(P.contrast(delta["new"], g[ground]), 2) == ratio
        assert ratio >= P.TEXT_MIN
    assert any(r < P.TEXT_MIN for r in delta["old_contrast"].values()), "a delta must fix a failing pair"
    assert delta["reason"]


def test_golden_latte_muted_was_the_only_contrast_failure():
    """Before the change the Latte muted text measured 4.37 on its canvas; every other
    pair of the three old themes already passed the gate."""
    for pid in IDS:
        old = dict(GOLDEN[pid])
        old["roles"] = {r: tuple(v) for r, v in old["roles"].items()}
        old["edges"] = {k: tuple(v) for k, v in old["edges"].items()}
        fails = P.check_theme(old, cvd="report")["failures"]
        if pid == "catppuccin-latte":
            assert fails and all("muted text" in f for f in fails)
        else:
            assert fails == []


@pytest.mark.parametrize("pid", IDS)
def test_overrides_pass_the_contrast_gate_and_report_colour_vision(pid, derived):
    th = derived[pid]
    assert th["checks"]["cvd_gate"] == "report"
    assert th["checks"]["text_min"] >= P.TEXT_MIN
    assert th["checks"]["nontext_min"] >= P.NONTEXT_MIN
    m = th["muted"]
    assert P.contrast(m, th["canvas"]) >= P.TEXT_MIN
    assert P.contrast(m, th["cl_fill"]) >= P.TEXT_MIN
    # constant-lightness fills: greyscale separation is reported, never hidden
    assert any("greyscale" in r for r in th["checks"]["reported"])


def test_override_values_are_not_derived_from_roles():
    """An override replaces derivation: changing the catalog roles does not change it."""
    entry = dict(P.catalog()["catppuccin-latte"])
    entry["roles"] = dict(entry["roles"], accent_1="#123456")
    th = P.derive_theme(entry, use_cache=False)
    assert th["roles"]["risk"] == tuple(GOLDEN["catppuccin-latte"]["roles"]["risk"])
