# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""media_plan.py: the image pass may visit content pages before the first live Checkpoint and
nothing else. The plan it writes is checked with ltjs.py check-url, the same gate the SKILL
runs before every navigation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills" / "lt-module-capture" / "scripts" / "media_plan.py"
SHARED = REPO / "shared"
LTJS = SHARED / "ltcore" / "ltjs.py"
BASE = "https://lt.example.invalid/i/inst0001/student/courses/1/runs/101/run"


def load():
    spec = importlib.util.spec_from_file_location("media_plan_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page(idx, no, *, kind="content", media=False):
    p = {"page_index": idx, "page_no": no, "title": f"Page {idx}", "is_report_page": kind == "report",
         "is_checkpoint": kind == "checkpoint", "items": [], "content": [],
         "url": f"{BASE}/page/{no}" if no else None}
    if kind == "checkpoint":
        p["checkpoint"] = {"index": 9, "committed": False}
    if media:
        p["content"].append({"kind": "image", "media": [{"key": "70"}]})
    return p


def module(pages, route_source="datakey+location"):
    """A capture; the current capture.js marks its measured routes with route_source."""
    return {"module_id": "c1-r101", "instance": "inst0001", "course_id": 1, "run_id": 101,
            "source_url": f"{BASE}/page/1", "route_source": route_source, "pages": pages,
            "commit_boundaries": {"checkpoint_pages": [p["page_index"] for p in pages if p["is_checkpoint"]]}}


def test_plan_stops_before_a_live_checkpoint_and_lists_media_pages():
    mp = load()
    m = module([page(1, 1, media=True), page(2, 2), page(3, None, kind="checkpoint"), page(4, 3, media=True),
                page(5, None, kind="report")])
    plan = mp.build_plan(m)
    assert plan["stop_before_page"] == 3
    assert [p["page_index"] for p in plan["pages"]] == [1, 2]
    assert plan["media_pages"] == [1]
    assert {"page_index": 5, "reason": "report page"} in plan["never_navigated"]
    assert plan["origin"] == "https://lt.example.invalid"


def test_no_checkpoint_means_every_content_page_is_visitable():
    plan = load().build_plan(module([page(1, 1), page(2, 2, media=True), page(3, None, kind="report")]))
    assert plan["stop_before_page"] is None
    assert [p["page_index"] for p in plan["pages"]] == [1, 2]
    assert plan["media_pages"] == [2]


@pytest.mark.skipif(not LTJS.is_file(), reason="not run: WP04 ltjs.py missing")
def test_check_url_follows_the_written_plan(tmp_path):
    m = module([page(1, 1, media=True), page(2, 2), page(3, None, kind="checkpoint"), page(4, 3),
                page(5, None, kind="report")])
    src = tmp_path / "module-c1-r101.json"
    src.write_text(json.dumps(m), encoding="utf-8", newline="\n")
    env = dict(os.environ, PYTHONPATH=str(SHARED), LT_SUITE_DATA=str(tmp_path / "data"))
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text('schema_version = 1\n[lt]\norigin = "https://lt.example.invalid"\n', encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, str(SCRIPT), str(src), "--config", str(cfg)],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    plan = tmp_path / "data" / "runs" / "c1-r101" / "media-plan.json"
    assert plan.is_file()
    assert f"visit page_index 1: {BASE}/page/1" in r.stdout

    def check(url):
        return subprocess.run([sys.executable, str(LTJS), "check-url", "--config", str(cfg), "--plan", str(plan), url],
                              capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, encoding="utf-8").returncode

    assert check(f"{BASE}/page/1") == 0
    assert check(f"{BASE}/page/2") == 0
    assert check(f"{BASE}/page/3") == 1, "page_no 3 is past the live checkpoint"
    assert check(f"{BASE}/complete") == 1
    assert check(f"{BASE}/checkpoint/1") == 1
    assert check("https://other.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/1") == 1


def background_module(numbered=20):
    """Lt's shape of a run that opens with a Background page holding a figure: then flow
    page N at /run/page/N-1, then the report page."""
    bg = page(1, None, media=True)
    bg.update({"title": "Background Information", "route": "background", "url": f"{BASE}/background"})
    pages = [bg] + [page(i, i - 1) for i in range(2, numbered + 2)] + [page(numbered + 2, None, kind="report")]
    return module(pages)


def run_media_plan(m, tmp_path):
    src = tmp_path / "module-c1-r101.json"
    src.write_text(json.dumps(m), encoding="utf-8", newline="\n")
    env = dict(os.environ, PYTHONPATH=str(SHARED), LT_SUITE_DATA=str(tmp_path / "data"))
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text('schema_version = 1\n[lt]\norigin = "https://lt.example.invalid"\n', encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), str(src), "--config", str(cfg)],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout, json.loads((tmp_path / "data" / "runs" / "c1-r101" / "media-plan.json").read_text(encoding="utf-8"))


def test_plan_copies_the_background_route_and_check_url_follows_it(tmp_path):
    from ltcore import ltjs
    out, plan = run_media_plan(background_module(), tmp_path)
    assert f"visit page_index 1: {BASE}/background" in out
    assert plan["pages"][0] == {"page_index": 1, "page_no": None, "route": "background",
                                "url": f"{BASE}/background", "title": "Background Information"}
    assert plan["route_refusal"] is None and plan["route_source"] == "datakey+location"
    assert ltjs.check_url(f"{BASE}/background", plan)[1]["page_index"] == 1
    assert ltjs.check_url(f"{BASE}/page/1", plan)[1]["page_index"] == 2
    assert ltjs.check_url(f"{BASE}/page/20", plan)[1]["page_index"] == 21
    code, r = ltjs.check_url(f"{BASE}/page/21", plan)
    assert (code, r["reason"]) == (1, "page-not-in-plan"), "Lt has 20 numbered pages"


def test_legacy_capture_with_a_background_page_is_refused(tmp_path):
    from ltcore import ltjs
    m = background_module()
    del m["route_source"]
    for p in m["pages"]:          # how capture.js numbered them before measured routes
        if not p["is_report_page"]:
            p.update({"page_no": p["page_index"], "route": None, "url": f"{BASE}/page/{p['page_index']}"})
    out, plan = run_media_plan(m, tmp_path)
    assert plan["route_refusal"] == ltjs.LEGACY_CAPTURE
    assert "routes refused:" in out and "visit page_index 1: no checked url" in out
    assert all(p["url"] is None and p["page_no"] is None for p in plan["pages"])
    for rest in ("background", "page/1", "page/2"):
        code, r = ltjs.check_url(f"{BASE}/{rest}", plan)
        assert (code, r["reason"]) == (1, "recapture-needed"), rest


@pytest.mark.parametrize("checkpoint", [True, False], ids=["pre-reading-checkpoint", "no-boundary"])
def test_every_legacy_capture_is_refused_whatever_its_title(tmp_path, checkpoint):
    # A Background page titled "Pre-reading" leaves no trace a heuristic could read, and a
    # capture with no boundary at all is refused the same way: no route_source, no routes.
    from ltcore import ltjs
    pages = [page(1, 1, media=True), page(2, 2)]
    pages += [page(3, None, kind="checkpoint"), page(4, 3)] if checkpoint else [page(3, 3)]
    pages += [page(len(pages) + 1, None, kind="report")]
    pages[0]["title"] = "Pre-reading"
    m = module(pages, route_source=None)
    del m["route_source"]
    out, plan = run_media_plan(m, tmp_path)
    assert "route_source" in plan and plan["route_source"] is None
    assert plan["route_refusal"] == ltjs.LEGACY_CAPTURE and "module_only" in plan["route_refusal"]
    assert all(p["url"] is None and p["page_no"] is None for p in plan["pages"])
    assert plan["stop_before_page"] == (3 if checkpoint else None)
    for frm, to in ((None, "page/1"), ("page/1", "page/2"), (None, "background")):
        code, r = ltjs.check_url(f"{BASE}/{to}", plan, from_url=f"{BASE}/{frm}" if frm else None)
        assert (code, r["reason"]) == (1, "recapture-needed"), (frm, to)


def test_cf1_shifted_route_beyond_a_checkpoint_is_refused():
    # Codex's CF-1 reproduction. Actual routes: Background, page/1, checkpoint, page/2; the
    # capture gave the content pages page/1, page/2, page/3. Its plan once approved /page/2 as
    # flow page 2 although Lt shows flow page 4 there, past the checkpoint at 3.
    from ltcore import ltjs
    mp = load()
    run = BASE
    legacy = {"module_id": "c1-r101", "source_url": run + "/background",
              "instance": "inst0001", "course_id": 1, "run_id": 101,
              "pages": [
                  {"page_index": 1, "page_no": 1, "url": run + "/page/1", "items": []},
                  {"page_index": 2, "page_no": 2, "url": run + "/page/2", "items": []},
                  {"page_index": 3, "page_no": None, "is_checkpoint": True, "items": []},
                  {"page_index": 4, "page_no": 3, "url": run + "/page/3", "items": []}],
              "commit_boundaries": {"checkpoint_pages": [3]}}
    plan = mp.build_plan(legacy)
    assert plan["stop_before_page"] == 3
    code, result = ltjs.check_url(run + "/page/2", plan, from_url=run + "/page/1")
    assert (code, result["reason"]) == (1, "recapture-needed")
    # Measured routes put /page/2 on flow page 4, and the plan stops before the checkpoint.
    measured = {**legacy, "route_source": "datakey+location", "pages": [
        {"page_index": 1, "page_no": None, "route": "background", "url": run + "/background", "items": []},
        {"page_index": 2, "page_no": 1, "url": run + "/page/1", "items": []},
        {"page_index": 3, "page_no": None, "is_checkpoint": True, "items": []},
        {"page_index": 4, "page_no": 2, "url": run + "/page/2", "items": []}]}
    code, result = ltjs.check_url(run + "/page/2", mp.build_plan(measured), from_url=run + "/page/1")
    assert (code, result["reason"], result["page_index"]) == (1, "at-or-past-stop", 4)


def test_unsafe_target_exits_3(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SHARED), LT_SUITE_DATA=str(tmp_path / "data"))
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text("schema_version = 1\n", encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, str(SCRIPT), "..x", "--config", str(cfg)],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, encoding="utf-8")
    assert r.returncode == 3
