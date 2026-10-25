# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protect installed files and isolate installer credentials and build outputs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[2] / "tools/install_tests/_common.py"
spec = importlib.util.spec_from_file_location("lt_install_common_test", HELPER)
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)


def test_existing_work_folder_is_preserved(tmp_path):
    """A caller-supplied directory may contain work from another process."""
    repo = tmp_path / "source"
    repo.mkdir()
    work = tmp_path / "work"
    sentinel = work / "codex" / "keep.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("existing work", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit, match="must not exist"):
        c.Workspace(repo, str(work), False, "codex")
    assert sentinel.read_text(encoding="utf-8") == "existing work"


def test_work_inside_source_is_rejected_before_creation(tmp_path):
    """A snapshot must never recursively copy its own destination."""
    with pytest.raises(SystemExit, match="outside the repo"):
        c.Workspace(tmp_path, str(tmp_path / "nested"), False, "codex")
    assert not (tmp_path / "nested").exists()


def test_unknown_credential_variable_is_not_inherited(tmp_path, monkeypatch):
    """An allowlist also protects providers unknown to the harness."""
    monkeypatch.setenv("CUSTOM_SERVICE_TOKEN", "synthetic-value")
    monkeypatch.setenv("PYTHONPATH", "/synthetic/untrusted")
    env = c.clean_env(tmp_path)
    assert "CUSTOM_SERVICE_TOKEN" not in env
    assert "PYTHONPATH" not in env
    assert env["HOME"] == str(tmp_path)


def test_entry_point_bytes_are_checked(tmp_path):
    """A matching filename set cannot hide a modified installed entry point."""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "main.py").write_text("print(1)\n", encoding="utf-8", newline="\n")
    (target / "main.py").write_text("print(2)\n", encoding="utf-8", newline="\n")
    assert c.file_set(source) == c.file_set(target)
    assert not c.same_files(source, target)


@pytest.mark.requires_bin("git")
def test_snapshot_omits_gitignored_render_outputs(tmp_path):
    """An active diagram build must not become an unapproved release input."""
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / ".gitignore").write_text("final-*.pdf\n", encoding="utf-8", newline="\n")
    (source / "final-example.pdf").write_bytes(b"synthetic render")
    (source / "source.py").write_text("print(1)\n", encoding="utf-8", newline="\n")
    with c.Workspace(source, None, False, "test") as ws:
        assert (ws.repo / "source.py").is_file()
        assert not (ws.repo / "final-example.pdf").exists()
