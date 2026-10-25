#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the JavaScript tests with node --test and fail on counts that are too low.

A test runner that finds no files, or files with no tests, exits 0 and proves
nothing. This wrapper runs every *.test.js / *.test.mjs under DIR with the TAP
reporter, counts the leaf tests that passed, and fails when:
  - no test file is found, or no test ran
  - any test fails
  - fewer files than --expect-files, or fewer passing tests than --expect-min
  - a named group has fewer passing tests than its expected count

Expected group counts come from --name G --expect N, from --expect-file F (JSON),
or, when neither is given, from every node-expect.json under DIR. The JSON is
{"groups": {"lost-save": 9}, "files": 2, "min_tests": 20} or simply {"lost-save": 9}.
A leaf counts toward group G when G is its name, an ancestor's name, or a prefix
followed by ":" or " ".

    python3 tools/node_tests.py tests/browser --expect-file tests/browser/node-expect.json
    python3 tools/node_tests.py tests/fill --name lost-save --expect 9
    python3 tools/node_tests.py tests --expect-files 12 --expect-min 300
Exit 0 pass, 1 fail, 2 usage or node missing.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POINT = re.compile(r"^(?P<ind> *)(?P<not>not )?ok \d+(?: - (?P<name>.*?))?"
                   r"(?:\s+#\s*(?P<dir>SKIP|TODO)\b.*)?$", re.I)
SUBTEST = re.compile(r"^(?P<ind> *)# Subtest: (?P<name>.*)$")


@dataclass
class Point:
    name: str
    ok: bool
    directive: str
    ancestors: list[str]
    leaf: bool = True
    wrapper: bool = False   # node's point for a whole test file; not a test




@dataclass
class Result:
    points: list[Point] = field(default_factory=list)

    @property
    def leaves(self) -> list[Point]:
        return [p for p in self.points if p.leaf]

    @property
    def passed(self) -> list[Point]:
        return [p for p in self.leaves if p.ok and not p.directive and not p.wrapper]

    @property
    def failed(self) -> list[Point]:
        # A failed file point means the file itself did not load or run.
        return [p for p in self.leaves if not p.ok and p.directive.upper() != "TODO"]

    @property
    def skipped(self) -> list[Point]:
        return [p for p in self.leaves if p.directive and not p.wrapper]

    def mark_files(self, files: list[str]) -> None:
        """Node reports a file as a test point named by its path; those are not tests."""
        names = set(files)
        for p in self.points:
            if p.name in names and not p.ancestors:
                p.wrapper = True


def _unescape(name: str) -> str:
    return name.replace("\\#", "#").replace("\\\\", "\\").strip()


def parse_tap(text: str) -> Result:
    """Build the test tree from node's TAP output (nesting by indentation)."""
    res = Result()
    stack: list[list] = []  # [indent, name, had_children]
    for raw in text.splitlines():
        m = SUBTEST.match(raw)
        if m:
            ind = len(m.group("ind"))
            while stack and stack[-1][0] >= ind:
                stack.pop()
            stack.append([ind, _unescape(m.group("name")), False])
            continue
        m = POINT.match(raw)
        if not m:
            continue
        ind = len(m.group("ind"))
        name = _unescape(m.group("name") or "")
        while stack and stack[-1][0] > ind:
            stack.pop()
        had_children = False
        if stack and stack[-1][0] == ind:
            had_children = stack[-1][2]
            stack.pop()
        if stack:
            stack[-1][2] = True
        res.points.append(Point(name=name, ok=m.group("not") is None,
                                directive=(m.group("dir") or ""),
                                ancestors=[s[1] for s in stack], leaf=not had_children))
    return res


def in_group(p: Point, group: str) -> bool:
    for n in [p.name] + p.ancestors:
        if n == group or n.startswith(group + ":") or n.startswith(group + " "):
            return True
    return False


def find_files(d: Path) -> list[Path]:
    pats = ("*.test.js", "*.test.mjs", "*.test.cjs")
    files = {p for pat in pats for p in d.rglob(pat) if "node_modules" not in p.parts}
    return sorted(files)


def load_expect(paths: list[Path]) -> tuple[dict[str, int], int, int]:
    groups: dict[str, int] = {}
    files = 0
    min_tests = 0
    for p in paths:
        doc = json.loads(p.read_text(encoding="utf-8"))
        g = doc.get("groups") if isinstance(doc.get("groups"), dict) else {
            k: v for k, v in doc.items() if isinstance(v, int) and k not in ("files", "min_tests")}
        for k, v in g.items():
            groups[k] = max(groups.get(k, 0), int(v))
        files = max(files, int(doc.get("files", 0) or 0))
        min_tests = max(min_tests, int(doc.get("min_tests", 0) or 0))
    return groups, files, min_tests


def main(argv: list[str] | None = None) -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path, help="folder searched for *.test.js files")
    ap.add_argument("--expect-file", type=Path, action="append", help="JSON with group counts")
    ap.add_argument("--expect-files", type=int, default=0, help="minimum number of test files")
    ap.add_argument("--expect-min", type=int, default=0, help="minimum number of passing tests")
    ap.add_argument("--name", help="group name for --expect")
    ap.add_argument("--expect", type=int, help="minimum passing tests in --name")
    ap.add_argument("--node", default="node", help="node executable")
    ap.add_argument("--timeout", type=int, default=900, help="seconds")
    a = ap.parse_args(argv)
    if (a.name is None) != (a.expect is None):
        print("error: --name and --expect go together", file=sys.stderr)
        return 2
    d = a.dir if a.dir.is_absolute() else Path.cwd() / a.dir
    if not d.is_dir():
        print(f"error: {a.dir} is not a folder", file=sys.stderr)
        return 2
    node = shutil.which(a.node)
    if node is None:
        print(f"error: {a.node} not found on PATH", file=sys.stderr)
        return 2

    expect_paths = a.expect_file or ([] if a.name else sorted(d.rglob("node-expect.json")))
    for p in expect_paths:
        if not p.is_file():
            print(f"error: expect file {p} not found", file=sys.stderr)
            return 2
    try:
        groups, min_files, min_tests = load_expect(expect_paths)
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: cannot read expect file: {exc}", file=sys.stderr)
        return 2
    if a.name:
        groups[a.name] = max(groups.get(a.name, 0), a.expect)
    min_files = max(min_files, a.expect_files)
    min_tests = max(min_tests, a.expect_min)

    files = find_files(d)
    problems: list[str] = []
    if not files:
        print(f"FAIL no *.test.js files under {a.dir}")
        return 1
    try:
        proc = subprocess.run([node, "--test", "--test-reporter=tap", *map(str, files)],
                              cwd=REPO, capture_output=True, text=True, timeout=a.timeout, encoding="utf-8")
    except subprocess.TimeoutExpired:
        print(f"FAIL node --test did not finish within {a.timeout} s")
        return 1
    res = parse_tap(proc.stdout)
    res.mark_files([str(f) for f in files])
    for p in res.failed:
        trail = " > ".join(p.ancestors + [p.name])
        problems.append(f"failed: {trail}")
    if proc.returncode != 0 and not res.failed:
        problems.append(f"node exited {proc.returncode}")
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
        problems.extend("  " + t for t in tail)
    if not res.passed:
        problems.append("no test passed")
    if len(files) < min_files:
        problems.append(f"{len(files)} test files; expected at least {min_files}")
    if len(res.passed) < min_tests:
        problems.append(f"{len(res.passed)} passing tests; expected at least {min_tests}")
    for g, n in sorted(groups.items()):
        got = sum(1 for p in res.passed if in_group(p, g))
        line = f"group {g}: {got} passing (expected at least {n})"
        if got < n:
            problems.append(line)
        else:
            print(f"ok {line}")
    print(f"node tests: {len(files)} files, {len(res.passed)} passed, {len(res.failed)} failed, "
          f"{len(res.skipped)} skipped")
    for pr in problems:
        print(f"FAIL {pr}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
