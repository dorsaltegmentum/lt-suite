# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Maintainer tools: gen_config_docs, check_skills, node_tests, check_isolation, check_release."""
from __future__ import annotations

import importlib.util
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
ID_NAME = "dorsaltegmentum"
ID_EMAIL = "208164922+dorsaltegmentum@users.noreply.github.com"


def tool(name: str, *args: str, cwd: Path | None = None, env: dict | None = None,
         timeout: int = 300) -> subprocess.CompletedProcess:
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(TOOLS / name), *args], capture_output=True,
                          text=True, cwd=cwd or REPO, env=e, timeout=timeout)


def w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ----------------------------------------------------------------- gen_config_docs

def test_config_docs_are_current():
    r = tool("gen_config_docs.py", "--check")
    assert r.returncode == 0, r.stdout


def test_config_docs_cover_every_schema_key():
    text = (REPO / "docs" / "configuration.md").read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("core_test_schema", Path(__file__).with_name("test_schema.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for key in mod.DESIGN_KEYS:
        assert f"| `{key}` |" in text, key


def test_config_docs_check_detects_drift(tmp_path):
    root = tmp_path / "r"
    shutil.copytree(REPO / "shared", root / "shared", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "docs").mkdir()
    assert tool("gen_config_docs.py", "--root", str(root)).returncode == 0
    assert tool("gen_config_docs.py", "--root", str(root), "--check").returncode == 0
    doc = root / "docs" / "configuration.md"
    doc.write_text(doc.read_text() + "hand edit\n")
    r = tool("gen_config_docs.py", "--root", str(root), "--check")
    assert r.returncode == 1 and "out of date" in r.stdout


# ----------------------------------------------------------------- check_skills

def _block() -> str:
    text = (REPO / "shared" / "references" / "safety.md").read_text(encoding="utf-8")
    a = text.index("<!-- lt-suite safety: begin -->")
    b = text.index("<!-- lt-suite safety: end -->") + len("<!-- lt-suite safety: end -->")
    return text[a:b]


def make_skill(root: Path, name: str = "demo-skill", *, fm: str | None = None,
               body: str | None = None) -> Path:
    d = root / "skills" / name
    fm = fm if fm is not None else (
        f"name: {name}\ndescription: Does one thing for tests.\nlicense: AGPL-3.0-or-later\n"
        "compatibility: \"Python 3.11+\"\nmetadata:\n  version: \"0.1.0\"\n")
    body = body if body is not None else f"# Demo\n\n{_block()}\n\nSteps.\n"
    w(d / "SKILL.md", f"---\n{fm}---\n{body}")
    return d


@pytest.fixture
def skills_root(tmp_path) -> Path:
    root = tmp_path / "r"
    w(root / "shared" / "references" / "safety.md",
      (REPO / "shared" / "references" / "safety.md").read_text(encoding="utf-8"))
    return root


def test_safety_block_is_ten_lines():
    lines = _block().splitlines()
    assert len(lines) == 12 and lines[1].startswith("## ")


def test_check_skills_passes_a_good_skill(skills_root):
    d = make_skill(skills_root)
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 0, r.stdout
    assert "ok demo-skill" in r.stdout


@pytest.mark.parametrize("fm, needle", [
    ("name: other\ndescription: x\nlicense: AGPL-3.0-or-later\nmetadata:\n  version: \"0.1.0\"\n",
     "differs from the folder"),
    ("name: demo-skill\ndescription: " + "x" * 201 + "\nlicense: AGPL-3.0-or-later\nmetadata:\n  version: \"0.1.0\"\n",
     "201 chars"),
    ("name: demo-skill\ndescription: uses <tags>\nlicense: AGPL-3.0-or-later\nmetadata:\n  version: \"0.1.0\"\n",
     "contains < or >"),
    ("name: demo-skill\ndescription: x\ncompatibility: " + "y" * 501 + "\nlicense: AGPL-3.0-or-later\n"
     "metadata:\n  version: \"0.1.0\"\n", "compatibility is 501"),
    ("name: demo-skill\ndescription: x\nmetadata:\n  version: \"0.1.0\"\n", "license must be"),
    ("name: demo-skill\ndescription: x\nlicense: MIT\nmetadata:\n  version: \"0.1.0\"\n", "license must be"),
    ("name: demo-skill\ndescription: x\nlicense: AGPL-3.0-or-later\n", "metadata.version missing"),
    ("name: demo-skill\ndescription: x\nlicense: AGPL-3.0-or-later\nmetadata:\n  version: \"0.1.0\"\n"
     "  phase: \"3\"\n", "metadata.phase"),
    ("name: demo-skill\ndescription: x\nlicense: AGPL-3.0-or-later\nallowed-tools: Bash\nmetadata:\n"
     "  version: \"0.1.0\"\n", "allowed-tools"),
    ("name: demo-skill\ndescription: >-\n  folded text\n  over two lines " + "z" * 200 +
     "\nlicense: AGPL-3.0-or-later\nmetadata:\n  version: \"0.1.0\"\n", "chars; the limit is 200"),
])
def test_check_skills_frontmatter_failures(skills_root, fm, needle):
    d = make_skill(skills_root, fm=fm)
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and needle in r.stdout, r.stdout


def test_check_skills_body_rules(skills_root):
    d = make_skill(skills_root, body="no safety block here\n")
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "inline safety block" in r.stdout
    changed = _block().replace("Never submit", "Rarely submit")
    d = make_skill(skills_root, body=changed + "\n")
    assert tool("check_skills.py", "--root", str(skills_root), str(d)).returncode == 1
    d = make_skill(skills_root, body=_block() + "\n" + "line\n" * 500)
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "the limit is 500" in r.stdout
    d = make_skill(skills_root, body=_block() + "\nThen fetch('http://127.0.0.1:8733/lib.js') in the page.\n")
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "loopback" in r.stdout
    d = make_skill(skills_root)
    w(d / "references" / "x.md", "Run curl http://localhost:8731/ to test.\n")
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "references/x.md:1" in r.stdout


def test_check_skills_size_count_symlink(skills_root):
    d = make_skill(skills_root)
    for i in range(101):
        w(d / "assets" / f"f{i}.txt", "x")
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 0 and "WARN demo-skill: 102 files" in r.stdout
    (d / "assets" / "big.bin").write_bytes(b"\0" * (10 * 1024 * 1024 + 1))
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "MB; the limit is 10 MB" in r.stdout
    (d / "assets" / "big.bin").unlink()
    try:
        os.symlink(d / "SKILL.md", d / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create symlinks here")
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "symlink link.md" in r.stdout


def test_check_skills_missing_skill_md(skills_root):
    d = skills_root / "skills" / "empty-skill"
    d.mkdir(parents=True)
    r = tool("check_skills.py", "--root", str(skills_root), str(d))
    assert r.returncode == 1 and "no SKILL.md" in r.stdout


# ----------------------------------------------------------------- node_tests

NODE_OK = """const test = require('node:test');
test.describe('lost-save', () => {
  test.it('one', () => {});
  test.it('two', () => {});
});
test('lost-save: flat', () => {});
test('other', () => {});
"""


@pytest.mark.requires_bin("node")
def test_node_tests_counts(tmp_path):
    w(tmp_path / "t" / "a.test.js", NODE_OK)
    w(tmp_path / "t" / "node-expect.json", '{"groups": {"lost-save": 3}, "min_tests": 4}')
    r = tool("node_tests.py", str(tmp_path / "t"))
    assert r.returncode == 0, r.stdout
    assert "group lost-save: 3 passing" in r.stdout and "4 passed" in r.stdout
    r = tool("node_tests.py", str(tmp_path / "t"), "--name", "lost-save", "--expect", "4")
    assert r.returncode == 1 and "expected at least 4" in r.stdout
    r = tool("node_tests.py", str(tmp_path / "t"), "--expect-files", "2")
    assert r.returncode == 1 and "expected at least 2" in r.stdout
    r = tool("node_tests.py", str(tmp_path / "t"), "--expect-min", "5")
    assert r.returncode == 1


@pytest.mark.requires_bin("node")
def test_node_tests_fail_on_zero_files_zero_tests_and_failures(tmp_path):
    (tmp_path / "empty").mkdir()
    r = tool("node_tests.py", str(tmp_path / "empty"))
    assert r.returncode == 1 and "no *.test.js files" in r.stdout
    w(tmp_path / "none" / "a.test.js", "// no tests here\n")
    r = tool("node_tests.py", str(tmp_path / "none"))
    assert r.returncode == 1 and "no test passed" in r.stdout
    w(tmp_path / "bad" / "a.test.js",
      "const test = require('node:test');\nconst assert = require('node:assert');\n"
      "test('works', () => {});\ntest('breaks', () => assert.equal(1, 2));\n")
    r = tool("node_tests.py", str(tmp_path / "bad"))
    assert r.returncode == 1 and "failed: breaks" in r.stdout


def test_node_tests_usage_errors(tmp_path):
    assert tool("node_tests.py", str(tmp_path / "missing")).returncode == 2
    assert tool("node_tests.py", str(tmp_path), "--name", "x").returncode == 2
    assert tool("node_tests.py", str(tmp_path), "--node", "lt-no-such-node-binary").returncode == 2


def test_tap_parser_nesting():
    sys.path.insert(0, str(TOOLS))
    try:
        import node_tests
    finally:
        sys.path.pop(0)
    tap = ("TAP version 13\n# Subtest: g\n    # Subtest: a\n    ok 1 - a\n    # Subtest: inner\n"
           "        # Subtest: deep\n        not ok 1 - deep\n    not ok 2 - inner\n"
           "not ok 1 - g\n# Subtest: s\nok 2 - s # SKIP\n# Subtest: t\nok 3 - t # TODO later\n")
    res = node_tests.parse_tap(tap)
    leaves = {p.name: p for p in res.leaves}
    assert set(leaves) == {"a", "deep", "s", "t"}
    assert leaves["deep"].ancestors == ["g", "inner"] and not leaves["deep"].ok
    assert [p.name for p in res.failed] == ["deep"]
    assert {p.name for p in res.skipped} == {"s", "t"}
    assert node_tests.in_group(leaves["deep"], "g") and not node_tests.in_group(leaves["a"], "inner")


# ----------------------------------------------------------------- check_isolation

def test_check_isolation_runs_scripts_alone(tmp_path):
    root = tmp_path / "r"
    good = root / "skills" / "good"
    w(good / "scripts" / "run.py", "import sys\nfrom pathlib import Path\n"
      "sys.path.insert(0, str(Path(__file__).resolve().parent / '_shared'))\n"
      "import ltcore\nimport argparse\n"
      "if __name__ == '__main__':\n    argparse.ArgumentParser().parse_args()\n")
    shutil.copytree(REPO / "shared" / "ltcore", good / "scripts" / "_shared" / "ltcore",
                    ignore=shutil.ignore_patterns("__pycache__"))
    w(good / "scripts" / "_shared" / "ltcore" / "validate.py", "OK = True\n")
    w(good / "scripts" / "lib_only.py", "X = 1\n")
    bad = root / "skills" / "bad"
    w(bad / "scripts" / "run.py", "import ltcore_missing_xyz\nif __name__ == '__main__':\n    pass\n")
    r = tool("check_isolation.py", "--root", str(root), "--skill", "good")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok good/scripts/run.py --help" in r.stdout and "import ltcore.validate" in r.stdout
    assert "ok good/scripts/lib_only.py --help" in r.stdout
    r = tool("check_isolation.py", "--root", str(root))
    assert r.returncode == 1 and "FAIL bad: scripts/run.py --help exited 1" in r.stdout
    assert tool("check_isolation.py", "--root", str(root), "--skill", "nope").returncode == 2


def test_isolation_hides_repo_shared(tmp_path):
    """A script that only works with the repo's shared/ on the path fails alone."""
    root = tmp_path / "r"
    w(root / "skills" / "leaky" / "scripts" / "run.py",
      "import ltcore\nif __name__ == '__main__':\n    pass\n")
    r = tool("check_isolation.py", "--root", str(root), env={"PYTHONPATH": str(REPO / "shared")})
    assert r.returncode == 1 and "ModuleNotFoundError" in r.stdout


def test_isolation_checks_scripts_without_main_guard(tmp_path):
    """A missing import must fail isolation even without a main guard."""
    root = tmp_path / "r"
    w(root / "skills" / "broken" / "scripts" / "run.py", "import ltcore_missing_xyz\n")
    r = tool("check_isolation.py", "--root", str(root))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "scripts/run.py --help exited 1" in r.stdout
    assert "ModuleNotFoundError" in r.stdout


def test_isolation_rejects_incomplete_discovery(tmp_path):
    root = tmp_path / "r"
    (root / "skills" / "empty" / "scripts").mkdir(parents=True)
    r = tool("check_isolation.py", "--root", str(root))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "entry" in r.stdout and "FAIL empty" in r.stdout


# ----------------------------------------------------------------- check_release

def git(cwd: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, **(env or {})})


def commit(cwd: Path, msg: str, *, name=ID_NAME, email=ID_EMAIL, date="2000-01-01T00:00:00+0000",
           committer_name=ID_NAME, committer_email=ID_EMAIL, author_date=None, committer_date=None):
    """Commit with author and committer set apart; both dates default to ``date``."""
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": committer_name, "GIT_COMMITTER_EMAIL": committer_email,
           "GIT_AUTHOR_DATE": author_date or date, "GIT_COMMITTER_DATE": committer_date or date}
    # Build fixture objects directly; never stage files or invoke git commit.
    def object_id(*args, data):
        r = subprocess.run(["git", "-C", str(cwd), *args], input=data, text=True,
                           capture_output=True, check=True)
        return r.stdout.strip()

    tree = object_id("mktree", data="")
    parent = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--verify", "HEAD"],
                            capture_output=True, text=True)

    def identity(who):
        date_value = env[f"GIT_{who}_DATE"]
        stamp = int(datetime.datetime.fromisoformat(date_value).timestamp())
        return f"{env[f'GIT_{who}_NAME']} <{env[f'GIT_{who}_EMAIL']}> {stamp} {date_value[-5:]}"

    body = f"tree {tree}\n"
    if parent.returncode == 0:
        body += f"parent {parent.stdout.strip()}\n"
    body += f"author {identity('AUTHOR')}\ncommitter {identity('COMMITTER')}\n\n{msg}\n"
    oid = object_id("hash-object", "-t", "commit", "-w", "--stdin", data=body)
    git(cwd, "update-ref", "HEAD", oid)


@pytest.fixture
def release_repo(tmp_path) -> Path:
    r = tmp_path / "rel"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    w(r / "README.md", "hello\n")
    return r


@pytest.mark.requires_bin("git")
def test_check_release_history(release_repo):
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators")
    assert r.returncode == 1 and "no commits" in r.stdout
    commit(release_repo, "Add readme")
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators")
    assert r.returncode == 0, r.stdout
    commit(release_repo, "Second", date="2000-01-01T11:00:00+1100")
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators")
    assert r.returncode == 1 and "offset is not +0000" in r.stdout


@pytest.mark.requires_bin("git")
@pytest.mark.parametrize("kwargs, msg, needle", [
    ({"name": "someone", "email": "someone@example.org"}, "x", "author is not the release identity"),
    ({"committer_name": "someone", "committer_email": "someone@example.org"}, "x",
     "committer is not the release identity"),
    ({"committer_date": "2000-01-01T11:00:00+1100"}, "x", "committer date offset is not +0000"),
    ({"author_date": "2000-01-01T11:00:00+1100"}, "x", "author date offset is not +0000"),
    ({}, "x\n\nCo-Authored-By: Someone <someone@example.org>", "commit trailer"),
    ({}, "x\n\nClaude-" "Session: https://example.org/s", "commit trailer"),
    ({}, "x\n\nsee https://claude" ".ai/x", "links an agent session"),
    ({}, "x\n\nSigned-off-by: Someone <someone@example.org>", "commit trailer"),
])
def test_check_release_bad_commits(release_repo, kwargs, msg, needle):
    commit(release_repo, msg, **kwargs)
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators")
    # each bad commit trips exactly one rule, so a dead check cannot hide behind another one
    fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
    assert r.returncode == 1 and len(fails) == 1 and needle in fails[0], r.stdout


@pytest.mark.requires_bin("git")
@pytest.mark.parametrize("trailer", [
    f"Signed-off-by: {ID_NAME} <{ID_EMAIL}>",
    "Reviewed-by: Reviewer <reviewer@example.invalid>",
    "Custom-Token: value",
    "Acked-by: Reviewer\n continuation",
])
def test_release_rejects_every_commit_trailer(release_repo, trailer):
    commit(release_repo, "Release\n\n" + trailer)
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "commit trailer" in r.stdout


@pytest.mark.requires_bin("git")
def test_release_allows_colon_in_subject(release_repo):
    commit(release_repo, "Fix: select the configured venv\n\nUse the selected config before re-execution.")
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators")
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_release_placeholder_and_generators(tmp_path):
    root = tmp_path / "plain"
    w(root / "a.txt", "fine\n")
    w(root / "b.md", "value: " + "T" + "BD\n")
    w(root / "c.bin", "\0binary " + "T" + "BD")
    r = tool("check_release.py", "--root", str(root), "--skip-history")
    assert r.returncode == 1
    assert "b.md:1: placeholder token" in r.stdout and "c.bin" not in r.stdout
    assert "tools/gen_manifests.py: generator missing" in r.stdout


@pytest.mark.requires_bin("git")
def test_release_public_history_accepts_contributors_after_baseline(release_repo):
    """Contributor identities, offsets and DCO trailers are valid after the initial release."""
    commit(release_repo, "Initial release")
    git(release_repo, "tag", "v0.1.0")
    commit(release_repo, f"Contribution\n\nSigned-off-by: Contributor <{ID_EMAIL}>",
           name="Contributor", email=ID_EMAIL,
           committer_name="Contributor", committer_email=ID_EMAIL,
           date="2000-01-01T10:00:00+1000")
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators",
             "--public-history", "--initial-release-ref", "v0.1.0")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.requires_bin("git")
def test_release_public_history_preserves_baseline_rules(release_repo):
    """Public CI must still reject a trailer inside the initial history."""
    commit(release_repo, f"Initial\n\nSigned-off-by: Contributor <{ID_EMAIL}>")
    git(release_repo, "tag", "v0.1.0")
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators",
             "--public-history", "--initial-release-ref", "v0.1.0")
    assert r.returncode == 1 and "commit trailer" in r.stdout, r.stdout + r.stderr


@pytest.mark.requires_bin("git")
@pytest.mark.parametrize("message", ["Session\n\nClaude-" "Session: value", "See https://claude" ".ai/x"])
def test_public_history_keeps_agent_session_ban(release_repo, message):
    """Contributor mode cannot allow session identifiers or links into public history."""
    commit(release_repo, message)
    r = tool("check_release.py", "--root", str(release_repo), "--skip-generators", "--public-history")
    assert r.returncode == 1 and "agent session" in r.stdout, r.stdout + r.stderr


def test_release_rejects_unresolved_html_review_markers(tmp_path):
    """Review markers in prose must fail even when no placeholder token remains."""
    w(tmp_path / "docs" / "guide.md", "Reviewed.\n<!-- " + "VERIFY" + " -->\n")
    r = tool("check_release.py", "--root", str(tmp_path), "--skip-generators", "--skip-history")
    assert r.returncode == 1 and "unresolved review marker" in r.stdout, r.stdout + r.stderr


def test_release_tag_requires_date_and_workflow_gates(tmp_path):
    """Tagged releases require a real changelog date, checksums and provenance before upload."""
    w(tmp_path / "pyproject.toml", '[project]\nversion = "0.1.0"\n')
    w(tmp_path / "CHANGELOG.md", "## [Unreleased]\n")
    args = ("--root", str(tmp_path), "--skip-generators", "--skip-history", "--tag", "v0.1.0")
    r = tool("check_release.py", *args)
    assert r.returncode == 1 and "dated changelog" in r.stdout, r.stdout + r.stderr
    w(tmp_path / "CHANGELOG.md", "## [0.1.0] - 2000-02-30\n")
    assert tool("check_release.py", *args).returncode == 1
    w(tmp_path / "CHANGELOG.md", "## [0.1.0] - 2000-02-29\n")
    r = tool("check_release.py", *args)
    assert r.returncode == 1 and "release workflow" in r.stdout, r.stdout + r.stderr
    shutil.copyfile(REPO / ".github" / "workflows" / "release.yml",
                    w(tmp_path / ".github" / "workflows" / "release.yml", ""))
    r = tool("check_release.py", *args)
    assert r.returncode == 0, r.stdout + r.stderr
    path = tmp_path / ".github" / "workflows" / "release.yml"
    path.write_text(path.read_text().replace("attest-build-provenance@", "other-action@"))
    r = tool("check_release.py", *args)
    assert r.returncode == 1 and "provenance" in r.stdout, r.stdout + r.stderr


def test_release_tag_reports_malformed_workflow_as_error(tmp_path):
    """Malformed workflow YAML must produce the documented checker-error exit code."""
    w(tmp_path / "pyproject.toml", '[project]\nversion = "0.1.0"\n')
    w(tmp_path / "CHANGELOG.md", "## [0.1.0] - 2000-02-29\n")
    w(tmp_path / ".github" / "workflows" / "release.yml", "jobs: [invalid\n")
    r = tool("check_release.py", "--root", str(tmp_path), "--skip-generators", "--skip-history",
             "--tag", "v0.1.0")
    assert r.returncode == 2 and "Traceback" not in r.stderr, r.stdout + r.stderr
