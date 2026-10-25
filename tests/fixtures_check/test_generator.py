# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""tools/gen_fixtures.py: determinism, drift detection and the scope of the seed."""
from __future__ import annotations

import subprocess
import sys
import json

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FIX, REPO  # noqa: E402


def test_check_mode_exits_zero():
    r = subprocess.run([sys.executable, str(REPO / "tools" / "gen_fixtures.py"), "--check"],
                       capture_output=True, text=True, cwd=REPO, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("OK:")


def test_two_builds_are_byte_identical(gen):
    a, _ = gen.build_all(gen.SEED_DEFAULT)
    b, _ = gen.build_all(gen.SEED_DEFAULT)
    assert a == b


def test_committed_files_are_the_generated_files(gen):
    files, _ = gen.build_all(gen.SEED_DEFAULT)
    assert gen.diff_tree(files, FIX) == []


def test_hand_written_file_is_never_generated(gen):
    files, _ = gen.build_all(gen.SEED_DEFAULT)
    assert gen.HAND_WRITTEN == {"modules/expected-boundaries.json"}
    assert not set(files) & gen.HAND_WRITTEN


def test_seed_moves_images_and_hashes_only(gen):
    """The structure of every module, view model, reference and course folder is fixed; the
    seed only changes the images, the fresh-load hashes and the team-test picks."""
    a, _ = gen.build_all(gen.SEED_DEFAULT)
    b, _ = gen.build_all(gen.SEED_DEFAULT + 1)
    same = ("modules/", "viewmodels/", "references/", "curriculum/", "pages/")
    for rel in a:
        if rel.startswith(same):
            assert a[rel] == b[rel], rel
    assert any(a[r] != b[r] for r in a if r.startswith("media/"))
    assert any(a[r] != b[r] for r in a if r.endswith("receipts.json"))


def test_drift_is_reported(gen, tmp_path):
    files, _ = gen.build_all(gen.SEED_DEFAULT)
    gen.write_tree(files, tmp_path)
    assert gen.diff_tree(files, tmp_path) == []
    victim = "modules/module-c1-r101.json"
    (tmp_path / victim).write_bytes(files[victim] + b" ")
    (tmp_path / "modules" / "stray.json").write_text("{}", encoding="utf-8", newline="\n")
    (tmp_path / "media" / "lt-c1-r101-31.png").unlink()
    got = gen.diff_tree(files, tmp_path)
    assert got == ["missing  media/lt-c1-r101-31.png", f"changed  {victim}",
                   "orphan   modules/stray.json"]


def test_text_scan_catches_a_private_value(gen):
    bad = {"a.json": ('{"path": "/' + 'home/someone/notes"}').encode(),
           "b.json": ('{"d": "2026-' + '10-01"}').encode(),
           "c.json": b'{"mail": "someone@example.org"}',
           "d.json": ('{"x": "D' + '7.12"}').encode(),
           "ok.json": b'{"t": "2000-01-01T00:00:00Z"}'}
    hits = gen.scan_text(bad)
    assert [h.split(":")[0] for h in hits] == ["a.json", "b.json", "c.json", "d.json"]


def test_missing_validator_is_a_failure(gen, monkeypatch, tmp_path):
    """A missing validator must never produce a successful validation report."""
    monkeypatch.setattr(gen, "load_validator", lambda: None)
    errors = gen.validate_tree(tmp_path, {})
    assert errors and "ltcore.validate" in errors[0]


def test_generation_preserves_unrecognised_files(gen, tmp_path, capsys):
    """Regeneration must report an orphan without deleting another contributor's file."""
    orphan = tmp_path / "modules" / "extra.json"
    orphan.parent.mkdir()
    orphan.write_text('{"keep": true}\n', encoding="utf-8", newline="\n")
    assert gen.main(["--out", str(tmp_path), "--no-validate"]) == 1
    assert orphan.read_text(encoding="utf-8") == '{"keep": true}\n'
    assert "orphan" in capsys.readouterr().out


def test_generated_approvals_cover_every_report_waiver(gen, validate, tmp_path):
    """Fill fixtures must verify their waiver ids against stored approval records."""
    files, _ = gen.build_all(gen.SEED_DEFAULT)
    gen.write_tree(files, tmp_path)
    approvals_path = "runs/c1-r104/approvals.json"
    assert approvals_path in files
    approvals = json.loads(files[approvals_path])
    module = json.loads(files["modules/module-c1-r104.json"])
    report = json.loads(files["fill-reports/fill-report-c1-r104.json"])
    assert validate.validate_doc(approvals, "approvals").ok
    result = validate.validate_doc(report, "fill-report", module=module, approvals=approvals,
                                   model_answer_read=True, base_dir=tmp_path / "fill-reports")
    assert result.ok, (result.fatal, result.rail_errors)
    assert len(result.waived) == 2


def test_check_validates_schema_inputs_without_writing_them(gen, monkeypatch, tmp_path):
    """A broken schema example must fail validation while its bytes remain untouched."""
    files, info = gen.build_all(gen.SEED_DEFAULT)
    generated, inputs = tmp_path / "generated", tmp_path / "inputs"
    gen.write_tree(files, generated)
    for directory in ("examples", "approvals"):
        for source in (FIX / directory).glob("*.json"):
            target = inputs / directory / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    broken = inputs / "examples" / "run.example.json"
    doc = json.loads(broken.read_text(encoding="utf-8"))
    doc["schema_version"] = "invalid"
    broken.write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    before = {p: p.read_bytes() for p in inputs.rglob("*.json")}
    monkeypatch.setattr(gen, "FIXTURES", inputs)
    errors = gen.validate_tree(generated, info["approvals_in_file"])
    assert any("examples/run.example.json" in e for e in errors)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('victim', [
    'modules/module-c1-r101.json', 'references/reference-c1-r101.json',
    'answers/answers-c1-r102.json', 'fill-reports/fill-report-c1-r102.json',
    'runs/c1-r102/run.json', 'runs/c1-r104/approvals.json',
])
def test_check_rejects_invalid_generated_documents_without_drift(gen, monkeypatch, tmp_path, capsys, victim):
    """Ignoring generated-document validator failures must fail this CLI regression.

    Match the corrupted generator output on disk so drift detection cannot mask a
    disabled validation branch. Keep the real schema and rail validator active.
    """
    files, info = gen.build_all(gen.SEED_DEFAULT)
    broken = json.loads(files[victim])
    broken['schema_version'] = 'invalid'
    files[victim] = json.dumps(broken).encode()
    gen.write_tree(files, tmp_path)
    monkeypatch.setattr(gen, 'FIXTURES', tmp_path)
    monkeypatch.setattr(gen, 'build_all', lambda seed: (files, info))
    assert gen.diff_tree(files, tmp_path) == []
    assert gen.main(['--check']) == 1
    output = capsys.readouterr().out
    assert 'invalid  ' + victim in output
    assert '0 drifted path(s)' in output
