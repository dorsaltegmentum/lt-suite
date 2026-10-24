# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Locate configured curriculum week folders and survey their source files.

Exit 0 for one match, 1 for none, 2 for ambiguity, 3 for usage or file errors.
Reports identify candidates; a person confirms the folder before drafting.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore

ltcore.bootstrap()
from ltcore import config

import argparse
import json
import os
import re


class Parser(argparse.ArgumentParser):
    """Keep resolver usage errors distinct from ambiguous candidates."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


def week_number(title: str, pattern: str) -> str | None:
    """One group is the week; an optional second group is the session."""
    rx = re.compile(pattern)
    if rx.groups not in (1, 2):
        raise ValueError("curriculum.week_number_pattern needs 1 or 2 capture groups")
    match = rx.search(title)
    if not match:
        return None
    return ".".join(str(int(part)) for part in match.groups() if part is not None)


def _visible_dirs(root: Path, skip_dirs: set[str]):
    """Walk without hidden, skipped or linked directories."""
    for base, dirs, _ in os.walk(root, followlinks=False, onerror=_walk_error):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".")
                         and name not in skip_dirs and not (Path(base) / name).is_symlink())
        yield Path(base), dirs


def _walk_error(error):
    raise error


def find_week_dirs(num: str, root: Path, pattern: str, skip_dirs: set[str]) -> list[Path]:
    """Match prefixed and bare folders at any depth beneath curriculum.root."""
    rx = re.compile(pattern.replace("{num}", re.escape(num)), re.IGNORECASE)
    hits = []
    for base, dirs in _visible_dirs(root, skip_dirs):
        matched = [name for name in dirs if rx.match(name)]
        hits.extend(base / name for name in matched)
        # A week may contain numbered topic folders; those are not more weeks.
        dirs[:] = [name for name in dirs if name not in matched]
    return sorted(hits)


def survey(week: Path, extensions: set[str], skip_dirs: set[str]) -> dict:
    """Count files by extension and immediate subject folder without reading them."""
    out = {"path": str(week), "block": week.parent.name, "name": week.name,
           "subjects": [], "files_by_ext": {}, "total_files": 0}
    subjects: dict[str, int] = {}
    for base, dirs in _visible_dirs(week, skip_dirs):
        if base == week:
            subjects.update({name: 0 for name in dirs})
        for f in sorted(base.iterdir()):
            if f.name.startswith(".") or f.is_symlink() or not f.is_file():
                continue
            out["total_files"] += 1
            relative = f.relative_to(week)
            if len(relative.parts) > 1:
                subjects[relative.parts[0]] += 1
            ext = f.suffix.lower()
            if ext in extensions:
                out["files_by_ext"][ext] = out["files_by_ext"].get(ext, 0) + 1
    out["subjects"] = [{"name": name, "files": n} for name, n in sorted(subjects.items())]
    return out


def _words(text: str, stopwords: set[str]) -> set[str]:
    return set(re.findall(r"[^\W\d_]{3,}", text.casefold())) - stopwords


def ilo_lines(num: str, title: str, path: Path | None, stopwords: set[str],
              limit: int = 8) -> list[str]:
    """Rank matching outcome lines by title overlap; retain competing headings."""
    if path is None:
        return []
    rx = re.compile(rf"(?<![\d.]){re.escape(num)}(?![\d.]|\d)")
    title_words = _words(title, stopwords)
    hits = []
    with path.open(encoding="utf-8") as stream:
        for index, line in enumerate(stream, 1):
            if rx.search(line):
                score = len(title_words & _words(line, stopwords))
                hits.append((score, index, line.strip()[:160]))
    hits.sort(key=lambda hit: (-hit[0], hit[1]))
    return [f"{'*' if score else ' '} {index}: {text}" for score, index, text in hits[:limit]]


def main(argv=None) -> int:
    ap = Parser(description=__doc__)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--module", type=Path, help="capture module.json")
    source.add_argument("--title", help="module title, for example 'Week 1 Demo'")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    try:
        cfg = config.load(args.config)
        title, course = args.title, ""
        if args.module:
            doc = json.loads(args.module.read_text(encoding="utf-8"))
            title = doc.get("lesson", {}).get("title", "")
            course = doc.get("lesson", {}).get("course_title", "")
        if not isinstance(title, str) or not isinstance(course, str):
            raise ValueError("module lesson title and course_title must be strings")
        root_value = cfg.get("curriculum.root")
        root = Path(root_value) if root_value else None
        if root is not None and not root.is_dir():
            raise ValueError(f"curriculum.root is not a directory: {root} "
                             f"(from {cfg.where('curriculum.root')})")
        num = week_number(title, cfg.get("curriculum.week_number_pattern"))
        skip = set(cfg.get("curriculum.skip_dirs"))
        hits = find_week_dirs(num, root, cfg.get("curriculum.week_dir_pattern"), skip) \
            if num and root else []
        outcome_value = cfg.get("curriculum.learning_outcomes_file")
        ilos = ilo_lines(num, title, Path(outcome_value) if outcome_value else None,
                         {s.casefold() for s in cfg.get("curriculum.title_stopwords")}) if num else []
        result = {
            "title": title, "course_title": course, "week_number": num,
            "curriculum_root": root_value, "curriculum_root_from": cfg.where("curriculum.root"),
            "learning_outcomes_file": outcome_value,
            "learning_outcomes_from": cfg.where("curriculum.learning_outcomes_file"),
            "matches": [survey(h, {e.lower() for e in cfg.get("curriculum.text_extensions")}, skip)
                        for h in hits],
            "ilo_hits": len(ilos), "ilo_sample": ilos,
            "status": "ok" if len(hits) == 1 else "ambiguous" if hits else "none",
            "lookup_disabled": root is None,
        }
    except (config.ConfigError, OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        print(f"resolver: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"module: {title}")
        print(f"week: {num or 'no configured week pattern matched'}")
        print(f"curriculum.root: {root_value or '(unset; lookup disabled)'} "
              f"(from {cfg.where('curriculum.root')})")
        for match in result["matches"]:
            print(f"match: {match['path']}; files: {match['total_files']}; "
                  f"by type: {match['files_by_ext']}")
            for subject in match["subjects"]:
                print(f"  {subject['name']}: {subject['files']} files")
        for line in ilos:
            print(f"outcome: {line}")
        if not hits:
            print("No candidate located. Ask for the Tier-1 folder; lookup failure is not source absence.")
        elif len(hits) > 1:
            print("Multiple candidates. Ask the user to choose.")
        print("Confirm the Tier-1 folder before drafting.")
    return {"ok": 0, "none": 1, "ambiguous": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
