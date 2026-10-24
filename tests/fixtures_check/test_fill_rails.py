# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fill-report fixtures trip the fill rails when they are changed, and pass when they
are not. Each case starts from a fixture that validates, makes one change, and states
whether the validator must still pass (0), reject on a rail (1) or reject on the schema (2).

Only exit codes are asserted: the wording of a message belongs to tests/rails.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FIX, STEMS, apply_ops, read_json  # noqa: E402

TS = "2000-01-01T00:00:00Z"


def report(mid):
    return read_json(f"fill-reports/fill-report-{STEMS[mid]}.json")


def module(mid):
    return read_json(f"modules/module-{STEMS[mid]}.json")


def item(rep, iid):
    return next(i for i in rep["items"] if i["item_id"] == iid)


def fill(rep, iid, **over):
    """Make an item a verified write with a matching receipt."""
    it = item(rep, iid)
    it.pop("todo", None)
    it.pop("stopped_reason", None)
    it.update(attempted=True, method="view-model", result="filled", verified=True,
              receipt={"expected_hash": 7, "observed_hash": 7, "observed_at": TS})
    it.update(over)


# ---- r104: the committed run, auto approval, two waivers ---------------------------------

def r104_drop_waiver(i):
    return lambda r: r["user_decisions"].pop(i)


def r104_idle_item(r):
    r["user_decisions"][1]["item_ids"].append("p3-13")


def r104_manual_keeps_waivers(r):
    r["approval_mode"] = "manual"


def r104_manual_no_waivers(r):
    r["approval_mode"] = "manual"
    r.pop("user_decisions")


def r104_write_read_only(r):
    fill(r, "p3-13")


def r104_submitted_single_written(r):
    fill(r, "p3-12")


def r104_single_not_listed(r):
    r["items"] = [i for i in r["items"] if i["item_id"] != "p3-12"]


def r104_single_attempted(r):
    item(r, "p3-12").update(attempted=True)


def r104_report_page_filled(r):
    r["pages_filled"] = [9, 10]


def r104_receipt_differs(r):
    item(r, "p9-71")["receipt"]["observed_hash"] += 1


def r104_failed_over_matching_receipt(r):
    item(r, "p9-71").update(result="failed", verified=False)


def r104_receipt_before_start(r):
    r["started_at"] = "2000-01-01T00:00:01Z"


def r104_filled_without_receipt(r):
    item(r, "p9-71").pop("receipt")


def r104_route_not_listed(r):
    r["never_navigated"] = []


def r104_unmapped_stop(r):
    r["boundary_reached"] = {"stopped": True, "class": "unmapped", "detail": "x"}


def r104_stop_without_class(r):
    r["boundary_reached"] = {"stopped": True, "class": None, "detail": "x"}


def r104_bad_approval_format(r):
    r["user_decisions"][0]["approval_id"] = "A-123"


def r104_unverified_filled(r):
    item(r, "p9-71").update(verified=False)
    item(r, "p9-71").pop("receipt")
    r.pop("source_receipts")


CASES = [
    ("r104 passes as generated", "c1-r104", lambda r: None, 0),
    ("r104 without the auto-approval waiver", "c1-r104", r104_drop_waiver(0), 1),
    ("r104 without the passed-single waiver", "c1-r104", r104_drop_waiver(1), 1),
    ("r104 waiver naming an item no violation names", "c1-r104", r104_idle_item, 1),
    ("r104 manual mode leaves both waivers idle", "c1-r104", r104_manual_keeps_waivers, 1),
    ("r104 manual mode without waivers", "c1-r104", r104_manual_no_waivers, 0),
    ("r104 write claimed on a read-only panel", "c1-r104", r104_write_read_only, 1),
    ("r104 submitted single-attempt item written", "c1-r104", r104_submitted_single_written, 1),
    ("r104 submitted single-attempt item left out of the report", "c1-r104", r104_single_not_listed, 1),
    ("r104 submitted single-attempt item marked attempted", "c1-r104", r104_single_attempted, 1),
    ("r104 report page in pages_filled", "c1-r104", r104_report_page_filled, 1),
    ("r104 receipt hashes differ but the item says filled", "c1-r104", r104_receipt_differs, 1),
    ("r104 receipt hashes match but the item says failed", "c1-r104", r104_failed_over_matching_receipt, 1),
    ("r104 receipt taken before the run started", "c1-r104", r104_receipt_before_start, 1),
    ("r104 filled item with no receipt in a receipt-built report", "c1-r104", r104_filled_without_receipt, 1),
    ("r104 completion route not in never_navigated", "c1-r104", r104_route_not_listed, 1),
    ("r104 stopped on class unmapped", "c1-r104", r104_unmapped_stop, 1),
    ("r104 stopped with no door class", "c1-r104", r104_stop_without_class, 1),
    ("r104 waiver id of the wrong shape", "c1-r104", r104_bad_approval_format, 2),
    ("r104 filled item that is not verified", "c1-r104", r104_unverified_filled, 1),
]


@pytest.mark.parametrize("label,mid,mutate,want", CASES, ids=[c[0] for c in CASES])
def test_fill_report_cases(validate, label, mid, mutate, want):
    rep = report(mid)
    mutate(rep)
    res = validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=module(mid), model_answer_read=True)
    assert res.exit_code == want, (label, res.schema_errors, res.rail_errors)


def test_the_two_waivers_are_counted(validate):
    res = validate.validate_doc(report("c1-r104"), "fill-report", base_dir=FIX / "fill-reports", module=module("c1-r104"),
                                model_answer_read=True)
    assert res.exit_code == 0 and len(res.waived) == 2


def test_waiver_ids_are_checked_against_an_approvals_file(validate):
    approvals = {"schema_version": 1, "module_id": "c1-r104", "approvals": []}
    res = validate.validate_doc(report("c1-r104"), "fill-report", base_dir=FIX / "fill-reports", module=module("c1-r104"),
                                approvals=approvals, model_answer_read=True)
    assert res.exit_code == 1, "waivers that rest on no recorded approval must fail"


# ---- r102: single-attempt writes and the model-answer policy -----------------------------

def test_single_attempt_write_needs_an_approval_id(validate):
    mod = module("c1-r102")
    rep = report("c1-r102")
    fill(rep, "p3-21")
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, model_answer_read=True).exit_code == 1
    approvals_mod = pytest.importorskip("ltcore.approvals")
    quote = "Yes, write p3-21 as drafted."
    from ltcore.digest import question_digest
    qdigest = question_digest(mod)
    aid = approvals_mod.approval_id("c1-r102", "p3-21", quote, TS, question_digest=qdigest)
    item(rep, "p3-21")["approval_id"] = aid
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, model_answer_read=True).exit_code == 0
    rec = {"approval_id": aid, "kind": "single-attempt", "item_id": "p3-21", "approved_by": "user",
           "quote": quote, "recorded_at": TS, "question_digest": qdigest}
    good = {"schema_version": 1, "module_id": "c1-r102", "approvals": [rec]}
    stale = {"schema_version": 1, "module_id": "c1-r102",
             "approvals": [dict(rec, item_id="p3-22")]}
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, approvals=stale,
                                 model_answer_read=True).exit_code == 3
    wrong_item = {"schema_version": 1, "module_id": "c1-r102",
                  "approvals": [dict(rec, item_id="p3-22", approval_id=approvals_mod.approval_id(
                      "c1-r102", "p3-22", quote, TS, question_digest=qdigest))]}
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, approvals=good,
                                 model_answer_read=True).exit_code == 0
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, approvals=wrong_item,
                                 model_answer_read=True).exit_code == 1


def test_lt_conflict_follows_the_model_answer_policy(validate):
    mod, rep = module("c1-r102"), report("c1-r102")
    assert item(rep, "p3-22")["lt_conflict"]["observed_via"] == "model-answer"
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, model_answer_read=True).exit_code == 0
    assert validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, model_answer_read=False).exit_code == 1


def test_a_todo_item_needs_its_todo_block(validate):
    rep = report("c1-r102")
    item(rep, "p3-23").pop("todo")
    res = validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=module("c1-r102"), model_answer_read=True)
    assert res.exit_code == 2


def test_a_drag_item_with_one_attempt_is_never_automated(validate):
    rep = report("c1-r103")
    fill(rep, "p4-31", attempts="single", approval_id="A-0000aaaa")
    res = validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=module("c1-r103"), model_answer_read=True)
    assert res.exit_code == 1


def test_a_locked_run_takes_no_write_claims(validate):
    rep = report("c1-r101")
    fill(rep, "p2-11")
    res = validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=module("c1-r101"), model_answer_read=True)
    assert res.exit_code == 1


# ---- r106: the first stop, and a waiver that names the written items ---------------------

def test_pages_filled_may_not_reach_the_checkpoint_page(validate):
    rep = report("c1-r106")
    rep["pages_filled"] = [2, 3]
    res = validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=module("c1-r106"), model_answer_read=True)
    assert res.exit_code == 1


def moved_behind_a_checkpoint():
    """c1-r106 with a live checkpoint before page 2: the two grids now sit past the stop."""
    return apply_ops(module("c1-r106"),
                     [{"op": "insert_checkpoint_page", "before_page_index": 2, "index": 6,
                       "committed": False}])


def waiver(item_ids):
    return {"rail": "write-past-first-door", "item_ids": item_ids, "who": "the user", "when": TS,
            "why": "The user accepted writes past the first checkpoint for these two grids.",
            "approval_id": "A-12345678"}


def test_writes_past_the_first_door_fail_unless_waived_item_by_item(validate):
    mod = moved_behind_a_checkpoint()
    rep = report("c1-r106")
    rep["pages_filled"] = []

    def run(r):
        return validate.validate_doc(r, "fill-report", base_dir=FIX / "fill-reports", module=mod, model_answer_read=True)
    assert run(rep).exit_code == 1
    both = copy.deepcopy(rep)
    both["user_decisions"] = [waiver(["p2-11", "p2-12"])]
    res = run(both)
    assert res.exit_code == 0 and len(res.waived) == 2
    one = copy.deepcopy(rep)
    one["user_decisions"] = [waiver(["p2-11"])]
    assert run(one).exit_code == 1, "the second write has no waiver"
    extra = copy.deepcopy(both)
    extra["user_decisions"][0]["item_ids"].append("p2-99")
    assert run(extra).exit_code == 1, "a waiver for an item nothing violates"


def test_a_failed_write_past_the_door_counts(validate):
    mod = moved_behind_a_checkpoint()
    rep = report("c1-r106")
    rep["pages_filled"] = []
    for iid in ("p2-11", "p2-12"):
        item(rep, iid).update(result="failed", verified=False)
        item(rep, iid).pop("receipt")
    rep.pop("source_receipts")
    res = validate.validate_doc(rep, "fill-report", base_dir=FIX / "fill-reports", module=mod, model_answer_read=True)
    assert res.exit_code == 1
