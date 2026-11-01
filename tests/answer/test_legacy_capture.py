# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answer drafting reads a capture without route_source.

Navigation refuses such a capture (recapture-needed), because its page URLs may be one page
late. Drafting never reads routes: the module validates, its question digest prints, and
answers bound to it validate, whatever its first page's title or its boundaries.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures"
SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "lt-module-answer" / "scripts"


@pytest.mark.parametrize("module_id", ["c1-r106", "c1-r101"], ids=["checkpoint", "no-boundary"])
def test_drafting_tools_accept_a_capture_without_route_source(run_py, tmp_path, module_id):
    module = json.loads((FIX / "modules" / f"module-{module_id}.json").read_text(encoding="utf-8"))
    assert "route_source" not in module
    module["pages"][0]["title"] = "Pre-reading"
    mpath = tmp_path / "modules" / f"module-{module_id}.json"
    mpath.parent.mkdir()
    mpath.write_text(json.dumps(module), encoding="utf-8", newline="\n")
    answers = json.loads((FIX / "answers" / f"answers-{module_id}.json").read_text(encoding="utf-8"))
    answers["source_module"]["sha256"] = hashlib.sha256(mpath.read_bytes()).hexdigest()
    apath = tmp_path / "answers" / f"answers-{module_id}.json"
    apath.parent.mkdir()
    apath.write_text(json.dumps(answers), encoding="utf-8", newline="\n")
    validate = str(SCRIPTS / "validate_answers.py")
    r = run_py(validate, str(mpath), "--schema", "module", cwd=tmp_path, shared_on_path=False)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_py(str(SCRIPTS / "question_digest.py"), str(mpath), cwd=tmp_path, shared_on_path=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert answers["source_module"]["question_digest"] in r.stdout
    r = run_py(validate, str(apath), "--schema", "answers", "--module", str(mpath), cwd=tmp_path,
               shared_on_path=False)
    assert r.returncode == 0, r.stdout + r.stderr
