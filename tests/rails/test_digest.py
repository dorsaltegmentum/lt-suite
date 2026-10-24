# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.digest: the question digest moves with questions only, and the pin fixer
keeps the schema examples bound to each other."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from ltcore import digest

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "tests" / "fixtures" / "examples"


def module():
    return json.loads((EX / "module.example.json").read_text(encoding="utf-8"))


def item(d, iid):
    return next(it for p in d["pages"] for it in p["items"] if it["item_id"] == iid)


def test_state_changes_keep_the_digest():
    m = module()
    before = digest.question_digest(m)
    m2 = copy.deepcopy(m)
    for p in m2["pages"]:
        for it in p["items"]:
            it["answered"] = True
            it["read_only"] = True
            it["automation"] = "degrade-todo"
    item(m2, "i-table-1")["constraints"]["note"] = "presentedRatio=0.88 locations=3"
    item(m2, "i-upann-1")["target_geometry"]["presented_ratio"] = 0.5
    item(m2, "i-text-1")["prompt_text"] = "Explain why  immersion oil improves resolution at high magnification."
    assert digest.question_digest(m2) == before


def test_question_changes_move_the_digest():
    base = digest.question_digest(module())
    for change in (
        lambda d: item(d, "i-text-1").update(prompt_text="Something else entirely."),
        lambda d: item(d, "i-rat-1")["options"].reverse(),
        lambda d: item(d, "i-table-1")["constraints"].update(rows=4),
        lambda d: item(d, "i-text-2").update(type="multiple-choice"),
        lambda d: d.update(module_id="c1-r999"),
    ):
        m = module()
        change(m)
        assert digest.question_digest(m) != base


def test_diff_names_the_item():
    old, new = module(), module()
    item(new, "i-text-1")["prompt_text"] = "A different question."
    out = digest.question_diff(old, new)
    assert len(out) == 1 and out[0].startswith("i-text-1: prompt_text")
    assert digest.question_diff(old, old) == []


def test_examples_are_pinned():
    assert digest.plan_pins(EX)[0] == {}
    ans = json.loads((EX / "answers.example.json").read_text())
    assert ans["source_module"]["question_digest"] == digest.digest_of_file(EX / "module.example.json")


def test_fix_pins_rewrites_a_stale_copy(tmp_path):
    d = tmp_path / "ex"
    shutil.copytree(EX, d)
    ans_path = d / "answers.example.json"
    ans = json.loads(ans_path.read_text())
    ans["source_module"]["sha256"] = "0" * 64
    ans["source_module"]["question_digest"] = "1" * 64
    ans_path.write_text(json.dumps(ans, indent=1) + "\n")
    assert digest.fix_pins(d, check=True) == 1
    assert digest.fix_pins(d) == 0
    assert digest.fix_pins(d, check=True) == 0
    assert ans_path.read_bytes() == (EX / "answers.example.json").read_bytes()
    assert (d / "fill-report.example.json").read_bytes() == (EX / "fill-report.example.json").read_bytes()


def test_fix_pins_reports_an_unresolvable_path(tmp_path):
    d = tmp_path / "ex"
    shutil.copytree(EX, d)
    (d / "module.example.json").unlink()
    assert digest.fix_pins(d, check=True) == 3


def test_cli(run_py, tmp_path):
    r = run_py("-m", "ltcore.digest", str(EX / "module.example.json"))
    assert r.returncode == 0 and r.stdout.strip() == digest.digest_of_file(EX / "module.example.json")
    r = run_py("-m", "ltcore.digest", "--fix-pins", "--check", str(EX))
    assert r.returncode == 0, r.stdout + r.stderr
    new = module()
    item(new, "i-text-1")["prompt_text"] = "A different question."
    np = tmp_path / "new.json"
    np.write_text(json.dumps(new))
    r = run_py("-m", "ltcore.digest", str(EX / "module.example.json"), "--diff", str(np))
    assert r.returncode == 1 and "i-text-1: prompt_text" in r.stdout
    r = run_py("-m", "ltcore.digest", str(EX / "module.example.json"), "--view")
    assert r.returncode == 0 and json.loads(r.stdout)["digest_version"] == digest.DIGEST_VERSION
    r = run_py("-m", "ltcore.digest", "--check", str(EX))
    assert r.returncode == 2
    r = run_py("-m", "ltcore.digest", str(tmp_path / "absent.json"))
    assert r.returncode == 3
