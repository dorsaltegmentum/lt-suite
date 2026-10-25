# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The capture page scripts as an agent runs them: rendered by the skill's own copy of
ltjs.py (guard prepended, arguments frozen in __LT_ARGS__), then evaluated in the vm harness.

Checks name resolution from another working folder, both forms and the hash-checked stub,
that the never-submit guard stays installed after the script returns, and that the download
route survives the guard's navigation listener.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "lt-module-capture"
LTJS = SKILL / "scripts" / "_shared" / "ltcore" / "ltjs.py"
RUNNER = REPO / "tests" / "capture" / "run_rendered.js"
SINK_ARGS = ["--arg", "transport=sink", "--arg", "sink=http://127.0.0.1:8731", "--arg", "token=test-token-0123456789abcdef"]

pytestmark = [
    pytest.mark.requires_bin("node"),
    pytest.mark.skipif(not LTJS.is_file(), reason="not run: WP04 ltjs.py missing from the synced skill"),
]


def render(tmp: Path, name: str, *args: str) -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, str(LTJS), "render", name, "--out-dir", str(tmp / "js"), *args],
                       capture_output=True, text=True, cwd=tmp, env=env, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def run(path: str, form: str, scenario: str = "plain") -> dict:
    r = subprocess.run([shutil.which("node") or "node", str(RUNNER), path, form, scenario],
                       capture_output=True, text=True, cwd=REPO, timeout=120, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_names_resolve_against_the_skill_folder_from_any_cwd(tmp_path):
    for name in ("capture", "fetch_media"):
        out = render(tmp_path, name, "--form", "function", "--no-stub", *SINK_ARGS)
        assert Path(out["source"]).resolve() == (SKILL / "scripts" / "browser" / f"{name}.js").resolve()
        assert out["net"] is False, "capture scripts carry no save watcher"
        assert out["guard"] == "block-all", "capture has no plan, so every page change is blocked"
        text = Path(out["path"]).read_text(encoding="utf-8")
        assert "LtGuard" in text and "__LT_ARGS__" in text


@pytest.mark.parametrize("form,stub", [("function", False), ("expression", False), ("function", True),
                                       ("expression", True)])
def test_rendered_capture_runs_and_reports_guard_trips(tmp_path, form, stub):
    out = render(tmp_path, "capture", "--form", form, "--stub" if stub else "--no-stub", *SINK_ARGS)
    res = run(out["path"], form, "demo")
    rec = res["receipt"]
    assert rec["ok"] is True, rec
    assert rec["guard_trips"] == 0
    assert rec["summary"]["module_id"] == "c1-r101"
    assert res["names"] == ["module-c1-r101", "reference-c1-r101"]
    assert res["after_commit"] and "never-submit" in res["after_commit"], \
        "the guard stays installed after the script returns"


def test_a_copying_slip_stops_the_stub(tmp_path):
    out = render(tmp_path, "capture", "--form", "function", "--stub", *SINK_ARGS)
    p = Path(out["path"])
    text = p.read_text(encoding="utf-8")
    i = text.index('const B64 = "') + len('const B64 = "') + 40
    flipped = text[:i] + ("A" if text[i] != "A" else "B") + text[i + 1:]
    bad = tmp_path / "bad.js"
    bad.write_text(flipped, encoding="utf-8", newline="\n")
    res = run(str(bad), "function")
    assert res["receipt"]["ok"] is False
    assert res["receipt"]["fatal"] == "stub-hash-mismatch"
    assert res["names"] == []


def test_module_only_argument_reaches_the_script(tmp_path):
    out = render(tmp_path, "capture", "--form", "function", *SINK_ARGS, "--arg", "module_only=true")
    res = run(out["path"], "function")
    assert res["receipt"]["module_only"] is True
    assert res["names"] == ["module-c1-r101"]


def test_typed_arguments_through_arg_json(tmp_path):
    out = render(tmp_path, "capture", "--form", "function", *SINK_ARGS,
                 "--arg-json", 'drop_text_patterns=["^Read the"]', "--arg-json", "post_ms=5000")
    res = run(out["path"], "function", "demo")
    assert res["receipt"]["ok"] is True
    assert res["receipt"]["bad_drop_patterns"] == []


def test_download_route_survives_the_guard(tmp_path):
    """The guard's navigate listener must let an <a download> of a blob URL through: a
    download changes no route. If it cancels the event, the download transport saves nothing."""
    out = render(tmp_path, "capture", "--form", "function", "--arg", "transport=download")
    res = run(out["path"], "function")
    assert res["navigate_listeners"] >= 1, "the guard listens for navigations"
    assert res["cancelled"] == [], f"the guard cancelled the downloads: {res['cancelled']}"
    assert res["downloads"] == ["module-c1-r101.json", "reference-c1-r101.json"]
    assert res["receipt"]["guard_trips"] == 0
