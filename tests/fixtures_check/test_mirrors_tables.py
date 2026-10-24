# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Facts about the fixtures that other test areas rest on: the mirror pairs of c1-r105, the
named rows of the two grids in c1-r106 and the label pools behind the placements."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import STEMS, read_json  # noqa: E402

EXPECTED = read_json("modules/expected-mirrors.json")["modules"]


def index(mod):
    return {i["item_id"]: (p["page_index"], i) for p in mod["pages"] for i in p["items"]}


def key(i):
    return json.dumps([i["type"], i["prompt_text"], i.get("constraints")], sort_keys=True)


def test_expected_table_names_every_module():
    assert set(EXPECTED) == set(STEMS)
    assert len(EXPECTED["c1-r105"]["pairs"]) == 3 and len(EXPECTED["c1-r105"]["near_duplicates"]) == 1
    assert all(not v["pairs"] and not v["near_duplicates"]
               for k, v in EXPECTED.items() if k != "c1-r105")


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_mirror_pairs_carry_the_facts_the_detector_rests_on(modules, mid):
    items = index(modules[mid])
    flagged = {iid for iid, (_p, i) in items.items() if i.get("mirror_of")}
    e = EXPECTED[mid]
    assert flagged == {x["mirror"] for x in e["pairs"] + e["near_duplicates"]}
    for x in e["pairs"]:
        sp, s = items[x["source"]]
        mp, m = items[x["mirror"]]
        assert key(s) == key(m)
        assert sp < mp, "the source is on a strictly earlier page"
        assert s["answered_evidence"] == m["answered_evidence"], "identical content"
        assert isinstance(s["read_only"], bool) and m["read_only"] is None
        assert m["mirror_of"]["item_id"] == x["source"] and m["mirror_of"]["page_index"] == sp
        assert m["mirror_of"]["basis"] == x["basis"]
    for x in e["near_duplicates"]:
        sp, s = items[x["source"]]
        mp, m = items[x["mirror"]]
        assert key(s) == key(m) and sp < mp
        assert s["answered_evidence"] != m["answered_evidence"], "the content differs"
        assert isinstance(s["read_only"], bool) and isinstance(m["read_only"], bool)
        assert m["mirror_of"]["basis"] == x["basis"] == ["duplicate-prompt-and-constraints"]


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_no_other_later_page_duplicate_hides(modules, mid):
    known = {x["mirror"] for x in EXPECTED[mid]["pairs"] + EXPECTED[mid]["near_duplicates"]}
    first = {}
    extra = []
    for p in modules[mid]["pages"]:
        for i in p["items"]:
            if i["type"] not in ("table-completion", "text-response") or not i["prompt_text"]:
                continue
            k = key(i)
            if k not in first:
                first[k] = p["page_index"]
            elif first[k] < p["page_index"] and i["item_id"] not in known:
                extra.append(i["item_id"])
    assert extra == []


def test_mirror_items_are_not_lock_evidence(modules):
    """A mirror is excluded from lock evidence, so c1-r105 (not locked) has none to read."""
    assert modules["c1-r105"]["lesson"]["run_locked"] is False


# ---- c1-r106: two grids of 17 and 6 rows -------------------------------------------------

def test_the_two_grids_have_17_and_6_rows_with_named_body_rows(modules):
    items = index(modules["c1-r106"])
    big, small = items["p2-11"][1], items["p2-12"][1]
    assert (big["constraints"]["rows"], small["constraints"]["rows"]) == (17, 6)
    assert len(big["constraints"]["row_headers"]) == 16
    assert len(small["constraints"]["row_headers"]) == 5
    assert big["constraints"]["row_headers"][0] == ["Minute 0"]
    assert small["constraints"]["row_headers"][0] == ["Short term"]
    assert big["constraints"]["column_headers"] == ["Interval", "Goal", "Option"]
    assert big["answered"] is True and small["answered"] is False


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_drafted_cells_fall_inside_their_grid_and_the_first_row_is_the_first_body_row(modules, mid):
    ans = read_json(f"answers/answers-{STEMS[mid]}.json")
    items = index(modules[mid])
    for a in ans["items"]:
        if not a.get("table_cells"):
            continue
        cons = items[a["item_id"]][1]["constraints"]
        rows, cols = {c["row"] for c in a["table_cells"]}, {c["col"] for c in a["table_cells"]}
        assert max(rows) < cons["rows"] and max(cols) < cons["cols"]
        assert len({(c["row"], c["col"]) for c in a["table_cells"]}) == len(a["table_cells"])
        if "row_headers" in cons:
            # The header row sits on top, so absolute row r is body row r - 1.
            assert min(rows) == 1
            names = [cons["row_headers"][r - 1][0] for r in sorted(rows)]
            assert names[0] == cons["row_headers"][0][0] and all(names)


# ---- label pools behind the placements ---------------------------------------------------

def test_a_pool_larger_than_its_zones_is_recorded_in_the_note(modules):
    it = index(modules["c1-r103"])["p3-21"][1]
    assert it["constraints"]["options_count"] == 5
    assert "locations=4" in it["constraints"]["note"]
    placed = {p["label"] for p in next(a for a in read_json("answers/answers-c1-r103.json")["items"]
                                       if a["item_id"] == "p3-21")["label_placements"]}
    assert len(placed) == 4 and placed < {o["text"] for o in it["options"]}


def test_the_label_pool_is_sorted_not_in_source_order(modules):
    it = index(modules["c1-r101"])["p4-31"][1]
    assert [o["text"] for o in it["options"]] == ["Orm lobe", "Pell duct", "Quillen cap", "Zeta fold"]
    assert [o["option_id"] for o in it["options"]] == ["o1", "o2", "o3", "o4"]
