# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""capture.js -> sink.py -> stamp_capture.py over real HTTP.

The page script runs in node against the built-in scenario and POSTs to a real sink with the
Origin header a browser adds and the session token sink.py printed. stamp_capture.py then
validates and moves the files. A module-only re-capture refreshes the module and leaves the
reference byte-identical; a full re-capture is refused for the reference only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DUMP = REPO / "tests" / "capture" / "dump_capture.js"
STAMP = REPO / "skills" / "lt-module-capture" / "scripts" / "stamp_capture.py"
SHARED = REPO / "shared"
MID = "c1-r101"

pytestmark = pytest.mark.requires_bin("node")


def sink_class():
    spec = importlib.util.spec_from_file_location("capture_test_sink", Path(__file__).with_name("test_sink.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def capture(s, *extra: str) -> dict:
    r = subprocess.run([shutil.which("node") or "node", str(DUMP), "demo", "--sink", f"http://127.0.0.1:{s.port}",
                        "--token", s.token, "--origin", "https://lt.example.invalid", *extra],
                       capture_output=True, text=True, cwd=REPO, timeout=120, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["receipt"]


def stamp(tmp: Path, cfg: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SHARED), LT_SUITE_DATA=str(tmp / "data"))
    return subprocess.run([sys.executable, str(STAMP), "--config", str(cfg), MID],
                          capture_output=True, text=True, env=env, cwd=tmp, timeout=120, encoding="utf-8")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_capture_sink_stamp_and_module_only_recapture(tmp_path):
    S = sink_class().Sink
    s = S(tmp_path)
    try:
        receipt = capture(s)
        assert [t["route"] for t in receipt["transport"]] == ["sink", "sink"], receipt
        assert receipt["ok"] is True and receipt["reference_ok"] is True
        assert (s.incoming / f"module-{MID}.json").is_file()
        assert (s.incoming / f"reference-{MID}.json").is_file()

        r = stamp(tmp_path, s.cfg)
        assert r.returncode == 0, r.stdout + r.stderr
        module = s.captures / f"module-{MID}.json"
        reference = s.captures / f"reference-{MID}.json"
        assert module.is_file() and reference.is_file()
        ref_sha = sha(reference)

        # A full re-capture: the module arrives, the reference is refused by the sink.
        again = capture(s)
        assert again["transport"][0]["ok"] is True
        assert again["transport"][1]["status"] == 409 and again["reference_ok"] is False

        # A module-only re-capture after a fill.
        only = capture(s, "--module-only")
        assert only["module_only"] is True and len(only["transport"]) == 2
        assert only["transport"][1]["route"] == "skipped"
        r = stamp(tmp_path, s.cfg)
        assert r.returncode == 0, r.stdout + r.stderr
        assert sha(reference) == ref_sha, "the reference is byte-identical after re-captures"
        assert not list(s.incoming.glob(f"module-{MID}*.json")), "every incoming copy was stamped or set aside"
    finally:
        s.stop()
