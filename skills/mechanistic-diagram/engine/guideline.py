#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The treatment-guideline ledger check: does each treatment box have a check for its current words?

    python3 guideline.py <case_dir> [--config FILE]

Reads the ledger file in the case folder named by diagram.treatment_check.ledger_name (default
guideline-ledger.json), which records whoever checked each treatment box against the treatment
guideline (diagram.treatment_check.provider_label names it in messages). Its format:

    {"checked": "<check date>", "by": "...", "access": "...",
     "verdicts": {"consistent": "matches the guideline", "partly": "...", "partly-checked": "...",
                  "not-in-guideline": "...", "unchecked": "..."},
     "entries": [{"node": "tx_enz", "text": "<the box's words when checked>",
                  "text_sha1": "<first 12 hex of sha1 of those words, no-break spaces as plain spaces>",
                  "verdict": "partly", "source": {"guideline": ..., "topic": ..., "published": ...} or null,
                  "notes": "..."}]}

For every node of role "tx" in content.py:
  MISSING  no ledger entry for it
  STALE    the entry's text_sha1 is not the hash of the node's current text (the words changed since
           the check; the old and new words are shown)
  NO VERDICT / UNKNOWN VERDICT   the entry has no verdict, or one the ledger's "verdicts" do not list
  and every entry whose verdict is not "consistent" is printed with its notes. Entries for nodes the
  diagram no longer has are listed as ORPHAN.
Warnings only: the exit status is 0 unless the ledger cannot be read as JSON (2).
"""
import argparse
import hashlib
import json
import pathlib
import sys

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)

LEDGER = "guideline-ledger.json"
LABEL = "treatment guideline"
GOOD = "consistent"


def settings():
    """(ledger file name, provider label) from diagram.treatment_check.*."""
    try:
        import style
        return (style.get("diagram.treatment_check.ledger_name", LEDGER),
                style.get("diagram.treatment_check.provider_label", LABEL))
    except Exception:  # noqa: BLE001  (no config: the defaults)
        return LEDGER, LABEL


def text_sha1(text, n=12):
    """The ledger's hash: sha1 of the text with no-break spaces (U+00A0, U+202F) as plain spaces."""
    plain = text.replace(" ", " ").replace(" ", " ")
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()[:n]


def check(case_dir, nodes=None, trust=False):
    """Returns (lines, counts). nodes: content.NODES (loaded from the case when not given)."""
    d = pathlib.Path(case_dir).resolve()
    ledger, label = settings()
    if nodes is None:
        sys.dont_write_bytecode = True
        from case import Case
        nodes = Case(d, trust=trust).content.NODES
    tx = {k: v for k, v in nodes.items() if v["role"] == "tx"}
    lines, counts = [], dict(ok=0, missing=0, stale=0, verdict=0, open=0, orphan=0)
    p = d / ledger
    head = f"Guideline ledger ({label})"
    if not p.exists():
        lines.append(f"{head}: no {ledger} in the case folder; {len(tx)} treatment box(es) unchecked: {', '.join(tx)}")
        counts["missing"] = len(tx)
        return lines, counts
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as ex:
        raise SystemExit(f"{head}: {p} is not valid JSON: {ex}")
    verdicts = led.get("verdicts", {})
    entries = {}
    for e in led.get("entries", []):
        entries.setdefault(e.get("node"), e)
    lines.append(f"{head} {p.name}: checked {led.get('checked', '?')} by {led.get('by', '?')}; "
                 f"{len(tx)} treatment box(es), {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
    for nid, nd in tx.items():
        e = entries.get(nid)
        if e is None:
            lines.append(f"  MISSING   {nid}: no ledger entry ({nd['text']!r})")
            counts["missing"] += 1
            continue
        want = (e.get("text_sha1") or "").lower()
        now = text_sha1(nd["text"], len(want) or 12)
        flags = []
        if not want or want != now:
            flags.append("STALE")
            counts["stale"] += 1
        v = e.get("verdict")
        if not v:
            flags.append("NO VERDICT")
            counts["verdict"] += 1
        elif verdicts and v not in verdicts:
            flags.append(f"UNKNOWN VERDICT {v!r}")
            counts["verdict"] += 1
        if v and v != GOOD:
            counts["open"] += 1
        if not flags and v == GOOD:
            counts["ok"] += 1
        tag = " ".join(flags) or (v or "")
        meaning = f" ({verdicts[v]})" if v in verdicts else ""
        where = e.get("source") or {}
        src = f"; {label} {where.get('guideline')} > {where.get('topic')}, {where.get('published')}" if where else ""
        lines.append(f"  {tag:9s} {nid}: verdict {v or 'none'}{meaning}{src}")
        if "STALE" in flags:
            lines.append(f"            checked words: {e.get('text', '?')!r}")
            lines.append(f"            current words: {nd['text'].replace(chr(0xa0), ' ')!r}")
        if v != GOOD or flags:
            if e.get("notes"):
                lines.append(f"            notes: {e['notes']}")
    for nid in entries:
        if nid not in tx:
            lines.append(f"  ORPHAN    {nid}: in the ledger but not a treatment box on the diagram")
            counts["orphan"] += 1
    lines.append(f"{head}: {counts['ok']} consistent and current, {counts['open']} with an open verdict, "
                 f"{counts['stale']} stale, {counts['missing']} missing, {counts['verdict']} without a known verdict"
                 + (f", {counts['orphan']} orphan" if counts["orphan"] else "") + " (warnings only)")
    return lines, counts


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Check a case's treatment boxes against its guideline ledger.")
    ap.add_argument("case_dir")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    from case import CaseError
    try:
        style.configure(a.config)
        lines, _ = check(a.case_dir, trust=a.trust_case)
    except (CaseError, style.StyleError) as ex:
        raise SystemExit(str(ex))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    _bootstrap.entry()
    sys.exit(main(sys.argv[1:]))
