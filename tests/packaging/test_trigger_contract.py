# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every shipped trigger dataset loads through the documented offline scorer."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('path', sorted((ROOT / 'evals').glob('*/triggers.json')), ids=lambda p: p.parent.name)
def test_trigger_dataset_uses_the_scorer_contract(path):
    spec = importlib.util.spec_from_file_location('release_eval_support', ROOT / 'dev/claude-code/eval_support.py')
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    rows = support.read_cases(path)
    assert rows
    assert all(isinstance(row['query'], str) and type(row['should_trigger']) is bool for row in rows.values())

