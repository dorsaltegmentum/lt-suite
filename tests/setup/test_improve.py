# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test review classification, proposal gates, and applied records."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills/lt-suite-setup/scripts/improve.py"
BENCHMARK = "evals/lt-module-answer/evals.json pass rate with the skill"

CLAIM = "The example value is 3. [T2:ExampleGuide:topic]"
CHANGED_CLAIM = "The example value is 4. [T2:ExampleGuide:topic]"
CHANGED_TAG = "The example value is 3. [T4:10.1000/example]"


@pytest.fixture
def workspace(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("schema_version = 1\n", encoding="utf-8", newline="\n")
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
    encoding="utf-8")
    assert result.returncode == expected, (
        f"exit {result.returncode}, expected {expected}\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return result


def _write_diff(tmp_path, before=CLAIM, after=CHANGED_CLAIM):
    path = tmp_path / "page.diff"
    path.write_text(
        f"--- a (on disk)\n+++ b (this run)\n-{before}\n+{after}\n",
        encoding="utf-8",
    newline="\n")
    return path


def _entry(klass="factual", item_id="p1-1", steered=True):
    return {
        "item_id": item_id,
        "class": klass,
        "steered_run_wrong": steered,
        "before": CLAIM,
        "after": CHANGED_TAG if klass == "provenance" else CHANGED_CLAIM,
        "note": "[skill:lt-module-answer] [diff:page.diff]",
    }


def _write_run(data, module_id="c1-r101", entries=None):
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
    }
    if entries is not None:
        record["diff_classification"] = entries
    path = data / "runs" / module_id / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8", newline="\n")
    return path


def _seed_pair(data, klass="factual", steered=True):
    return [
        _write_run(
            data,
            module_id,
            [_entry(klass, item_id, steered)],
        )
        for module_id, item_id in (
            ("c1-r101", "p1-1"),
            ("c1-r102", "p2-2"),
        )
    ]


def _proposal_path(data, klass="factual"):
    return data / "lexicon" / "proposals" / f"lt-module-answer--{klass}.md"


def _files(root):
    return {
        path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _hashes(*roots):
    result = {}
    for root in roots:
        assert root.is_dir(), root
        for path in _files(root):
            result[path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@pytest.mark.parametrize(
    ("before", "after", "suggested", "confidence"),
    [
        pytest.param(CLAIM, CHANGED_TAG, "provenance", "proved", id="tag-only"),
        pytest.param(
            "The  example value is 3.",
            "The example value is 3",
            "style",
            "proved",
            id="whitespace-and-punctuation",
        ),
        pytest.param(CLAIM, CHANGED_CLAIM, None, "none", id="changed-claim"),
    ],
)
def test_classify_reports_only_changes_the_text_proves(
    workspace, tmp_path, before, after, suggested, confidence
):
    diff = _write_diff(tmp_path, before, after)

    result = _run(workspace, "classify", "--diff", str(diff))

    classification = json.loads(result.stdout)
    assert classification["suggested"] == suggested
    assert classification["confidence"] == confidence


def test_classify_recognizes_tag_templates_from_config(workspace, tmp_path):
    cfg_file, _ = workspace
    cfg_file.write_text(
        "schema_version = 1\n"
        "[[tiers.tier]]\n"
        "id = 1\n"
        'label = "L"\n'
        'tag_template = "[R1:{x}]"\n',
        encoding="utf-8",
    newline="\n")
    diff = _write_diff(
        tmp_path,
        "The example value is 3. [R1:a]",
        "The example value is 3. [R1:b]",
    )

    result = _run(workspace, "classify", "--diff", str(diff))

    classification = json.loads(result.stdout)
    assert classification["suggested"] == "provenance"
    assert classification["confidence"] == "proved"


def test_ingest_requires_a_class_for_a_changed_claim(workspace, tmp_path):
    _, data = workspace
    run = _write_run(data)
    before = run.read_bytes()
    diff = _write_diff(tmp_path)

    result = _run(
        workspace,
        "ingest",
        "--run", str(run),
        "--diff", str(diff),
        "--item-id", "p3-12",
        expected=2,
    )

    assert "no class" in result.stderr.lower()
    assert run.read_bytes() == before


def test_ingest_requires_a_skill_for_steered_style_changes(workspace, tmp_path):
    _, data = workspace
    run = _write_run(data)
    before = run.read_bytes()
    diff = _write_diff(
        tmp_path, "The  example value is 3.", "The example value is 3"
    )

    result = _run(
        workspace,
        "ingest",
        "--run", str(run),
        "--diff", str(diff),
        "--item-id", "p3-12",
        "--class", "style",
        "--steered-run-wrong",
        expected=2,
    )

    assert "--skill" in result.stderr
    assert run.read_bytes() == before


@pytest.mark.parametrize("reference", ["path", "module-id"])
def test_ingest_records_evidence_using_a_path_or_module_id(
    workspace, tmp_path, reference
):
    _, data = workspace
    run = _write_run(data)
    diff = _write_diff(tmp_path)
    run_arg = str(run) if reference == "path" else "c1-r101"

    result = _run(
        workspace,
        "ingest",
        "--run", run_arg,
        "--diff", str(diff),
        "--item-id", "p3-12",
        "--class", "factual",
        "--skill", "lt-module-answer",
        "--steered-run-wrong",
    )

    entries = json.loads(run.read_text(encoding="utf-8"))["diff_classification"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["class"] == "factual"
    assert entry["item_id"] == "p3-12"
    assert entry["before"] == CLAIM
    assert entry["after"] == CHANGED_CLAIM
    assert entry["steered_run_wrong"] is True
    assert "[skill:lt-module-answer]" in entry["note"]
    assert (
        "schema ok" in result.stdout
        or "the schema check was skipped" in result.stdout
    )


def test_ingest_reports_an_override_of_a_proved_class(workspace, tmp_path):
    _, data = workspace
    run = _write_run(data)
    diff = _write_diff(tmp_path, CLAIM, CHANGED_TAG)

    result = _run(
        workspace,
        "ingest",
        "--run", str(run),
        "--diff", str(diff),
        "--item-id", "p3-12",
        "--class", "factual",
    )

    assert "overrides a proved classification" in result.stdout
    entries = json.loads(run.read_text(encoding="utf-8"))["diff_classification"]
    assert entries[0]["class"] == "factual"


def test_ingest_dry_run_writes_nothing(workspace, tmp_path):
    _, data = workspace
    run = _write_run(data)
    diff = _write_diff(tmp_path)
    before = {path: path.read_bytes() for path in _files(tmp_path)}

    result = _run(
        workspace,
        "ingest",
        "--run", str(run),
        "--diff", str(diff),
        "--item-id", "p3-12",
        "--class", "factual",
        "--dry-run",
    )

    assert "dry run:" in result.stdout
    assert _files(tmp_path) == set(before)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_propose_holds_multiple_changes_from_one_run(workspace):
    _, data = workspace
    _write_run(
        data,
        entries=[_entry(item_id="p1-1"), _entry(item_id="p1-2")],
    )

    result = _run(workspace, "propose", "--dry-run")

    assert "held" in result.stdout
    assert "1 run(s), needs 2" in result.stdout
    assert "PROPOSAL" not in result.stdout


def test_propose_names_both_runs_and_the_default_benchmark(workspace):
    _, data = workspace
    _seed_pair(data)

    result = _run(workspace, "propose", "--dry-run")

    assert "PROPOSAL  lt-module-answer  (factual)" in result.stdout
    assert "c1-r101" in result.stdout
    assert "c1-r102" in result.stdout
    assert BENCHMARK in result.stdout


def test_propose_counts_unsteered_changes_without_proposing_them(workspace):
    _, data = workspace
    _seed_pair(data, steered=False)

    result = _run(workspace, "propose", "--dry-run")

    assert "2 classified change(s), 0 marked steered_run_wrong" in result.stdout
    assert "PROPOSAL" not in result.stdout


def test_propose_writes_evidence_and_leaves_the_rule_for_review(workspace):
    _, data = workspace
    _seed_pair(data)

    result = _run(workspace, "propose")

    proposal = _proposal_path(data)
    assert "PROPOSAL  lt-module-answer  (factual)" in result.stdout
    assert str(proposal) in result.stdout
    text = proposal.read_text(encoding="utf-8")
    for expected in (
        CLAIM,
        CHANGED_CLAIM,
        "status: proposed",
        "c1-r101",
        "c1-r102",
        BENCHMARK,
        "Proposed rule",
    ):
        assert expected in text
    rule = text.split("## Proposed rule\n", 1)[1].split("## Evidence", 1)[0]
    assert rule.strip() == (
        "(One sentence that would have prevented every edit below. "
        "Write it after reading them.)"
    )


def test_propose_dry_run_prints_a_proposal_without_writing_files(workspace):
    _, data = workspace
    _seed_pair(data)
    before = {path: path.read_bytes() for path in _files(data)}

    result = _run(workspace, "propose", "--dry-run")

    assert "PROPOSAL  lt-module-answer  (factual)" in result.stdout
    assert not _proposal_path(data).exists()
    assert _files(data) == set(before)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_propose_uses_the_configured_benchmark_cell(workspace):
    cfg_file, data = workspace
    cfg_file.write_text(
        "schema_version = 1\n"
        "[improvement]\n"
        'benchmark_cells = ["lt-module-answer: evals/custom pass rate"]\n',
        encoding="utf-8",
    newline="\n")
    _seed_pair(data)

    result = _run(workspace, "propose")

    assert "evals/custom pass rate" in result.stdout
    text = _proposal_path(data).read_text(encoding="utf-8")
    assert "benchmark cell that must move: evals/custom pass rate" in text
    assert BENCHMARK not in text


def test_firing_propose_preserves_setup_and_shared_files(workspace, tmp_path):
    _, data = workspace
    _seed_pair(data)
    protected = (REPO / "skills/lt-suite-setup", REPO / "shared")
    before_hashes = _hashes(*protected)
    assert before_hashes
    before_files = _files(REPO) | _files(tmp_path)

    result = _run(workspace, "propose")

    assert "PROPOSAL  lt-module-answer  (factual)" in result.stdout
    assert _hashes(*protected) == before_hashes
    after_files = _files(REPO) | _files(tmp_path)
    assert before_files <= after_files
    assert after_files - before_files == {_proposal_path(data)}


@pytest.fixture
def item_diff(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "title:: Example\n"
        "- ## Question one\n"
        "  item:: p1-1\n"
        "\t\t- Claim one as drafted. [T2:ExampleGuide:topic]\n"
        "\t\t\t- Edited reply about ONE.\n"
        "- ## Question two\n"
        "  item:: p2-2\n"
        "\t\t- Claim two as drafted. [T2:ExampleGuide:topic]\n"
        "\t\t\t- Edited reply about TWO.\n"
        "- ## Question three\n"
        "  item:: p3-3\n"
        "\t\t- Claim three, untouched. [T2:ExampleGuide:topic]\n",
        encoding="utf-8",
    newline="\n")
    diff = tmp_path / "page.md.diff"
    diff.write_text(
        f"--- {page} (on disk)\n"
        f"+++ {page} (this run)\n"
        "@@ -4,3 +4,2 @@\n"
        "-\t\t- Claim one as drafted. [T2:ExampleGuide:topic]\n"
        "-\t\t\t- Edited reply about ONE.\n"
        "+\t\t- Claim one as drafted, regenerated. [T2:ExampleGuide:topic]\n"
        " - ## Question two\n"
        "@@ -8,3 +7,2 @@\n"
        "-\t\t- Claim two as drafted. [T2:ExampleGuide:topic]\n"
        "-\t\t\t- Edited reply about TWO.\n"
        "+\t\t- Claim two as drafted, regenerated. [T2:ExampleGuide:topic]\n"
        " - ## Question three\n",
        encoding="utf-8",
    newline="\n")
    return diff


def test_ingest_slices_each_item_to_its_own_changed_lines(workspace, item_diff):
    _, data = workspace
    run = _write_run(data)
    before = run.read_bytes()
    entries = {}

    for item_id, word, marker in (
        ("p1-1", "one", "ONE"),
        ("p2-2", "two", "TWO"),
    ):
        result = _run(
            workspace,
            "ingest",
            "--run", str(run),
            "--diff", str(item_diff),
            "--item-id", item_id,
            "--class", "factual",
            "--skill", "lt-module-answer",
            "--dry-run",
        )
        assert "could not read the exported page" not in result.stdout
        body = result.stdout.split("\ndry run:", 1)[0]
        entry = json.loads(body)
        assert entry["item_id"] == item_id
        assert entry["before"] == (
            f"\t\t- Claim {word} as drafted. [T2:ExampleGuide:topic]\n"
            f"\t\t\t- Edited reply about {marker}."
        )
        assert entry["after"] == (
            f"\t\t- Claim {word} as drafted, regenerated. "
            "[T2:ExampleGuide:topic]"
        )
        entries[item_id] = entry

    assert entries["p1-1"]["before"] != entries["p2-2"]["before"]
    assert run.read_bytes() == before


def test_ingest_refuses_an_untouched_item_and_names_touched_items(
    workspace, item_diff
):
    _, data = workspace
    run = _write_run(data)
    before = run.read_bytes()

    result = _run(
        workspace,
        "ingest",
        "--run", str(run),
        "--diff", str(item_diff),
        "--item-id", "p3-3",
        "--class", "factual",
        "--skill", "lt-module-answer",
        "--dry-run",
        expected=2,
    )

    assert "no changed line inside p3-3" in result.stderr
    assert "items the diff does touch: p1-1, p2-2" in result.stderr
    assert run.read_bytes() == before


def test_classify_reports_no_changed_lines_for_an_untouched_item(
    workspace, item_diff
):
    result = _run(
        workspace,
        "classify",
        "--diff", str(item_diff),
        "--item-id", "p3-3",
    )

    classification = json.loads(result.stdout)
    assert classification["suggested"] is None
    assert classification["confidence"] == "none"
    assert "no changed lines" in classification["why"]


def _applied_args(applied_file):
    return [
        "applied",
        "--applied-file", str(applied_file),
        "--skill", "lt-module-answer",
        "--class", "factual",
        "--items", "p2-2", "p1-1", "p1-1",
        "--date", "2000-01-01",
    ]


def test_applied_list_reports_an_empty_ledger(workspace, tmp_path):
    applied_file = tmp_path / "applied.json"

    result = _run(
        workspace, "applied", "--applied-file", str(applied_file), "--list"
    )

    assert "nothing recorded as applied" in result.stdout
    assert not applied_file.exists()


@pytest.mark.parametrize("missing", ["measured", "decision"])
def test_applied_requires_approval_and_measurement(workspace, tmp_path, missing):
    applied_file = tmp_path / "applied.json"
    args = _applied_args(applied_file)
    if missing != "decision":
        args.extend(["--decision", "approval recorded"])
    if missing != "measured":
        args.extend(["--measured", "evals/example pass rate 1/2 to 2/2"])

    result = _run(workspace, *args, expected=3)

    assert f"--{missing} is required" in result.stderr
    assert not applied_file.exists()


def test_applied_closes_a_pair_without_changing_run_evidence(workspace, tmp_path):
    _, data = workspace
    runs = _seed_pair(data)
    before_runs = {path: path.read_bytes() for path in runs}
    applied_file = tmp_path / "applied.json"
    ledger_args = ["--applied-file", str(applied_file)]

    before = _run(workspace, "propose", *ledger_args, "--dry-run")
    assert "PROPOSAL  lt-module-answer  (factual)" in before.stdout

    args = [
        *_applied_args(applied_file),
        "--decision", "approval recorded",
        "--measured", "evals/example pass rate 1/2 to 2/2",
    ]
    recorded = _run(workspace, *args)
    assert "recorded lt-module-answer / factual" in recorded.stdout
    assert json.loads(applied_file.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "applied": [
            {
                "skill": "lt-module-answer",
                "class": "factual",
                "items": ["p1-1", "p2-2"],
                "date": "2000-01-01",
                "decision": "approval recorded",
                "measured": "evals/example pass rate 1/2 to 2/2",
            }
        ],
    }

    ledger_bytes = applied_file.read_bytes()
    repeated = _run(workspace, *args)
    assert "already recorded" in repeated.stdout
    assert applied_file.read_bytes() == ledger_bytes

    closed = _run(workspace, "propose", *ledger_args, "--dry-run")
    assert "closed lt-module-answer / factual" in closed.stdout
    assert "No OUTSTANDING proposal" in closed.stdout
    assert "PROPOSAL" not in closed.stdout

    shown = _run(
        workspace, "propose", *ledger_args, "--show-applied", "--dry-run"
    )
    assert "PROPOSAL  lt-module-answer  (factual)" in shown.stdout

    listed = _run(workspace, "applied", *ledger_args, "--list")
    assert "lt-module-answer  (factual)  applied 2000-01-01" in listed.stdout
    assert "approval recorded" in listed.stdout
    assert "evals/example pass rate 1/2 to 2/2" in listed.stdout
    assert all(path.read_bytes() == content for path, content in before_runs.items())


def test_applied_does_not_close_a_different_class(workspace, tmp_path):
    _, data = workspace
    for module_id, item_id in (
        ("c1-r101", "p1-1"),
        ("c1-r102", "p2-2"),
    ):
        _write_run(
            data,
            module_id,
            [_entry("factual", item_id), _entry("provenance", item_id)],
        )
    applied_file = tmp_path / "applied.json"
    _run(
        workspace,
        *_applied_args(applied_file),
        "--decision", "approval recorded",
        "--measured", "evals/example pass rate 1/2 to 2/2",
    )

    result = _run(
        workspace,
        "propose",
        "--applied-file", str(applied_file),
        "--dry-run",
    )

    assert "closed lt-module-answer / factual" in result.stdout
    assert "PROPOSAL  lt-module-answer  (factual)" not in result.stdout
    assert "closed lt-module-answer / provenance" not in result.stdout
    assert "PROPOSAL  lt-module-answer  (provenance)" in result.stdout


@pytest.mark.parametrize(("before", "after"), [
    ("Value < 5", "Value > 5"),
    ("Value <= 5", "Value < 5"),
    ("Value = 5", "Value != 5"),
    ("Value + 5", "Value - 5"),
    ("Value * 5", "Value / 5"),
    ("Value -5", "Value 5"),
    ("Value +5", "Value 5"),
    ("Value −5", "Value +5"),
    ("Value ≥ 5", "Value ≤ 5"),
    ("Value 1.5", "Value 15"),
    ("Value 5%", "Value 5"),
])
def test_classify_requires_human_for_math_changes(workspace, tmp_path, before, after):
    # Punctuation normalization must preserve mathematical meaning.
    diff = _write_diff(tmp_path, before, after)
    result = _run(workspace, "classify", "--diff", str(diff))
    classification = json.loads(result.stdout)
    assert classification["suggested"] is None
    assert classification["confidence"] == "none"


def test_ingest_math_change_requires_human_class(workspace, tmp_path):
    _, data = workspace
    run = _write_run(data)
    before = run.read_bytes()
    diff = _write_diff(tmp_path, "Value < 5", "Value > 5")
    _run(workspace, "ingest", "--run", str(run), "--diff", str(diff),
         "--item-id", "p1-1", expected=2)
    assert run.read_bytes() == before
