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


def module(pages):
    return {"module_id": "c1-r101", "instance": "inst0001", "course_id": 1, "run_id": 101,
            "source_url": f"{BASE}/page/1", "pages": pages,
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


def test_unsafe_target_exits_3(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SHARED), LT_SUITE_DATA=str(tmp_path / "data"))
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text("schema_version = 1\n", encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, str(SCRIPT), "..x", "--config", str(cfg)],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, encoding="utf-8")
    assert r.returncode == 3
