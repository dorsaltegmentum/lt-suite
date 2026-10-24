# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for make_fill_report.py on the synthetic run c1-r102: a valid report, the fallback
and unconfirmed pages listed on stdout and in each item's stopped_reason, the approval_mode
values, the refusals before anything is written, and the validator from the skill's shared
copy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fillkit import ORIGIN, REPO  # noqa: E402

FIX = REPO / "tests" / "fixtures"
MOD = FIX / "modules" / "module-c1-r102.json"
ANS = FIX / "answers" / "answers-c1-r102.json"
RCP = FIX / "runs" / "c1-r102" / "receipts.json"
NOTES = FIX / "runs" / "c1-r102" / "fill-notes.json"


@pytest.fixture
def rep(kit):
    kit.config(f'schema_version = 1\n[lt]\norigin = "{ORIGIN}"\n')
    kit.write("waits.json", {"save_waits": [
        {"receipt": "fill-p2.json", "receipt_sha256": "a" * 64, "pages": [2], "save_signal": "fallback",
         "waited_ms": 4100, "keys_total": 3, "keys_saved": 3, "keys_pending": 0, "save_errors": 0,
         "calibrated": False, "ok": True, "fatal": None},
        {"receipt": "fill-b1.json", "receipt_sha256": "b" * 64, "pages": [3, 4], "save_signal": "unconfirmed",
         "waited_ms": 20000, "keys_total": 2, "keys_saved": 1, "keys_pending": 1, "save_errors": 0,
         "calibrated": True, "ok": True, "fatal": None}]})

    def run(*extra, notes=None, receipts=None):
        npath = kit.write("notes.json", notes) if notes is not None else NOTES
        rpath = kit.write("receipts.json", receipts) if receipts is not None else RCP
        r = kit.run("make_fill_report.py", "--module", MOD, "--answers", ANS, "--receipts", rpath,
                    "--notes", npath, "--save-waits", "waits.json", "--out", "report.json", *extra)
        doc = kit.read("report.json") if (kit.tmp / "report.json").exists() else None
        return r, doc
    return run


def notes_doc(**kw):
    d = json.loads(NOTES.read_text())
    d.update(kw)
    return d


def test_a_valid_report_lists_the_weak_pages(rep):
    r, doc = rep()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fallback pages: 2 " in r.stdout
    assert "unconfirmed pages: 3, 4 " in r.stdout
    rows = {row["item_id"]: row for row in doc["items"]}
    assert rows["p2-11"]["result"] == "filled" and rows["p2-11"]["verified"] is True
    assert "fallback wait" in rows["p2-11"]["stopped_reason"]
    assert "unconfirmed" in rows["p4-31"]["stopped_reason"]
    assert [w["save_signal"] for w in doc["save_waits"]] == ["fallback", "unconfirmed"]


def test_a_receipt_whose_hashes_differ_is_failed(rep):
    rows = json.loads(RCP.read_text())
    rows[0]["observed_hash"] = 1
    r, doc = rep(receipts=rows)
    item = next(i for i in doc["items"] if i["item_id"] == rows[0]["item_id"])
    assert (item["result"], item["verified"]) == ("failed", False)


@pytest.mark.parametrize("mode,code", [("manual", 0), ("auto", 1), ("skip-all", 1)])
def test_approval_mode_values(rep, mode, code):
    # c1-r102 reaches the run-level commit, so the validator wants manual supervision.
    r, doc = rep(notes=notes_doc(approval_mode=mode))
    assert doc["approval_mode"] == mode
    assert r.returncode == code, r.stdout + r.stderr


@pytest.mark.parametrize("label,notes", [
    ("approval_mode missing", {"approval_mode": None}),
    ("a note sets verified", {"items": {"p2-11": {"method": "view-model", "verified": True}}}),
    ("an unknown top-level key", {"extra": 1}),
])
def test_refusals_write_nothing(rep, label, notes):
    r, doc = rep(notes=notes_doc(**notes))
    assert r.returncode == 3, label
    assert doc is None, label


def test_refuses_a_planned_item_missing_both_receipt_and_note(rep):
    rows = [x for x in json.loads(RCP.read_text()) if x["item_id"] != "p2-12"]
    n = notes_doc()
    n["items"] = {k: v for k, v in n["items"].items() if k != "p2-12"}
    r, doc = rep(notes=n, receipts=rows)
    assert r.returncode == 3 and doc is None


def test_no_validator_is_not_validated(rep):
    r, doc = rep("--validator", "missing/validate.py")
    assert r.returncode == 1
    assert "NOT VALIDATED" in r.stderr
    assert doc is not None


def test_the_validator_comes_from_the_shared_copy():
    sys.path.insert(0, str(REPO / "skills" / "lt-module-fill" / "scripts"))
    import make_fill_report  # noqa: PLC0415
    p = make_fill_report.validator_path()
    assert p is not None and p.name == "validate.py" and p.parent.name == "ltcore"
