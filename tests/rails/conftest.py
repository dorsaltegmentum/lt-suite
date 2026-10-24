# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rails test setup: a tally of rail cases and its summary line.

Every rail case in test_rails.py records whether its rail fired. The terminal summary
prints ``<N> rails fired, <M> missed, <T> tested`` even under ``-q`` for diagnostics.
test_rail_tally.py asserts every case in-process for CI and package acceptance.
"""
from __future__ import annotations

import pytest


def pytest_configure(config):
    config._lt_rail_tally = {"fired": 0, "missed": 0, "tested": 0}


@pytest.fixture
def rail_tally(request):
    return request.config._lt_rail_tally


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    t = getattr(config, "_lt_rail_tally", None)
    if t and t["tested"]:
        terminalreporter.write_line(
            f"{t['fired']} rails fired, {t['missed']} missed, {t['tested']} tested")
