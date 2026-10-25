# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fill evals and trigger queries: 10 evals on synthetic fixtures that exist, each with a
prompt and expectations, and trigger queries with both answers present."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVALS = REPO / "evals" / "lt-module-fill"


def test_ten_evals_on_existing_synthetic_fixtures():
    doc = json.loads((EVALS / "evals.json").read_text(encoding="utf-8"))
    assert doc["skill_name"] == "lt-module-fill"
    evs = doc["evals"]
    assert [e["id"] for e in evs] == list(range(1, 9)) + [63, 84]
    for e in evs:
        assert e["prompt"].strip() and len(e["expectations"]) >= 5
        for f in e["files"]:
            assert f.startswith("tests/fixtures/"), f
            assert (REPO / f).is_file(), f


def test_trigger_queries_cover_both_answers():
    rows = json.loads((EVALS / "triggers.json").read_text(encoding="utf-8"))["cases"]
    assert {r["should_trigger"] for r in rows} == {True, False}
    assert len({r["query"] for r in rows}) == len(rows)
