# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fixture set agrees with the code that reads it.

Two independent oracles stand behind ltcore.boundaries: the hand-written
modules/expected-boundaries.json (written from the boundary rules, never from the output
of the code) and, in tests/capture, the boundary detector in capture.js run over the same
view models. This file holds the first and the validator pass over every fixture, plus the
hash bindings between the files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FIX, FILL_REPORT_IDS, STEMS, apply_ops, module_path, read_json  # noqa: E402

EXPECTED = read_json("modules/expected-boundaries.json")
VARIANTS = {v["id"]: v for v in EXPECTED["variants"]}


def stop_of(v):
    """A first-stop answer as [page_index, class, item_id] or None."""
    if v is None:
        return None
    if isinstance(v, dict):
        return [v["page_index"], v["class"], v.get("item_id")]
    return [v[0], v[1], v[2] if len(v) > 2 else None]


def facts(boundaries, mod):
    """What the expectation file states, read from the code."""
    bm = boundaries.boundary_map(mod)
    return {
        "first_stop": stop_of(boundaries.first_stop_boundary(mod)),
        "doors": [[d["class"], d["page_index"], d["item_id"]] for d in bm["doors"]],
        "single_attempts_passed": [d["item_id"] for d in bm["single_attempts_passed"]],
        "checkpoints_passed": [c["index"] for c in bm["checkpoints_passed"]],
        "inferred_locks": {k: v["class"] for k, v in boundaries.inferred_locks(mod).items()},
    }


def wanted(e):
    return {
        "first_stop": stop_of(e["first_stop"]),
        "doors": [[d["class"], d["page_index"], d["item_id"]] for d in e["doors"]],
        "single_attempts_passed": e["single_attempts_passed"],
        "checkpoints_passed": e["checkpoints_passed"],
        "inferred_locks": e["inferred_locks"],
    }


# ---- the hand-written file --------------------------------------------------------------

def test_expectations_name_every_module_and_known_bases(modules):
    assert set(EXPECTED["modules"]) == set(STEMS)
    assert len(VARIANTS) == len(EXPECTED["variants"]), "duplicate variant id"
    assert all(v["base"] in STEMS for v in VARIANTS.values())
    for mid, e in EXPECTED["modules"].items():
        cb = modules[mid]["commit_boundaries"]
        assert e["checkpoint_pages"] == cb["checkpoint_pages"], mid
        assert e["single_attempt_items"] == cb["single_attempt_items"], mid


def test_expectations_cover_every_boundary_class():
    """A set of expectations that never names a class cannot catch a loss of it."""
    entries = list(EXPECTED["modules"].values()) + EXPECTED["variants"]
    doors = {d["class"] for e in entries for d in e["doors"]}
    assert doors == {"report-page", "checkpoint-page", "single-attempt-item", "unmapped"}
    assert any(e["first_stop"] for e in entries)
    assert any(e["single_attempts_passed"] for e in entries)
    assert any(e["checkpoints_passed"] for e in entries)
    assert {c for e in entries for c in e["inferred_locks"].values()} == {
        "read-only-inferred", "lock-unknown"}
    assert any(not e["first_stop"] and e["doors"] for e in entries), "per-item doors that do not stop"


def test_every_variant_changes_something(modules):
    for v in EXPECTED["variants"]:
        assert apply_ops(modules[v["base"]], v["ops"]) != modules[v["base"]], v["id"]


# ---- ltcore.boundaries against the hand-written file ------------------------------------

@pytest.mark.parametrize("mid", sorted(EXPECTED["modules"]))
def test_first_stop_boundary_equals_expected_on_each_module(boundaries, modules, mid):
    mod = modules[mid]
    assert stop_of(boundaries.first_stop_boundary(mod)) == stop_of(EXPECTED["modules"][mid]["first_stop"])


@pytest.mark.parametrize("mid", sorted(EXPECTED["modules"]))
def test_boundary_map_equals_expected_on_each_module(boundaries, modules, mid):
    assert facts(boundaries, modules[mid]) == wanted(EXPECTED["modules"][mid])


@pytest.mark.parametrize("vid", sorted(VARIANTS))
def test_boundaries_equal_expected_on_each_mutation_variant(boundaries, modules, vid):
    v = VARIANTS[vid]
    mod = apply_ops(modules[v["base"]], v["ops"])
    assert facts(boundaries, mod) == wanted(v), v["why"]


def test_first_door_is_the_first_stop(boundaries, modules):
    for mid, mod in modules.items():
        door = boundaries.first_door(mod)
        stop = boundaries.first_stop_boundary(mod)
        assert (door is None) == (stop is None), mid
        if door:
            assert (door["page_index"], door["class"], door["item_id"]) == tuple(stop), mid


# ---- the validator passes every fixture --------------------------------------------------

def documents():
    out = []
    for mid, stem in STEMS.items():
        out.append(("module", mid, f"modules/module-{stem}.json", None))
        out.append(("reference", mid, f"references/reference-{stem}.json", None))
        out.append(("answers", mid, f"answers/answers-{stem}.json", mid))
        out.append(("run", mid, f"runs/{mid}/run.json", None) if (FIX / f"runs/{mid}/run.json").is_file()
                   else None)
    for mid in FILL_REPORT_IDS:
        out.append(("fill-report", mid, f"fill-reports/fill-report-{STEMS[mid]}.json", mid))
    return [d for d in out if d]


def approvals_for(mid, kind):
    """The approvals file, when it holds every id the document cites."""
    p = FIX / "approvals" / "approvals-c1-r104.json"
    if mid != "c1-r104" or not p.is_file():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    ids = {a["approval_id"]: a for a in doc["approvals"]}
    target = FIX / ("answers/answers-c1-r104.json" if kind == "answers"
                    else "fill-reports/fill-report-c1-r104.json")
    body = json.loads(target.read_text(encoding="utf-8"))
    if kind == "answers":
        cited = [i["single_attempt_approval"]["approval_id"] for i in body["items"]
                 if i.get("single_attempt_approval")]
    else:
        cited = [d["approval_id"] for d in body.get("user_decisions", [])]
    return p if all(c in ids for c in cited) else None


@pytest.mark.parametrize("kind,mid,rel,module", documents(), ids=lambda v: str(v)[:40])
def test_validator_passes_every_fixture(validate, kind, mid, rel, module):
    kw = {"model_answer_read": True}
    if module:
        kw["module"] = module_path(module)
    if kind in ("answers", "fill-report"):
        ap = approvals_for(mid, "answers" if kind == "answers" else "fill-report")
        if ap:
            kw["approvals"] = ap
    res = validate.validate_file(FIX / rel, **kw)
    assert res.exit_code == 0, (res.fatal, res.schema_errors, res.rail_errors)


def test_validator_passes_the_approvals_file(validate):
    p = FIX / "approvals" / "approvals-c1-r104.json"
    if not p.is_file():
        pytest.skip("not run: tests/fixtures/approvals/approvals-c1-r104.json missing")
    res = validate.validate_file(p)
    assert res.exit_code == 0, (res.fatal, res.schema_errors, res.rail_errors)


# ---- hash bindings between files ---------------------------------------------------------

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_answers_are_bound_to_their_module(modules, mid):
    digest = pytest.importorskip("ltcore.digest", reason="not run: ltcore.digest missing")
    a_path = FIX / f"answers/answers-{STEMS[mid]}.json"
    a = json.loads(a_path.read_text(encoding="utf-8"))
    src = a["source_module"]
    target = (a_path.parent / src["path"]).resolve()
    assert target == module_path(mid).resolve()
    assert src["sha256"] == sha(target)
    assert src["question_digest"] == digest.question_digest(modules[mid])
    assert a["module_id"] == mid and a["run_id"] == modules[mid]["run_id"]


@pytest.mark.parametrize("mid", FILL_REPORT_IDS)
def test_fill_reports_are_bound_to_their_inputs(mid):
    r = read_json(f"fill-reports/fill-report-{STEMS[mid]}.json")
    root = FIX / "fill-reports"
    assert r["source_answers"]["sha256"] == sha(root / r["source_answers"]["path"])
    if "source_receipts" in r:
        rp = root / r["source_receipts"]["path"]
        assert r["source_receipts"]["sha256"] == sha(rp)
        receipts = {x["item_id"]: x for x in json.loads(rp.read_text(encoding="utf-8"))}
        filled = {i["item_id"]: i for i in r["items"] if i["result"] == "filled"}
        assert set(receipts) == set(filled)
        for iid, it in filled.items():
            assert it["receipt"] == {k: receipts[iid][k]
                                     for k in ("expected_hash", "observed_hash", "observed_at")}


def test_single_attempt_approval_is_the_one_in_the_approvals_file():
    p = FIX / "approvals" / "approvals-c1-r104.json"
    if not p.is_file():
        pytest.skip("not run: tests/fixtures/approvals/approvals-c1-r104.json missing")
    approvals = json.loads(p.read_text(encoding="utf-8"))["approvals"]
    single = [a for a in approvals if a["kind"] == "single-attempt" and a["item_id"] == "p3-12"]
    assert len(single) == 1
    answers = read_json("answers/answers-c1-r104.json")
    drafts = {i["item_id"]: i for i in answers["items"]}
    assert drafts["p3-12"]["single_attempt_approval"]["approval_id"] == single[0]["approval_id"]
