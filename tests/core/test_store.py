# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data folder resolution order, layout, and the store CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import config, paths, store


@pytest.mark.parametrize("name", [
    "CON", "con.txt", "NUL", "AUX", "PRN", "COM1", "com9.json", "LPT1", "LPT9",
    "COM\u00b9", "LPT\u00b2.txt", "COM\u00b3", "CONIN$", "CONOUT$", "c1-r101.",
])
def test_store_rejects_windows_device_names_and_aliases(name):
    with pytest.raises(ValueError, match="safe module id"):
        store.check_id(name)


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def test_data_dir_order_flag_env_config_default(tmp_path, monkeypatch):
    f = write(tmp_path / "c.toml", '[paths]\ndata_dir = "cfgdata"\ncache_dir = "cfgcache"\n')
    cfg = config.load(f)
    monkeypatch.setenv("LT_SUITE_DATA", str(tmp_path / "envdata"))
    monkeypatch.setenv("LT_SUITE_CACHE", str(tmp_path / "envcache"))
    assert store.resolve_data_dir(tmp_path / "flagdata", cfg=cfg) == (tmp_path / "flagdata", "flag --data-dir")
    assert store.resolve_data_dir(cfg=cfg) == (tmp_path / "envdata", "env LT_SUITE_DATA")
    assert store.resolve_cache_dir(cfg=cfg) == (tmp_path / "envcache", "env LT_SUITE_CACHE")
    monkeypatch.delenv("LT_SUITE_DATA")
    monkeypatch.delenv("LT_SUITE_CACHE")
    assert store.resolve_data_dir(cfg=cfg) == (tmp_path / "cfgdata", "config paths.data_dir")
    assert store.resolve_cache_dir(cfg=cfg) == (tmp_path / "cfgcache", "config paths.cache_dir")
    plain = config.load(write(tmp_path / "p.toml", "schema_version = 1\n"))
    assert store.resolve_data_dir(cfg=plain) == (Path(str(paths.data_dir())), "default")
    assert store.resolve_cache_dir(cfg=plain) == (Path(str(paths.cache_dir())), "default")


def test_store_reads_config_when_none_given(tmp_path, monkeypatch):
    f = write(tmp_path / "c.toml", '[paths]\ndata_dir = "d"\n')
    monkeypatch.setenv("LT_SUITE_CONFIG", str(f))
    st = store.Store()
    assert st.data_dir == tmp_path / "d" and st.data_source == "config paths.data_dir"


def test_broken_config_falls_back_to_default(tmp_path, monkeypatch):
    f = write(tmp_path / "c.toml", "[paths\n")
    monkeypatch.setenv("LT_SUITE_CONFIG", str(f))
    path, src = store.resolve_data_dir()
    assert src == "default" and path == Path(str(paths.data_dir()))


def test_layout(tmp_path):
    st = store.Store(config.load(write(tmp_path / "c.toml", "schema_version = 1\n")),
                     data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    d = tmp_path / "data"
    assert st.captures_dir == d / "captures"
    assert st.media_dir == d / "captures" / "media"
    assert st.incoming_dir == d / "incoming"
    assert st.runs_dir == d / "runs"
    assert st.run_dir("c1-r101") == d / "runs" / "c1-r101"
    assert st.run_path("c1-r101") == d / "runs" / "c1-r101" / "run.json"
    assert st.approvals_path("c1-r101").name == "approvals.json"
    assert st.fill_report_path("c1-r101").name == "fill-report.json"
    assert st.model_answer_path("c1-r101").name == "model-answer.json"
    assert st.logs_dir("c1-r101").name == "logs" and st.fill_js_dir("c1-r101").name == "fill-js"
    assert st.capture_path("c1-r101") == d / "captures" / "module-c1-r101.json"
    assert st.reference_path("c1-r101") == d / "captures" / "reference-c1-r101.json"
    assert st.calibration_path == d / "state" / "calibration.json"
    assert st.ledger_path == d / "state" / "ledger.json"
    assert st.applied_path == d / "state" / "applied.json"
    assert st.lexicon_dir == d / "lexicon" and st.fonts_dir == d / "fonts" and st.venv_dir == d / "venv"
    assert st.themes_cache_dir == tmp_path / "cache" / "themes"
    assert st.font_cache_dir == tmp_path / "cache" / "font-cache"
    assert not d.exists()                      # resolving creates nothing
    st.ensure()
    for name in ("captures", "captures/media", "incoming", "runs", "state", "lexicon", "fonts"):
        assert (d / name).is_dir(), name
    assert not (d / "venv").exists() and not (tmp_path / "cache").exists()


def test_captures_and_runs_dirs_from_config(tmp_path):
    f = write(tmp_path / "c.toml", f'[paths]\ncaptures_dir = "caps"\nruns_dir = "{(tmp_path / "abs-runs").as_posix()}"\n')
    st = store.Store(config.load(f), data_dir=tmp_path / "data")
    assert st.captures_dir == tmp_path / "data" / "caps"
    assert st.runs_dir == tmp_path / "abs-runs"
    d = st.as_dict()
    assert d["paths"]["captures"]["where"] == "config paths.captures_dir, inside the data folder"
    assert d["paths"]["runs"]["where"] == "config paths.runs_dir"
    assert d["data_dir"]["where"] == "flag --data-dir"


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "..", "x" * 200, "c1 r1", ".hidden"])
def test_unsafe_module_ids_are_refused(tmp_path, bad):
    st = store.Store(data_dir=tmp_path)
    with pytest.raises(ValueError):
        st.run_dir(bad)


def test_iter_runs(tmp_path, capsys):
    st = store.Store(data_dir=tmp_path)
    write(st.run_path("c1-r102"), json.dumps({"module_id": "c1-r102"}))
    write(st.run_path("c1-r101"), json.dumps({"module_id": "c1-r101"}))
    write(st.runs_dir / "c1-r103" / "run.json", "{broken")
    got = [doc["module_id"] for _, doc in st.iter_runs()]
    assert got == ["c1-r101", "c1-r102"]
    assert "unreadable run record" in capsys.readouterr().err
    assert st.as_dict()["run_records"] == 2


def test_describe_has_no_removed_entries(tmp_path):
    d = store.Store(data_dir=tmp_path).as_dict()
    assert "decisions" not in d["paths"] and "permissions" not in d["paths"]
    for v in d["paths"].values():
        assert v["where"]


def test_store_cli(run_py, tmp_path):
    r = run_py("-m", "ltcore.store", "--json", "--data-dir", str(tmp_path / "d"))
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["data_dir"]["path"] == str(tmp_path / "d")
    r = run_py("-m", "ltcore.store", "--data-dir", str(tmp_path / "d"))
    assert r.returncode == 0 and "flag --data-dir" in r.stdout
    bad = write(tmp_path / "bad.toml", "[lt]\nbogus = 1\n")
    r = run_py("-m", "ltcore.store", "--config", str(bad))
    assert r.returncode == 1 and "unknown key" in r.stderr
