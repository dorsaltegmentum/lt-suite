# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared page classification store for RAT filling and Check Answer.

Public API for consumers:
* page_digest(page), classification_digest(page): stable question/policy inputs.
* classify(module, policy, record=None, answers=None, path=None): refresh safe
  evidence using permitted RAT reads; validate and atomically write when path set.
* current(module, record, policy, answers=None): metadata-only freshness check.
* load_current(module, cfg=None, answers=None, path=None, data_dir=None): validated
  fresh record or None. Never reads sheets; default path is the module's run.
  File consumers pass answers_sha256 (the exact answers file hash); in-memory
  callers omit it to use a canonical document hash.
* page_status(module, record, *, policy=None): ordered safe per-page projection, no scans or reads.
* mark/choose: bind explicit user replies to current evidence and page digests.

A consumer must call current/load_current before page_status or rat.decide when
external inputs may have changed. page_status itself checks page digests. No
answer-bearing match mappings or sheet metadata values enter this store.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ltcore import config, rat, store
from ltcore.digest import canonical_bytes, item_view, normalise_text

NEEDS = ["schemas/page-kinds.schema.json"]
SCHEMA = Path(__file__).resolve().parent.parent / NEEDS[0]


def page_digest(page) -> str:
    """Hash question-bearing item views in capture order, ignoring answer state."""
    return rat.sha([item_view(i) for i in page.get("items") or []])


def classification_digest(page) -> str:
    """Hash title, content, flags and attempt settings, ignoring answer state."""
    return rat.sha({"title": page.get("title"),
                    "content_text": [normalise_text(p.get("text")) for p in page.get("content") or []],
                    "rat_candidate": page.get("rat_candidate"), "is_report_page": page.get("is_report_page"),
                    "items": [{k: i.get(k) for k in ("item_id", "attempts", "assessment_type")}
                              for i in page.get("items") or []]})


def _pin(page):
    return {"page_index": page["page_index"], "page_digest": page_digest(page),
            "classification_digest": classification_digest(page)}


def _text_evidence(page, policy):
    text = "\n".join(p.get("text") or "" for p in page.get("content") or [])
    kinds = [k for k, pattern in policy.get("kind_markers", rat.DEFAULT_MARKERS).items() if re.search(pattern, text)]
    return bool(kinds), kinds[0] if len(kinds) == 1 else None


def is_candidate(page, policy=None) -> bool:
    """Test shape, capture hint or configured content markers; exclude reports."""
    items = page.get("items") or []
    return bool(items and not page.get("is_report_page") and
                (page.get("rat_candidate") or (len(items) >= 2 and all(i.get("type") == "multiple-choice" for i in items))
                 or _text_evidence(page, policy or rat.DEFAULT_POLICY)[0]))


def _page_rows(module, policy, answers):
    ans = {a["item_id"]: a for a in (answers or {}).get("items") or []}
    rows = []
    for p in module.get("pages") or []:
        if not is_candidate(p, policy):
            continue
        has_text, text_kind = _text_evidence(p, policy)
        states = [ans.get(i["item_id"], {}) for i in p["items"]]
        answer_kind = None
        if all(a.get("status") == "skipped" and a.get("skip_reason") == "irat-policy" for a in states):
            answer_kind = "irat"
        elif all(a.get("status") == "skipped" and a.get("skip_reason") == "trat-answers-unavailable" for a in states):
            answer_kind = "trat"
        rows.append(dict(_pin(p), text_kind=text_kind, answer_kind=answer_kind,
                         drafted=sum(a.get("status") == "drafted" for a in states),
                         sources=(["page-text"] if has_text else []) + (["answers-json"] if answer_kind else [])))
    return rows


def _policy(policy):
    out = {"irat": policy["irat"], "trat": policy["trat"], "markers": rat.sha(policy.get("kind_markers", rat.DEFAULT_MARKERS))}
    if "_week_pattern" in policy:
        out["week_pattern_sha256"] = rat.sha(policy["_week_pattern"])
    return out


def _answers_hash(answers):
    return rat.sha(answers) if answers is not None else None


def validate_record(record):
    """Validate store structure with the standard-library schema subset."""
    errors = []
    config._validate(record, json.loads(SCHEMA.read_text(encoding="utf-8")), "page-kinds", errors)
    if errors:
        raise rat.RatError("invalid page-kinds record")
    for name in ("marks", "choices", "matches"):
        keys = [(r["page_index"], r.get("evidence", {}).get("root"), r.get("evidence", {}).get("name")) for r in record[name]]
        if len(keys) != len(set(keys)):
            raise rat.RatError("duplicate page-kinds entry")
    return record


def atomic_write(path, record):
    """Validate before replacing a store. Never expose a partially written record."""
    validate_record(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, scratch = tempfile.mkstemp(prefix=".page-kinds-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(scratch, path)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def record_hash(record):
    """Hash exact store serialization, matching atomic_write and CLI files."""
    return hashlib.sha256((json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode()).hexdigest() if record else None


def page_current(page, record):
    if not record or record.get("_stale"):
        return False
    pin = _pin(page)
    return any(all(r.get(k) == v for k, v in pin.items()) for r in (record.get("scan") or {}).get("pages") or [])


def current(module, record, policy, answers=None, *, answers_sha256=None) -> bool:
    """Check policy, answers, page pins and root stat listings without reading sheets."""
    if not record or record.get("module_id") != module.get("module_id"):
        return False
    try:
        validate_record(record)
        scan = record["scan"]
        if scan["policy"] != _policy(policy) or scan["answers_sha256"] != (answers_sha256 or _answers_hash(answers)):
            return False
        if scan["pages"] != _page_rows(module, policy, answers):
            return False
        if scan["roots_digest"] != rat.roots(policy)["roots_digest"]:
            return False
        for choice in record["choices"]:
            if choice["roots_digest"] != scan["roots_digest"] or choice["policy_sha256"] != rat.policy_hash(policy):
                return False
            named = choice.get("sheet")
            if named and not any(m["page_index"] == choice["page_index"] and
                all(m["evidence"].get(k) == named.get(k) for k in ("root", "name", "sha256"))
                for m in record["matches"]):
                return False
        pages = {p["page_index"]: p for p in module["pages"]}
        for name in ("marks", "choices", "matches"):
            if any(r["page_index"] not in pages or any(r[k] != v for k, v in _pin(pages[r["page_index"]]).items()) for r in record[name]):
                return False
        return True
    except (rat.RatError, KeyError, TypeError, OSError):
        return False


def load_current(module, cfg=None, answers=None, path=None, data_dir=None, *, answers_sha256=None):
    """Load fresh store or return None. No sheet reads, no automatic refresh."""
    st = store.Store(cfg, data_dir=data_dir)
    path = Path(path) if path else st.run_dir(module["module_id"]) / "page-kinds.json"
    try:
        rat.validate_roots(rat.policy_from_config(cfg), data_dir=st.data_dir)
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if current(module, record, rat.policy_from_config(cfg), answers, answers_sha256=answers_sha256) else None
    except (OSError, ValueError):
        return None


def _live_entries(entries, pages):
    return [copy.deepcopy(e) for e in entries if e.get("page_index") in pages and
            all(e.get(k) == v for k, v in _pin(pages[e["page_index"]]).items())]


def classify(module, policy=None, record=None, answers=None, *, path=None, now=None, answers_sha256=None):
    """Refresh shared evidence; only rat.read_sheet opens authorized synthetic/user sheets."""
    policy = policy or rat.DEFAULT_POLICY
    record = record or {}
    if record.get("module_id") != module.get("module_id"):
        record = {}
    pages = {p["page_index"]: p for p in module.get("pages") or [] if is_candidate(p, policy)}
    root_report = rat.roots(policy)
    marks = _live_entries(record.get("marks", []), pages)
    choices = [e for e in _live_entries(record.get("choices", []), pages)
               if e.get("roots_digest") == root_report["roots_digest"] and e.get("policy_sha256") == rat.policy_hash(policy)]
    preliminary = {"module_id": module["module_id"], "marks": marks, "choices": choices, "matches": [],
                   "scan": {"pages": _page_rows(module, policy, answers)}}
    kinds = {s["page_index"]: s["kind"] for s in page_status(module, preliminary, policy=policy)}
    existing = any(r["exists"] for r in root_report["roots"])
    allowed = {k: existing and (policy[k] == "from_dir" or (policy[k] == "ask" and any(
        c["fill"] == "sheet" and (kinds.get(c["page_index"]) == k or
        kinds.get(c["page_index"]) is None and policy["irat"] == policy["trat"]) for c in choices)))
        for k in ("irat", "trat")}
    report, sheets = rat.scan_sheets(policy, allowed)
    stamp = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report["read"] = dict(allowed, why="policy or current sheet choice" if any(allowed.values()) else "no kind allows reading")
    scan = dict(report, made_at=stamp, policy=_policy(policy), answers_sha256=answers_sha256 or _answers_hash(answers),
                pages=_page_rows(module, policy, answers))
    out = {"schema_version": 1, "module_id": module["module_id"], "marks": marks,
           "choices": choices, "scan": scan, "matches": []}
    for idx, page in pages.items():
        matches = [m for s in sheets if (m := rat.match_sheet(s, page, module, week_pattern=policy.get("_week_pattern"))) and m["grade"] != "none"]
        # Determine kind before sheets: declarations never classify a page.
        kind = page_status(module, out, policy=policy)[list(p["page_index"] for p in module["pages"]).index(idx)]["kind"]
        selected, reasons = rat.select_match(matches, kind)
        for m in matches:
            ev = rat.evidence(m)
            ev.pop("selection_sha256", None)
            ev["chosen"] = m is selected
            ev["confirmation_reasons"] = reasons if m is selected else []
            out["matches"].append(dict(_pin(page), kind="rat", source="rat-answers", evidence=ev))
    # A named sheet must still have the bytes that were approved.
    out["choices"] = [c for c in choices if not c.get("sheet") or any(
        all(m["evidence"].get(k) == c["sheet"].get(k) for k in ("root", "name", "sha256"))
        and m["page_index"] == c["page_index"] for m in out["matches"])]
    validate_record(out)
    if path is not None:
        atomic_write(path, out)
    return out


def _classifications(module, record, policy=None) -> list[dict]:
    """Read-only per-page projection. No roots, sheet reads, or filesystem scans.

    Stale/unknown candidates hold checks with rat-unmarked. Marker or full-match
    evidence vetoes Check Answer even after an informed ordinary-page mark.
    """
    rows = []
    for page in module.get("pages") or []:
        idx = page["page_index"]
        scanrow = next((r for r in (record or {}).get("scan", {}).get("pages", []) if r["page_index"] == idx), {})
        candidate = is_candidate(page, policy) or bool(scanrow and not page.get("is_report_page") and page.get("items"))
        shape = len(page.get("items") or []) >= 2 and all(i.get("type") == "multiple-choice" for i in page.get("items") or [])
        status = dict(_pin(page), candidate=candidate, rat_shape=shape, rat_candidate=bool(page.get("rat_candidate")),
                      rat=None if candidate else False, kind=None, sources=[], decision="rat-unclassified" if candidate else "not-rat",
                      check_allowed=not candidate, record_sha256=record_hash(record))
        if candidate and page_current(page, record):
            sources = list(scanrow.get("sources") or [])
            full = any(m["page_index"] == idx and m["evidence"]["grade"] == "full" for m in record["matches"])
            if full:
                sources.append("rat-answers")
            markrow = next((m for m in record["marks"] if m["page_index"] == idx), {})
            if markrow:
                sources.append("user-mark")
            status["sources"] = sources
            kind = markrow.get("kind") or scanrow.get("text_kind") or scanrow.get("answer_kind")
            conflict = markrow.get("kind") == "not-rat" and any(s not in markrow.get("evidence_seen", []) for s in sources if s != "user-mark")
            if conflict:
                status.update(rat=None, kind=None)
            elif kind == "not-rat":
                status.update(rat=False, kind=None, decision="not-rat")
            else:
                status.update(rat=True if sources else None, kind=kind)
            veto = "page-text" in sources or "rat-answers" in sources
            status["check_allowed"] = status["rat"] is False and not veto
            status["check_reason"] = None if status["check_allowed"] else "rat-unmarked" if conflict or status["rat"] is None else "rat-page" if veto or status["rat"] else "rat-unmarked"
        elif candidate:
            status["check_reason"] = "rat-unmarked"
        rows.append(status)
    return rows


def page_status(module, record, *, policy=None) -> list[dict]:
    """Project classification and fill decision without opening files or scanning roots.

    Call current/load_current first when consuming an on-disk store. Check Answer
    must use check_allowed/check_reason, independently of the fill decision.
    Pass the effective policy to preserve custom markers without a current record.
    """
    rows = _classifications(module, record, policy)
    policy = policy or (record or {}).get("scan", {}).get("policy") or rat.DEFAULT_POLICY
    by_index = {p["page_index"]: p for p in module.get("pages") or []}
    for row in rows:
        if row["candidate"]:
            row["decision"] = rat.decide(by_index[row["page_index"]], policy, record)["decision"]
    return rows


def mark(module, record, index, kind, answer, *, now=None):
    """Record a current user mark with exactly the evidence presented to the user."""
    if kind not in {"trat", "irat", "not-rat"}:
        raise rat.RatError("invalid mark")
    page = next((p for p in module["pages"] if p["page_index"] == index), None)
    if page is None or not page_current(page, record):
        raise rat.RatError("unknown or stale page")
    sources = next(s for s in page_status(module, record) if s["page_index"] == index)["sources"]
    record = copy.deepcopy(record)
    record["marks"] = [m for m in record["marks"] if m["page_index"] != index]
    record["marks"].append(dict(_pin(page), kind=kind, source="user-mark", answer=answer,
                                evidence_seen=[s for s in sources if s != "user-mark"],
                                at=now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")))
    return record


def choose(module, record, policy, index, fill, answer, sheet=None, answers=None, *, now=None):
    """Bind a user's fill choice to this page, policy, roots and optional sheet hash."""
    page = next((p for p in module["pages"] if p["page_index"] == index), None)
    if page is None or not page_current(page, record) or fill not in {"sheet", "draft", "leave"}:
        raise rat.RatError("unknown or stale page")
    decision = rat.decide(page, policy, record, answers)
    if fill == "draft" and decision["kind"] != "trat":
        raise rat.RatError("only a team RAT can be drafted")
    named = None
    if sheet:
        named = next((m["evidence"] for m in record["matches"] if m["page_index"] == index and
                      sheet == m["evidence"]["root"] + ":" + m["evidence"]["name"] and m["evidence"]["grade"] in {"full", "partial"}), None)
        if named is None:
            raise rat.RatError("sheet is not a current match")
    record = copy.deepcopy(record)
    record["choices"] = [c for c in record["choices"] if c["page_index"] != index]
    entry = dict(_pin(page), fill=fill, asked=decision["decision"], answer=answer,
                 roots_digest=record["scan"]["roots_digest"], policy_sha256=rat.policy_hash(policy),
                 at=now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    if named:
        entry["sheet"] = {k: named[k] for k in ("root", "name", "sha256")}
    record["choices"].append(entry)
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest="command", required=True)
    for name in ("classify", "mark", "choose", "roots"):
        sub = subs.add_parser(name)
        for arg in ("config", "data-dir"):
            sub.add_argument("--" + arg)
        sub.add_argument("--json", action="store_true")
        if name != "roots":
            sub.add_argument("--module", required=True)
            sub.add_argument("--answers")
        if name == "mark":
            sub.add_argument("--mark", action="append", required=True)
            sub.add_argument("--answer", required=True)
        if name == "choose":
            sub.add_argument("--page", type=int, required=True)
            sub.add_argument("--fill", choices=["sheet", "draft", "leave"], required=True)
            sub.add_argument("--sheet")
            sub.add_argument("--answer", required=True)
    a = ap.parse_args(argv)
    try:
        cfg = config.load(a.config)
        policy = rat.policy_from_config(cfg)
        rat.validate_roots(policy, data_dir=store.Store(cfg, data_dir=a.data_dir).data_dir)
        if a.command == "roots":
            result = rat.roots(policy)
            print(json.dumps(result, indent=1))
            return 0
        module = json.loads(Path(a.module).read_text(encoding="utf-8"))
        answers = json.loads(Path(a.answers).read_text(encoding="utf-8")) if a.answers else None
        path = store.Store(cfg, data_dir=a.data_dir).run_dir(module["module_id"]) / "page-kinds.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        answer_hash = hashlib.sha256(Path(a.answers).read_bytes()).hexdigest() if a.answers else None
        record = classify(module, policy, old, answers, answers_sha256=answer_hash)
        if a.command == "mark":
            for val in a.mark:
                idx, kind = val.split("=", 1)
                record = mark(module, record, int(idx), kind, a.answer)
            record = classify(module, policy, record, answers, answers_sha256=answer_hash)
        if a.command == "choose":
            record = choose(module, record, policy, a.page, a.fill, a.answer, a.sheet, answers)
            record = classify(module, policy, record, answers, answers_sha256=answer_hash)
        atomic_write(path, record)
        decisions = [dict(rat.decide(p, policy, record, answers), title=p.get("title"), item_count=len(p.get("items") or []))
                     for p in module["pages"] if is_candidate(p, policy)]
        print(json.dumps({"module_id": module["module_id"], "pages": decisions}, indent=1))
        return 2 if not decisions else 1 if any(d.get("question") for d in decisions) else 0
    except (config.ConfigError, rat.RatError, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, rat.RatError) else "input, config or root error"
        print("page-kinds: " + message, file=__import__("sys").stderr)
        return 3
