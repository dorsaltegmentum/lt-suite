# Generated from shared/ltcore/rat.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RAT sheet boundary. Only read_sheet opens sheets; parsed answers stay in-process.

roots(policy) lists metadata only. scan_sheets(policy, allowed) calls read_sheet.
match_sheet(sheet, page, module) returns answer-bearing positional mappings, never
serialize those mappings outside the fill payload. evidence() is the safe projection.
decide(page, policy, record, answers) returns a safe fill decision, never an answer.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from ltcore.digest import canonical_bytes

NEEDS: list[str] = []
OPTION_MIN = CHOSEN_MIN = 0.80
DISTINCT_MARGIN = 0.10
MAX_BYTES = 256 * 1024
MAX_FILES = 500
EXTENSIONS = {".md", ".markdown", ".txt"}
DEFAULT_MARKERS = {"trat": r"(?i)\b[tg]-?RAT\b|\b(?:team|group) readiness\b",
                   "irat": r"(?i)\bi-?RAT\b|\bindividual readiness\b"}
DEFAULT_POLICY = {"irat": "skip", "trat": "ask", "answers_dir": "", "answers_dirs": [],
                  "kind_markers": DEFAULT_MARKERS}


class RatError(ValueError):
    """A safe error containing only a root key or a fixed reason."""


def sha(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def policy_from_config(cfg=None) -> dict:
    out = {k: cfg.get("rat." + k, v) if cfg is not None else v for k, v in DEFAULT_POLICY.items()}
    if cfg is not None:
        out["_week_pattern"] = cfg.get("curriculum.week_number_pattern")
    return out


def policy_hash(policy) -> str:
    values = {k: policy.get(k, DEFAULT_POLICY[k]) for k in DEFAULT_POLICY}
    if "_week_pattern" in policy:
        values["_week_pattern"] = policy["_week_pattern"]
    return sha(values)


def within(path, parent) -> bool:
    """Containment through resolved aliases and samefile, including missing tails."""
    p, q = Path(path).expanduser().resolve(), Path(parent).expanduser().resolve()
    for a in (p, *p.parents):
        try:
            if os.path.samefile(a, q):
                return True
        except OSError:
            if a == q:
                return True
    return False


def effective_roots(policy) -> list[tuple[str, Path]]:
    pairs = [("rat.answers_dir", policy.get("answers_dir"))]
    pairs.extend((f"rat.answers_dirs[{i}]", p) for i, p in enumerate(policy.get("answers_dirs") or []))
    out = []
    for key, value in pairs:
        if not value:
            continue
        p = Path(value).expanduser().resolve()
        if not any(within(p, old) and within(old, p) for _, old in out):
            out.append((key, p))
    return out


def validate_roots(policy, *, data_dir=None, notes_graph=None, curriculum_root=None):
    """Check effective runtime boundaries, including a CLI data-dir override."""
    for key, root in effective_roots(policy):
        if root.exists() and not root.is_dir():
            raise RatError(f"{key}: not a directory")
        for label, other, symmetric in (("data folder", data_dir, True),
                                        ("notes graph", notes_graph, True),
                                        ("curriculum root", curriculum_root, False)):
            if other and (within(other, root) or symmetric and within(root, other)):
                raise RatError(f"{key}: overlaps {label}")


def _linked(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) &
                                         getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _listing(key, root):
    skipped, files, listing = {}, [], []
    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1
    if root.exists() and not root.is_dir():
        raise RatError(f"{key}: not a directory")
    if root.is_dir():
        try:
            for entry in sorted(root.iterdir(), key=lambda p: p.name):
                st = os.lstat(entry)
                if entry.name.startswith("."):
                    skip("hidden")
                elif _linked(st):
                    skip("linked")
                elif not stat.S_ISREG(st.st_mode):
                    skip("not-a-file")
                elif entry.suffix.lower() not in EXTENSIONS:
                    skip("not-a-sheet")
                else:
                    files.append(entry)
                    listing.append((entry.name, st.st_size, st.st_mtime_ns))
                    if len(files) > MAX_FILES:
                        raise RatError(f"{key}: more than 500 candidate files")
        except OSError:
            raise RatError(f"{key}: cannot list root") from None
    return {"key": key, "real_sha256": sha(str(root)), "exists": root.is_dir(),
            "listing_sha256": sha(listing), "files_seen": len(files),
            "sheets_parsed": 0, "skipped": skipped}, files


def roots(policy=None) -> dict:
    """Safe metadata report. Never opens a file under a root, even with from_dir."""
    policy = policy or DEFAULT_POLICY
    rows = [_listing(k, p)[0] for k, p in effective_roots(policy)]
    exists = any(r["exists"] for r in rows)
    return {"roots": rows, "read": {k: exists and policy.get(k) == "from_dir" for k in ("irat", "trat")},
            "roots_digest": sha([{k: r[k] for k in ("key", "real_sha256", "listing_sha256")} for r in rows])}


def safe_name(name, digest) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9 ._()-]{1,80}", name) else "sheet-" + digest[:8]


def read_sheet(root, name) -> dict:
    """The sole sheet opener. Refuses links, escapes, races, oversized and non-UTF8 files.

    root is an effective real directory; name is one immediate child name. Returns
    a parsed answer-bearing dict plus sha256/name. Errors contain no input text.
    """
    root = Path(root).resolve()
    if Path(name).name != name or name.startswith(".") or Path(name).suffix.lower() not in EXTENSIONS:
        raise RatError("not-a-sheet")
    path = root / name
    fd = None
    try:
        before = os.lstat(path)
        if _linked(before) or not stat.S_ISREG(before.st_mode) or path.parent.resolve() != root:
            raise RatError("linked-or-not-regular")
        if before.st_size > MAX_BYTES:
            raise RatError("too-large")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode) or _linked(after) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RatError("changed-during-open")
        if after.st_size > MAX_BYTES:
            raise RatError("too-large")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise RatError("too-large")
        digest = hashlib.sha256(raw).hexdigest()
        sheet = parse_sheet(raw.decode("utf-8-sig"))
        sheet.update(sha256=digest, name=safe_name(name, digest))
        return sheet
    except (OSError, UnicodeError):
        raise RatError("unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)


def _front(text):
    meta = {}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration:
            return {}, []
        for line in lines[1:end]:
            m = re.match(r"^([a-z-]+):\s*(.*?)\s*$", line.split("#", 1)[0])
            if m and m[1] in {"lt-rat", "module", "page", "kind", "questions", "week"}:
                meta[m[1]] = m[2].strip("\"'")
        lines = lines[end + 1:]
    return meta, lines


def parse_sheet(text) -> dict:
    """Parse fixed compact/marked grammar. No YAML, execution, or unknown metadata."""
    meta, lines = _front(text)
    questions = []
    compact = meta.get("lt-rat") == "1"
    malformed = False
    if compact:
        malformed = not meta.get("module") or not str(meta.get("page", "")).isdigit()
        for line in lines:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"(\d+)[.)]\s*([A-Z](?:\s*,\s*[A-Z])*|[?-])(?:\s*\|\s*(.*))?", line)
            if not m:
                malformed = True
                continue
            letters = [] if m[2] in {"?", "-"} else [ord(x.strip()) - 65 for x in m[2].split(",")]
            malformed |= int(m[1]) != len(questions) + 1 or len(set(letters)) != len(letters)
            questions.append({"selected": letters, "crosscheck": m[3], "options": None, "malformed": False})
    else:
        run = []
        def flush():
            if len(run) >= 2:
                letters = [x[0] for x in run]
                questions.append({"options": [x[1] for x in run],
                                  "selected": [i for i, x in enumerate(run) if x[2]],
                                  "malformed": letters != list(range(len(run)))})
            run.clear()
        for line in lines:
            if not line.strip():
                continue
            m = re.match(r"^\s*(?:-\s*)?(?:\*\*)?(?:\[([xX ])\]\s*)?([A-Z])[.)]\s*(.*)$", line)
            if m:
                run.append((ord(m[2]) - 65, m[3], m[1] in {"x", "X"} or bool(re.search("[✓✔✅]", m[3]))))
            else:
                flush()
        flush()
    status = "malformed" if malformed else "parsed" if questions else "not-a-sheet"
    return {"format": "compact" if compact else "marked", "meta": meta,
            "questions": questions, "status": status}


def normalise(text, position=None) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"\*\*|__|`|[✓✔✅✗✘❌]|\[[xX ]\]", "", s).strip()
    if position is not None:
        s = re.sub(r"^" + re.escape(chr(65 + position)) + r"[.)]\s*", "", s)
    return " ".join(s.split()).casefold().rstrip(" .;:")


def _ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _alignment(question, item, compact=False):
    opts = [normalise(o.get("text"), i) for i, o in enumerate(item.get("options") or [])]
    selected = question["selected"]
    bad = question.get("malformed", False)
    mean = 0.0
    if compact:
        aligned = bool(opts) and all(0 <= i < len(opts) for i in selected)
        mean = 1.0 if aligned else 0.0
        mismatch = not aligned or bool(question.get("crosscheck") and
                       any(_ratio(normalise(question["crosscheck"]), opts[i]) < CHOSEN_MIN for i in selected if i < len(opts)))
    else:
        src = [normalise(s) for s in question["options"]]
        scores = [[_ratio(a, b) for b in opts] for a in src]
        aligned = not bad and len(src) == len(opts) and bool(opts)
        if aligned:
            own = [row[i] for i, row in enumerate(scores)]
            mean = sum(own) / len(own)
            aligned = mean >= OPTION_MIN and all(row[i] > max((v for j, v in enumerate(row) if j != i), default=-1)
                                                for i, row in enumerate(scores))
        mismatch = bool(aligned and any(scores[i][i] < CHOSEN_MIN for i in selected))
    multi = len(selected) > 1 and item.get("input_mode") != "multi" and not (item.get("constraints") or {}).get("multiselect") and (item.get("constraints") or {}).get("note") != "multiselect"
    status = ("malformed" if bad else "mark-mismatch" if mismatch else "unaligned" if not aligned
              else "unanswered" if not selected else "multi-marked" if multi else "answered")
    return {"aligned": aligned, "mean": mean, "status": status, "selected": selected if status == "answered" else []}


def _week(value, pattern=None):
    if pattern is not None:
        match = re.search(pattern, str(value or ""))
        return ".".join(str(int(p)) for p in match.groups() if p is not None) if match else None
    match = re.search(r"\b(\d+)(?:\.(\d+))?\b", str(value or ""))
    return ".".join(str(int(p)) for p in match.groups() if p is not None) if match else None


def match_sheet(sheet, page, module, *, week_pattern=None) -> dict | None:
    """Return a positional, answer-bearing match or None after hard filters."""
    if sheet["status"] != "parsed":
        return None
    items = page.get("items") or []
    meta = sheet["meta"]
    if any(meta.get(k) is not None and str(meta[k]) != str(v) for k, v in
           (("module", module.get("module_id")), ("page", page.get("page_index")), ("questions", len(items)))):
        return None
    compact = sheet["format"] == "compact"
    mappings = []
    any_aligned = 0
    for k, q in enumerate(sheet["questions"]):
        row = _alignment(q, items[k], compact) if k < len(items) else {"aligned": False, "mean": 0, "status": "unaligned", "selected": []}
        other_items = [it for j, it in enumerate(items) if j != k]
        others = [_alignment(q, it, compact) for it in other_items]
        row["distinct"] = row["aligned"] and (compact or all(
            row["mean"] - other["mean"] >= DISTINCT_MARGIN - 1e-12
            for it, other in zip(other_items, others)
            if len(q["options"]) == len(it.get("options") or [])))
        row["question"] = k + 1
        any_aligned += int(row["aligned"] or any(o["aligned"] for o in others))
        mappings.append(row)
    full = len(mappings) == len(items) and all(r["aligned"] for r in mappings)
    grade = "full" if full else "partial" if any_aligned >= max(2, len(items) // 2) else "none"
    for k in range(len(mappings), len(items)):
        mappings.append({"aligned": False, "mean": 0.0, "status": "unaligned", "selected": [],
                         "distinct": False, "question": k + 1})
    sw = _week(meta["week"]) if "week" in meta else _week(sheet.get("name"), week_pattern)
    mw = _week((module.get("lesson") or {}).get("title"), week_pattern)
    hint = "absent" if not sw or not mw else "agree" if sw == mw else "differs"
    return {"root": sheet.get("root"), "name": sheet.get("name"), "sha256": sheet.get("sha256"),
            "format": sheet["format"], "declared_kind": meta.get("kind") if meta.get("kind") in {"irat", "trat"} else None,
            "grade": grade, "distinct": sum(r["distinct"] for r in mappings),
            "aligned": sum(r["aligned"] for r in mappings), "answered": sum(r["status"] == "answered" for r in mappings),
            "unanswered": [r["question"] for r in mappings if r["status"] == "unanswered"],
            "mark_mismatch": [r["question"] for r in mappings if r["status"] == "mark-mismatch"],
            "aligned_questions": [r["question"] for r in mappings if r["aligned"]], "week": hint,
            "todo_questions": {str(r["question"]): r["status"] for r in mappings if r["status"] != "answered"},
            "selection_sha256": sha([r["selected"] for r in mappings]), "mapping": mappings}


def evidence(match):
    """Safe persistent projection: counts and hashes only; no selections or text."""
    return {k: v for k, v in match.items() if k not in {"mapping", "selection_sha256"}}


def scan_sheets(policy, allowed) -> tuple[dict, list[dict]]:
    report = roots(policy)
    sheets = []
    if not any(allowed.values()):
        return report, sheets
    for row, (key, root) in zip(report["roots"], effective_roots(policy)):
        for path in _listing(key, root)[1]:
            try:
                sheet = read_sheet(root, path.name)
                if sheet["status"] != "parsed":
                    why = sheet["status"]
                else:
                    row["sheets_parsed"] += 1
                    sheet["root"] = key
                    sheets.append(sheet)
                    continue
            except RatError as exc:
                why = str(exc)
            row["skipped"][why] = row["skipped"].get(why, 0) + 1
    return report, sheets


def select_match(matches, kind):
    if any("chosen" in m for m in matches):
        selected = next((m for m in matches if m.get("chosen")), None)
        return selected, list((selected or {}).get("confirmation_reasons", []))
    full = [m for m in matches if m["grade"] == "full" and m["distinct"] == m["aligned"]]
    choices = full or [m for m in matches if m["grade"] in {"full", "partial"}]
    if not choices:
        return None, []
    reasons = []
    if full and len({m.get("selection_sha256") for m in full}) > 1:
        declared = [m for m in full if m.get("declared_kind") == kind]
        if len(declared) == 1:
            choices = declared
        else:
            reasons.append("ambiguous")
    m = choices[0]
    if m["grade"] == "partial":
        reasons.append("partial")
    if m["distinct"] < m["aligned"]:
        reasons.append("indistinct")
    if kind and m.get("declared_kind") and kind != m["declared_kind"]:
        reasons.append("kind-differs")
    if m.get("week") == "differs":
        reasons.append("week-differs")
    return m, reasons


def decide(page, policy, record, answers=None) -> dict:
    """Safe fill policy (table 2.7); callers validate freshness before passing record."""
    from ltcore import page_kinds
    status = page_kinds._classifications({"module_id": (record or {}).get("module_id"), "pages": [page]}, record, policy)[0]
    def result(decision, **extra):
        return dict(status, decision=decision, **extra)
    if not status["candidate"]:
        return result("not-rat")
    if not page_kinds.page_current(page, record):
        return result("rat-record-stale", question="Run page_kinds.py classify --json")
    if status["rat"] is False:
        return result("not-rat")
    kind = status["kind"]
    if status["rat"] is None or (kind is None and policy["irat"] != policy["trat"]):
        return result("rat-unclassified", question="Team test, individual test or ordinary page?")
    choice = next((c for c in record["choices"] if c["page_index"] == page["page_index"]), {})
    fill = choice.get("fill")
    if fill == "leave":
        return result("rat-user-leave")
    if fill == "draft" and kind == "trat":
        return result("fill-from-draft")
    matches = [m["evidence"] for m in record["matches"] if m["page_index"] == page["page_index"]]
    named = choice.get("sheet")
    if fill == "sheet" and named:
        match = next((m for m in matches if all(m.get(k) == named.get(k) for k in ("root", "name", "sha256")) and m["grade"] in {"full", "partial"}), None)
        if match:
            return result("fill-from-sheet", sheet=match)
        return result("rat-record-stale")
    mode = policy[kind or "irat"]
    if mode == "skip":
        return result("irat-policy" if kind == "irat" else "trat-policy-skip" if kind == "trat" else "rat-policy-skip")
    if mode == "ask" and fill != "sheet":
        return result("rat-ask", question="Fill from my sheets, draft (team only), or leave?")
    match, reasons = select_match(matches, kind)
    if match is None:
        return result("rat-sheet-missing", question="Leave until a sheet exists, or draft (team only)?")
    ids = {it["item_id"] for it in page.get("items") or []}
    drafted = any(a.get("status") == "drafted" and a.get("item_id") in ids for a in (answers or {}).get("items", []))
    if answers is None:
        drafted = any(r["page_index"] == page["page_index"] and r.get("drafted", 0) > 0 for r in record["scan"]["pages"])
    if drafted:
        reasons.append("drafts-exist")
    if reasons:
        return result("rat-sheet-confirm", sheet=match, reasons=reasons,
                      question=f"Fill from {match['name']} questions {', '.join(map(str, match['aligned_questions']))}, or leave?")
    return result("fill-from-sheet", sheet=match)
