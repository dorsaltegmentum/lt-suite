# Generated from shared/ltcore/digest.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Question digest of a capture, and the pin fixer for files bound to a capture.

``answers.source_module.sha256`` hashes the whole ``module.json``. That hash cannot
tell "the questions changed" from "the student answered them": every re-capture
after a fill moves it, because ``answered`` flags, media and geometry change. The
question digest hashes only the fields that carry the question, so a post-fill
re-capture keeps it and a real edit to a question changes it:

    item_id, type, prompt_text, options[], the authored half of constraints

Left out on purpose:

* ``answered``, ``answered_evidence``, ``commit_state``, ``read_only``: state.
* ``media[]``, ``target_geometry``: a student upload lands in both. The author's own
  stimulus image lands there too, so a swapped figure is not seen here. The
  whole-file sha256 still sees it and the validator reports that as a note.
* ``constraints.note``: one string mixes authored facts and state. Parsing the
  authored half back out of prose would be fragile, so the whole note is excluded.
* ``attempts``, ``assessment_type``, ``maximum_grade``, ``automation``, ``degrade``,
  ``mirror_of``, ``ilo_refs``: these steer the fill; the question stays the same.

All exclusions lean one way: the digest can miss a change that the whole-file hash
still catches, and it never reports a change that did not happen.

CLI:
    python3 -m ltcore.digest <module.json>                  print the digest
    python3 -m ltcore.digest <module.json> --view           print the canonical view
    python3 -m ltcore.digest <old.json> --diff <new.json>   name the questions that changed
    python3 -m ltcore.digest --fix-pins <dir>               rewrite stale pins in <dir>
    python3 -m ltcore.digest --fix-pins --check <dir>       report stale pins, write nothing

Pins: ``answers*.json`` records ``source_module.sha256`` and ``question_digest`` of
the capture at ``source_module.path``; ``fill-report*.json`` records
``source_answers.sha256`` of the answers file at ``source_answers.path``. A relative
path resolves against the pinned file's own folder.

Exit codes: 0 ok (no question changed, no stale pin), 1 questions differ or a pin is
stale, 3 usage or file error.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402

NEEDS: list[str] = []

# Hashed with the payload. Bump it when the field set changes, so a digest stored by an
# older version fails loudly instead of agreeing by accident.
DIGEST_VERSION = 1

# The authored half of `constraints`: stimulus the author wrote. None of these moves
# when a student answers.
AUTHORED_CONSTRAINTS = (
    "word_limit", "char_limit", "rows", "cols",
    "column_headers", "row_headers", "options_count", "categories",
)


def normalise_text(s):
    """Collapse whitespace. Lt returns the same prompt with a no-break space or stray
    line breaks depending on how the panel rendered; a prompt that differs only in
    spacing is the same question."""
    if not isinstance(s, str):
        return s
    return " ".join(s.split())


def item_view(item: dict) -> dict:
    v = {
        "item_id": item.get("item_id"),
        "type": item.get("type"),
        "prompt_text": normalise_text(item.get("prompt_text")),
    }
    opts = item.get("options")
    if opts:
        # option_id is positional in the sorted pool and means nothing to the live page,
        # but answers.json cites it, so a re-ordering is a change the drafter must see.
        v["options"] = [[o.get("option_id"), normalise_text(o.get("text"))] for o in opts]
    cons = item.get("constraints") or {}
    authored = {k: cons[k] for k in AUTHORED_CONSTRAINTS if k in cons}
    if authored:
        v["constraints"] = authored
    return v


def question_view(module: dict) -> dict:
    """The canonical question-bearing view of a capture, in document order.

    Order is kept: the sequence a student meets the items in is part of the module.
    """
    items = []
    for pg in module.get("pages") or []:
        for it in pg.get("items") or []:
            items.append(item_view(it))
    return {"digest_version": DIGEST_VERSION, "module_id": module.get("module_id"),
            "items": items}


def canonical_bytes(view: dict) -> bytes:
    return json.dumps(view, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def question_digest(module: dict) -> str:
    return hashlib.sha256(canonical_bytes(question_view(module))).hexdigest()


def digest_of_file(path: str | Path) -> str:
    return question_digest(json.loads(Path(path).read_text(encoding="utf-8")))


def question_diff(old: dict, new: dict) -> list[str]:
    """Which questions changed, in words, item by item."""
    out = []
    if old.get("module_id") != new.get("module_id"):
        out.append(f"module_id: {old.get('module_id')!r} -> {new.get('module_id')!r}")
    a = {i["item_id"]: i for i in question_view(old)["items"]}
    b = {i["item_id"]: i for i in question_view(new)["items"]}
    for iid in a:
        if iid not in b:
            out.append(f"{iid}: item is gone from the new capture")
    for iid in b:
        if iid not in a:
            out.append(f"{iid}: item is new in this capture")
    for iid, av in a.items():
        bv = b.get(iid)
        if bv is None or av == bv:
            continue
        for field in ("type", "prompt_text", "options", "constraints"):
            if av.get(field) != bv.get(field):
                out.append(f"{iid}: {field}\n    - {json.dumps(av.get(field), ensure_ascii=False)}"
                           f"\n    + {json.dumps(bv.get(field), ensure_ascii=False)}")
    old_order = [i["item_id"] for i in question_view(old)["items"]]
    new_order = [i["item_id"] for i in question_view(new)["items"]]
    if set(old_order) == set(new_order) and old_order != new_order:
        out.append("the items are the same but their order changed")
    return out


# --- pins -------------------------------------------------------------------------

def dump_json(doc) -> str:
    """The on-disk form of every pinned file: one-space indent, UTF-8, final newline."""
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def resolve_pinned(base: Path, rel: str) -> Path | None:
    """The file a pin names: absolute, else relative to the pinned file's folder."""
    if not rel:
        return None
    p = Path(rel).expanduser()
    if not p.is_absolute():
        p = base / p
    return p if p.is_file() else None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_pins(folder: Path) -> tuple[dict[Path, str], list[str], list[str]]:
    """({path: new text} for files whose pins change, change lines, error lines).

    Answers files are pinned first, in memory, so a fill report pins the answers
    bytes that will be on disk after the fix.
    """
    folder = Path(folder)
    new_text: dict[Path, str] = {}
    changes: list[str] = []
    errors: list[str] = []

    def current_bytes(p: Path) -> bytes:
        return new_text[p].encode("utf-8") if p in new_text else p.read_bytes()

    files = sorted(folder.rglob("*.json"))
    answers = [p for p in files if p.name.startswith("answers")]
    reports = [p for p in files if p.name.startswith("fill-report")]
    for p in answers:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{p}: {e}")
            continue
        src = doc.get("source_module") or {}
        mod_path = resolve_pinned(p.parent, src.get("path", ""))
        if mod_path is None:
            errors.append(f"{p}: source_module.path {src.get('path')!r} does not resolve")
            continue
        try:
            mod_bytes = current_bytes(mod_path.resolve())
            mod = json.loads(mod_bytes)
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{p}: {mod_path}: {e}")
            continue
        sha, qd = hashlib.sha256(mod_bytes).hexdigest(), question_digest(mod)
        if src.get("sha256") != sha:
            changes.append(f"{p}: source_module.sha256 {str(src.get('sha256'))[:12]} -> {sha[:12]}")
            src["sha256"] = sha
        if "question_digest" in src and src.get("question_digest") != qd:
            changes.append(f"{p}: source_module.question_digest "
                           f"{str(src.get('question_digest'))[:12]} -> {qd[:12]}")
            src["question_digest"] = qd
        text = dump_json(doc)
        if text.encode("utf-8") != p.read_bytes():
            new_text[p.resolve()] = text
    for p in reports:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{p}: {e}")
            continue
        src = doc.get("source_answers")
        if not src:
            continue
        ans_path = resolve_pinned(p.parent, src.get("path", ""))
        if ans_path is None:
            errors.append(f"{p}: source_answers.path {src.get('path')!r} does not resolve")
            continue
        sha = hashlib.sha256(current_bytes(ans_path.resolve())).hexdigest()
        if src.get("sha256") != sha:
            changes.append(f"{p}: source_answers.sha256 {str(src.get('sha256'))[:12]} -> {sha[:12]}")
            src["sha256"] = sha
        text = dump_json(doc)
        if text.encode("utf-8") != p.read_bytes():
            new_text[p.resolve()] = text
    return new_text, changes, errors


def _show(p: Path) -> str:
    """A path relative to the current folder when it lies inside it."""
    try:
        return str(Path(p).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


def fix_pins(folder: str | Path, *, check: bool = False, out=None) -> int:
    """Rewrite stale pins under ``folder``; with ``check`` only report them."""
    out = out or sys.stdout
    folder = Path(folder)
    if not folder.is_dir():
        print(f"FATAL: {folder} is not a folder", file=sys.stderr)
        return 3
    new_text, changes, errors = plan_pins(folder)
    for e in errors:
        print(f"FATAL: {e}", file=sys.stderr)
    if errors:
        return 3
    if check:
        for c in changes:
            print(f"stale pin: {c}", file=out)
        for p in sorted(new_text):
            shown = _show(p)
            if not any(c.startswith(f"{shown}:") or c.startswith(f"{p}:") for c in changes):
                print(f"not in canonical form: {shown}", file=out)
        if new_text:
            print(f"{len(new_text)} file(s) need python3 -m ltcore.digest --fix-pins {folder}",
                  file=out)
            return 1
        print(f"OK: every pin under {folder} is current", file=out)
        return 0
    for p, text in sorted(new_text.items()):
        p.write_text(text, encoding="utf-8")
    for c in changes:
        print(f"fixed: {c}", file=out)
    print(f"{len(new_text)} file(s) rewritten under {folder}", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.digest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="module.json, or the folder for --fix-pins")
    ap.add_argument("--diff", type=Path, metavar="NEW",
                    help="compare the question-bearing fields of two captures")
    ap.add_argument("--view", action="store_true", help="print the canonical view, not the hash")
    ap.add_argument("--fix-pins", action="store_true",
                    help="rewrite source_module and source_answers pins under the folder")
    ap.add_argument("--check", action="store_true",
                    help="with --fix-pins: report stale pins, write nothing, exit 1 if any")
    args = ap.parse_args(argv)

    if args.check and not args.fix_pins:
        ap.error("--check needs --fix-pins")
    if args.fix_pins:
        return fix_pins(args.path, check=args.check)

    try:
        doc = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3
    if args.diff:
        try:
            new = json.loads(args.diff.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 3
        diffs = question_diff(doc, new)
        print(f"{args.path.name}: {question_digest(doc)}")
        print(f"{args.diff.name}: {question_digest(new)}")
        if not diffs:
            print("\nNo question-bearing field differs. A draft bound to one is bound to the "
                  "other; whatever else changed is state, media or geometry.")
            return 0
        print(f"\n{len(diffs)} question-bearing difference(s). A draft written against the "
              f"first does not answer the second:")
        for d in diffs:
            print(f"  - {d}")
        return 1
    if args.view:
        print(json.dumps(question_view(doc), indent=2, ensure_ascii=False))
        return 0
    print(question_digest(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
