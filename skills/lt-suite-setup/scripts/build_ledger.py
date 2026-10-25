#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Aggregate the selector events of every run record into <data>/state/ledger.json.

A fill report records one event per selector it tried: which selector, which item type,
and whether it hit. Export projects those events into the run record. Nothing else merges
them, so without this ledger the knowledge that a selector is dead is re-derived by hand
each time.

A re-export is not new evidence. Exporting a module twice writes the same event array
twice. Records whose event array is identical within one module count as ONE independent
observation, and the duplication is reported rather than hidden: a count of what was
found is not a measurement until something independent confirms it.

The ledger also audits itself against one rule: a panel is addressed as (page index,
panel key), never by a CSS class alone. A class alone can match the wrong panel and
write into it, so every selector is scored for that shape and the summary names them.

    python3 build_ledger.py                      # build and write <data>/state/ledger.json
    python3 build_ledger.py --dry-run            # report, write nothing
    python3 build_ledger.py --fill-report f.json # fold one fill report in
    python3 build_ledger.py --json               # print the ledger

Exit codes: 0 ok, 3 no runs folder.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
for _base in (HERE / "_shared", HERE.parent.parent.parent / "shared"):
    if (_base / "ltcore").is_dir():
        sys.path.insert(0, str(_base))
        break

import ltcore  # noqa: E402

ltcore.bootstrap([])

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from collections import defaultdict  # noqa: E402

from ltcore import config, store  # noqa: E402

# A selector that is only classes, ids and tags, with nothing narrowing it to ONE panel.
# The text in brackets after the selector is prose: the selector proper is judged on what
# comes before the bracket, and disambiguation on the whole string, because the prose is
# where a run records HOW it narrowed the match.
BARE_CSS = re.compile(r"^[.#a-zA-Z\[][\w.#>\s:\[\]=\"'-]*$")
DISAMBIGUATED = ("panelKey", "currentInputModel", "identity", "nth", "index", "filtered")


def is_bare_css(selector):
    core = selector.split("(")[0].strip()
    if not core or not BARE_CSS.match(core):
        return False
    return not any(t in selector for t in DISAMBIGUATED)


def collect(st, extra_reports):
    """Every selector event keyed by (selector, item_type), plus a census."""
    entries = {}
    seen_arrays = defaultdict(set)
    census = {"records": 0, "records_with_events": 0, "duplicate_records": 0,
              "raw_events": 0, "independent_events": 0, "modules": set(), "fill_reports": 0}

    def fold(events, source, module_id, independent):
        for e in events:
            key = (e["selector"], e["item_type"])
            ent = entries.setdefault(key, {
                "selector": e["selector"], "item_type": e["item_type"], "hits": 0, "misses": 0,
                "first_seen": None, "last_seen": None, "replaced_by": None, "death_date": None,
                "sources": [], "modules": set()})
            if source not in ent["sources"]:
                ent["sources"].append(source)
            ent["modules"].add(module_id)
            if independent:
                ent["hits" if e["outcome"] == "hit" else "misses"] += 1
            # recorded_at exists only on the fill-report side; run records do not carry
            # it. An absent timestamp narrows the window rather than inventing a point.
            ts = e.get("recorded_at")
            if ts:
                ent["first_seen"] = min(ent["first_seen"] or ts, ts)
                ent["last_seen"] = max(ent["last_seen"] or ts, ts)
            if e.get("replaced_by"):
                ent["replaced_by"] = e["replaced_by"]
            if e.get("death_date"):
                ent["death_date"] = e["death_date"]

    for path, rec in st.iter_runs():
        census["records"] += 1
        census["modules"].add(rec["module_id"])
        events = rec.get("selector_events") or []
        if not events:
            continue
        census["records_with_events"] += 1
        census["raw_events"] += len(events)
        fingerprint = json.dumps(events, sort_keys=True)
        independent = fingerprint not in seen_arrays[rec["module_id"]]
        if independent:
            seen_arrays[rec["module_id"]].add(fingerprint)
            census["independent_events"] += len(events)
        else:
            census["duplicate_records"] += 1
        fold(events, "%s/%s" % (path.parent.name, path.name), rec["module_id"], independent)

    for fr_path in extra_reports:
        fr = json.loads(Path(fr_path).read_text(encoding="utf-8"))
        events = fr.get("selector_events") or []
        if not events:
            continue
        census["fill_reports"] += 1
        census["modules"].add(fr["module_id"])
        fingerprint = json.dumps([{k: e[k] for k in ("selector", "item_type", "outcome")} for e in events],
                                 sort_keys=True)
        # A fill report is the ORIGINAL of the events a run record projects. Folding it in
        # adds timestamps to entries that already exist; it adds observations only when
        # no run record carried the same events.
        independent = fingerprint not in seen_arrays[fr["module_id"]]
        if independent:
            seen_arrays[fr["module_id"]].add(fingerprint)
            census["independent_events"] += len(events)
        fold(events, Path(fr_path).name, fr["module_id"], independent)

    census["modules"] = sorted(census["modules"])
    return entries, census


def verdict(ent):
    """What a run should do with this selector next time."""
    if ent["death_date"]:
        return "dead"
    if ent["hits"] and not ent["misses"]:
        return "live"
    if ent["hits"] and ent["misses"]:
        return "flaky"
    return "failed"


def build(st, extra_reports):
    entries, census = collect(st, extra_reports)
    out = []
    for ent in sorted(entries.values(), key=lambda e: (e["item_type"], e["selector"])):
        ent["modules"] = sorted(ent["modules"])
        ent["verdict"] = verdict(ent)
        ent["bare_css"] = is_bare_css(ent["selector"])
        out.append(ent)
    rails = [e for e in out if e["bare_css"]]
    return {
        "schema_version": 1,
        "generated_from": {
            "records": census["records"],
            "records_carrying_events": census["records_with_events"],
            "duplicate_records_collapsed": census["duplicate_records"],
            "fill_reports_folded": census["fill_reports"],
            "modules": census["modules"],
            "raw_events": census["raw_events"],
            "independent_events": census["independent_events"],
            "note": ("raw_events counts every event in every record; independent_events collapses "
                     "records whose event array is byte-identical within one module, because a "
                     "re-export re-reports evidence rather than adding any. Read the second number."),
        },
        "summary": {
            "selectors": len(out),
            "live": sum(1 for e in out if e["verdict"] == "live"),
            "flaky": sum(1 for e in out if e["verdict"] == "flaky"),
            "failed": sum(1 for e in out if e["verdict"] == "failed"),
            "dead": sum(1 for e in out if e["verdict"] == "dead"),
            "bare_css_violations": len(rails),
            "bare_css_hits": sum(1 for e in rails if e["hits"]),
            "bare_css_misses": sum(1 for e in rails if e["misses"]),
            "rail": ("A panel is addressed as (page_index, panel_key), never by a CSS class alone. "
                     "These entries are that rule tested against a live page. A bare-CSS selector "
                     "that HIT is not a counter-example: it matched the panel that happened to be "
                     "first, which is the failure mode, not the absence of one."),
        },
        "entries": out,
    }


def main(argv=None):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config file (else LT_SUITE_CONFIG, ./lt-suite.toml, the platform file)")
    ap.add_argument("--data-dir", help="data folder")
    ap.add_argument("--dry-run", action="store_true", help="report; write nothing")
    ap.add_argument("--fill-report", action="append", default=[],
                    help="a fill-report.json to fold in (it carries recorded_at; run records do not)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = config.load(a.config, validate=False)
    except config.ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 3
    st = store.Store(cfg, data_dir=a.data_dir)
    if not st.runs_dir.is_dir():
        print("FATAL: no runs folder at %s" % st.runs_dir, file=sys.stderr)
        return 3
    led = build(st, a.fill_report)
    if a.json:
        print(json.dumps(led, indent=1))
    else:
        g, s = led["generated_from"], led["summary"]
        print("runs        %d records, %d carrying events, %d collapsed as re-exports"
              % (g["records"], g["records_carrying_events"], g["duplicate_records_collapsed"]))
        print("modules     %s" % ", ".join(g["modules"]))
        print("events      %d raw -> %d independent" % (g["raw_events"], g["independent_events"]))
        print("selectors   %d  live %d  flaky %d  failed %d  dead %d"
              % (s["selectors"], s["live"], s["flaky"], s["failed"], s["dead"]))
        for e in led["entries"]:
            flag = "  <- bare CSS" if e["bare_css"] else ""
            print("  [%6s] %-16s %s%s" % (e["verdict"], e["item_type"], e["selector"], flag))
        if s["bare_css_violations"]:
            print("\n%d bare-CSS selector(s) on record (%d hit, %d missed). %s"
                  % (s["bare_css_violations"], s["bare_css_hits"], s["bare_css_misses"], s["rail"]))
    if not a.dry_run:
        p = st.ledger_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(led, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("\nledger      %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
