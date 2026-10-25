# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Python version gate and the venv bootstrap in ltcore/__init__.py."""
from __future__ import annotations

import ast
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import ltcore

REPO = Path(__file__).resolve().parents[2]
SHARED = REPO / "shared"
MESSAGE = ("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
           "python3 <setup skill>/scripts/setup.py bootstrap")

# The gate every entry script starts with (DESIGN: import rule). It must parse on 3.8.
ENTRY_GATE = '''\
import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore; ltcore.bootstrap()
print("past the gate")
'''


def _fake_version(code: str) -> str:
    return "import sys; sys.version_info = (3, 10, 14, 'final', 0)\n" + code


def test_init_parses_on_python_38():
    ast.parse((SHARED / "ltcore" / "__init__.py").read_text(encoding="utf-8"), feature_version=(3, 8))


def test_entry_gate_parses_on_python_38():
    ast.parse(ENTRY_GATE, feature_version=(3, 8))


def test_check_python_message():
    with pytest.raises(SystemExit) as ei:
        ltcore.check_python((3, 10, 14))
    assert str(ei.value.code) == MESSAGE == ltcore.VERSION_MESSAGE
    ltcore.check_python((3, 11, 0))
    ltcore.check_python((4, 0))


def test_import_on_fake_310_prints_the_message():
    code = _fake_version(f"sys.path.insert(0, {str(SHARED)!r})\nimport ltcore\nprint('imported')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1
    assert MESSAGE in r.stderr and "imported" not in r.stdout


def test_entry_gate_on_fake_310(tmp_path):
    script = tmp_path / "scripts" / "entry.py"
    script.parent.mkdir()
    script.write_text(ENTRY_GATE, encoding="utf-8", newline="\n")
    shutil.copytree(SHARED / "ltcore", tmp_path / "scripts" / "_shared" / "ltcore")
    runner = _fake_version(f"__file__ = {str(script)!r}\nexec(open(__file__).read())")
    r = subprocess.run([sys.executable, "-c", runner], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and MESSAGE in r.stderr
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                       env={**os.environ, "LT_SUITE_DATA": str(tmp_path / "data")}, encoding="utf-8")
    assert r.returncode == 0 and "past the gate" in r.stdout, r.stderr


def test_missing_modules():
    assert ltcore.missing_modules(["json", "lt_surely_absent_xyz"]) == ["lt_surely_absent_xyz"]
    assert ltcore.missing_modules([]) == []
    assert ltcore.missing_modules() == []        # source tree: no requirements.txt


def test_bootstrap_without_venv_returns_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SUITE_DATA", str(tmp_path / "data"))
    assert ltcore.bootstrap(["lt_surely_absent_xyz"]) == ["lt_surely_absent_xyz"]
    assert ltcore.bootstrap(["json"]) == []
    assert "lt_surely_absent_xyz" in ltcore.missing_hint(["lt_surely_absent_xyz"])


@pytest.mark.skipif(os.name == "nt", reason="exec of a shell script")
def test_bootstrap_reexecs_inside_data_venv(tmp_path):
    data = tmp_path / "data"
    py = data / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    py.write_text(f'#!/bin/sh\necho "$@ guard=$LT_SUITE_BOOTSTRAPPED" > "{marker}"\nexit 7\n', encoding="utf-8", newline="\n")
    py.chmod(py.stat().st_mode | stat.S_IXUSR)
    code = (f"import sys; sys.path.insert(0, {str(SHARED)!r})\nimport ltcore\n"
            "ltcore.bootstrap(['lt_surely_absent_xyz'], argv=['script.py', '--flag'])\n"
            "print('not re-executed')")
    env = {**os.environ, "LT_SUITE_DATA": str(data)}
    env.pop("LT_SUITE_BOOTSTRAPPED", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 7, r.stdout + r.stderr
    assert marker.read_text(encoding="utf-8").strip() == "script.py --flag guard=1"
    # the guard stops a loop: with LT_SUITE_BOOTSTRAPPED=1 nothing is re-executed
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**env, "LT_SUITE_BOOTSTRAPPED": "1"}, encoding="utf-8")
    assert r.returncode == 0 and "not re-executed" in r.stdout


def test_requirements_read_from_skill_layout(tmp_path):
    skill = tmp_path / "skill"
    shutil.copytree(SHARED / "ltcore", skill / "scripts" / "_shared" / "ltcore")
    (skill / "requirements.txt").write_text(
        "# generated\nlt-surely-absent-dist>=1.0\njson5 ; python_version >= '3.11'\n"
        "# Optional: imported only when installed.\n# pikepdf>=9\n", encoding="utf-8", newline="\n")
    code = (f"import sys; sys.path.insert(0, {str(skill / 'scripts' / '_shared')!r})\n"
            "import ltcore; print(ltcore._skill_requirements()); print(ltcore.missing_modules())")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "['lt-surely-absent-dist', 'json5']"
    assert "lt_surely_absent_dist" in lines[1]


def test_generated_requirement_guards_select_running_python(tmp_path):
    skill = tmp_path / "skill"
    shutil.copytree(SHARED / "ltcore", skill / "scripts" / "_shared" / "ltcore")
    (skill / "requirements.txt").write_text(
        "future-only==1 ; python_full_version >= '99' --hash=sha256:abc\n"
        "old-only==1 ; python_full_version < '3.11' --hash=sha256:def\n"
        "current-only==1 ; python_full_version >= '3.11' --hash=sha256:ghi\n", encoding="utf-8", newline="\n")
    code = (f"import sys; sys.path.insert(0, {str(skill / 'scripts' / '_shared')!r})\n"
            "import ltcore; print(ltcore._skill_requirements())")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "['current-only']"
