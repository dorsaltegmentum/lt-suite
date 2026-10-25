# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run real installation harnesses without using personal agent configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "install_tests"


def run_harness(script, tmp_path, *args):
    """Keep platform evidence outside the checkout and expose failures in pytest."""
    report = tmp_path / "result.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS / script), "--repo", str(ROOT),
         "--json-out", str(report), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["counts"]["FAIL"] == 0, data
    return data


def test_claude_code_install(tmp_path):
    """Validate, inventory and load 6 skills using a temporary Claude home."""
    cli = shutil.which("claude")
    if not cli:
        pytest.skip("Claude Code CLI is not installed")
    run_harness("install_claude_code.py", tmp_path, "--claude", cli, "--timeout", "45")


def test_codex_install(tmp_path):
    """Discover 6 skills from the marketplace and 2 skill-directory routes."""
    cli = shutil.which("codex")
    if not cli:
        pytest.skip("Codex CLI is not installed")
    run_harness("install_codex.py", tmp_path, "--codex", cli, "--timeout", "45")


def test_per_skill_archives(tmp_path):
    """Run entry points from each real distribution archive in isolation."""
    run_harness("install_zips.py", tmp_path, "--builder", "dist", "--strict-deps")


def test_gemini_and_antigravity_static(tmp_path):
    """Check manifests; missing CLIs emit explicit manual steps."""
    run_harness("install_gemini_agy.py", tmp_path, "--timeout", "45")


def test_npx_install(tmp_path):
    """Opt into npm downloads while retaining clean missing-CLI skips."""
    if not shutil.which("npx") or not shutil.which("node"):
        pytest.skip("npx or Node.js is not installed")
    if os.environ.get("LT_SUITE_INSTALL_NETWORK") != "1":
        pytest.skip("set LT_SUITE_INSTALL_NETWORK=1 to test the npm install")
    data = run_harness("install_npx_skills.py", tmp_path, "--timeout", "90")
    if any(i["status"] == "SKIP" and i["check"].endswith("/list") for i in data["results"]):
        pytest.skip("npm registry is unavailable")
