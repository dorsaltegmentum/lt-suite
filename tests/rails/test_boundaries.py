# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.boundaries on synthetic variants of the schema example module.

The planner and the validator share this one implementation. These tests pin its rules
on hand-built shapes: which class stops a run, when a submitted single-attempt item is
already passed, which subpage children inherit a page lock, and that the class sets
agree with the schemas and the approvals module.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ltcore import approvals, boundaries as b, validate

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "tests" / "fixtures" / "examples"
SCHEMAS = REPO / "shared" / "schemas"


def module():
    return json.loads((EX / "module.example.json").read_text(encoding="utf-8"))


def schema(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def page(d, idx):
    return next(p for p in d["pages"] if p["page_index"] == idx)


def top(iid, typ, ro, **extra):
    """A top-level item with the fields the lock rules read."""
    return dict({"item_id": iid, "type": typ, "parent_item_id": None, "read_only": ro,
                 "answered": False, "attempts": "multi", "automation": "structured-path",
                 "panel_key": f"pk-{iid}", "mirror_of": None, "degrade": None}, **extra)


def child(iid, parent):
    return top(iid, "text-response", None, parent_item_id=parent)


def locked_module(run_locked=False, committed_checkpoint=True):
    """Pages 1 to 4 plus a report page. Page 3 reads locked (its table reads read_only
    true) and holds a subpage with two children whose lock the capture cannot read.
    Page 4 has no evidence item, only a subpage and its child."""
    d = {"module_id": "c1-r102", "lesson": {"run_locked": run_locked, "has_completion_page": True},
         "commit_boundaries": {"checkpoints": [], "checkpoints_container_present": True},
         "pages": [
             {"page_index": 1, "items": []},
             {"page_index": 2, "items": [top("p2-1", "text-response", True)]},
             {"page_index": 3, "items": [top("p3-1", "table-completion", True),
                                         top("p3-2", "subpage", None),
                                         child("p3-3", "p3-2"), child("p3-4", "p3-2")]},
             {"page_index": 4, "items": [top("p4-1", "subpage", None), child("p4-2", "p4-1")]},
             {"page_index": 5, "items": [top("p5-1", "text-response", False),
                                         top("p5-2", "subpage", None), child("p5-3", "p5-2")]},
             {"page_index": 6, "is_report_page": True, "route": "complete", "items": []},
         ]}
    if committed_checkpoint:
        d["commit_boundaries"]["checkpoints"] = [
            {"index": 1, "page_index": None, "committed": True, "in_flow": False,
             "source": "checkpointPageContainer"}]
    return d


def test_example_module_has_no_stop_boundary():
    m = module()
    assert b.first_stop_boundary(m) is None
    assert b.first_door(m) is None
    bm = b.boundary_map(m)
    doors = {(d["class"], d["item_id"]): d["severity"] for d in bm["doors"]}
    assert doors[("single-attempt-item", "i-cat-1")] == "per-item"
    assert doors[("unmapped", "i-unmapped-1")] == "per-item"
    assert doors[("report-page", None)] == "never-navigate"
    assert [n["route"] for n in bm["never_navigated"]] == ["complete"]


def test_a_live_checkpoint_is_the_first_stop_boundary():
    m = module()
    cp = {"page_index": 5, "page_no": None, "route": "checkpoint", "url": None,
          "is_report_page": False, "is_checkpoint": True,
          "checkpoint": {"index": 4, "committed": False}, "items": []}
    m["pages"].insert(4, cp)
    m["pages"][-1]["page_index"] = 6
    assert b.first_stop_boundary(m) == (5, "checkpoint-page", None)
    assert b.first_door(m)["severity"] == "stop"


def test_a_checkpoint_sorts_before_items_on_its_page():
    m = {"pages": [{"page_index": 2, "is_checkpoint": True,
                    "items": [top("p2-1", "text-response", None, attempts="single")]}]}
    assert b.first_stop_boundary(m) == (2, "checkpoint-page", None)
    assert [d["class"] for d in b.boundary_map(m)["doors"]] == ["checkpoint-page",
                                                                "single-attempt-item"]


def test_severity_tables_agree():
    assert b.STOP_CLASSES == {"checkpoint-page"}
    assert b.STOP_CLASSES <= b.ONE_WAY_DOORS
    assert "unmapped" not in b.ONE_WAY_DOORS
    enum = schema("fill-report")["properties"]["boundary_reached"]["properties"]["class"]["enum"]
    assert set(enum) - {None} == b.ONE_WAY_DOORS | {"unmapped"}
    assert set(b.SEVERITY) == b.ONE_WAY_DOORS | {"unmapped", "report-page"}


def test_waivable_rails_agree_everywhere():
    fr = schema("fill-report")["properties"]["user_decisions"]["items"]
    assert set(fr["properties"]["rail"]["enum"]) == set(validate.WAIVABLE_RAILS)
    per_item = set(fr["allOf"][0]["if"]["properties"]["rail"]["enum"])
    assert per_item == {r for r, v in validate.WAIVABLE_RAILS.items() if v}
    ap = schema("approvals")["properties"]["approvals"]["items"]["properties"]["rail"]["enum"]
    assert set(ap) == set(validate.WAIVABLE_RAILS) == set(approvals.WAIVER_RAILS)


def test_placement_types_match_the_validator():
    assert validate.PLACEMENT_TYPES == b.PLACEMENT_TYPES


def test_submitted_single_needs_all_three_fields():
    for att in ("single", "multi", None):
        for ro in (True, False, None):
            for an in (True, False, None):
                want = att == "single" and ro is True and an is True
                assert b.submitted_single({"attempts": att, "read_only": ro, "answered": an}) is want


def test_lock_cause():
    assert b.lock_cause(locked_module()) == "Checkpoint index 1 is committed"
    assert b.lock_cause(locked_module(run_locked=True)) == "The lesson reads run_locked true"
    assert b.lock_cause(locked_module(committed_checkpoint=False)) is None
    live = locked_module()
    live["commit_boundaries"]["checkpoints"][0]["in_flow"] = True
    assert b.lock_cause(live) is None  # a committed checkpoint still in the flow is a door


def test_lock_evidence_exclusions():
    assert b.lock_evidence(top("x", "text-response", True))
    for it in (child("x", "y"), top("x", "subpage", None), top("x", "unmapped", True),
               top("x", "text-response", True, plugin_key="notebook"),
               top("x", "text-response", True, attempts="single", answered=True),
               top("x", "text-response", True, mirror_of={"item_id": "z"}),
               top("x", "text-response", True, degrade={"class": "permanent"})):
        assert not b.lock_evidence(it), it


def test_inferred_locks_under_a_committed_checkpoint():
    got = b.inferred_locks(locked_module())
    # Page 3 reads locked: its children are read-only-inferred. Page 4 has no evidence and
    # lies inside the reach: lock-unknown. Page 5 reads writable and ends the reach.
    assert got["p3-3"]["class"] == got["p3-4"]["class"] == "read-only-inferred"
    assert got["p3-3"]["basis"] == ["p3-1"]
    assert got["p4-2"]["class"] == "lock-unknown"
    assert got["p4-2"]["basis"] == ["page 2", "page 3"]
    assert "p5-3" not in got
    assert b.single_pass_pages(locked_module()) == {2, 3}


def test_no_cause_means_no_inference():
    assert b.inferred_locks(locked_module(committed_checkpoint=False)) == {}
    assert b.single_pass_pages(locked_module(committed_checkpoint=False)) == set()


def test_run_locked_reach_is_the_whole_run():
    m = locked_module(run_locked=True)
    assert b.single_pass_pages(m) == {1, 2, 3, 4, 5}
    got = b.inferred_locks(m)
    assert got["p4-2"] == {"class": "lock-unknown", "cause": "The lesson reads run_locked true",
                           "basis": []}
    assert "p5-3" not in got  # page 5 evidence reads false


def test_a_rat_page_after_a_locked_page_ends_the_reach():
    m = locked_module()
    page(m, 4)["rat_candidate"] = {"basis": "all items are multiple-choice"}
    got = b.inferred_locks(m)
    assert "p4-2" not in got and got["p3-3"]["class"] == "read-only-inferred"


def test_submitted_single_on_a_locked_page_is_passed_not_a_door():
    m = locked_module()
    page(m, 3)["items"].append(top("p3-9", "text-response", True, attempts="single", answered=True))
    bm = b.boundary_map(m)
    assert [s["item_id"] for s in bm["single_attempts_passed"]] == ["p3-9"]
    assert not any(d["item_id"] == "p3-9" for d in bm["doors"])


def test_submitted_single_without_a_cause_keeps_its_door():
    m = locked_module(committed_checkpoint=False)
    page(m, 3)["items"].append(top("p3-9", "text-response", True, attempts="single", answered=True))
    bm = b.boundary_map(m)
    door = next(d for d in bm["doors"] if d["item_id"] == "p3-9")
    assert door["severity"] == "per-item"
    assert "checkpoint and run-lock evidence remain absent" in door["detail"]
    assert bm["single_attempts_passed"] == []


def test_submitted_single_on_an_unlocked_page_keeps_its_door():
    m = locked_module()
    page(m, 5)["items"].append(top("p5-9", "text-response", True, attempts="single", answered=True))
    door = next(d for d in b.boundary_map(m)["doors"] if d["item_id"] == "p5-9")
    assert "page 5 does not read locked" in door["detail"]


def test_committed_checkpoint_is_reported_as_passed():
    bm = b.boundary_map(locked_module())
    assert [c["index"] for c in bm["checkpoints_passed"]] == [1]
    assert bm["checkpoints_inventory_measured"] is True


def test_report_page_is_never_inferred():
    m = locked_module(run_locked=True)
    page(m, 6)["items"] = [top("p6-1", "subpage", None), child("p6-2", "p6-1")]
    assert "p6-2" not in b.inferred_locks(m)


def test_boundary_functions_do_not_mutate_the_module():
    m = locked_module()
    before = copy.deepcopy(m)
    b.boundary_map(m)
    b.inferred_locks(m)
    b.first_stop_boundary(m)
    assert m == before
