# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paths, loaders and the operations that turn a fixture module into a boundary variant.

Imported by the tests and by conftest.py. pytest runs with --import-mode=importlib, which
puts no test folder on sys.path, so each importer inserts this folder first.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures"
SHARED = REPO / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

# module id -> file stem (the demo run c1-r107 is stored as module-demo.json)
STEMS = {"c1-r101": "c1-r101", "c1-r102": "c1-r102", "c1-r103": "c1-r103", "c1-r104": "c1-r104",
         "c1-r105": "c1-r105", "c1-r106": "c1-r106", "c1-r107": "demo"}
FILL_REPORT_IDS = ("c1-r101", "c1-r102", "c1-r103", "c1-r104", "c1-r106", "c1-r107")


def read_json(rel: str):
    return json.loads((FIX / rel).read_text(encoding="utf-8"))


def module_path(mid: str) -> Path:
    return FIX / "modules" / f"module-{STEMS[mid]}.json"


def _page(mod, page_index):
    return next(p for p in mod["pages"] if p["page_index"] == page_index)


def _item(mod, item_id):
    return next(i for p in mod["pages"] for i in p["items"] if i["item_id"] == item_id)


def _shift(mod, at, delta):
    """Move every page with page_index >= at by delta, and the checkpoint records with it."""
    for p in mod["pages"]:
        if p["page_index"] >= at:
            p["page_index"] += delta
    cb = mod["commit_boundaries"]
    cb["checkpoint_pages"] = [i + delta if i >= at else i for i in cb["checkpoint_pages"]]
    for e in cb["checkpoints"]:
        if e.get("page_index") is not None and e["page_index"] >= at:
            e["page_index"] += delta


def apply_ops(base: dict, ops: list) -> dict:
    """Apply the operations of expected-boundaries.json to a deep copy of a module. The
    meaning of each operation is written in tests/fixtures/README.md."""
    mod = copy.deepcopy(base)
    for op in ops:
        kind = op["op"]
        if kind == "item":
            _item(mod, op["item_id"]).update(op["set"])
        elif kind == "lesson":
            mod["lesson"].update(op["set"])
        elif kind == "checkpoints":
            mod["commit_boundaries"]["checkpoints"] = copy.deepcopy(op["value"])
        elif kind == "add_item":
            _page(mod, op["page_index"])["items"].append(copy.deepcopy(op["item"]))
        elif kind == "remove_page":
            n = op["page_index"]
            mod["pages"] = [p for p in mod["pages"] if p["page_index"] != n]
            cb = mod["commit_boundaries"]
            cb["checkpoint_pages"] = [i for i in cb["checkpoint_pages"] if i != n]
            cb["checkpoints"] = [e for e in cb["checkpoints"] if e.get("page_index") != n]
            _shift(mod, n + 1, -1)
        elif kind == "insert_checkpoint_page":
            n = op["before_page_index"]
            _shift(mod, n, 1)
            mod["pages"].append({
                "page_index": n, "page_no": None, "route": "checkpoint", "url": None,
                "title": "Checkpoint", "is_report_page": False, "is_checkpoint": True,
                "content": [], "items": [],
                "checkpoint": {"index": op["index"], "committed": op["committed"]}})
            mod["pages"].sort(key=lambda p: p["page_index"])
            cb = mod["commit_boundaries"]
            cb["checkpoint_pages"] = sorted(cb["checkpoint_pages"] + [n])
            cb["checkpoints"].append({"index": op["index"], "page_index": n,
                                      "committed": op["committed"], "in_flow": True,
                                      "source": "lessonFlowPages"})
        else:
            raise ValueError(f"unknown operation {kind!r}")
    return mod
