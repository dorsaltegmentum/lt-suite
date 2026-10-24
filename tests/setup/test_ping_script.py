# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the rendered ping script in the fake Lt page and check its reply against ``ping verify``.

The script comes from ``setup.py ping render`` (the guard is prepended by ltjs.py), runs in
tests/browser/harness.js, and the JSON it returns goes back through ``ping verify``.
"""
from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "skills/lt-suite-setup/scripts"
RUNNER = REPO / "tests/browser/run_rendered.js"
ORIGIN = "https://lt.example.invalid"  # the origin the fake Lt page reports
RUN_PAGE = "/i/inst/student/courses/1/runs/2/run/page/3"

pytestmark = pytest.mark.requires_bin("node")


def render(run_py, cfg, out, form):
    p = run_py(str(SCRIPTS / "setup.py"), "ping", "render", "--config", str(cfg),
               "--form", form, "--out-dir", str(out))
    assert p.returncode == 0, p.stderr
    return Path(json.loads(p.stdout)["path"])


def run_in_page(script, tmp_path, path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"mode": "run", "file": str(script), "path": path}))
    p = subprocess.run(["node", str(RUNNER), str(spec)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('schema_version = 1\n[lt]\norigin = "%s"\n' % ORIGIN)
    return path


@pytest.mark.parametrize("form", ["expression", "function"])
def test_rendered_ping_reply_passes_verify_and_records_directory(run_py, tmp_path, cfg, form):
    out = tmp_path / "js"
    ran = run_in_page(render(run_py, cfg, out, form), tmp_path, RUN_PAGE)
    reply = ran["result"]
    assert reply["ok"] is True
    assert reply["origin"] == ORIGIN
    assert reply["path_kind"] == "run-page"
    assert reply["lt_client"] is True
    assert reply["expected_origin_matches"] is True
    assert ran["commits"] == 0 and ran["trips"] == 0
    # The reply carries no page address and no page content beyond the origin.
    assert "href" not in reply and "pathname" not in reply
    p = run_py(str(SCRIPTS / "setup.py"), "ping", "verify", "--config", str(cfg),
               "--record-dir", str(out), "--reply", json.dumps(reply))
    assert p.returncode == 0, p.stderr
    assert tomllib.loads(cfg.read_text())["browser"]["js_tmp_dir"] == str(out)


def test_rendered_ping_refuses_to_run_on_the_completion_page(run_py, tmp_path, cfg):
    out = tmp_path / "js"
    ran = run_in_page(render(run_py, cfg, out, "expression"), tmp_path,
                      "/i/inst/student/courses/1/runs/2/run/complete")
    assert ran["result"]["ok"] is False
    assert ran["result"]["fatal"] == "forbidden-route"
    before = cfg.read_bytes()
    p = run_py(str(SCRIPTS / "setup.py"), "ping", "verify", "--config", str(cfg),
               "--record-dir", str(out), "--reply", json.dumps(ran["result"]))
    assert p.returncode == 1
    assert "completion page" in p.stderr
    assert cfg.read_bytes() == before
