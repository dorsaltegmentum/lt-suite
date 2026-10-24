#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write fill-report.json from what a fill run measured, then validate it.

    make_fill_report.py --module <module.json> --answers <answers.json>
        [--receipts R] [--notes N] [--save-waits W] [--approvals A] [--out F]
        [--config C] [--data-dir D]

Defaults, all inside <data>/runs/<module_id>/: receipts.json and save-waits.json (written by
`emit_fill.py record`), fill-notes.json, approvals.json, and fill-report.json for --out.

Exit: 0 written and the validator passes it, 1 written and the validator rejects it or cannot
be found ("NOT VALIDATED"), 3 input refused and nothing written.

`result` and `verified` for a written item come from one source: the fresh-load hash
receipts. No input can set `verified`.

Receipts: a JSON list, or {"receipts": [...]}, of exactly

    {"item_id": "p4-12", "expected_hash": 2066687429, "observed_hash": 2066687429,
     "observed_at": "2000-01-01T00:00:00Z"}

from a fresh load of the item's page. expected_hash is the hash of the drafted answer and
observed_hash the hash of what the load read back; for a table both cover every target cell.
Hashes are strings or integers and compare as strings. An item's latest receipt decides it:
equal hashes give result filled and verified true, unequal give failed and verified false.
Earlier receipts go into stopped_reason, so a refill that a later load verified reads as one.
A receipt is refused when its item is not in answers.json, when it falls outside started_at
to ended_at, or when it ties another for its item's latest time.

Save waits: `emit_fill.py record` keeps each call's save_signal. Every page whose wait ended
on the fallback rule (an origin without a calibrated save signal) or unconfirmed is listed
on stdout, and the items written there say so in stopped_reason: for them the fresh-load
receipt is the only proof of the save.

Notes (fill-notes.json) carry what only the operator saw:

    started_at, ended_at     ISO 8601 with an offset; ended_at may be null
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
    selector_events, connection_events    copied as given

A note may carry stopped_reason, method, selector_used and lt_conflict. A note on an item with
no receipt may also carry result (failed, filled-unverified, skipped or todo), attempted and
todo, and needs a stopped_reason when it sets result. No note carries `verified`, no note says
result filled, and no note sets the result of an item that has a receipt.

A drafted item that plan_fill.py plans as fillable, with no receipt and no note, is refused:
either it was written and its receipt is missing, or it was not written and the reason is
missing. A drafted item the plan holds back is reported skipped with the plan's reason, or
todo with the plan's placement for an unmapped type. A blocked draft is a todo with its own
placement. A skipped draft stays skipped with its skip_reason.
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

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402

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
TOP_KEYS = {"started_at", "ended_at", "approval_mode", "boundary_reached", "pages_filled",
            "never_navigated", "items", "user_decisions", "selector_events",
            "connection_events"}
# Written through a file input. Every other type is written through its view model.
SELECTOR_TYPES = {"upload-and-annotate", "image-annotation"}
# Save waits that prove nothing by themselves; the fresh-load receipt is the proof.
WEAK_SIGNALS = {"fallback": "Saved under the fallback wait (no calibrated save signal for this origin); "
                            "the fresh-load receipt is the proof of the save.",
                "unconfirmed": "The save wait ended unconfirmed; the fresh-load receipt decides.",
                "error": "The save wait reported a server error on this call; the fresh-load receipt decides."}


class Refused(Exception):
    pass


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


def load_receipts(raw, answer_ids, start, end):
    """{item_id: [receipt, ...]} oldest first, each receipt checked."""
    rows = raw.get("receipts") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise Refused("receipts: expected a list, or an object with a 'receipts' list")
    by_item: dict[str, list] = {}
    for n, r in enumerate(rows):
        tag = f"receipts[{n}]"
        if not isinstance(r, dict) or set(r) != RECEIPT_KEYS:
            got = sorted(r) if isinstance(r, dict) else type(r).__name__
            raise Refused(f"{tag}: keys must be exactly {sorted(RECEIPT_KEYS)}, got {got}")
        if r["item_id"] not in answer_ids:
            raise Refused(f"{tag}: {r['item_id']!r} is not an item in answers.json")
        for k in ("expected_hash", "observed_hash"):
            if not is_hash(r[k]):
                raise Refused(f"{tag}: {k} {r[k]!r} is not a hash")
        t = aware(r["observed_at"], f"{tag}.observed_at")
        if t < start:
            raise Refused(f"{tag}: observed_at {r['observed_at']} precedes started_at; a load "
                          f"taken before the run cannot verify its write")
        if end is not None and t > end:
            raise Refused(f"{tag}: observed_at {r['observed_at']} is after ended_at; a run ends "
                          f"after its last check")
        by_item.setdefault(r["item_id"], []).append((t, r))
    out = {}
    for iid, rs in by_item.items():
        rs.sort(key=lambda x: x[0])
        if len(rs) > 1 and rs[-1][0] == rs[-2][0]:
            raise Refused(f"{iid}: two receipts share its latest observed_at "
                          f"{rs[-1][1]['observed_at']}; one fresh load gives one receipt")
        out[iid] = [r for _, r in rs]
    return out


def receipt_reason(rs):
    last = rs[-1]
    if matches(last):
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
          cfg=None, schema_props=None):
    if ans.get("module_id") != mod.get("module_id"):
        raise Refused(f"answers.json is for {ans.get('module_id')!r}, module.json is "
                      f"{mod.get('module_id')!r}")
    unknown = sorted(set(notes) - TOP_KEYS)
    if unknown:
        raise Refused(f"notes: unknown key(s) {unknown}; allowed {sorted(TOP_KEYS)}")
    hits = keys_named(notes, "verified")
    if hits:
        raise Refused(f"notes: 'verified' at {', '.join(hits)}; verified comes from receipts only")
    if notes.get("approval_mode") not in APPROVAL_MODES:
        raise Refused("notes: approval_mode must record how the browser tool was supervised: "
                      "manual, auto or skip-all")
    start = aware(notes.get("started_at"), "notes.started_at")
    end = (None if notes.get("ended_at") is None
           else aware(notes["ended_at"], "notes.ended_at"))
    if end is not None and end < start:
        raise Refused("notes: ended_at precedes started_at")

    mod_items = {i["item_id"]: (p["page_index"], i) for p in mod["pages"] for i in p["items"]}
    answer_ids = [a["item_id"] for a in ans["items"]]
    receipts = load_receipts(raw_receipts, set(answer_ids), start, end)
    note_items = notes.get("items") or {}
    stray = sorted(set(note_items) - set(answer_ids))
    if stray:
        raise Refused(f"notes.items: {stray} are not items in answers.json")

    pl = plan_fill.plan(mod, ans, False, cfg)
    bm = pl["boundary_map"]
    fillable, held, page_skips = plan_index(pl)
    weak = weak_pages(save_waits)

    items, missing = [], []
    for a in ans["items"]:
        iid = a["item_id"]
        if iid not in mod_items:
            raise Refused(f"{iid}: in answers.json but not in module.json")
        pg, m = mod_items[iid]
        note = note_items.get(iid) or {}
        rs = receipts.get(iid)
        base = {"item_id": iid, "item_type": m["type"], "attempts": m.get("attempts") or "multi"}
        type_method = "selector" if m["type"] in SELECTOR_TYPES else "view-model"
        if rs:
            extra = sorted(set(note) - NOTE_KEYS)
            if extra:
                raise Refused(f"{iid}: has a receipt, so its result is the receipt's; the note "
                              f"may carry only {sorted(NOTE_KEYS)}, not {extra}")
            method = note.get("method") or type_method
            if method not in WRITE_METHODS:
                raise Refused(f"{iid}: has a receipt, so it was written; method {method!r} "
                              f"says it was not")
            last, ok = rs[-1], matches(rs[-1])
            base.update(attempted=True, method=method, result="filled" if ok else "failed",
                        verified=ok, stopped_reason=joined(note.get("stopped_reason"),
                                                           WEAK_SIGNALS.get(weak.get(pg)),
                                                           receipt_reason(rs)),
                        receipt={k: last[k] for k in ("expected_hash", "observed_hash",
                                                      "observed_at")})
            ap = a.get("single_attempt_approval") or {}
            if m.get("attempts") == "single" and ap.get("approval_id"):
                base["approval_id"] = ap["approval_id"]
        elif "result" in note:
            extra = sorted(set(note) - UNRECEIPTED_KEYS)
            if extra:
                raise Refused(f"{iid}: note keys {extra} are not allowed")
            res = note["result"]
            if res not in NOTE_RESULTS:
                raise Refused(f"{iid}: note result {res!r}; filled needs a fresh-load receipt, "
                              f"and a note may set only {sorted(NOTE_RESULTS)}")
            if not note.get("stopped_reason"):
                raise Refused(f"{iid}: a note that sets result needs a stopped_reason")
            attempted = note.get("attempted", res in {"failed", "filled-unverified"})
            if res in {"failed", "filled-unverified"} and not attempted:
                raise Refused(f"{iid}: result {res!r} with attempted false")
            method = note.get("method") or (type_method if attempted else
                                            "manual-todo" if res == "todo" else "not-attempted")
            base.update(attempted=attempted, method=method, result=res, verified=False,
                        stopped_reason=note["stopped_reason"])
            if res == "todo":
                todo = note.get("todo") or a.get("todo")
                if not todo:
                    raise Refused(f"{iid}: result todo with no todo in the note or the draft")
                base["todo"] = todo
            ap = a.get("single_attempt_approval") or {}
            if attempted and m.get("attempts") == "single" and ap.get("approval_id"):
                base["approval_id"] = ap["approval_id"]
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
        raise Refused(f"{len(missing)} drafted item(s) planned fillable have no receipt and no "
                      f"note: {', '.join(missing)}. Record a fresh-load receipt for each one "
                      f"written (emit_fill.py record), or a note with its result and reason for "
                      f"each one not written or not verified")

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
        "started_at": notes["started_at"], "ended_at": notes.get("ended_at"),
        "approval_mode": notes["approval_mode"],
        "source_answers": {"path": str(answers_path.resolve()),
                           "sha256": hashlib.sha256(answers_path.read_bytes()).hexdigest()},
        "boundary_reached": notes.get("boundary_reached") or default_boundary(pl, mod),
        "never_navigated": routes,
        "pages_filled": pages_filled,
        "items": items,
    }
    if receipts_path is not None and receipts_path.is_file():
        rep["source_receipts"] = {"path": str(receipts_path.resolve()),
                                  "sha256": hashlib.sha256(receipts_path.read_bytes()).hexdigest()}
    for k in ("user_decisions", "selector_events", "connection_events"):
        if notes.get(k):
            rep[k] = notes[k]
    if schema_props and "save_waits" in schema_props and save_waits:
        rows = save_waits.get("save_waits") if isinstance(save_waits, dict) else save_waits
        rep["save_waits"] = rows
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


def main(argv=None) -> int:
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
                          cfg=cfg, schema_props=schema_properties())
    except (config.ConfigError, ValueError, Refused) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    out = args.out or st.fill_report_path(mid)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = {r: sum(i["result"] == r for i in rep["items"])
         for r in ("filled", "failed", "filled-unverified", "todo", "skipped")}
    print(f"wrote {out} | " + ", ".join(f"{v} {k}" for k, v in n.items()), flush=True)
    for sig in ("fallback", "unconfirmed", "error"):
        pages = sorted(p for p, s in weak.items() if s == sig)
        if pages:
            print(f"{sig} pages: {', '.join(map(str, pages))} (the fresh-load receipts are the proof "
                  f"for these pages)", flush=True)
    vpath = args.validator or validator_path()
    if vpath is None or not vpath.is_file():
        print(f"NOT VALIDATED: {vpath or 'ltcore/validate.py'} not found", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(vpath), str(out), "--schema", "fill-report", "--module", str(args.module)]
    if args.config:
        cmd += ["--config", args.config]
    apath = args.approvals or run / "approvals.json"
    if apath.is_file():
        cmd += ["--approvals", str(apath)]
    r = subprocess.run(cmd)
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
