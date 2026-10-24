# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared pytest setup for every test area.

- Puts shared/ on sys.path, so tests import ``ltcore`` the way skills do.
- ``@pytest.mark.requires_bin("node", "rsvg-convert")`` skips a test unless every
  named program is on PATH.
- Every test runs with the lt-suite environment variables removed and the XDG and
  Windows app-data folders (and HOME on macOS) pointed into a temp folder, so no
  test reads or writes the developer's real config, data or cache.
- Fixtures: ``repo_root``, ``shared_dir``, ``run_py`` (run a Python snippet or
  module with shared/ on PYTHONPATH and return the CompletedProcess).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHARED = REPO / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
# Bind the package before skill entry points prepend their generated copies.
# Each copy contains only its skill's closure; the integrated suite needs shared/.
import ltcore  # noqa: E402,F401

LT_ENV = ("LT_SUITE_CONFIG", "LT_SUITE_DATA", "LT_SUITE_CACHE", "LT_SUITE_NO_INSTALL",
          "LT_SUITE_BOOTSTRAPPED")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requires_bin(*names): skip unless every named program is on PATH")


def pytest_runtest_setup(item):
    for mark in item.iter_markers(name="requires_bin"):
        missing = [n for n in mark.args if shutil.which(n) is None]
        if missing:
            pytest.skip(f"needs {', '.join(missing)} on PATH")


@pytest.fixture(autouse=True)
def _isolated_lt_env(monkeypatch, tmp_path_factory):
    base = tmp_path_factory.mktemp("lt-env")
    for name in LT_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                      ("XDG_CACHE_HOME", "cache"), ("APPDATA", "appdata"),
                      ("LOCALAPPDATA", "localappdata")):
        monkeypatch.setenv(name, str(base / sub))
    if sys.platform == "darwin":
        # macOS folders hang off HOME (~/Library/...), so HOME moves too.
        monkeypatch.setenv("HOME", str(base / "home"))
    yield


@pytest.fixture
def repo_root() -> Path:
    return REPO


@pytest.fixture
def shared_dir() -> Path:
    return SHARED


@pytest.fixture
def run_py():
    """Run ``python args...`` with shared/ on PYTHONPATH; returns CompletedProcess."""
    def _run(*args: str, cwd: str | os.PathLike | None = None, env: dict | None = None,
             stdin: str | None = None, timeout: int = 120,
             shared_on_path: bool = True) -> subprocess.CompletedProcess:
        e = dict(os.environ)
        if env:
            e.update(env)
        if shared_on_path:
            e["PYTHONPATH"] = str(SHARED) + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
        else:
            e.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, *args], cwd=cwd or REPO, env=e, input=stdin,
                              capture_output=True, text=True, timeout=timeout)
    return _run


@pytest.fixture(scope="session")
def static_test_font(tmp_path_factory):
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    with TTFont(SHARED / "fonts/noto-sans/NotoSans-Variable.ttf") as font:
        font = instantiateVariableFont(font, {a.axisTag: a.defaultValue for a in font['fvar'].axes})
        path = tmp_path_factory.mktemp("font-template") / "template.ttf"
        font.save(path)
    return path


@pytest.fixture
def font_file(tmp_path, static_test_font):
    """Invented faces derived from the pinned test asset, independent of host fonts."""
    from fontTools.ttLib import TTFont
    counter = 0

    def make(family="Test Sans", *, weight=400, fs_type=0, licence="SIL Open Font License 1.1",
             variable=False, filename=None, copyright="Synthetic test font"):
        nonlocal counter
        counter += 1
        source = SHARED / "fonts/noto-sans/NotoSans-Variable.ttf" if variable else static_test_font
        with TTFont(source) as font:
            names = font['name']
            for nid in (1, 2, 4, 6, 16, 17, 21, 22, 25, 0, 13):
                names.removeNames(nameID=nid)
            for nid, value in ((1, family), (2, "Regular"), (4, family + " Regular"),
                               (6, family.replace(" ", "") + "-Regular"), (0, copyright), (13, licence)):
                names.setName(value, nid, 3, 1, 0x409)
            font['OS/2'].fsType = fs_type
            font['OS/2'].usWeightClass = weight
            path = tmp_path / (filename or f"face-{counter}.ttf")
            path.parent.mkdir(parents=True, exist_ok=True)
            font.save(path)
        return path
    return make
