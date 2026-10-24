#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Classify local export diffs and record evidence for reviewed lexicon proposals.

Export retains a diff when a manually edited page prevents replacement. Review uses
that diff to record the user's corrections against the generated draft. Only entries
marked steered_run_wrong can support a proposal. Each candidate also identifies its
owning skill, a measurable benchmark cell, and at least 2 independent runs by default.

Proposal files go into <data>/lexicon/proposals/. The scripts and instructions shipped
with an installed skill receive no writes. A reviewer supplies the proposed rule,
approves any resulting change, and records benchmark results after applying it.

    python3 improve.py classify --diff page.md.diff
    python3 improve.py ingest --run <run.json> --skill lt-module-answer --class factual \\
        --diff page.md.diff --steered-run-wrong --item-id p1-1
    python3 improve.py propose                 # writes <data>/lexicon/proposals/*.md
    python3 improve.py propose --dry-run       # prints only
    python3 improve.py applied --list

Exit codes: 0 ok, 2 refused input, 3 missing data or option.
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
import datetime as dt  # noqa: E402
import importlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from collections import defaultdict  # noqa: E402

from ltcore import config, store  # noqa: E402

CLASSES = ("factual", "provenance", "style", "scope", "diagram")
SKILLS = ("lt-module-capture", "lt-module-answer", "lt-module-fill", "lt-module-export",
          "mechanistic-diagram")

# Which skill owns a class of defect. style and scope are absent on purpose: a wording or
# scope change can belong to the drafter or to the exporter, and guessing would put
# evidence against a file that never steered anything.
OWNER = {"factual": "lt-module-answer", "provenance": "lt-module-answer",
         "diagram": "mechanistic-diagram"}

DEFAULT_TAG = r"\[T[1-9]:[^\]]*\]"
# Export properties that change on every run by construction. Export already strips
# them before deciding whether a file changed, but it writes the raw diff, so the line
# is still in the file read here. The diff is for people; the evidence must be clean.
VOLATILE = re.compile(r"^\s*lt-exported::")
# Export writes `item:: <id>` under each drafted item's heading, so the rendered page
# carries the item boundaries the diff does not.
ITEM_MARK = re.compile(r"^\s*item:: (\S+)\s*$")
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
OLD_PATH = re.compile(r"^--- (.+?) \(on disk\)\s*$", re.M)


def tag_regex(cfg=None):
    """Regex for provenance tags, built from the tier tag templates in the config
    (`[T1:{source}]` gives `\\[T1:[^\\]]*\\]`). A line that changes only these tags
    changed the sourcing, not the claim."""
    parts = []
    if cfg is not None:
        for t in cfg.get("tiers.tier", []) or []:
            tpl = t.get("tag_template", "") if isinstance(t, dict) else ""
            prefix = tpl.split("{", 1)[0]
            if prefix and tpl.endswith("]") and prefix.startswith("["):
                parts.append(re.escape(prefix) + r"[^\]]*\]")
    return re.compile("|".join(parts) if parts else DEFAULT_TAG)


def changed_lines(diff_text):
    """The changed lines, minus the ones that change on every run.

    Export diffs the file ON DISK against the draft THIS RUN would write, so the `-`
    side is the person's edited text and the `+` side is the regenerated draft. The
    person's words are in `before`."""
    minus = [ln[1:] for ln in diff_text.splitlines()
             if ln.startswith("-") and not ln.startswith("---") and not VOLATILE.match(ln[1:])]
    plus = [ln[1:] for ln in diff_text.splitlines()
            if ln.startswith("+") and not ln.startswith("+++") and not VOLATILE.match(ln[1:])]
    return minus, plus


def page_item_map(path):
    """1-based line number -> the item_id whose block that line falls in, built from the
    page ON DISK (the side of the diff the person edited and whose line numbers the hunk
    headers count)."""
    out, cur = {}, None
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        m = ITEM_MARK.match(line)
        if m:
            cur = m.group(1)
        out[n] = cur
    return out


def slice_by_item(diff_text, item_id, page):
    """The changed lines inside ONE item, or None when the page cannot be read.

    A unified diff cannot be sliced alone: three lines of context do not reach the
    `item::` property above the first segment. The page on disk holds it and the hunk
    headers count its lines, so the attribution is arithmetic. `-` lines and context
    advance the old-file counter; `+` lines take the item at the current old-file
    position, which is the item they replace text inside."""
    try:
        line_item = page_item_map(page)
    except OSError:
        return None
    minus, plus = [], []
    old_n = None
    for line in diff_text.splitlines():
        m = HUNK.match(line)
        if m:
            old_n = int(m.group(1))
            continue
        if old_n is None or line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            if line_item.get(old_n) == item_id and not VOLATILE.match(line[1:]):
                minus.append(line[1:])
            old_n += 1
        elif line.startswith("+"):
            if line_item.get(old_n) == item_id and not VOLATILE.match(line[1:]):
                plus.append(line[1:])
        else:
            old_n += 1
    return minus, plus


def items_touched(diff_text, page):
    """Item ids the diff has changed lines inside, for error messages."""
    line_item = page_item_map(page)
    out, old_n = set(), None
    for line in diff_text.splitlines():
        m = HUNK.match(line)
        if m:
            old_n = int(m.group(1))
            continue
        if old_n is None or line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            if not VOLATILE.match(line[1:]) and line_item.get(old_n):
                out.add(line_item[old_n])
            old_n += 1
        elif line.startswith("+"):
            if not VOLATILE.match(line[1:]) and line_item.get(old_n):
                out.add(line_item[old_n])
        else:
            old_n += 1
    return out


def norm(s):
    """Ignore spacing and prose punctuation while retaining mathematical symbols."""
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", "", s)
    return re.sub(r"[\s,;]+", " ", s).strip().lower()


def classify(diff_text, tag=None):
    """Recognise tag-only and punctuation-only edits. Leave claim judgements to
    the reviewer: textual changes alone establish no factual correction."""
    tag = tag or re.compile(DEFAULT_TAG)
    minus, plus = changed_lines(diff_text)
    if not minus and not plus:
        return {"suggested": None, "confidence": "none", "why": "no changed lines in the diff"}
    stripped_m = [norm(tag.sub("", ln)) for ln in minus]
    stripped_p = [norm(tag.sub("", ln)) for ln in plus]
    tags_m = sorted(t for ln in minus for t in tag.findall(ln))
    tags_p = sorted(t for ln in plus for t in tag.findall(ln))
    if stripped_m == stripped_p and tags_m != tags_p:
        return {"suggested": "provenance", "confidence": "proved",
                "why": "the prose is unchanged once provenance tags are removed; only the tags differ"}
    if [norm(ln) for ln in minus] == [norm(ln) for ln in plus] and minus != plus:
        return {"suggested": "style", "confidence": "proved",
                "why": "the lines are identical after normalising whitespace and punctuation"}
    return {"suggested": None, "confidence": "none",
            "why": ("the prose itself changed. factual, scope and diagram cannot be told "
                    "apart from the text alone: name the class with --class")}


# ------------------------------------------------------------------- run records

def resolve_run(arg, st):
    """A run record path from a file path or a module id."""
    p = Path(arg)
    if p.is_file():
        return p
    try:
        q = st.run_path(arg)
    except ValueError:
        q = None
    if q is not None and q.is_file():
        return q
    raise FileNotFoundError("no run record at %s and none for module id %r under %s"
                            % (arg, arg, st.runs_dir))


def validate(path):
    """Check a run record against run.schema.json after every write. Returns the exit
    code of the validator; 0 when the validator is not installed or cannot run (the
    record was still written)."""
    try:
        importlib.import_module("ltcore.validate")
    except ImportError:
        print("note: ltcore.validate is not part of this install, so the schema check was skipped")
        return 0
    env = dict(os.environ)
    root = str(Path(ltcore.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    p = subprocess.run([sys.executable, "-m", "ltcore.validate", str(path), "--schema", "run", "--quiet"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)
    if p.returncode == 3:
        print("note: the schema check was skipped (%s)" % (p.stderr.strip().splitlines() or ["validator unavailable"])[-1])
        return 0
    if p.returncode != 0:
        print(p.stdout + p.stderr, file=sys.stderr)
    return p.returncode


def ingest(a, st, cfg):
    rec_path = resolve_run(a.run, st)
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    diff_text = Path(a.diff).read_text(encoding="utf-8") if a.diff else ""
    auto = classify(diff_text, tag_regex(cfg)) if diff_text else {"suggested": None}
    klass = a.klass or auto.get("suggested")
    if klass is None:
        print("FATAL: no class. The diff does not prove one, so it must be named with "
              "--class {%s}.\n       %s" % (",".join(CLASSES), auto.get("why", "")), file=sys.stderr)
        return 2
    if a.klass and auto.get("confidence") == "proved" and auto["suggested"] != a.klass:
        # Keep a visible receipt when the reviewer's judgement overrides the
        # mechanical classification; later reviewers need both readings.
        print("note: --class %s overrides a proved classification of %s (%s)"
              % (a.klass, auto["suggested"], auto["why"]))
    minus, plus = changed_lines(diff_text)
    page = Path(a.page) if a.page else None
    if page is None and diff_text:
        m = OLD_PATH.search(diff_text)
        if m:
            page = Path(m.group(1))
    sliced = slice_by_item(diff_text, a.item_id, page) if (diff_text and page) else None
    if sliced is None:
        if diff_text:
            print("note: could not read the exported page (%s), so this entry carries the WHOLE "
                  "diff rather than %s's share of it. Pass --page <module page on disk> to slice it."
                  % (page, a.item_id))
    else:
        minus, plus = sliced
        if not minus and not plus:
            touched = sorted(items_touched(diff_text, page))
            print("FATAL: the diff contains no changed line inside %s. Recording it would file "
                  "someone else's edit as this item's evidence.\n       items the diff does touch: %s"
                  % (a.item_id, ", ".join(touched) or "(none)"), file=sys.stderr)
            return 2
    entry = {"item_id": a.item_id, "class": klass, "steered_run_wrong": bool(a.steered_run_wrong)}
    if minus:
        entry["before"] = "\n".join(minus)[:2000]
    if plus:
        entry["after"] = "\n".join(plus)[:2000]
    note = a.note or ""
    skill = a.skill or OWNER.get(klass)
    if a.steered_run_wrong and not skill:
        print("FATAL: --steered-run-wrong needs --skill. A '%s' change has no single owner, and "
              "evidence filed against the wrong file is worse than none." % klass, file=sys.stderr)
        return 2
    if skill:
        note = (note + " [skill:%s]" % skill).strip()
    if a.diff:
        note = (note + " [diff:%s]" % Path(a.diff).name).strip()
    if note:
        entry["note"] = note
    rec.setdefault("diff_classification", []).append(entry)
    if a.dry_run:
        print(json.dumps(entry, indent=1))
        print("\ndry run: %s not written" % rec_path)
        return 0
    rec_path.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    rc = validate(rec_path)
    print("%s: %d classified change(s); schema %s"
          % (rec_path, len(rec["diff_classification"]), "ok" if rc == 0 else "FAILED"))
    return rc


def gather(st):
    """Evidence keyed by (skill, class), and a census of what was looked at."""
    ev = defaultdict(list)
    census = {"records": 0, "records_with_diffs": 0, "entries": 0, "narrow": 0}
    for path, rec in st.iter_runs():
        census["records"] += 1
        entries = rec.get("diff_classification") or []
        if entries:
            census["records_with_diffs"] += 1
        for e in entries:
            census["entries"] += 1
            if not e.get("steered_run_wrong"):
                continue
            census["narrow"] += 1
            m = re.search(r"\[skill:([\w-]+)\]", e.get("note", "") or "")
            skill = m.group(1) if m else OWNER.get(e["class"])
            if skill is None:
                continue
            ev[(skill, e["class"])].append(
                {"run": path.parent.name, "run_ts": rec.get("run_ts"),
                 "module_id": rec.get("module_id"), "item_id": e.get("item_id"),
                 "before": e.get("before", ""), "after": e.get("after", ""),
                 "note": e.get("note", "")})
    return ev, census


def benchmark_cell(skill, cfg):
    """The benchmark cell a proposal for ``skill`` must move: `improvement.benchmark_cells`
    entries written as `<skill>: <cell>`, else the skill's eval pass rate."""
    if skill not in SKILLS:
        return None
    for entry in (cfg.get("improvement.benchmark_cells", []) if cfg is not None else []):
        name, sep, cell = str(entry).partition(":")
        if sep and name.strip() == skill and cell.strip():
            return cell.strip()
    return "evals/%s/evals.json pass rate with the skill" % skill


# ------------------------------------------------------------------ applied ledger

def applied_file(st, override):
    return Path(override) if override else st.applied_path


def load_applied(st, override=None):
    fp = applied_file(st, override)
    if not fp.is_file():
        return []
    return json.loads(fp.read_text(encoding="utf-8")).get("applied", [])


def applied_key(rec):
    return (rec["skill"], rec["class"])


def cmd_applied(a, st):
    """Record that a proposal was approved and applied, or list what already was. This is
    the only thing that stops `propose` re-emitting a closed proposal forever. It writes
    no skill file and proposes nothing: it records a human decision that already happened."""
    fp = applied_file(st, a.applied_file)
    entries = load_applied(st, a.applied_file)
    if a.list or not a.skill:
        if not entries:
            print("nothing recorded as applied. `propose` will emit every corroborated "
                  "proposal, including any already applied to a skill.")
            return 0
        for e in entries:
            print("%s  (%s)  applied %s" % (e["skill"], e["class"], e["date"]))
            print("  items    : %s" % ", ".join(e["items"]))
            print("  decision : %s" % e.get("decision", "-"))
            print("  measured : %s" % e.get("measured", "-"))
        return 0
    rec = {"skill": a.skill, "class": a.klass, "items": sorted(set(a.items or [])),
           "date": a.date, "decision": a.decision, "measured": a.measured}
    for missing in ("decision", "measured"):
        if not rec[missing]:
            print("FATAL: --%s is required. A proposal is applied only once a person approved it "
                  "and the before and after numbers were shown; recording it without saying where "
                  "that is written down defeats the point." % missing, file=sys.stderr)
            return 3
    if not rec["class"]:
        print("FATAL: --class is required with --skill.", file=sys.stderr)
        return 3
    if any(applied_key(e) == applied_key(rec) for e in entries):
        print("already recorded: %s / %s" % (rec["skill"], rec["class"]))
        return 0
    entries.append(rec)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps({"schema_version": 1, "applied": entries}, indent=1,
                             ensure_ascii=False) + "\n", encoding="utf-8")
    print("recorded %s / %s as applied (%d items) -> %s" % (rec["skill"], rec["class"],
                                                           len(rec["items"]), fp))
    return 0


# ----------------------------------------------------------------------- propose

def proposal_markdown(p):
    """A proposal file: evidence the person reads, and a blank for the rule. The script
    never writes the rule: a rule composed from evidence by code is a self-edit."""
    today = dt.date.today()
    lines = [
        "---",
        "name: proposal-%s-%s" % (p["skill"], p["class"]),
        "description: Proposed lexicon change for %s from %d runs of %s edits" % (p["skill"], len(p["runs"]), p["class"]),
        "review_by: %s" % (today + dt.timedelta(days=365)).isoformat(),
        "evidence: []",
        "status: proposed",
        "---",
        "",
        "# Proposal: %s, %s edits" % (p["skill"], p["class"]),
        "",
        "This file is a proposal, not an active lexicon entry. Nothing reads it until a person",
        "moves it up one folder, writes the rule, cites evidence from docs or tests, and sets",
        "`status: active`. The skills are never edited by this script.",
        "",
        "- justified by: %s" % ", ".join(p["runs"]),
        "- items: %s" % ", ".join(sorted(set(p["items"]))),
        "- benchmark cell that must move: %s" % p["benchmark_cell"],
        "- next: approve, apply the edit by hand, re-run the evals, show the before and after. "
        "A change that moves no number is rejected.",
        "",
        "## Proposed rule",
        "",
        "(One sentence that would have prevented every edit below. Write it after reading them.)",
        "",
        "## Evidence",
        "",
    ]
    for e in p["evidence"]:
        lines.append("### %s, item %s" % (e["run"], e["item_id"]))
        lines.append("")
        if e.get("before"):
            lines.append("The person's text:")
            lines.append("")
            lines.extend("    " + ln for ln in e["before"].splitlines())
            lines.append("")
        if e.get("after"):
            lines.append("The regenerated draft:")
            lines.append("")
            lines.extend("    " + ln for ln in e["after"].splitlines())
            lines.append("")
    return "\n".join(lines) + "\n"


def propose(a, st, cfg):
    ev, census = gather(st)
    already = {applied_key(e): e for e in load_applied(st, a.applied_file)}
    print("evidence    %d run records, %d carrying review diffs, %d classified change(s), "
          "%d marked steered_run_wrong" % (census["records"], census["records_with_diffs"],
                                           census["entries"], census["narrow"]))
    proposals = []
    for (skill, klass), items in sorted(ev.items()):
        runs = sorted({i["run"] for i in items})
        if len(runs) < a.min_runs:
            print("  held   %s / %s: %d run(s), needs %d. One run is an anecdote; the loop "
                  "accumulates over runs." % (skill, klass, len(runs), a.min_runs))
            continue
        if (skill, klass) in already and not a.show_applied:
            rec = already[(skill, klass)]
            print("  closed %s / %s: applied %s (%s). The evidence stays in the run records "
                  "(they are append-only), so this is the applied ledger, not a filter on the "
                  "evidence. --show-applied lists it anyway." % (skill, klass, rec["date"], rec["decision"]))
            continue
        cell = benchmark_cell(skill, cfg)
        if not cell:
            print("  reject %s / %s: no benchmark cell is defined for this skill, so the proposal "
                  "could not be shown to have worked." % (skill, klass))
            continue
        proposals.append({"skill": skill, "class": klass, "runs": runs,
                          "items": [i["item_id"] for i in items], "benchmark_cell": cell,
                          "evidence": items})
    if not proposals:
        closed = [k for k in ev if k in already]
        if closed and not a.show_applied:
            print("\nNo OUTSTANDING proposal. %d corroborated pair(s) are already applied and "
                  "recorded (see `applied --list`). Do not re-apply them." % len(closed))
        else:
            print("\nNo proposal. That is the correct output when the loop has no evidence: a skill "
                  "edit is allowed only on the difference between a person's edit and the draft, and "
                  "a run that went fine changes nothing. Collect review diffs with `ingest`.")
        return 0
    print()
    written = []
    out_dir = st.lexicon_dir / "proposals"
    for p in proposals:
        print("PROPOSAL  %s  (%s)" % (p["skill"], p["class"]))
        print("  justified by : %s" % ", ".join(p["runs"]))
        print("  items        : %s" % ", ".join(sorted(set(p["items"]))))
        print("  must move    : %s" % p["benchmark_cell"])
        print("  next         : a person approves, the edit is applied by hand, then the eval set is")
        print("                 re-run and the before and after numbers shown. A change that moves")
        print("                 no number is rejected.")
        if not a.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / ("%s--%s.md" % (p["skill"], p["class"]))
            target.write_text(proposal_markdown(p), encoding="utf-8")
            written.append(target)
            print("  file         : %s" % target)
        print()
    if a.json:
        print(json.dumps(proposals, indent=1))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config file (else LT_SUITE_CONFIG, ./lt-suite.toml, the platform file)")
    ap.add_argument("--data-dir", help="data folder (runs, lexicon proposals, state)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify", help="what the diff text itself proves")
    c.add_argument("--diff", required=True)
    c.add_argument("--item-id", help="classify only this item's share of the diff")
    c.add_argument("--page", help="the exported page ON DISK, for slicing by item (default: the "
                   "path in the diff header)")

    i = sub.add_parser("ingest", help="record one classified change into a run record")
    i.add_argument("--run", required=True, help="a run.json path or a module id")
    i.add_argument("--diff")
    i.add_argument("--item-id", required=True)
    i.add_argument("--page", help="the exported page ON DISK, to attribute the diff's lines to this item")
    i.add_argument("--class", dest="klass", choices=CLASSES)
    i.add_argument("--skill", choices=SKILLS)
    i.add_argument("--steered-run-wrong", action="store_true",
                   help="a recorded file steered the run wrong. Without it the entry is kept but "
                   "proposes nothing.")
    i.add_argument("--note")
    i.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("propose", help="gated proposals from the accumulated evidence")
    p.add_argument("--min-runs", type=int, default=2)
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print only; write no proposal file")
    p.add_argument("--show-applied", action="store_true", help="include proposals already recorded as applied")
    p.add_argument("--applied-file", help="override the applied ledger's path")

    q = sub.add_parser("applied", help="record or list proposals a person approved and applied")
    q.add_argument("--skill", choices=SKILLS)
    q.add_argument("--class", dest="klass", choices=CLASSES)
    q.add_argument("--items", nargs="*")
    q.add_argument("--date", default=dt.date.today().isoformat())
    q.add_argument("--decision", help="where the approval is written down")
    q.add_argument("--measured", help="where the before and after benchmark is written down")
    q.add_argument("--list", action="store_true")
    q.add_argument("--applied-file", help="override the applied ledger's path")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        cfg = config.load(a.config, validate=False)
    except config.ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 3
    st = store.Store(cfg, data_dir=a.data_dir)
    if a.cmd == "classify":
        text = Path(a.diff).read_text(encoding="utf-8")
        if a.item_id:
            page = Path(a.page) if a.page else None
            if page is None:
                m = OLD_PATH.search(text)
                page = Path(m.group(1)) if m else None
            sliced = slice_by_item(text, a.item_id, page) if page else None
            if sliced is None:
                print("FATAL: need the exported page to slice by item; pass --page", file=sys.stderr)
                return 2
            text = "\n".join(["--- x (on disk)", "+++ y (this run)"]
                             + ["-" + ln for ln in sliced[0]] + ["+" + ln for ln in sliced[1]])
        print(json.dumps(classify(text, tag_regex(cfg)), indent=1))
        return 0
    try:
        if a.cmd == "ingest":
            return ingest(a, st, cfg)
        if a.cmd == "applied":
            return cmd_applied(a, st)
        return propose(a, st, cfg)
    except FileNotFoundError as exc:
        print("FATAL: %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
