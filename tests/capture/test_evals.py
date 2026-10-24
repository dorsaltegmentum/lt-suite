# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The capture evals stay true to the fixture they cite, and the trigger list is well formed.

Eval 1 and 2 state numbers and ids from tests/fixtures/modules/module-demo.json. When the
fixture generator changes that file, this test names the claim that went stale.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVALS = REPO / "evals" / "lt-module-capture"
DEMO = REPO / "tests" / "fixtures" / "modules" / "module-demo.json"


def load(name: str) -> dict:
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


def test_every_eval_has_a_prompt_and_expectations_and_its_files_exist():
    doc = load("evals.json")
    assert doc["skill_name"] == "lt-module-capture"
    ids = [e["id"] for e in doc["evals"]]
    assert len(ids) == len(set(ids)) >= 4
    for e in doc["evals"]:
        assert e["prompt"].strip() and e["expectations"], e["id"]
        for f in e.get("files", []):
            assert (REPO / f).is_file(), f"eval {e['id']} names a missing file {f}"


def test_trigger_cases_are_unique_and_cover_both_kinds():
    doc = load("triggers.json")
    cases = doc["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["query"] for c in cases}) == len(cases)
    kinds = collections.Counter(c["should_trigger"] for c in cases)
    assert kinds[True] >= 8 and kinds[False] >= 8


@pytest.mark.skipif(not DEMO.is_file(), reason="not run: WP03 module-demo.json missing")
def test_eval_numbers_match_module_demo():
    d = json.loads(DEMO.read_text(encoding="utf-8"))
    items = [i for p in d["pages"] for i in p["items"]]
    by_type = collections.Counter(i["type"] for i in items)
    cb = d["commit_boundaries"]
    assert len(d["pages"]) == 2
    assert len(items) == 8 and d["census"]["answerable_total"] == 7
    assert by_type == {"multiple-choice": 3, "text-response": 2, "upload-and-annotate": 1,
                       "subpage": 1, "unmapped": 1}
    assert sum(i["answered"] for i in items) == 0
    assert cb["single_attempt_items"] == [] and cb["checkpoint_pages"] == [] and cb["checkpoints"] == []
    assert cb["checkpoints_container_present"] is True
    assert d["lesson"]["has_completion_page"] is False
    assert [p["page_index"] for p in d["pages"] if p.get("rat_candidate")] == [2]
    sub = next(i for i in items if i["item_id"] == "p1-13")
    assert sub["type"] == "subpage" and [i["item_id"] for i in items if i["parent_item_id"] == "p1-13"]
    nb = next(i for i in items if i["item_id"] == "p1-16")
    assert nb["type"] == "unmapped" and nb["degrade"]["reason"] == "notebook-never-written"
    up = next(i for i in items if i["item_id"] == "p1-12")
    assert up["target_geometry"]["presented_ratio"] is None
    assert up["degrade"]["reason"] == "geometry-unknown-until-presented"
    assert [m["key"] for m in up["media"]] == [None]
