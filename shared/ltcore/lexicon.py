# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inventory and check the lexicon: frontmatter, evidence anchors, review dates, budget.

The lexicon is read into an agent's context, so its cost is attention, not disk.
This prints the context budget and reports the ways an entry stops being worth
its place:

    malformed   no YAML frontmatter, a missing field, or a name that differs from the file
    unevidenced no evidence, or evidence that is not a docs anchor or a test reference
    anchor      evidence that points at a file, heading or test that does not exist
    overdue     past its review_by date: re-verify it or prune it, never renew on sight
    budget      the lexicon (entries plus README) outgrows its word budget

Without --check problems are listed and the exit code is 0. With --check, a
problem exits 1, except that --expired warn and --anchors warn report those two
kinds as warnings. Exit 3: no lexicon folder.

    PYTHONPATH=shared python3 -m ltcore.lexicon
    PYTHONPATH=shared python3 -m ltcore.lexicon --check --expired warn --anchors warn
    PYTHONPATH=shared python3 -m ltcore.lexicon --check --dir <data>/lexicon
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import re  # noqa: E402
import unicodedata  # noqa: E402

NEEDS: list[str] = []
# The source tree's lexicon folder, read when the checker runs there. Skills get
# the entries through sync.toml (references/_shared/lexicon/), not through this list.
OPTIONAL_NEEDS = ["lexicon/"]

REQUIRED = ("name", "description", "review_by", "evidence", "status")
STATUSES = ("active", "empty-by-construction", "deprecated")
FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
BUDGET_WORDS = 4000

DOC_ANCHOR = re.compile(r"^(docs/[A-Za-z0-9_./-]+\.md)#([a-z0-9][a-z0-9-]*)$")
TEST_ANCHOR = re.compile(r"^(tests/[A-Za-z0-9_./-]+\.(?:py|js|mjs))::([A-Za-z_][A-Za-z0-9_ .:-]*)$")

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent


def default_dir() -> Path:
    """shared/lexicon in the source tree; references/_shared/lexicon inside a skill."""
    src = SHARED / "lexicon"
    if src.is_dir():
        return src
    skill = SHARED.parent.parent  # <skill>/scripts/_shared -> <skill>
    return skill / "references" / "_shared" / "lexicon"


def default_repo_root(lexicon_dir: Path) -> Path:
    """The folder that holds docs/ and tests/: two levels above shared/lexicon."""
    return lexicon_dir.resolve().parent.parent


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML: ``key: value`` and ``key: [a, b]``. The frontmatter is a flat
    mapping by design, so no YAML library is needed."""
    m = FM.match(text)
    if not m:
        return None
    out: dict = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[k] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()] if inner else []
        else:
            out[k] = v.strip("'\"")
    return out


def as_date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def slugify(heading: str) -> str:
    """GitHub-style heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    s = unicodedata.normalize("NFKC", heading).strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def doc_anchors(path: Path) -> set[str]:
    """Anchors a Markdown file defines: heading slugs, <a id>, and {#id} attributes."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    out: set[str] = set()
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            title = m.group(1)
            explicit = re.search(r"\{#([A-Za-z0-9_-]+)\}\s*$", title)
            if explicit:
                out.add(explicit.group(1))
                title = title[:explicit.start()]
            out.add(slugify(title))
        for a in re.finditer(r"<a\s+(?:id|name)=[\"']([A-Za-z0-9_-]+)[\"']", line):
            out.add(a.group(1))
    return out


def has_test(path: Path, name: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    func = name.split("::")[-1]
    if path.suffix == ".py":
        return re.search(rf"^\s*(?:async\s+)?def\s+{re.escape(func)}\s*\(", text, re.M) is not None
    return any(q + name + q in text for q in ("'", '"', "`"))


def check_anchor(ev: str, repo_root: Path) -> str | None:
    """None when ``ev`` resolves; otherwise the reason it does not."""
    m = DOC_ANCHOR.match(ev)
    if m:
        f = repo_root / m.group(1)
        if not f.is_file():
            return f"{m.group(1)} does not exist"
        if m.group(2) not in doc_anchors(f):
            return f"{m.group(1)} has no heading or anchor #{m.group(2)}"
        return None
    m = TEST_ANCHOR.match(ev)
    if m:
        f = repo_root / m.group(1)
        if not f.is_file():
            return f"{m.group(1)} does not exist"
        if not has_test(f, m.group(2)):
            return f"{m.group(1)} defines no test {m.group(2)}"
        return None
    return "not a docs/<file>.md#<slug> or tests/<path>::<test> reference"


def run(lexicon_dir: Path, *, today: dt.date, repo_root: Path,
        expired: str = "fail", anchors: str = "fail", out=None) -> tuple[list[str], list[str]]:
    """Check every entry. Returns (failing problems, warnings) and prints the inventory."""
    out = out or sys.stdout
    entries = sorted(p for p in lexicon_dir.glob("*.md") if p.name != "README.md")
    fails: list[str] = []
    warns: list[str] = []

    def problem(kind: str, msg: str) -> None:
        mode = {"overdue": expired, "anchor": anchors}.get(kind, "fail")
        (fails if mode == "fail" else warns).append(f"{kind}: {msg}")

    readme = lexicon_dir / "README.md"
    readme_words = len(readme.read_text(encoding="utf-8").split()) if readme.is_file() else 0
    total = 0
    print(f"lexicon     {lexicon_dir}", file=out)
    print(f"{'entry':<32}{'words':>7}{'review_by':>13}  evidence", file=out)
    for p in entries:
        text = p.read_text(encoding="utf-8")
        words = len(text.split())
        total += words
        fm = parse_frontmatter(text)
        if fm is None:
            problem("malformed", f"{p.name}: no YAML frontmatter")
            print(f"{p.name:<32}{words:>7}{'-':>13}  MALFORMED", file=out)
            continue
        missing = [k for k in REQUIRED if k not in fm]
        if missing:
            problem("malformed", f"{p.name}: frontmatter missing {', '.join(missing)}")
        if fm.get("name") and fm["name"] != p.stem:
            problem("malformed", f"{p.name}: name {fm['name']!r} differs from the file name")
        if fm.get("status") and fm["status"] not in STATUSES:
            problem("malformed", f"{p.name}: status {fm['status']!r} is not one of {', '.join(STATUSES)}")
        ev = fm.get("evidence") or []
        if isinstance(ev, str):
            ev = [ev]
        if not ev:
            problem("unevidenced", f"{p.name}: no evidence; cite docs/design-notes.md#<slug> "
                    f"or tests/<path>::<test>")
        for e in ev:
            if not (DOC_ANCHOR.match(e) or TEST_ANCHOR.match(e)):
                problem("unevidenced", f"{p.name}: evidence {e!r} is not a docs/<file>.md#<slug> "
                        f"or tests/<path>::<test> reference")
                continue
            why = check_anchor(e, repo_root)
            if why:
                problem("anchor", f"{p.name}: evidence {e}: {why}")
        rb = as_date(fm.get("review_by"))
        if rb is None:
            problem("malformed", f"{p.name}: review_by is not an ISO date ({fm.get('review_by')!r})")
        elif rb < today:
            problem("overdue", f"{p.name}: overdue for review since {rb}; re-verify or prune it, "
                    f"do not renew the date on sight")
        flag = "" if (rb and rb >= today) else "  OVERDUE"
        print(f"{p.name:<32}{words:>7}{str(fm.get('review_by')):>13}  {', '.join(ev)}{flag}",
              file=out)
    loaded = total + readme_words
    n = len(entries)
    print(f"\n{n} entr{'y' if n == 1 else 'ies'}, {total} words (+{readme_words} README) = "
          f"{loaded} words of context [{loaded * 100 // BUDGET_WORDS}% of a {BUDGET_WORDS}-word "
          f"budget]", file=out)
    if loaded > BUDGET_WORDS:
        problem("budget", f"the lexicon is {loaded} words against a {BUDGET_WORDS}-word budget; "
                f"prune before adding")
    return fails, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.lexicon", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 on any failing problem")
    ap.add_argument("--dir", type=Path, help="lexicon folder (default: shared/lexicon, or the "
                    "skill's references/_shared/lexicon)")
    ap.add_argument("--repo-root", type=Path, help="folder that holds docs/ and tests/ "
                    "(default: two levels above the lexicon folder)")
    ap.add_argument("--expired", choices=("warn", "fail"), default="fail",
                    help="how --check treats an entry past review_by (default fail)")
    ap.add_argument("--anchors", choices=("warn", "fail"), default="fail",
                    help="how --check treats evidence whose target is missing (default fail)")
    ap.add_argument("--today", help="override today's date (ISO), for tests")
    a = ap.parse_args(argv)

    d = a.dir or default_dir()
    if not d.is_dir():
        print(f"FATAL: no lexicon folder at {d}", file=sys.stderr)
        return 3
    today = as_date(a.today) or dt.date.today()
    if a.today and as_date(a.today) is None:
        print(f"error: --today {a.today!r} is not an ISO date", file=sys.stderr)
        return 2
    root = a.repo_root or default_repo_root(d)
    fails, warns = run(d, today=today, repo_root=root, expired=a.expired, anchors=a.anchors)
    if warns or fails:
        print()
    for w in warns:
        print(f"  WARN     {w}")
    for f in fails:
        print(f"  PROBLEM  {f}")
    if not fails and not warns:
        print("no problems")
    return 1 if (a.check and fails) else 0


if __name__ == "__main__":
    raise SystemExit(main())
