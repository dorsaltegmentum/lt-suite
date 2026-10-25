# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approval scope, captured item facts, and save receipts survive handoff validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import approvals, validate
from ltcore.digest import question_digest

EX = Path(__file__).resolve().parents[1] / "fixtures" / "examples"
T0 = "2000-01-01T00:00:00Z"


def example(name):
    return json.loads((EX / f"{name}.example.json").read_text(encoding="utf-8"))


def test_report_cannot_override_captured_single_attempt():
    # A report claiming multi-attempt must still require the captured single approval.
    mod, report = example("module"), example("fill-report")
    cap = next(i for p in mod["pages"] for i in p["items"] if i["item_id"] == "i-text-1")
    cap["attempts"] = "single"
    row = next(i for i in report["items"] if i["item_id"] == "i-text-1")
    row["attempts"] = "multi"
    res = validate.validate_doc(report, "fill-report", module=mod, base_dir=EX)
    assert res.exit_code == 1
    assert any("approval_id" in e for e in res.rail_errors)


@pytest.mark.parametrize("schema", ["answers", "fill-report"])
def test_cross_check_refuses_another_module(schema):
    doc, mod = example(schema), example("module")
    mod["module_id"] = "c1-r102"
    res = validate.validate_doc(doc, schema, module=mod, base_dir=EX)
    assert res.exit_code == 1
    assert any("module_id" in e for e in res.rail_errors)


def test_report_single_approval_is_bound_to_the_module(tmp_path):
    report = example("fill-report")
    row = next(i for i in report["items"] if i["item_id"] == "i-text-1")
    row["attempts"] = "single"
    p = tmp_path / "approvals.json"
    rec = approvals.append(p, "c1-r102", row["item_id"], "Yes, write this item.", recorded_at=T0,
                           question_digest=question_digest(example("module")))
    row["approval_id"] = rec["approval_id"]
    res = validate.validate_doc(report, "fill-report", approvals=approvals.load(p))
    assert res.exit_code == 1
    assert any("module" in e for e in res.rail_errors)


def test_parsed_approvals_receive_the_same_integrity_check_as_a_file(tmp_path):
    p = tmp_path / "approvals.json"
    approvals.append(p, "c1-r101", "i-text-1", "Yes, write this item.", recorded_at=T0,
                     question_digest=question_digest(example("module")))
    doc = approvals.load(p)
    doc["approvals"][0]["quote"] = "Different words."
    report = example("fill-report")
    res = validate.validate_doc(report, "fill-report", approvals=doc)
    assert res.exit_code == 3
    assert "does not match its record" in res.fatal


@pytest.mark.parametrize("field,value", [
    ("recorded_at", "2000-02-31T00:00:00Z"),
    ("item_id", 12),
    ("item_ids", [12]),
    ("item_ids", ["p3-12", "p3-12"]),
    ("note", 12),
])
def test_approvals_loader_refuses_invalid_record_fields(tmp_path, field, value):
    # The stdlib loader must enforce the schema even when jsonschema is unavailable.
    p = tmp_path / "approvals.json"
    approvals.append(p, "c1-r101", "p3-12", "Yes.", recorded_at=T0,
                     question_digest=question_digest(example("module")))
    doc = json.loads(p.read_text(encoding="utf-8"))
    rec = doc["approvals"][0]
    rec[field] = value
    rec["approval_id"] = approvals.approval_id(
        doc["module_id"], rec.get("item_id"), rec["quote"], rec["recorded_at"],
        question_digest=rec["question_digest"], item_ids=rec.get("item_ids"), note=rec.get("note"))
    p.write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    with pytest.raises(approvals.ApprovalError):
        approvals.load(p)


def test_different_waiver_scopes_receive_different_ids(tmp_path):
    p = tmp_path / "approvals.json"
    first = approvals.append(p, "c1-r101", None, "Yes.", kind="waiver",
                             rail="write-past-first-door", item_ids=["p3-12"], recorded_at=T0,
                             question_digest=question_digest(example("module")))
    second = approvals.append(p, "c1-r101", None, "Yes.", kind="waiver",
                              rail="write-past-first-door", item_ids=["p3-13"], recorded_at=T0,
                              question_digest=question_digest(example("module")))
    assert first["approval_id"] != second["approval_id"]
    assert approvals.load(p)["approvals"] == [first, second]


@pytest.mark.parametrize("signal", ["model", "net", "fallback", "unconfirmed", "error"])
def test_fill_report_accepts_browser_save_waits(signal):
    report = example("fill-report")
    report["save_waits"] = [{
        "receipt": "fill-result.json", "receipt_sha256": "0" * 64, "pages": [3, 4],
        "save_signal": signal, "waited_ms": 4200, "keys_total": 2,
        "keys_saved": None, "keys_pending": None, "save_errors": 0,
        "calibrated": False, "ok": True, "fatal": None,
    }]
    assert validate.validate_doc(report, "fill-report", base_dir=EX).ok


def test_fill_report_refuses_unknown_save_signal():
    report = example("fill-report")
    report["save_waits"] = [{"pages": [3], "save_signal": "guessed", "waited_ms": 1}]
    assert validate.validate_doc(report, "fill-report").exit_code == 2
