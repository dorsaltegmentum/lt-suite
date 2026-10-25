# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.validate as a command and as a library: exit codes, schema inference,
legacy fields, the model-answer setting and a missing jsonschema."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import validate

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "tests" / "fixtures" / "examples"
SCHEMAS = REPO / "shared" / "schemas"


def test_schema_ids_point_at_the_public_repo():
    for p in sorted(SCHEMAS.glob("*.schema.json")):
        if p.name == "config.schema.json":
            continue
        sid = json.loads(p.read_text(encoding="utf-8"))["$id"]
        assert sid == f"https://github.com/dorsaltegmentum/lt-suite/schemas/{p.name}", p.name


def test_retired_gate_fields_are_gone_from_the_schemas():
    mod = json.loads((SCHEMAS / "module.schema.json").read_text(encoding="utf-8"))
    run = json.loads((SCHEMAS / "run.schema.json").read_text(encoding="utf-8"))
    assert "permission" not in mod["properties"] and "permission" not in mod["required"]
    assert "permission_check" not in run["properties"]
    assert {"tool_version", "config_digest"} <= set(run["required"])
    ref = json.loads((SCHEMAS / "reference.schema.json").read_text(encoding="utf-8"))
    assert validate.MODEL_ANSWER_SOURCE in ref["properties"]["provenance_of_reference"]["enum"]


@pytest.mark.parametrize("name,want", [
    ("module-c1-r101.json", "module.schema.json"),
    ("answers.json", "answers.schema.json"),
    ("fill-report-c1-r101.json", "fill-report.schema.json"),
    ("reference-x.json", "reference.schema.json"),
    ("run-2000.json", "run.schema.json"),
    ("approvals.json", "approvals.schema.json"),
    ("notes.json", None),
])
def test_infer_schema(name, want):
    assert validate.infer_schema(name) == want


def test_cli_module_example(run_py):
    r = run_py("-m", "ltcore.validate", "--schemas-dir", str(SCHEMAS),
               str(EX / "module.example.json"))
    assert r.returncode == 0 and r.stdout.startswith("OK: module.example.json"), r.stdout + r.stderr


def test_cli_runs_by_path_without_pythonpath(run_py, repo_root):
    r = run_py(str(repo_root / "shared" / "ltcore" / "validate.py"),
               str(EX / "run.example.json"), shared_on_path=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_exit_3_cases(run_py, tmp_path):
    r = run_py("-m", "ltcore.validate", str(tmp_path / "module-absent.json"))
    assert r.returncode == 3 and "no such instance file" in r.stderr
    notes = tmp_path / "notes.json"
    notes.write_text("{}", encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.validate", str(notes))
    assert r.returncode == 3 and "cannot infer a schema" in r.stderr
    bad = tmp_path / "module-bad.json"
    bad.write_text("{not json", encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.validate", str(bad))
    assert r.returncode == 3
    r = run_py("-m", "ltcore.validate", str(EX / "module.example.json"), "--schemas-dir",
               str(tmp_path))
    assert r.returncode == 3 and "cannot load schema" in r.stderr


def test_cli_schema_fail_is_exit_2(run_py, tmp_path):
    d = json.loads((EX / "module.example.json").read_text(encoding="utf-8"))
    del d["pages"]
    p = tmp_path / "module.json"
    p.write_text(json.dumps(d), encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.validate", str(p))
    assert r.returncode == 2 and "SCHEMA FAIL" in r.stdout and "'pages' is a required" in r.stdout


def test_missing_jsonschema_exits_3(run_py):
    code = ("import sys; sys.modules['jsonschema'] = None\n"
            "from ltcore import validate\n"
            f"sys.exit(validate.main([{str(EX / 'module.example.json')!r}]))\n")
    r = run_py("-c", code)
    assert r.returncode == 3 and "jsonschema" in r.stderr, r.stdout + r.stderr


def test_import_needs_no_jsonschema(run_py):
    r = run_py("-c", "import sys; sys.modules['jsonschema'] = None; import ltcore.validate")
    assert r.returncode == 0, r.stderr


def test_drop_legacy_fields():
    doc = {"module_id": "c1-r101", "permission": {"unit_code": "X101"},
           "permission_check": {"exit_code": 0}}
    assert validate.drop_legacy_fields(doc) == ["permission", "permission_check"]
    assert doc == {"module_id": "c1-r101"}
    assert validate.drop_legacy_fields(doc) == []
    assert "permission" in validate.legacy_note(["permission"])


def test_validate_doc_does_not_mutate_the_callers_document():
    d = json.loads((EX / "module.example.json").read_text(encoding="utf-8"))
    d["permission"] = {"unit_code": "X101", "checked_at": "2000-01-01T00:00:00Z", "exit_code": 0}
    res = validate.validate_doc(d, "module")
    assert res.ok and "permission" in d
    assert any("dropped legacy field(s) permission" in n for n in res.notes)


def test_library_api_with_parsed_documents():
    fr = EX / "fill-report.example.json"
    mod = json.loads((EX / "module.example.json").read_text(encoding="utf-8"))
    res = validate.validate_file(fr, module=mod)
    assert res.ok and res.schema_name == "fill-report.schema.json"
    res = validate.validate_file(fr, module=mod, model_answer_read=False)
    assert res.exit_code == 1 and any("model-answer-policy" in e for e in res.rail_errors)


def test_model_answer_setting_comes_from_the_config(tmp_path, run_py):
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text("schema_version = 1\n\n[model_answer]\nread = false\n", encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.validate", str(EX / "fill-report.example.json"), "--config", str(cfg))
    assert r.returncode == 1 and "model-answer-policy" in r.stdout
    assert f"config {cfg}" in r.stdout or str(cfg) in r.stdout
    r = run_py("-m", "ltcore.validate", str(EX / "fill-report.example.json"), "--config", str(cfg),
               "--model-answer-read", "true")
    assert r.returncode == 0, r.stdout
    # The variable is how a skill points every script at one config file.
    r = run_py("-m", "ltcore.validate", str(EX / "fill-report.example.json"),
               env={"LT_SUITE_CONFIG": str(cfg)})
    assert r.returncode == 1 and "model-answer-policy" in r.stdout


def test_a_broken_config_is_exit_3(tmp_path, run_py):
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text("[model_answer\n", encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.validate", str(EX / "reference.example.json"), "--config", str(cfg))
    assert r.returncode == 3 and "config" in r.stderr


def test_bad_model_answer_flag_is_a_usage_error(run_py):
    r = run_py("-m", "ltcore.validate", str(EX / "reference.example.json"),
               "--model-answer-read", "maybe")
    assert r.returncode == 2


def test_an_invalid_approvals_file_is_exit_3(tmp_path, run_py):
    ap = tmp_path / "approvals.json"
    ap.write_text(json.dumps({"schema_version": 1, "module_id": "c1-r101",
                              "approvals": [{"approval_id": "nope"}]}), encoding="utf-8", newline="\n")
    r = run_py("-m", "ltcore.validate", str(EX / "fill-report.example.json"), "--approvals", str(ap))
    assert r.returncode == 3 and "approvals file" in r.stderr
