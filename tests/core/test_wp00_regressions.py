# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bootstrap selection and concurrent config publishing regressions."""
from __future__ import annotations

import os
import sys
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import ltcore
from ltcore import config


@pytest.mark.parametrize("flag_style", ["separate", "equals"])
@pytest.mark.parametrize("explicit_argv", [False, True])
def test_bootstrap_config_flag_selects_venv(tmp_path, monkeypatch, flag_style, explicit_argv):
    """The config flag must select the venv before dependency re-execution."""
    selected = tmp_path / "selected.toml"
    selected.write_text('[paths]\ndata_dir = "selected-data"\n', encoding="utf-8")
    other = tmp_path / "other.toml"
    other.write_text('[paths]\ndata_dir = "other-data"\n', encoding="utf-8")
    monkeypatch.setenv("LT_SUITE_CONFIG", str(other))
    py = Path(ltcore.venv_python(str(tmp_path / "selected-data")))
    py.parent.mkdir(parents=True)
    py.touch()
    argv = ["entry.py"] + (["--config", str(selected)] if flag_style == "separate"
                            else [f"--config={selected}"])
    monkeypatch.setattr(sys, "argv", ["other.py"] if explicit_argv else argv)
    calls = []

    def reexec(*args, **kwargs):
        calls.append((args, kwargs))
        raise SystemExit(17)

    monkeypatch.setattr(os, "execve", reexec)
    monkeypatch.setattr(ltcore.subprocess, "call", reexec)
    with pytest.raises(SystemExit) as exc:
        ltcore.bootstrap(["lt_missing_wp00_xyz"], argv=argv if explicit_argv else None)
    assert exc.value.code == 17
    args, kwargs = calls[0]
    if os.name == "nt":
        assert args[0] == [str(py), *argv]
        assert kwargs["env"]["LT_SUITE_BOOTSTRAPPED"] == "1"
    else:
        assert args[:2] == (str(py), [str(py), *argv])
        assert args[2]["LT_SUITE_BOOTSTRAPPED"] == "1"


@pytest.mark.parametrize("force", [False, True])
def test_config_creation_race_preserves_existing_file(tmp_path, monkeypatch, force):
    """A target appearing after discovery must survive or become the backup."""
    target = tmp_path / "config.toml"
    original = 'schema_version = 1\n[institution]\nname = "Concurrent"\n'
    mkstemp = config.tempfile.mkstemp

    def competing_create(*args, **kwargs):
        target.write_text(original, encoding="utf-8")
        return mkstemp(*args, **kwargs)

    monkeypatch.setattr(config.tempfile, "mkstemp", competing_create)
    if force:
        config.write({"schema_version": 1}, target, force=True)
        assert target.with_suffix(".toml.bak").read_text(encoding="utf-8") == original
        assert tomllib.loads(target.read_text(encoding="utf-8")) == {"schema_version": 1}
    else:
        with pytest.raises(config.ConfigError, match="--force"):
            config.write({"schema_version": 1}, target)
        assert target.read_text(encoding="utf-8") == original
        assert not target.with_suffix(".toml.bak").exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_config_forced_replacements_serialize_backup(tmp_path, monkeypatch):
    """Two forced writers must back up the immediately preceding published config."""
    target = tmp_path / "config.toml"
    config.write({"institution": {"name": "Original"}}, target)
    first_publishing = threading.Event()
    release_first = threading.Event()
    second_rendered = threading.Event()
    second_copy = threading.Event()
    replace, copy2, render = config.os.replace, config.shutil.copy2, config.render

    def paused_replace(src, dst):
        if threading.current_thread().name == "first":
            first_publishing.set()
            assert release_first.wait(5)
        return replace(src, dst)

    def observe_copy(*args, **kwargs):
        if threading.current_thread().name == "second":
            second_copy.set()
        return copy2(*args, **kwargs)

    def observe_render(doc):
        text = render(doc)
        if threading.current_thread().name == "second":
            second_rendered.set()
        return text

    def write_named(name):
        threading.current_thread().name = name
        return config.write({"institution": {"name": name}}, target, force=True)

    monkeypatch.setattr(config.os, "replace", paused_replace)
    monkeypatch.setattr(config.shutil, "copy2", observe_copy)
    monkeypatch.setattr(config, "render", observe_render)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_named, "first")
        try:
            assert first_publishing.wait(5)
            second = pool.submit(write_named, "second")
            assert second_rendered.wait(5)
            assert not second_copy.wait(0.5), "second writer copied before first published"
        finally:
            release_first.set()
        assert first.result(timeout=5) == second.result(timeout=5) == target
    assert tomllib.loads(target.read_text())["institution"]["name"] == "second"
    assert tomllib.loads(target.with_suffix(".toml.bak").read_text())["institution"]["name"] == "first"


def test_config_failed_replace_cleans_temp_and_lock(tmp_path, monkeypatch):
    target = config.write({"institution": {"name": "Original"}}, tmp_path / "config.toml")
    before = target.read_bytes()

    def fail_replace(*args):
        raise OSError("replacement failed")

    with monkeypatch.context() as patch:
        patch.setattr(config.os, "replace", fail_replace)
        with pytest.raises(OSError, match="replacement failed"):
            config.write({"institution": {"name": "Updated"}}, target, force=True)
    assert target.read_bytes() == target.with_suffix(".toml.bak").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml", "config.toml.bak"]
    config.write({"institution": {"name": "Updated"}}, target, force=True)
    assert tomllib.loads(target.read_text())["institution"]["name"] == "Updated"


@pytest.mark.parametrize("location", ["git-directory", "worktree-file", "data-symlink", "venv-symlink"])
def test_runtime_environment_stays_outside_repositories(tmp_path, monkeypatch, location):
    """Repository-local environments must fall back to the platform data directory."""
    from ltcore import paths
    repo = tmp_path / "repository"
    repo.mkdir()
    marker = repo / ".git"
    if location == "worktree-file":
        marker.write_text("gitdir: elsewhere\n")
    else:
        marker.mkdir()
    external = tmp_path / "external"
    data = repo / "data"
    if location == "data-symlink":
        data = tmp_path / "linked-data"
        data.symlink_to(repo, target_is_directory=True)
    elif location == "venv-symlink":
        data = tmp_path / "data"
        data.mkdir()
        (data / "venv").symlink_to(repo, target_is_directory=True)
    monkeypatch.setattr(paths, "data_dir", lambda: external)
    suffix = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    assert Path(ltcore.venv_python(str(data))) == external / "venv" / suffix


def test_runtime_environment_rejects_unsafe_platform_fallback(tmp_path, monkeypatch):
    """A second repository must never become a bootstrap fallback."""
    from ltcore import paths
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "platform")
    with pytest.raises(ValueError, match="outside a git work tree"):
        ltcore.venv_python(str(tmp_path / "data"))


def test_runtime_environment_preserves_safe_lexical_symlink(tmp_path):
    """Safe external data symlinks retain the path selected by the user."""
    data = tmp_path / "data"
    data.mkdir()
    link = tmp_path / "link"
    link.symlink_to(data, target_is_directory=True)
    suffix = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    assert Path(ltcore.venv_python(str(link))) == link / "venv" / suffix


def test_bootstrap_recognizes_locked_rpds_distribution(monkeypatch):
    """The jsonschema transitive distribution imports as rpds, without a py suffix."""
    pytest.importorskip("rpds")
    monkeypatch.setattr(ltcore, "_skill_requirements", lambda: ["rpds-py"])
    assert ltcore.missing_modules() == []
