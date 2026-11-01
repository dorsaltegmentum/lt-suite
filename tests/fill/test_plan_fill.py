# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""plan_fill.py on single-attempt items: an unapproved draft is held before the payload,
each single prints its captured state ("answered, not submitted"), and MANUAL MODE lists the
pages the validator's manual-approval rules would cover."""
from __future__ import annotations

import json

from fillkit import ORIGIN, answers, drafted, item, module


def single_mod(**lesson):
    mod = module([(4, [item("p4-10", "10", "text-response", "text-question", attempts="single"),
                       item("p4-11", "11", "text-response", "text-question")]),
                  (5, [item("p5-20", "20", "text-response", "text-question", attempts="single",
                            answered=True)])])
    mod["lesson"].update(lesson)
    return mod


def plan(kit, mod, ans, *extra):
    kit.config(f'schema_version = 1\n[lt]\norigin = "{ORIGIN}"\n')
    kit.write("module.json", mod)
    kit.write("answers.json", ans)
    return kit.run("plan_fill.py", "module.json", "--answers", "answers.json", *extra)


def entries(doc):
    return {i["item_id"]: i for p in doc["pages"] for i in p["items"]}


def test_an_unapproved_single_attempt_draft_is_held_for_approval(kit):
    r = plan(kit, single_mod(), answers([drafted("p4-10"), drafted("p4-11"), drafted("p5-20")]), "--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    rows = entries(doc)
    assert rows["p4-10"]["fillable"] is False
    assert rows["p4-10"]["skip"]["class"] == "single-attempt-unapproved"
    assert rows["p4-11"]["fillable"] is True
    assert (doc["fillable_items"], doc["held_for_approval"]) == (1, 2)


def test_an_approved_single_attempt_draft_stays_fillable(kit):
    ok = {"approved_by": "user", "approval_id": "A-0123abcd"}
    r = plan(kit, single_mod(), answers([drafted("p4-10", single_attempt_approval=ok)]), "--json")
    rows = entries(json.loads(r.stdout))
    assert rows["p4-10"]["fillable"] is True and "skip" not in rows["p4-10"]


def test_answered_alone_is_not_submitted(kit):
    r = plan(kit, single_mod(), answers([drafted("p4-11")]), "--json")
    rows = entries(json.loads(r.stdout))
    assert (rows["p5-20"]["captured_answered"], rows["p5-20"]["captured_submitted"],
            rows["p5-20"]["captured_commit_state"]) == (True, False, "open")
    text = plan(kit, single_mod(), answers([drafted("p4-11")])).stdout
    assert "single-attempt: answered, not submitted (read_only false, commit_state open)" in text
    assert "single-attempt: not answered, not submitted" in text
    assert "SKIP: single-attempt-unapproved" not in text   # p4-10 has no draft here


def test_manual_mode_is_none_when_no_rule_covers_a_planned_page(kit):
    r = plan(kit, single_mod(), answers([drafted("p4-10"), drafted("p4-11")]))
    assert "MANUAL MODE (predicted from the plan): none" in r.stdout
    assert "held for approval: 1" in r.stdout
    assert "SKIP: single-attempt-unapproved: needs-approval" in r.stdout


def test_manual_mode_names_pages_after_a_submitted_single_and_the_stop_page(kit):
    mod = single_mod(run_locked=True)
    mod["pages"][1]["items"][0]["read_only"] = True   # p5-20 submitted on a locked run
    mod["pages"].append({"page_index": 6, "page_no": 6, "route": None, "title": "Checkpoint",
                         "url": f"{ORIGIN}/i/inst0001/student/courses/1/runs/101/run/page/6",
                         "is_report_page": False, "is_checkpoint": True, "items": []})
    r = plan(kit, mod, answers([drafted("p4-11")]), "--json")
    rows = {(m["page_index"], m["rule"]) for m in json.loads(r.stdout)["manual_mode_pages"]}
    assert rows == {(5, "passed-single"), (6, "commit-boundary")}
    text = plan(kit, mod, answers([drafted("p4-11")])).stdout
    assert "MANUAL MODE (predicted from the plan): approval_mode must be manual" in text
    assert "single-attempt: submitted (read_only true, commit_state open, answered true)" in text


def test_the_payload_still_refuses_what_the_plan_holds(kit):
    kit.config(f'schema_version = 1\n[lt]\norigin = "{ORIGIN}"\n')
    rc, pay, out = kit.payload(single_mod(), answers([drafted("p4-10"), drafted("p4-11")]))
    assert rc == 0, out
    assert [(r["item"], r["reason"]) for r in pay["refused"]] == [("p4-10", "refused-single-attempt")]
    assert pay["not_emitted"] == []


def test_the_plan_always_writes_route_source_and_refuses_a_legacy_capture(kit):
    from ltcore import ltjs
    mod = single_mod()
    mod["pages"][0]["title"] = "Pre-reading"
    doc = json.loads(plan(kit, mod, answers([drafted("p4-11")]), "--json").stdout)
    assert "route_source" in doc and doc["route_source"] is None
    assert doc["route_refusal"] == ltjs.LEGACY_CAPTURE
    assert all(p["url"] is None and p["page_no"] is None for p in doc["pages"] + doc["flow_pages"])
    mod["route_source"] = "datakey+location"
    doc = json.loads(plan(kit, mod, answers([drafted("p4-11")]), "--json").stdout)
    assert (doc["route_source"], doc["route_refusal"]) == ("datakey+location", None)
    assert [p["page_no"] for p in doc["flow_pages"]] == [4, 5]
