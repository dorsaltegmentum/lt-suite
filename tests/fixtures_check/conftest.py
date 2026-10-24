# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for tests/fixtures_check: the generator module, the validator and boundary
modules (skipped when the code is missing) and the loaded fixture modules.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FIX, REPO, STEMS, apply_ops, module_path  # noqa: E402

@pytest.fixture(scope="session")
def fix_dir() -> Path:
    return FIX


@pytest.fixture(scope="session")
def modules() -> dict:
    return {mid: json.loads(module_path(mid).read_text(encoding="utf-8")) for mid in STEMS}


@pytest.fixture(scope="session")
def gen():
    """tools/gen_fixtures.py as a module."""
    spec = importlib.util.spec_from_file_location("gen_fixtures_under_test",
                                                  REPO / "tools" / "gen_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def validate():
    return pytest.importorskip("ltcore.validate", reason="not run: ltcore.validate missing")


@pytest.fixture(scope="session")
def boundaries():
    return pytest.importorskip("ltcore.boundaries", reason="not run: ltcore.boundaries missing")


@pytest.fixture(scope="session")
def ops():
    return apply_ops
