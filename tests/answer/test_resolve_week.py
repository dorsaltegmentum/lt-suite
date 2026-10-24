# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise week resolution, configurable matching and all four exit codes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills/lt-module-answer/scripts/resolve_week.py"


def settings(tmp_path, extra=""):
    root = tmp_path / "curriculum"
    root.mkdir(exist_ok=True)
    cfg = tmp_path / "config.toml"
    cfg.write_text('[curriculum]\nroot = "curriculum"\n' + extra, encoding="utf-8")
    return root, cfg


def resolve(run_py, cfg, title="Week 1 Demo"):
    return run_py(str(SCRIPT), "--config", str(cfg), "--title", title, "--json")


@pytest.mark.parametrize("folder", ["Week 1 Demo", "1 Demo"])
def test_one_match_both_folder_conventions(tmp_path, run_py, folder):
    root, cfg = settings(tmp_path)
    week = root / "Block" / folder
    subject = week / "Topic"
    subject.mkdir(parents=True)
    (subject / "lecture.txt").write_text("Synthetic lecture material.")
    (week / "figure.png").write_bytes(b"synthetic")
    result = resolve(run_py, cfg)
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["week_number"] == "1" and doc["status"] == "ok"
    assert doc["matches"] == [{"path": str(week), "block": "Block", "name": folder,
                               "subjects": [{"name": "Topic", "files": 1}],
                               "files_by_ext": {".txt": 1}, "total_files": 2}]
    assert doc["curriculum_root_from"].startswith("config ")


def test_no_match_exit_one_does_not_invent_absence(tmp_path, run_py):
    _, cfg = settings(tmp_path)
    result = run_py(str(SCRIPT), "--config", str(cfg), "--title", "Week 4 Demo")
    assert result.returncode == 1
    assert "lookup failure is not source absence" in result.stdout


def test_ambiguity_is_exit_two(tmp_path, run_py):
    root, cfg = settings(tmp_path)
    for block in ("Alpha", "Beta"):
        (root / block / "1 Demo").mkdir(parents=True)
    result = resolve(run_py, cfg)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "ambiguous"
    assert len(json.loads(result.stdout)["matches"]) == 2


def test_missing_module_is_exit_three(tmp_path, run_py):
    _, cfg = settings(tmp_path)
    result = run_py(str(SCRIPT), "--config", str(cfg), "--module", str(tmp_path / "missing.json"))
    assert result.returncode == 3 and "resolver:" in result.stderr


@pytest.mark.parametrize("payload", ["{", "[]", '{"lesson": null}', '{"lesson": {"title": 7}}'])
def test_invalid_module_is_exit_three(tmp_path, run_py, payload):
    _, cfg = settings(tmp_path)
    module = tmp_path / "module.json"
    module.write_text(payload)
    result = run_py(str(SCRIPT), "--config", str(cfg), "--module", str(module))
    assert result.returncode == 3 and "Traceback" not in result.stderr


def test_usage_is_three_help_is_zero(run_py):
    assert run_py(str(SCRIPT)).returncode == 3
    assert run_py(str(SCRIPT), "--help").returncode == 0


def test_disabled_root_is_not_current_directory(tmp_path, run_py):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    (tmp_path / "1 Demo").mkdir()
    result = resolve(run_py, cfg)
    assert result.returncode == 1
    assert json.loads(result.stdout)["lookup_disabled"] is True


def test_number_is_not_required_when_user_must_supply_source(tmp_path, run_py):
    _, cfg = settings(tmp_path)
    result = resolve(run_py, cfg, "Demo without a week")
    assert result.returncode == 1 and json.loads(result.stdout)["week_number"] is None


@pytest.mark.parametrize("folder,title,number", [
    ("Week 2.3 Demo", "Session 02.03 Demo", "2.3"),
    ("2.3 Demo", "Session 2.3 Demo", "2.3"),
    ("Week 2 Demo", "Session 2 Demo", "2"),
])
def test_optional_second_capture_group(tmp_path, run_py, folder, title, number):
    root, cfg = settings(tmp_path, "week_number_pattern = '(?i)session (\\d+)(?:\\.(\\d+))?'\n")
    (root / "Block" / folder).mkdir(parents=True)
    result = resolve(run_py, cfg, title)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["week_number"] == number


def test_custom_folder_pattern_and_nested_material(tmp_path, run_py):
    root, cfg = settings(tmp_path, "week_dir_pattern = '^Unit-{num}(?:\\D|$)'\n")
    (root / "Program" / "Block" / "Unit-1 Demo").mkdir(parents=True)
    assert resolve(run_py, cfg).returncode == 0


def test_skip_dirs_hidden_files_and_custom_extensions(tmp_path, run_py):
    root, cfg = settings(tmp_path, 'skip_dirs = ["Archive"]\ntext_extensions = [".rst"]\n')
    for name in ("Archive", ".hidden", "Current"):
        (root / name / "Week 1 Demo").mkdir(parents=True)
    week = root / "Current" / "Week 1 Demo"
    (week / ".hidden.txt").write_text("hidden")
    (week / "Archive").mkdir()
    (week / "Archive" / "old.txt").write_text("ignored")
    (week / "Topic").mkdir()
    (week / "Topic" / "notes.RST").write_text("synthetic source")
    result = resolve(run_py, cfg)
    assert result.returncode == 0
    match = json.loads(result.stdout)["matches"][0]
    assert match["total_files"] == 1 and match["files_by_ext"] == {".rst": 1}


def test_does_not_treat_nested_topics_as_more_weeks(tmp_path, run_py):
    root, cfg = settings(tmp_path)
    (root / "Week 1 Demo" / "1 Topic").mkdir(parents=True)
    assert resolve(run_py, cfg).returncode == 0


def test_learning_outcome_ranking_and_stopwords(tmp_path, run_py):
    root, cfg = settings(tmp_path, 'learning_outcomes_file = "outcomes.md"\ntitle_stopwords = ["Week"]\n')
    (root / "Block" / "1 Demo").mkdir(parents=True)
    (root / "outcomes.md").write_text("Week 1 Other\nWeek 1 Demo\nWeek 10 Demo\nWeek 1.2 Demo\n")
    result = resolve(run_py, cfg)
    assert result.returncode == 0
    doc = json.loads(result.stdout)
    assert doc["ilo_hits"] == 2
    assert doc["ilo_sample"][0] == "* 2: Week 1 Demo"
    assert doc["ilo_sample"][1] == "  1: Week 1 Other"


@pytest.mark.parametrize("extra", [
    "week_number_pattern = '(unclosed'\n",
    "week_number_pattern = 'week'\n",
    'learning_outcomes_file = "missing.md"\n',
])
def test_config_or_outcome_error_is_three(tmp_path, run_py, extra):
    _, cfg = settings(tmp_path, extra)
    assert resolve(run_py, cfg).returncode == 3


def test_missing_configured_root_is_three(tmp_path, run_py):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[curriculum]\nroot = "missing"\n')
    assert resolve(run_py, cfg).returncode == 3


def test_module_title_and_relative_paths_work_from_other_cwd(tmp_path, run_py):
    root, cfg = settings(tmp_path)
    (root / "Week 1 Demo").mkdir()
    module = tmp_path / "module.json"
    module.write_text(json.dumps({"lesson": {"title": "Week 1 Demo", "course_title": "Synthetic course"}}))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = run_py(str(SCRIPT), "--module", str(module), "--config", str(cfg), "--json", cwd=elsewhere)
    assert result.returncode == 0
    assert json.loads(result.stdout)["course_title"] == "Synthetic course"


@pytest.mark.parametrize('alias', [False, True])
def test_rat_learning_outcome_path_refused_before_read(tmp_path, run_py, alias):
    root, cfg = settings(tmp_path)
    (root / 'Week 1 Demo').mkdir()
    sheets = root / 'sheets'
    sheets.mkdir()
    sheet = sheets / 'answers.md'
    sheet.write_text('Week 1 invented-secret-selection\n')
    outcome = sheet
    if alias:
        outcome = root / 'outcomes.md'
        outcome.symlink_to(sheet)
    cfg.write_text(cfg.read_text() + f'learning_outcomes_file = {json.dumps(str(outcome))}\n'
                   f'[rat]\nanswers_dir = {json.dumps(str(sheets))}\nirat = "skip"\ntrat = "skip"\n')
    result = resolve(run_py, cfg)
    assert result.returncode == 3
    assert 'RAT root' in result.stderr
    assert 'invented-secret-selection' not in result.stdout + result.stderr


def test_rat_outcome_refusal_opens_no_sheet(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('review_resolve_week', SCRIPT)
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    sheets = tmp_path/'sheets'
    sheets.mkdir()
    sheet = sheets/'answers.md'
    sheet.write_text('Week 1 invented selection')
    alias = tmp_path/'alias.md'
    alias.symlink_to(sheet)
    opened = []
    original = Path.open
    def watched(path, *args, **kwargs):
        if path.resolve() == sheet:
            opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', watched)
    with pytest.raises(ValueError, match='RAT root'):
        resolver.ilo_lines('1', 'Week 1 Demo', alias, set(), rat_roots=[sheets])
    assert opened == []
