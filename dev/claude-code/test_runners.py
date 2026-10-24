# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise the routing graders through their real command-line interfaces."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent


def invoke(
    tmp_path: Path,
    runner: str,
    expected_text: str,
    observed_text: str,
) -> subprocess.CompletedProcess[str]:
    expected = tmp_path / "expected.json"
    observed = tmp_path / "observed.json"
    expected.write_text(expected_text, encoding="utf-8")
    observed.write_text(observed_text, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / runner),
            "--expectations",
            str(expected),
            "--observations",
            str(observed),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def encode(rows: list[dict]) -> str:
    return json.dumps({"cases": rows})


def test_trigger_pass_counts_positive_and_negative(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode(
            [
                {"id": "positive", "query": "Capture a practice module.", "should_trigger": True},
                {"id": "negative", "query": "Translate a sentence.", "should_trigger": False},
            ]
        ),
        encode(
            [
                {"id": "negative", "triggered": False},
                {"id": "positive", "triggered": True},
            ]
        ),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["total"] == report["passed"] == 2
    assert report["counts"] == {
        "true_positive": 1,
        "true_negative": 1,
        "false_positive": 0,
        "false_negative": 0,
    }


def test_trigger_mismatches_fail_with_both_error_classes(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode(
            [
                {"id": "miss", "query": "Capture a practice module.", "should_trigger": True},
                {"id": "extra", "query": "Translate a sentence.", "should_trigger": False},
            ]
        ),
        encode(
            [
                {"id": "miss", "triggered": False},
                {"id": "extra", "triggered": True},
            ]
        ),
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["failed"] == 2
    assert report["accuracy"] == 0
    assert report["counts"]["false_positive"] == 1
    assert report["counts"]["false_negative"] == 1


@pytest.mark.parametrize(
    "observed_rows",
    [
        [],
        [{"id": "other", "triggered": True}],
        [{"id": "case", "triggered": True}, {"id": "extra", "triggered": False}],
    ],
)
def test_missing_or_extra_cases_are_input_errors(
    tmp_path: Path, observed_rows: list[dict]
) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode([{"id": "case", "query": "Capture a practice module.", "should_trigger": True}]),
        encode(observed_rows),
    )
    assert result.returncode == 2
    assert "Input error:" in result.stderr
    assert result.stdout == ""


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode([{"id": "case", "query": "Capture a practice module.", "should_trigger": True}]),
        encode([{"id": "case", "triggered": True}, {"id": "case", "triggered": False}]),
    )
    assert result.returncode == 2
    assert "duplicate case id" in result.stderr


def test_duplicate_json_members_are_rejected(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode([{"id": "case", "query": "Capture a practice module.", "should_trigger": True}]),
        '{"cases":[{"id":"case","triggered":true,"triggered":false}]}',
    )
    assert result.returncode == 2
    assert "Duplicate JSON member" in result.stderr


@pytest.mark.parametrize("value", [1, "true", None])
def test_trigger_observation_requires_boolean(tmp_path: Path, value: object) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode([{"id": "case", "query": "Capture a practice module.", "should_trigger": True}]),
        encode([{"id": "case", "triggered": value}]),
    )
    assert result.returncode == 2
    assert "must be a boolean" in result.stderr


def test_empty_expectations_cannot_pass(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        encode([]),
        encode([]),
    )
    assert result.returncode == 2
    assert "nonempty list" in result.stderr


def test_malformed_json_is_an_input_error(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_trigger_eval.py",
        "{",
        encode([{"id": "case", "triggered": True}]),
    )
    assert result.returncode == 2
    assert "Invalid JSON" in result.stderr


def test_confusion_matrix_records_misrouting(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_confusion_eval.py",
        encode(
            [
                {"id": "export", "query": "Export a capture.", "expected_skill": "lt-module-export"},
                {"id": "none", "query": "Translate a sentence.", "expected_skill": None},
            ]
        ),
        encode(
            [
                {"id": "export", "actual_skill": "lt-module-fill"},
                {"id": "none", "actual_skill": None},
            ]
        ),
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["matrix"]["lt-module-export"]["lt-module-fill"] == 1
    assert report["matrix"]["none"]["none"] == 1
    assert report["passed"] == report["failed"] == 1
    assert report["accuracy"] == 0.5


def test_confusion_pass_accepts_null_label(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        "run_confusion_eval.py",
        encode([{"id": "none", "query": "Translate a sentence.", "expected_skill": None}]),
        encode([{"id": "none", "actual_skill": None}]),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["failed"] == 0
    assert report["matrix"]["none"]["none"] == 1


@pytest.mark.parametrize(
    "observed",
    [
        {"id": "case"},
        {"id": "case", "actual_skill": "unknown-skill"},
        {"id": "case", "actual_skill": ["lt-module-fill"]},
    ],
)
def test_confusion_rejects_missing_or_unknown_labels(
    tmp_path: Path, observed: dict
) -> None:
    result = invoke(
        tmp_path,
        "run_confusion_eval.py",
        encode([{"id": "case", "query": "Fill a practice module.", "expected_skill": "lt-module-fill"}]),
        encode([observed]),
    )
    assert result.returncode == 2
    assert "Input error:" in result.stderr


def test_unreadable_file_returns_input_error(tmp_path: Path) -> None:
    observed = tmp_path / "observed.json"
    observed.write_text(encode([{"id": "case", "triggered": True}]), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "run_trigger_eval.py"),
            "--expectations",
            str(tmp_path / "missing.json"),
            "--observations",
            str(observed),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "Cannot read" in result.stderr


@pytest.mark.parametrize(
    "runner",
    ["run_trigger_eval.py", "run_confusion_eval.py"],
)
def test_help_runs_outside_repository(tmp_path: Path, runner: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / runner), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert "--observations" in result.stdout
