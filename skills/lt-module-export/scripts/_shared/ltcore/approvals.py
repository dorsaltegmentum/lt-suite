# Generated from shared/ltcore/approvals.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The approvals file of one run: the user's recorded yes, read, appended and checked.

A single-attempt item is written only after the user approves it, and a waivable
validator rail passes only on an approval that names it. Each approval is one record
in ``<data>/runs/<module_id>/approvals.json`` (schema: schemas/approvals.schema.json):

    {"schema_version": 1, "module_id": "c1-r104",
     "approvals": [{"approval_id": "A-3f2a9c1e", "kind": "single-attempt",
                    "item_id": "p3-12", "approved_by": "user",
                    "quote": "Yes, write p3-12 as drafted.",
                    "recorded_at": "2000-01-01T00:00:00Z", "question_digest": "..."}]}

``approval_id`` hashes the canonical JSON record, excluding the ID itself, together
with its module ID. Every scope field participates. The agent records the user's reply.

``check(module, answers, approvals_path)`` refuses (ApprovalError, CLI exit 3) when an
answer's ``single_attempt_approval.approval_id`` is missing from the file, names another
item, belongs to another module, or is not a single-attempt approval.

CLI:
    python3 -m ltcore.approvals add --approvals F --module-id ID --item ITEM --quote TEXT
                                    --question-digest DIGEST
                                    [--kind single-attempt|waiver] [--rail RAIL] [--recorded-at T]
    python3 -m ltcore.approvals check --module M --answers A --approvals F
    python3 -m ltcore.approvals show F

Exit codes: 0 ok, 2 usage, 3 refused or unreadable.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402

from ltcore.digest import question_digest as capture_digest  # noqa: E402

NEEDS: list[str] = []

SCHEMA_VERSION = 1
APPROVAL_ID_RE = re.compile(r"^A-[0-9a-f]{8}$")
KINDS = ("single-attempt", "waiver")
# Rails a waiver approval may name; ltcore.validate keeps the same set (WAIVABLE_RAILS).
WAIVER_RAILS = ("approval-manual-at-boundary", "write-past-first-door", "passed-single-approval")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ApprovalError(Exception):
    """An approval check refused. ``problems`` lists every reason."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


def _record_id(module_id: str, record: dict) -> str:
    scope = {k: v for k, v in record.items() if k != "approval_id"}
    raw = json.dumps({"module_id": module_id, "record": scope}, sort_keys=True,
                     separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "A-" + hashlib.sha256(raw).hexdigest()[:8]


def approval_id(module_id: str, item_id: str | None, quote: str, recorded_at: str, *,
                kind: str = "single-attempt", rail: str | None = None,
                item_ids: list[str] | None = None, question_digest: str | None = None,
                approved_by: str = "user", note: str | None = None) -> str:
    """Hash all recorded fields and the module ID using canonical JSON."""
    rec = {"kind": kind, "quote": quote, "recorded_at": recorded_at,
           "approved_by": approved_by}
    for key, value in (("item_id", item_id), ("rail", rail), ("item_ids", item_ids),
                       ("question_digest", question_digest), ("note", note)):
        if value is not None:
            rec[key] = value
    return _record_id(module_id, rec)


def now_utc() -> str:
    """The current time as an ISO 8601 UTC timestamp with a Z suffix, to the second."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty(module_id: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "module_id": module_id, "approvals": []}


def problems(doc) -> list[str]:
    """Structural and id problems in an approvals document (standard library only).

    Mirrors schemas/approvals.schema.json and adds what a schema cannot say: each id
    must recompute from its record, and ids are unique.
    """
    out: list[str] = []
    if not isinstance(doc, dict):
        return ["the approvals file is not a JSON object"]
    if type(doc.get("schema_version")) is not int or doc.get("schema_version") != SCHEMA_VERSION:
        out.append(f"schema_version is {doc.get('schema_version')!r}, expected {SCHEMA_VERSION}")
    mid = doc.get("module_id")
    if not isinstance(mid, str) or not mid:
        out.append("module_id is missing")
    extra = set(doc) - {"schema_version", "module_id", "approvals"}
    if extra:
        out.append(f"unknown top-level keys {sorted(extra)}")
    recs = doc.get("approvals")
    if not isinstance(recs, list):
        return out + ["approvals is not a list"]
    seen: set[str] = set()
    allowed = {"approval_id", "kind", "item_id", "item_ids", "rail", "approved_by", "quote",
               "recorded_at", "note", "question_digest"}
    for k, r in enumerate(recs):
        tag = f"approvals[{k}]"
        if not isinstance(r, dict):
            out.append(f"{tag} is not an object")
            continue
        extra = set(r) - allowed
        if extra:
            out.append(f"{tag}: unknown keys {sorted(extra)}")
        aid = r.get("approval_id")
        if not isinstance(aid, str) or not APPROVAL_ID_RE.match(aid):
            out.append(f"{tag}: approval_id {aid!r} does not match ^A-[0-9a-f]{{8}}$")
        kind = r.get("kind")
        qdigest = r.get("question_digest")
        if (not isinstance(qdigest, str) or not _DIGEST_RE.fullmatch(qdigest)
                or qdigest == "0" * 64):
            out.append(f"{tag}: question_digest must bind the approval to a capture")
        if kind not in KINDS:
            out.append(f"{tag}: kind {kind!r} is not one of {list(KINDS)}")
        if r.get("approved_by") != "user":
            out.append(f"{tag}: approved_by must be 'user'")
        if not isinstance(r.get("quote"), str) or not r.get("quote"):
            out.append(f"{tag}: quote is missing; record the user's words verbatim")
        ts = r.get("recorded_at")
        valid_ts = isinstance(ts, str) and _TS_RE.fullmatch(ts)
        if valid_ts:
            try:
                valid_ts = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).tzinfo is not None
            except ValueError:
                valid_ts = False
        if not valid_ts:
            out.append(f"{tag}: recorded_at {ts!r} is not an ISO 8601 timestamp with an offset")
        if "item_id" in r and (not isinstance(r["item_id"], str) or not r["item_id"]):
            out.append(f"{tag}: item_id must be a non-empty string")
        if kind == "single-attempt" and not r.get("item_id"):
            out.append(f"{tag}: a single-attempt approval names its item_id")
        if "rail" in r and r["rail"] not in WAIVER_RAILS:
            out.append(f"{tag}: rail must name one of {list(WAIVER_RAILS)}")
        if kind == "waiver" and r.get("rail") not in WAIVER_RAILS:
            out.append(f"{tag}: a waiver names one rail of {list(WAIVER_RAILS)}, "
                       f"got {r.get('rail')!r}")
        if "item_ids" in r:
            ids = r["item_ids"]
            if (not isinstance(ids, list) or not ids
                    or any(not isinstance(i, str) or not i for i in ids)):
                out.append(f"{tag}: item_ids must be a non-empty list of non-empty strings")
            elif len(set(ids)) != len(ids):
                out.append(f"{tag}: item_ids must be unique")
        if "note" in r and not isinstance(r["note"], str):
            out.append(f"{tag}: note must be a string")
        if (isinstance(aid, str) and isinstance(mid, str) and isinstance(r.get("quote"), str)
                and isinstance(ts, str)):
            want = _record_id(mid, r)
            if aid != want:
                out.append(f"{tag}: approval_id {aid} does not match its record (recomputes "
                           f"to {want}); an approval is never edited after it is recorded")
        if isinstance(aid, str):
            if aid in seen:
                out.append(f"{tag}: approval_id {aid} appears twice")
            seen.add(aid)
    return out


def load(path: str | os.PathLike, module_id: str | None = None, *, missing_ok: bool = False) -> dict:
    """Read and check an approvals file.

    A missing file is an empty file for ``module_id`` when ``missing_ok``; otherwise it
    raises. A file for another module raises.
    """
    p = Path(path)
    if not p.is_file():
        if missing_ok and module_id:
            return empty(module_id)
        raise ApprovalError(f"no approvals file at {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ApprovalError(f"{p}: {e}") from e
    bad = problems(doc)
    if bad:
        raise ApprovalError([f"{p}: {b}" for b in bad])
    if module_id is not None and doc["module_id"] != module_id:
        raise ApprovalError(f"{p} holds approvals for module {doc['module_id']!r}, "
                            f"not {module_id!r}")
    return doc


def find(doc: dict, aid: str) -> dict | None:
    return next((r for r in doc.get("approvals", []) if r.get("approval_id") == aid), None)


def _write_atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".approvals-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append(path: str | os.PathLike, module_id: str, item_id: str | None, quote: str, *,
           kind: str = "single-attempt", rail: str | None = None,
           item_ids: list[str] | None = None, recorded_at: str | None = None,
           note: str | None = None, question_digest: str | None = None) -> dict:
    """Record one approval and return it. The file is created when absent.

    Recording the same words for the same item at the same time again returns the
    existing record; nothing is written twice. question_digest must name the capture
    the user approved; omitting it refuses the append.
    """
    if not quote or not quote.strip():
        raise ApprovalError("quote is empty; record the user's words verbatim")
    if kind not in KINDS:
        raise ApprovalError(f"kind {kind!r} is not one of {list(KINDS)}")
    if kind == "single-attempt" and not item_id:
        raise ApprovalError("a single-attempt approval names its item")
    if kind == "waiver" and rail not in WAIVER_RAILS:
        raise ApprovalError(f"a waiver names one rail of {list(WAIVER_RAILS)}, got {rail!r}")
    if (not isinstance(question_digest, str) or not _DIGEST_RE.fullmatch(question_digest)
            or question_digest == "0" * 64):
        raise ApprovalError("question_digest is required; bind the approval to its capture")
    p = Path(path)
    doc = load(p, module_id, missing_ok=True)
    ts = recorded_at or now_utc()
    rec = {"kind": kind}
    if item_id is not None:
        rec["item_id"] = item_id
    if rail is not None:
        rec["rail"] = rail
    if item_ids is not None:
        rec["item_ids"] = list(item_ids)
    rec.update({"approved_by": "user", "quote": quote, "recorded_at": ts})
    if note is not None:
        rec["note"] = note
    if question_digest is not None:
        rec["question_digest"] = question_digest
    rec = {"approval_id": _record_id(module_id, rec), **rec}
    have = find(doc, rec["approval_id"])
    if have is not None:
        if have != rec:
            raise ApprovalError(f"approval_id collision {rec['approval_id']}: the existing "
                                "record has different scope or contents; record a new reply")
        return have
    doc["approvals"].append(rec)
    bad = problems(doc)
    if bad:
        raise ApprovalError(bad)
    _write_atomic(p, doc)
    return rec


def check(module: dict, answers: dict, approvals_path: str | os.PathLike | None) -> dict[str, dict]:
    """{item_id: approval record} for every answer carrying ``single_attempt_approval``.

    Raises ApprovalError listing every refusal: an approval on an item the capture does
    not mark single-attempt, approved_by other than 'user', a malformed id, no approvals
    file, a file for another module, an id missing from the file, an approval for
    another item, or one that is not a single-attempt approval.
    """
    caps = {i["item_id"]: i for p in module.get("pages", []) for i in p.get("items", [])}
    wanted = [(a["item_id"], a["single_attempt_approval"]) for a in answers.get("items", [])
              if a.get("single_attempt_approval")]
    if not wanted:
        return {}
    errs: list[str] = []
    doc = None
    mid = module.get("module_id")
    if not mid or answers.get("module_id") != mid:
        errs.append(f"answers module_id {answers.get('module_id')!r} differs from the capture "
                    f"module_id {mid!r}")
    if approvals_path is None:
        errs.append("answers carry single_attempt_approval but no approvals file was given")
    else:
        try:
            doc = load(approvals_path, mid)
        except ApprovalError as e:
            errs.extend(e.problems)
    out: dict[str, dict] = {}
    for iid, ap in wanted:
        cap = caps.get(iid)
        if cap is None or cap.get("attempts") != "single":
            errs.append(f"{iid}: single_attempt_approval on an item the capture does not mark "
                        f"single-attempt; an approval for nothing is a mistake, not a no-op")
            continue
        if ap.get("approved_by") != "user":
            errs.append(f"{iid}: single_attempt_approval.approved_by must be 'user'")
        aid = ap.get("approval_id")
        if not isinstance(aid, str) or not APPROVAL_ID_RE.match(aid):
            errs.append(f"{iid}: approval_id {aid!r} does not match ^A-[0-9a-f]{{8}}$")
            continue
        if doc is None:
            continue
        rec = find(doc, aid)
        if rec is None:
            errs.append(f"{iid}: approval_id {aid} is not in the approvals file; ask the user "
                        f"and record the reply with `approve` first")
            continue
        if rec.get("kind") != "single-attempt":
            errs.append(f"{iid}: approval {aid} is a {rec.get('kind')} approval, not a "
                        f"single-attempt approval")
            continue
        if rec.get("item_id") != iid:
            errs.append(f"{iid}: approval {aid} approves item {rec.get('item_id')!r}, "
                        f"not {iid!r}")
            continue
        if rec.get("question_digest") != capture_digest(module):
            errs.append(f"{iid}: approval {aid} question_digest differs from the supplied capture; "
                        "ask the user for a new approval")
            continue
        out[iid] = rec
    if errs:
        raise ApprovalError(errs)
    return out


def check_waiver(doc: dict | None, module_id: str | None, aid: str, rail: str,
                 item_ids: list[str] | None, *, question_digest: str | None = None) -> list[str]:
    """Check waiver scope and capture binding; a missing digest refuses verification."""
    if not isinstance(aid, str) or not APPROVAL_ID_RE.match(aid):
        return [f"approval_id {aid!r} does not match ^A-[0-9a-f]{{8}}$"]
    if doc is None:
        return []
    out = []
    if module_id is not None and doc.get("module_id") != module_id:
        out.append(f"the approvals file is for module {doc.get('module_id')!r}, the report is "
                   f"for {module_id!r}")
    rec = find(doc, aid)
    if rec is None:
        return out + [f"approval_id {aid} is not in the approvals file; a waiver rests on the "
                      f"user's recorded approval"]
    if question_digest is None or rec.get("question_digest") != question_digest:
        out.append(f"approval {aid} question_digest cannot be verified against this capture")
    if rec.get("kind") != "waiver":
        out.append(f"approval {aid} is a {rec.get('kind')} approval, not a waiver")
    elif rec.get("rail") != rail:
        out.append(f"approval {aid} waives {rec.get('rail')!r}, not {rail!r}")
    elif rec.get("item_ids") and item_ids:
        extra = sorted(set(item_ids) - set(rec["item_ids"]))
        if extra:
            out.append(f"approval {aid} does not cover item(s) {extra}")
    return out


def _read_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ApprovalError(f"{path}: {e}") from e


def main(argv: list[str] | None = None) -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(prog="python3 -m ltcore.approvals", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="record one approval and print its id")
    a.add_argument("--approvals", required=True)
    a.add_argument("--module-id", required=True)
    a.add_argument("--item")
    a.add_argument("--quote", required=True, help="the user's reply, verbatim")
    a.add_argument("--kind", choices=KINDS, default="single-attempt")
    a.add_argument("--rail", choices=WAIVER_RAILS)
    a.add_argument("--item-ids", nargs="+")
    a.add_argument("--recorded-at")
    a.add_argument("--question-digest", help="question digest of the approved capture")
    c = sub.add_parser("check", help="check answers' single-attempt approvals")
    c.add_argument("--module", required=True)
    c.add_argument("--answers", required=True)
    c.add_argument("--approvals", required=True)
    s = sub.add_parser("show", help="print an approvals file after checking it")
    s.add_argument("approvals")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            rec = append(args.approvals, args.module_id, args.item, args.quote, kind=args.kind,
                         rail=args.rail, item_ids=args.item_ids, recorded_at=args.recorded_at,
                         question_digest=args.question_digest)
            print(rec["approval_id"])
        elif args.cmd == "check":
            got = check(_read_json(args.module), _read_json(args.answers), args.approvals)
            print(f"OK: {len(got)} single-attempt approval(s) check out"
                  + (f": {', '.join(sorted(got))}" if got else ""))
        else:
            doc = load(args.approvals)
            print(json.dumps(doc, indent=1, ensure_ascii=False))
    except ApprovalError as e:
        for p in e.problems:
            print(f"REFUSED: {p}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
