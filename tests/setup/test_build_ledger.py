# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test selector aggregation, report deduplication, and ledger output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills/lt-suite-setup/scripts/build_ledger.py"

LIVE = ".module-kcExample (filtered by panelKey)"
MIXED = ".module-kcChoice (filtered by panelKey)"
FAILED = ".module-kcMissing (filtered by panelKey)"


@pytest.fixture
def workspace(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("schema_version = 1\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    return cfg_file, data


def _run(workspace, *args, expected=0):
    cfg_file, data = workspace
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(cfg_file),
            "--data-dir",
            str(data),
            *args,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, (
        f"exit {result.returncode}, expected {expected}\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return result


def _event(selector, outcome, item_type="text-response"):
    return {
        "selector": selector,
        "item_type": item_type,
        "outcome": outcome,
    }


def _write_run(data, module_id="c1-r101", events=None):
    record = {
        "schema_version": 1,
        "run_ts": "2000-01-01T00:00:00Z",
        "module_id": module_id,
        "skills_invoked": [
            "lt-module-capture",
            "lt-module-answer",
            "lt-module-export",
        ],
        "question_type_census": {"text-response": 3},
        "tool_version": "0.1.0",
        "config_digest": "0" * 64,
        "outcome": "complete",
        "selector_events": events if events is not None else [],
    }
    path = data / "runs" / module_id / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def _ledger(workspace, *args):
    result = _run(workspace, "--json", "--dry-run", *args)
    return json.loads(result.stdout)


def _entries(ledger):
    return {
        (entry["selector"], entry["item_type"]): entry
        for entry in ledger["entries"]
    }


def test_ledger_counts_hits_and_misses_per_selector_and_item_type(workspace):
    _, data = workspace
    _write_run(
        data,
        "c1-r101",
        [
            _event(LIVE, "hit"),
            _event(MIXED, "hit"),
            _event(FAILED, "miss"),
            _event(LIVE, "miss", "table"),
        ],
    )
    _write_run(
        data,
        "c1-r102",
        [
            _event(LIVE, "hit"),
            _event(MIXED, "miss"),
            _event(FAILED, "miss"),
            _event(LIVE, "miss", "table"),
        ],
    )

    ledger = _ledger(workspace)

    entries = _entries(ledger)
    assert len(entries) == 4
    expected = {
        (LIVE, "text-response"): (2, 0, "live"),
        (MIXED, "text-response"): (1, 1, "flaky"),
        (FAILED, "text-response"): (0, 2, "failed"),
        (LIVE, "table"): (0, 2, "failed"),
    }
    for key, counts in expected.items():
        entry = entries[key]
        assert (entry["hits"], entry["misses"], entry["verdict"]) == counts
        assert entry["modules"] == ["c1-r101", "c1-r102"]
        assert set(entry["sources"]) == {
            "c1-r101/run.json",
            "c1-r102/run.json",
        }

    summary = ledger["summary"]
    assert summary["selectors"] == 4
    assert summary["live"] == 1
    assert summary["flaky"] == 1
    assert summary["failed"] == 2
    assert summary["dead"] == 0

    generated = ledger["generated_from"]
    assert generated["records"] == 2
    assert generated["records_carrying_events"] == 2
    assert generated["modules"] == ["c1-r101", "c1-r102"]
    assert generated["raw_events"] == 8
    assert generated["independent_events"] == 8


def test_matching_fill_report_adds_metadata_without_independent_events(
    workspace, tmp_path
):
    _, data = workspace
    events = [_event(LIVE, "hit"), _event(FAILED, "miss")]
    _write_run(data, events=events)
    report = tmp_path / "fill-report.json"
    report.write_text(
        json.dumps(
            {
                "module_id": "c1-r101",
                "selector_events": [
                    {**event, "recorded_at": "2000-01-01T00:00:00Z"}
                    for event in events
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    baseline_text = _run(workspace, "--dry-run")
    baseline = _ledger(workspace)

    folded_text = _run(
        workspace, "--dry-run", "--fill-report", str(report)
    )
    folded = _ledger(workspace, "--fill-report", str(report))

    census = "events      2 raw -> 2 independent"
    assert census in baseline_text.stdout
    assert census in folded_text.stdout
    for key in ("raw_events", "independent_events"):
        assert folded["generated_from"][key] == baseline["generated_from"][key] == 2
    assert folded["generated_from"]["fill_reports_folded"] == 1
    assert folded["summary"] == baseline["summary"]

    original_entries = _entries(baseline)
    for key, entry in _entries(folded).items():
        original = original_entries[key]
        assert entry["hits"] == original["hits"]
        assert entry["misses"] == original["misses"]
        assert entry["verdict"] == original["verdict"]
        assert set(entry["sources"]) == {"c1-r101/run.json", report.name}
        assert entry["modules"] == ["c1-r101"]
        assert entry["first_seen"] == "2000-01-01T00:00:00Z"
        assert entry["last_seen"] == "2000-01-01T00:00:00Z"


def test_ledger_flags_bare_css_and_accepts_panel_key_filtering(workspace):
    _, data = workspace
    bare = ".module-kcExample"
    _write_run(
        data,
        events=[_event(bare, "hit"), _event(LIVE, "miss")],
    )

    ledger = _ledger(workspace)

    entries = _entries(ledger)
    assert entries[(bare, "text-response")]["bare_css"] is True
    assert entries[(LIVE, "text-response")]["bare_css"] is False
    assert ledger["summary"]["bare_css_violations"] == 1
    assert ledger["summary"]["bare_css_hits"] == 1
    assert ledger["summary"]["bare_css_misses"] == 0


def test_ledger_dry_run_writes_no_files(workspace):
    _, data = workspace
    run = _write_run(data, events=[_event(LIVE, "hit")])
    before = {
        path: path.read_bytes()
        for path in data.rglob("*")
        if path.is_file()
    }

    result = _run(workspace, "--dry-run")

    assert "events      1 raw -> 1 independent" in result.stdout
    assert not (data / "state" / "ledger.json").exists()
    after = {
        path: path.read_bytes()
        for path in data.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert run in after


def test_ledger_default_run_writes_and_prints_the_ledger_path(workspace):
    _, data = workspace
    run = _write_run(data, events=[_event(LIVE, "hit")])
    before = run.read_bytes()

    result = _run(workspace)

    path = data / "state" / "ledger.json"
    assert f"ledger      {path}" in result.stdout
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert ledger["schema_version"] == 1
    assert ledger["summary"]["live"] == 1
    assert ledger["generated_from"]["independent_events"] == 1
    assert len(ledger["entries"]) == 1
    assert run.read_bytes() == before


def test_ledger_json_dry_run_prints_a_parseable_document(workspace):
    _, data = workspace
    _write_run(data, events=[_event(LIVE, "hit")])

    result = _run(workspace, "--json", "--dry-run")

    ledger = json.loads(result.stdout)
    assert isinstance(ledger["summary"], dict)
    assert isinstance(ledger["entries"], list)
    assert ledger["entries"][0]["selector"] == LIVE
    assert not (data / "state" / "ledger.json").exists()


def test_ledger_refuses_a_missing_runs_folder(workspace):
    _, data = workspace
    assert not (data / "runs").exists()

    result = _run(workspace, expected=3)

    assert "no runs folder" in result.stderr
    assert not (data / "state" / "ledger.json").exists()
