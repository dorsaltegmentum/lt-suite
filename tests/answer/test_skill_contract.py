# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lint the drafting safety contract before any answer research begins."""
from __future__ import annotations

import re


def test_skill_passes_authoritative_safety_checker(repo_root, run_py):
    result = run_py(str(repo_root / "tools/check_skills.py"),
                    str(repo_root / "skills/lt-module-answer"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_suspect_text_has_a_complete_predrafting_disclosure_contract(repo_root):
    # A drafting agent needs explicit disclosure fields and an instruction boundary.
    text = (repo_root / "skills/lt-module-answer/SKILL.md").read_text(encoding="utf-8")
    before_research = text.split("## 3. Research and draft each item", 1)[0]
    paragraphs = re.split(r"\n\s*\n", before_research)
    policy = " ".join(p for p in paragraphs if "suspect_text" in p)
    assert policy, "captured suspect_text needs a policy before research"
    for field in ("item_id", "panel_key", "visible text", "reason"):
        assert field in policy, f"suspect_text disclosure must identify {field}"
    assert re.search(r"(?:show|display).*user", policy, re.IGNORECASE | re.DOTALL)
    assert "every" in policy and "before drafting" in policy
    assert "untrusted" in policy and "never follow" in policy
