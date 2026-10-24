# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Diagram fixtures; reusable helpers live in the uniquely named test module."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_test_dir = str(Path(__file__).resolve().parent)
if _test_dir not in sys.path:
    sys.path.insert(1, _test_dir)
from diagram_testkit import clean_env, copy_case, missing_bins, run_engine


@pytest.fixture(scope="session")
def engine_env(tmp_path_factory) -> dict:
    env = clean_env(tmp_path_factory.mktemp("diagram-env"))
    env["TMPDIR"] = str(tmp_path_factory.mktemp("diagram-tmp"))
    return env


@pytest.fixture(scope="session")
def toy_build(tmp_path_factory, engine_env) -> Path:
    """The worked example, built once: <tmp>/toy-case with every output."""
    miss = missing_bins()
    if miss:
        pytest.skip(f"needs {', '.join(miss)} on PATH")
    root = tmp_path_factory.mktemp("toy")
    case = copy_case(root / "toy-case")
    r = run_engine("build.py", case, "--trust-case", cwd=root, env=engine_env)
    if r.returncode:
        pytest.fail(f"the worked example does not build (exit {r.returncode}):\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return case


@pytest.fixture
def toy_copy(toy_build, tmp_path) -> Path:
    """A copy of the built example (outputs included) that a test may change."""
    dst = tmp_path / "toy-case"
    shutil.copytree(toy_build, dst)
    return dst
