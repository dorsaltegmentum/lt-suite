#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the commit-boundary map and the fill plan before anything is typed.

    plan_fill.py <module.json> [--answers <answers.json>] [--json] [--out plan.json]
                 [--include-rat] [--config F]

Exit: 0 at least one item is fillable, 2 nothing is fillable, 3 input broken.

The rail "map every commit boundary before writing, stop at the first unmapped one" is
arithmetic over module.json, not prose: the answer is a number, `stop_before_page`. The
doors and their severities come from ltcore.boundaries, the one implementation the
validator also uses:

  severity        class                effect
  stop            checkpoint-page      sets stop_before_page; never advance past it
  per-item        single-attempt-item  never written without an approval recorded in the
                                       run's approvals.json, never submitted; later pages
                                       are still planned, because moving between pages
                                       submits nothing
  per-item        unmapped             the item becomes a TODO and is never written to
  never-navigate  run-level-commit     the completion route is never visited
  never-navigate  report-page          never visited; its panels mirror real ones and its
                                       panel keys repeat keys of real pages, so a target
                                       is (page_index, panel_key), never panel_key alone

An unmapped item is a gap in this suite, not a door: not writing it is a complete
defence, so it skips one item and the run continues. A Checkpoint is crossed by
navigating, and skipping cannot protect against a door one walks through.

Every candidate page is decided by the shared page-kinds.json store. Missing or
stale evidence holds the page. Sheet pages accept rat-sheet skips; tRAT drafts
need a recorded draft choice. --include-rat is deprecated and changes no decision.

Occupancy is not decided here. A non-empty target is a question for the user, settled
against the live page at fill time, never against the captured `answered` flag.

The JSON plan (--json, --out) carries the fields `ltjs.py check-url --plan` and the
never-submit guard read: origin, instance, course_id, run_id, stop_before_page,
never_navigated, report_pages, checkpoint_pages, flow_pages[] (every module page with
page_index, page_no, url, is_checkpoint and is_report_page) and pages[] with page_index,
page_no and url.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402
ltcore.bootstrap([])
from ltcore import boundaries, config, rat as rat_mod, page_kinds  # noqa: E402

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

# How each automation class is reached, in order of preference.
METHOD_FOR = {
    "structured-path": "view-model",
    "resilient-selector": "selector",
    "degrade-todo": "manual-todo",
}

NOT_FILLABLE = boundaries.NOT_FILLABLE
PLACEMENT_TYPES = boundaries.PLACEMENT_TYPES
SEVERITY = boundaries.SEVERITY
# Classes that may appear as fill-report's boundary_reached.class. `unmapped` is absent: a
# run that stopped on an unmapped item stopped for a reason that is no longer a stop.
STOPPING_CLASSES = {c for c, sev in SEVERITY.items() if sev == "stop"}



def fatal(msg: str) -> int:
    print(f"FATAL: {msg}", file=sys.stderr)
    return 3


def origin_of(url: str | None) -> str | None:
    m = re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else None


def _first_stop(module: dict) -> dict | None:
    """The first stop-severity door as a door dict, or None."""
    fsb = boundaries.first_stop_boundary(module)
    if fsb is None:
        return None
    if isinstance(fsb, dict):
        idx, cls, item = fsb.get("page_index"), fsb.get("class"), fsb.get("item_id")
    else:
        idx, cls, item = fsb
    bm = boundaries.boundary_map(module)
    for d in bm["doors"]:
        if d["page_index"] == idx and d["class"] == cls and d["item_id"] == item:
            return d
    return {"class": cls, "severity": "stop", "page_index": idx, "item_id": item,
            "detail": "first stop-severity door"}


def rat_policy(cfg) -> dict:
    return rat_mod.policy_from_config(cfg)


def _skip_entries(it: dict, p: dict, idx: int, entry: dict, state: str, lock: dict | None) -> None:
    """Set `skip`, `todo` and `occupancy` on a plan entry for every reason it is held back."""
    unmapped = it["type"] == "unmapped"
    declined = state.startswith("draft-skipped:") or state == "draft-blocked"
    read_only = it.get("read_only") is True
    inferred = lock is not None
    permanent = (it.get("degrade") or {}).get("class") == "permanent"
    mirror = bool(it.get("mirror_of"))
    where = f"page {idx}" + (f" ({p.get('page_no')})" if p.get("page_no") else "")
    if read_only:
        # isReadOnly true: Lt accepts no write, typically because the page was committed.
        # Not "already answered": distinct reason, distinct remedy, so no TODO.
        entry["skip"] = {"class": "read-only", "severity": "per-item", "skip_reason": "read-only",
                         "detail": "the panel carries isReadOnly=true; Lt will not accept a write, "
                                   "typically because the page has been committed. This is not "
                                   "'already answered': there is nothing to do here."}
        entry["occupancy"] = "not-applicable"
    if inferred and lock["class"] == "read-only-inferred":
        entry["skip"] = {"class": "read-only-inferred", "severity": "per-item",
                         "skip_reason": "read-only-inferred",
                         "detail": f"subpage child with read_only null. {lock['cause']}, and every "
                                   f"top-level item on this page that can show its lock reads "
                                   f"read_only true ({', '.join(lock['basis'])}). A write to a "
                                   f"locked panel reports success locally and never reaches the "
                                   f"server; verify from a fresh load if it matters."}
        entry["occupancy"] = "not-applicable"
    elif inferred:
        entry["skip"] = {"class": "lock-unknown", "severity": "per-item", "skip_reason": "lock-unknown",
                         "detail": f"subpage child with read_only null on a page with no top-level "
                                   f"item that can show its lock. {lock['cause']}, and "
                                   + (f"{', '.join(lock['basis'])} read locked with no page that "
                                      f"reads writable between them and this one"
                                      if lock["basis"] else "the whole run is locked")
                                   + ", so this page is probably locked. Not planned as fillable. "
                                     "To fill it anyway, settle occupancy first, then write one "
                                     "panel and check it from a fresh load: a locked write reports "
                                     "success locally and shows the old text on reload."}
    if mirror and not read_only and not inferred:
        mo = it["mirror_of"]
        entry["skip"] = {"class": "read-only-mirror", "severity": "per-item",
                         "skip_reason": "read-only-mirror",
                         "detail": f"this panel re-displays {mo['item_id']} from page "
                                   f"{mo['page_index']} and accepts no write. Basis: "
                                   f"{', '.join(mo['basis'])}. The answer belongs in the source "
                                   f"panel and appears here once it is there."}
        entry["occupancy"] = "not-applicable"
    if permanent and not unmapped and not read_only and not inferred and not mirror:
        deg = it.get("degrade") or {}
        reason = deg.get("reason") or "reason not recorded"
        entry["skip"] = {"class": "degrade-permanent", "severity": "per-item",
                         "skip_reason": "panel-accepts-no-write",
                         "detail": f"degrade.class is permanent ({reason}); this panel accepts no "
                                   f"write from anyone. Distinct from unmapped, which clears when "
                                   f"a write path is added."}
        entry["todo"] = {"blocker": f"Panel accepts no write, permanently: {reason}.",
                         "placement": f"{where}, panel {it['panel_key']}; deliver outside Lt."}
        entry["occupancy"] = "not-applicable"
    if declined and not unmapped:
        reason = state.split(":", 1)[1] if ":" in state else "blocked"
        entry["skip"] = {"class": "draft-declined", "severity": "per-item", "skip_reason": reason,
                         "detail": f"lt-module-answer returned status "
                                   f"{'skipped' if state.startswith('draft-skipped') else 'blocked'} "
                                   f"for this item ({reason}); there is nothing drafted to write "
                                   f"and the skip was deliberate."}
    if unmapped:
        note = (it.get("capture_note") or "pluginKey has no tested write path").rstrip(". ")
        entry["skip"] = {"class": "unmapped", "severity": "per-item",
                         "skip_reason": "unmapped-boundary", "detail": note}
        entry["todo"] = {"blocker": f"Item type is unmapped: {note}. No tested write path exists, "
                                    f"so this suite will not write here.",
                         "placement": f"{where}, panel {it['panel_key']}; fill by hand, or add a "
                                      f"write path and capture again (a capture is never edited)."}
        entry["occupancy"] = "not-applicable"


def plan(mod: dict, answers: dict | None, include_rat: bool, cfg=None, *, record=None, data_dir=None, page_kinds_path=None, answers_sha256=None) -> dict:
    bm = boundaries.boundary_map(mod)
    # Only a stop-severity door truncates the run. Per-item doors are handled item by item
    # below, and never-navigate doors by skipped_pages.
    first = _first_stop(mod)
    report_idx = sorted(p["page_index"] for p in mod.get("pages", [])
                        if p.get("is_report_page") and p.get("page_index") is not None)
    stop_before = first["page_index"] if first else None
    rat = rat_policy(cfg)
    if include_rat:
        print("warning: --include-rat is decided by page-kinds.json", file=sys.stderr)
    if record is None:
        record = page_kinds.load_current(mod, cfg, answers, path=page_kinds_path, data_dir=data_dir, answers_sha256=answers_sha256)
    elif not page_kinds.current(mod, record, rat, answers, answers_sha256=answers_sha256):
        record = None
    rat_pages = [rat_mod.decide(p, rat, record, answers) for p in mod.get("pages", [])
                 if page_kinds.is_candidate(p, rat)]
    decisions = {d["page_index"]: d for d in rat_pages}

    drafted = {a["item_id"]: a for a in (answers or {}).get("items", [])}
    # A subpage child is reached as a raw Backbone model, so its read_only is null even on
    # a page Lt has locked. inferred_locks() names the children a page-level lock covers.
    locks = boundaries.inferred_locks(mod)

    pages_out, skipped_pages = [], []
    for p in mod.get("pages", []):
        idx = p.get("page_index")
        if idx in report_idx:
            skipped_pages.append({"page_index": idx, "reason": "report-page"})
            continue
        if stop_before is not None and idx is not None and idx >= stop_before:
            skipped_pages.append({"page_index": idx, "reason": "at-or-past-first-boundary"})
            continue
        if p.get("policy_skip"):
            skipped_pages.append({"page_index": idx, "reason": "policy-skip",
                                  "detail": p["policy_skip"].get("reason")})
            continue
        rd = decisions.get(idx, {"decision": "not-rat"})
        if rd["decision"] not in {"not-rat", "fill-from-draft", "fill-from-sheet"}:
            skipped_pages.append({"page_index": idx, "reason": rd["decision"],
                                  "detail": rd.get("question", rd["decision"])})
            continue
        if rd.get("rat") and rd.get("kind") != "trat" and any(
                drafted.get(i["item_id"], {}).get("status") == "drafted" for i in p.get("items", [])):
            skipped_pages.append({"page_index": idx, "reason": "rat-irat-drafted"})
            continue

        items = []
        for it in p.get("items", []):
            if it["type"] in NOT_FILLABLE:
                continue
            a = drafted.get(it["item_id"])
            if answers and a is None:
                state = "no-draft"
            elif a and a.get("status") == "skipped":
                state = f"draft-skipped:{a.get('skip_reason', 'unspecified')}"
            elif a and a.get("status") == "blocked":
                state = "draft-blocked"
            elif a:
                state = "drafted"
            else:
                state = "answers-not-supplied"
            from_sheet = rd["decision"] == "fill-from-sheet" and a and (
                a.get("status") == "drafted" or a.get("status") == "skipped" and
                a.get("skip_reason") in {"rat-sheet", "trat-answers-unavailable"})
            if from_sheet:
                state = "rat-sheet"
            unmapped = it["type"] == "unmapped"
            # A deliberate skip by the answerer is not a target. `already-answered` matters
            # most: that item holds the student's own work.
            declined = state.startswith("draft-skipped:") or state == "draft-blocked"
            # read_only is tri-state and only True skips. None means the flag was not found;
            # the live probe settles it.
            read_only = it.get("read_only") is True
            lock = locks.get(it["item_id"])
            permanent = (it.get("degrade") or {}).get("class") == "permanent"
            # A mirror re-displays another panel's content and takes no write.
            mirror = bool(it.get("mirror_of"))
            entry = {
                "item_id": it["item_id"],
                # A target is (page_index, panel_key); panel_key alone is ambiguous.
                "target": {"page_index": idx, "panel_key": it["panel_key"]},
                "type": it["type"],
                "input_mode": it.get("input_mode"),
                "attempts": it.get("attempts"),
                "method": "manual-todo" if unmapped
                          else METHOD_FOR.get(it.get("automation"), "manual-todo"),
                "automation": it.get("automation"),
                "degrade": it.get("degrade"),
                # The capture never settles occupancy; the live page does.
                "occupancy": "must-ask" if it.get("answered") else "must-verify-live",
                "captured_answered": bool(it.get("answered")),
                "draft_state": state,
                "fillable": not unmapped and not declined and not read_only
                            and lock is None and not permanent and not mirror,
                "read_only": it.get("read_only"),
            }
            # Mapping types: the plan carries each label as text. A capture option_id counts
            # positions in an alphabetical list, which differs from Lt's live label order.
            # Without label_placements the draft states its mapping in the segments.
            if it["type"] in PLACEMENT_TYPES:
                pls = (a or {}).get("label_placements") or []
                if pls:
                    entry["placements_source"] = "label_placements"
                    entry["placements"] = [
                        {k: v for k, v in (("label", pl.get("label")), ("target", pl.get("target")),
                                           ("target_index", pl.get("target_index")),
                                           ("confidence", pl.get("confidence")),
                                           ("alternative", pl.get("alternative")))
                         if v is not None}
                        for pl in pls]
                elif state == "drafted":
                    entry["placements_source"] = "segments-prose"
                    entry["placements_note"] = (
                        f"drafted at answers schema_version "
                        f"{(answers or {}).get('schema_version', 1)} with no label_placements; the "
                        f"mapping is in `segments` as prose and must be read out of it before "
                        f"writing. A version-2 draft states the mapping directly.")
                else:
                    entry["placements_source"] = None
            if from_sheet:
                entry["answer_source"] = "rat-sheet"
            _skip_entries(it, p, idx, entry, state, lock)
            items.append(entry)
        if items:
            pages_out.append({"page_index": idx, "page_no": p.get("page_no"),
                              "url": p.get("url"), "title": p.get("title"), "items": items})

    never_routes = [dict(n) for n in bm["never_navigated"]]
    never = never_routes + [{"page_index": i, "reason": "report page"} for i in report_idx]
    # Every module page, so check-url can place a tab that rests on a page with no items.
    flow_pages = [{"page_index": p.get("page_index"), "page_no": p.get("page_no"), "url": p.get("url"),
                   "is_checkpoint": bool(p.get("is_checkpoint")),
                   "is_report_page": bool(p.get("is_report_page"))} for p in mod.get("pages", [])]
    checkpoint_pages = sorted({p["page_index"] for p in flow_pages
                               if p["is_checkpoint"] and isinstance(p["page_index"], int)}
                              | {i for i in ((mod.get("commit_boundaries") or {}).get("checkpoint_pages") or [])
                                 if isinstance(i, int)})
    result = {
        "module_id": mod.get("module_id"),
        "origin": origin_of(mod.get("source_url")),
        "instance": mod.get("instance"),
        "course_id": mod.get("course_id"),
        "run_id": mod.get("run_id"),
        "boundary_map": bm,
        "stop_before_page": stop_before,
        "first_boundary": first,
        "never_navigated": never,
        "report_pages": report_idx,
        "checkpoint_pages": checkpoint_pages,
        "flow_pages": flow_pages,
        "rat_policy": dict({k: rat[k] for k in ("irat", "trat")}, record_sha256=page_kinds.record_hash(record)),
        "rat_pages": rat_pages,
        "pages": pages_out,
        "skipped_pages": skipped_pages,
        "fillable_items": sum(1 for p in pages_out for i in p["items"] if i["fillable"]),
        # todo_items: gaps a person finishes by hand. declined_items: drafts the answering
        # step refused on purpose. The two counts stay separate.
        "todo_items": sum(1 for p in pages_out for i in p["items"] if i.get("todo")),
        "declined_items": sum(1 for p in pages_out for i in p["items"]
                              if (i.get("skip") or {}).get("class") == "draft-declined"),
        "placements": {
            "items": sum(1 for p in pages_out for i in p["items"]
                         if i.get("placements_source") is not None),
            "from_label_placements": sum(1 for p in pages_out for i in p["items"]
                                         if i.get("placements_source") == "label_placements"),
            "from_prose": sum(1 for p in pages_out for i in p["items"]
                              if i.get("placements_source") == "segments-prose"),
            "labels_to_place": sum(len(i.get("placements") or [])
                                   for p in pages_out for i in p["items"]),
        },
    }

    if cfg is not None and cfg.get('fill.check_answer.enabled',False) is True:
        from ltcore import check_answer as ca
        statuses={r['page_index']:r for r in page_kinds.page_status(mod,record,policy=rat)}
        candidate_items=[]
        for page in mod['pages']:
            for item in page['items']:
                reason=ca.eligibility(item,page,result,statuses[page['page_index']],written=True,verified=True,calibrated=True)
                if reason is None:candidate_items.append({'item_id':item['item_id'],'page':page['page_index']})
        result['check_answer']={'items':candidate_items,'requires_written_verified_calibrated':True}
    return result


def render(pl: dict) -> None:
    print(f"module {pl['module_id']}")
    if pl.get('check_answer'):
        rows=pl['check_answer']['items']
        print('Check Answer: '+str(len(rows))+' items on pages '+', '.join(map(str,sorted({r['page'] for r in rows})))+'; written, verified drafts and origin calibration required')
    doors = pl["boundary_map"]["doors"]
    stops = [d for d in doors if d["severity"] == "stop"]
    peritem = [d for d in doors if d["severity"] == "per-item"]

    print("\ncommit-boundary map: ONE-WAY DOORS (these stop the run)")
    if not stops:
        print("  (none in the capture)")
    for d in stops:
        loc = f"page {d['page_index']}" + (f" item {d['item_id']}" if d["item_id"] else "")
        print(f"  {d['class']:<20} {loc}")
        print(f"  {'':<20} {d['detail']}")

    print("\nper-item boundaries: never written without an approval or a write path; "
          "the run continues past them")
    if not peritem:
        print("  (none)")
    for d in peritem:
        loc = f"page {d['page_index']}" + (f" item {d['item_id']}" if d["item_id"] else "")
        print(f"  {d['class']:<20} {loc}")
        print(f"  {'':<20} {d['detail']}")

    passed = pl["boundary_map"].get("checkpoints_passed") or []
    singles = pl["boundary_map"].get("single_attempts_passed") or []
    if passed or singles:
        print("\nALREADY COMMITTED: one-way doors the run is already through; they stop nothing")
        for c in passed:
            print(f"  checkpoint index {c.get('index')} was committed; Lt has removed it from the "
                  f"flow, so it appears in no page and stops nothing")
        for s in singles:
            print(f"  single-attempt item {s['item_id']} on page {s['page_index']} was submitted; "
                  f"the capture reads it read_only and answered, so it is no door and is never "
                  f"written")
            print(f"  {'':<20} For page {s['page_index']} and subsequent pages, require manual "
                  f"approval, fresh read_only evidence, and a not-attempted entry. "
                  f"Keep the submitted panel untouched.")
    if not passed and not pl["boundary_map"].get("checkpoints_inventory_measured"):
        print("\ncheckpoint inventory NOT measured in this capture: an empty door list is not")
        print("  evidence of absence here; capture again with the current capture skill")

    print("\nnever navigated")
    for n in pl["boundary_map"]["never_navigated"]:
        print(f"  {n['route']:<20} {n['reason']}")
    for i in pl.get("report_pages") or []:
        print(f"  {'page ' + str(i):<20} report page")

    fb = pl["first_boundary"]
    print()
    if fb:
        print(f"STOP BEFORE PAGE {pl['stop_before_page']}  ({fb['class']}"
              + (f", {fb['item_id']}" if fb["item_id"] else "") + ")")
    else:
        print("no boundary before the end of the module")

    print(f"\nfillable: {pl['fillable_items']} item(s) across {len(pl['pages'])} page(s)"
          f"   per-item TODOs: {pl['todo_items']}"
          f"   declined by the answerer: {pl.get('declined_items', 0)}")
    pc = pl.get("placements") or {}
    if pc.get("items"):
        print(f"placement items: {pc['items']}   mapping stated as label_placements: "
              f"{pc['from_label_placements']} ({pc['labels_to_place']} label(s))   "
              f"mapping in prose only: {pc['from_prose']}")
    for p in pl["pages"]:
        print(f"  page {p['page_index']}  {p['title']!r}")
        for it in p["items"]:
            mark = "  " if it["fillable"] else "! "
            print(f"  {mark}{it['item_id']:<12} {it['type']:<22} {it['method']:<12} "
                  f"{it['occupancy']:<18} {it['draft_state']}")
            src = it.get("placements_source")
            if src == "label_placements":
                pls = it.get("placements") or []
                print(f"      PLACEMENTS ({len(pls)}): resolve each label by TEXT against the "
                      f"live pool; option_id is meaningless here")
                for lp in pls:
                    conf = lp.get("confidence")
                    tag = f" [{conf}]" if conf else ""
                    ti = lp.get("target_index")
                    where = f" (bin {ti})" if ti is not None else ""
                    print(f"        {lp.get('label', '?')!r} -> {lp.get('target', '?')}{where}{tag}")
                    if lp.get("alternative"):
                        print(f"          alternative: {lp['alternative']}")
            elif src == "segments-prose":
                print(f"      PLACEMENTS: not stated as a field; {it['placements_note']}")
            if not it["fillable"]:
                # Not every skip is a TODO: a read-only panel carries none.
                todo = it.get("todo")
                if todo:
                    print(f"      TODO: {todo['blocker']}")
                else:
                    sk = it.get("skip") or {}
                    print(f"      SKIP: {sk.get('class', 'unclassified')}: "
                          f"{sk.get('skip_reason', 'no reason recorded')}")
                    if sk.get("class") in ("read-only-inferred", "lock-unknown"):
                        print(f"        {sk['detail']}")
    if pl["skipped_pages"]:
        print("\nskipped pages")
        for s in pl["skipped_pages"]:
            extra = f": {s['detail']}" if s.get("detail") else ""
            print(f"  page {s['page_index']:<4} {s['reason']}{extra}")


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module")
    ap.add_argument("--answers")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    ap.add_argument("--out", help="also write the JSON plan here (for ltjs.py check-url --plan)")
    ap.add_argument("--include-rat", action="store_true",
                    help="deprecated; decisions come from page-kinds.json")
    ap.add_argument("--data-dir")
    ap.add_argument("--page-kinds")
    ap.add_argument("--config", help="config file (default: the lt-suite lookup order)")
    args = ap.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        return fatal(str(e))
    try:
        mod = load_json(args.module)
    except (OSError, json.JSONDecodeError) as e:
        return fatal(str(e))
    if mod.get("schema_version") != 1 or "pages" not in mod:
        return fatal(f"{args.module} does not look like a module.json")

    answers = None
    if args.answers:
        try:
            answers = load_json(args.answers)
        except (OSError, json.JSONDecodeError) as e:
            return fatal(str(e))
        if answers.get("module_id") != mod.get("module_id"):
            return fatal(f"answers.json is for module {answers.get('module_id')!r}, "
                         f"module.json is {mod.get('module_id')!r}")

    pl = plan(mod, answers, args.include_rat, cfg, data_dir=args.data_dir, page_kinds_path=args.page_kinds, answers_sha256=hashlib.sha256(Path(args.answers).read_bytes()).hexdigest() if args.answers else None)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pl, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(pl, indent=1, ensure_ascii=False))
    else:
        render(pl)
        if args.out:
            print(f"\nplan written to {args.out}")
    return 0 if pl["fillable_items"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
