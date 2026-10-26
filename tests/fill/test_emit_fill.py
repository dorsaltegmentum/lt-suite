# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for emit_fill.py: what is refused before any script exists, the approvals file,
the committed capture c1-r104, the pages-per-call gate, the calibration tickets and their
four outcomes (auto-calibration, a failed calibration, a cross-page batch, a cross-page
miss), the identified transport a save record keeps (a save LtNet could not identify never
calibrates; a record without a write endpoint counts as absent), the explicit `calibrate`, the dialog record, the served build's integrity check and
the serve allow-list. Receipts are written as fill_lib.js returns them; tests/fill/*.test.js
runs the page side.
"""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fillkit import (ORIGIN, REPO, SCRIPTS, answers, drafted, item, jobs, module,  # noqa: E402
                     reasons, text_pages)

FIX = REPO / "tests" / "fixtures"
TPL = "/api/panels/{uuid}/state"
CONFIG = f'''schema_version = 1
[lt]
origin = "{ORIGIN}"
'''


def cfg(kit, extra: str = "") -> None:
    kit.config(CONFIG + f'[browser]\njs_tmp_dir = "{kit.tmp / "js"}"\n' + extra)


def calibration(kit) -> dict:
    p = kit.calibration_path
    return json.loads(p.read_text(encoding="utf-8")).get(ORIGIN, {}) if p.exists() else {}


def scripts(kit, *extra: str) -> dict:
    r = kit.emit("scripts", "--out", "out", "--no-render", *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    man = kit.read("out/scripts.json")
    man["stdout"] = r.stdout
    return man


def fills(man) -> list[list[str]]:
    return man["groups"]


def receipt_payload(kit, ticket=None):
    if ticket:
        for name in ("scripts.json", "scripts_calibrate.json"):
            path = kit.tmp / "out" / name
            if path.is_file():
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if any(t.get("ticket") == ticket for t in manifest.get("tickets", [])):
                    named = Path(manifest["payload"])
                    return named if named.is_absolute() else kit.tmp / named
    return kit.tmp / "out/fill-payload.json"


def fill_receipt(kit, name: str, pages: list[int], written: list[str], *, signal: str, calibrated: bool,
                 ticket: str | None = None, doc: str = "a1a1a1a1a1a1a1a1", client: str = "c1-2-0badf00d",
                 ack=("net", "PUT", TPL), ok: bool = True, extra_items=None, **kw) -> Path:
    rec = {"ok": ok, "module": "c1-r101", "run": 101, "mode": "fill", "doc_id": doc, "client_sig": client,
           "calibration_stale": False, "ticket": ticket,
           "items": [{"item": i, "result": "written", "page": int(i[1:].split("-")[0])} for i in written]
                    + list(extra_items or []),
           "save_signal": signal, "calibrated": calibrated, "waited_ms": 4100,
           "keys_total": len(written), "keys_saved": len(written), "keys_pending": 0, "save_errors": 0,
           "jq": "1/1", "ack_source": ack[0], "ack_method": ack[1], "ack_path_template": ack[2]}
    if len(pages) == 1:
        rec["page"] = pages[0]
    else:
        rec["batch"] = pages
    payload = receipt_payload(kit, ticket)
    if payload.is_file():
        rec["source_payload_sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    rec.update(kw)
    return kit.write(name, rec)


def verify_receipt(kit, name: str, rows: dict[str, str], *, doc: str = "b2b2b2b2b2b2b2b2",
                   client: str = "c1-2-0badf00d", fresh: bool = True, ticket=None) -> Path:
    return kit.write(name, {"ok": True, "module": "c1-r101", "run": 101, "mode": "fill", "doc_id": doc,
                            "client_sig": client, "fresh_load": fresh, "waited": None,
                            "observed_at": "2000-01-01T00:00:00Z",
                            "source_payload_sha256": hashlib.sha256(receipt_payload(kit, ticket).read_bytes()).hexdigest(),
                            "items": [[i, state, 8, 12345] for i, state in rows.items()]})


def record(kit, fill_paths, verify_path) -> subprocess.CompletedProcess:
    destination = kit.run_dir() / 'fill-js/fill-payload.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(kit.tmp / 'out/fill-payload.json', destination)
    return kit.emit("record", "--module", "module.json", "--fill", *map(str, fill_paths),
                    "--verify", str(verify_path), "--payload", str(destination))


@pytest.fixture
def eight(kit):
    """Pages 1 to 8, one text item each (p1-10 ... p8-80), drafted, payload written."""
    cfg(kit)
    mod = module(text_pages(8))
    rc, pay, out = kit.payload(mod, answers([drafted(f"p{p}-{p * 10}") for p in range(1, 9)]))
    assert rc == 0, out
    return kit


def ticket_of(man, kind: str) -> dict:
    return next(t for t in man["tickets"] if t["kind"] == kind)


# ---- the hash ------------------------------------------------------------------------------------
def test_hash_twins(emit_mod):
    E = emit_mod
    assert E.djb2("") == 5381
    assert E.djb2("a") == 5381 * 33 + 97
    assert E.djb2(chr(0x1F600)) == ((5381 * 33 + 0xD83D) * 33 + 0xDE00) & 0xFFFFFFFF
    assert E.page_text("<p> a&amp;amp;b </p><br/><td>c&nbsp;d</td>") == "a&amp;b c d"
    assert E.canon_pairs("lab:", [(0, "x" + chr(0xFF5E)), (0, "x" + chr(0x1F600))]).split("\n")[0] == \
        "lab:0\tx" + chr(0x1F600)


# ---- single attempt and the approvals file -------------------------------------------------------
def single_mod():
    return module([(4, [item("p4-10", "10", "text-response", "text-question", attempts="single"),
                        item("p4-11", "11", "text-response", "text-question")]),
                   (5, [item("p5-20", "20", "text-response", "text-question")])])


def test_single_attempt_without_approval_is_refused_alone(kit):
    cfg(kit)
    rc, pay, out = kit.payload(single_mod(), answers([drafted("p4-10"), drafted("p4-11"), drafted("p5-20")]))
    assert (rc, jobs(pay)) == (0, ["p4-11", "p5-20"])
    assert reasons(pay, "refused") == [("p4-10", "refused-single-attempt")]
    assert pay["not_emitted"] == []


def test_single_attempt_with_a_recorded_approval(kit):
    cfg(kit)
    kit.write("module.json", single_mod())
    r = kit.emit("approve", "--module", "module.json", "--item", "p4-10", "--quote", "Yes, write p4-10.")
    assert r.returncode == 0, r.stderr
    aid = r.stdout.strip().splitlines()[0]
    assert aid.startswith("A-") and len(aid) == 10
    from ltcore.digest import question_digest
    record = json.loads((kit.data / "runs/c1-r101/approvals.json").read_text(encoding="utf-8"))["approvals"][0]
    assert record["question_digest"] == question_digest(single_mod())
    ok = {"approved_by": "user", "approval_id": aid}
    rc, pay, out = kit.payload(single_mod(), answers([drafted("p4-10", single_attempt_approval=ok),
                                                      drafted("p4-11"), drafted("p5-20")]))
    assert (rc, jobs(pay)) == (0, ["p4-10", "p4-11", "p5-20"]), out
    job = pay["pages"]["4"]["jobs"][0]
    assert (job["single_attempt_approved"], job["approval_id"]) == (True, aid)
    assert pay["pages"]["4"]["single_attempt_keys"] == ["10"]
    assert any(aid in n for n in pay["notes"])


@pytest.mark.parametrize("label,approval", [
    ("an id the file does not have", {"approved_by": "user", "approval_id": "A-00000000"}),
    ("a malformed id", {"approved_by": "user", "approval_id": "approved by voice"}),
])
def test_single_attempt_bad_approval_refuses_the_run(kit, label, approval):
    cfg(kit)
    rc, _, out = kit.payload(single_mod(), answers([drafted("p4-10", single_attempt_approval=approval)]))
    assert rc == 3, f"{label}: {out}"


def test_approval_for_another_item_refuses(kit):
    cfg(kit)
    mod = module([(4, [item("p4-10", "10", "text-response", "text-question", attempts="single"),
                       item("p4-12", "12", "text-response", "text-question", attempts="single")])])
    kit.write("module.json", mod)
    aid = kit.emit("approve", "--module", "module.json", "--item", "p4-12", "--quote", "Yes, p4-12.").stdout.split()[0]
    rc, _, out = kit.payload(mod, answers([drafted("p4-10", single_attempt_approval={"approved_by": "user",
                                                                                     "approval_id": aid})]))
    assert rc == 3, out


def test_approve_refuses_a_multi_attempt_item_and_an_empty_quote(kit):
    cfg(kit)
    kit.write("module.json", single_mod())
    assert kit.emit("approve", "--module", "module.json", "--item", "p4-11", "--quote", "yes").returncode == 3
    assert kit.emit("approve", "--module", "module.json", "--item", "p4-10", "--quote", "  ").returncode == 3
    assert kit.emit("approve", "--module", "module.json", "--item", "p9-99", "--quote", "yes").returncode == 3


def test_an_approval_on_a_multi_attempt_item_refuses(kit):
    cfg(kit)
    rc, _, _ = kit.payload(single_mod(), answers([drafted("p4-11", single_attempt_approval={
        "approved_by": "user", "approval_id": "A-12345678"})]))
    assert rc == 3


# ---- the committed capture c1-r104 ---------------------------------------------------------------
def test_committed_capture_fills_only_the_team_quiz(kit):
    cfg(kit)
    for name, src in (("module.json", FIX / "modules/module-c1-r104.json"),
                      ("answers.json", FIX / "answers/answers-c1-r104.json"),
                      ("approvals.json", FIX / "approvals/approvals-c1-r104.json")):
        shutil.copy(src, kit.tmp / name)
    kit.write("answers.json", kit.read("answers.json"))
    for extra in ([], ["--mark", "9=trat", "--answer", "Team test"]):
        r = kit.run("page_kinds.py", "mark" if extra else "classify", "--module", "module.json", "--answers", "answers.json", *extra)
        assert r.returncode in (0, 1), r.stdout + r.stderr
    r = kit.run("page_kinds.py", "choose", "--module", "module.json", "--answers", "answers.json", "--page", "9", "--fill", "draft", "--answer", "Draft the team page")
    assert r.returncode == 0, r.stdout + r.stderr
    r = kit.emit("payload", "--module", "module.json", "--answers", "answers.json", "--out", "out",
                 "--approvals", "approvals.json")
    assert r.returncode == 0, r.stdout + r.stderr
    pay = kit.read("out/fill-payload.json")
    assert {k: len(v["jobs"]) for k, v in pay["pages"].items()} == {"9": 15}
    assert pay["refused"] == []
    # p3-12 is submitted and read-only: the plan holds it back even with an approval.
    assert reasons(pay, "not_emitted") == [("p3-12", "plan: read-only")]
    assert pay["pages"]["9"]["single_attempt_locked"] == [[3, "12"]]
    plan = kit.read("out/plan.json")
    assert plan["never_navigated"] and plan["origin"] == ORIGIN
    assert 10 in plan["never_navigated_pages"]
    assert [p["page_index"] for p in plan["flow_pages"]] == list(range(1, 11))
    assert plan["checkpoint_pages"] == []
    run = f"{ORIGIN}/i/inst0001/student/courses/1/runs/104/run"
    for page, code in ((9, 0), (10, 1)):
        r = kit.run("_shared/ltcore/ltjs.py", "check-url", "--plan", "out/plan.json", "--from", f"{run}/page/1",
                    f"{run}/page/{page}")
        assert r.returncode == code, (page, r.stdout + r.stderr)


def test_committed_capture_without_the_approvals_file_refuses(kit):
    cfg(kit)
    shutil.copy(FIX / "modules/module-c1-r104.json", kit.tmp / "module.json")
    shutil.copy(FIX / "answers/answers-c1-r104.json", kit.tmp / "answers.json")
    r = kit.emit("payload", "--module", "module.json", "--answers", "answers.json", "--out", "out",
                 "--approvals", "missing.json")
    assert r.returncode == 3, r.stdout + r.stderr


# ---- binding to the capture ------------------------------------------------------------------------
def test_binding_to_the_capture(kit, emit_mod):
    cfg(kit)
    mod = module([(4, [item("p4-10", "10", "text-response", "text-question")])])
    assert kit.payload(mod, answers([drafted("p4-10")], module_id="c1-r102"))[0] == 3
    assert kit.payload(mod, answers([drafted("p4-10")], run_id=102))[0] == 3
    sm = {"path": "module.json", "sha256": "0" * 64, "question_digest": "f" * 64}
    assert kit.payload(mod, answers([drafted("p4-10")], source_module=sm))[0] == 3
    sm["question_digest"] = emit_mod.question_digest(mod)
    rc, pay, _ = kit.payload(mod, answers([drafted("p4-10")], source_module=sm))
    assert (rc, pay["notes"]) == (0, [])


# ---- plan, pages and types ---------------------------------------------------------------------------
def test_plan_pages_and_types(kit):
    cfg(kit)
    mod = module([(4, [item("p4-10", "10", "text-response", "text-question"),
                       item("p4-11", "11", "text-response", "text-question", read_only=True),
                       item("p4-12", "12", "data-value", "kcDataValue"),
                       item("p4-13", "13", "unmapped", "notebook", automation="degrade-todo",
                            degrade={"class": "lifts-on-capture-support", "reason": "unmapped-type"}),
                       item("p4-14", "14", "upload-and-annotate", "image-annotation",
                            input_mode="allowImageAndAnnotation", automation="resilient-selector"),
                       item("p4-15", "15", "multiple-choice", "select",
                            options=[{"option_id": "o1", "text": "A"}, {"option_id": "o2", "text": "B"}])]),
                  (5, [item("p5-20", "20", "text-response", "text-question")]),
                  (6, [item("p6-30", "30", "text-response", "text-question")])])
    a = answers([drafted("p4-10"), drafted("p4-11"), drafted("p4-12"), drafted("p4-13"),
                 drafted("p4-14", needs_external_image=True, asset_refs=["x.png"]),
                 drafted("p4-15", proposed_selection=["o2"]), drafted("p5-20"), drafted("p6-30")])
    rc, pay, out = kit.payload(mod, a, "--pages", "4-5", "--exclude", "p5-20")
    assert jobs(pay) == ["p4-10", "p4-15"], out
    sel = pay["pages"]["4"]["jobs"][1]
    assert (sel["want"], sel["want_text"]) == ([1], ["B"])
    assert reasons(pay, "refused") == [("p4-12", "no-write-path")]
    assert reasons(pay, "not_emitted") == [("p4-11", "plan: read-only"), ("p4-13", "plan: unmapped"),
                                           ("p5-20", "excluded by --exclude"), ("p6-30", "outside --pages")]
    assert [t["item"] for t in pay["pages"]["4"]["todo"]] == ["p4-14"]
    assert sorted(n.split(" ")[0] for n in pay["notes"] if n.startswith("--")) == ["--exclude", "--pages"]


def test_labels_and_categories(kit):
    cfg(kit)
    opts = [{"option_id": "o1", "text": "alpha"}, {"option_id": "o2", "text": "zeta"}]
    lab = lambda iid, key: item(iid, key, "image-labeling", "kcImageLabelling", options=opts,  # noqa: E731
                                constraints={"note": "presentedRatio=0.5 locations=2"})
    cat = lambda iid, key: item(iid, key, "categorization", "categories", options=opts,  # noqa: E731
                                constraints={"categories": ["Bin A", "Bin B"]})
    mod = module([(4, [lab("p4-10", "10"), lab("p4-11", "11"), lab("p4-12", "12"), lab("p4-13", "13"),
                       cat("p4-14", "14"), cat("p4-15", "15")])])
    lp = lambda lab_, tgt, **kw: dict({"label": lab_, "target": tgt}, **kw)  # noqa: E731
    a = answers([
        drafted("p4-10", label_placements=[lp("alpha", "left, anchor 1 at 40.0% across, 50.5% down"),
                                           lp("zeta", "right, anchor 0 at 90.0% across, 10.0% down")]),
        drafted("p4-11", label_placements=[lp("alpha", "somewhere on the left")]),
        drafted("p4-12", label_placements=[lp("omega", "left, anchor 0 at 1.0% across, 1.0% down")]),
        drafted("p4-13", label_placements=[lp("alpha", "x, anchor 0 at 1.0% across, 1.0% down"),
                                           lp("zeta", "y, anchor 0 at 2.0% across, 2.0% down")]),
        drafted("p4-14", label_placements=[lp("alpha", "Bin B"), lp("zeta", "Bin A", target_index=0)]),
        drafted("p4-15", label_placements=[lp("alpha", "Bin C")])])
    kit.write("anchors.json", {"p4-11": {"alpha": {"loc": 1, "x": 0.4, "y": 0.5}}})
    rc, pay, out = kit.payload(mod, a, "--anchors", "anchors.json")
    js = {j["item"]: j for j in pay["pages"]["4"]["jobs"]}
    assert js["p4-10"]["placements"] == [{"label": "alpha", "loc": 1, "x": 0.4, "y": 0.505},
                                         {"label": "zeta", "loc": 0, "x": 0.9, "y": 0.1}]
    assert js["p4-11"]["placements"] == [{"label": "alpha", "loc": 1, "x": 0.4, "y": 0.5}]
    assert js["p4-14"]["placements"] == [{"label": "alpha", "cat_index": 1, "cat": "Bin B"},
                                         {"label": "zeta", "cat_index": 0, "cat": "Bin A"}]
    assert reasons(pay, "refused") == [("p4-12", "label-not-in-capture"), ("p4-13", "bad-location"),
                                       ("p4-15", "category-not-in-capture")]
    rc, pay, out = kit.payload(mod, a)
    assert ("p4-11", "no-location") in reasons(pay, "refused")


# ---- the scripts ---------------------------------------------------------------------------------------
@pytest.mark.requires_bin("node")
def test_comment_stripping_keeps_code_and_parses(emit_mod, tmp_path):
    for name in ("fill_lib.js", "probe_panels.js"):
        src = (SCRIPTS / "browser" / name).read_text(encoding="utf-8")
        stripped = emit_mod.strip_comments(src)
        code = [ln for ln in src.splitlines() if ln.strip() and not ln.strip().startswith(("//", "/*", "*"))]
        assert [ln for ln in code if ln not in stripped.splitlines()] == []
        f = tmp_path / name
        body = stripped if name == "fill_lib.js" else "(async () => {\n" + stripped + "\n});\n"
        f.write_text(body, encoding="utf-8", newline="\n")
        assert subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0, name


def test_inline_build_is_the_default_and_renders_with_the_guard(eight):
    r = eight.emit("scripts", "--out", "out", "--form", "function", "--no-stub")
    assert r.returncode == 0, r.stdout + r.stderr
    man = eight.read("out/scripts.json")
    body = (eight.tmp / "out" / man["calls"][0]["name"]).read_text(encoding="utf-8")
    assert "fetch(BASE" not in body and "LtFillLib.fillPage(" in body
    rendered = Path(man["calls"][0]["rendered"]).read_text(encoding="utf-8")
    assert "LtGuard" in rendered and "LtNet" in rendered
    assert Path(man["calls"][0]["rendered"]).parent == eight.tmp / "js"


# ---- pages per call and calibration ------------------------------------------------------------------
def test_uncalibrated_origin_one_page_per_call_with_a_save_ticket(eight):
    man = scripts(eight, "--pages-per-call", "3")
    assert fills(man) == [[str(p)] for p in range(1, 9)]
    assert "needs cross-page calibration" in man["stdout"]
    t = ticket_of(man, "save")
    assert (t["pages"], man["calibrated"]) == ([1], False)
    pend = calibration(eight)["pending"]
    assert [(p["ticket"], p["kind"], p["items"]) for p in pend] == [(t["ticket"], "save", ["p1-10"])]
    body = (eight.tmp / "out" / "_fill-c1-r101-p1.js").read_text(encoding="utf-8")
    assert f'"ticket": "{t["ticket"]}"' in body
    assert '"ticket"' not in (eight.tmp / "out" / "_fill-c1-r101-p2.js").read_text(encoding="utf-8")


def set_calibration(kit, **records) -> None:
    doc = {ORIGIN: {k: dict(v, version=1) for k, v in records.items()}}
    kit.calibration_path.parent.mkdir(parents=True, exist_ok=True)
    kit.calibration_path.write_text(json.dumps(doc), encoding="utf-8", newline="\n")


def test_pages_per_call_gate_with_a_cross_page_record(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    man = scripts(eight, "--pages-per-call", "3")
    assert fills(man) == [[str(p) for p in g] for g in (range(1, 4), range(4, 7), range(7, 9))]
    assert man["calibrated"] is True and man["tickets"] == []
    body = (eight.tmp / "out" / "_fill-c1-r101-b1.js").read_text(encoding="utf-8")
    assert "LtFillLib.fillPages(" in body and '"batch_pages": [1, 2, 3]' in body
    assert '"client_sig": "c1-2-0badf00d"' in body and '"version": 1' in body
    # No flag: fill.max_pages_per_call, 5 by default.
    assert fills(scripts(eight)) == [[str(p) for p in g] for g in (range(1, 6), range(6, 9))]


def test_a_record_of_another_format_version_counts_as_absent(eight):
    eight.calibration_path.parent.mkdir(parents=True, exist_ok=True)
    eight.calibration_path.write_text(json.dumps({ORIGIN: {
        "save": {"version": 0, "source": "net", "method": "PUT", "path_template": TPL},
        "cross_page": {"version": 1, "ok": True}}}), encoding="utf-8", newline="\n")
    man = scripts(eight)
    assert man["calibrated"] is False and fills(man)[0] == ["1"]
    assert "format version 0" in " ".join(man["notes"])


def calibrate_save(kit) -> dict:
    """The first page on the fallback wait, verified from a fresh load: the auto-calibration."""
    man = scripts(kit)
    t = ticket_of(man, "save")
    f = fill_receipt(kit, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False, ticket=t["ticket"])
    v = verify_receipt(kit, "r/verify-p1.json", {"p1-10": "ok"})
    r = record(kit, [f], v)
    assert r.returncode == 0, r.stdout + r.stderr
    return {"run": r, "ticket": t["ticket"]}


def test_auto_calibration_success(eight):
    out = calibrate_save(eight)
    st = calibration(eight)
    save = st["save"]
    assert {k: save[k] for k in ("version", "source", "method", "path_template", "client_sig", "ticket")} == \
        {"version": 1, "source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d",
         "ticket": out["ticket"]}
    assert len(save["receipt_sha256"]) == 64
    assert st["pending"] == []
    assert "save wait calibrated" in out["run"].stdout
    assert [h["event"] for h in st["history"]] == ["save-calibrated"]
    assert json.loads((eight.run_dir() / "save-waits.json").read_text(encoding="utf-8"))["save_waits"][0]["save_signal"] == "fallback"
    # The next scripts: calibrated, one 2-page trial batch with a cross_page ticket.
    man = scripts(eight, "--pages", "2-8")
    assert man["calibrated"] is True
    assert fills(man) == [["2", "3"], ["4"], ["5"], ["6"], ["7"], ["8"]]
    assert ticket_of(man, "cross_page")["pages"] == [2, 3]


def test_calibration_failure_keeps_the_fallback(eight):
    man = scripts(eight)
    t = ticket_of(man, "save")["ticket"]
    f = fill_receipt(eight, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False, ticket=t)
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "BAD"})
    r = record(eight, [f], v)
    assert r.returncode == 0
    st = calibration(eight)
    assert "save" not in st
    assert st["history"][-1]["event"] == "save-not-calibrated" and "p1-10 BAD" in st["history"][-1]["detail"]
    assert "NOT calibrated" in r.stdout
    man = scripts(eight, "--pages", "2-8")
    assert man["calibrated"] is False and fills(man) == [[str(p)] for p in range(2, 9)]
    assert ticket_of(man, "save")["pages"] == [2]


@pytest.mark.parametrize("why,kw,verify_kw", [
    ("no acknowledging signal", {"ack": (None, None, None)}, {}),
    ("the verify ran in the filling document", {"doc": "c3c3c3c3c3c3c3c3"}, {"doc": "c3c3c3c3c3c3c3c3"}),
    ("an empty written set", {"written": []}, {}),
    ("an item outside the ticket", {"extra_items": [{"item": "p2-20", "result": "written"}]}, {}),
    ("an unconfirmed wait", {"signal": "unconfirmed"}, {}),
    ("no doc_id on the fill receipt", {"doc": ""}, {}),
    ("no client_sig on the receipts", {"client": None}, {"client": None}),
    ("a ticket item left unwritten", {"extra_items": [{"item": "p1-10", "result": "occupied-skipped"}],
                                      "written": []}, {}),
    ("a model acknowledgement with no endpoint", {"ack": ("model", None, None)}, {}),
    ("an acknowledgement on a read method", {"ack": ("net", "GET", TPL)}, {}),
    ("a key whose save LtNet never identified", {"ack": ("model", "PUT", TPL), "keys_saved": 0, "keys_pending": 1,
                                                 "model_unidentified": 1}, {}),
    ("saves acknowledged on two endpoints", {"ack_endpoints": [f"PUT {TPL}", "POST /api/batch/{n}"]}, {}),
])
def test_save_ticket_rules(eight, why, kw, verify_kw):
    man = scripts(eight)
    t = ticket_of(man, "save")["ticket"]
    written = kw.pop("written", ["p1-10"])
    signal = kw.pop("signal", "fallback")
    f = fill_receipt(eight, "r/fill-p1.json", [1], written, signal=signal, calibrated=False, ticket=t, **kw)
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "ok", "p2-20": "ok"}, **verify_kw)
    assert record(eight, [f], v).returncode == 0
    st = calibration(eight)
    assert "save" not in st, why
    assert st["history"][-1]["event"] in ("save-not-calibrated", "save-inconclusive"), why


def test_auto_calibration_records_the_identified_model_transport(eight):
    man = scripts(eight)
    t = ticket_of(man, "save")["ticket"]
    f = fill_receipt(eight, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False, ticket=t,
                     ack=("model", "put", TPL), ack_endpoints=[f"PUT {TPL}"], model_unidentified=0)
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "ok"})
    r = record(eight, [f], v)
    assert r.returncode == 0, r.stdout + r.stderr
    save = calibration(eight)["save"]
    assert (save["source"], save["method"], save["path_template"], save["keys_acknowledged"]) == ("model", "PUT", TPL, 1)
    assert f"save wait calibrated: model PUT {TPL}" in r.stdout
    # The next scripts carry the endpoint into the calibrated wait of the trial batch.
    man = scripts(eight, "--pages", "2-8")
    assert man["calibrated"] is True and ticket_of(man, "cross_page")["pages"] == [2, 3]
    body = (eight.tmp / "out" / "_fill-c1-r101-b1.js").read_text(encoding="utf-8")
    assert '"source": "model"' in body and '"method": "PUT"' in body and f'"path_template": "{TPL}"' in body


def test_an_unidentified_save_keeps_the_fallback_and_says_why(eight):
    man = scripts(eight)
    t = ticket_of(man, "save")["ticket"]
    f = fill_receipt(eight, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False, ticket=t,
                     ack=(None, None, None), keys_saved=0, keys_pending=1, model_unidentified=1,
                     next="fresh-load verify of these pages before any other page")
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "ok"})
    r = record(eight, [f], v)
    st = calibration(eight)
    assert "save" not in st and st["history"][-1]["event"] == "save-not-calibrated"
    assert "through a transport LtNet could not identify" in st["history"][-1]["detail"]
    man = scripts(eight, "--pages", "2-8")
    assert man["calibrated"] is False and ticket_of(man, "save")["pages"] == [2]
    assert "NOT calibrated" in r.stdout


def test_a_model_save_record_without_an_endpoint_counts_as_absent(eight):
    set_calibration(eight, save={"source": "model", "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    man = scripts(eight, "--pages-per-call", "3")
    assert man["calibrated"] is False and fills(man) == [[str(p)] for p in range(1, 9)]
    assert ticket_of(man, "save")["pages"] == [1]
    assert "names no write method and path template" in " ".join(man["notes"])


def test_a_batch_refused_for_a_record_without_an_endpoint_drops_it(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2, 3], [], signal=None, calibrated=True, ok=False,
                     fatal="calibration-stale", calibration_stale=True, stale_reason="no-endpoint")
    v = verify_receipt(eight, "r/verify-b1.json", {})
    r = record(eight, [f], v)
    assert r.returncode == 0, r.stdout + r.stderr
    st = calibration(eight)
    assert "save" not in st and "cross_page" not in st
    assert st["history"][-1]["event"] == "save-record-unusable"


def test_a_calibrated_unconfirmed_call_records_the_unidentified_saves(eight):
    set_calibration(eight, save={"source": "model", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    scripts(eight)
    written = ["p1-10", "p2-20", "p3-30"]
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2, 3], written, signal="unconfirmed", calibrated=True,
                     keys_saved=0, keys_pending=3, model_unidentified=3)
    v = verify_receipt(eight, "r/verify-b1.json", {i: "ok" for i in written})
    r = record(eight, [f], v)
    assert r.returncode == 0, r.stdout + r.stderr
    st = calibration(eight)
    assert st["save"]["version"] == 1 and st["cross_page"]["ok"] is False
    assert "3 of 3 key(s) had no identified save" in st["cross_page"]["reason"]
    assert "transport LtNet could not identify" in st["cross_page"]["reason"]
    man = scripts(eight, "--pages", "4-8")
    assert fills(man) == [[str(p)] for p in range(4, 9)]


def test_a_ticket_is_used_once(eight):
    calibrate_save(eight)
    before = calibration(eight)["save"]
    eight.calibration_path.write_text(json.dumps({ORIGIN: {"history": []}}), encoding="utf-8", newline="\n")
    f = eight.tmp / "r/fill-p1.json"
    v = eight.tmp / "r/verify-p1.json"
    r = record(eight, [f], v)
    assert "unknown or already used" in r.stdout
    assert "save" not in calibration(eight)
    assert before["version"] == 1


def test_record_refuses_a_verify_that_is_not_a_fresh_load(eight):
    man = scripts(eight)
    t = ticket_of(man, "save")["ticket"]
    f = fill_receipt(eight, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False, ticket=t)
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "ok"}, fresh=False)
    assert record(eight, [f], v).returncode == 3
    assert "save" not in calibration(eight)


def trial(kit):
    calibrate_save(kit)
    man = scripts(kit, "--pages", "2-8")
    return ticket_of(man, "cross_page")["ticket"]


def test_cross_page_success_enables_batches_up_to_five(eight):
    t = trial(eight)
    f = fill_receipt(eight, "r/fill-b1.json", [2, 3], ["p2-20", "p3-30"], signal="net", calibrated=True, ticket=t)
    v = verify_receipt(eight, "r/verify-b1.json", {"p2-20": "ok", "p3-30": "ok"})
    r = record(eight, [f], v)
    assert r.returncode == 0 and "cross-page calibrated" in r.stdout, r.stdout
    cp = calibration(eight)["cross_page"]
    assert (cp["ok"], cp["pages"], cp["version"]) == (True, [2, 3], 1)
    man = scripts(eight, "--pages", "4-8")
    assert fills(man) == [["4", "5", "6", "7", "8"]] and man["tickets"] == []


def test_cross_page_miss_falls_back_to_one_page_per_call(eight):
    t = trial(eight)
    f = fill_receipt(eight, "r/fill-b1.json", [2, 3], ["p2-20", "p3-30"], signal="net", calibrated=True, ticket=t)
    v = verify_receipt(eight, "r/verify-b1.json", {"p2-20": "ok", "p3-30": "BAD"})
    r = record(eight, [f], v)
    assert "FAILED" in r.stdout
    st = calibration(eight)
    assert st["cross_page"]["ok"] is False and "p3-30 BAD" in st["cross_page"]["reason"]
    # The wait said saved and a fresh load disagreed: the save record goes too.
    assert "save" not in st
    man = scripts(eight, "--pages", "4-8", "--pages-per-call", "5")
    assert fills(man) == [[str(p)] for p in range(4, 9)]
    assert "one page per call" in man["stdout"] or man["calibrated"] is False


def test_cross_page_unconfirmed_keeps_save_and_stays_at_one(eight):
    t = trial(eight)
    f = fill_receipt(eight, "r/fill-b1.json", [2, 3], ["p2-20", "p3-30"], signal="unconfirmed",
                     calibrated=True, ticket=t)
    v = verify_receipt(eight, "r/verify-b1.json", {"p2-20": "ok", "p3-30": "ok"})
    record(eight, [f], v)
    st = calibration(eight)
    assert st["save"]["version"] == 1
    assert st["cross_page"]["ok"] is False and "unconfirmed" in st["cross_page"]["reason"]
    man = scripts(eight, "--pages", "4-8")
    assert man["calibrated"] is True and fills(man) == [[str(p)] for p in range(4, 9)]
    assert "cross-page calibration failed" in man["stdout"]


def test_cross_page_pending_keys_cannot_enable_larger_batches(eight):
    """A signal without a per-key acknowledgement must keep batching at one page."""
    t = trial(eight)
    f = fill_receipt(eight, "r/fill-b1.json", [2, 3], ["p2-20", "p3-30"], signal="model",
                     calibrated=True, ticket=t, keys_saved=1, keys_pending=1, model_unidentified=1)
    v = verify_receipt(eight, "r/verify-b1.json", {"p2-20": "ok", "p3-30": "ok"})
    r = record(eight, [f], v)
    assert r.returncode == 0, r.stdout + r.stderr
    st = calibration(eight)
    assert st["cross_page"]["ok"] is False
    assert "no identified save" in st["cross_page"]["reason"]
    assert fills(scripts(eight, "--pages", "4-8")) == [[str(p)] for p in range(4, 9)]


def test_a_later_calibrated_miss_resets(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    scripts(eight)
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2, 3, 4, 5], [f"p{p}-{p * 10}" for p in range(1, 6)],
                     signal="net", calibrated=True)
    v = verify_receipt(eight, "r/verify-b1.json", {f"p{p}-{p * 10}": ("BAD" if p == 4 else "ok") for p in range(1, 6)})
    r = record(eight, [f], v)
    assert "DROPPED" in r.stdout
    st = calibration(eight)
    assert "save" not in st and st["cross_page"]["ok"] is False


def test_a_later_pending_key_resets_batching(eight):
    """An incomplete acknowledgement must revoke an existing cross-page approval."""
    set_calibration(eight, save={"source": "model", "method": "PUT", "path_template": TPL,
                                 "client_sig": "c1-2-0badf00d"}, cross_page={"ok": True, "pages": [1, 2]})
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2], ["p1-10", "p2-20"], signal="model",
                     calibrated=True, keys_saved=1, keys_pending=1, model_unidentified=1)
    v = verify_receipt(eight, "r/verify-b1.json", {"p1-10": "ok", "p2-20": "ok"})
    r = record(eight, [f], v)
    assert r.returncode == 0, r.stdout + r.stderr
    assert calibration(eight)["cross_page"]["ok"] is False
    assert fills(scripts(eight, "--pages", "3-8")) == [[str(p)] for p in range(3, 9)]


def test_a_changed_client_build_drops_the_records(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    f = fill_receipt(eight, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False,
                     client="c1-2-feedface", calibration_stale=True)
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "ok"}, client="c1-2-feedface")
    record(eight, [f], v)
    st = calibration(eight)
    assert "save" not in st and "cross_page" not in st
    assert st["history"][-1]["event"] == "client-changed"


def test_explicit_calibrate_clears_a_failed_cross_page(eight):
    set_calibration(eight, cross_page={"ok": False, "reason": "an earlier miss"})
    r = eight.emit("calibrate", "--module", "module.json", "--answers", "answers.json", "--item", "p3-30",
                   "--out", "out", "--no-render")
    assert r.returncode == 0, r.stdout + r.stderr
    man = eight.read("out/scripts_calibrate.json")
    assert fills(man) == [["3"]] and man["calibrated"] is False
    t = ticket_of(man, "save")["ticket"]
    assert calibration(eight)["pending"][0]["explicit"] is True
    f = fill_receipt(eight, "r/fill-cal.json", [3], ["p3-30"], signal="fallback", calibrated=False, ticket=t)
    v = verify_receipt(eight, "r/verify-cal.json", {"p3-30": "ok"}, ticket=t)
    destination = eight.run_dir() / 'calibrate-payload.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(eight.tmp / 'out/calibrate-payload.json', destination)
    r = eight.emit("record", "--module", "module.json", "--fill", str(f), "--verify", str(v),
                   "--payload", str(destination))
    assert r.returncode == 0
    st = calibration(eight)
    assert st["save"]["source"] == "net" and "cross_page" not in st


def test_calibrate_refuses_an_item_that_cannot_be_written(eight):
    r = eight.emit("calibrate", "--module", "module.json", "--answers", "answers.json", "--item", "p9-99",
                   "--out", "out", "--no-render")
    assert r.returncode == 3


# ---- the dialog wait -----------------------------------------------------------------------------------
def dialog_receipt(kit, status=200, **kw):
    rec = {"ok": True, "accepted": True, "text_len": 25, "upload": {"panel": "15", "sha256": "a" * 64},
           "still_open": False, "save": {"signal": "fallback", "waited_ms": 6000, "capped": False},
           "ack_method": "PUT", "ack_path_template": "/api/images/{n}/replace", "ack_status": status}
    rec.update(kw)
    return kit.write("r/dialog.json", rec)


def test_record_dialog_calibrates_on_a_2xx_save(eight):
    p = dialog_receipt(eight)
    assert eight.emit("record", "--module", "module.json", "--dialog", str(p)).returncode == 0
    d = calibration(eight)["dialog"]
    assert (d["method"], d["path_template"], d["version"]) == ("PUT", "/api/images/{n}/replace", 1)


@pytest.mark.parametrize("status,kw", [(202, {}), (500, {}), (200, {"accepted": False}),
                                       (200, {"still_open": True}), (200, {"ack_method": "GET"}),
                                       (200, {"ack_method": "HEAD"}), (200, {"ack_method": "OPTIONS"}),
                                       (200, {"ack_path_template": ""})])
def test_record_dialog_refuses_weak_evidence(eight, status, kw):
    p = dialog_receipt(eight, status, **kw)
    assert eight.emit("record", "--module", "module.json", "--dialog", str(p)).returncode == 0
    assert "dialog" not in calibration(eight)


# ---- served build and serve ----------------------------------------------------------------------------
SERVED_RUNNER = r"""
const vm = require('vm');
const fs = require('fs');
const { webcrypto } = require('crypto');
const [body, lib, probe, tamper] = process.argv.slice(2).map((a, i) => i < 3 ? fs.readFileSync(a, 'utf8') : a);
const files = { 'fill_lib.js': tamper === 'lib' ? lib.replace('use strict', 'use strict ') : lib, 'probe_panels.js': probe };
const ctx = { console, TextEncoder, crypto: webcrypto, AbortController, setTimeout, clearTimeout, URL };
ctx.fetch = async url => { const n = String(url).split('/').pop(); return { ok: n in files, text: async () => files[n] }; };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext('(async () => {\n' + body + '\n})()', ctx).then(r => {
  console.log(JSON.stringify({ r, loaded: typeof ctx.LtFillLib === 'object' }));
});
"""


@pytest.mark.requires_bin("node")
@pytest.mark.parametrize("tamper,fatal,loaded", [("none", None, False), ("lib", "lib-integrity", False)])
def test_served_build_checks_the_library_hash(eight, tamper, fatal, loaded):
    r = eight.emit("scripts", "--out", "out", "--no-render", "--lib-mode", "served",
                   "--lib-url", "http://127.0.0.1:8799")
    assert r.returncode == 0, r.stderr
    body = (eight.tmp / "out" / "_fill-c1-r101-p1.js").read_text(encoding="utf-8")
    assert "lib-integrity" in body and "fill_lib.js" in body
    runner = eight.tmp / "served.js"
    runner.write_text(SERVED_RUNNER, encoding="utf-8", newline="\n")
    p = subprocess.run(["node", str(runner), str(eight.tmp / "out" / "_fill-c1-r101-p1.js"),
                        str(SCRIPTS / "browser" / "fill_lib.js"), str(SCRIPTS / "browser" / "probe_panels.js"),
                        tamper], capture_output=True, text=True, timeout=60, encoding="utf-8")
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["loaded"] is loaded, p.stdout + p.stderr
    if fatal:
        assert (out["r"]["ok"], out["r"]["fatal"], out["r"]["writes"]) == (False, fatal, 0)
    else:
        assert out["r"]["fatal"] == "not-a-run-page"   # the library ran, then refused this fake page


def serve(emit_mod, allowed):
    files = {"fill_lib.js": SCRIPTS / "browser" / "fill_lib.js",
             "probe_panels.js": SCRIPTS / "browser" / "probe_panels.js"}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), emit_mod.make_serve_handler(files, allowed, BaseHTTPRequestHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def get(url, origin=None, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"Origin": origin} if origin else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def test_serve_allow_list(emit_mod):
    srv = serve(emit_mod, {ORIGIN})
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, h = get(base + "/fill_lib.js", ORIGIN)
        assert code == 200 and h.get("Access-Control-Allow-Origin") == ORIGIN
        code, h = get(base + "/fill_lib.js", "https://other.example.invalid")
        assert code == 403 and "Access-Control-Allow-Origin" not in h
        code, h = get(base + "/fill_lib.js")
        assert code == 403
        code, _ = get(base + "/other.js", ORIGIN)
        assert code == 404
        code, _ = get(base + "/fill_lib.js", ORIGIN, method="POST")
        assert code == 405
    finally:
        srv.shutdown()


def test_serve_binds_loopback_only():
    src = (SCRIPTS / "emit_fill.py").read_text(encoding="utf-8")
    assert 'ThreadingHTTPServer(("127.0.0.1",' in src
    assert "0.0.0.0" not in src


def test_serve_without_an_origin_refuses(kit):
    kit.config("schema_version = 1\n")
    assert kit.emit("serve").returncode == 3


# ---- model answer ---------------------------------------------------------------------------------------
def test_save_model_answer_and_the_config_switch(eight):
    res = eight.write("r/ma.json", {"ok": True, "rows": [["10", 1, "text", "ok", "alpha ", "beta"],
                                                         ["20", 2, "select", "none"]]})
    r = eight.emit("save-model-answer", "--module", "module.json", "--from", str(res))
    assert r.returncode == 0, r.stderr
    doc = json.loads((eight.run_dir() / "model-answer.json").read_text(encoding="utf-8"))
    assert doc["answers"]["10"] == {"page": 1, "kind": "text", "status": "ok", "values": ["alpha beta"]}
    assert doc["source"] == "lt-model-answer"
    cfg(eight, "[model_answer]\nread = false\n")
    assert eight.emit("save-model-answer", "--module", "module.json", "--from", str(res)).returncode == 4


def test_dialog_args_carry_the_config_waits_and_the_dialog_record(eight):
    r = eight.emit("dialog-args", "--module", "module.json", "--panel", "15", "--out", "r/args.json")
    assert r.returncode == 0, r.stderr
    doc = eight.read("r/args.json")
    assert doc == {"action": "accept", "panel": "15", "max_ms": 15000, "fallback_ms": 6000, "quiet_ms": 300,
                   "calibration": None}
    set_calibration(eight, dialog={"method": "PUT", "path_template": "/api/images/{n}/replace"})
    eight.emit("dialog-args", "--module", "module.json", "--panel", "15", "--out", "r/args.json")
    assert eight.read("r/args.json")["calibration"] == {"method": "PUT", "path_template": "/api/images/{n}/replace"}



# ---- findings of the second review ------------------------------------------------------------------------
def test_a_client_build_change_between_fill_and_verify_drops_the_records(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2], ["p1-10", "p2-20"], signal="net", calibrated=True)
    v = verify_receipt(eight, "r/verify-b1.json", {"p1-10": "ok", "p2-20": "ok"}, client="c1-2-feedface")
    record(eight, [f], v)
    st = calibration(eight)
    assert "save" not in st and "cross_page" not in st
    assert st["history"][-1]["event"] == "client-changed"


def test_a_client_change_voids_pending_tickets(eight):
    man = scripts(eight)
    t = ticket_of(man, "save")["ticket"]
    stale = fill_receipt(eight, "r/fill-p2.json", [2], ["p2-20"], signal="fallback", calibrated=False,
                         client="c1-2-feedface", calibration_stale=True)
    v2 = verify_receipt(eight, "r/verify-p2.json", {"p2-20": "ok"}, client="c1-2-feedface")
    record(eight, [stale], v2)
    assert calibration(eight)["pending"] == []
    f = fill_receipt(eight, "r/fill-p1.json", [1], ["p1-10"], signal="fallback", calibrated=False, ticket=t)
    v = verify_receipt(eight, "r/verify-p1.json", {"p1-10": "ok"})
    r = record(eight, [f], v)
    assert "unknown or already used" in r.stdout
    assert "save" not in calibration(eight)


def test_an_incomplete_cross_page_trial_is_inconclusive(eight):
    t = trial(eight)
    f = fill_receipt(eight, "r/fill-b1.json", [2, 3], ["p2-20"], signal="net", calibrated=True, ticket=t,
                     extra_items=[{"item": "p3-30", "result": "no-panel", "page": 3}])
    v = verify_receipt(eight, "r/verify-b1.json", {"p2-20": "ok"})
    r = record(eight, [f], v)
    assert "inconclusive" in r.stdout
    st = calibration(eight)
    assert "cross_page" not in st and st["save"]["version"] == 1
    assert st["history"][-1]["event"] == "cross-page-inconclusive"
    man = scripts(eight, "--pages", "2-8")
    assert ticket_of(man, "cross_page")["pages"] == [2, 3]


def test_an_explicit_calibration_that_fails_drops_save_and_batching(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    r = eight.emit("calibrate", "--module", "module.json", "--answers", "answers.json", "--item", "p3-30",
                   "--out", "out", "--no-render")
    assert r.returncode == 0
    t = ticket_of(eight.read("out/scripts_calibrate.json"), "save")["ticket"]
    f = fill_receipt(eight, "r/fill-cal.json", [3], ["p3-30"], signal="fallback", calibrated=False, ticket=t)
    v = verify_receipt(eight, "r/verify-cal.json", {"p3-30": "BAD"}, ticket=t)
    destination = eight.run_dir() / 'calibrate-payload.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(eight.tmp / 'out/calibrate-payload.json', destination)
    eight.emit("record", "--module", "module.json", "--fill", str(f), "--verify", str(v),
               "--payload", str(destination))
    st = calibration(eight)
    assert "save" not in st and st["cross_page"]["ok"] is False
    assert fills(scripts(eight))[0] == ["1"]


def test_an_uncalibrated_call_that_misses_stops_batching(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    # A service worker made LtNet fall back: calibrated false, and the wait ended unconfirmed.
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2], ["p1-10", "p2-20"], signal="unconfirmed", calibrated=False)
    v = verify_receipt(eight, "r/verify-b1.json", {"p1-10": "ok", "p2-20": "ok"})
    record(eight, [f], v)
    st = calibration(eight)
    assert st["cross_page"]["ok"] is False and "unconfirmed" in st["cross_page"]["reason"]
    assert fills(scripts(eight)) == [[str(p)] for p in range(1, 9)]


def test_a_failed_write_that_a_fresh_load_misses_drops_save(eight):
    set_calibration(eight, save={"source": "net", "method": "PUT", "path_template": TPL, "client_sig": "c1-2-0badf00d"},
                    cross_page={"ok": True, "pages": [1, 2]})
    f = fill_receipt(eight, "r/fill-b1.json", [1, 2], ["p1-10"], signal="net", calibrated=True,
                     extra_items=[{"item": "p2-20", "result": "write-mismatch", "page": 2}])
    v = verify_receipt(eight, "r/verify-b1.json", {"p1-10": "ok", "p2-20": "BAD"})
    record(eight, [f], v)
    st = calibration(eight)
    assert "save" not in st and st["cross_page"]["ok"] is False


def test_a_new_save_record_needs_a_new_cross_page_proof(eight):
    eight.calibration_path.parent.mkdir(parents=True, exist_ok=True)
    eight.calibration_path.write_text(json.dumps({ORIGIN: {
        "save": {"version": 0, "source": "net", "method": "PUT", "path_template": TPL},
        "cross_page": {"version": 1, "ok": True, "pages": [1, 2]}}}), encoding="utf-8", newline="\n")
    calibrate_save(eight)
    st = calibration(eight)
    assert st["save"]["version"] == 1 and "cross_page" not in st
    man = scripts(eight, "--pages", "2-8")
    assert fills(man)[0] == ["2", "3"] and ticket_of(man, "cross_page")["pages"] == [2, 3]


def test_inline_fill_and_verify_share_cached_runner_without_payload_data(eight):
    import base64
    import gzip
    import re
    result = eight.emit('scripts', '--out', 'out', '--stub')
    assert result.returncode == 0, result.stdout + result.stderr
    calls = eight.read('out/scripts.json')['calls']
    bodies = set()
    for call in calls:
        loader = Path(call['rendered']).read_text(encoding='utf-8')
        assert len(loader.encode()) < 15000
        assert 'localStorage.getItem(KEY)' in loader
        fallback = call['runner_payload']
        bodies.add(fallback['body_sha256'])
        packed = ''.join(Path(p['payload_path']).read_text(encoding='utf-8') for p in fallback['chunks'])
        body = gzip.decompress(base64.b64decode(packed)).decode()
        assert 'LtFillLib.fillPage' in body
        assert 'LtCheck.run' in body
        assert 'probeLtPanels' in body
        assert call.get('ticket', 'T-secret') not in body
        assert not re.search(r'"html"\s*:', body)
    assert len(bodies) == 1
