# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the fill skill's Python tests: `kit` (a Kit in the test's temp folder), `fk`
(the builders as a namespace) and `emit_mod` (emit_fill.py imported as a module). The
builders live in fillkit.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fillkit import (ORIGIN, REPO, SCRIPTS, Kit, answers, drafted, item, module,  # noqa: E402
                     text_pages)


@pytest.fixture
def kit(tmp_path) -> Kit:
    return Kit(tmp_path)


@pytest.fixture
def fk():
    """The builders, as a namespace."""
    return SimpleNamespace(item=item, module=module, drafted=drafted, answers=answers,
                           text_pages=text_pages, ORIGIN=ORIGIN, SCRIPTS=SCRIPTS, REPO=REPO)


@pytest.fixture
def emit_mod():
    """emit_fill.py imported as a module (shared/ is on sys.path through tests/conftest.py)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import emit_fill  # noqa: PLC0415
    return emit_fill


