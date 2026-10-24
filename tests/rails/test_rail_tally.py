# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the rail tally inside pytest so failures propagate without a shell pipeline."""
from __future__ import annotations

from .test_rails import CASES, base_doc, run_validator


def test_every_negative_rail_fires_in_process(tmp_path):
    assert CASES, "the tally must exercise at least one negative rail"
    missed = []
    for index, (label, base, mutate, code, message, options) in enumerate(CASES):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        actual_code, output = run_validator(case_dir, base, mutate(base_doc(base)), options)
        if actual_code != code or message.lower() not in output.lower():
            missed.append(f"{label}: expected exit {code} containing {message!r}; "
                          f"got exit {actual_code}: {output}")
    assert not missed, "\n".join(missed)
