# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and cache paths remain outside Git's release selection."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', ['.venv/probe', 'dist/probe', 'build/probe', 'node_modules/probe',
                                  '.pytest_cache/probe', '.ruff_cache/probe', '.mypy_cache/probe',
                                  '.cache/probe', 'htmlcov/probe'])
def test_build_outputs_and_caches_are_ignored(name):
    import subprocess
    result = subprocess.run(['git', 'check-ignore', '--quiet', name], cwd=ROOT)
    assert result.returncode == 0, name

