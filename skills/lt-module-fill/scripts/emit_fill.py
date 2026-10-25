#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one fill generator: answers.json + module.json -> Lt fill scripts, and the run's records.

    emit_fill.py payload   --module M --answers A [--out DIR] [--pages 4-11] [--exclude ITEM ...]
                           [--anchors F] [--approvals F] [--replace --against F] [--include-rat]
    emit_fill.py scripts   [--out DIR] [--payload F] [--pages-per-call N] [--form expression|function]
                           [--stub | --no-stub] [--lib-mode inline|served] [--lib-url URL] [--no-render]
    emit_fill.py approve   --module M --item ITEM --quote "<the user's words>"
    emit_fill.py record    --module M --fill RECEIPT [RECEIPT ...] --verify RECEIPT [--payload F ...]
    emit_fill.py check     --module M [--answers A] [--fill RECEIPT ...] [--resume RECEIPT ...]
    emit_fill.py check-verify --module M --check RECEIPT [...]
    emit_fill.py record    --module M --check RECEIPT [...] --verify RECEIPT
    emit_fill.py record    --module M --dialog RECEIPT
    emit_fill.py calibrate --module M --answers A --item ITEM
    emit_fill.py dialog-args --module M --panel KEY
    emit_fill.py save-model-answer --module M --from RESULT
    emit_fill.py serve     [--port N] [--allow-origin URL ...]
    emit_fill.py djb2 TEXT ...
Every subcommand takes --config F and --data-dir D. DIR defaults to <data>/runs/<module_id>/fill-js.
Exit: 0 written | 2 nothing to emit | 3 input broken or refused | 4 refused by config.

payload: plans with plan_fill.py, so an item the plan marks unfillable (read-only, a mirror, a
permanent degrade, an unmapped type, a page at or past the first door, a RAT page) is never
emitted. It writes fill-payload.json (the HTML of each target, one <p> per segment or table
cell) and plan.json (the plan `ltjs.py check-url --plan` reads), then runs every config
[[text_cleaners]] entry with stage "fill-text" on the payload, so the hashes are taken from
the text that is actually written. A cleaner may change `html` strings only.

scripts: writes one body per call, `_fill-<module>-p<N>.js` for one page or
`_fill-<module>-b<K>.js` for a batch, `_verify-<module>-p<N>.js` per call and
`_verify-<module>.js` for the run, then renders each through ltjs.py (guard, save watcher,
the form your browser tool evaluates) and prints the rendered paths. `--pages` limits the
scripts to some pages. Pages per call follow the origin's calibration in
<data>/state/calibration.json:
  no save record      one page per call, on the fallback wait (at least 4 s after the last
                      write and jQuery.active back at its base); the first call carries a
                      `save` ticket
  save, no cross_page one 2-page trial batch with a `cross_page` ticket, every other page alone
  cross_page ok       batches of up to --pages-per-call (default fill.max_pages_per_call)
  cross_page failed   one page per call, and the reason is printed
Only pages whose jobs are all text, table or multiple-choice join a batch: addAnswer writes
are measured on a displayed page only. A ticket is a pending record in calibration.json
(written atomically) that the fill receipt echoes; `record` uses each ticket once.

record: reads the fill receipts and the fresh-load verify receipt (the JSON each script
returned), appends the item receipts make_fill_report.py needs to <run>/receipts.json and the
save waits to <run>/save-waits.json, and calibrates the origin:
  - a `save` ticket filled on the fallback wait, with every ticket item written, every
    written item `ok` (exact ids, at least one) in a fresh load of another document on the
    same Lt client build, and every key's save identified by LtNet on one write endpoint (a
    Backbone sync on a request LtNet observed, or a keyed request), writes the `save` record
    with the identified transport (source, method, path template) and client_sig; a save
    LtNet could not identify keeps the origin on the fallback wait; a new save record clears
    a passed cross_page record;
  - a `cross_page` ticket, a 2-page batch on the calibrated wait ending model or net with
    every ticket item written and `ok`, writes cross_page ok; any miss, an unconfirmed or
    failed wait writes cross_page failed (one page per call); unwritten items make the
    trial inconclusive and the next scripts emit another;
  - any miss after a calibrated wait that claimed saved drops `save` too (back to the
    fallback wait); a changed Lt client build drops the records and every pending ticket.
  Records carry a format version; a record of another version, or a save record with no
  write method and path template, counts as absent. Every
  change is written to the origin's history with its reason. With --dialog, a
  confirm_dialog.js receipt on the fallback wait whose one file-carrying request answered
  2xx records the dialog method and path template.
calibrate: an explicit one-item calibration: a payload and a script for that item alone,
on the fallback wait whatever is recorded, with a `save` ticket. Success also clears a
failed cross_page record; failure drops the save record and fails cross_page. Run it,
verify from a fresh load, then `record`.

approve: records the user's words for one single-attempt item in the run's approvals.json
(ltcore.approvals) and prints the approval_id that answers.json's
single_attempt_approval must carry. The agent asks the user first and passes the reply
verbatim.

Refused before any script exists (exit 3):
  * a single-attempt item without single_attempt_approval, or with an approval that is
    missing from approvals.json, names another item or module, or is not single-attempt
  * answers for another module or run, or a draft whose question_digest no longer matches
  * replace mode on anything but text and table items
  * a label placement with no location index, a label or bin that is not in the capture
  * an upload-and-annotate item becomes a TODO record: upload.js writes it, one panel at a time
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402
ltcore.bootstrap([])
from ltcore import approvals, config, store  # noqa: E402
from ltcore.digest import question_digest  # noqa: E402

from ltcore import page_kinds, rat

import argparse  # noqa: E402
import copy  # noqa: E402
import datetime as dt  # noqa: E402
import hashlib  # noqa: E402
import html  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan_fill  # noqa: E402

BROWSER = HERE / "browser"
LIB = BROWSER / "fill_lib.js"
PROBE = BROWSER / "probe_panels.js"
PAYLOAD_SCHEMA = "lt-fill-payload/2"
OLD_PAYLOAD_SCHEMAS = {"lt-fill-payload/1"}

# Capture type -> job kind. Anything else the plan calls fillable is refused, not guessed.
KIND = {
    "text-response": "text",
    "table-completion": "table",
    "multiple-choice": "select",
    "image-labeling": "labels",
    "drag-and-drop-labeling": "labels",
    "categorization": "categories",
}
UPLOAD_TYPES = {"upload-and-annotate"}
REPLACEABLE = {"text", "table"}
# Kinds written by model.set alone: the only kinds a batch page that is not displayed takes.
SET_KINDS = {"text", "table", "select"}
ANCHOR_RE = re.compile(r"\banchor (\d+) at (\d+(?:\.\d+)?)% across, (\d+(?:\.\d+)?)% down")
ANCHOR_IDX_RE = re.compile(r"\banchor (\d+)\b")
UNCALIBRATED_FLOOR_MS = 4000
# Fill receipt results that mean the item's panel was written, or holds this suite's answer.
WRITE_RESULTS = {"written", "replaced", "already-new", "write-mismatch", "partial",
                 "write-not-visible", "write-threw", "save-error"}

# ---- page text and the hash: fill_lib.js's pageText() and djb2() are the twins ----------------
# The whitespace class is spelled out because Python's \s and JS's \s differ (U+FEFF, U+001C to
# U+001F, U+0085). Tag patterns match ASCII case only, as JS /i does without the u flag.
WS = r"[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"
RE_BR = re.compile(r"<" + WS + r"*br" + WS + r"*/?>", re.I | re.A)
RE_CLOSE = re.compile(r"</" + WS + r"*(p|div|li|tr|td|h[1-6])" + WS + r"*>", re.I | re.A)
RE_TAG = re.compile(r"<[^>]+>")
RE_ENT = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z]+);")
RE_WS_RUN = re.compile(WS + r"+")
RE_WS_EDGE = re.compile(r"^" + WS + r"+|" + WS + r"+$")
NAMED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": "\u00a0"}


def _entity(m: re.Match) -> str:
    e = m.group(1)
    if e[0] == "#":
        n = int(e[2:], 16) if e[1] in "xX" else int(e[1:])
        return chr(n) if 0 < n <= 0x10FFFF else m.group(0)
    return NAMED.get(e, m.group(0))


def page_text(h: str) -> str:
    """The text Lt shows for an HTML value, whitespace collapsed. Twin of fill_lib.js pageText()."""
    s = RE_TAG.sub("", RE_CLOSE.sub(" ", RE_BR.sub(" ", h or "")))
    s = RE_ENT.sub(_entity, s)
    return RE_WS_EDGE.sub("", RE_WS_RUN.sub(" ", s))


def djb2(s: str) -> int:
    """32-bit djb2 over UTF-16 code units, as JS charCodeAt sees them. Twin of fill_lib.js djb2()."""
    h = 5381
    data = s.encode("utf-16-le", "surrogatepass")
    for i in range(0, len(data), 2):
        h = (h * 33 + int.from_bytes(data[i:i + 2], "little")) & 0xFFFFFFFF
    return h


def s_text(h) -> str:
    """capture.js's S(): tags out, a few entities decoded. Twin of fill_lib.js S()."""
    s = "" if h is None else str(h)
    s = re.sub(r"<\s*br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|div|li|tr|h[1-6])\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                    ("&quot;", '"'), ("&#39;", "'")):
        s = re.sub(re.escape(ent), lambda _m, c=ch: c, s, flags=re.I)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return RE_WS_EDGE.sub("", s)


def title_hash(title) -> int:
    """djb2 of a page title as fill_lib.js titleHash() reads it."""
    return djb2(RE_WS_EDGE.sub("", RE_WS_RUN.sub(" ", s_text(title))))


def table_hash(hs: list) -> str:
    """A table's cell hashes as one string. Twin of fill_lib.js tableHash()."""
    return "cells:" + ",".join(str(h) for h in hs)


def _u16(s: str) -> bytes:
    return s.encode("utf-16-be", "surrogatepass")


def canon_select(idx: list[int]) -> str:
    return "sel:" + ",".join(str(i) for i in sorted(idx))


def nt(t: str) -> str:
    """fill_lib.js's NT(): numeric entities decoded, whitespace runs (NBSP too) collapsed."""
    t = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), str(t))
    t = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), t)
    return re.sub(r"[\u00a0\s]+", " ", t).strip()


def canon_pairs(prefix: str, pairs: list[tuple]) -> str:
    """Lines sorted by UTF-16 code unit, which is what JS's default sort() compares."""
    return prefix + "\n".join(sorted((f"{a}\t{nt(b)}" for a, b in pairs), key=_u16))


def as_html(texts: list[str]) -> str:
    """One <p> per segment or cell, HTML-escaped (a draft may contain '<')."""
    return "".join(f"<p>{html.escape(t.strip(), quote=False)}</p>" for t in texts)


def job_hash(job: dict) -> None:
    """Set `h` on a job (and on each table cell) from the payload as it stands now."""
    k = job["kind"]
    if k == "text":
        job["h"] = djb2(page_text(job["html"]))
    elif k == "table":
        for c in job["cells"]:
            c["h"] = djb2(page_text(c["html"]))
    elif k == "select":
        job["h"] = djb2(canon_select(job["want"]))
    elif k == "labels":
        job["h"] = djb2(canon_pairs("lab:", [(p["loc"], p["label"]) for p in job["placements"]]))
    elif k == "categories":
        job["h"] = djb2(canon_pairs("cat:", [(p["cat_index"], p["label"]) for p in job["placements"]]))


def expected_hash(job: dict):
    """What the fresh-load verify must read for this job: an int, or a table's cell string."""
    j = copy.deepcopy(job)
    job_hash(j)
    if j["kind"] == "table":
        return table_hash([c["h"] for c in j["cells"]])
    return j.get("h")


# ---- inputs -----------------------------------------------------------------------------------
class Refusal(Exception):
    """A whole-run refusal: exit 3, nothing written."""


class ConfigRefusal(Refusal):
    """Refused by a config switch: exit 4."""


def load(path: str | Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise Refusal(f"{path}: {e}")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, doc) -> None:
    """Atomic write: a temp file in the same folder, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_pages(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def origin_of(url: str | None) -> str | None:
    m = re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else None


def load_module(path) -> dict:
    mod = load(path)
    if mod.get("schema_version") != 1 or "pages" not in mod:
        raise Refusal(f"{path} does not look like a module.json")
    try:
        store.check_id(str(mod.get("module_id")))
    except ValueError as e:
        raise Refusal(str(e))
    return mod


def check_binding(mod: dict, ans: dict) -> list[str]:
    """Answers bound to this capture, or a Refusal. Returns notes."""
    notes = []
    if ans.get("module_id") != mod.get("module_id"):
        raise Refusal(f"answers are for module {ans.get('module_id')!r}, the capture is {mod.get('module_id')!r}")
    if ans.get("run_id") != mod.get("run_id"):
        raise Refusal(f"answers are for run {ans.get('run_id')!r}, the capture is run {mod.get('run_id')!r}")
    want = (ans.get("source_module") or {}).get("question_digest")
    got = question_digest(mod)
    if want and want != got:
        raise Refusal(f"answers.source_module.question_digest {want[:12]}... does not match the capture "
                      f"({got[:12]}...): a question changed since the draft. Draft again, or compare with "
                      f"python3 -m ltcore.digest <old> --diff <new>.")
    if not want:
        notes.append("answers record no question_digest; the binding to this capture is unchecked")
    return notes


def check_approvals(mod: dict, ans: dict, path: Path | None) -> dict[str, dict]:
    """item_id -> approval record, each checked against the capture and approvals.json."""
    try:
        return approvals.check(mod, ans, path)
    except approvals.ApprovalError as e:
        raise Refusal("; ".join(e.problems))


def load_anchors(path: str | None) -> dict:
    """--anchors: {item_id: {label: loc | {"loc": n, "x": 0.41, "y": 0.32}}}."""
    if not path:
        return {}
    data = load(path)
    if not isinstance(data, dict):
        raise Refusal(f"{path}: expected an object keyed by item_id")
    return data


def prior_hashes(path: str) -> tuple[str, dict]:
    """--against: the recorded text of the earlier fill, as {item_id: {"h": n} | {"cells": {(r,c): n}}}.

    A fill payload is the record of what was written, after the text cleaners, so it is
    preferred. An answers file is accepted; where a cleaner changed a character its hash
    misses and the target is reported changed-since-fill-skipped, the safe direction."""
    data = load(path)
    out: dict[str, dict] = {}
    jobs = []
    if isinstance(data, dict) and (data.get("schema") == PAYLOAD_SCHEMA or data.get("schema") in OLD_PAYLOAD_SCHEMAS):
        kind = "payload"
        for pg in data.get("pages", {}).values():
            jobs.extend(pg.get("jobs", []))
    elif isinstance(data, dict) and "items" in data and "source_module" in data:
        kind = "answers"
        for it in data["items"]:
            if it.get("status") != "drafted":
                continue
            if it.get("table_cells"):
                jobs.append({"item": it["item_id"], "kind": "table",
                             "cells": [{"row": c["row"], "col": c["col"], "html": as_html([c["text"]])}
                                       for c in it["table_cells"]]})
            elif it.get("segments"):
                jobs.append({"item": it["item_id"], "kind": "text",
                             "html": as_html([s["text"] for s in it["segments"]])})
    else:
        raise Refusal(f"{path}: neither a fill payload nor an answers file")
    for j in jobs:
        if j.get("kind") == "text":
            out[j["item"]] = {"h": djb2(page_text(j["html"]))}
        elif j.get("kind") == "table":
            out[j["item"]] = {"cells": {(c["row"], c["col"]): djb2(page_text(c["html"])) for c in j["cells"]}}
    return kind, out


def plan_fields(pl: dict, mod: dict) -> dict:
    """The plan subset check-url and the guard read, plus the page indexes never visited."""
    routes = {n.get("route") for n in pl["boundary_map"]["never_navigated"] if n.get("route")}
    never_pages = set(pl.get("report_pages") or [])
    for p in mod.get("pages", []):
        r = p.get("route")
        if r and r in routes and p.get("page_index") is not None:
            never_pages.add(p["page_index"])
    return {
        "module_id": pl["module_id"], "origin": pl.get("origin"), "instance": pl.get("instance"),
        "course_id": pl.get("course_id"), "run_id": pl.get("run_id"),
        "stop_before_page": pl["stop_before_page"],
        "never_navigated": pl["never_navigated"],
        "never_navigated_pages": sorted(never_pages),
        "report_pages": pl.get("report_pages") or [],
        "checkpoint_pages": pl.get("checkpoint_pages") or [],
        "flow_pages": pl.get("flow_pages") or [],
        "pages": [{"page_index": p.get("page_index"), "page_no": p.get("page_no"), "url": p.get("url")}
                  for p in pl["pages"]],
    }


# ---- payload ----------------------------------------------------------------------------------
def build_jobs(mod: dict, ans: dict, args, cfg, *, approvals_path: Path | None,
               only_items: set[str] | None = None) -> dict:
    """The whole payload as a dict. Raises Refusal on a run-level problem."""
    notes = check_binding(mod, ans)
    appr = check_approvals(mod, ans, approvals_path)
    anchors = load_anchors(getattr(args, "anchors", None))
    only_pages = parse_pages(getattr(args, "pages", None))
    exclude = set(getattr(args, "exclude", None) or [])
    replace = bool(getattr(args, "replace", False))
    against = getattr(args, "against", None)
    if replace and not against:
        raise Refusal("--replace needs --against: the recorded text of the earlier fill is the hash guard")
    against_kind, prior = prior_hashes(against) if replace else (None, {})

    # Verify each item approval. Scripts retain single-attempt flags and never submit.
    if appr:
        for iid, rec in appr.items():
            notes.append(f"{iid}: single-attempt item approved by the user ({rec['approval_id']}); "
                         f"written through the model only, never submitted")
    answer_hash = sha256(Path(args.answers))
    record = page_kinds.load_current(mod, cfg, ans, data_dir=getattr(args, "data_dir", None), answers_sha256=answer_hash)
    if record is None and any(page_kinds.is_candidate(p, rat.policy_from_config(cfg)) for p in mod["pages"]):
        raise Refusal("rat-record-stale: run page_kinds.py classify --json")
    pl = plan_fill.plan(mod, ans, bool(getattr(args, "include_rat", False)), cfg, record=record,
                        data_dir=getattr(args, "data_dir", None), answers_sha256=answer_hash)
    # The capture as it is: the plan check-url reads, and the submitted single-attempt items
    # every later script must find still read-only, approved or not.
    plan_out = pl

    caps = {i["item_id"]: (p, i) for p in mod["pages"] for i in p["items"]}
    planned = {i["item_id"]: (pg, i) for pg in pl["pages"] for i in pg["items"]}
    page_reason = {s["page_index"]: s["reason"] for s in pl["skipped_pages"]}
    single_keys: dict[int, list[str]] = {}
    for p in mod["pages"]:
        for it in p["items"]:
            if it.get("attempts") == "single":
                single_keys.setdefault(p["page_index"], []).append(str(it["panel_key"]))
    # A submitted single-attempt item the plan passed: a script on its page or a later one
    # runs only while the item reads read-only.
    passed = [(s["page_index"], str(caps[s["item_id"]][1]["panel_key"]))
              for s in plan_out["boundary_map"].get("single_attempts_passed") or []]

    pages: dict[str, dict] = {}
    todo, refused, not_emitted = [], [], []
    unchanged = 0

    def page_entry(p: dict) -> dict:
        return pages.setdefault(str(p["page_index"]), {
            "title": p.get("title"), "title_hash": title_hash(p.get("title")),
            "url": p.get("url"), "jobs": [], "todo": [],
            "rat": any(r["page_index"] == p["page_index"] and r["rat"] is True for r in pl.get("rat_pages", [])),
            "single_attempt_keys": single_keys.get(p["page_index"], []),
            "single_attempt_locked": [[pg, k] for pg, k in passed if pg <= p["page_index"]]})

    sheet_pages = {d["page_index"]: d for d in pl.get("rat_pages", []) if d["decision"] == "fill-from-sheet"}
    for a in ans.get("items", []):
        iid = a["item_id"]
        if iid in caps and caps[iid][0]["page_index"] in sheet_pages:
            continue
        if a.get("status") != "drafted":
            continue
        if only_items is not None and iid not in only_items:
            continue
        if iid not in caps:
            raise Refusal(f"{iid}: drafted but not in the capture")
        p, cap = caps[iid]
        idx = p["page_index"]
        if cap.get("attempts") == "single" and iid not in appr:
            refused.append({"item": iid, "page": idx, "reason": "refused-single-attempt",
                            "detail": "single-attempt; answers.json carries no single_attempt_approval"})
            continue
        if iid in exclude:
            not_emitted.append({"item": iid, "page": idx, "reason": "excluded by --exclude"})
            continue
        if only_pages is not None and idx not in only_pages:
            not_emitted.append({"item": iid, "page": idx, "reason": "outside --pages"})
            continue
        if iid not in planned:
            not_emitted.append({"item": iid, "page": idx,
                                "reason": f"plan: page {page_reason.get(idx, 'not planned')}"})
            continue
        entry = planned[iid][1]
        if not entry["fillable"]:
            sk = entry.get("skip") or {}
            not_emitted.append({"item": iid, "page": idx,
                                "reason": f"plan: {sk.get('class', 'not fillable')}"})
            continue
        typ = cap["type"]
        base = {"item": iid, "panel_key": str(cap["panel_key"]), "plugin_key": cap.get("plugin_key"),
                "type": typ}
        if iid in appr:
            base["single_attempt_approved"] = True
            base["approval_id"] = appr[iid]["approval_id"]
        if typ in UPLOAD_TYPES:
            todo.append({"item": iid, "page": idx, "panel_key": str(cap["panel_key"]), "kind": "upload",
                         "asset_refs": a.get("asset_refs") or [],
                         "note": "upload one prepared PNG or JPEG with ltjs.py render upload; "
                                 "not written by these scripts"})
            continue
        kind = KIND.get(typ)
        if kind is None:
            refused.append({"item": iid, "page": idx, "reason": "no-write-path", "detail": typ})
            continue
        if replace and kind not in REPLACEABLE:
            refused.append({"item": iid, "page": idx, "reason": "replace-unsupported-type", "detail": typ})
            continue
        job = dict(base, kind=kind)
        why = None
        if kind == "text":
            segs = [s["text"] for s in a.get("segments", []) if s.get("text", "").strip()]
            if not segs:
                why = "empty-draft"
            job["html"] = as_html(segs)
        elif kind == "table":
            cells = a.get("table_cells") or []
            if not cells:
                why = "empty-draft"
            job["cells"] = [{"row": c["row"], "col": c["col"], "html": as_html([c["text"]])} for c in cells]
        elif kind == "select":
            opts = [o["option_id"] for o in cap.get("options", [])]
            texts = {o["option_id"]: o["text"] for o in cap.get("options", [])}
            sel = a.get("proposed_selection") or []
            if not sel:
                why = "no-selection"
            elif any(o not in opts for o in sel):
                why = "option-not-in-capture"
            else:
                # Select options are captured in live order (o1..on), so the position is the
                # live index; the script checks each text again before writing.
                job["want"] = [opts.index(o) for o in sel]
                job["want_text"] = [texts[o] for o in sel]
        elif kind == "labels":
            pool = {o["text"] for o in cap.get("options", [])}
            m = re.search(r"\blocations=(\d+)", (cap.get("constraints") or {}).get("note") or "")
            nloc = int(m.group(1)) if m else None
            given = anchors.get(iid, {})
            pls, seen = [], set()
            for lp in a.get("label_placements") or []:
                lab = lp.get("label")
                if lab not in pool:
                    why = "label-not-in-capture"
                    break
                g = given.get(lab)
                x = y = None
                mi = ANCHOR_IDX_RE.search(lp.get("target") or "")
                if mi and sum(1 for o in cap.get("options", []) if o["text"] == lab) > 1:
                    # A repeated label: the anchor map is keyed by text and holds one copy only.
                    g = int(mi.group(1))
                if isinstance(g, int):
                    loc = g
                elif isinstance(g, dict) and isinstance(g.get("loc"), int):
                    loc, x, y = g["loc"], g.get("x"), g.get("y")
                else:
                    mm = ANCHOR_RE.search(lp.get("target") or "")
                    if not mm:
                        why = "no-location"
                        break
                    loc, x, y = int(mm.group(1)), float(mm.group(2)) / 100, float(mm.group(3)) / 100
                if loc in seen or (nloc is not None and not 0 <= loc < nloc):
                    why = "bad-location"
                    break
                seen.add(loc)
                pl_ = {"label": lab, "loc": loc}
                if x is not None and y is not None:
                    pl_["x"], pl_["y"] = round(float(x), 6), round(float(y), 6)
                pls.append(pl_)
            if not pls and not why:
                why = "no-placements"
            job["placements"] = pls
        elif kind == "categories":
            pool = {o["text"] for o in cap.get("options", [])}
            bins = (cap.get("constraints") or {}).get("categories") or []
            pls = []
            for lp in a.get("label_placements") or []:
                lab, ti, tgt = lp.get("label"), lp.get("target_index"), lp.get("target")
                if lab not in pool:
                    why = "label-not-in-capture"
                    break
                if ti is None:
                    if tgt not in bins:
                        why = "category-not-in-capture"
                        break
                    ti = bins.index(tgt)
                if not 0 <= ti < len(bins) or (tgt is not None and tgt != bins[ti]):
                    why = "category-not-in-capture"
                    break
                pls.append({"label": lab, "cat_index": ti, "cat": bins[ti]})
            if not pls and not why:
                why = "no-placements"
            job["placements"] = pls
        if why:
            refused.append({"item": iid, "page": idx, "reason": why})
            continue
        if replace:
            old = prior.get(iid)
            if kind == "text":
                job["oh"] = old.get("h") if old else None
                if job["oh"] is not None and job["oh"] == djb2(page_text(job["html"])):
                    unchanged += 1
                    continue
            else:
                oc = (old or {}).get("cells", {})
                for c in job["cells"]:
                    c["oh"] = oc.get((c["row"], c["col"]))
                if all(c["oh"] == djb2(page_text(c["html"])) for c in job["cells"]):
                    unchanged += 1
                    continue
        page_entry(p)["jobs"].append(job)

    # Sheet mappings live only in this process and the emitted payload/scripts.
    root_paths = dict(rat.effective_roots(rat.policy_from_config(cfg)))
    answer_map = {a["item_id"]: a for a in ans.get("items", [])}
    for idx, status in sheet_pages.items():
        if only_pages is not None and idx not in only_pages:
            continue
        page = next(p for p in mod["pages"] if p["page_index"] == idx)
        if any(i["item_id"] not in answer_map for i in page["items"]):
            raise Refusal("RAT page item missing from answers.json")
        ref = status["sheet"]
        root = root_paths.get(ref["root"])
        if root is None:
            raise Refusal("rat-record-stale: root changed")
        # Resolve sanitized display names by stat-only iteration; only read_sheet opens.
        matched = None
        for path in rat._listing(ref["root"], root)[1]:
            if path.name == ref["name"] or ref["name"].startswith("sheet-"):
                try:
                    candidate = rat.read_sheet(root, path.name)
                except rat.RatError:
                    continue
                if candidate["sha256"] == ref["sha256"] and candidate["name"] == ref["name"]:
                    matched = candidate
                    break
        if matched is None:
            raise Refusal("rat-record-stale: sheet hash changed")
        matched["root"] = ref["root"]
        match = rat.match_sheet(matched, page, mod, week_pattern=rat.policy_from_config(cfg).get("_week_pattern"))
        if match is None or match["grade"] not in {"full", "partial"}:
            raise Refusal("rat-record-stale: sheet no longer aligns")
        pg = page_entry(page)
        pg["rat"] = True
        for number, cap in enumerate(page["items"], 1):
            iid = cap["item_id"]
            if iid in exclude or (only_items is not None and iid not in only_items):
                continue
            if cap.get("attempts") == "single" and iid not in appr:
                refused.append({"item": iid, "page": idx, "reason": "refused-single-attempt"})
                continue
            if iid not in planned or not planned[iid][1]["fillable"]:
                continue
            row = match["mapping"][number-1] if number <= len(match["mapping"]) else {"status": "unaligned"}
            if row["status"] != "answered":
                todo.append({"item": iid, "page": idx, "panel_key": str(cap["panel_key"]), "kind": "rat-sheet",
                             "blocker": f"Question {number} has {row['status']} answer in {ref['name']}",
                             "placement": f"page {idx}, panel {cap['panel_key']}"})
                continue
            if replace:
                raise Refusal("RAT sheet entry cannot replace an occupied answer")
            selected = row["selected"]
            job = {"item": iid, "panel_key": str(cap["panel_key"]), "plugin_key": cap.get("plugin_key"),
                   "type": cap["type"], "kind": "select", "want": selected,
                   "want_text": [cap["options"][k]["text"] for k in selected], "source": "rat-sheet",
                   "sheet": dict({k: ref[k] for k in ("root", "name", "sha256")}, question=number)}
            if iid in appr:
                job.update(single_attempt_approved=True, approval_id=appr[iid]["approval_id"])
            pg["jobs"].append(job)

    if getattr(args, "pages", None):
        notes.append(f"--pages {args.pages}: items outside it are not emitted")
    if exclude:
        notes.append(f"--exclude {' '.join(sorted(exclude))}")
    if only_items is not None:
        notes.append(f"calibration payload: item {', '.join(sorted(only_items))} only")
    if replace:
        notes.append(f"replace mode against {against} ({against_kind}); {unchanged} item(s) unchanged "
                     f"and not emitted")
    for t in todo:
        page_entry(caps[t["item"]][0])["todo"].append(t)

    plan = plan_fields(plan_out, mod)
    return {
        "schema": PAYLOAD_SCHEMA,
        "mode": "replace" if replace else "fill",
        "module_id": mod["module_id"], "run_id": mod["run_id"],
        "instance": mod.get("instance"), "course_id": mod.get("course_id"),
        "origin": origin_of(mod.get("source_url")),
        "generated_at": now_utc(),
        "module": {"path": str(args.module), "sha256": sha256(Path(args.module)),
                   "question_digest": question_digest(mod)},
        "answers": {"path": str(args.answers), "sha256": sha256(Path(args.answers))},
        "source_page_kinds": ({"path": str((_open_store(args, cfg).run_dir(mod["module_id"]) / "page-kinds.json").resolve()),
                               "sha256": page_kinds.record_hash(record)} if record else None),
        "against": ({"path": str(against), "sha256": sha256(Path(against))} if replace else None),
        "stop_before_page": plan["stop_before_page"],
        "never_navigated_pages": plan["never_navigated_pages"],
        "plan": plan,
        "notes": notes,
        "pages": dict(sorted(pages.items(), key=lambda kv: (bool(kv[1].get("rat")), int(kv[0])))),
        "todo": todo,
        "refused": refused,
        "not_emitted": not_emitted,
    }


# ---- text cleaners (config [[text_cleaners]], stage fill-text) ----------------------------------
def _shape(node, trail=""):
    """Every non-html leaf of a payload, as (path, value); html strings are free to change."""
    out = []
    if isinstance(node, dict):
        for k in sorted(node):
            if k == "html" and isinstance(node[k], str):
                out.append((f"{trail}.html", "<html>"))
            else:
                out += _shape(node[k], f"{trail}.{k}")
    elif isinstance(node, list):
        out.append((f"{trail}[]", len(node)))
        for i, v in enumerate(node):
            out += _shape(v, f"{trail}[{i}]")
    else:
        out.append((trail, node))
    return out


def _cleaners_module():
    try:
        from ltcore import cleaners  # noqa: PLC0415
    except ImportError:
        return None
    return cleaners if hasattr(cleaners, "run_cleaner") and hasattr(cleaners, "cleaners_for") else None


def _run_local(entry: dict, fin: Path, fout: Path, timeout: int) -> None:
    """The same rules as ltcore.cleaners.run_cleaner, for an install without it: argv list,
    no shell, {in} and {out} replaced, a timeout, a non-empty output file."""
    argv = entry.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise Refusal(f"text cleaner {entry.get('name')!r}: argv must be a non-empty list of strings")
    cmd = [re.sub(r"\{(in|out)\}", lambda m: str(fin) if m.group(1) == "in" else str(fout), a) for a in argv]
    try:
        r = subprocess.run(cmd, encoding='utf-8', capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Refusal(f"text cleaner {entry.get('name')!r} did not finish within {timeout} s")
    except OSError as e:
        raise Refusal(f"text cleaner {entry.get('name')!r} could not start: {e}")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        raise Refusal(f"text cleaner {entry.get('name')!r} exited {r.returncode}: {tail[0][:200]}")
    if not fout.is_file() or fout.stat().st_size == 0:
        raise Refusal(f"text cleaner {entry.get('name')!r} produced no non-empty output file")
    fin.write_bytes(fout.read_bytes())


def run_text_cleaners(cfg, pay: dict, work: Path) -> tuple[dict, list[str]]:
    """Run each config [[text_cleaners]] entry with stage fill-text over the payload, in
    order, through ltcore.cleaners. Only `html` strings may change; anything else, a
    non-zero exit, a timeout or a missing output refuses the payload."""
    cl = _cleaners_module()
    if cl is not None:
        try:
            entries = cl.cleaners_for("fill-text", cfg=cfg)
        except Exception as e:  # CleanerError: an unknown stage or a broken entry
            raise Refusal(f"text_cleaners: {e}")
    else:
        entries = [dict(c) for c in (cfg.get("text_cleaners", []) or []) if c.get("stage") == "fill-text"]
    notes = []
    cur = pay
    for n, entry in enumerate(entries):
        name = str(entry.get("name") or f"cleaner-{n}")
        timeout = entry.get("timeout_s", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise Refusal(f"text cleaner {name!r}: timeout_s must be a positive integer")
        fin = work / f"payload-{n}.json"
        fin.write_text(json.dumps(cur, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if cl is not None:
            res = cl.run_cleaner(entry, fin, timeout_s=timeout)
            if getattr(res, "status", None) not in ("cleaned", "unchanged"):
                raise Refusal(f"text cleaner {name!r} {getattr(res, 'status', 'failed')}: "
                              f"{getattr(res, 'detail', '')}; nothing was written")
        else:
            _run_local(entry, fin, work / f"payload-{n}.out.json", timeout)
        try:
            new = json.loads(fin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise Refusal(f"text cleaner {name!r} left no readable JSON: {e}")
        if _shape(new) != _shape(cur):
            raise Refusal(f"text cleaner {name!r} changed the payload beyond `html` strings "
                          f"(keys, items or job count); nothing was written")
        changed = sum(1 for a, b in zip(_html_values(cur), _html_values(new)) if a != b)
        notes.append(f"text cleaner {name}: {changed} html value(s) changed")
        cur = new
    return cur, notes


def _html_values(node) -> list[str]:
    out = []
    if isinstance(node, dict):
        for k in sorted(node):
            if k == "html" and isinstance(node[k], str):
                out.append(node[k])
            else:
                out += _html_values(node[k])
    elif isinstance(node, list):
        for v in node:
            out += _html_values(v)
    return out


def _open_store(args, cfg) -> "store.Store":
    return store.Store(cfg, data_dir=getattr(args, "data_dir", None))


def _approvals_path(args, st, mod) -> Path | None:
    if getattr(args, "approvals", None):
        return Path(args.approvals)
    p = st.approvals_path(mod["module_id"])
    return p


def _write_payload(pay: dict, dest: Path, cfg, force: bool) -> list[str]:
    if dest.exists() and not force:
        raise Refusal(f"{dest} exists. It may be the record of a fill already made, which a later "
                      f"--replace reads as its hash guard; pass --force to overwrite it.")
    with tempfile.TemporaryDirectory(prefix="lt-fill-clean-") as td:
        pay, notes = run_text_cleaners(cfg, pay, Path(td))
    pay["notes"] = pay.get("notes", []) + notes
    write_json(dest, pay)
    write_json(dest.parent / "plan.json", pay["plan"])
    return notes


def cmd_payload(args, cfg) -> int:
    mod, ans = load_module(args.module), load(args.answers)
    st = _open_store(args, cfg)
    pay = build_jobs(mod, ans, args, cfg, approvals_path=_approvals_path(args, st, mod))
    out = Path(args.out) if args.out else st.fill_js_dir(mod["module_id"])
    out.mkdir(parents=True, exist_ok=True)
    dest = out / ("replace-payload.json" if pay["mode"] == "replace" else "fill-payload.json")
    _write_payload(pay, dest, cfg, args.force)
    pay = load(dest)
    n = sum(len(p["jobs"]) for p in pay["pages"].values())
    print(f"{dest}: {n} job(s) on page(s) {', '.join(k for k, v in pay['pages'].items() if v['jobs']) or 'none'}"
          f" | {len(pay['todo'])} todo | {len(pay['refused'])} refused | {len(pay['not_emitted'])} not emitted")
    for r in pay["refused"]:
        print(f"  REFUSED {r['item']:<12} {r['reason']}" + (f" ({r['detail']})" if r.get("detail") else ""))
    for t in pay["todo"]:
        print(f"  TODO    {t['item']:<12} {t['kind']}: {t.get('note', t.get('blocker', 'manual TODO'))}")
    for note in pay["notes"]:
        print(f"  note    {note}")
    print(f"plan for check-url: {dest.parent / 'plan.json'}")
    print("next: emit_fill.py scripts" + (f" --out {out}" if args.out else f" --module {args.module}"))
    return 0 if n else 2


# ---- calibration state ------------------------------------------------------------------------
CAL_VERSION = 1
# Receipt results that count as a write a fresh load can confirm, and those that fail it.
PASS_RESULTS = {"written", "replaced"}
FAIL_RESULTS = {"write-mismatch", "partial", "write-not-visible", "write-threw", "save-error"}
# Methods that never carry a save; a save record naming one has no write endpoint.
NON_WRITE = {"GET", "HEAD", "OPTIONS"}


def has_endpoint(method, tpl) -> bool:
    """True when (method, path template) names a write request: what LtNet needs to match a
    save on a calibrated wait. A record without one runs LtNet's fallback rule."""
    return (isinstance(method, str) and bool(method.strip()) and method.strip().upper() not in NON_WRITE
            and isinstance(tpl, str) and bool(tpl.strip()))


def load_calibration(path: Path) -> dict:
    """<data>/state/calibration.json: {origin: {save, dialog, cross_page, pending, history}}."""
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: {path} unreadable ({e}); treating every origin as uncalibrated", file=sys.stderr)
        return {}
    return doc if isinstance(doc, dict) else {}


def _history(state: dict, event: str, detail: str) -> None:
    h = state.setdefault("history", [])
    h.append({"at": now_utc(), "event": event, "detail": detail})
    del h[:-20]


def calibration_state(cal: dict, origin: str | None) -> tuple[dict | None, dict | None, list[str]]:
    """(save record, cross_page record, notes) for an origin. A record of another format
    version counts as absent, so the origin calibrates again. So does a save record with no
    write endpoint (method and path template): LtNet would run the fallback rule on it, and
    a batch would refuse it."""
    state = cal.get(origin or "", {}) if isinstance(cal, dict) else {}
    notes, out = [], []
    for name in ("save", "cross_page"):
        rec = state.get(name) if isinstance(state.get(name), dict) else None
        if rec is not None and rec.get("version") != CAL_VERSION:
            notes.append(f"the {name} record for {origin} has format version {rec.get('version')!r}, not "
                         f"{CAL_VERSION}; it is ignored and the origin calibrates again")
            rec = None
        if name == "save" and rec is not None and not has_endpoint(rec.get("method"), rec.get("path_template")):
            notes.append(f"the save record for {origin} ({rec.get('source')!r}) names no write method and "
                         f"path template; it is ignored and the origin calibrates again")
            rec = None
        out.append(rec)
    return out[0], out[1], notes


def issue_ticket(st, origin: str, *, kind: str, module_id: str, pages: list[int], items: list[str],
                 payload_sha: str, explicit: bool = False) -> str:
    """Record a pending calibration ticket for one call, atomically, and return its id.

    `emit_fill.py record` calibrates only from a receipt that echoes a pending ticket, and
    uses each ticket once. A newer ticket of the same kind for the same module replaces an
    older one."""
    path = st.calibration_path
    cal = load_calibration(path)
    state = cal.setdefault(origin, {})
    issued = now_utc()
    tid = "T-" + hashlib.sha256(f"{module_id}|{kind}|{pages}|{payload_sha}|{issued}|{os.urandom(8).hex()}"
                                .encode()).hexdigest()[:8]
    pend = [t for t in state.get("pending", []) if isinstance(t, dict)
            and not (t.get("module_id") == module_id and t.get("kind") == kind)]
    pend.append({"ticket": tid, "kind": kind, "module_id": module_id, "pages": pages, "items": items,
                 "payload_sha256": payload_sha, "explicit": explicit, "issued_at": issued})
    state["pending"] = pend[-20:]
    write_json(path, cal)
    return tid


# ---- scripts ----------------------------------------------------------------------------------
def strip_comments(src: str) -> str:
    """Drop comment-only lines and blank lines. A block comment counts when its /* opens a line.

    Code lines are kept byte for byte, trailing comments included. Safe for fill_lib.js and
    probe_panels.js, which hold no multi-line string with a line opening with // or /*;
    tests/fill/fill_lib.test.js runs the stripped copies to keep it that way."""
    out, in_block = [], False
    for line in src.splitlines():
        s = line.strip()
        if in_block:
            end = line.find("*/")
            if end < 0:
                continue
            in_block = False
            rest = line[end + 2:]
            if rest.strip():
                out.append(rest)
            continue
        if not s or s.startswith("//"):
            continue
        if s.startswith("/*"):
            end = s.find("*/", 2)
            if end < 0:
                in_block = True
                continue
            rest = s[end + 2:]
            if rest.strip():
                out.append(rest)
            continue
        out.append(line)
    return "\n".join(out) + "\n"


PROBE_RETURN = re.compile(r"\n[ \t]*return await probeLtPanels\(\);[ \t]*\n?$")


def probe_function_body() -> str:
    """probe_panels.js without its final `return await probeLtPanels();`, for embedding."""
    src = strip_comments(PROBE.read_text(encoding="utf-8"))
    out = PROBE_RETURN.sub("\n", src)
    if out == src:
        raise Refusal(f"{PROBE} does not end in `return await probeLtPanels();`")
    return out


def lean(job: dict) -> dict:
    """A job without its HTML: what the verify script carries."""
    j = {k: v for k, v in job.items() if k not in ("html", "oh", "want_text")}
    if job["kind"] == "table":
        j["cells"] = [{k: v for k, v in c.items() if k not in ("html", "oh")} for c in job["cells"]]
    return j


def save_wait(cfg, save_rec: dict | None) -> tuple[dict, list[str]]:
    """cfg.save_wait for fill_lib.js: config fill.save_wait.* plus the origin's save record."""
    notes = []
    sw = {k: int(cfg.get(f"fill.save_wait.{k}")) for k in ("quiet_ms", "max_ms", "fallback_ms", "render_wait_ms")}
    if sw["fallback_ms"] < UNCALIBRATED_FLOOR_MS:
        notes.append(f"fill.save_wait.fallback_ms {sw['fallback_ms']} is below the uncalibrated floor; "
                     f"{UNCALIBRATED_FLOOR_MS} ms is used")
        sw["fallback_ms"] = UNCALIBRATED_FLOOR_MS
    sw["calibration"] = ({k: save_rec.get(k) for k in ("version", "source", "method", "path_template", "client_sig")}
                         if save_rec else None)
    return sw, notes


def cfg_base(pay: dict, sw: dict) -> dict:
    return {"module_id": pay["module_id"], "run_id": pay["run_id"], "instance": pay.get("instance"),
            "course_id": pay.get("course_id"), "origin": pay.get("origin"), "mode": pay["mode"],
            "save_wait": sw, "source_payload_sha256": pay.get("_source_payload_sha256")}


def cfg_page(pay: dict, sw: dict, page: str, pg: dict, ticket: str | None = None) -> dict:
    c = cfg_base(pay, sw)
    if ticket:
        c["ticket"] = ticket
    c.update({"page": int(page), "title": pg.get("title"),
              "single_attempt_keys": pg.get("single_attempt_keys", []),
              "single_attempt_locked": pg.get("single_attempt_locked", []),
              "todo": [{"item": t["item"], "kind": t["kind"]} for t in pg.get("todo", [])]})
    return c


def cfg_batch(pay: dict, sw: dict, pages: list[str], ticket: str | None = None) -> dict:
    c = cfg_base(pay, sw)
    if ticket:
        c["ticket"] = ticket
    c.update({"batch_pages": [int(p) for p in pages],
              "never_navigated": pay.get("never_navigated_pages", []),
              "stop_before_page": pay.get("stop_before_page")})
    return c


def batch_plan(pay: dict, pages: list[str]) -> list[dict]:
    out = []
    for page in pages:
        pg = pay["pages"][page]
        out.append({"page": int(page), "title_hash": pg.get("title_hash", title_hash(pg.get("title"))),
                    "jobs": pg["jobs"],
                    "todo": [{"item": t["item"], "kind": t["kind"]} for t in pg.get("todo", [])],
                    "single_attempt_keys": pg.get("single_attempt_keys", []),
                    "single_attempt_locked": pg.get("single_attempt_locked", [])})
    return out


# The thin loader (served mode). It fetches both sources from a loopback server, checks each
# against the sha256 this script was built with, and reuses a copy already loaded in the tab.
THIN = r"""const BASE = __URL__, BUILD = __BUILD__, WANT = __WANT__;
const G = globalThis;
if (!(G.LtFillLib && G.LtFillLib.build === BUILD)) {
  const get = async n => {
    const ac = new AbortController(); const t = setTimeout(() => ac.abort(), 8000);
    try { const r = await fetch(BASE + '/' + n, { signal: ac.signal, cache: 'no-store' }); return r.ok ? await r.text() : null; }
    catch (e) { return null; } finally { clearTimeout(t); }
  };
  const hex = async s => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))))
    .map(b => b.toString(16).padStart(2, '0')).join('');
  const src = {};
  for (const n of Object.keys(WANT)) {
    src[n] = await get(n);
    if (src[n] === null) return { ok: false, fatal: 'lib-unreachable', file: n, writes: 0 };
    if (await hex(src[n]) !== WANT[n]) return { ok: false, fatal: 'lib-integrity', file: n, writes: 0 };
  }
  new Function(src['fill_lib.js'])();
  const probeBody = src['probe_panels.js'].replace(/\n[ \t]*return await probeLtPanels\(\);[ \t]*\n?$/, '\n');
  G.LtFillLib.loadProbe = new Function(probeBody + '\nreturn probeLtPanels;');
  G.LtFillLib.build = BUILD;
}
return await __CALL__;
"""


def script_body(call: str, lib_url: str | None) -> str:
    """The script body after its header: the whole library inline, or the thin loader."""
    if lib_url:
        want = {"fill_lib.js": sha256(LIB), "probe_panels.js": sha256(PROBE)}
        build = hashlib.sha256((want["fill_lib.js"] + want["probe_panels.js"]).encode()).hexdigest()[:16]
        return (THIN.replace("__URL__", json.dumps(lib_url.rstrip("/"))).replace("__BUILD__", json.dumps(build))
                .replace("__WANT__", json.dumps(want)).replace("__CALL__", call))
    lib = strip_comments(LIB.read_text(encoding="utf-8"))
    probe = probe_function_body()
    return (lib + "LtFillLib.loadProbe = function () {\n" + probe + "return probeLtPanels;\n};\n"
            + "return await " + call + ";\n")


def group_pages(pay: dict, requested: int, cap: int, save_rec, cross, only_singles: bool,
                only_pages: set[int] | None = None) -> tuple[list[list[str]], list[str]]:
    """Pages with jobs, grouped into calls by the calibration state. Returns (groups, notes)."""
    pages = [k for k, v in sorted(pay["pages"].items(), key=lambda kv: (bool(kv[1].get("rat")), int(kv[0]))) if v["jobs"] and (only_pages is None or int(k) in only_pages)]
    n = max(1, min(requested, cap)) if requested > 0 else 1
    notes = []
    origin = pay.get("origin") or "this origin"
    batchable = {k for k in pages if not pay["pages"][k].get("rat") and all(j["kind"] in SET_KINDS for j in pay["pages"][k]["jobs"])}
    singles = [[k] for k in pages]
    if only_singles or n == 1:
        return singles, notes
    if save_rec is None:
        notes.append(f"pages-per-call {n} needs cross-page calibration, and {origin} has no save "
                      f"calibration yet: one page per call on the fallback wait. After the first "
                      f"page's fresh-load verify, `emit_fill.py record` calibrates it; "
                      f"then run `emit_fill.py scripts --pages <the rest>` again.")
        return singles, notes
    if cross is None:
        groups, trial_done, i = [], False, 0
        while i < len(pages):
            k = pages[i]
            if not trial_done and k in batchable and i + 1 < len(pages) and pages[i + 1] in batchable:
                groups.append([k, pages[i + 1]])
                trial_done = True
                i += 2
                continue
            groups.append([k])
            i += 1
        if trial_done:
            notes.append(f"cross-page trial: pages {groups[[len(g) for g in groups].index(2)][0]} and "
                         f"{groups[[len(g) for g in groups].index(2)][1]} in one call. Verify them from a "
                         f"fresh load and run `emit_fill.py record` before any larger batch.")
        else:
            notes.append("no two batchable pages for the cross-page trial; one page per call")
        if n > 2:
            notes.append(f"batches of {n} need cross_page calibration for {origin}; none recorded yet")
        return groups, notes
    if not cross.get("ok"):
        notes.append(f"cross-page calibration failed for {origin}: {cross.get('reason', 'no reason recorded')}; "
                     f"one page per call")
        return singles, notes
    groups, cur = [], []
    for k in pages:
        if k not in batchable:
            if cur:
                groups.append(cur)
                cur = []
            groups.append([k])
            continue
        cur.append(k)
        if len(cur) == n:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    notes.append(f"cross-page calibration recorded for {origin}: batches of up to {n} page(s)")
    return groups, notes


def render_script(path: Path, *, form: str | None, stub: bool | None, cfg, plan: dict | None,
                  out_dir: str | None) -> dict:
    """Wrap a body with ltjs.py (guard, save watcher, form, stub)."""
    try:
        from ltcore import ltjs  # noqa: PLC0415
    except ImportError as e:
        raise Refusal(f"cannot render {path.name}: ltcore.ltjs is not available ({e}); "
                      f"run `ltjs.py render {path}` yourself or pass --no-render")
    try:
        return ltjs.render(str(path), form=form, stub=stub, net=True, plan=plan, cfg=cfg,
                           out_dir=out_dir)
    except Exception as e:  # ltjs.RenderRefused and anything else
        raise Refusal(f"ltjs.py refused to render {path.name}: {e}")


def write_scripts(pay: dict, out: Path, ppath: Path, cfg, st, *, requested: int, lib_url: str | None,
                  form: str | None, stub: bool | None, render: bool, prefix: str | None = None,
                  force_uncalibrated: bool = False, only_singles: bool = False,
                  only_pages: set[int] | None = None) -> dict:
    """Write the bodies (and their rendered files). Returns a manifest dict.

    A call that can calibrate the origin gets a pending ticket in calibration.json: the
    first page when the origin has no save record (on the fallback wait), or the 2-page
    trial batch when it has a save record and no cross_page record."""
    psha = sha256(ppath)[:16]
    full_sha = sha256(ppath)
    if pay.get("source_page_kinds"):
        pay = dict(pay, _source_payload_sha256=full_sha)
    mid = pay["module_id"]
    origin = pay.get("origin")
    prefix = prefix or ("_replace" if pay["mode"] == "replace" else "_fill")
    cal = load_calibration(st.calibration_path)
    save_rec, cross, notes = calibration_state(cal, origin)
    if force_uncalibrated:
        save_rec, cross = None, None
    sw, swnotes = save_wait(cfg, save_rec)
    notes += swnotes
    cap = int(cfg.get("fill.max_pages_per_call") or 1)
    groups, gnotes = group_pages(pay, requested, cap, save_rec, cross, only_singles, only_pages)
    notes += gnotes
    how = (f"// Served build: needs `emit_fill.py serve` reachable at {lib_url}; it checks both files' sha256.\n"
           if lib_url else "")
    for page, pg in pay["pages"].items():
        for job in pg["jobs"]:
            job_hash(job)
        if not pg["jobs"] and pg.get("todo"):
            notes.append(f"page {page}: no script, {len(pg['todo'])} todo by hand: "
                         + ", ".join(f"{t['item']} {t['kind']}" for t in pg["todo"]))

    # Which call carries a calibration ticket.
    trial: dict[int, str] = {}
    if not origin:
        notes.append("the capture has no origin (module.json source_url); calibration is off for these scripts")
    elif groups and save_rec is None:
        trial[0] = "save"
    elif groups and cross is None:
        for i, g in enumerate(groups):
            if len(g) == 2:
                trial[i] = "cross_page"
                break

    calls, tickets = [], []
    plan = pay.get("plan")
    out_dir = cfg.get("browser.js_tmp_dir") or None

    def emit(name: str, kind: str, pages: list[str], head: str, call: str, ticket: str | None = None) -> None:
        body = head + how + script_body(call, lib_url)
        path = out / name
        path.write_text(body, encoding="utf-8")
        rec = {"name": name, "kind": kind, "pages": [int(p) for p in pages], "source": str(path),
               "bytes": len(body), "rendered": None}
        if ticket:
            rec["ticket"] = ticket
        if render:
            info = render_script(path, form=form, stub=stub, cfg=cfg, plan=plan, out_dir=out_dir)
            rec["rendered"] = info.get("path")
            rec["form"] = info.get("form")
            rec["sha256"] = info.get("sha256")
        calls.append(rec)

    k = 0
    for gi, g in enumerate(groups):
        jobs_n = sum(len(pay["pages"][p]["jobs"]) for p in g)
        ticket = None
        if gi in trial:
            ticket = issue_ticket(st, origin, kind=trial[gi], module_id=mid, pages=[int(p) for p in g],
                                  items=[j["item"] for p in g for j in pay["pages"][p]["jobs"]],
                                  payload_sha=full_sha, explicit=force_uncalibrated)
            tickets.append({"ticket": ticket, "kind": trial[gi], "pages": [int(p) for p in g]})
            notes.append(f"calibration ticket {ticket} ({trial[gi]}) on page(s) {', '.join(g)}: run it, "
                         f"verify from a fresh load, then `emit_fill.py record`")
        if len(g) == 1:
            page = g[0]
            pg = pay["pages"][page]
            name = f"{prefix}-{mid}-{'rat-' if pg.get('rat') else ''}p{page}.js"
            head = (f"// {name}: generated by emit_fill.py from {ppath.name} (sha256 {psha}...). Do not edit; regenerate.\n"
                    f"// {mid} run {pay['run_id']} page {page}: {jobs_n} job(s), {len(pg.get('todo', []))} todo, "
                    f"mode {pay['mode']}, save wait {'calibrated' if sw['calibration'] else 'fallback'}.\n"
                    f"// Run the rendered file on {pg.get('url') or 'page ' + page} with the browser tool's evaluate-file action.\n")
            call = ("LtFillLib.fillPage(" + json.dumps(cfg_page(pay, sw, page, pg, ticket), ensure_ascii=False) + ", "
                    + json.dumps(pg["jobs"], ensure_ascii=False) + ")")
            vname = f"_verify-{mid}-{'rat-' if pg.get('rat') else ''}p{page}.js"
        else:
            k += 1
            name = f"{prefix}-{mid}-b{k}.js"
            first = pay["pages"][g[0]]
            head = (f"// {name}: generated by emit_fill.py from {ppath.name} (sha256 {psha}...). Do not edit; regenerate.\n"
                    f"// {mid} run {pay['run_id']} pages {', '.join(g)} in one call: {jobs_n} job(s), mode {pay['mode']}.\n"
                    f"// Run the rendered file on {first.get('url') or 'page ' + g[0]} with the browser tool's evaluate-file action.\n")
            call = ("LtFillLib.fillPages(" + json.dumps(cfg_batch(pay, sw, g, ticket), ensure_ascii=False) + ", "
                    + json.dumps(batch_plan(pay, g), ensure_ascii=False) + ")")
            vname = f"_verify-{mid}-b{k}.js"
        emit(name, "fill", g, head, call, ticket)
        vpages = {p: [lean(j) for j in pay["pages"][p]["jobs"]] for p in g}
        vhead = (f"// {vname}: generated by emit_fill.py from {ppath.name}. Run it in a fresh load of any "
                 f"page of run {pay['run_id']}\n// (a new tab, or the same tab after a reload) once {name} has returned.\n")
        vcall = ("LtFillLib.verify(" + json.dumps(cfg_base(pay, sw), ensure_ascii=False) + ", "
                 + json.dumps(vpages, ensure_ascii=False) + ")")
        emit(vname, "verify", g, vhead, vcall)

    all_pages = [p for g in groups for p in g]
    if all_pages:
        vname = f"_verify{'-replace' if pay['mode'] == 'replace' else ''}-{mid}.js"
        vpages = {p: [lean(j) for j in pay["pages"][p]["jobs"]] for p in all_pages}
        vhead = (f"// {vname}: generated by emit_fill.py from {ppath.name} (sha256 {psha}...). Do not edit; regenerate.\n"
                 f"// Run it in a fresh load of any page of run {pay['run_id']}. It returns per-item ok or BAD with lengths and hashes only.\n")
        vcall = ("LtFillLib.verify(" + json.dumps(cfg_base(pay, sw), ensure_ascii=False) + ", "
                 + json.dumps(vpages, ensure_ascii=False) + ")")
        emit(vname, "verify-run", all_pages, vhead, vcall)
    manifest = {"payload": str(ppath), "payload_sha256": full_sha, "generated_at": now_utc(),
                "origin": origin, "calibrated": bool(sw["calibration"]),
                "groups": groups, "calls": calls, "tickets": tickets, "notes": notes}
    write_json(out / ("scripts.json" if prefix in ("_fill", "_replace") else f"scripts{prefix}.json"), manifest)
    return manifest


def _module_dir(args, cfg) -> tuple[Path, Path]:
    """(fill-js folder, payload path) from --out/--payload or --module."""
    if args.out:
        out = Path(args.out)
    elif getattr(args, "module", None):
        mod = load_module(args.module)
        out = _open_store(args, cfg).fill_js_dir(mod["module_id"])
    else:
        raise Refusal("give --out <fill-js folder> or --module <module.json>")
    ppath = Path(args.payload) if args.payload else out / "fill-payload.json"
    return out, ppath


def cmd_scripts(args, cfg) -> int:
    out, ppath = _module_dir(args, cfg)
    pay = load(ppath)
    if pay.get("schema") != PAYLOAD_SCHEMA:
        raise Refusal(f"{ppath} is not an emit_fill.py payload (schema {pay.get('schema')!r}); build it again "
                      f"with `emit_fill.py payload`")
    st = _open_store(args, cfg)
    lib_mode = args.lib_mode or cfg.get("browser.lib_mode") or "inline"
    lib_url = args.lib_url
    if lib_mode == "served" and not lib_url:
        lib_url = f"http://127.0.0.1:{int(cfg.get('browser.lib_port') or 8733)}"
    if lib_mode == "inline":
        lib_url = None
    requested = args.pages_per_call if args.pages_per_call is not None else int(cfg.get("fill.max_pages_per_call") or 1)
    stub = True if args.stub else (False if args.no_stub else None)
    out.mkdir(parents=True, exist_ok=True)
    man = write_scripts(pay, out, ppath, cfg, st, requested=requested, lib_url=lib_url, form=args.form,
                        stub=stub, render=not args.no_render, only_pages=parse_pages(args.pages))
    for c in man["calls"]:
        where = c["rendered"] or c["source"]
        print(f"{c['kind']:<10} pages {','.join(map(str, c['pages'])):<10} {c['name']:<34} -> {where}")
    for note in man["notes"]:
        print(f"note: {note}")
    if not args.no_render and man["calls"]:
        print("Run each fill file on its page, save the receipt, then run its _verify file in a fresh load.")
    return 0 if man["calls"] else 2


# ---- Check Answer: explicit opt-in; historical receipts always remain recordable ----
def _check_body():
    source = (BROWSER / 'check_answer.js').read_text(encoding='utf-8')
    return source.rsplit('return await LtCheck.run(__LT_ARGS__.cfg, __LT_ARGS__.jobs);', 1)[0]


def _check_receipts(paths, mid):
    result=[]
    for path in paths or []:
        receipt=_receipt(path)
        if receipt.get('module') != mid or receipt.get('mode') != 'check':
            raise Refusal('check receipt module or mode mismatch')
        if not isinstance(receipt.get('items'),list):
            raise Refusal('check receipt has no item records')
        result.append((Path(path),receipt))
    return result


def _check_job(job, cap, owned=None):
    from ltcore import check_answer as ca
    j=copy.deepcopy(job)
    j.update(type=cap['type'],attempts=cap.get('attempts'),multiselect=ca.multiselect(cap),
             opt_h=[djb2(page_text(o['text'])) for o in cap.get('options',[])],
             pool_h=[djb2(page_text(s)) for s in ca.pool_for(cap)],targets=ca.target_count(cap))
    if j['kind']=='select':
        if (cap.get('constraints') or {}).get('note') not in {'single-select','multiselect'}:
            raise Refusal('shape-changed: capture has no explicit selection policy')
        values=j['want']
    else:
        try:
            pool=ca.ordered_pool(cap,owned['pool_h'])
            values=ca.decode(j['kind'],owned['index_state'])
            j['pool_h']=owned['pool_h']
        except (ValueError,KeyError,TypeError):
            raise Refusal('draft-index-unknown: use a fill receipt with pool hashes and index_state')
    j['draft_state']=ca.encode(j['kind'],values)
    j['draft_h']=ca.hashes(j['kind'],j['draft_state'],pool if j['kind']!='select' else [])
    if str(expected_hash(job))!=j['draft_h'].split('/')[0]:
        raise Refusal('check draft hash does not match payload')
    return j


def cmd_check(args,cfg):
    from ltcore import check_answer as ca
    if cfg.get('fill.check_answer.enabled',False) is not True:
        raise ConfigRefusal('fill.check_answer.enabled is false')
    if ca.VERDICT_MAX_MS+cfg.get('fill.dialog.max_ms')+cfg.get('fill.save_wait.max_ms')>ca.CALL_DEADLINE_MS:
        raise Refusal('check reserve exceeds CALL_DEADLINE_MS')
    mod=load_module(args.module);st=_open_store(args,cfg);mid=mod['module_id']
    out=Path(args.out) if args.out else st.fill_js_dir(mid)
    ppath=Path(args.payload) if args.payload else out/'fill-payload.json'
    pay=load(ppath)
    if pay.get('schema')!=PAYLOAD_SCHEMA or pay.get('module_id')!=mid:
        raise Refusal('check needs this module’s fill-payload.json')
    apath=Path(args.answers or pay['answers']['path']); ans=load(apath)
    check_binding(mod,ans)
    if pay['answers']['sha256']!=sha256(apath) or pay['module']['question_digest']!=question_digest(mod):
        raise Refusal('check payload binding is stale')
    record=page_kinds.load_current(mod,cfg,ans,data_dir=args.data_dir,answers_sha256=sha256(apath))
    if record is None and any(page_kinds.is_candidate(p,rat.policy_from_config(cfg)) for p in mod['pages']):
        raise Refusal('rat-record-stale: run page_kinds.py classify --json')
    statuses={s['page_index']:s for s in page_kinds.page_status(mod,record,policy=rat.policy_from_config(cfg))}
    pl=plan_fill.plan(mod,ans,False,cfg,record=record,data_dir=args.data_dir,answers_sha256=sha256(apath))
    singles=(mod.get('commit_boundaries') or {}).get('single_attempt_items',[])
    pl['single_attempt_items']=singles
    written={}
    saved=st.run_dir(mid)/'fill-results.json'
    for entry in load(saved).get('items',[]) if saved.exists() else []:
        written[entry['item_id']]=entry
    for path in args.fill or []:
        receipt=_receipt(path)
        if receipt.get('module')!=mid:
            raise Refusal('fill receipt module mismatch')
        for r in receipt.get('items',[]):
            written[r['item']]=dict(r)
    receipt_path=Path(args.receipts) if args.receipts else st.run_dir(mid)/'receipts.json'
    verified={}
    for r in load(receipt_path).get('receipts',[]) if receipt_path.exists() else []:
        iid=r['item_id']
        if iid not in verified or r['observed_at']>=verified[iid]['observed_at']:
            verified[iid]=r
    save,_,_=calibration_state(load_calibration(st.calibration_path),origin_of(mod.get('source_url')))
    sw,_=save_wait(cfg,save)
    prior={}
    for path,receipt in _check_receipts(args.resume,mid):
        for r in receipt['items']:
            prior[r['item_id']]=r
    jobs={j['item']:j for p in pay['pages'].values() if not p.get('rat') for j in p['jobs'] if j.get('source')!='rat-sheet'}
    pages={}; refused=[]; only=parse_pages(args.pages)
    for page in mod['pages']:
        index=page['page_index']
        for cap in page['items']:
            iid=cap['item_id'];job=jobs.get(iid);v=verified.get(iid,{})
            owned=written.get(iid,{}).get('result') in {'written','replaced'}
            valid=bool(job and str(v.get('expected_hash'))==str(expected_hash(job)) and v.get('observed_hash')==v.get('expected_hash'))
            reason=ca.eligibility(cap,page,pl,statuses[index],written=owned,verified=valid,calibrated=save is not None)
            if args.resume and iid in prior and prior[iid]['stop']=='not-finished':
                # The preceding call carries ownership over its own saved index state.
                reason=ca.eligibility(cap,page,pl,statuses[index],written=owned,verified=True,calibrated=save is not None)
            if not reason and job and job['kind']!='select' and not {'pool_h','index_state'} <= set(written.get(iid,{})):
                reason='draft-index-unknown'
            if reason or not job:
                refused.append({'item':iid,'page':index,'reason':reason or 'draft-not-written'})
                continue
            if only is not None and index not in only:
                continue
            if args.resume and (iid not in prior or prior[iid]['stop']!='not-finished'):
                continue
            j=_check_job(job,cap,written.get(iid))
            if iid in prior:
                r=prior[iid]
                if r['draft_h']!=j['draft_h']:
                    raise Refusal('resume draft binding differs')
                j['resume']=dict(r,presses_before=r.get('presses_before',0)+len(r['presses']))
            pages.setdefault(str(index),[]).append(j)
    checkplan=plan_fields(pl,mod)
    checkplan['check_answer_keys']=[[int(p),str(j['panel_key'])] for p,js in pages.items() for j in js]
    prior_drafts=[]
    for page in mod['pages']:
        status=statuses[page['page_index']]
        ids=[i['item_id'] for i in page['items'] if written.get(i['item_id'],{}).get('result') in {'written','replaced'}]
        if status['rat'] is True and ids:
            prior_drafts.append({'page':page['page_index'],'kind':status['kind'],'sources':status['sources'],'items':ids})
    checkplan.update(refused=refused,rat_pages=list(statuses.values()),prior_drafts_on_rat=prior_drafts)
    checkplan['policy']={'enabled':True,'config':hashlib.sha256(json.dumps(cfg.values,sort_keys=True).encode()).hexdigest()}
    out.mkdir(parents=True,exist_ok=True)
    write_json(out/'check-plan.json',checkplan)
    all_pages=copy.deepcopy(pages)
    previous_jobs=out/'check-jobs.json'
    if previous_jobs.exists():
        old=load(previous_jobs)
        if args.resume and old.get('answers_sha256')!=sha256(apath):
            raise Refusal('resume job binding differs')
        for p,js in (old.get('pages',{}) if old.get('answers_sha256')==sha256(apath) else {}).items():
            by={j['item']:j for j in js}
            by.update({j['item']:j for j in all_pages.get(p,[])})
            all_pages[p]=list(by.values())
    write_json(previous_jobs,{'module_id':mid,'pages':all_pages,'answers_sha256':sha256(apath)})
    policy={'enabled':True,'config':hashlib.sha256(json.dumps(cfg.values,sort_keys=True).encode()).hexdigest()}
    for p,js in pages.items():
        c=dict(cfg_base(pay,sw),enabled=True,page=int(p),policy=policy,rat_page=statuses[int(p)],
               classification_pin=ca.classification_pin(next(pg for pg in mod['pages'] if pg['page_index']==int(p))),
               drop_text_patterns=cfg.get('capture.drop_text_patterns'),
               single_attempt_keys=pay['pages'][p].get('single_attempt_keys',[]),
               single_attempt_locked=[[s['page_index'],str(next(i['panel_key'] for pg in mod['pages'] for i in pg['items'] if i['item_id']==s['item_id']))] for s in pl['boundary_map'].get('single_attempts_passed',[]) if s['page_index']<=int(p)],
               dialog_max_ms=cfg.get('fill.dialog.max_ms'),resumed=bool(args.resume))
        source=out/f'_check-{mid}-p{p}.js'
        source.write_text(LIB.read_text(encoding='utf-8')+ '\n'+ _check_body()+'\nreturn await LtCheck.run('+json.dumps(c)+','+json.dumps(js)+');\n',encoding='utf-8')
        if not args.no_render:
            r=render_script(source,form=args.form,stub=True if args.stub else False if args.no_stub else None,cfg=cfg,plan=checkplan,out_dir=str(out))
            print(r['path'] if 'path' in r else r)
        else:
            print(source)
    print(f'Check Answer: {sum(map(len,pages.values()))} items on pages '+(', '.join(pages) or 'none'))
    for r in refused:
        print(f"{r['item']}: {r['reason']}")
    return 0 if pages else 2


def cmd_check_verify(args,cfg):
    mod=load_module(args.module);st=_open_store(args,cfg);mid=mod['module_id']
    out=Path(args.out) if args.out else st.fill_js_dir(mid)
    recorded=_check_receipts(args.check,mid)
    jobsdoc=load(out/'check-jobs.json');jobs={j['item']:j for js in jobsdoc['pages'].values() for j in js}
    latest={}
    for _,call in recorded:
        for row in call['items']:
            if row['presses'] or row.get('wrote') or row.get('presses_before'):
                latest[row['item_id']]=(call,row)
    pages={}
    for iid,(call,row) in latest.items():
        if iid not in jobs:
            raise Refusal('check-verify needs the matching check-jobs.json')
        job=dict(jobs[iid],final_h=row['final_h'])
        pages.setdefault(str(call['page']),[]).append(job)
    if not pages:
        return 2
    source=out/f'_verify-check-{mid}.js'
    c={'module_id':mid,'run_id':mod['run_id'],'origin':origin_of(mod['source_url']),'instance':mod.get('instance'),'course_id':mod.get('course_id')}
    source.write_text(LIB.read_text(encoding='utf-8')+'\n'+_check_body()+'\nreturn await LtCheck.verify('+json.dumps(c)+','+json.dumps(pages)+');\n',encoding='utf-8')
    if not args.no_render:
        print(render_script(source,form=args.form,stub=True if args.stub else False if args.no_stub else None,cfg=cfg,plan=load(out/'check-plan.json'),out_dir=str(out)))
    else:
        print(source)
    return 0


def record_check(args,cfg,mod,st):
    mid=mod['module_id'];calls=_check_receipts(args.check,mid)
    if not args.verify:
        raise Refusal('record --check needs --verify from a fresh load')
    ver=_receipt(args.verify)
    if ver.get('ok') is not True or ver.get('fresh_load') is not True or ver.get('module')!=mid or ver.get('mode')!='check-verify':
        raise Refusal('check verify needs a matching fresh-load receipt')
    rows={r[0]:r for r in ver.get('items',[]) if isinstance(r,list) and len(r)>=4}
    latest={};ledger=[];last_write={}
    for path,call in calls:
        if call.get('doc_id') and call.get('doc_id')==ver.get('doc_id'):
            raise Refusal('check verify ran in the checking document')
        ledger.append({'receipt':path.name,'receipt_sha256':sha256(path),'page':call['page'],
                       'policy':call.get('policy'),'rat_page':call.get('rat_page'),'items':call['items']})
        for row in call['items']:
            if row.get('wrote'):last_write[row['item_id']]=row.get('save_signal')
            if row['presses'] or row.get('wrote') or row.get('presses_before'):
                latest[row['item_id']]=(call,row)
    records=[];misses=[];drop=False
    for iid,(call,row) in latest.items():
        vr=rows.get(iid)
        # Shape failures can retain the expected selection hashes. Keep their
        # failed status in the receipt so hash-only report readers also fail.
        observed=vr[3] if vr and vr[1]=='ok' else 'verify-'+str(vr[1]) if vr else 'missing'
        records.append({'item_id':iid,'expected_hash':row['final_h'],'observed_hash':observed,
                        'observed_at':ver.get('observed_at') or now_utc()})
        if observed!=row['final_h']:
            misses.append(iid)
            if last_write.get(iid) in {'model','net'}:
                drop=True
    run=st.run_dir(mid)
    _append_unique(run/'check-answer.json','checks',ledger,lambda r:r['receipt_sha256'])
    _append_unique(run/'receipts.json','receipts',records,lambda r:(r['item_id'],r['observed_at']))
    if drop:
        origin=origin_of(mod['source_url']);cal=load_calibration(st.calibration_path);state=cal.get(origin,{})
        state.pop('save',None)
        _history(state,'check-verify-miss','calibrated check write did not persist')
        write_json(st.calibration_path,cal)
    print(f'Check receipts recorded: {len(ledger)}; verify misses: {len(misses)}')
    for iid in misses:
        print(f'{iid}: '+('post-check verify miss; calibration dropped' if drop else 'lt-changed-state; calibration unchanged'))
    return 0


# ---- approve ----------------------------------------------------------------------------------
def cmd_approve(args, cfg) -> int:
    mod = load_module(args.module)
    st = _open_store(args, cfg)
    items = {i["item_id"]: i for p in mod["pages"] for i in p["items"]}
    it = items.get(args.item)
    if it is None:
        raise Refusal(f"{args.item} is not an item of {mod['module_id']}")
    if it.get("attempts") != "single":
        raise Refusal(f"{args.item} is not single-attempt in the capture; an approval for nothing is a "
                      f"mistake, not a no-op")
    if not (args.quote or "").strip():
        raise Refusal("--quote is empty: ask the user and pass the reply verbatim")
    path = Path(args.approvals) if args.approvals else st.approvals_path(mod["module_id"])
    try:
        rec = approvals.append(path, mod["module_id"], args.item, args.quote, kind="single-attempt",
                               recorded_at=args.recorded_at, question_digest=question_digest(mod))
    except approvals.ApprovalError as e:
        raise Refusal("; ".join(e.problems))
    print(rec["approval_id"])
    print(f"recorded in {path}", file=sys.stderr)
    print(f"answers.json item {args.item}: \"single_attempt_approval\": "
          f"{{\"approved_by\": \"user\", \"approval_id\": \"{rec['approval_id']}\"}}", file=sys.stderr)
    return 0


# ---- record: receipts, save waits and calibration ------------------------------------------------
def _receipt(path: str) -> dict:
    doc = load(path)
    if isinstance(doc, dict) and "result" in doc and isinstance(doc["result"], dict) and "ok" in doc["result"]:
        doc = doc["result"]   # a tool that wraps the returned value
    if not isinstance(doc, dict) or "ok" not in doc:
        raise Refusal(f"{path}: not a receipt returned by a fill, verify or dialog script")
    return doc


def _payload_jobs(paths: list[Path]) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    for p in paths:
        if not p.is_file():
            continue
        raw = p.read_bytes()
        pay = json.loads(raw)
        binding = {"path": str(p.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
        if pay.get("schema") != PAYLOAD_SCHEMA:
            continue
        for pg in pay.get("pages", {}).values():
            for j in pg.get("jobs", []):
                jobs[j["item"]] = dict(j, _source_payload=binding)
    return jobs


def _append_unique(path: Path, key: str, rows: list[dict], ident) -> int:
    doc = load(path) if path.is_file() else {key: []}
    have = {ident(r) for r in doc.get(key, [])}
    added = 0
    for r in rows:
        if ident(r) not in have:
            doc.setdefault(key, []).append(r)
            have.add(ident(r))
            added += 1
    write_json(path, doc)
    return added


def record_dialog(args, cfg, st, mod) -> int:
    """Calibrate the dialog wait from one confirm_dialog.js receipt taken on the fallback wait."""
    rc = _receipt(args.dialog)
    origin = origin_of(mod.get("source_url"))
    if not origin:
        raise Refusal("the capture has no origin (module.json source_url); nothing to calibrate")
    cal = load_calibration(st.calibration_path)
    state = cal.setdefault(origin, {})
    save = rc.get("save") if isinstance(rc.get("save"), dict) else {}
    status = rc.get("ack_status")
    why = []
    if not (rc.get("ok") and rc.get("accepted")):
        why.append(f"no dialog was accepted ({rc.get('refused') or rc.get('fatal')})")
    if rc.get("still_open"):
        why.append("the dialog was still open after the click")
    if save.get("signal") != "fallback":
        why.append(f"the wait was {save.get('signal')!r}, not the fallback")
    if not has_endpoint(rc.get("ack_method"), rc.get("ack_path_template")):
        why.append("no save request was seen after the click")
    if not (isinstance(status, int) and 200 <= status < 300 and status != 202):
        why.append(f"the save request answered {status!r}, not 2xx")
    if why:
        _history(state, "dialog-not-calibrated", "; ".join(why))
        print(f"{origin}: dialog wait stays on the {cfg.get('fill.dialog.fallback_ms')} ms fallback: {'; '.join(why)}")
    else:
        state["dialog"] = {"version": CAL_VERSION, "method": rc["ack_method"], "path_template": rc["ack_path_template"],
                           "verified_at": now_utc(), "receipt_sha256": sha256(Path(args.dialog)),
                           "module_id": mod["module_id"]}
        _history(state, "dialog-calibrated", f"{rc['ack_method']} {rc['ack_path_template']}")
        print(f"{origin}: dialog wait calibrated ({rc['ack_method']} {rc['ack_path_template']})")
    write_json(st.calibration_path, cal)
    return 0


def _pages_of(f: dict) -> list:
    if isinstance(f.get("batch"), list):
        return list(f["batch"])
    return [f.get("page")] if f.get("page") is not None else []


def call_check(f: dict, vrows: dict, expected: set[str]) -> dict:
    """One fill receipt against the fresh-load verify, by exact item id.

    stray: receipt items outside `expected`; failed: items with a failed write result;
    vmiss: items the call wrote or tried to write (written, replaced or a failed result)
    whose verify row is not `ok`; unwritten: expected items the call did not write
    (absent, skipped, refused or already-new); written: items written or replaced;
    pages: the pages with at least one written item."""
    items = [r for r in (f.get("items") or []) if isinstance(r, dict)]
    by = {str(r.get("item")): r for r in items}
    written = [i for i, r in by.items() if r.get("result") in PASS_RESULTS]
    tried = [i for i, r in by.items() if r.get("result") in PASS_RESULTS | FAIL_RESULTS]
    vmiss = []
    for iid in tried:
        row = vrows.get(iid)
        if row is None or len(row) < 2 or row[1] != "ok":
            vmiss.append(f"{iid} {row[1] if row and len(row) > 1 else 'not in the verify'}")
    fpage = f.get("page")
    pages = {r.get("page", fpage) for i, r in by.items() if i in written}
    return {"stray": sorted(set(by) - expected),
            "failed": [f"{i} {r.get('result')}" for i, r in by.items() if r.get("result") in FAIL_RESULTS],
            "vmiss": vmiss, "written": written,
            "unwritten": sorted(i for i in expected if i not in written),
            "pages": {p for p in pages if p is not None}}


def _num(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _unidentified(f: dict) -> str:
    """'' or why keys of a call had no identified save: pending keys, and the Backbone saves
    LtNet saw through a transport it could not identify (receipt model_unidentified)."""
    pend, mu = _num(f.get("keys_pending")), _num(f.get("model_unidentified"))
    if not pend:
        return ""
    s = f"{pend} of {_num(f.get('keys_total')) or '?'} key(s) had no identified save"
    if mu:
        s += f" ({mu} Backbone save(s) through a transport LtNet could not identify)"
    return s


def _wait_reason(f: dict) -> str:
    """save_signal plus the unidentified keys, for history and messages."""
    u = _unidentified(f)
    return f"save_signal {f.get('save_signal')}" + (f"; {u}" if u else "")


def _save_evidence(f: dict) -> list[str]:
    """Why a fill receipt cannot calibrate the save wait ([] when it can).

    The receipt must name the request that acknowledged the first save (source model or net,
    a write method and a path template), every key of the call must have had an identified
    save by the end of the wait, and every acknowledgement must share one endpoint: a
    calibrated wait counts only that method and template, so a key saved elsewhere would
    stay pending on every later call."""
    why = []
    src, method, tpl = f.get("ack_source"), f.get("ack_method"), f.get("ack_path_template")
    if src not in ("model", "net"):
        why.append("no Backbone sync on an identified request and no keyed request acknowledged a write")
    elif not has_endpoint(method, tpl):
        why.append(f"the {src} acknowledgement carries no write method and path template")
    total, saved, pend = _num(f.get("keys_total")), _num(f.get("keys_saved")), _num(f.get("keys_pending"))
    if not (total and saved == total and not pend):
        why.append(_unidentified(f) or f"{saved if saved is not None else 'unknown'} of "
                   f"{total if total is not None else 'unknown'} key(s) had an identified save")
    eps = f.get("ack_endpoints")
    if isinstance(eps, list):
        distinct = sorted({e for e in eps if isinstance(e, str)})
        if len(distinct) > 1:
            why.append(f"saves were acknowledged on {len(distinct)} endpoints ({'; '.join(distinct[:3])}); "
                       f"one save record names one")
    return why


def _fail_cross(state: dict, reason: str, pages=None) -> None:
    state["cross_page"] = {"version": CAL_VERSION, "ok": False, "reason": reason, "at": now_utc()}
    if pages:
        state["cross_page"]["pages"] = pages


def calibrate_from(state: dict, pending: dict, fpath: Path, f: dict, vpath: Path, ver: dict,
                   vrows: dict, mid: str, cfg) -> list[str]:
    """Apply one fill receipt and the fresh-load verify to an origin's calibration state.

    Returns the messages to print. Rules, in order:
      - a changed Lt client build (the fill ran on a stale record, or the verify saw another
        build) drops the save record, a passed cross_page record and every pending ticket;
      - a receipt that echoes a pending ticket of this module uses it up;
      - a save record that no longer holds (the fill receipt names stale_reason
        no-endpoint) is dropped with a passed cross_page record and every pending ticket;
      - a `save` ticket proves the save wait only when the call ran on the fallback wait in
        one document, the verify ran in another document on the same build, every ticket item
        was written, every written item reads ok, and LtNet identified the save of every key
        of the call on one write endpoint (a Backbone sync on a request LtNet observed, or a
        keyed request). The record keeps that request's source, method and path template.
        A save sent through a transport LtNet cannot identify leaves its key pending, so the
        origin stays on the fallback wait. A new save record clears a passed cross_page
        record, which must be proved again on it. An explicit ticket that fails drops save
        and fails cross_page;
      - a `cross_page` ticket proves batching only when the 2-page call ran on the calibrated
        wait, ended model or net, wrote every ticket item on both pages and every item reads
        ok; a miss fails cross_page (one page per call), and drops save when the wait
        claimed saved; items left unwritten make the trial inconclusive;
      - outside a ticket, a miss or an unconfirmed or failed wait on an origin with a save
        record fails cross_page, and drops save when a calibrated wait claimed saved."""
    msg = []
    signal = f.get("save_signal")
    wait_issues = _save_evidence(f) if signal in ("model", "net") else []
    calibrated = bool(f.get("calibrated"))
    tid = f.get("ticket")
    ticket = pending.pop(tid, None) if tid else None
    rsha = hashlib.sha256((sha256(fpath) + sha256(vpath)).encode()).hexdigest()
    verified_at = ver.get("observed_at") or now_utc()
    if ticket is not None and ticket.get("module_id") != mid:
        msg.append(f"ticket {tid} belongs to module {ticket.get('module_id')}; calibration unchanged")
        return msg
    fsig, vsig = f.get("client_sig"), ver.get("client_sig")
    if f.get("calibration_stale") and f.get("stale_reason") == "no-endpoint" and not (fsig and vsig and fsig != vsig):
        state.pop("save", None)
        if (state.get("cross_page") or {}).get("ok"):
            state.pop("cross_page", None)
        dropped = len(pending)
        pending.clear()
        _history(state, "save-record-unusable", f"the save record names no write method and path template; "
                                                f"records and {dropped} pending ticket(s) dropped")
        msg.append("the save record names no write endpoint: calibration records and pending tickets "
                   "dropped; the next scripts calibrate again")
        return msg
    if f.get("calibration_stale") or (fsig and vsig and fsig != vsig):
        state.pop("save", None)
        if (state.get("cross_page") or {}).get("ok"):
            state.pop("cross_page", None)
        dropped = len(pending)
        pending.clear()
        _history(state, "client-changed", f"the Lt client build changed (fill {fsig}, verify {vsig}); "
                                          f"records and {dropped} pending ticket(s) from the old build dropped")
        msg.append("the Lt client build changed: calibration records and pending tickets dropped; "
                   "the next scripts calibrate again")
        return msg
    if not f.get("ok"):
        _history(state, "receipt-fatal", str(f.get("fatal")))
        msg.append(f"{fpath.name}: the fill did not run ({f.get('fatal')}); calibration unchanged")
        return msg
    pages = _pages_of(f)
    has_save = isinstance(state.get("save"), dict)

    if ticket is None:
        if tid:
            msg.append(f"{fpath.name}: ticket {tid} is unknown or already used; calibration unchanged")
        c = call_check(f, vrows, {str(r.get("item")) for r in f.get("items") or [] if isinstance(r, dict)})
        if c["vmiss"] and calibrated and signal in ("model", "net"):
            reason = "after a calibrated wait the fresh-load verify missed " + "; ".join(c["vmiss"][:5])
            state.pop("save", None)
            _fail_cross(state, reason)
            _history(state, "calibration-dropped", reason)
            msg.append("calibration DROPPED: the calibrated wait said saved and a fresh load disagreed; "
                       "back to the fallback wait, one page per call")
        elif c["vmiss"] or c["failed"] or wait_issues or signal in ("unconfirmed", "error"):
            reason = ("save acknowledgement incomplete: " + "; ".join(wait_issues) if wait_issues else
                      _wait_reason(f) if signal in ("unconfirmed", "error")
                      else "fresh-load verify missed " + "; ".join((c["vmiss"] + c["failed"])[:5]))
            if has_save:
                _fail_cross(state, reason)
                _history(state, "cross-page-failed", reason)
                msg.append(f"one page per call from now on: {reason}")
            else:
                _history(state, "fallback-call-missed", reason)
                msg.append(f"the call on page(s) {pages} missed ({reason}); the origin stays on the fallback wait")
        else:
            msg.append(f"call on page(s) {pages} verified")
        return msg

    c = call_check(f, vrows, {str(i) for i in ticket.get("items") or []})
    hard = list(c["vmiss"]) + c["failed"]
    if c["stray"]:
        hard.append("receipt items outside the ticket: " + ", ".join(c["stray"][:5]))
    if not c["written"]:
        hard.append("no item was written in the call; an empty set proves nothing")
    if sorted(map(str, pages)) != sorted(map(str, ticket.get("pages") or [])):
        hard.append(f"receipt pages {pages} differ from the ticket's {ticket.get('pages')}")
    if not (f.get("doc_id") and ver.get("doc_id")):
        hard.append("a receipt carries no doc_id")
    elif f.get("doc_id") == ver.get("doc_id"):
        hard.append("the verify ran in the document that filled (same doc_id)")
    if not (fsig and vsig):
        hard.append("a receipt carries no client_sig (Lt client build)")
    unwritten = c["unwritten"]

    if ticket["kind"] == "save":
        if calibrated:
            hard.append("the call ran on a calibrated wait, not the fallback")
        if signal != "fallback":
            hard.append(f"save_signal {signal}, not fallback")
        hard += _save_evidence(f)
        src = f.get("ack_source")
        explicit = bool(ticket.get("explicit"))
        if hard or (unwritten and explicit):
            why = hard + ([f"ticket items not written: {', '.join(unwritten[:5])}"] if unwritten else [])
            _history(state, "save-not-calibrated", "; ".join(why[:5]))
            if explicit:
                state.pop("save", None)
                _fail_cross(state, "explicit calibration failed: " + "; ".join(why[:3]))
                msg.append(f"explicit calibration FAILED ({'; '.join(why[:3])}); save record dropped, "
                           f"one page per call on the fallback wait")
            else:
                msg.append(f"save wait NOT calibrated ({'; '.join(why[:3])}); the origin stays on the fallback wait")
            return msg
        if unwritten:
            _history(state, "save-inconclusive", "ticket items not written: " + ", ".join(unwritten[:5]))
            msg.append(f"save calibration inconclusive: {len(unwritten)} ticket item(s) were not written "
                       f"({', '.join(unwritten[:3])}); the next scripts carry a new save ticket")
            return msg
        # The identified transport: the request that acknowledged the saves (Backbone sync on
        # it, or the key in it), by source, write method and path template.
        method = str(f.get("ack_method")).strip().upper()
        tpl = str(f.get("ack_path_template")).strip()
        state["save"] = {"version": CAL_VERSION, "source": src, "method": method,
                         "path_template": tpl, "client_sig": fsig,
                         "keys_acknowledged": _num(f.get("keys_saved")),
                         "verified_at": verified_at, "receipt_sha256": rsha, "module_id": mid,
                         "pages": pages, "ticket": tid}
        if explicit or (state.get("cross_page") or {}).get("ok"):
            state.pop("cross_page", None)
        _history(state, "save-calibrated", f"{src} {method} {tpl}")
        msg.append(f"save wait calibrated: {src} {method} {tpl}"
                   + ". The next `emit_fill.py scripts` waits on each key's own save"
                   + ("" if (state.get("cross_page") or {}).get("ok") is False else " and emits one 2-page trial batch")
                   + ".")
        return msg

    # cross_page
    if not calibrated:
        hard.append("the batch ran on the fallback wait, not the calibrated one")
    if len(pages) < 2:
        hard.append("the call held one page, not a batch")
    if signal not in ("model", "net"):
        hard.append(_wait_reason(f))
    hard += wait_issues
    if hard:
        reason = "; ".join(hard[:5])
        if c["vmiss"] and signal in ("model", "net"):
            state.pop("save", None)
            reason = "the calibrated wait said saved and a fresh load missed " + "; ".join(c["vmiss"][:5])
        _fail_cross(state, reason, pages)
        _history(state, "cross-page-failed", reason)
        msg.append(f"cross-page trial FAILED ({reason}); one page per call from now on")
        return msg
    missing_pages = sorted(set(map(str, pages)) - set(map(str, c["pages"])))
    if unwritten or missing_pages:
        why = ([f"ticket items not written: {', '.join(unwritten[:5])}"] if unwritten else []) + \
              ([f"no item written on page(s) {', '.join(missing_pages)}"] if missing_pages else [])
        _history(state, "cross-page-inconclusive", "; ".join(why))
        msg.append(f"cross-page trial inconclusive ({'; '.join(why)}); the next scripts emit another trial batch")
        return msg
    state["cross_page"] = {"version": CAL_VERSION, "ok": True, "pages": pages, "verified_at": verified_at,
                           "receipt_sha256": rsha, "client_sig": fsig, "ticket": tid}
    _history(state, "cross-page-calibrated", f"pages {pages}")
    msg.append(f"cross-page calibrated on pages {pages}: batches of up to fill.max_pages_per_call "
               f"({cfg.get('fill.max_pages_per_call')}) from now on")
    return msg


def cmd_record(args, cfg) -> int:
    mod = load_module(args.module)
    st = _open_store(args, cfg)
    if getattr(args, 'check', None):
        return record_check(args,cfg,mod,st)
    if args.dialog:
        return record_dialog(args, cfg, st, mod)
    if not args.fill or not args.verify:
        raise Refusal("record needs --fill RECEIPT [...] and --verify RECEIPT (or --dialog RECEIPT)")
    fills = [(Path(p), _receipt(p)) for p in args.fill]
    vpath = Path(args.verify)
    ver = _receipt(args.verify)
    mid = mod["module_id"]
    for p, f in fills:
        if f.get("module") not in (None, mid):
            raise Refusal(f"{p}: a receipt for module {f.get('module')!r}, not {mid!r}")
    if ver.get("module") not in (None, mid):
        raise Refusal(f"{vpath}: a verify receipt for module {ver.get('module')!r}, not {mid!r}")
    if ver.get("ok") is not True:
        raise Refusal(f"{vpath}: the verify did not run ({ver.get('fatal')}); nothing recorded")
    if ver.get("fresh_load") is not True:
        raise Refusal(f"{vpath}: fresh_load is not true. A verify in the document that filled proves "
                      f"nothing about the server; load the page again and run the _verify file there")

    fjs = st.fill_js_dir(mid)
    ppaths = [Path(p) for p in args.payload] if args.payload else [
        fjs / "fill-payload.json", fjs / "replace-payload.json", fjs / "calibrate-payload.json"]
    if any(rat.within(p, root) for p in ppaths for _, root in rat.effective_roots(rat.policy_from_config(cfg))):
        raise Refusal("RAT receipt binding: payload path is inside a quarantined RAT root")
    jobs = _payload_jobs(ppaths)
    vrows = {str(row[0]): row for row in ver.get("items", []) if isinstance(row, list) and row}
    observed_at = ver.get("observed_at") or now_utc()

    receipts, waits, misses = [], [], []
    for p, f in fills:
        waits.append({"receipt": p.name, "receipt_sha256": sha256(p), "pages": _pages_of(f),
                      "save_signal": f.get("save_signal"), "waited_ms": f.get("waited_ms"),
                      "keys_total": f.get("keys_total"), "keys_saved": f.get("keys_saved"),
                      "keys_pending": f.get("keys_pending"), "save_errors": f.get("save_errors"),
                      "calibrated": bool(f.get("calibrated")), "ok": bool(f.get("ok")),
                      "fatal": f.get("fatal")})
        for rec in f.get("items") or []:
            if rec.get("result") not in WRITE_RESULTS:
                continue
            iid = rec["item"]
            row = vrows.get(iid)
            if iid in jobs and jobs[iid].get("source") == "rat-sheet":
                binding = jobs[iid]["_source_payload"]
                if (f.get("source_payload_sha256") != binding["sha256"] or
                        ver.get("source_payload_sha256") != binding["sha256"]):
                    raise Refusal("RAT receipt binding: fill and verify must name the executed payload")
            if row is None or row[1] != "ok":
                misses.append(f"{iid} {row[1] if row else 'not in the verify'}")
            if iid in jobs and row is not None and len(row) >= 4:
                receipts.append({"item_id": iid, "expected_hash": expected_hash(jobs[iid]),
                                 "observed_hash": row[3], "observed_at": observed_at,
                                 **({"source_payload": jobs[iid]["_source_payload"]} if jobs[iid].get("source") == "rat-sheet" else {})})
            elif row is not None and row[1] in ("no-page", "no-panel") and iid in jobs:
                receipts.append({"item_id": iid, "expected_hash": expected_hash(jobs[iid]),
                                 "observed_hash": row[1], "observed_at": observed_at,
                                 **({"source_payload": jobs[iid]["_source_payload"]} if jobs[iid].get("source") == "rat-sheet" else {})})

    run_dir = st.run_dir(mid)
    nr = _append_unique(run_dir / "receipts.json", "receipts", receipts,
                        lambda r: (r["item_id"], r["observed_at"]))
    nw = _append_unique(run_dir / "save-waits.json", "save_waits", waits,
                        lambda r: r["receipt_sha256"])
    _append_unique(run_dir / 'fill-results.json', 'items',
        [{'item_id':r['item'], 'result':r.get('result'), 'receipt_sha256':sha256(p), **{k:r[k] for k in ('pool_h','index_state') if k in r}} for p,f in fills for r in f.get('items',[])],
        lambda r:(r['item_id'],r['receipt_sha256']))
    print(f"{run_dir / 'receipts.json'}: {nr} receipt(s) added; {run_dir / 'save-waits.json'}: {nw} wait(s) added")

    origin = origin_of(mod.get("source_url"))
    if not origin:
        print("the capture has no origin (module.json source_url); calibration unchanged")
    else:
        cal = load_calibration(st.calibration_path)
        state = cal.setdefault(origin, {})
        pending = {t["ticket"]: t for t in state.get("pending", []) if isinstance(t, dict) and t.get("ticket")}
        msg = []
        for p, f in fills:
            msg += calibrate_from(state, pending, p, f, vpath, ver, vrows, mid, cfg)
        state["pending"] = list(pending.values())
        write_json(st.calibration_path, cal)
        for m in msg:
            print(f"{origin}: {m}")
    if misses:
        print("not ok in the fresh load: " + "; ".join(misses))
    return 0


# ---- calibrate: one explicit item ------------------------------------------------------------------
def cmd_calibrate(args, cfg) -> int:
    mod, ans = load_module(args.module), load(args.answers)
    st = _open_store(args, cfg)
    pay = build_jobs(mod, ans, args, cfg, approvals_path=_approvals_path(args, st, mod),
                     only_items={args.item})
    jobs = [j for p in pay["pages"].values() for j in p["jobs"]]
    if not jobs:
        why = next((r["reason"] for r in pay["refused"] + pay["not_emitted"] if r["item"] == args.item),
                   "not drafted in answers.json")
        raise Refusal(f"{args.item} cannot calibrate: {why}")
    out = Path(args.out) if args.out else st.fill_js_dir(mod["module_id"])
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "calibrate-payload.json"
    _write_payload(pay, dest, cfg, force=True)
    pay = load(dest)
    stub = True if args.stub else (False if args.no_stub else None)
    man = write_scripts(pay, out, dest, cfg, st, requested=1, lib_url=None, form=args.form, stub=stub,
                        render=not args.no_render, prefix="_calibrate", force_uncalibrated=True,
                        only_singles=True)
    for c in man["calls"]:
        print(f"{c['kind']:<10} pages {','.join(map(str, c['pages'])):<6} {c['name']:<40} -> {c['rendered'] or c['source']}")
    for note in man["notes"]:
        print(f"note: {note}")
    page = next(k for k, v in pay["pages"].items() if v["jobs"])
    print(f"calibration item {args.item} on page {page}: run the fill file there on the fallback wait, "
          f"run its _verify file in a fresh load, then:\n  emit_fill.py record --module {args.module} "
          f"--fill <fill receipt> --verify <verify receipt> --payload {dest}")
    return 0


# ---- dialog arguments ------------------------------------------------------------------------------
def cmd_dialog_args(args, cfg) -> int:
    """Write the __LT_ARGS__ file for confirm_dialog.js: action accept, the panel, the dialog
    wait from config fill.dialog.* and save_wait.quiet_ms, and the origin's dialog record."""
    mod = load_module(args.module)
    st = _open_store(args, cfg)
    origin = origin_of(mod.get("source_url"))
    cal = load_calibration(st.calibration_path)
    rec = (cal.get(origin or "", {}) or {}).get("dialog")
    if isinstance(rec, dict) and rec.get("version") != CAL_VERSION:
        rec = None
    doc = {"action": "accept", "panel": str(args.panel),
           "max_ms": int(cfg.get("fill.dialog.max_ms")), "fallback_ms": int(cfg.get("fill.dialog.fallback_ms")),
           "quiet_ms": int(cfg.get("fill.save_wait.quiet_ms")),
           "calibration": ({"method": rec.get("method"), "path_template": rec.get("path_template")}
                           if isinstance(rec, dict) else None)}
    dest = Path(args.out) if args.out else st.logs_dir(mod["module_id"]) / f"dialog-args-{args.panel}.json"
    write_json(dest, doc)
    print(dest)
    print(f"render: ltjs.py render confirm_dialog --args-file {dest}"
          + ("" if doc["calibration"] else f"  (no dialog calibration for {origin}: {doc['fallback_ms']} ms wait)"),
          file=sys.stderr)
    return 0


# ---- model answer ----------------------------------------------------------------------------------
def cmd_save_model_answer(args, cfg) -> int:
    if not cfg.get("model_answer.read"):
        raise ConfigRefusal("model_answer.read is false in the config: Lt's model answer is not read or stored")
    mod = load_module(args.module)
    st = _open_store(args, cfg)
    res = _receipt(getattr(args, "from"))
    if res.get("ok") is not True or not isinstance(res.get("rows"), list):
        raise Refusal(f"{getattr(args, 'from')}: not a model_answer.js result ({res.get('fatal')})")
    answers: dict[str, dict] = {}
    for row in res["rows"]:
        if not (isinstance(row, list) and len(row) >= 4):
            raise Refusal("a model-answer row is not [panel_key, page, kind, status, ...]")
        key, page, kind, status = row[:4]
        values = row[4:]
        if kind == "text":
            values = ["".join(values)] if values else []
        answers[str(key)] = {"page": page, "kind": kind, "status": status, "values": values}
    dest = Path(args.out) if args.out else st.model_answer_path(mod["module_id"])
    doc = {"schema_version": 1, "module_id": mod["module_id"], "read_at": res.get("read_at") or now_utc(),
           "source": "lt-model-answer", "answers": answers}
    write_json(dest, doc)
    ok = sum(1 for a in answers.values() if a["status"] == "ok")
    print(f"{dest}: {len(answers)} panel(s), {ok} with a model answer")
    return 0


# ---- serve (served lib mode) ----------------------------------------------------------------------------
def cmd_serve(args, cfg) -> int:
    """Serve fill_lib.js and probe_panels.js, GET only, on 127.0.0.1, to allowed origins only."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: PLC0415
    files = {"fill_lib.js": LIB, "probe_panels.js": PROBE}
    allowed = {o.rstrip("/") for o in ([cfg.get("lt.origin")] + list(args.allow_origin or [])) if o}
    if not allowed:
        raise Refusal("no allowed origin: set lt.origin in the config or pass --allow-origin")
    handler = make_serve_handler(files, allowed, BaseHTTPRequestHandler)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port or int(cfg.get("browser.lib_port") or 8733)), handler)
    print(f"serving fill_lib.js and probe_panels.js on http://127.0.0.1:{srv.server_address[1]} to "
          f"{', '.join(sorted(allowed))}; Ctrl-C stops")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def make_serve_handler(files: dict, allowed: set, base):
    """The request handler for `serve`: an Origin not on the list gets 403 and no CORS headers."""

    class Handler(base):
        def _origin(self):
            o = self.headers.get("Origin")
            return o if o and o.rstrip("/") in allowed else None

        def _reply(self, code, data=b"", ctype="text/plain", origin=None):
            self.send_response(code)
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_OPTIONS(self):
            o = self._origin()
            if not o:
                return self._reply(403, b"origin not allowed")
            self._reply(204, origin=o)

        def do_GET(self):
            o = self._origin()
            if not o:
                return self._reply(403, b"origin not allowed")
            f = files.get(self.path.lstrip("/").split("?")[0])
            if f is None:
                return self._reply(404, b"not found", origin=o)
            self._reply(200, f.read_bytes(), "application/javascript; charset=utf-8", origin=o)

        def _refuse(self):
            self._reply(405, b"GET only")

        do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

        def log_message(self, *a):
            pass

    return Handler


def cmd_djb2(args, cfg) -> int:
    for t in args.text:
        pt = page_text(t)
        print(json.dumps({"text": t, "page_text": pt, "h": djb2(pt)}, ensure_ascii=False))
    return 0


def _common(p) -> None:
    p.add_argument("--config", help="config file (default: the lt-suite lookup order)")
    p.add_argument("--data-dir", help="data folder (default: LT_SUITE_DATA, paths.data_dir, platform)")


def _render_opts(p) -> None:
    p.add_argument("--form", choices=["expression", "function"],
                   help="what your browser tool evaluates (default from browser.adapter)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--stub", action="store_true", help="base64 plus sha256 stub, for tools that take inline text")
    g.add_argument("--no-stub", action="store_true", help="never a stub")
    p.add_argument("--no-render", action="store_true", help="write the bodies only; render them with ltjs.py yourself")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("payload", help="answers.json + module.json -> fill-payload.json and plan.json")
    p.add_argument("--module", required=True)
    p.add_argument("--answers", required=True)
    p.add_argument("--out", help="fill-js folder (default <data>/runs/<module_id>/fill-js)")
    p.add_argument("--pages", help="e.g. 4-11 or 4,6,7; narrows the plan, never widens it")
    p.add_argument("--exclude", nargs="*", default=[])
    p.add_argument("--anchors", help="{item_id: {label: locationIndex | {loc, x, y}}} for labelling items")
    p.add_argument("--approvals", help="approvals.json (default <data>/runs/<module_id>/approvals.json)")
    p.add_argument("--replace", action="store_true",
                   help="overwrite text this suite wrote earlier, only where Lt still holds it")
    p.add_argument("--against", help="with --replace: the earlier fill-payload.json (or answers.json)")
    p.add_argument("--include-rat", action="store_true", help="passed to plan_fill.py")
    p.add_argument("--force", action="store_true", help="overwrite an existing payload file")
    _common(p)
    s = sub.add_parser("scripts", help="fill-payload.json -> fill and verify scripts, rendered")
    s.add_argument("--out", help="fill-js folder (default from --module)")
    s.add_argument("--module", help="module.json, to find the default fill-js folder")
    s.add_argument("--payload", help="default: <out>/fill-payload.json")
    s.add_argument("--pages-per-call", type=int, help="pages per browser call (default fill.max_pages_per_call); "
                                                      "above 1 only after cross-page calibration")
    s.add_argument("--pages", help="e.g. 5-9: scripts for these pages only (the rest after a calibration)")
    s.add_argument("--lib-mode", choices=["inline", "served"], help="default browser.lib_mode")
    s.add_argument("--lib-url", help="served mode: where `emit_fill.py serve` listens")
    _render_opts(s)
    _common(s)
    for name,func in (("check",cmd_check),("check-verify",cmd_check_verify)):
        c = sub.add_parser(name)
        c.add_argument("--module",required=True)
        c.add_argument("--out")
        if name=="check":
            c.add_argument("--answers")
            c.add_argument("--payload")
            c.add_argument("--fill",nargs="+")
            c.add_argument("--receipts")
            c.add_argument("--resume",nargs="+")
            c.add_argument("--pages")
        else:
            c.add_argument("--check",nargs="+",required=True)
        _render_opts(c)
        _common(c)
    a = sub.add_parser("approve", help="record the user's approval of one single-attempt item")
    a.add_argument("--module", required=True)
    a.add_argument("--item", required=True)
    a.add_argument("--quote", required=True, help="the user's reply, verbatim")
    a.add_argument("--approvals", help="default <data>/runs/<module_id>/approvals.json")
    a.add_argument("--recorded-at", help=argparse.SUPPRESS)
    _common(a)
    r = sub.add_parser("record", help="fill and verify receipts -> receipts.json, save-waits.json, calibration")
    r.add_argument("--module", required=True)
    r.add_argument("--fill", nargs="+", help="fill receipt(s): the JSON the fill script returned")
    r.add_argument("--verify", help="the verify receipt from a fresh load")
    r.add_argument("--payload", nargs="+", help="payload(s) holding the jobs (default: those in fill-js)")
    r.add_argument("--check", nargs="+", help="check receipt(s), preserving historical policy")
    r.add_argument("--dialog", help="a confirm_dialog.js receipt, to calibrate the dialog wait")
    _common(r)
    c = sub.add_parser("calibrate", help="a one-item payload and script on the fallback wait")
    c.add_argument("--module", required=True)
    c.add_argument("--answers", required=True)
    c.add_argument("--item", required=True)
    c.add_argument("--out", help="fill-js folder (default <data>/runs/<module_id>/fill-js)")
    c.add_argument("--anchors")
    c.add_argument("--approvals")
    _render_opts(c)
    _common(c)
    d = sub.add_parser("dialog-args", help="__LT_ARGS__ file for confirm_dialog.js (one upload's dialog)")
    d.add_argument("--module", required=True)
    d.add_argument("--panel", required=True, help="the panel key upload.js wrote")
    d.add_argument("--out", help="default <data>/runs/<module_id>/logs/dialog-args-<panel>.json")
    _common(d)
    m = sub.add_parser("save-model-answer", help="store model_answer.js output as model-answer.json")
    m.add_argument("--module", required=True)
    m.add_argument("--from", required=True, help="the JSON model_answer.js returned")
    m.add_argument("--out", help="default <data>/runs/<module_id>/model-answer.json")
    _common(m)
    v = sub.add_parser("serve", help="served mode: fill_lib.js and probe_panels.js on 127.0.0.1")
    v.add_argument("--port", type=int)
    v.add_argument("--allow-origin", action="append", help="an origin besides lt.origin (repeatable)")
    _common(v)
    h = sub.add_parser("djb2", help="print page_text and djb2 of each TEXT (HTML allowed)")
    h.add_argument("text", nargs="+")
    _common(h)
    args = ap.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    try:
        return {"check":cmd_check,"check-verify":cmd_check_verify,"payload": cmd_payload, "scripts": cmd_scripts, "approve": cmd_approve,
                "record": cmd_record, "calibrate": cmd_calibrate, "dialog-args": cmd_dialog_args,
                "save-model-answer": cmd_save_model_answer, "serve": cmd_serve,
                "djb2": cmd_djb2}[args.cmd](args, cfg)
    except ConfigRefusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 4
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, TypeError):
        print("REFUSED: invalid input or receipt", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
