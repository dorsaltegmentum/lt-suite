#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write fill-report.json from what a fill run measured, then validate it.

    make_fill_report.py --module <module.json> --answers <answers.json>
        [--receipts R] [--notes N] [--save-waits W] [--approvals A] [--out F]
        [--config C] [--data-dir D] [--check-notes]

Defaults, all inside <data>/runs/<module_id>/: receipts.json and save-waits.json (written by
`emit_fill.py record`), fill-notes.json, approvals.json, and fill-report.json for --out.

Exit: 0 written and the validator passes it, 1 the validator rejects it or cannot be found
("NOT VALIDATED"), 3 input refused. Every refusal lists all its problems at once. The report
is written to a temporary file beside --out and replaces --out only after the validator passes
it; a rejected report goes to <out stem>.rejected.json and --out stays as it was.

--check-notes runs every check against fill-notes.json, receipts.json and the plan, prints each
problem, and writes nothing: exit 0 when the report would be built, 3 otherwise.

`result` and `verified` for a written item come from one source: the fresh-load hash
receipts. No input can set `verified`.

Receipts: a JSON list, or {"receipts": [...]}, with these required keys:

    {"item_id": "p4-12", "expected_hash": 2066687429, "observed_hash": 2066687429,
     "observed_at": "2000-01-01T00:00:00Z"}

A receipt with source_payload {path, sha256} (every receipt `record` writes) binds the executed
payload, and the payload pins answers.json by sha256: answers.json must stay byte-identical
from payload to report on every page, not only RAT pages. When answers.json changed but no
written draft did (a builder rerun, a trimmed locator), `emit_fill.py repin` checks each job
against the current answers.json and records the new pin in answers-repin.json beside the
payload; it never writes answers.json. Any agent can write that file, so the report uses a
record only after it rebuilds every job of the payload from the current answers.json to the
same job content (emit_fill.job_digest: page, panel, text, each table cell's row and column,
selections and placements, by sha256; never the 32-bit expected_hash), and refuses on any
difference. RAT sheet receipts require the binding. Keep the executed payload and its pinned page-kinds.json unchanged until reporting.
Missing or changed bindings refuse reporting; reclassification cannot replace provenance. The
report names the field each browser receipt bound by (payload_binding, normally
source_payload_sha16) and the full sha256 the report checked the local payload file by.

Each receipt comes from a fresh load of the item's page. expected_hash hashes the drafted answer;
observed_hash the hash of what the load read back; for a table both cover every target cell.
Hashes are strings or integers and compare as strings. An item's latest receipt decides it:
equal hashes give result filled and verified true, unequal give failed and verified false.
A receipt without source_payload (one recorded before payload binding, "unbound-legacy")
cannot tie its hash to an executed payload or to today's draft: equal hashes give
filled-unverified, unequal give failed, verified false either way, and the report item's
receipt carries binding "unbound-legacy". Fill and verify again for a verified result.
Earlier receipts go into stopped_reason, so a refill that a later load verified reads as one.
A receipt is refused when its item is not in answers.json, when it falls outside started_at
to ended_at, or when it ties another for its item's latest time.

started_at defaults to the earliest bound payload's generated_at. When no receipt binds a
payload it is the earliest generated_at of the payloads in the run folder, and the first
receipt's observed_at only when the run folder holds no payload. ended_at defaults to the last
receipt's observed_at. page-kinds.json follows the
payload's rule (emit_fill.stale_page_kinds): classified against other answers.json bytes, it
refuses.

Save waits: `emit_fill.py record` keeps each call's save_signal. Every page whose wait ended
on the fallback rule (an origin without a calibrated save signal) or unconfirmed is listed
on stdout, and the items written there say so in stopped_reason: for them the fresh-load
receipt is the only proof of the save.

Notes (fill-notes.json, shared/schemas/fill-notes.schema.json; `emit_fill.py payload` writes a
template) carry what only the operator saw:

    started_at, ended_at     optional ISO 8601 with an offset; a value that differs from the
                             payload and receipt times needs times_reason
    times_reason             why; copied into the report's run_notes
    approval_mode            how the browser tool was supervised (required):
                               manual    a human approved each page-changing action
                               auto      the tool acted without asking per action
                               skip-all  the tool's approvals were switched off
                             Pages holding a commit boundary require manual.
    boundary_reached         optional; the default is plan_fill.py's first stop door
    pages_filled             optional; the default is the pages of the items written
    never_navigated          optional extra routes, after plan_fill.py's own
    items                    {item_id: note}, below
    user_decisions           copied as given; the validator checks each approval_id
    selector_events, connection_events    copied as given; connection_events kinds:
                             worker-idle, modal-block, navigation-timeout,
                             screenshot-timeout, content-filter-block
    run_notes                free-text strings, copied into the report

A note may carry stopped_reason, method, selector_used and lt_conflict. A note on an item with
no receipt may also carry result (failed, filled-unverified, skipped or todo), attempted and
todo, and needs a stopped_reason when it sets result. No note carries `verified`, no note says
result filled, and no note sets the result of an item that has a receipt.

A drafted item that plan_fill.py plans as fillable, with no receipt and no note, is refused:
either it was written and its receipt is missing, or it was not written and the reason is
missing. A drafted item the plan holds back is reported skipped with the plan's reason, or
todo with the plan's placement for an unmapped type. A single-attempt draft with no
single_attempt_approval is held by the plan and reported skipped, "yours to check and
submit", with no note needed. A blocked draft is a todo with its own placement. A skipped
draft stays skipped with its skip_reason.

When fill-js/check-plan.json exists, the report carries its check_answer fields and pins it
as source_check_plan; the validator rejects a report in the run folder that predates it.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402
ltcore.bootstrap(["jsonschema"])
from ltcore import config, store  # noqa: E402

from ltcore import page_kinds, rat

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan_fill  # noqa: E402

RECEIPT_KEYS = {"item_id", "expected_hash", "observed_hash", "observed_at"}
NOTE_KEYS = {"stopped_reason", "method", "selector_used", "lt_conflict"}
UNRECEIPTED_KEYS = NOTE_KEYS | {"result", "attempted", "todo"}
NOTE_RESULTS = {"failed", "filled-unverified", "skipped", "todo"}
WRITTEN = {"filled", "filled-unverified", "failed"}
WRITE_METHODS = {"url", "selector", "view-model"}
APPROVAL_MODES = {"manual", "auto", "skip-all"}
TOP_KEYS = {"started_at", "ended_at", "times_reason", "approval_mode", "boundary_reached",
            "pages_filled", "never_navigated", "items", "user_decisions", "selector_events",
            "connection_events", "run_notes"}
# Written through a file input. Every other type is written through its view model.
SELECTOR_TYPES = {"upload-and-annotate", "image-annotation"}
# A receipt without source_payload: recorded before payload binding, so its hash is tied to
# no executed payload and not to today's draft. The report item's receipt says so.
UNBOUND = "unbound-legacy"
UNBOUND_REASON = ("Not verified: this receipt carries no source_payload binding (unbound-legacy), "
                  "so its hash is tied to no executed payload and not to today's draft. Fill and "
                  "verify the item again for a verified result.")
# Save waits that prove nothing by themselves; the fresh-load receipt is the proof.
WEAK_SIGNALS = {"fallback": "Saved under the fallback wait (no calibrated save signal for this origin); "
                            "the fresh-load receipt is the proof of the save.",
                "unconfirmed": "The save wait ended unconfirmed; the fresh-load receipt decides.",
                "error": "The save wait reported a server error on this call; the fresh-load receipt decides."}


class Refused(Exception):
    """Input refused; `problems` lists every reason found, one string each."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


def aware(v, what):
    if not isinstance(v, str):
        raise Refused(f"{what}: {v!r} is not an ISO 8601 timestamp")
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise Refused(f"{what}: {v!r} is not an ISO 8601 timestamp") from None
    if t.tzinfo is None:
        raise Refused(f"{what}: {v!r} has no offset")
    return t


def is_hash(h):
    return (isinstance(h, str) and h != "") or (isinstance(h, int) and not isinstance(h, bool))


def matches(r):
    return str(r["expected_hash"]) == str(r["observed_hash"])


def keys_named(node, name, trail=""):
    """Every path in `node` whose key is `name`."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{trail}.{k}" if trail else k
            if k == name:
                out.append(here)
            out += keys_named(v, name, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += keys_named(v, name, f"{trail}[{i}]")
    return out


def load_receipts(raw, answer_ids):
    """{item_id: [receipt, ...]} oldest first, each receipt checked. The run window is checked
    later, by window_problems(), once the start and end are known."""
    rows = raw.get("receipts") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise Refused("receipts: expected a list, or an object with a 'receipts' list")
    by_item: dict[str, list] = {}
    for n, r in enumerate(rows):
        tag = f"receipts[{n}]"
        if not isinstance(r, dict) or not RECEIPT_KEYS <= set(r) or set(r) - RECEIPT_KEYS - {"source_payload", "payload_binding", "verify_receipt_sha256"}:
            got = sorted(r) if isinstance(r, dict) else type(r).__name__
            raise Refused(f"{tag}: keys must be {sorted(RECEIPT_KEYS)} with optional source_payload, got {got}")
        if r["item_id"] not in answer_ids:
            raise Refused(f"{tag}: {r['item_id']!r} is not an item in answers.json")
        for k in ("expected_hash", "observed_hash"):
            if not is_hash(r[k]):
                raise Refused(f"{tag}: {k} {r[k]!r} is not a hash")
        t = aware(r["observed_at"], f"{tag}.observed_at")
        by_item.setdefault(r["item_id"], []).append((t, r))
    out = {}
    for iid, rs in by_item.items():
        rs.sort(key=lambda x: x[0])
        if len(rs) > 1 and rs[-1][0] == rs[-2][0]:
            raise Refused(f"{iid}: two receipts share its latest observed_at "
                          f"{rs[-1][1]['observed_at']}; one fresh load gives one receipt")
        out[iid] = [r for _, r in rs]
    return out


def window_problems(receipts, start, end, derived_start, derived_end) -> list[str]:
    """One problem per receipt outside started_at to ended_at, each naming the value to set."""
    out = []
    for rs in receipts.values():
        for r in rs:
            t = aware(r["observed_at"], "observed_at")
            if t < start:
                out.append(f"{r['item_id']}: receipt observed_at {r['observed_at']} precedes "
                           f"started_at; a load taken before the run cannot verify its write. Set "
                           f"started_at at or before {derived_start[1]}, or drop it from "
                           f"fill-notes.json to use that time")
            if end is not None and t > end:
                out.append(f"{r['item_id']}: receipt observed_at {r['observed_at']} is after "
                           f"ended_at; a run ends after its last check. Set ended_at at or after "
                           f"{derived_end[1] if derived_end else r['observed_at']}, or drop it")
    return out


def notes_schema() -> dict | None:
    """fill-notes.schema.json from the shared copy beside the validator, or None."""
    try:
        from ltcore import validate  # noqa: PLC0415
        sd = Path(validate.__file__).resolve().parent.parent / "schemas" / "fill-notes.schema.json"
        return json.loads(sd.read_text(encoding="utf-8"))
    except Exception:
        return None


def notes_problems(notes) -> list[str]:
    """Every shape problem in fill-notes.json: its schema, then the rules a schema cannot say."""
    if not isinstance(notes, dict):
        return ["notes: fill-notes.json must hold a JSON object"]
    out = []
    unknown = sorted(set(notes) - TOP_KEYS)
    if unknown:
        out.append(f"notes: unknown key(s) {unknown}; allowed {sorted(TOP_KEYS)}. Free text "
                   f"belongs in run_notes, a list of strings")
    hits = keys_named(notes, "verified")
    if hits:
        out.append(f"notes: 'verified' at {', '.join(hits)}; verified comes from receipts only")
    if notes.get("approval_mode") not in APPROVAL_MODES:
        out.append("notes: approval_mode must record how the browser tool was supervised: "
                   "manual, auto or skip-all")
    schema = notes_schema()
    if schema is not None:
        from jsonschema import Draft202012Validator  # noqa: PLC0415
        for e in sorted(Draft202012Validator(schema).iter_errors(notes),
                        key=lambda e: [str(x) for x in e.absolute_path]):
            path = [str(x) for x in e.absolute_path]
            # The three rules above say these in the operator's terms.
            if (path[:1] == ["approval_mode"] or e.validator == "additionalProperties" and not path
                    or e.validator == "required" and "approval_mode" in e.message
                    or e.validator == "additionalProperties" and "'verified'" in e.message):
                continue
            out.append(f"notes: [{'/'.join(path) or '<root>'}] {e.message}")
    for k in ("started_at", "ended_at"):
        v = notes.get(k)
        if v is not None:
            try:
                aware(v, f"notes.{k}")
            except Refused as e:
                out += e.problems
    return out


def payload_times(receipts, base, st, mid, cfg) -> list[tuple]:
    """(datetime, text) of generated_at for each payload a receipt binds, confined like the
    binding itself: inside the run folder and outside every RAT answers folder."""
    import emit_fill  # noqa: PLC0415
    roots = [root for _, root in rat.effective_roots(rat.policy_from_config(cfg))]
    out, seen = [], set()
    for rs in receipts.values():
        for r in rs:
            b = r.get("source_payload")
            if not isinstance(b, dict) or not isinstance(b.get("path"), str):
                continue
            path = Path(b["path"])
            path = path if path.is_absolute() else base / path
            if path in seen or any(rat.within(path, root) for root in roots):
                continue
            seen.add(path)
            try:
                if path.resolve() not in emit_fill.payload_candidates(st, mid, [path]):
                    continue
                text = json.loads(path.read_text(encoding="utf-8")).get("generated_at")
                out.append((aware(text, "generated_at"), text))
            except (OSError, ValueError, AttributeError, Refused):
                continue
    return out


def run_payload_times(st, mid, cfg) -> list[tuple]:
    """(datetime, text) of generated_at for each payload file in the run folder
    (emit_fill.payload_candidates), outside every RAT answers folder."""
    import emit_fill  # noqa: PLC0415
    roots = [root for _, root in rat.effective_roots(rat.policy_from_config(cfg))]
    out = []
    for path in emit_fill.payload_candidates(st, mid, []):
        if any(rat.within(path, root) for root in roots):
            continue
        try:
            text = json.loads(path.read_text(encoding="utf-8")).get("generated_at")
            out.append((aware(text, "generated_at"), text))
        except (OSError, ValueError, AttributeError, Refused):
            continue
    return out


def binding_label(receipt, local_field) -> str:
    """Which hash bound the payload at each step: the browser receipts' own field, as record
    kept it in payload_binding, then the local file check made here."""
    local = ("its full sha256" if local_field == "source_payload_sha256"
             else "source_payload_sha16, the 16-hex prefix of its sha256")
    pb = receipt.get("payload_binding") or {}
    if pb.get("fill") or pb.get("verify"):
        return (f"Payload binding: the fill receipt by {pb.get('fill') or 'none'}, the fresh-load "
                f"receipt by {pb.get('verify') or 'none'}; the report checked the local payload "
                f"file by {local}.")
    return f"Payload binding: the report checked the local payload file by {local}."


def receipt_reason(rs):
    last = rs[-1]
    if matches(last) and not last.get("source_payload"):
        text = (f"A fresh load at {last['observed_at']} read hash {last['observed_hash']}, equal "
                f"to the receipt's own {last['expected_hash']}.")
    elif matches(last):
        text = (f"Verified on a fresh load at {last['observed_at']}: hash "
                f"{last['observed_hash']} equals the drafted {last['expected_hash']}.")
    else:
        text = (f"A fresh load at {last['observed_at']} read hash {last['observed_hash']}, "
                f"expected {last['expected_hash']}: the write did not reach the server.")
    if len(rs) > 1:
        seen = ", ".join(f"{r['observed_at']} {'matched' if matches(r) else 'did not match'}"
                         for r in rs[:-1])
        text = f"Earlier fresh loads: {seen}. {text}"
    return text


def joined(*parts):
    return " ".join(p.strip() for p in parts if p and p.strip()) or None


def plan_index(pl):
    """(fillable ids, {item_id: plan entry} for each held-back item, {page_index: skip})."""
    fillable, held = set(), {}
    for pg in pl["pages"]:
        for e in pg["items"]:
            if e["fillable"]:
                fillable.add(e["item_id"])
            else:
                held[e["item_id"]] = e
    return fillable, held, {s["page_index"]: s for s in pl["skipped_pages"]}


def default_boundary(pl, mod):
    first = pl.get("first_boundary")
    if first:
        return {"stopped": True, "class": first["class"], "page_index": first["page_index"],
                "item_id": first["item_id"],
                "detail": f"plan_fill.py's first one-way door: {first['detail']}."}
    detail = "The capture has no Checkpoint page before the end of the module."
    if (mod.get("lesson") or {}).get("has_completion_page"):
        detail += " The Completion page carries the run-level commit and was never navigated."
    return {"stopped": False, "class": None, "page_index": None, "item_id": None,
            "detail": detail}


def weak_pages(save_waits) -> dict[int, str]:
    """{page: signal} for every page whose save wait proves nothing by itself."""
    rows = save_waits.get("save_waits") if isinstance(save_waits, dict) else (save_waits or [])
    out: dict[int, str] = {}
    for w in rows or []:
        sig = w.get("save_signal")
        if sig not in WEAK_SIGNALS:
            continue
        for p in w.get("pages") or []:
            if isinstance(p, int):
                # A later strong wait on the same page does not clear an earlier weak one:
                # the receipts list every load, and the reader sees both.
                out.setdefault(p, sig)
    return out


def build(mod, ans, raw_receipts, notes, answers_path, receipts_path, *, save_waits=None,
          cfg=None, schema_props=None, page_kinds_path=None, data_dir=None, check_ledger=None, check_plan=None,
          module_path=None):
    if ans.get("module_id") != mod.get("module_id"):
        raise Refused(f"answers.json is for {ans.get('module_id')!r}, module.json is "
                      f"{mod.get('module_id')!r}")
    # Collect every problem, then refuse once with all of them.
    problems = notes_problems(notes)
    if not isinstance(notes, dict):
        raise Refused(problems)

    mod_items = {i["item_id"]: (p["page_index"], i) for p in mod["pages"] for i in p["items"]}
    answer_ids = [a["item_id"] for a in ans["items"]]
    try:
        receipts = load_receipts(raw_receipts, set(answer_ids))
    except Refused as e:
        raise Refused(problems + e.problems) from None
    st = store.Store(cfg, data_dir=data_dir)
    base = receipts_path.parent if receipts_path else answers_path.parent

    # The run window, unless the notes say otherwise and why: it starts when the earliest bound
    # payload was built, or at the first receipt when no receipt binds a payload, and ends at
    # the last receipt. A receipt taken before that payload then falls outside the window.
    seen = sorted((aware(r["observed_at"], "observed_at"), r["observed_at"])
                  for rs in receipts.values() for r in rs)
    built = payload_times(receipts, base, st, mod["module_id"], cfg)
    # Unbound receipts never set the start while the run folder holds a payload.
    starts = built or run_payload_times(st, mod["module_id"], cfg) or seen[:1]
    derived_start = min(starts, key=lambda x: x[0]) if starts else None
    derived_end = seen[-1] if seen else None
    given_notes = notes.get("run_notes")
    run_notes = [n for n in given_notes if isinstance(n, str)] if isinstance(given_notes, list) else []
    window = {}
    for key, derived in (("started_at", derived_start), ("ended_at", derived_end)):
        given = notes.get(key)
        try:
            t = aware(given, f"notes.{key}") if given is not None else None
        except Refused:
            t = given = None   # notes_problems() reported it
        if t is None:
            window[key] = derived
            continue
        window[key] = (t, given)
        if derived is not None and t != derived[0]:
            if not notes.get("times_reason"):
                problems.append(f"notes.{key} {given} differs from {derived[1]}, the time the "
                                f"payload and receipts give; add times_reason saying why, or drop "
                                f"{key} from fill-notes.json")
            else:
                run_notes.append(f"{key} {given} from fill-notes.json, where the payload and "
                                 f"receipts give {derived[1]}: {notes['times_reason']}")
    if window["started_at"] is None:
        problems.append("notes: started_at is missing and no receipt or bound payload gives a "
                        "time; set started_at in fill-notes.json")
    start, end = window["started_at"], window["ended_at"]
    if start and end and end[0] < start[0]:
        problems.append("notes: ended_at precedes started_at")
    if start:
        problems += window_problems(receipts, start[0], end[0] if end else None,
                                    derived_start or start, derived_end)
    note_items = notes.get("items") or {}
    if not isinstance(note_items, dict):
        note_items = {}
    stray = sorted(set(note_items) - set(answer_ids))
    if stray:
        problems.append(f"notes.items: {stray} are not items in answers.json")

    answers_digest = hashlib.sha256(answers_path.read_bytes()).hexdigest()
    pl = plan_fill.plan(mod, ans, False, cfg, page_kinds_path=page_kinds_path, data_dir=data_dir, answers_sha256=answers_digest)
    rat_status = {r["page_index"]: r for r in pl.get("rat_pages", [])}
    sheet_status = {idx: r for idx, r in rat_status.items() if r["decision"] == "fill-from-sheet"}

    # Never infer an executed sheet from today's decision. Both browser receipts
    # echo the payload hash; record pins it, and the payload pins classification.
    import emit_fill
    pkpath = Path(page_kinds_path) if page_kinds_path else st.run_dir(mod["module_id"]) / "page-kinds.json"
    stale = emit_fill.stale_page_kinds(pkpath, answers_digest)
    if stale:
        problems.append(stale)
    payloads = {}
    binding_fields = {}
    repins = {}
    bound = True
    for iid, rs in receipts.items():
        if not bound:
            break
        pg = mod_items[iid][0]
        answer = next(a for a in ans["items"] if a["item_id"] == iid)
        for receipt in rs:
            binding = receipt.get("source_payload")
            page = next(p for p in mod["pages"] if p["page_index"] == pg)
            required = (answer.get("skip_reason") == "rat-sheet" or
                        page_kinds.is_candidate(page, rat.policy_from_config(cfg)) and
                        rat_status.get(pg, {}).get("decision") not in {"not-rat", "fill-from-draft"})
            if not binding and not required:
                continue
            rat_receipt = required
            reason = "source payload binding is missing or malformed"
            try:
                if not isinstance(binding, dict) or "path" not in binding or set(binding) - {"path", "sha256", "sha16"}:
                    raise ValueError
                reason = "payload path is invalid"
                ppath = Path(binding["path"])
                if not ppath.is_absolute():
                    ppath = base / ppath
                if any(rat.within(ppath, root) for _, root in rat.effective_roots(rat.policy_from_config(cfg))):
                    reason = "payload path is inside a RAT answers folder"
                    raise ValueError
                dropped = []
                candidates = emit_fill.payload_candidates(st, mod["module_id"], [ppath], dropped)
                reason = (f"payload path {dropped[0]}" if dropped and "outside" in dropped[0]
                          else "payload hash mismatch" if ppath.is_file() else "payload file missing")
                ppath, digest, bound_field = emit_fill.bind_payload(
                    {"source_payload_sha256": binding.get("sha256"), "source_payload_sha16": binding.get("sha16")}, candidates)
                binding_fields[(iid, receipt["observed_at"])] = binding_label(receipt, bound_field)
                reason = "payload file missing or unreadable"
                raw = ppath.read_bytes()
                reason = "payload JSON is malformed"
                payload = payloads.setdefault(digest, json.loads(raw))
                reason = "payload schema differs"
                if payload.get("schema") != emit_fill.PAYLOAD_SCHEMA:
                    raise ValueError
                reason = "payload module or run differs"
                if payload.get("module_id") != mod["module_id"] or payload.get("run_id") != mod["run_id"]:
                    raise ValueError
                reason = "question digest changed"
                if payload.get("module", {}).get("question_digest") != emit_fill.question_digest(mod):
                    raise ValueError
                # A load taken before its payload was built cannot verify a write from it.
                reason = "payload generated_at is not an ISO 8601 timestamp with an offset"
                try:
                    built_at = aware(payload.get("generated_at"), "generated_at")
                except Refused:
                    raise ValueError from None
                if aware(receipt["observed_at"], "observed_at") < built_at:
                    problems.append(f"{iid}: receipt observed_at {receipt['observed_at']} precedes "
                                    f"its payload's generated_at {payload['generated_at']}; a load "
                                    f"taken before the payload was built cannot verify its write")
                pinned_answers = payload.get("answers", {}).get("sha256")
                reason = ("answers.json changed since the payload was built "
                          f"(payload {str(pinned_answers)[:16]}, current {answers_digest[:16]}). "
                          f"If no written draft changed, run emit_fill.py repin --module <module.json> "
                          f"--answers <answers.json>; it checks every job and never writes answers.json")
                if pinned_answers != answers_digest:
                    repin = emit_fill.repin_record(ppath, digest, pinned_answers, answers_digest)
                    if repin is None:
                        raise ValueError
                    if str(ppath) not in repins:
                        # Any agent can write answers-repin.json, so a matching record proves
                        # nothing: rebuild every job the payload holds, as repin did.
                        reason = (f"answers-repin.json for {ppath} cannot be checked: the module "
                                  f"path is unknown")
                        if module_path is None:
                            raise ValueError
                        reason = (f"answers-repin.json for {ppath} cannot be checked: the payload's "
                                  f"jobs do not rebuild from answers.json")
                        try:
                            changed = emit_fill.repin_changed(
                                mod, ans, payload, module_path=module_path, answers_path=answers_path,
                                cfg=cfg, data_dir=data_dir, anchors=repin.get("anchors"),
                                approvals=repin.get("approvals"))
                        except emit_fill.Refusal as e:
                            reason = f"answers-repin.json for {ppath} cannot be checked: {e}"
                            raise ValueError from None
                        reason = (f"answers-repin.json for {ppath} does not hold: {len(changed)} "
                                  f"job(s) no longer rebuild from answers.json to the payload's "
                                  f"job content: {', '.join(changed)}. What they wrote, or where, "
                                  f"differs from answers.json; build a new payload, fill and "
                                  f"verify them")
                        if changed:
                            raise ValueError
                    repins[str(ppath)] = repin
                reason = f"job missing for {iid}"
                job = next(j for page in payload["pages"].values() for j in page["jobs"] if j["item"] == iid)
                rat_receipt = required or job.get("source") == "rat-sheet"
                reason = f"expected hash differs for {iid}"
                if str(emit_fill.expected_hash(job)) != str(receipt["expected_hash"]):
                    raise ValueError
                if job.get("source") != "rat-sheet":
                    if required:
                        reason = f"job source differs from the RAT sheet for {iid}"
                        raise ValueError
                    continue
                reason = "page-kinds snapshot changed"
                pin = payload.get("source_page_kinds") or {}
                if (not pin.get("sha256") or not pkpath.is_file() or
                        hashlib.sha256(pkpath.read_bytes()).hexdigest() != pin["sha256"] or
                        pl["rat_policy"]["record_sha256"] != pin["sha256"]):
                    raise ValueError
                reason = f"RAT sheet identity changed for {iid}"
                sheet = sheet_status.get(pg, {}).get("sheet") or {}
                if any(job.get("sheet", {}).get(k) != sheet.get(k) for k in ("root", "name", "sha256")):
                    raise ValueError
            except (OSError, ValueError, TypeError, KeyError, StopIteration, emit_fill.Refusal):
                prefix = "RAT receipt binding" if rat_receipt else "receipt binding"
                problems.append(f"{prefix}: {reason}")
                bound = False
                break
    for path, r in repins.items():
        run_notes.append(f"answers.json re-pinned for {path} at {r.get('recorded_at')}: "
                         f"{str(r.get('from_answers_sha256'))[:16]} to "
                         f"{str((r.get('to_answers') or {}).get('sha256'))[:16]}, "
                         f"{r.get('jobs_checked')} job(s) rebuild to the same job content")

    bm = pl["boundary_map"]
    fillable, held, page_skips = plan_index(pl)
    weak = weak_pages(save_waits)

    items, missing = [], []
    for a in ans["items"]:
        iid = a["item_id"]
        if iid not in mod_items:
            problems.append(f"{iid}: in answers.json but not in module.json")
            continue
        pg, m = mod_items[iid]
        note = note_items.get(iid) or {}
        if not isinstance(note, dict):
            continue   # notes_problems() reported it
        rs = receipts.get(iid)
        base = {"item_id": iid, "item_type": m["type"], "attempts": m.get("attempts") or "multi"}
        type_method = "selector" if m["type"] in SELECTOR_TYPES else "view-model"
        sheet_page = sheet_status.get(pg)
        sheet = (sheet_page or {}).get("sheet")
        number = next((n for p in mod["pages"] if p["page_index"] == pg
                       for n, it in enumerate(p["items"], 1) if it["item_id"] == iid), None)
        sheet_item = sheet and a.get("skip_reason") in {"rat-sheet", "trat-answers-unavailable"} or sheet and a.get("status") == "drafted"
        if sheet_item:
            base["answer_source"] = dict({k: sheet[k] for k in ("root", "name", "sha256")},
                                         kind="rat-sheet", question=number, page_kind=sheet_page["kind"],
                                         classified_by=sheet_page["sources"])
        if rs:
            if sheet_item and str(number) in sheet.get("todo_questions", {}):
                problems.append(f"{iid}: receipt for an unanswered RAT sheet question")
                continue
            extra = sorted(set(note) - NOTE_KEYS)
            if extra:
                problems.append(f"{iid}: has a receipt, so its result is the receipt's; the note "
                                f"may carry only {sorted(NOTE_KEYS)}, not {extra}")
                continue
            method = note.get("method") or type_method
            if method not in WRITE_METHODS:
                problems.append(f"{iid}: has a receipt, so it was written; method {method!r} "
                                f"says it was not")
                continue
            last, ok = rs[-1], matches(rs[-1])
            # Only a receipt bound to an executed payload whose jobs match today's draft
            # verifies; the binding loop above refused every bound receipt that does not.
            unbound = not last.get("source_payload")
            receipt = {k: last[k] for k in ("expected_hash", "observed_hash", "observed_at")}
            if unbound:
                receipt["binding"] = UNBOUND
            base.update(attempted=True, method=method,
                        result=("filled-unverified" if unbound else "filled") if ok else "failed",
                        verified=ok and not unbound,
                        stopped_reason=joined(note.get("stopped_reason"),
                                              WEAK_SIGNALS.get(weak.get(pg)),
                                              receipt_reason(rs),
                                              UNBOUND_REASON if unbound else None,
                                              binding_fields.get((iid, rs[-1]["observed_at"]))),
                        receipt=receipt)
            ap = a.get("single_attempt_approval") or {}
            if m.get("attempts") == "single" and ap.get("approval_id"):
                base["approval_id"] = ap["approval_id"]
        elif "result" in note:
            extra = sorted(set(note) - UNRECEIPTED_KEYS)
            if extra:
                problems.append(f"{iid}: note keys {extra} are not allowed")
                continue
            res = note["result"]
            if res not in NOTE_RESULTS:
                problems.append(f"{iid}: note result {res!r}; filled needs a fresh-load receipt, "
                                f"and a note may set only {sorted(NOTE_RESULTS)}")
                continue
            if not note.get("stopped_reason"):
                problems.append(f"{iid}: a note that sets result needs a stopped_reason")
                continue
            attempted = note.get("attempted", res in {"failed", "filled-unverified"})
            if res in {"failed", "filled-unverified"} and not attempted:
                problems.append(f"{iid}: result {res!r} with attempted false")
                continue
            method = note.get("method") or (type_method if attempted else
                                            "manual-todo" if res == "todo" else "not-attempted")
            base.update(attempted=attempted, method=method, result=res, verified=False,
                        stopped_reason=note["stopped_reason"])
            if res == "todo":
                todo = note.get("todo") or a.get("todo")
                if not todo:
                    problems.append(f"{iid}: result todo with no todo in the note or the draft")
                    continue
                base["todo"] = todo
            ap = a.get("single_attempt_approval") or {}
            if attempted and m.get("attempts") == "single" and ap.get("approval_id"):
                base["approval_id"] = ap["approval_id"]
        elif sheet_item and str(number) in sheet.get("todo_questions", {}):
            state = sheet["todo_questions"][str(number)]
            blocker = (f"Question {number} has no marked answer in {sheet['name']}" if state == "unanswered" else
                       f"Question {number} has {state} answer in {sheet['name']}")
            base.update(attempted=False, method="manual-todo", result="todo", verified=False,
                        todo={"blocker": blocker, "placement": f"page {pg}, panel {m['panel_key']}"},
                        stopped_reason="RAT sheet question needs your answer")
        elif sheet_item and iid in fillable:
            missing.append(iid)
            continue
        elif pg in page_skips:
            base.update(attempted=False, method="not-attempted", result="skipped", verified=False,
                        stopped_reason=joined(page_skips[pg]["reason"], page_skips[pg].get("detail")))
        elif a.get("status") == "blocked":
            base.update(attempted=False, method="manual-todo", result="todo", verified=False,
                        todo=a["todo"], stopped_reason=joined(
                            note.get("stopped_reason"),
                            f"Blocked in the draft: {a['todo']['blocker']}"))
        elif a.get("status") == "skipped":
            base.update(attempted=False, method="not-attempted", result="skipped",
                        verified=False, stopped_reason=joined(
                            note.get("stopped_reason"),
                            f"Declined by the answerer: {a.get('skip_reason')}."))
        elif iid in fillable:
            missing.append(iid)
            continue
        else:
            e = held.get(iid)
            if e is not None and (e.get("skip") or {}).get("class") == "unmapped":
                base.update(attempted=False, method="manual-todo", result="todo", verified=False,
                            todo=e["todo"], stopped_reason=joined(
                                note.get("stopped_reason"),
                                f"Not written: unmapped type. {e['skip']['detail']}."))
            elif e is not None and (e.get("skip") or {}).get("class") == "single-attempt-unapproved":
                base.update(attempted=False, method="not-attempted", result="skipped", verified=False,
                            stopped_reason=joined(
                                note.get("stopped_reason"),
                                "Not written: single-attempt, and answers.json carries no "
                                "single_attempt_approval; yours to check and submit."))
            else:
                if e is not None:
                    why = (e.get("skip") or {}).get("class") or e.get("draft_state")
                elif pg in page_skips:
                    why = f"page {pg} skipped, {page_skips[pg]['reason']}"
                else:
                    why = f"type {m['type']} is not a fill target"
                base.update(attempted=False, method="not-attempted", result="skipped",
                            verified=False, stopped_reason=joined(
                                note.get("stopped_reason"),
                                f"Not written: plan_fill.py holds it back ({why})."))
        for k in ("selector_used", "lt_conflict"):
            if k in note:
                base[k] = note[k]
        items.append({k: v for k, v in base.items() if v is not None})
    if missing:
        problems.append(f"{len(missing)} drafted item(s) planned fillable have no receipt and no "
                        f"note: {', '.join(missing)}. Record a fresh-load receipt for each one "
                        f"written (emit_fill.py record), or a note with its result and reason for "
                        f"each one not written or not verified")
    if problems:
        raise Refused(problems)

    page_of = {iid: pg for iid, (pg, _) in mod_items.items()}
    pages_filled = notes.get("pages_filled")
    if pages_filled is None:
        pages_filled = sorted({page_of[i["item_id"]] for i in items if i["result"] in WRITTEN})
    routes = [dict(n) for n in bm["never_navigated"]]
    for n in notes.get("never_navigated") or []:
        if n.get("route") not in {r["route"] for r in routes}:
            routes.append(n)

    rep = {
        "schema_version": 1, "module_id": mod["module_id"], "run_id": mod["run_id"],
        "started_at": start[1], "ended_at": end[1] if end else None,
        "approval_mode": notes["approval_mode"],
        "source_answers": {"path": str(answers_path.resolve()),
                           "sha256": hashlib.sha256(answers_path.read_bytes()).hexdigest()},
        "boundary_reached": notes.get("boundary_reached") or default_boundary(pl, mod),
        "never_navigated": routes,
        "pages_filled": pages_filled,
        "items": items,
    }
    if pl.get("rat_pages"):
        # Safe status projection only. The record's sheet evidence contains no answers.
        rep["rat_pages"] = [{k: v for k, v in r.items() if k not in {"question", "reasons", "check_reason"}}
                            for r in pl["rat_pages"]]
        if pkpath.is_file() and pl["rat_policy"]["record_sha256"]:
            rep["source_page_kinds"] = {"path": str(pkpath.resolve()), "sha256": hashlib.sha256(pkpath.read_bytes()).hexdigest()}
    if receipts_path is not None and receipts_path.is_file():
        rep["source_receipts"] = {"path": str(receipts_path.resolve()),
                                  "sha256": hashlib.sha256(receipts_path.read_bytes()).hexdigest()}
    for k in ("user_decisions", "selector_events", "connection_events"):
        if notes.get(k):
            rep[k] = notes[k]
    if run_notes and (not schema_props or "run_notes" in schema_props):
        rep["run_notes"] = run_notes
    if schema_props and "save_waits" in schema_props and save_waits:
        rows = save_waits.get("save_waits") if isinstance(save_waits, dict) else save_waits
        rep["save_waits"] = rows
    if check_ledger or check_plan:
        from ltcore import check_answer as ca
        projected = ca.project(mod, ans, check_ledger)
        statuses={r['page_index']:r for r in (check_plan or {}).get('rat_pages',[])}
        for refusal in (check_plan or {}).get('refused',[]):
            iid=refusal['item'];reason=refusal['reason']
            if iid not in projected:
                projected[iid]={'eligible':False,'reason':reason,'policy':check_plan['policy'],
                  'rat_page':statuses[refusal['page']],'calls':[], 'pool_h':[], 'draft':{'state':{},'hash':'','refs':[]},
                  'presses':[], 'final':{'state':{},'hash':'','index_state':'','verdict':None,'stop':reason,'moved':False},'lt_feedback':''}
        for item in rep['items']:
            iid = item['item_id']
            if iid in projected:
                item['check_answer'] = projected[iid]
        rep['check_answer'] = ca.summary(rep['items'])
        # Judge each historical press against its recorded classification.
        rat_rows = {r['page_index']:r for r in rep.get('rat_pages',[])}
        for call in (check_ledger or {}).get('checks',[]):
            status = call.get('rat_page') or {}
            if status.get('candidate') and call.get('page') not in sheet_status:
                rat_rows[call['page']] = {k:v for k,v in status.items() if k not in {'check_reason','question','reasons'}}
        if rat_rows:
            rep['rat_pages'] = list(rat_rows.values())
    return rep, weak


def schema_properties() -> set[str]:
    """Top-level properties fill-report.schema.json allows, or an empty set when unknown."""
    try:
        from ltcore import validate  # noqa: PLC0415
        sd = Path(validate.__file__).resolve().parent.parent / "schemas" / "fill-report.schema.json"
        return set(json.loads(sd.read_text(encoding="utf-8")).get("properties", {}))
    except Exception:
        return set()


def validator_path() -> Path | None:
    """ltcore/validate.py in this skill's _shared copy, or wherever ltcore is imported from."""
    p = Path(ltcore.__file__).resolve().parent / "validate.py"
    return p if p.is_file() else None


def _read(path: Path, what: str, default=None):
    if path is None or not path.is_file():
        if default is not None:
            return default
        raise Refused(f"{what}: {path} not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise Refused(f"{what}: {e}")


def refusal_text(e: Refused) -> str:
    if len(e.problems) == 1:
        return f"REFUSED: {e.problems[0]}"
    return f"REFUSED: {len(e.problems)} problems\n" + "\n".join(f"  - {p}" for p in e.problems)


def main(argv=None) -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", required=True, type=Path)
    ap.add_argument("--answers", required=True, type=Path)
    ap.add_argument("--receipts", type=Path, help="default <run>/receipts.json")
    ap.add_argument("--notes", type=Path, help="default <run>/fill-notes.json")
    ap.add_argument("--save-waits", type=Path, help="default <run>/save-waits.json")
    ap.add_argument("--approvals", type=Path, help="default <run>/approvals.json when it exists")
    ap.add_argument("--out", type=Path, help="default <run>/fill-report.json")
    ap.add_argument("--validator", type=Path, help="validate.py to run (default the shared copy)")
    ap.add_argument("--page-kinds", type=Path)
    ap.add_argument("--check-notes", action="store_true",
                    help="run every check, print each problem, write nothing")
    ap.add_argument("--config")
    ap.add_argument("--data-dir")
    args = ap.parse_args(argv)
    try:
        cfg = config.load(args.config)
        mod = _read(args.module, "module")
        ans = _read(args.answers, "answers")
        st = store.Store(cfg, data_dir=args.data_dir)
        mid = store.check_id(str(mod.get("module_id")))
        run = st.run_dir(mid)
        rpath = args.receipts or run / "receipts.json"
        npath = args.notes or run / "fill-notes.json"
        wpath = args.save_waits or run / "save-waits.json"
        receipts = _read(rpath, "receipts", default={"receipts": []})
        notes = _read(npath, "notes")
        waits = _read(wpath, "save waits", default={"save_waits": []})
        rep, weak = build(mod, ans, receipts, notes, args.answers, rpath, save_waits=waits,
                          cfg=cfg, schema_props=schema_properties(), page_kinds_path=args.page_kinds, data_dir=args.data_dir,
                          check_ledger=_read(run/'check-answer.json','check ledger',default={}),
                          check_plan=_read(run/'fill-js/check-plan.json','check plan',default={}),
                          module_path=args.module)
    except Refused as e:
        print(refusal_text(e), file=sys.stderr)
        return 3
    except (config.ConfigError, ValueError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    n = {r: sum(i["result"] == r for i in rep["items"])
         for r in ("filled", "failed", "filled-unverified", "todo", "skipped")}
    counts = ", ".join(f"{v} {k}" for k, v in n.items())
    if args.check_notes:
        print(f"notes OK: {npath} | started_at {rep['started_at']}, ended_at {rep['ended_at']} | "
              f"the report would hold {counts}; nothing written")
        return 0
    for name, path in (("source_check_answer", run / "check-answer.json"),
                       ("source_check_plan", run / "fill-js" / "check-plan.json")):
        if path.is_file():
            rep[name] = {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    out = args.out or st.fill_report_path(mid)
    out.parent.mkdir(parents=True, exist_ok=True)
    # The candidate replaces --out only after the validator passes it.
    fd, tmp = tempfile.mkstemp(prefix="." + out.name + ".", suffix=".json", dir=str(out.parent))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    rejected = out.with_name(out.stem + ".rejected.json")
    for sig in ("fallback", "unconfirmed", "error"):
        pages = sorted(p for p, s in weak.items() if s == sig)
        if pages:
            print(f"{sig} pages: {', '.join(map(str, pages))} (the fresh-load receipts are the proof "
                  f"for these pages)", flush=True)

    def reject(why: str) -> int:
        os.replace(tmp, rejected)
        print(f"{why}; {out} {'kept as it was' if out.exists() else 'not written'}, the rejected "
              f"report is {rejected}", file=sys.stderr)
        return 1

    vpath = args.validator or validator_path()
    if vpath is None or not vpath.is_file():
        return reject(f"NOT VALIDATED: {vpath or 'ltcore/validate.py'} not found")
    cmd = [sys.executable, str(vpath), tmp, "--schema", "fill-report", "--module", str(args.module)]
    if args.config:
        cmd += ["--config", args.config]
    apath = args.approvals or run / "approvals.json"
    if apath.is_file():
        cmd += ["--approvals", str(apath)]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        return reject(f"NOT WRITTEN: the validator exited {r.returncode}")
    os.replace(tmp, out)
    print(f"wrote {out} | {counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
