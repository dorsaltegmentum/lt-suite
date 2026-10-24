# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Approval records cannot gain a new scope while retaining their recorded ID."""
from __future__ import annotations

import json
import copy

import pytest

from ltcore import approvals
from ltcore.digest import question_digest


@pytest.mark.parametrize("field,value", [
    ("kind", "single-attempt"),
    ("rail", "passed-single-approval"),
    ("item_ids", ["other-item"]),
    ("item_id", "other-item"),
    ("question_digest", "b" * 64),
    ("note", "A broader approval."),
])
def test_editing_approval_scope_invalidates_its_id(tmp_path, field, value):
    # An edited waiver must fail integrity checks before it can cover another write.
    path = tmp_path / "approvals.json"
    approvals.append(path, "demo", "item-one", "Yes, for this item.", kind="waiver",
                     rail="write-past-first-door", item_ids=["item-one"],
                     recorded_at="2000-01-01T00:00:00Z", question_digest="a" * 64)
    doc = json.loads(path.read_text())
    doc["approvals"][0][field] = value
    path.write_text(json.dumps(doc))
    with pytest.raises(approvals.ApprovalError, match="does not match its record"):
        approvals.load(path)


def test_waiver_cannot_cover_an_item_outside_its_recorded_scope(tmp_path):
    # Reversing the item_ids guard must expose the unauthorized item as a refusal.
    path = tmp_path / "approvals.json"
    rec = approvals.append(path, "demo", None, "Yes, for item-one.", kind="waiver",
                           rail="write-past-first-door", item_ids=["item-one"],
                           recorded_at="2000-01-01T00:00:00Z", question_digest="a" * 64)
    doc = approvals.load(path)
    assert approvals.check_waiver(doc, "demo", rec["approval_id"],
                                  "write-past-first-door", ["item-one"],
                                  question_digest="a" * 64) == []
    assert any("does not cover" in p for p in approvals.check_waiver(
        doc, "demo", rec["approval_id"], "write-past-first-door", ["other-item"],
        question_digest="a" * 64))


def bound_approval(tmp_path):
    module = {"module_id": "demo", "pages": [{"page_index": 1, "items": [
        {"item_id": "one", "type": "text-response", "attempts": "single",
         "prompt_text": "Explain the original question."}]}]}
    path = tmp_path / "approvals.json"
    rec = approvals.append(path, "demo", "one", "Yes, write this answer.",
                           question_digest=question_digest(module))
    answers = {"module_id": "demo", "items": [{"item_id": "one",
        "single_attempt_approval": {"approved_by": "user", "approval_id": rec["approval_id"]}}]}
    return module, answers, path


def test_changed_capture_cannot_replay_an_approval(tmp_path):
    module, answers, path = bound_approval(tmp_path)
    assert "one" in approvals.check(module, answers, path)
    module["pages"][0]["items"][0]["prompt_text"] = "A different question."
    with pytest.raises(approvals.ApprovalError, match="question_digest"):
        approvals.check(module, answers, path)


def test_answers_from_another_module_cannot_replay_an_approval(tmp_path):
    module, answers, path = bound_approval(tmp_path)
    answers["module_id"] = "another-module"
    with pytest.raises(approvals.ApprovalError, match="module_id"):
        approvals.check(module, answers, path)


def test_state_only_recapture_keeps_approval_valid(tmp_path):
    module, answers, path = bound_approval(tmp_path)
    module = copy.deepcopy(module)
    module["pages"][0]["items"][0]["answered"] = True
    assert "one" in approvals.check(module, answers, path)


def test_recording_an_approval_requires_a_capture_digest(tmp_path):
    with pytest.raises(approvals.ApprovalError, match="question_digest"):
        approvals.append(tmp_path / "approvals.json", "demo", "one", "Yes.")


def test_unbound_legacy_approval_fails_closed(tmp_path):
    module, answers, path = bound_approval(tmp_path)
    doc = json.loads(path.read_text())
    rec = doc["approvals"][0]
    rec.pop("question_digest")
    rec["approval_id"] = approvals.approval_id("demo", "one", rec["quote"], rec["recorded_at"])
    answers["items"][0]["single_attempt_approval"]["approval_id"] = rec["approval_id"]
    path.write_text(json.dumps(doc))
    with pytest.raises(approvals.ApprovalError, match="question_digest"):
        approvals.check(module, answers, path)


def test_removing_the_waiver_item_scope_invalidates_its_id(tmp_path):
    path = tmp_path / "approvals.json"
    approvals.append(path, "demo", None, "Yes, for one item.", kind="waiver",
                     rail="write-past-first-door", item_ids=["one"], question_digest="a" * 64)
    doc = json.loads(path.read_text())
    doc["approvals"][0].pop("item_ids")
    path.write_text(json.dumps(doc))
    with pytest.raises(approvals.ApprovalError, match="does not match its record"):
        approvals.load(path)


def test_waiver_verification_requires_the_current_capture_digest(tmp_path):
    path = tmp_path / "approvals.json"
    rec = approvals.append(path, "demo", None, "Yes, for one item.", kind="waiver",
                           rail="write-past-first-door", item_ids=["one"], question_digest="a" * 64)
    assert any("question_digest" in p for p in approvals.check_waiver(
        approvals.load(path), "demo", rec["approval_id"], "write-past-first-door", ["one"]))
