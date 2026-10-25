# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""render_table_cells names each row after its own header, not the next row's.

A drafted cell's ``row`` is the absolute grid row, while the capture's ``row_headers``
holds body rows only, because the header row is not a body row. Indexing one by the other
shifts every row name down by one and leaves the last row with a number. The synthetic
grids pin the arithmetic; the fixture capture c1-r106 pins it against two real-shaped
tables (17 and 6 rows) using only their authored headers and the drafted cell positions.
"""
from __future__ import annotations

import json
import re

import pytest


def labels(E, item: dict, cells: list[tuple[int, int]]) -> list[str]:
    ans = {"table_cells": [{"row": r, "col": c, "text": "x"} for r, c in cells]}
    out = E.render_table_cells(ans, item)
    return [re.match(r"^\*\*(.+?) · ", line)[1] for line in out[1:]]


def test_header_row_each_row_takes_its_own_name(export_module):
    item = {"constraints": {"rows": 4, "cols": 3, "column_headers": ["", "GOALS", "OPTIONS"],
                            "row_headers": [["Short"], ["Medium"], ["Long term"]]}}
    assert labels(export_module, item, [(1, 1), (2, 1), (3, 1)]) == ["Short", "Medium", "Long term"]


def test_no_header_row_names_line_up_unshifted(export_module):
    item = {"constraints": {"rows": 3, "cols": 2, "row_headers": [["a"], ["b"], ["c"]]}}
    assert labels(export_module, item, [(0, 1), (1, 1), (2, 1)]) == ["a", "b", "c"]


def test_no_row_headers_fall_back_to_one_based_rows(export_module):
    item = {"constraints": {"rows": 3, "cols": 2, "column_headers": ["x", "y"]}}
    assert labels(export_module, item, [(1, 1), (2, 1)]) == ["row 2", "row 3"]


def test_header_cell_with_a_line_break_stays_on_one_bold_line(export_module):
    item = {"constraints": {"rows": 2, "cols": 2,
                            "column_headers": ["x", "Kind of Signal\n(fast or slow)"]}}
    out = export_module.render_table_cells(
        {"table_cells": [{"row": 1, "col": 1, "text": "None"}]}, item)
    assert "\n" not in out[1]
    assert out[1].startswith("**row 2 · Kind of Signal (fast or slow)**")


def test_no_cells_render_nothing(export_module):
    assert export_module.render_table_cells({}, {"constraints": {}}) == []
    assert export_module.render_table_cells({"table_cells": []}, {}) == []


@pytest.mark.parametrize("refs,expected", [
    ({}, "value"),
    ({"provenance_refs": None}, "value"),
    ({"provenance_refs": []}, "value"),
    ({"provenance_refs": ["p1"]}, "value (Source one)"),
    ({"provenance_refs": ["missing"]}, "value (unresolved source missing)"),
])
def test_table_cells_cite_only_explicit_refs(export_module, refs, expected):
    cell = {"row": 0, "col": 0, "text": "value", **refs}
    rendered = export_module.render_table_cells(
        {"table_cells": [cell]}, {}, {"p1": "(Source one)", "p2": "(Source two)"})
    assert rendered[1] == f"**row 1 · column 0** — {expected}"


@pytest.mark.parametrize("item_id, rows", [("p2-11", 17), ("p2-12", 6)])
def test_fixture_tables_name_every_body_row(export_module, fixtures, item_id, rows):
    module = json.loads((fixtures / "modules" / "module-c1-r106.json").read_text(encoding="utf-8"))
    answers = json.loads((fixtures / "answers" / "answers-c1-r106.json").read_text(encoding="utf-8"))
    item = next(it for it in export_module.module_items(module) if it["item_id"] == item_id)
    ans = export_module.answer_index(answers)[item_id]
    constraints = item["constraints"]
    assert constraints["rows"] == rows
    body = [r[0] if r else "" for r in constraints["row_headers"]]
    got = list(dict.fromkeys(
        labels(export_module, item, [(c["row"], c["col"]) for c in ans["table_cells"]])))
    assert got[:1] == body[:1], "the first drafted row carries the first body row's name"
    assert got == body, "each body row keeps its own name, in order"
    assert len(got) == rows - 1
    assert not any(re.fullmatch(r"row \d+", g) for g in got)
