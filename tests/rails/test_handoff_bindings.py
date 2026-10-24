# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reject handoffs whose supplied capture, source pins, or item identities disagree."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from ltcore import approvals, validate
from ltcore.digest import question_digest

EXAMPLES = Path(__file__).resolve().parents[1] / "fixtures/examples"


@pytest.fixture
def handoff(tmp_path):
    for source in EXAMPLES.glob("*.json"):
        shutil.copyfile(source, tmp_path / source.name)
    def read(name):
        return json.loads((tmp_path / f"{name}.example.json").read_text())
    return tmp_path, read


@pytest.mark.parametrize("legacy", [False, True])
def test_supplied_capture_must_match_the_bound_questions(handoff, legacy):
    base, read = handoff
    answers, module = read("answers"), read("module")
    if legacy:
        answers["source_module"].pop("question_digest")
    assert validate.validate_doc(answers, "answers", module=module, base_dir=base).ok
    module["pages"][1]["items"][0]["prompt_text"] = "An edited question."
    result = validate.validate_doc(answers, "answers", module=module, base_dir=base)
    assert result.exit_code == 1
    assert any("supplied capture" in error and "question" in error.lower()
               for error in result.rail_errors), result.rail_errors


@pytest.mark.parametrize("supply_module", [False, True])
def test_missing_capture_source_fails_closed(handoff, supply_module):
    base, read = handoff
    answers = read("answers")
    answers["source_module"]["path"] = "absent-module.json"
    result = validate.validate_doc(answers, "answers", base_dir=base,
                                   module=read("module") if supply_module else None)
    assert result.exit_code == 1
    assert any("source_module.path" in error for error in result.rail_errors)


@pytest.mark.parametrize("status", ["filled", "filled-unverified", "failed"])
def test_policy_skips_check_written_items_even_without_a_page_visit(handoff, status):
    base, read = handoff
    report, module = read("fill-report"), read("module")
    row = next(row for row in report["items"] if row["item_id"] == "i-rat-1")
    row.update(attempted=True, method="view-model", result=status, verified=status == "filled")
    result = validate.validate_doc(report, "fill-report", module=module, base_dir=base)
    assert result.exit_code == 1
    assert any("i-rat-1" in error and "standing policy" in error
               for error in result.rail_errors), result.rail_errors


@pytest.mark.parametrize("pin", ["0" * 64, "a" * 64])
def test_fill_report_verifies_the_answer_file_pin(handoff, pin):
    base, read = handoff
    report = read("fill-report")
    assert validate.validate_doc(report, "fill-report", base_dir=base).ok
    report["source_answers"]["sha256"] = pin
    result = validate.validate_doc(report, "fill-report", base_dir=base)
    assert result.exit_code == 1
    assert any("source_answers.sha256" in error for error in result.rail_errors)


def test_missing_answer_source_fails_closed(handoff):
    base, read = handoff
    report = read("fill-report")
    report["source_answers"]["path"] = "absent-answers.json"
    result = validate.validate_doc(report, "fill-report", base_dir=base)
    assert result.exit_code == 1
    assert any("source_answers.path" in error for error in result.rail_errors)


@pytest.mark.parametrize("schema", ["answers", "fill-report"])
def test_handoffs_reject_duplicate_item_ids(handoff, schema):
    base, read = handoff
    doc = read(schema)
    doc["items"].append(copy.deepcopy(doc["items"][0]))
    result = validate.validate_doc(doc, schema, base_dir=base)
    assert result.exit_code == 1
    assert any("duplicate item_id" in error for error in result.rail_errors)


def test_matching_digest_accepts_state_only_recapture(handoff):
    base, read = handoff
    answers, module = read("answers"), read("module")
    module["pages"][1]["items"][0]["answered"] = True
    assert validate.validate_doc(answers, "answers", module=module, base_dir=base).ok


def test_answer_pin_is_checked_on_file_validation(handoff):
    base, read = handoff
    source = base / "answers.example.json"
    source.write_bytes(source.read_bytes() + b"\n")
    result = validate.validate_file(base / "fill-report.example.json")
    assert result.exit_code == 1
    assert any("source_answers.sha256" in error for error in result.rail_errors)


@pytest.mark.parametrize("schema", ["answers", "fill-report"])
def test_validator_rejects_approval_replay_after_questions_change(handoff, schema):
    base, read = handoff
    module, doc = read("module"), read(schema)
    cap = next(i for p in module["pages"] for i in p["items"] if i["item_id"] == "i-text-1")
    cap["attempts"] = "single"
    path = base / "approvals.json"
    rec = approvals.append(path, module["module_id"], cap["item_id"], "Yes, write it.",
                           question_digest=question_digest(module))
    row = next(i for i in doc["items"] if i["item_id"] == cap["item_id"])
    if schema == "answers":
        row["single_attempt_approval"] = {"approved_by": "user", "approval_id": rec["approval_id"]}
    else:
        row["approval_id"] = rec["approval_id"]
    cap["prompt_text"] = "A question the user never approved."
    module_path = base / "module.example.json"
    module_path.write_text(json.dumps(module))
    if schema == "answers":
        doc["source_module"].update(question_digest=question_digest(module),
            sha256=hashlib.sha256(module_path.read_bytes()).hexdigest())
    result = validate.validate_doc(doc, schema, module=module,
                                   approvals=approvals.load(path), base_dir=base)
    assert result.exit_code == 1
    assert any("approval" in error and "question_digest" in error
               for error in result.rail_errors), result.rail_errors


def test_validator_rejects_waiver_replay_after_questions_change(handoff):
    base, read = handoff
    module, report = read("module"), read("fill-report")
    path = base / "approvals.json"
    rec = approvals.append(path, module["module_id"], None, "Yes, use auto approval.",
                           kind="waiver", rail="approval-manual-at-boundary",
                           question_digest=question_digest(module))
    report.update(approval_mode="auto", user_decisions=[{
        "rail": "approval-manual-at-boundary", "who": "user", "when": "2000-01-01",
        "approval_id": rec["approval_id"], "why": "The user approved this supervision mode."}])
    assert validate.validate_doc(report, "fill-report", module=module,
                                  approvals=approvals.load(path), base_dir=base).ok
    module["pages"][1]["items"][0]["prompt_text"] = "Another question."
    result = validate.validate_doc(report, "fill-report", module=module,
                                   approvals=approvals.load(path), base_dir=base)
    assert result.exit_code == 1
    assert any("question_digest" in error for error in result.rail_errors)
