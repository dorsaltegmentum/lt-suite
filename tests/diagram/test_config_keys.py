# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The diagram.* config keys reach the engine: header fields, naming, delivery labels, the illustration
credit, the treatment ledger name and the page limits."""
import json

import pytest

import case as C
import decor
import guideline as G
import make_artifact
from diagram_testkit import ENGINE, TOY, copy_case, run_engine


@pytest.fixture
def configured(tmp_path):
    import style

    def use(text):
        cfg = tmp_path / "lt-suite.toml"
        cfg.write_text(text, encoding="utf-8", newline="\n")
        style.configure(str(cfg))
        return cfg
    yield use
    style.configure(None)


def test_illustration_credit_from_the_config(tmp_path, configured, monkeypatch):
    case_dir = copy_case(tmp_path / "toy-case")
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    del meta["inset"]["credit"]
    (case_dir / "case.json").write_text(json.dumps(meta), encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path)
    configured('[diagram.illustration]\ncredit = "Drawing: the course team"\n')
    assert decor.inset_spec(C.Case(case_dir, trust=True))["credit"] == "Drawing: the course team"


def test_case_credit_wins(configured):
    configured('[diagram.illustration]\ncredit = "from the config"\n')
    assert decor.inset_spec(C.Case(TOY))["credit"] == "Drawn for this example"


def test_delivery_labels(configured):
    assert make_artifact.submission_note() == " This is the submission copy."
    configured('[diagram.delivery]\nlms_name = "the course site"\nsubmission_label = "the mechanism task"\n')
    assert make_artifact.submission_note() == \
        " This is the submission copy (the mechanism task); hand it in through the course site."


def test_header_fields_and_member_order(tmp_path, configured, monkeypatch):
    case_dir = copy_case(tmp_path / "toy-case")
    monkeypatch.chdir(tmp_path)
    configured('[diagram.header]\nrequired_fields = ["title", "group", "members"]\nmembers_order = "surname"\n')
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    meta["members"] = ["Amy Zulu", "Zed Able"]          # Zulu before Able: not in surname order
    (case_dir / "case.json").write_text(json.dumps(meta), encoding="utf-8", newline="\n")
    pre = C.Case(case_dir, trust=True).preflight()
    assert any("surname order" in p for p in pre), pre
    meta.pop("group")
    (case_dir / "case.json").write_text(json.dumps(meta), encoding="utf-8", newline="\n")
    assert any("group" in p for p in C.Case(case_dir, trust=True).preflight())


def test_ledger_name_from_the_config(tmp_path, configured):
    case_dir = copy_case(tmp_path / "toy-case")
    (case_dir / "guideline-ledger.json").rename(case_dir / "my-ledger.json")
    configured('[diagram.treatment_check]\nledger_name = "my-ledger.json"\nprovider_label = "local formulary"\n')
    lines, counts = G.check(case_dir, trust=True)
    assert counts["ok"] == 2
    assert any("local formulary" in line for line in lines)


def test_new_case_naming(tmp_path, engine_env):
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text('[diagram.naming]\ncase_slug_pattern = "unit-{n}"\neyebrow_template = "Session {n}"\n', encoding="utf-8", newline="\n")
    r = run_engine("new_case.py", tmp_path / "week-7", "--config", cfg, cwd=tmp_path, env=engine_env, timeout=120)
    assert r.returncode == 0, r.stderr
    meta = json.loads((tmp_path / "week-7" / "case.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "unit-7"
    assert 'EYEBROW = "Session 7"' in (tmp_path / "week-7" / "artifact_content.py").read_text(encoding="utf-8")
    again = run_engine("new_case.py", tmp_path / "week-7", cwd=tmp_path, env=engine_env, timeout=120)
    assert again.returncode != 0 and "not overwriting" in (again.stdout + again.stderr)
    assert ENGINE.is_dir()


def test_diagram_fonts_from_the_diagram_table(configured,font_file):
    import core,style,themes
    path=font_file('Test Diagram')
    configured('[fonts.diagram]\nsans = '+json.dumps(str(path))+'\n')
    assert style.font_family('sans') == 'Test Diagram'
    assert style.font_path('sans') == path
    assert all(t['sans']=='Test Diagram' for t in themes.THEMES.values())
    core.use_configured_fonts()
    core.fonts_for_geometry({'font':'Test Diagram'})
    with pytest.raises(ValueError,match='fonts.diagram.sans'):
        core.fonts_for_geometry({'font':'Other Font'})
    core._FR=None
