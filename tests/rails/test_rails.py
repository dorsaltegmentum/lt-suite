# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Negative tests for ltcore.validate: every rail gets a document built to trip it.

A rail that has never been seen rejecting a bad document is decoration. Each case
below mutates one schema example in tests/fixtures/examples so that exactly one rule
breaks, runs the validator CLI on it, and asserts the exit code and the reason text.
The rails conftest counts the cases and prints ``<N> rails fired, <M> missed``.

PASS_CASES are the other half: well-formed documents that carry an allowed shape
(a waiver resting on a recorded approval, Lt's model answer with model_answer.read
on, a legacy capture) and must validate.
"""
from __future__ import annotations

import contextlib
import copy
import io
import hashlib
import json
from pathlib import Path

import pytest

from ltcore import approvals, validate
from ltcore.digest import question_digest

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "tests" / "fixtures" / "examples"
APPROVALS_FIXTURE = REPO / "tests" / "fixtures" / "approvals" / "approvals-c1-r104.json"
SCHEMAS = REPO / "shared" / "schemas"

SCHEMA_FAIL, RAIL_FAIL, OK = 2, 1, 0
T0 = "2000-01-01T00:00:00Z"
ORIGIN = "https://lt.example.invalid"


def load(name):
    return json.loads((EX / f"{name}.example.json").read_text(encoding="utf-8"))


def page(doc, idx):
    return next(p for p in doc["pages"] if p["page_index"] == idx)


def item(doc, iid):
    return next(it for p in doc["pages"] for it in p["items"] if it["item_id"] == iid)


def ans(doc, iid):
    return next(a for a in doc["items"] if a["item_id"] == iid)


def fitem(doc, iid):
    return next(it for it in doc["items"] if it["item_id"] == iid)


# --- approvals used by the waiver and single-attempt cases ---------------------------

def _rec(module_id, kind, quote, item_id=None, rail=None, item_ids=None, qdigest=None):
    qdigest = qdigest or question_digest(load("module"))
    r = {"approval_id": approvals.approval_id(module_id, item_id, quote, T0, kind=kind,
         rail=rail, item_ids=item_ids, question_digest=qdigest), "kind": kind,
         "question_digest": qdigest}
    if item_id:
        r["item_id"] = item_id
    if rail:
        r["rail"] = rail
    if item_ids:
        r["item_ids"] = item_ids
    r.update(approved_by="user", quote=quote, recorded_at=T0)
    return r


W_MANUAL = _rec("c1-r101", "waiver", "Yes, run this page on auto approval.",
                rail="approval-manual-at-boundary")
W_DOOR = _rec("c1-r101", "waiver", "Yes, the write past the checkpoint is fine.",
              rail="write-past-first-door", item_ids=["i-cat-1"])
S_TEXT1 = _rec("c1-r101", "single-attempt", "Yes, write i-text-1 as drafted.", item_id="i-text-1")
WAIVER_APPROVALS = {"schema_version": 1, "module_id": "c1-r101",
                    "approvals": [W_MANUAL, W_DOOR, S_TEXT1]}
FIXTURE_APPROVAL_ID = json.loads(APPROVALS_FIXTURE.read_text())["approvals"][0]["approval_id"]


# --- module.json mutations -------------------------------------------------------------

def m_denied_key(d):
    d["census"]["by_type"]["modelAnswer"] = 1
    d["census"]["captured_total"] += 1
    return d


def m_report_page_no(d):
    d["pages"][-1]["page_no"] = 5
    return d


def m_report_items(d):
    d["pages"][-1]["items"] = [copy.deepcopy(item(d, "i-text-1"))]
    return d


def m_census_drift(d):
    d["census"]["answerable_total"] = 8
    return d


def m_subpage_counted(d):
    d["census"]["answerable_total"] = 10
    return d


def m_no_run_commit(d):
    d["commit_boundaries"]["run_level_commit"]["present"] = False
    return d


def m_single_attempt_drag_automated(d):
    it = item(d, "i-cat-1")
    it["automation"] = "resilient-selector"
    # degrade is forbidden on an item that is not degraded; dropping it keeps one rule broken.
    it.pop("degrade", None)
    return d


def m_degrade_class_disagrees(d):
    item(d, "i-unmapped-1")["degrade"]["class"] = "permanent"
    return d


def m_accepts_upload_lies(d):
    item(d, "i-ann-1")["target_geometry"]["accepts_upload"] = True
    return d


def m_degrade_blames_geometry_on_annotate_only(d):
    # A panel that can never take an upload, filed as if it were waiting for a scale.
    it = item(d, "i-ann-1")
    it["automation"] = "degrade-todo"
    it["target_geometry"]["presented_ratio"] = None
    it["target_geometry"]["geometry_source"] = "unavailable-until-presented"
    it["degrade"] = {"reason": "geometry-unknown-until-presented"}
    it["degrade"]["class"] = validate.DEGRADE_CLASS[it["degrade"]["reason"]]
    return d


def m_boundary_map_drift(d):
    item(d, "i-cat-1")["attempts"] = "multi"
    return d


def m_missing_geometry(d):
    del item(d, "i-upann-1")["target_geometry"]
    return d


def m_unknown_geometry_automated(d):
    tg = item(d, "i-upann-1")["target_geometry"]
    tg["presented_ratio"] = None
    tg["geometry_source"] = "unavailable-until-presented"
    item(d, "i-upann-1")["automation"] = "structured-path"
    return d


def m_rat_flag_untrue(d):
    page(d, 4)["rat_candidate"] = {"basis": "all items are multiple-choice"}
    return d


def m_answered_map_says_empty(d):
    # A grid nobody opened reading as answered.
    it = item(d, "i-table-1")
    it["answered"] = True
    it["answered_evidence"] = {"filled_map": ["...", "...", "..."]}
    return d


def m_answered_map_says_filled(d):
    # A completed grid reading as unanswered.
    it = item(d, "i-table-1")
    it["answered"] = False
    it["answered_evidence"] = {"filled_map": ["#..", "#.."]}
    return d


def m_answered_suspect_without_the_shape(d):
    it = item(d, "i-table-1")
    it["answered"] = True
    it["answered_evidence"] = {"filled_map": ["##.", "#.#"],
                               "answered_suspect": {"basis": "only the first body column holds text"}}
    return d


def m_answered_suspect_l_with_a_filled_interior(d):
    # An authored L has an empty interior; one filled interior cell makes it an answer.
    it = item(d, "i-table-1")
    it["answered"] = True
    it["answered_evidence"] = {
        "filled_map": [".##", "##.", "#.."],
        "answered_suspect": {"basis": "every non-empty body cell lies in body row 0 or body column 0"}}
    return d


def m_answered_evidence_on_a_text_item(d):
    item(d, "i-text-1")["answered_evidence"] = {"filled_map": ["#"]}
    return d


def m_authored_mark_without_an_author_grid(d):
    it = item(d, "i-table-1")
    it["answered"] = False
    it["answered_evidence"] = {"filled_map": ["a.", "a."],
                               "author_grid": {"status": "absent", "source": "content.rows"}}
    return d


def m_authored_count_disagrees_with_the_map(d):
    it = item(d, "i-table-1")
    it["answered"] = False
    it["answered_evidence"] = {"filled_map": ["a.", ".."],
                               "author_grid": {"status": "read", "source": "content.rows",
                                               "authored_cells": 2}}
    return d


def m_notebook_reason_on_another_plugin(d):
    item(d, "i-unmapped-1")["degrade"] = {"class": "permanent", "reason": "notebook-never-written"}
    return d


def m_geometry_source_lies(d):
    item(d, "i-upann-1")["target_geometry"]["geometry_source"] = "unavailable-until-presented"
    return d


def m_unmapped_not_degraded(d):
    item(d, "i-unmapped-1")["automation"] = "structured-path"
    return d


def m_naive_timestamp(d):
    d["captured_at"] = "2000-01-01T00:00:00"
    return d


def _with_checkpoint(d, **over):
    """Insert a well-formed Checkpoint page before the Report page and keep every count
    consistent, so the rail under test is the only one that can fire."""
    cp = {"page_index": 5, "page_no": None, "route": "checkpoint", "url": None,
          "title": "Checkpoint", "is_report_page": False, "is_checkpoint": True,
          "checkpoint": {"index": 4, "committed": False}, "content": [], "items": []}
    cp.update(over)
    d["pages"].insert(4, cp)
    d["pages"][-1]["page_index"] = 6
    d["lesson"]["page_count"] = 6
    d["census"]["pages_total"] = 6
    d["commit_boundaries"]["checkpoint_pages"] = [5]
    d["commit_boundaries"]["checkpoints"] = [
        {"index": cp["checkpoint"]["index"], "page_index": cp["page_index"],
         "committed": cp["checkpoint"]["committed"], "in_flow": True, "source": "lessonFlowPages"}]
    d["commit_boundaries"]["checkpoints_container_present"] = True
    return d


def _checkpoint_before_page4(d):
    """A live checkpoint at page_index 4; the old pages 4 and 5 move to 5 and 6."""
    for p in d["pages"]:
        if p["page_index"] >= 4:
            p["page_index"] += 1
            if p.get("page_no") is not None:
                p["page_no"] += 1
    cp = {"page_index": 4, "page_no": None, "route": "checkpoint", "url": None,
          "title": "Checkpoint", "is_report_page": False, "is_checkpoint": True,
          "checkpoint": {"index": 1, "committed": False}, "content": [], "items": []}
    d["pages"].insert(3, cp)
    d["lesson"]["page_count"] = 6
    d["census"]["pages_total"] = 6
    d["commit_boundaries"]["checkpoint_pages"] = [4]
    d["commit_boundaries"]["checkpoints"] = [
        {"index": 1, "page_index": 4, "committed": False, "in_flow": True,
         "source": "lessonFlowPages"}]
    for u in d["commit_boundaries"]["unmapped"]:
        if u["page_index"] >= 4:
            u["page_index"] += 1
    return d


def _with_single_text(d, iid="p3-12", module_id=None):
    """Add an unsubmitted single-attempt text item on page 3 and keep the counts."""
    it = copy.deepcopy(item(d, "i-text-2"))
    it.update(item_id=iid, panel_key=f"pk-{iid}", attempts="single",
              prompt_text="State the total magnification of a 10x eyepiece with a 40x objective.")
    it.pop("parent_item_id", None)
    page(d, 3)["items"].append(it)
    d["census"]["captured_total"] += 1
    d["census"]["answerable_total"] += 1
    d["census"]["by_type"]["text-response"] += 1
    d["commit_boundaries"]["single_attempt_items"].append(iid)
    if module_id:
        d["module_id"] = module_id
    return d


def m_checkpoint_inventory_missing(d):
    d = _with_checkpoint(d)
    del d["commit_boundaries"]["checkpoints"]
    del d["commit_boundaries"]["checkpoints_container_present"]
    return d


def m_checkpoint_inventory_omits_live_door(d):
    d = _with_checkpoint(d)
    d["commit_boundaries"]["checkpoints"] = []
    return d


def m_checkpoint_dropped_but_claims_a_page(d):
    d = _with_checkpoint(d)
    d["commit_boundaries"]["checkpoints"][0]["in_flow"] = False
    return d


def m_checkpoint_committed_and_still_walked(d):
    d = _with_checkpoint(d)
    d["pages"][4]["checkpoint"]["committed"] = True
    d["commit_boundaries"]["checkpoints"][0]["committed"] = True
    return d


def m_checkpoint_inventory_without_a_container(d):
    d = _with_checkpoint(d)
    d["commit_boundaries"]["checkpoints"].append(
        {"index": 9, "page_index": None, "committed": True, "in_flow": False,
         "source": "checkpointPageContainer"})
    d["commit_boundaries"]["checkpoints_container_present"] = False
    return d


def m_checkpoint_not_in_boundary_map(d):
    d = _with_checkpoint(d)
    d["commit_boundaries"]["checkpoint_pages"] = []
    return d


def m_checkpoint_page_no(d):
    return _with_checkpoint(d, page_no=5)


def m_checkpoint_route_null(d):
    return _with_checkpoint(d, route=None)


def m_checkpoint_url_guessed(d):
    return _with_checkpoint(d, url=f"{ORIGIN}/i/inst0001/student/courses/1/runs/101/run/page/5")


def m_checkpoint_items(d):
    return _with_checkpoint(d, items=[copy.deepcopy(item(d, "i-text-1"))])


def m_checkpoint_no_committed(d):
    d = _with_checkpoint(d)
    del page(d, 5)["checkpoint"]
    return d


def m_content_page_carries_checkpoint(d):
    page(d, 1)["checkpoint"] = {"index": 0, "committed": False}
    return d


def m_completion_gone_with_nothing_hiding_it(d):
    d["pages"] = d["pages"][:-1]
    d["lesson"]["page_count"] = 4
    d["census"]["pages_total"] = 4
    return d


def m_committed_checkpoint_cannot_hide_the_report(d):
    # Lt removes pages only after an uncommitted checkpoint.
    d = _with_checkpoint(d, checkpoint={"index": 4, "committed": True})
    d["pages"] = [p for p in d["pages"] if not p["is_report_page"]]
    d["lesson"]["page_count"] = 5
    d["census"]["pages_total"] = 5
    return d


def m_page_menu_remaining_without_a_checkpoint(d):
    d["lesson"]["show_page_menu_page_remaining"] = True
    return d


# --- answers.json mutations ----------------------------------------------------------

def _drafted(a, text, refs=("p1",)):
    a["status"] = "drafted"
    a["confidence"] = "medium"
    a.pop("skip_reason", None)
    a["segments"] = [{"kind": "claim", "text": text, "provenance_refs": list(refs)}]
    a["provenance"] = [{"ref": "p1", "tier": 1, "source_id": "EX-PRAC", "locator": "slide-3",
                        "tag": "[T1:EX-PRAC:slide-3]"}]
    return a


def a_table_drafted_without_cells(d):
    _drafted(ans(d, "i-table-1"), "The table sets out each objective lens and its magnification.")
    return d


def a_duplicate_table_cell(d):
    a = _drafted(ans(d, "i-table-1"), "The 40x objective is the high-power dry lens.")
    a["table_cells"] = [{"row": 1, "col": 1, "text": "40x"}, {"row": 1, "col": 1, "text": "100x"}]
    return d


def a_table_cell_off_the_grid(d):
    a = _drafted(ans(d, "i-table-1"), "The 40x objective is the high-power dry lens.")
    a["table_cells"] = [{"row": 1, "col": 99, "text": "40x"}]
    return d


def _draft_the_categorization(d, placements=None):
    """Build a schema-version-2 categorization response for placement validation."""
    d["schema_version"] = 2
    a = _drafted(ans(d, "i-cat-1"), "The eyepiece and the objective are lenses; the stage is not.")
    a["uncertainties"] = [{"question": "Which group does the condenser belong in?",
                           "why_it_matters": "It decides the answer."}]
    if placements is not None:
        a["label_placements"] = placements
    return d


def _full_placements():
    return [{"label": "Eyepiece", "target": "Optical parts"},
            {"label": "Objective lens", "target": "Optical parts"},
            {"label": "Stage", "target": "Mechanical parts"}]


def a_placement_type_drafted_without_placements(d):
    return _draft_the_categorization(d)


def a_placement_partial_mapping(d):
    return _draft_the_categorization(d, _full_placements()[:-1])


def a_placement_invented_vocabulary(d):
    pl = _full_placements()
    pl[0] = {"label": "Condenser lens", "target": "Optical parts"}
    return _draft_the_categorization(d, pl)


def a_placement_medium_without_alternative(d):
    pl = _full_placements()
    pl[0]["confidence"] = "medium"
    return _draft_the_categorization(d, pl)


def a_placement_index_guessed_on_an_image(d):
    ans(d, "i-upann-1")["label_placements"] = [{"label": "x", "target": "y", "target_index": 3}]
    return d


def a_placements_on_an_item_with_no_pool(d):
    ans(d, "i-text-1")["label_placements"] = [{"label": "nope", "target": "nowhere"}]
    return d


def a_table_cells_on_a_text_item(d):
    ans(d, "i-text-1")["table_cells"] = [{"row": 0, "col": 0, "text": "nope"}]
    return d


def a_answer_for_an_item_not_in_the_capture(d):
    d["items"].append({"item_id": "i-ghost-42", "status": "skipped",
                       "skip_reason": "already-answered"})
    return d


def m_stale_source_binding(d):
    # A draft written without a question digest has only the whole-file hash to go on.
    d["source_module"]["sha256"] = "0" * 64
    d["source_module"].pop("question_digest", None)
    return d


def m_questions_changed(d):
    d["source_module"]["question_digest"] = "0" * 64
    return d


def m_question_digest_unverifiable(d):
    # The path resolves to a real file that is not a capture: hashing works, digesting cannot.
    d["source_module"]["path"] = "README.txt"
    return d


def m_untagged_claim(d):
    ans(d, "i-text-1")["segments"][0]["provenance_refs"] = []
    return d


def m_claim_as_connective(d):
    seg = ans(d, "i-text-1")["segments"][0]
    seg["kind"] = "connective"
    seg["provenance_refs"] = []
    return d


def m_connective_share(d):
    """Each relabelled fragment stays under the per-segment cap, so only the share cap
    can catch this."""
    a = ans(d, "i-text-1")
    a["segments"] = [
        {"kind": "claim", "text": "Oil matches the refractive index of glass.", "provenance_refs": ["p1"]},
        {"kind": "connective", "text": "This keeps more of the light cone inside the objective lens.",
         "provenance_refs": []},
        {"kind": "connective", "text": "Numerical aperture rises, and so does the finest resolvable detail.",
         "provenance_refs": []},
    ]
    a["provenance"] = [p for p in a["provenance"] if p["ref"] == "p1"]
    return d


def m_bad_tier4(d):
    ans(d, "i-upann-1")["provenance"][1]["source_id"] = "Example et al. 2000"
    return d


def m_missing_descent(d):
    ans(d, "i-upann-1")["tier_descent"] = [{"from_tier": 1, "to_tier": 2, "reason": "insufficient-depth"}]
    return d


def m_orphan_citation(d):
    ans(d, "i-text-1")["provenance"].append(
        {"ref": "p9", "tier": 1, "source_id": "EX-PRAC", "locator": "slide-30"})
    return d


def m_dangling_ref(d):
    ans(d, "i-text-1")["segments"][0]["provenance_refs"] = ["p99"]
    return d


def m_conflict_inverted(d):
    c = ans(d, "i-text-2")["conflicts"][0]
    c["higher_ref"], c["lower_ref"] = c["lower_ref"], c["higher_ref"]
    return d


def m_low_conf_no_uncertainty(d):
    del ans(d, "i-text-2")["uncertainties"]
    return d


def m_diagram_no_brief(d):
    del ans(d, "i-upann-1")["diagram_brief"]
    return d


def m_quarantined_corpus(d):
    d["tier1_corpus"].append({"source_id": "PRIOR", "path": "fixtures/reference-c1-r101.json",
                              "kind": "other"})
    return d


def m_quarantined_citation(d):
    a = next(x for x in d["items"] if x.get("provenance"))
    a["provenance"].append({"ref": "pq", "tier": 1,
                            "source_id": "data/captures/reference-c1-r101.json",
                            "locator": "item i-text-1"})
    next(s for s in a["segments"] if "provenance_refs" in s)["provenance_refs"].append("pq")
    return d


def m_blocked_no_todo(d):
    del ans(d, "i-unmapped-1")["todo"]
    return d


def m_ans_skip_without_reason(d):
    next(x for x in d["items"] if x.get("status") == "skipped").pop("skip_reason", None)
    return d


def a_cites_the_model_answer(d):
    a = ans(d, "i-text-1")
    a["provenance"].append({"ref": "pm", "tier": 1, "source_id": validate.MODEL_ANSWER_SOURCE,
                            "locator": "panelState.modelAnswer"})
    a["segments"][0]["provenance_refs"].append("pm")
    return d


def _approved_single(d, module_id="c1-r104", approval_id=None, iid="p3-12"):
    """Answers for the c1-r104 variant: p3-12 drafted under a single-attempt approval."""
    d["module_id"] = module_id
    d["source_module"]["path"] = "cross-module.json"  # bind to the variant in the harness
    a = _drafted({"item_id": iid}, "Total magnification is 10 x 40 = 400x.")
    a["single_attempt_approval"] = {"approved_by": "user",
                                    "approval_id": approval_id or FIXTURE_APPROVAL_ID}
    d["items"].append(a)
    return d


def a_approval_missing_from_the_file(d):
    return _approved_single(d, approval_id="A-00000000")


def a_approval_for_another_item(d):
    return _approved_single(d, iid="p3-13")


def a_approval_for_another_module(d):
    return _approved_single(d, module_id="c1-r101")


def a_approval_on_a_multi_attempt_item(d):
    a = ans(d, "i-text-1")
    a["single_attempt_approval"] = {"approved_by": "user", "approval_id": S_TEXT1["approval_id"]}
    return d


def a_approval_without_an_id(d):
    a = ans(d, "i-text-1")
    a["single_attempt_approval"] = {"approved_by": "user", "decision": "old-style"}
    return d


# --- fill-report.json mutations ------------------------------------------------------

def m_fill_no_never_navigated(d):
    d["never_navigated"] = []
    return d


def m_fill_report_page_filled(d):
    d["pages_filled"].append(5)
    return d


def m_fill_rat_page_filled(d):
    d["pages_filled"].append(2)
    return d


def m_fill_unverified_as_filled(d):
    fitem(d, "i-text-1")["verified"] = False
    return d


def m_fill_auto_at_boundary(d):
    d["approval_mode"] = "auto"
    return d


def m_fill_unmapped_stopped(d):
    # An unmapped item type is a gap in this suite; it may not truncate a run.
    d["boundary_reached"]["class"] = "unmapped"
    return d


def m_fill_past_the_door(d):
    # The module variant has a live checkpoint on page 5.
    d["pages_filled"].append(5)
    return d


def m_fill_unknown_item(d):
    d["items"].append({"item_id": "i-ghost", "attempted": False, "method": "not-attempted",
                       "result": "skipped"})
    return d


def m_fill_conflict_on_unattempted(d):
    fitem(d, "i-rat-1")["lt_conflict"] = copy.deepcopy(fitem(d, "i-text-1")["lt_conflict"])
    return d


def m_fill_conflict_after_reverting(d):
    it = fitem(d, "i-text-1")
    it["result"] = "failed"
    it["verified"] = False
    return d


def m_fill_conflict_agrees_with_itself(d):
    it = fitem(d, "i-text-1")
    it["lt_conflict"]["lt_indicated"] = it["lt_conflict"]["held_answer"]
    return d


def m_fill_conflict_without_a_citation(d):
    del fitem(d, "i-text-1")["lt_conflict"]["draft_ref"]
    return d


def m_fill_failed_single_attempt(d):
    # A failed write still reached the panel; on a single-attempt drag that is the door.
    it = fitem(d, "i-cat-1")
    it.update(attempted=True, method="view-model", result="failed", verified=False)
    it.pop("todo", None)
    return d


def _single_written(d, approval_id=None, module_id=None):
    if module_id:
        d["module_id"] = module_id
    it = {"item_id": "p3-12", "item_type": "text-response", "attempts": "single",
          "attempted": True, "method": "view-model", "result": "filled-unverified",
          "verified": False}
    if approval_id:
        it["approval_id"] = approval_id
    d["items"].append(it)
    return d


def m_fill_single_written_without_approval(d):
    return _single_written(d)


def m_fill_single_written_with_unknown_approval(d):
    return _single_written(d, approval_id="A-00000000", module_id="c1-r104")


_EX_WAIVER = {"who": "the user", "when": "2000-01-01", "approval_id": W_MANUAL["approval_id"],
              "why": "Synthetic waiver for the negative tests of the waiver rails."}


def m_fill_waiver_of_an_unwaivable_rail(d):
    d["user_decisions"] = [dict(_EX_WAIVER, rail="filled-without-verified")]
    return d


def m_fill_item_waiver_without_items(d):
    d["user_decisions"] = [dict(_EX_WAIVER, rail="write-past-first-door")]
    return d


def m_fill_waiver_without_an_approval(d):
    d["approval_mode"] = "auto"
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary",
                                approval_id="A-00000000")]
    return d


def m_fill_waiver_resting_on_a_single_attempt_approval(d):
    d["approval_mode"] = "auto"
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary",
                                approval_id=S_TEXT1["approval_id"])]
    return d


def m_fill_waiver_resting_on_another_rails_approval(d):
    d["approval_mode"] = "auto"
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary",
                                approval_id=W_DOOR["approval_id"])]
    return d


def m_fill_waiver_from_another_modules_file(d):
    d["approval_mode"] = "auto"
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary",
                                approval_id=FIXTURE_APPROVAL_ID)]
    return d


def m_fill_waiver_that_waives_nothing(d):
    # approval_mode is manual in the example, so the approval rail holds and the waiver is idle.
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary")]
    return d


def m_fill_item_waiver_naming_a_clean_item(d):
    d["user_decisions"] = [dict(_EX_WAIVER, rail="write-past-first-door", item_ids=["i-text-1"],
                                approval_id=W_DOOR["approval_id"])]
    return d


def m_fill_waiver_with_no_date(d):
    d["approval_mode"] = "auto"
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary", when="1 Jan 2000")]
    return d


def m_fill_waiver_on_no_calendar_day(d):
    d["approval_mode"] = "auto"
    d["user_decisions"] = [dict(_EX_WAIVER, rail="approval-manual-at-boundary", when="2000-02-30")]
    return d


def m_fill_write_past_a_checkpoint(d):
    # The module variant puts a live checkpoint on page 4; i-cat-1 now sits on page 5.
    it = fitem(d, "i-cat-1")
    it.update(attempted=True, method="manual-todo", result="failed", verified=False)
    it.pop("todo", None)
    return d


def _receipt(exp, obs, at="2000-01-01T00:10:00Z"):
    return {"expected_hash": exp, "observed_hash": obs, "observed_at": at}


def m_fill_verified_over_differing_hashes(d):
    fitem(d, "i-text-1")["receipt"] = _receipt(1, 2)
    return d


def m_fill_failed_over_matching_hashes(d):
    fitem(d, "i-text-2").update(result="failed", verified=False, receipt=_receipt("7", 7))
    return d


def m_fill_receipt_before_the_run(d):
    fitem(d, "i-text-1")["receipt"] = _receipt(5, 5, "1999-12-31T23:59:59Z")
    return d


def m_fill_receipt_after_the_run(d):
    fitem(d, "i-text-1")["receipt"] = _receipt(5, 5, "2000-01-01T00:22:01Z")
    return d


def m_fill_receipt_on_an_unattempted_item(d):
    fitem(d, "i-rat-1")["receipt"] = _receipt(5, 5)
    return d


def m_fill_receipt_naive_timestamp(d):
    fitem(d, "i-text-1")["receipt"] = _receipt(5, 5, "2000-01-01T00:10:00")
    return d


def m_fill_filled_without_receipt_from_receipts(d):
    d["source_receipts"] = {"path": "runs/x/receipts.json", "sha256": "0" * 64}
    for iid in ("i-upann-1", "i-table-1"):
        fitem(d, iid)["receipt"] = _receipt(5, 5)
    return d


# --- reference.json, run.json, approvals.json mutations --------------------------------

def m_ref_model_answer(d):
    d["provenance_of_reference"] = validate.MODEL_ANSWER_SOURCE
    return d


def m_ref_not_quarantined(d):
    d["never_load_into_drafting_context"] = False
    return d


def m_run_evidence_free_claim(d):
    for e in d["diff_classification"]:
        if e["steered_run_wrong"]:
            del e["before"]
    return d


def m_run_without_config_digest(d):
    del d["config_digest"]
    return d


def m_approvals_record_edited(d):
    d["approvals"][0]["quote"] = "Yes, write p3-13 as drafted."
    return d


def m_approvals_duplicate_record(d):
    d["approvals"].append(copy.deepcopy(d["approvals"][0]))
    return d


def m_approvals_bad_id(d):
    d["approvals"][0]["approval_id"] = "decision-89"
    return d


def ident(d):
    return d


def m_legacy_permission(d):
    d["permission"] = {"unit_code": "X101", "checked_at": T0, "exit_code": 0}
    return d


def m_legacy_permission_check(d):
    d["permission_check"] = {"unit_code": "X101", "exit_code": 0, "checked_at": T0}
    return d


# --- module variants for --module ----------------------------------------------------

def mod_with_checkpoint(m):
    return _with_checkpoint(m)


def mod_checkpoint_before_page4(m):
    return _checkpoint_before_page4(m)


def mod_c1_r104(m):
    return _with_single_text(m, "p3-12", module_id="c1-r104")


def mod_c1_r104_two_singles(m):
    return _with_single_text(_with_single_text(m, "p3-12", module_id="c1-r104"), "p3-13")


def mod_single_text(m):
    return _with_single_text(m, "p3-12")


READ_FALSE = "schema_version = 1\n\n[model_answer]\nread = false\n"

# (label, base example, mutation, expected exit, expected substring, options)
# options: module (callable on the example module for --module), no_module, approvals
# ("fixture", "waivers" or None), read (bool for --model-answer-read), config (TOML text).
CASES = [
    ("an answer key smuggled into a free-form map", "module", m_denied_key, RAIL_FAIL, "module-has-no-answer-key", {}),
    ("checkpoint page absent from the boundary map", "module", m_checkpoint_not_in_boundary_map, RAIL_FAIL, "checkpoint_pages", {}),
    ("capture predating the checkpoint inventory", "module", m_checkpoint_inventory_missing, SCHEMA_FAIL, "'checkpoints' is a required property", {}),
    ("inventory denies a door the page walk found", "module", m_checkpoint_inventory_omits_live_door, RAIL_FAIL, "in_flow page_index", {}),
    ("a dropped checkpoint still claiming a page", "module", m_checkpoint_dropped_but_claims_a_page, RAIL_FAIL, "occupies no page", {}),
    ("committed checkpoint still in the page walk", "module", m_checkpoint_committed_and_still_walked, RAIL_FAIL, "must not be asserted", {}),
    ("container entry with no container to read", "module", m_checkpoint_inventory_without_a_container, RAIL_FAIL, "nothing but the container", {}),
    ("checkpoint page given a URL page number", "module", m_checkpoint_page_no, RAIL_FAIL, "no /page/<n> address", {}),
    ("checkpoint page with no route of its own", "module", m_checkpoint_route_null, RAIL_FAIL, "route must be 'checkpoint'", {}),
    ("checkpoint page given a guessed url", "module", m_checkpoint_url_guessed, RAIL_FAIL, "not the URL segment", {}),
    ("checkpoint page carrying items", "module", m_checkpoint_items, RAIL_FAIL, "holds no panels", {}),
    ("checkpoint that will not say whether it is shut", "module", m_checkpoint_no_committed, RAIL_FAIL, "checkpoint.committed", {}),
    ("content page carrying a checkpoint block", "module", m_content_page_carries_checkpoint, RAIL_FAIL, "non-checkpoint page", {}),
    ("report page dropped with nothing hiding it", "module", m_completion_gone_with_nothing_hiding_it, RAIL_FAIL, "no uncommitted checkpoint", {}),
    ("a committed checkpoint hides no report page", "module", m_committed_checkpoint_cannot_hide_the_report, RAIL_FAIL, "no uncommitted checkpoint", {}),
    ("pages remain behind a checkpoint nothing mapped", "module", m_page_menu_remaining_without_a_checkpoint, RAIL_FAIL, "no checkpoint page is mapped", {}),
    ("report page given a URL page number", "module", m_report_page_no, RAIL_FAIL, "not addressable", {}),
    ("report-page mirrors counted as items", "module", m_report_items, RAIL_FAIL, "mirrors", {}),
    ("census disagrees with the inventory", "module", m_census_drift, RAIL_FAIL, "answerable_total", {}),
    ("subpage container counted as a question", "module", m_subpage_counted, RAIL_FAIL, "subpage container", {}),
    ("completion page without a run-level commit", "module", m_no_run_commit, RAIL_FAIL, "live Commit button", {}),
    ("single-attempt drag marked automatable", "module", m_single_attempt_drag_automated, RAIL_FAIL, "must be automation=degrade-todo", {}),
    ("degrade class contradicts its own reason", "module", m_degrade_class_disagrees, RAIL_FAIL, "implies class", {}),
    ("accepts_upload disagrees with input_mode", "module", m_accepts_upload_lies, RAIL_FAIL, "accepts an upload", {}),
    ("permanent degrade filed as pending geometry", "module", m_degrade_blames_geometry_on_annotate_only, RAIL_FAIL, "permanent, not pending", {}),
    ("boundary map drifts from the items", "module", m_boundary_map_drift, RAIL_FAIL, "attempts=single", {}),
    ("upload-and-annotate without target geometry", "module", m_missing_geometry, SCHEMA_FAIL, "target_geometry", {}),
    ("unknown canvas scale claimed as automatable", "module", m_unknown_geometry_automated, RAIL_FAIL, "presented_ratio is null, so automation must be degrade-todo", {}),
    ("answered:true over a grid its own map calls empty", "module", m_answered_map_says_empty, RAIL_FAIL, "recomputes to", {}),
    ("answered:false over a grid its own map calls filled", "module", m_answered_map_says_filled, RAIL_FAIL, "recomputes to", {}),
    ("answered_suspect flagged on a shape that is not there", "module", m_answered_suspect_without_the_shape, RAIL_FAIL, "not the single-filled-column or authored-L shape", {}),
    ("answered_suspect on an L whose interior a student filled", "module", m_answered_suspect_l_with_a_filled_interior, RAIL_FAIL, "authored-L shape", {}),
    ("answered_evidence on an item with no grid", "module", m_answered_evidence_on_a_text_item, RAIL_FAIL, "table-completion only", {}),
    ("authored 'a' cells with no read of the author grid", "module", m_authored_mark_without_an_author_grid, RAIL_FAIL, "only a read of", {}),
    ("authored_cells disagrees with its own filled_map", "module", m_authored_count_disagrees_with_the_map, RAIL_FAIL, "carries 1 'a' mark", {}),
    ("notebook never-write reason on another plugin", "module", m_notebook_reason_on_another_plugin, RAIL_FAIL, "unmapped notebook panels only", {}),
    ("geometry_source disagrees with presented_ratio", "module", m_geometry_source_lies, RAIL_FAIL, "geometry_source", {}),
    ("RAT flag whose stated basis is false", "module", m_rat_flag_untrue, RAIL_FAIL, "basis does not hold", {}),
    ("unmapped type not degraded to TODO", "module", m_unmapped_not_degraded, SCHEMA_FAIL, "degrade-todo", {}),
    ("timestamp with no timezone", "module", m_naive_timestamp, RAIL_FAIL, "timezone", {}),
    ("a drafted table-completion carrying no table_cells", "answers", a_table_drafted_without_cells, RAIL_FAIL, "the cells are the answer", {}),
    ("two table_cells addressing the same coordinate", "answers", a_duplicate_table_cell, RAIL_FAIL, "silently overwrites", {}),
    ("a table cell outside the captured grid", "answers", a_table_cell_off_the_grid, RAIL_FAIL, "outside the captured grid", {}),
    ("a drafted placement type carrying no label_placements", "answers", a_placement_type_drafted_without_placements, RAIL_FAIL, "the placements are the answer", {}),
    ("a partial label mapping", "answers", a_placement_partial_mapping, RAIL_FAIL, "a partial mapping", {}),
    ("a placement naming a label that is not in the pool", "answers", a_placement_invented_vocabulary, RAIL_FAIL, "invented vocabulary", {}),
    ("a medium-confidence placement with no competing reading", "answers", a_placement_medium_without_alternative, RAIL_FAIL, "no alternative", {}),
    ("an anchor index guessed on an image item", "answers", a_placement_index_guessed_on_an_image, RAIL_FAIL, "index is a guess", {}),
    ("label_placements on an item with no label pool", "answers", a_placements_on_an_item_with_no_pool, RAIL_FAIL, "label pool to place", {}),
    ("table_cells on an item with no grid", "answers", a_table_cells_on_a_text_item, RAIL_FAIL, "has no grid", {}),
    ("an answer for an item the capture does not contain", "answers", a_answer_for_an_item_not_in_the_capture, RAIL_FAIL, "not an item in the capture", {}),
    ("draft pinned to capture bytes that have since changed", "answers", m_stale_source_binding, RAIL_FAIL, "no longer exists", {}),
    ("the questions themselves changed under a draft", "answers", m_questions_changed, RAIL_FAIL, "QUESTIONS this draft answers have changed", {}),
    ("a recorded digest that cannot be recomputed", "answers", m_question_digest_unverifiable, RAIL_FAIL, "could not be read as a capture", {}),
    ("untagged factual claim", "answers", m_untagged_claim, SCHEMA_FAIL, "provenance_refs", {}),
    ("long claim relabelled connective", "answers", m_claim_as_connective, SCHEMA_FAIL, "too long", {}),
    ("short claims relabelled to escape citation", "answers", m_connective_share, RAIL_FAIL, "connective prose", {}),
    ("Tier-4 citation with no resolvable id", "answers", m_bad_tier4, RAIL_FAIL, "fabrication", {}),
    ("tier descended with no recorded reason", "answers", m_missing_descent, RAIL_FAIL, "descending", {}),
    ("citation cited by nothing", "answers", m_orphan_citation, RAIL_FAIL, "decoration", {}),
    ("segment cites a provenance that does not exist", "answers", m_dangling_ref, RAIL_FAIL, "not in provenance", {}),
    ("conflict recorded with the tiers inverted", "answers", m_conflict_inverted, RAIL_FAIL, "not above", {}),
    ("low confidence with nothing stated as uncertain", "answers", m_low_conf_no_uncertainty, SCHEMA_FAIL, "uncertainties", {}),
    ("needs_diagram with no brief to hand over", "answers", m_diagram_no_brief, RAIL_FAIL, "diagram_brief", {}),
    ("blocked item with no TODO", "answers", m_blocked_no_todo, SCHEMA_FAIL, "todo", {}),
    ("quarantined reference listed as Tier-1 corpus", "answers", m_quarantined_corpus, RAIL_FAIL, "quarantined", {}),
    ("quarantined reference cited as provenance", "answers", m_quarantined_citation, RAIL_FAIL, "quarantined", {}),
    ("an item skipped with no reason recorded", "answers", m_ans_skip_without_reason, SCHEMA_FAIL, "skip_reason", {}),
    ("answers cite the model answer while model_answer.read is false", "answers", a_cites_the_model_answer, RAIL_FAIL, "model-answer-policy", {"read": False}),
    ("model answer cited under a config that turns it off", "answers", a_cites_the_model_answer, RAIL_FAIL, "model-answer-policy", {"config": READ_FALSE}),
    ("single-attempt approval id missing from the approvals file", "answers", a_approval_missing_from_the_file, RAIL_FAIL, "is not in the approvals file", {"module": mod_c1_r104, "approvals": "fixture"}),
    ("single-attempt approval naming another item", "answers", a_approval_for_another_item, RAIL_FAIL, "approves item 'p3-12', not 'p3-13'", {"module": mod_c1_r104_two_singles, "approvals": "fixture"}),
    ("single-attempt approval from another module's file", "answers", a_approval_for_another_module, RAIL_FAIL, "the approvals file is for module 'c1-r104'", {"module": mod_single_text, "approvals": "fixture"}),
    ("single-attempt approval on a multi-attempt item", "answers", a_approval_on_a_multi_attempt_item, RAIL_FAIL, "does not mark single-attempt", {}),
    ("single-attempt approval in the old decision-log shape", "answers", a_approval_without_an_id, SCHEMA_FAIL, "approval_id", {}),
    ("completion route not declared off-limits", "fill-report", m_fill_no_never_navigated, RAIL_FAIL, "not listed in never_navigated", {}),
    ("report page reported as filled", "fill-report", m_fill_report_page_filled, RAIL_FAIL, "includes the report page", {}),
    ("policy-skipped RAT page reported as filled", "fill-report", m_fill_rat_page_filled, RAIL_FAIL, "skipped by standing policy", {}),
    ("filled without per-item verification", "fill-report", m_fill_unverified_as_filled, RAIL_FAIL, "filled-unverified", {}),
    ("auto-approve on a run that hit a boundary", "fill-report", m_fill_auto_at_boundary, RAIL_FAIL, "a run that met a commit boundary", {}),
    ("fill reports an item the capture never saw", "fill-report", m_fill_unknown_item, RAIL_FAIL, "not present", {}),
    ("unmapped item type reported as a stopping boundary", "fill-report", m_fill_unmapped_stopped, RAIL_FAIL, "not a one-way door", {}),
    ("a page at or past a checkpoint reported as filled", "fill-report", m_fill_past_the_door, RAIL_FAIL, "one-way door", {"module": mod_with_checkpoint}),
    ("a write on a page past a checkpoint", "fill-report", m_fill_write_past_a_checkpoint, RAIL_FAIL, "plan_fill plans no item there", {"module": mod_checkpoint_before_page4}),
    ("Lt conflict on an item nothing was written into", "fill-report", m_fill_conflict_on_unattempted, RAIL_FAIL, "nothing to contradict", {}),
    ("Lt conflict filed against an answer that was not kept", "fill-report", m_fill_conflict_after_reverting, RAIL_FAIL, "STAYS written", {}),
    ("Lt conflict whose two sides say the same thing", "fill-report", m_fill_conflict_agrees_with_itself, RAIL_FAIL, "nothing is in conflict", {}),
    ("Lt conflict with no citation behind the held answer", "fill-report", m_fill_conflict_without_a_citation, SCHEMA_FAIL, "draft_ref", {}),
    ("Lt conflict seen through the model answer while it is off", "fill-report", ident, RAIL_FAIL, "model-answer-policy", {"read": False}),
    ("failed automated write on a single-attempt drag item", "fill-report", m_fill_failed_single_attempt, RAIL_FAIL, "single-attempt item reported as 'failed'", {}),
    ("single-attempt item written with no approval_id", "fill-report", m_fill_single_written_without_approval, RAIL_FAIL, "with no approval_id", {"module": mod_single_text}),
    ("single-attempt write resting on an unknown approval", "fill-report", m_fill_single_written_with_unknown_approval, RAIL_FAIL, "is not a single-attempt approval for this item", {"module": mod_c1_r104, "approvals": "fixture"}),
    ("waiver for a rail no approval may waive", "fill-report", m_fill_waiver_of_an_unwaivable_rail, SCHEMA_FAIL, "user_decisions/0/rail", {}),
    ("per-item waiver naming no items", "fill-report", m_fill_item_waiver_without_items, SCHEMA_FAIL, "item_ids", {}),
    ("waiver resting on no recorded approval", "fill-report", m_fill_waiver_without_an_approval, RAIL_FAIL, "is not in the approvals file", {"approvals": "waivers"}),
    ("waiver resting on a single-attempt approval", "fill-report", m_fill_waiver_resting_on_a_single_attempt_approval, RAIL_FAIL, "not a waiver", {"approvals": "waivers"}),
    ("waiver resting on another rail's approval", "fill-report", m_fill_waiver_resting_on_another_rails_approval, RAIL_FAIL, "waives 'write-past-first-door', not 'approval-manual-at-boundary'", {"approvals": "waivers"}),
    ("waiver checked against another module's approvals", "fill-report", m_fill_waiver_from_another_modules_file, RAIL_FAIL, "the approvals file is for module 'c1-r104'", {"approvals": "fixture"}),
    ("waiver of a rail the report does not break", "fill-report", m_fill_waiver_that_waives_nothing, RAIL_FAIL, "waives nothing", {}),
    ("per-item waiver naming an item no rail flags", "fill-report", m_fill_item_waiver_naming_a_clean_item, RAIL_FAIL, "no violation of that rail names them", {}),
    ("waiver with no date", "fill-report", m_fill_waiver_with_no_date, RAIL_FAIL, "neither a YYYY-MM-DD date", {}),
    ("waiver dated on no calendar day", "fill-report", m_fill_waiver_on_no_calendar_day, RAIL_FAIL, "neither a YYYY-MM-DD date", {}),
    ("verified=true over a receipt whose hashes differ", "fill-report", m_fill_verified_over_differing_hashes, RAIL_FAIL, "the receipt decides", {}),
    ("failed over a receipt whose hashes match", "fill-report", m_fill_failed_over_matching_hashes, RAIL_FAIL, "the receipt decides", {}),
    ("receipt from a load before the run", "fill-report", m_fill_receipt_before_the_run, RAIL_FAIL, "precedes started_at", {}),
    ("receipt from a load after the run ended", "fill-report", m_fill_receipt_after_the_run, RAIL_FAIL, "is after ended_at", {}),
    ("receipt on an item never attempted", "fill-report", m_fill_receipt_on_an_unattempted_item, RAIL_FAIL, "reported attempted=false", {}),
    ("receipt timestamp with no timezone", "fill-report", m_fill_receipt_naive_timestamp, RAIL_FAIL, "timezone", {}),
    ("filled with no receipt in a report built from receipts", "fill-report", m_fill_filled_without_receipt_from_receipts, RAIL_FAIL, "never from a literal", {}),
    ("reference built from Lt's model answer while it is off", "reference", m_ref_model_answer, RAIL_FAIL, "model-answer-policy", {"read": False}),
    ("eval reference not quarantined from drafting", "reference", m_ref_not_quarantined, SCHEMA_FAIL, "never_load", {}),
    ("skill edit proposed with no diff evidence", "run", m_run_evidence_free_claim, RAIL_FAIL, "evidence", {}),
    ("run record with no config digest", "run", m_run_without_config_digest, SCHEMA_FAIL, "config_digest", {}),
    ("approval record edited after it was written", "approvals", m_approvals_record_edited, RAIL_FAIL, "does not match its record", {}),
    ("approval recorded twice", "approvals", m_approvals_duplicate_record, RAIL_FAIL, "appears twice", {}),
    ("approval id in the old decision-log form", "approvals", m_approvals_bad_id, SCHEMA_FAIL, "approval_id", {}),
]

# (label, base example, mutation, options, substring the non-quiet output must carry or None)
PASS_CASES = [
    ("every schema example as shipped: module", "module", ident, {}, None),
    ("every schema example as shipped: answers", "answers", ident, {}, None),
    ("every schema example as shipped: fill-report", "fill-report", ident, {}, None),
    ("every schema example as shipped: reference", "reference", ident, {}, None),
    ("every schema example as shipped: run", "run", ident, {}, None),
    ("the approvals fixture", "approvals", ident, {}, None),
    ("reference built from Lt's model answer while it is on", "reference", m_ref_model_answer, {"read": True}, None),
    ("model answer on by default (no config file)", "reference", m_ref_model_answer, {}, None),
    ("answers citing the model answer while it is on", "answers", a_cites_the_model_answer, {"read": True}, None),
    ("legacy capture carrying the retired permission block", "module", m_legacy_permission, {}, "dropped legacy field(s) permission"),
    ("legacy run record carrying permission_check", "run", m_legacy_permission_check, {}, "dropped legacy field(s) permission_check"),
    ("single-attempt approval bound to the supplied variant", "answers", _approved_single, {"module": mod_c1_r104, "approvals": "variant"}, None),
    ("single-attempt write resting on a capture-bound approval", "fill-report",
     lambda d: _single_written(d, approval_id=FIXTURE_APPROVAL_ID, module_id="c1-r104"),
     {"module": mod_c1_r104, "approvals": "variant"}, None),
    ("auto approval at a boundary waived by a recorded approval", "fill-report",
     lambda d: dict(d, approval_mode="auto", user_decisions=[dict(_EX_WAIVER, rail="approval-manual-at-boundary")]),
     {"approvals": "waivers"}, "1 violation(s) waived by user_decisions"),
    ("a write past a checkpoint waived for the item it names", "fill-report",
     lambda d: dict(m_fill_write_past_a_checkpoint(d), user_decisions=[
         dict(_EX_WAIVER, rail="write-past-first-door", item_ids=["i-cat-1"],
              approval_id=W_DOOR["approval_id"])]),
     {"module": mod_checkpoint_before_page4, "approvals": "waivers"}, "waived write-past-first-door for i-cat-1"),
    ("a waiver without --approvals is checked for format only", "fill-report",
     lambda d: dict(d, approval_mode="auto", user_decisions=[dict(_EX_WAIVER, rail="approval-manual-at-boundary")]),
     {}, "checked for format only"),
    ("a single-attempt item that never stops the run", "fill-report",
     lambda d: dict(d, pages_filled=[3, 4]), {}, None),
]


def run_validator(tmp_path: Path, base: str, doc: dict, opts: dict, quiet: bool = True):
    """Write ``doc`` and its companions to tmp_path, run the validator CLI in-process,
    return (exit code, combined output)."""
    fp = tmp_path / f"{base}.json"
    # The answers example pins module.example.json next to itself.
    (tmp_path / "module.example.json").write_bytes((EX / "module.example.json").read_bytes())
    (tmp_path / "answers.example.json").write_bytes((EX / "answers.example.json").read_bytes())
    (tmp_path / "README.txt").write_text("Not a capture.\n", encoding="utf-8")
    argv = [str(fp), "--schemas-dir", str(SCHEMAS)]
    if quiet:
        argv.append("--quiet")
    if base in ("fill-report", "answers") and not opts.get("no_module"):
        mod = opts.get("module", ident)(load("module"))
        mp = tmp_path / "cross-module.json"
        mp.write_text(json.dumps(mod, indent=1), encoding="utf-8")
        argv += ["--module", str(mp)]
        if base == "answers" and doc["source_module"]["path"] == "cross-module.json":
            doc["source_module"].update(sha256=hashlib.sha256(mp.read_bytes()).hexdigest(),
                                        question_digest=question_digest(mod))
    if opts.get("approvals") == "fixture":
        argv += ["--approvals", str(APPROVALS_FIXTURE)]
    elif opts.get("approvals") == "waivers":
        ap = tmp_path / "approvals.json"
        ap.write_text(json.dumps(WAIVER_APPROVALS, indent=1), encoding="utf-8")
        argv += ["--approvals", str(ap)]
    elif opts.get("approvals") == "variant":
        rec = _rec(mod["module_id"], "single-attempt", "Yes, write this variant.",
                   item_id="p3-12", qdigest=question_digest(mod))
        ap = tmp_path / "approvals.json"
        ap.write_text(json.dumps({"schema_version": 1, "module_id": mod["module_id"],
                                  "approvals": [rec]}), encoding="utf-8")
        row = next(row for row in doc["items"] if row["item_id"] == "p3-12")
        if base == "answers":
            row["single_attempt_approval"]["approval_id"] = rec["approval_id"]
        else:
            row["approval_id"] = rec["approval_id"]
        argv += ["--approvals", str(ap)]
    if "read" in opts:
        argv += ["--model-answer-read", "true" if opts["read"] else "false"]
    if "config" in opts:
        cp = tmp_path / "lt-suite.toml"
        cp.write_text(opts["config"], encoding="utf-8")
        argv += ["--config", str(cp)]
    fp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = validate.main(argv)
    return rc, out.getvalue() + err.getvalue()


def base_doc(base: str) -> dict:
    if base == "approvals":
        return json.loads(APPROVALS_FIXTURE.read_text(encoding="utf-8"))
    return load(base)


@pytest.mark.parametrize("label,base,mutate,want_code,want_text,opts", CASES,
                         ids=[c[0] for c in CASES])
def test_rail_fires(label, base, mutate, want_code, want_text, opts, tmp_path, rail_tally):
    doc = mutate(base_doc(base))
    rc, out = run_validator(tmp_path, base, doc, opts)
    fired = rc == want_code and want_text.lower() in out.lower()
    rail_tally["tested"] += 1
    rail_tally["fired" if fired else "missed"] += 1
    assert fired, f"{label}: wanted exit {want_code} containing {want_text!r}; got exit {rc}:\n{out}"


@pytest.mark.parametrize("label,base,mutate,opts,want_note", PASS_CASES,
                         ids=[c[0] for c in PASS_CASES])
def test_allowed_shape_passes(label, base, mutate, opts, want_note, tmp_path):
    doc = mutate(base_doc(base))
    rc, out = run_validator(tmp_path, base, doc, opts, quiet=False)
    assert rc == OK, f"{label}: exit {rc}:\n{out}"
    if want_note:
        assert want_note in out, out


def test_rail_fail_reports_exactly_one_violation_per_mutation(tmp_path):
    """A mutation breaks one rule; the summary line counts what fired. Spot-check that a
    single-rule mutation reports a single violation, so another rail cannot stand in."""
    doc = m_fill_rat_page_filled(load("fill-report"))
    rc, out = run_validator(tmp_path, "fill-report", doc, {})
    assert rc == RAIL_FAIL and "1 violation(s)" in out, out


def test_author_grid_hotspot_and_notebook_shapes_pass(tmp_path):
    """Shapes a valid capture carries: an author-grid table with authored cells, a
    hotspot content panel, an unread content panel and a notebook with its permanent
    degrade."""
    d = load("module")
    tb = item(d, "i-table-1")
    tb["answered"] = False
    tb["constraints"]["authored_cells"] = [{"row": 0, "col": 0, "text": "Low power"},
                                           {"row": 1, "col": 0, "text": "High power"}]
    tb["answered_evidence"] = {"filled_map": ["a.", "a.", ".."],
                               "author_grid": {"status": "read", "source": "content.rows",
                                               "authored_cells": 2}}
    pg = page(d, 3)
    pg["content"].append({
        "panel_key": "pk-90", "plugin_key": "hotspot", "kind": "hotspot",
        "text": "Calibration grid: squares - 1 mm",
        "media": [{"kind": "image", "key": "759", "alt": None, "url": None, "width": None,
                   "height": None, "local_path": None}],
        "hotspots": [
            {"id": 1, "parent_id": None, "hotspot_type": "CUSTOM", "colour": "BLUE",
             "title": "", "text": "", "image_key": "759", "alt": None, "points": []},
            {"id": 2, "parent_id": 1, "hotspot_type": "POINT", "colour": "RED",
             "title": "Calibration grid", "text": "squares - 1 mm", "image_key": None,
             "alt": None, "points": [{"x": 0.05, "y": 0.106}]}],
        "capture_note": "hotspot reader"})
    pg["content"].append({"panel_key": "pk-91", "plugin_key": "kcMystery", "kind": "other",
                          "capture_note": "pluginKey kcMystery has no content reader"})
    nb = copy.deepcopy(item(d, "i-unmapped-1"))
    nb.update({"item_id": "i-notebook-1", "panel_key": "pk-92", "plugin_key": "notebook",
               "capture_note": "Notebook panel (pluginKey notebook); written by nobody but its owner.",
               "degrade": {"class": "permanent", "reason": "notebook-never-written"}})
    pg["items"].append(nb)
    d["census"]["captured_total"] += 1
    d["census"]["answerable_total"] += 1
    d["census"]["by_type"]["unmapped"] = d["census"]["by_type"].get("unmapped", 0) + 1
    d["commit_boundaries"]["unmapped"].append(
        {"page_index": 3, "item_id": "i-notebook-1", "reason": nb["capture_note"]})
    rc, out = run_validator(tmp_path, "module", d, {})
    assert rc == OK, out


def test_tally_covers_every_case():
    labels = [c[0] for c in CASES]
    assert len(labels) == len(set(labels)), "duplicate case labels"
    assert len(CASES) >= 100
