# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.approvals: the approvals file is written once per reply and checked strictly.

check() refuses when an approval id is missing from the file, names another item,
belongs to another module, or is not a single-attempt approval; the CLI exits 3.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import approvals
from ltcore.digest import question_digest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "approvals" / "approvals-c1-r104.json"
T0 = "2000-01-01T00:00:00Z"


def fixture_doc():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def module(module_id="c1-r104", single=("p3-12",), multi=("p3-11",)):
    if module_id == "c1-r104" and single == ("p3-12",) and multi == ("p3-11",):
        return json.loads((REPO / "tests/fixtures/modules/module-c1-r104.json").read_text(encoding="utf-8"))
    items = [{"item_id": i, "type": "text-response", "attempts": "single"} for i in single]
    items += [{"item_id": i, "type": "text-response", "attempts": "multi"} for i in multi]
    return {"module_id": module_id, "pages": [{"page_index": 3, "items": items}]}


def answers(*pairs, module_id="c1-r104"):
    return {"module_id": module_id,
            "items": [{"item_id": iid, "status": "drafted",
                       "single_attempt_approval": {"approved_by": "user", "approval_id": aid}}
                      for iid, aid in pairs]}


FIX_ID = fixture_doc()["approvals"][0]["approval_id"]
QDIGEST = fixture_doc()["approvals"][0]["question_digest"]


def test_id_is_the_documented_hash():
    want = "A-4f004ddd"
    assert approvals.approval_id("c1-r104", "p3-12", "Yes, write p3-12 as drafted.", T0,
                                 question_digest=QDIGEST) == want
    assert FIX_ID == want
    assert approvals.APPROVAL_ID_RE.match(want)


def test_fixture_is_well_formed():
    doc = fixture_doc()
    assert approvals.problems(doc) == []
    assert doc["module_id"] == "c1-r104" and len(doc["approvals"]) == 1
    assert doc["approvals"][0]["kind"] == "single-attempt"


def test_append_creates_the_file_and_is_idempotent(tmp_path):
    p = tmp_path / "runs" / "c1-r104" / "approvals.json"
    r1 = approvals.append(p, "c1-r104", "p3-12", "Yes, write p3-12 as drafted.", recorded_at=T0,
                          question_digest=QDIGEST)
    r2 = approvals.append(p, "c1-r104", "p3-12", "Yes, write p3-12 as drafted.", recorded_at=T0,
                          question_digest=QDIGEST)
    assert r1 == r2 and r1["approval_id"] == FIX_ID
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert len(doc["approvals"]) == 1 and approvals.problems(doc) == []
    assert json.loads(p.read_text(encoding="utf-8")) == fixture_doc()


def test_append_defaults_to_now_and_keeps_earlier_records(tmp_path):
    p = tmp_path / "approvals.json"
    approvals.append(p, "c1-r104", "p3-12", "Yes.", recorded_at=T0, question_digest=QDIGEST)
    rec = approvals.append(p, "c1-r104", None, "Run it on auto.", kind="waiver",
                           rail="approval-manual-at-boundary", question_digest=QDIGEST)
    assert rec["recorded_at"].endswith("Z") and rec["kind"] == "waiver" and "item_id" not in rec
    assert len(json.loads(p.read_text(encoding="utf-8"))["approvals"]) == 2


@pytest.mark.parametrize("kwargs,want", [
    ({"quote": " "}, "quote is empty"),
    ({"kind": "other"}, "kind 'other'"),
    ({"item_id": None}, "names its item"),
    ({"kind": "waiver", "rail": "filled-without-verified"}, "a waiver names one rail"),
])
def test_append_refuses_bad_records(tmp_path, kwargs, want):
    args = {"item_id": "p3-12", "quote": "Yes.", "kind": "single-attempt", "rail": None}
    args.update(kwargs)
    with pytest.raises(approvals.ApprovalError, match=want):
        approvals.append(tmp_path / "a.json", "c1-r104", args.pop("item_id"), args.pop("quote"),
                         **args)


def test_append_refuses_a_file_for_another_module(tmp_path):
    p = tmp_path / "approvals.json"
    p.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    with pytest.raises(approvals.ApprovalError, match="not 'c1-r101'"):
        approvals.append(p, "c1-r101", "p3-12", "Yes.", question_digest=QDIGEST)


def test_problems_catch_an_edited_record_and_a_duplicate():
    doc = fixture_doc()
    doc["approvals"][0]["item_id"] = "p3-13"
    assert any("does not match its record" in p for p in approvals.problems(doc))
    doc = fixture_doc()
    doc["approvals"].append(dict(doc["approvals"][0]))
    assert any("appears twice" in p for p in approvals.problems(doc))
    assert approvals.problems([]) == ["the approvals file is not a JSON object"]


def test_check_passes_with_the_fixture():
    got = approvals.check(module(), answers(("p3-12", FIX_ID)), FIXTURE)
    assert list(got) == ["p3-12"] and got["p3-12"]["quote"] == "Yes, write p3-12 as drafted."


def test_check_with_no_approvals_asked_for_needs_no_file():
    assert approvals.check(module(), {"items": [{"item_id": "p3-12"}]}, None) == {}


@pytest.mark.parametrize("label,mod,ans,want", [
    ("missing id", module(), answers(("p3-12", "A-00000000")), "is not in the approvals file"),
    ("wrong item", module(single=("p3-12", "p3-13")), answers(("p3-13", FIX_ID)),
     "approves item 'p3-12', not 'p3-13'"),
    ("wrong module", module(module_id="c1-r101"), answers(("p3-12", FIX_ID), module_id="c1-r101"),
     "holds approvals for module 'c1-r104', not 'c1-r101'"),
    ("not single-attempt in the capture", module(), answers(("p3-11", FIX_ID)),
     "does not mark single-attempt"),
    ("malformed id", module(), answers(("p3-12", "decision-4")), "does not match ^A-"),
])
def test_check_refuses(label, mod, ans, want):
    with pytest.raises(approvals.ApprovalError) as e:
        approvals.check(mod, ans, FIXTURE)
    assert any(want in p for p in e.value.problems), (label, e.value.problems)


def test_check_refuses_a_waiver_used_as_a_single_attempt_approval(tmp_path):
    p = tmp_path / "approvals.json"
    w = approvals.append(p, "c1-r104", "p3-12", "Run it on auto.", kind="waiver",
                         rail="approval-manual-at-boundary", recorded_at=T0,
                         question_digest=QDIGEST)
    with pytest.raises(approvals.ApprovalError, match="is a waiver approval, not a single-attempt"):
        approvals.check(module(), answers(("p3-12", w["approval_id"])), p)


def test_check_refuses_approved_by_anyone_but_the_user():
    ans = answers(("p3-12", FIX_ID))
    ans["items"][0]["single_attempt_approval"]["approved_by"] = "agent"
    with pytest.raises(approvals.ApprovalError, match="approved_by must be 'user'"):
        approvals.check(module(), ans, FIXTURE)


def test_check_refuses_without_a_file(tmp_path):
    with pytest.raises(approvals.ApprovalError, match="no approvals file"):
        approvals.check(module(), answers(("p3-12", FIX_ID)), tmp_path / "absent.json")
    with pytest.raises(approvals.ApprovalError, match="no approvals file was given"):
        approvals.check(module(), answers(("p3-12", FIX_ID)), None)


def test_check_waiver():
    doc = {"schema_version": 1, "module_id": "c1-r101", "approvals": []}
    assert approvals.check_waiver(None, "c1-r101", "A-12345678", "write-past-first-door", None) == []
    assert approvals.check_waiver(None, "c1-r101", "decision-1", "write-past-first-door", None)
    assert "is not in the approvals file" in approvals.check_waiver(
        doc, "c1-r101", "A-12345678", "write-past-first-door", None)[0]


def test_cli_add_check_show(run_py, tmp_path):
    ap = tmp_path / "approvals.json"
    r = run_py("-m", "ltcore.approvals", "add", "--approvals", str(ap), "--module-id", "c1-r104",
               "--item", "p3-12", "--quote", "Yes, write p3-12 as drafted.", "--recorded-at", T0,
               "--question-digest", QDIGEST)
    assert r.returncode == 0 and r.stdout.strip() == FIX_ID, r.stderr
    mp, an = tmp_path / "module.json", tmp_path / "answers.json"
    mp.write_text(json.dumps(module()), encoding="utf-8", newline="\n")
    an.write_text(json.dumps(answers(("p3-12", FIX_ID))), encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.approvals", "check", "--module", str(mp), "--answers", str(an),
               "--approvals", str(ap))
    assert r.returncode == 0 and "1 single-attempt approval(s)" in r.stdout, r.stderr
    an.write_text(json.dumps(answers(("p3-12", "A-00000000"))), encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.approvals", "check", "--module", str(mp), "--answers", str(an),
               "--approvals", str(ap))
    assert r.returncode == 3 and "REFUSED" in r.stderr
    r = run_py("-m", "ltcore.approvals", "show", str(FIXTURE))
    assert r.returncode == 0 and FIX_ID in r.stdout


def test_cli_runs_by_path(run_py, repo_root):
    r = run_py(str(repo_root / "shared" / "ltcore" / "approvals.py"), "show", str(FIXTURE),
               shared_on_path=False)
    assert r.returncode == 0, r.stderr
