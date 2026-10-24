# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The JavaScript tests run on fixtures/toy-case-page.html, the worked example's page cut down by
make_page_fixture.py. A fresh build of the example must cut down to the same text, so the JavaScript
tests always see the page the engine makes today. After a deliberate change, rebuild the example and run:
    python3 tests/diagram/make_page_fixture.py skills/mechanistic-diagram/examples/toy-case/toy-case.html
"""
import make_page_fixture as M


def test_fixture_matches_a_fresh_build(toy_build):
    page = toy_build / "toy-case.html"
    fresh = M.strip(page.read_text(encoding="utf-8"))
    fixture = M.DEFAULT_OUT.read_text(encoding="utf-8")
    if fresh != fixture:
        import difflib
        diff = list(difflib.unified_diff(fixture.splitlines(), fresh.splitlines(), "fixture", "fresh", n=0,
                                         lineterm=""))
        raise AssertionError("the page fixture is stale:\n" + "\n".join(line[:200] for line in diff[:30]))


def test_fixture_has_no_payloads_and_one_marker():
    text = M.DEFAULT_OUT.read_text(encoding="utf-8")
    assert text.count(M.MARKER) == 1
    assert "@font-face" not in text
    assert not M.PAYLOAD.search(text), "every base64 payload is emptied"
