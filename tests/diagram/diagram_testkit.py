# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named helpers for the diagram engine tests.

- Puts the engine folder (skills/mechanistic-diagram/engine) and this folder on sys.path, after the
  repository's shared/ (tests/conftest.py), so tests import engine modules as the engine does.
- ``toy_build`` (session): the worked example (examples/toy-case) copied into a temp folder and built
  once with build.py in a clean environment (no user config, a temp data and cache folder). Tests that
  change outputs copy it first with ``toy_copy``.
- ``engine_env``: the environment the build ran with, for tests that run engine scripts themselves.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SKILL = REPO / "skills" / "mechanistic-diagram"
ENGINE = SKILL / "engine"
TOY = SKILL / "examples" / "toy-case"
BUILD_BINS = ("rsvg-convert", "pdfinfo", "pdftoppm", "xmllint")
OUTPUTS = ("final-*", "geometry*.json", "*.drawio", "*.html", "deliver", "checks", "consistency-report.md",
           "verify-result.json", "verify.js", "__pycache__")

for p in (str(HERE), str(ENGINE)):
    if p not in sys.path:
        sys.path.insert(1, p)


def clean_env(base: Path) -> dict:
    """os.environ without lt-suite variables, with XDG folders under base (no user config, data or cache)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LT_SUITE_") and k != "SOURCE_DATE_EPOCH"}
    for name, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                      ("APPDATA", "appdata"), ("LOCALAPPDATA", "localappdata")):
        env[name] = str(base / sub)
    if sys.platform == "darwin":
        env["HOME"] = str(base / "home")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def copy_case(dst: Path, src: Path = TOY) -> Path:
    """The example's inputs (not its build outputs) copied to dst; source mtimes kept for SOURCE_DATE_EPOCH."""
    outputs = shutil.ignore_patterns(*OUTPUTS)

    def ignore(folder, names):
        skip = set(outputs(folder, names))
        if Path(folder).name == "assets":          # per-palette copies the build makes beside the source
            skip |= {n for n in names if "-" in n}
        return skip
    shutil.copytree(src, dst, ignore=ignore)
    return dst


def run_engine(script: str, *args, cwd: Path, env: dict, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ENGINE / script), *map(str, args)], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout, encoding="utf-8")


def missing_bins() -> list[str]:
    return [b for b in BUILD_BINS if shutil.which(b) is None]


