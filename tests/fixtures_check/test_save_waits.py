# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Save waits in the fill-report fixtures: the report, the run folder and the item text agree."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FILL_REPORT_IDS, STEMS, read_json  # noqa: E402

WEAK = ("fallback", "unconfirmed", "error")
STRONG = ("model", "net")
WITH_WAITS = [m for m in FILL_REPORT_IDS if m != "c1-r101"]


def report(mid):
    return read_json(f"fill-reports/fill-report-{STEMS[mid]}.json")


def filled_by_page(mid):
    """{page_index: [item_id, ...]} for every item the report says was filled."""
    mod = read_json(f"modules/module-{STEMS[mid]}.json")
    page_of = {i["item_id"]: p["page_index"] for p in mod["pages"] for i in p["items"]}
    out = {}
    for it in report(mid)["items"]:
        if it["result"] == "filled":
            out.setdefault(page_of[it["item_id"]], []).append(it["item_id"])
    return out


def test_the_locked_run_waited_for_nothing():
    assert "save_waits" not in report("c1-r101")


@pytest.mark.parametrize("mid", WITH_WAITS)
def test_report_rows_equal_the_run_folder_file(mid):
    assert report(mid)["save_waits"] == read_json(f"runs/{mid}/save-waits.json")["save_waits"]


@pytest.mark.parametrize("mid", WITH_WAITS)
def test_every_row_is_a_complete_record(mid):
    for n, row in enumerate(report(mid)["save_waits"], 1):
        assert row["receipt"] == f"fill-result-{n}.json"
        assert re.fullmatch(r"[0-9a-f]{64}", row["receipt_sha256"])
        assert row["save_signal"] in WEAK + STRONG
        assert row["waited_ms"] > 0
        assert (row["save_signal"] == "fallback") == (row["calibrated"] is False), row
        assert row["ok"] is (row["fatal"] is None), row


@pytest.mark.parametrize("mid", WITH_WAITS)
def test_rows_cover_the_filled_pages_and_count_their_keys(mid):
    filled = filled_by_page(mid)
    rows = report(mid)["save_waits"]
    assert {p for r in rows for p in r["pages"]} == set(filled) == set(report(mid)["pages_filled"])
    for row in rows:
        assert row["keys_total"] == sum(len(filled[p]) for p in row["pages"]), row
        if row["save_signal"] in STRONG:
            assert row["keys_saved"] == row["keys_total"] and row["keys_pending"] == 0, row


@pytest.mark.parametrize("mid", WITH_WAITS)
def test_a_weak_wait_leaves_its_sentence_on_the_filled_items(mid):
    """The first weak signal on a page wins, as in the report builder: a later strong wait
    does not clear it. Items on strong-only pages carry no wait sentence."""
    weak_text = {"fallback": "fallback wait", "unconfirmed": "ended unconfirmed",
                 "error": "reported a server error"}
    weak = {}
    for row in report(mid)["save_waits"]:
        if row["save_signal"] in WEAK:
            for p in row["pages"]:
                weak.setdefault(p, row["save_signal"])
    by_id = {i["item_id"]: i for i in report(mid)["items"]}
    for page, ids in filled_by_page(mid).items():
        for iid in ids:
            reason = by_id[iid].get("stopped_reason", "")
            if page in weak:
                assert weak_text[weak[page]] in reason, (iid, reason)
                assert "Verified on a fresh load" in reason, (iid, reason)
            else:
                assert not any(t in reason for t in weak_text.values()), (iid, reason)


def test_every_save_signal_appears_in_some_fixture():
    seen = {r["save_signal"] for m in WITH_WAITS for r in report(m)["save_waits"]}
    assert seen == set(WEAK + STRONG)


def test_a_failed_save_is_followed_by_a_row_that_acknowledges_the_retry():
    rows = report("c1-r106")["save_waits"]
    assert [r["save_signal"] for r in rows] == ["error", "model"]
    assert rows[0]["ok"] is False and rows[0]["save_errors"] == 1
    assert rows[1]["ok"] is True and rows[0]["pages"] == rows[1]["pages"]


def test_one_call_may_cover_two_pages():
    rows = report("c1-r102")["save_waits"]
    assert [r["pages"] for r in rows] == [[2], [3, 4]]
    assert [r["calibrated"] for r in rows] == [False, True]
