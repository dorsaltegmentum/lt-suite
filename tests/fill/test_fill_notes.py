# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""fill-notes.json and the report it feeds: the schema and its template, --check-notes listing
every problem at once, run times from the payload and receipts, unapproved singles reported
without notes, one page-kinds rule for payload and report, the binding label, the canonical
report kept on a validator failure, the Check Answer plan pin, and emit_fill.py repin."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from fillkit import ORIGIN, REPO, answers, drafted, item, module, text_pages
from ltcore import validate

SCHEMAS = REPO / "shared" / "schemas"
NOTES_SCHEMA = json.loads((SCHEMAS / "fill-notes.schema.json").read_text(encoding="utf-8"))
REPORT_SCHEMA = json.loads((SCHEMAS / "fill-report.schema.json").read_text(encoding="utf-8"))
CONFIG = f'schema_version = 1\n[lt]\norigin = "{ORIGIN}"\n'


def later(stamp: str, seconds: int) -> str:
    t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")) + dt.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def run(kit, emit_mod):
    """A bound one-page fill of p1-10 and p1-11, with the receipt 60 s after the payload."""
    kit.config(CONFIG)
    mod = module(text_pages(1, 2))
    ans = answers([drafted("p1-10"), drafted("p1-11")])
    folder = kit.run_dir() / "fill-js"
    code, payload, output = kit.payload(mod, ans, out=str(folder))
    assert code == 0, output
    path = folder / "fill-payload.json"
    seen = later(payload["generated_at"], 60)
    rows = []
    for job in payload["pages"]["1"]["jobs"]:
        value = emit_mod.expected_hash(job)
        rows.append({"item_id": job["item"], "expected_hash": value, "observed_hash": value,
                     "observed_at": seen,
                     "source_payload": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                     "payload_binding": {"fill": "source_payload_sha16", "verify": "source_payload_sha16"}})
    kit.write("receipts.json", {"receipts": rows})
    kit.write("notes.json", {"approval_mode": "manual"})

    def report(*extra, notes=None):
        if notes is not None:
            kit.write("notes.json", notes)
        return kit.run("make_fill_report.py", "--module", "module.json", "--answers", "answers.json",
                       "--receipts", "receipts.json", "--notes", "notes.json", "--out", "report.json", *extra)
    return kit, payload, seen, report


# ---- the schema and the template ------------------------------------------------------------------
def test_notes_schema_copies_the_report_shapes():
    for key in ("approval_mode", "boundary_reached", "pages_filled", "never_navigated",
                "user_decisions", "selector_events", "connection_events"):
        assert NOTES_SCHEMA["properties"][key] == REPORT_SCHEMA["properties"][key], key
    note = NOTES_SCHEMA["$defs"]["item_note"]["properties"]
    item_props = REPORT_SCHEMA["properties"]["items"]["items"]["properties"]
    for key in ("stopped_reason", "method", "selector_used", "lt_conflict", "attempted", "todo"):
        assert note[key] == item_props[key], key
    assert REPORT_SCHEMA["properties"]["run_notes"] == NOTES_SCHEMA["properties"]["run_notes"] | {
        "description": REPORT_SCHEMA["properties"]["run_notes"]["description"]}


def test_every_fixture_notes_file_validates_by_its_name():
    for path in sorted((REPO / "tests" / "fixtures" / "runs").glob("*/fill-notes.json")):
        res = validate.validate_file(path)
        assert res.schema_name == "fill-notes.schema.json" and res.ok, (path, res.schema_errors)


def test_the_skill_example_validates():
    text = (REPO / "skills" / "lt-module-fill" / "references" / "fill-notes.md").read_text(encoding="utf-8")
    example = json.loads(text.split("```json\n", 1)[1].split("```", 1)[0])
    res = validate.validate_doc(example, "fill-notes")
    assert res.ok, res.schema_errors
    kinds = REPORT_SCHEMA["properties"]["connection_events"]["items"]["properties"]["kind"]["enum"]
    assert all(f"`{k}`" in text for k in kinds)


def test_payload_writes_a_template_once(kit):
    kit.config(CONFIG)
    code, _, out = kit.payload(module(text_pages(1)), answers([drafted("p1-10")]))
    assert code == 0, out
    path = kit.run_dir() / "fill-notes.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    assert template["approval_mode"] is None and "fill notes template" in out
    res = validate.validate_doc(dict(template, approval_mode="manual"), "fill-notes")
    assert res.ok, res.schema_errors
    path.write_text('{"approval_mode": "auto"}\n', encoding="utf-8", newline="\n")
    kit.payload(module(text_pages(1)), answers([drafted("p1-10")]))
    assert json.loads(path.read_text(encoding="utf-8")) == {"approval_mode": "auto"}


# ---- --check-notes ------------------------------------------------------------------------------
def test_check_notes_lists_every_problem_at_once_and_writes_nothing(run):
    kit, payload, seen, report = run
    notes = {"approval_mode": None, "notes": "moved to work/run-notes.md",
             "connection_events": [{"at": seen, "kind": "cdp-timeout"}],
             "items": {"p1-11": {"result": "filled"}}}
    r = report("--check-notes", notes=notes)
    assert r.returncode == 3
    for want in ("unknown key(s) ['notes']", "run_notes", "approval_mode must record",
                 "[connection_events/0/kind] 'cdp-timeout' is not one of",
                 "'stopped_reason' is a dependency of 'result'", "REFUSED: "):
        assert want in r.stderr, want
    assert not (kit.tmp / "report.json").exists()


def test_check_notes_passes_without_writing(run):
    kit, payload, seen, report = run
    r = report("--check-notes")
    assert r.returncode == 0, r.stderr
    assert "notes OK" in r.stdout and f"started_at {payload['generated_at']}" in r.stdout
    assert not (kit.tmp / "report.json").exists()


# ---- run times ----------------------------------------------------------------------------------
def test_run_times_come_from_the_payload_and_the_receipts(run):
    kit, payload, seen, report = run
    r = report()
    assert r.returncode == 0, r.stdout + r.stderr
    doc = kit.read("report.json")
    assert (doc["started_at"], doc["ended_at"]) == (payload["generated_at"], seen)
    assert "run_notes" not in doc


def test_a_notes_time_that_differs_needs_a_reason(run):
    kit, payload, seen, report = run
    early = later(payload["generated_at"], -600)
    r = report(notes={"approval_mode": "manual", "started_at": early})
    assert r.returncode == 3 and "add times_reason" in r.stderr
    r = report(notes={"approval_mode": "manual", "started_at": early,
                      "times_reason": "Navigation began ten minutes before the payload.",
                      "run_notes": ["Page 1 needed a second guard run."]})
    assert r.returncode == 0, r.stdout + r.stderr
    doc = kit.read("report.json")
    assert doc["started_at"] == early
    assert doc["run_notes"][0] == "Page 1 needed a second guard run."
    assert "Navigation began ten minutes before the payload." in doc["run_notes"][1]


def test_a_late_start_names_the_time_to_set(run):
    kit, payload, seen, report = run
    r = report(notes={"approval_mode": "manual", "started_at": later(seen, 60),
                      "times_reason": "typed by hand"})
    assert r.returncode == 3
    assert f"Set started_at at or before {payload['generated_at']}" in r.stderr


def test_a_receipt_before_its_payload_is_refused(run):
    # started_at comes from the bound payload, so a receipt from before it leaves the window;
    # receipts give the start only when no receipt binds a payload.
    kit, payload, seen, report = run
    rows = kit.read("receipts.json")
    early = later(payload["generated_at"], -60)
    for row in rows["receipts"]:
        row["observed_at"] = early
    kit.write("receipts.json", rows)
    r = report()
    assert r.returncode == 3
    assert f"receipt observed_at {early} precedes started_at" in r.stderr
    assert f"Set started_at at or before {payload['generated_at']}" in r.stderr
    assert f"precedes its payload's generated_at {payload['generated_at']}" in r.stderr
    assert not (kit.tmp / "report.json").exists()


def test_a_notes_start_before_the_payload_still_checks_each_receipt(run):
    kit, payload, seen, report = run
    rows = kit.read("receipts.json")
    early = later(payload["generated_at"], -60)
    rows["receipts"][0]["observed_at"] = early
    kit.write("receipts.json", rows)
    r = report(notes={"approval_mode": "manual", "started_at": later(payload["generated_at"], -600),
                      "times_reason": "Navigation began ten minutes before the payload."})
    assert r.returncode == 3
    assert "precedes started_at" not in r.stderr
    assert f"p1-10: receipt observed_at {early} precedes its payload's generated_at" in r.stderr


def unbound_run(kit, emit_mod, *, keep_payload):
    """p1-10 with one legacy receipt (no source_payload) 30 s after the payload was built."""
    kit.config(CONFIG)
    code, payload, out = kit.payload(module(text_pages(1)), answers([drafted("p1-10")]),
                                     out=str(kit.run_dir() / "fill-js"))
    assert code == 0, out
    if not keep_payload:
        (kit.run_dir() / "fill-js" / "fill-payload.json").unlink()
    value = emit_mod.expected_hash(payload["pages"]["1"]["jobs"][0])
    first = later(payload["generated_at"], 30)
    kit.write("receipts.json", [{"item_id": "p1-10", "expected_hash": value, "observed_hash": value,
                                 "observed_at": first}])
    kit.write("notes.json", {"approval_mode": "manual"})
    r = kit.run("make_fill_report.py", "--module", "module.json", "--answers", "answers.json",
                "--receipts", "receipts.json", "--notes", "notes.json", "--out", "report.json")
    assert r.returncode == 0, r.stdout + r.stderr
    return payload, first, kit.read("report.json")


def test_without_a_bound_payload_the_run_folder_payload_starts_the_run(kit, emit_mod):
    # Unbound receipts alone set the start only when the run folder holds no payload.
    payload, first, doc = unbound_run(kit, emit_mod, keep_payload=True)
    assert (doc["started_at"], doc["ended_at"]) == (payload["generated_at"], first)


def test_without_any_payload_the_first_receipt_starts_the_run(kit, emit_mod):
    payload, first, doc = unbound_run(kit, emit_mod, keep_payload=False)
    assert (doc["started_at"], doc["ended_at"]) == (first, first)


# An unbound receipt over a changed draft was reported filled and verified.
def test_an_unbound_receipt_is_filled_unverified_even_over_a_changed_draft(kit, emit_mod):
    payload, first, doc = unbound_run(kit, emit_mod, keep_payload=True)
    item = doc["items"][0]
    assert (item["result"], item["verified"], item["receipt"]["binding"]) == ("filled-unverified", False, "unbound-legacy")
    assert "unbound-legacy" in item["stopped_reason"] and "Verified" not in item["stopped_reason"]
    ans = kit.read("answers.json")
    ans["items"][0]["segments"][0]["text"] = "Different text."
    kit.write("answers.json", ans)
    r = kit.run("make_fill_report.py", "--module", "module.json", "--answers", "answers.json",
                "--receipts", "receipts.json", "--notes", "notes.json", "--out", "report.json")
    assert r.returncode == 0, r.stdout + r.stderr
    item = kit.read("report.json")["items"][0]
    assert (item["result"], item["verified"]) == ("filled-unverified", False)
    rows = kit.read("receipts.json")
    rows[0]["observed_hash"] = 1
    kit.write("receipts.json", rows)
    r = kit.run("make_fill_report.py", "--module", "module.json", "--answers", "answers.json",
                "--receipts", "receipts.json", "--notes", "notes.json", "--out", "report.json")
    assert r.returncode == 0, r.stdout + r.stderr
    item = kit.read("report.json")["items"][0]
    assert (item["result"], item["verified"], item["receipt"]["binding"]) == ("failed", False, "unbound-legacy")


# ---- items, binding and page kinds --------------------------------------------------------------
def test_an_unapproved_single_attempt_draft_needs_no_note(kit, emit_mod):
    kit.config(CONFIG)
    mod = module([(1, [item("p1-10", "10", "text-response", "text-question"),
                       item("p1-11", "11", "text-response", "text-question", attempts="single")])])
    folder = kit.run_dir() / "fill-js"
    code, payload, out = kit.payload(mod, answers([drafted("p1-10"), drafted("p1-11")]), out=str(folder))
    assert code == 0, out
    job = payload["pages"]["1"]["jobs"][0]
    value = emit_mod.expected_hash(job)
    kit.write("receipts.json", [{"item_id": "p1-10", "expected_hash": value, "observed_hash": value,
                                 "observed_at": later(payload["generated_at"], 30)}])
    kit.write("notes.json", {"approval_mode": "manual"})
    r = kit.run("make_fill_report.py", "--module", "module.json", "--answers", "answers.json",
                "--receipts", "receipts.json", "--notes", "notes.json", "--out", "report.json")
    assert r.returncode == 0, r.stdout + r.stderr
    row = next(i for i in kit.read("report.json")["items"] if i["item_id"] == "p1-11")
    assert (row["result"], row["attempted"], row["method"]) == ("skipped", False, "not-attempted")
    assert "yours to check and submit" in row["stopped_reason"]


def test_the_report_names_each_binding_step(run):
    kit, payload, seen, report = run
    assert report().returncode == 0
    reason = kit.read("report.json")["items"][0]["stopped_reason"]
    assert ("the fill receipt by source_payload_sha16, the fresh-load receipt by "
            "source_payload_sha16; the report checked the local payload file by its full sha256") in reason
    assert "payload bound by" not in reason


def test_payload_and_report_refuse_the_same_stale_page_kinds(run):
    kit, payload, seen, report = run
    stale = kit.run_dir() / "page-kinds.json"
    stale.write_text(json.dumps({"scan": {"answers_sha256": "0" * 64}}), encoding="utf-8", newline="\n")
    r = report()
    assert r.returncode == 3 and "page-kinds.json is stale for this answers.json" in r.stderr
    r = kit.emit("payload", "--module", "module.json", "--answers", "answers.json", "--force")
    assert r.returncode == 3 and "page-kinds.json is stale for this answers.json" in r.stderr


def test_the_report_names_a_payload_path_outside_the_run(run):
    kit, payload, seen, report = run
    rows = kit.read("receipts.json")
    outside = kit.write("elsewhere/fill-payload.json", payload)
    for row in rows["receipts"]:
        row["source_payload"] = {"path": str(outside),
                                 "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}
    kit.write("receipts.json", rows)
    r = report()
    assert r.returncode == 3
    assert f"receipt binding: payload path {outside} (outside the run folder" in r.stderr


def test_a_blocked_full_hash_of_any_kind_binds_by_the_short_prefix(run, emit_mod):
    kit, payload, seen, report = run
    path = kit.run_dir() / "fill-js" / "fill-payload.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {"source_payload_sha256": "[BLOCKED: Sensitive key]", "source_payload_sha16": digest[:16]}
    assert emit_mod.bind_payload(receipt, [path]) == (path, digest, "source_payload_sha16")


# ---- the canonical report and the Check Answer plan ---------------------------------------------
def test_a_rejected_report_leaves_the_canonical_one_in_place(run):
    kit, payload, seen, report = run
    assert report().returncode == 0
    before = (kit.tmp / "report.json").read_bytes()
    r = report(notes={"approval_mode": "manual", "user_decisions": [
        {"rail": "approval-manual-at-boundary", "who": "the user", "when": "2000-01-01",
         "why": "A waiver that waives nothing in this run.", "approval_id": "A-0123abcd"}]})
    assert r.returncode == 1
    assert (kit.tmp / "report.json").read_bytes() == before
    assert kit.read("report.rejected.json")["user_decisions"]


def test_the_report_pins_the_check_plan_and_a_report_without_it_is_stale(run):
    kit, payload, seen, report = run
    plan = kit.run_dir() / "fill-js" / "check-plan.json"
    out = kit.run_dir() / "fill-report.json"
    r = report("--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "source_check_plan" not in doc
    plan.write_text(json.dumps({"refused": [], "rat_pages": [], "policy": {}}), encoding="utf-8", newline="\n")
    res = validate.validate_file(out, module=kit.tmp / "module.json")
    assert not res.ok and any("check-plan-stale" in e and "predates" in e for e in res.rail_errors)
    r = report("--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["source_check_plan"]["sha256"] == hashlib.sha256(plan.read_bytes()).hexdigest()
    assert validate.validate_file(out, module=kit.tmp / "module.json").ok
    plan.write_text(json.dumps({"refused": [], "rat_pages": [], "policy": {"x": 1}}), encoding="utf-8", newline="\n")
    res = validate.validate_file(out, module=kit.tmp / "module.json")
    assert any("check-plan-stale" in e and "differs" in e for e in res.rail_errors)


# ---- repin --------------------------------------------------------------------------------------
def repin(kit):
    return kit.emit("repin", "--module", "module.json", "--answers", "answers.json")


def test_repin_moves_the_pin_when_no_written_draft_changed(run):
    kit, payload, seen, report = run
    ans = kit.read("answers.json")
    for a in ans["items"]:
        a["provenance"][0]["locator"] = "slide 1, lower half"
    kit.write("answers.json", ans)
    r = report()
    assert r.returncode == 3 and "emit_fill.py repin" in r.stderr
    before = (kit.tmp / "answers.json").read_bytes()
    r = repin(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (kit.tmp / "answers.json").read_bytes() == before
    rec = json.loads((kit.run_dir() / "fill-js" / "answers-repin.json").read_text(encoding="utf-8"))["repins"][0]
    assert (rec["from_answers_sha256"], rec["to_answers"]["sha256"], rec["jobs_checked"]) == (
        payload["answers"]["sha256"], hashlib.sha256(before).hexdigest(), 2)
    r = report()
    assert r.returncode == 0, r.stdout + r.stderr
    assert any("re-pinned" in n for n in kit.read("report.json")["run_notes"])
    assert repin(kit).returncode == 0


def test_repin_refuses_a_changed_written_draft(run):
    kit, payload, seen, report = run
    ans = kit.read("answers.json")
    ans["items"][1]["segments"][0]["text"] = "A different answer."
    kit.write("answers.json", ans)
    r = repin(kit)
    assert r.returncode == 3 and "p1-11" in r.stderr and "p1-10" not in r.stderr
    assert not (kit.run_dir() / "fill-js" / "answers-repin.json").exists()
    assert report().returncode == 3


def hand_repin(kit, payload):
    """An answers-repin.json written by hand: it matches every sha the report compares."""
    path = kit.run_dir() / "fill-js" / "fill-payload.json"
    rec = {"payload": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
           "from_answers_sha256": payload["answers"]["sha256"],
           "to_answers": {"path": str(kit.tmp / "answers.json"),
                          "sha256": hashlib.sha256((kit.tmp / "answers.json").read_bytes()).hexdigest()},
           "jobs_checked": 2, "recorded_at": "2000-01-01T00:00:00Z"}
    (path.parent / "answers-repin.json").write_text(json.dumps({"repins": [rec]}), encoding="utf-8", newline="\n")


def test_a_hand_written_repin_over_an_edited_draft_is_refused(run):
    kit, payload, seen, report = run
    ans = kit.read("answers.json")
    ans["items"][1]["segments"][0]["text"] = "A different answer."
    kit.write("answers.json", ans)
    hand_repin(kit, payload)
    r = report()
    assert r.returncode == 3
    assert "answers-repin.json" in r.stderr and "does not hold" in r.stderr
    assert "no longer rebuild from answers.json to the payload's job content: p1-11." in r.stderr
    assert not (kit.tmp / "report.json").exists()


def test_a_hand_written_repin_holds_only_when_every_job_rebuilds(run):
    # The record proves nothing by itself; the report's own rebuild decides.
    kit, payload, seen, report = run
    ans = kit.read("answers.json")
    for a in ans["items"]:
        a["provenance"][0]["locator"] = "slide 1, lower half"
    kit.write("answers.json", ans)
    hand_repin(kit, payload)
    r = report()
    assert r.returncode == 0, r.stdout + r.stderr
    assert any("re-pinned" in n for n in kit.read("report.json")["run_notes"])


# repin and the report compared 32-bit receipt hashes, which collide and drop table cell positions.
def one_job_run(kit, emit_mod, mod, ans):
    """A payload for one job, a bound receipt 60 s later, and the report's other inputs."""
    kit.config(CONFIG)
    code, payload, out = kit.payload(mod, ans, out=str(kit.run_dir() / "fill-js"))
    assert code == 0, out
    job = payload["pages"]["1"]["jobs"][0]
    path = kit.run_dir() / "fill-js" / "fill-payload.json"
    value = emit_mod.expected_hash(job)
    kit.write("receipts.json", [{"item_id": job["item"], "expected_hash": value, "observed_hash": value,
                                 "observed_at": later(payload["generated_at"], 60),
                                 "source_payload": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}])
    kit.write("notes.json", {"approval_mode": "manual"})
    return payload, job


def report_refuses_hand_repin(kit, payload, iid):
    hand_repin(kit, payload)
    r = kit.run("make_fill_report.py", "--module", "module.json", "--answers", "answers.json",
                "--receipts", "receipts.json", "--notes", "notes.json", "--out", "report.json")
    assert r.returncode == 3 and "does not hold" in r.stderr and iid in r.stderr, r.stdout + r.stderr
    assert not (kit.tmp / "report.json").exists()


def test_repin_and_the_report_refuse_a_draft_with_a_colliding_hash(kit, emit_mod):
    assert emit_mod.djb2("Marker Hb") == emit_mod.djb2("Marker IA")
    seg = {"kind": "claim", "text": "Marker Hb", "provenance_refs": ["p1"]}
    payload, job = one_job_run(kit, emit_mod, module(text_pages(1)), answers([drafted("p1-10", segments=[seg])]))
    twin = dict(job, html="<p>Marker IA</p>")
    assert emit_mod.expected_hash(twin) == emit_mod.expected_hash(job)
    assert emit_mod.job_digest("1", twin) != emit_mod.job_digest("1", job)
    ans = kit.read("answers.json")
    ans["items"][0]["segments"][0]["text"] = "Marker IA"
    kit.write("answers.json", ans)
    r = repin(kit)
    assert r.returncode == 3 and "p1-10" in r.stderr and "job content" in r.stderr
    assert not (kit.run_dir() / "fill-js" / "answers-repin.json").exists()
    report_refuses_hand_repin(kit, payload, "p1-10")


def test_repin_and_the_report_refuse_a_table_cell_moved_to_another_row(kit, emit_mod):
    cell = {"row": 0, "col": 0, "text": "Left", "provenance_refs": ["p1"]}
    mod = module([(1, [item("p1-10", "10", "table-completion", "table")])])
    payload, job = one_job_run(kit, emit_mod, mod, answers([drafted("p1-10", table_cells=[cell], segments=[])]))
    moved = dict(job, cells=[dict(job["cells"][0], row=1)])
    assert emit_mod.expected_hash(moved) == emit_mod.expected_hash(job)
    assert emit_mod.job_digest("1", moved) != emit_mod.job_digest("1", job)
    two = dict(job, cells=[dict(job["cells"][0], col=1), job["cells"][0]])
    assert emit_mod.job_digest("1", two) == emit_mod.job_digest("1", dict(two, cells=two["cells"][::-1])), \
        "cell order alone changes nothing written"
    ans = kit.read("answers.json")
    ans["items"][0]["table_cells"] = [dict(cell, row=1)]
    kit.write("answers.json", ans)
    r = repin(kit)
    assert r.returncode == 3 and "p1-10" in r.stderr
    assert not (kit.run_dir() / "fill-js" / "answers-repin.json").exists()
    report_refuses_hand_repin(kit, payload, "p1-10")


def test_repin_reports_nothing_to_do_and_checks_the_question_digest(run):
    kit, payload, seen, report = run
    assert repin(kit).returncode == 2
    mod = kit.read("module.json")
    mod["pages"][0]["items"][0]["prompt_text"] = "A changed question"
    kit.write("module.json", mod)
    r = repin(kit)
    assert r.returncode == 3 and "question digest" in r.stderr
