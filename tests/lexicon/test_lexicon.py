# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lexicon checker: frontmatter, evidence anchors, review dates, budget, exit codes."""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

import pytest

from ltcore import lexicon

REPO = Path(__file__).resolve().parents[2]
SLUGS = {"state-split", "view-model-capture", "model-answer-policy", "checkpoint-detector",
         "completion-page-out-of-bounds", "two-numbering-schemes", "segments-and-connectives",
         "member-allow-list", "reference-quarantine", "save-wait", "fresh-load-verify",
         "single-attempt-approvals", "no-bulk-harvesting", "never-submit", "text-cleaners",
         "why-drawio", "question-digest"}


def entry(name: str, *, review_by: str = "2099-01-01", evidence: str = "[docs/notes.md#rule-one]",
          status: str = "active", extra: str = "") -> str:
    return (f"---\nname: {name}\ndescription: test entry\nreview_by: {review_by}\n"
            f"evidence: {evidence}\nstatus: {status}\n{extra}---\n\n# {name}\n\nBody text.\n")


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    lex = r / "shared" / "lexicon"
    lex.mkdir(parents=True)
    (r / "docs").mkdir()
    (r / "docs" / "notes.md").write_text(
        "# Notes\n\n## Rule one\n\ntext\n\n### Second rule {#custom-id}\n\n<a id=\"plain-anchor\"></a>\n"
        "```\n## not a heading\n```\n", encoding="utf-8")
    (r / "tests").mkdir()
    (r / "tests" / "test_x.py").write_text("def test_something():\n    pass\n", encoding="utf-8")
    (r / "tests" / "y.test.js").write_text("test('js case', () => {});\n", encoding="utf-8")
    (lex / "README.md").write_text("# Lexicon\n", encoding="utf-8")
    return r


def check(repo: Path, *args: str) -> int:
    return lexicon.main(["--dir", str(repo / "shared" / "lexicon"), "--today", "2001-06-01", *args])


def put(repo: Path, name: str, text: str) -> None:
    (repo / "shared" / "lexicon" / f"{name}.md").write_text(text, encoding="utf-8")


def test_shipped_lexicon_passes_with_anchor_warnings(capsys):
    rc = lexicon.main(["--check", "--expired", "fail", "--anchors", "warn"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "PROBLEM" not in out


def test_shipped_entries_cite_design_note_slugs():
    for p in sorted((REPO / "shared" / "lexicon").glob("*.md")):
        if p.name == "README.md":
            continue
        fm = lexicon.parse_frontmatter(p.read_text(encoding="utf-8"))
        assert fm is not None and fm["name"] == p.stem
        assert fm["evidence"], p.name
        for ev in fm["evidence"]:
            m = lexicon.DOC_ANCHOR.match(ev) or lexicon.TEST_ANCHOR.match(ev)
            assert m, ev
            if ev.startswith("docs/design-notes.md#"):
                assert ev.split("#", 1)[1] in SLUGS, ev


def test_shipped_review_by_is_a_year_out():
    for p in (REPO / "shared" / "lexicon").glob("*.md"):
        if p.name == "README.md":
            continue
        rb = lexicon.as_date(lexicon.parse_frontmatter(p.read_text(encoding="utf-8"))["review_by"])
        assert rb is not None and rb.year >= 2027


def test_good_entries_pass(repo, capsys):
    put(repo, "a", entry("a"))
    put(repo, "b", entry("b", evidence="[docs/notes.md#custom-id, docs/notes.md#plain-anchor, "
                                        "tests/test_x.py::test_something, tests/y.test.js::js case]"))
    assert check(repo, "--check") == 0
    assert "no problems" in capsys.readouterr().out


@pytest.mark.parametrize("text, needle", [
    ("no frontmatter\n", "no YAML frontmatter"),
    (entry("x").replace("status: active\n", ""), "frontmatter missing status"),
    (entry("other-name"), "differs from the file name"),
    (entry("x", status="draft"), "status 'draft'"),
    (entry("x", evidence="[]"), "no evidence"),
    (entry("x", evidence="[remembered-claim]"), "is not a docs/<file>.md#<slug>"),
    (entry("x", review_by="soon"), "not an ISO date"),
])
def test_failing_problems(repo, capsys, text, needle):
    put(repo, "x", text)
    assert check(repo, "--check") == 1
    assert needle in capsys.readouterr().out
    assert check(repo) == 0                       # without --check: report only


def test_expired_warn_or_fail(repo, capsys):
    put(repo, "old", entry("old", review_by="2001-01-01"))
    assert check(repo, "--check", "--expired", "fail") == 1
    out = capsys.readouterr().out
    assert "PROBLEM  overdue: old.md" in out and "OVERDUE" in out
    assert check(repo, "--check", "--expired", "warn") == 0
    assert "WARN     overdue: old.md" in capsys.readouterr().out
    assert check(repo, "--check") == 1            # default is fail


@pytest.mark.parametrize("evidence, needle", [
    ("[docs/missing.md#rule-one]", "docs/missing.md does not exist"),
    ("[docs/notes.md#no-such-slug]", "has no heading or anchor #no-such-slug"),
    ("[docs/notes.md#not-a-heading]", "has no heading or anchor"),
    ("[tests/test_x.py::test_absent]", "defines no test test_absent"),
    ("[tests/nope.py::test_a]", "tests/nope.py does not exist"),
])
def test_anchor_warn_or_fail(repo, capsys, evidence, needle):
    put(repo, "x", entry("x", evidence=evidence))
    assert check(repo, "--check", "--anchors", "fail") == 1
    assert needle in capsys.readouterr().out
    assert check(repo, "--check", "--anchors", "warn") == 0
    assert "WARN     anchor:" in capsys.readouterr().out


def test_budget(repo, capsys):
    put(repo, "big", entry("big").replace("Body text.", "word " * 4100))
    assert check(repo, "--check") == 1
    assert "budget" in capsys.readouterr().out


def test_fatal_without_folder(tmp_path, capsys):
    assert lexicon.main(["--dir", str(tmp_path / "none")]) == 3


def test_bad_today_is_usage_error(repo):
    assert lexicon.main(["--dir", str(repo / "shared" / "lexicon"), "--today", "tomorrow"]) == 2


def test_slugify_and_anchors(tmp_path):
    assert lexicon.slugify("Save wait") == "save-wait"
    assert lexicon.slugify("Model-answer policy!") == "model-answer-policy"
    assert lexicon.slugify("Why draw.io?") == "why-drawio"
    doc = tmp_path / "d.md"
    doc.write_text("## Save wait\n## Two numbering schemes\n", encoding="utf-8")
    assert {"save-wait", "two-numbering-schemes"} <= lexicon.doc_anchors(doc)


def test_cli_module_entry_point(run_py):
    r = run_py("-m", "ltcore.lexicon", "--check", "--expired", "fail", "--anchors", "warn")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 entries" in r.stdout


def test_run_returns_lists(repo):
    put(repo, "a", entry("a", review_by="2001-01-01"))
    fails, warns = lexicon.run(repo / "shared" / "lexicon", today=dt.date(2001, 6, 1),
                               repo_root=repo, expired="warn", anchors="fail", out=io.StringIO())
    assert fails == [] and len(warns) == 1
