# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise CI subprocess failure handling, digest assets and contributor sign-offs."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    spec = importlib.util.spec_from_file_location('packaging_ci', ROOT / 'packaging/ci.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize('blocking', [True, False])
def test_claude_checks_nested_directory_findings(monkeypatch, blocking):
    # Claude may exit zero while reporting a blocking directory error.
    m = module()
    payload = {'success': True, 'directory': {'findings': [{'blocking': blocking}]}}
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(payload), ''))
    assert m.main(['claude']) == (1 if blocking else 0)


@pytest.mark.parametrize('payload', ['{}', 'garbled', '{"success": false}', 'null', '[]'])
def test_claude_fails_closed_on_bad_output(monkeypatch, payload):
    m = module()
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, payload, ''))
    assert m.main(['claude']) == 1


def test_checksums_match_flat_github_release_assets(tmp_path):
    # Absolute paths or basename collisions make release digests unusable.
    m = module()
    (tmp_path / 'b').mkdir()
    (tmp_path / 'b/two.zip').write_bytes(b'two')
    (tmp_path / 'one.zip').write_bytes(b'one')
    assert m.main(['checksums', '--out', str(tmp_path)]) == 0
    import hashlib
    assert (tmp_path / 'SHA256SUMS').read_text(encoding="utf-8").splitlines() == [
        hashlib.sha256(b'one').hexdigest() + '  one.zip',
        hashlib.sha256(b'two').hexdigest() + '  two.zip',
    ]


@pytest.mark.parametrize('signed', [True, False])
def test_dco_requires_each_non_merge_commit_author_signoff(monkeypatch, signed):
    # A PR body signoff or a different person's trailer cannot certify a commit.
    m = module()
    email = '208164922+dorsaltegmentum@users.noreply.github.com'
    record = 'a' * 40 + f'\x1fExample Contributor\x1f{email}\x1fSubject\n\n'
    if signed:
        record += f'Signed-off-by: Example Contributor <{email}>\n'
    record += '\x1e'
    monkeypatch.setenv('PR_BASE', 'b' * 40)
    monkeypatch.setenv('PR_HEAD', 'c' * 40)
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, record, ''))
    assert m.main(['dco']) == (0 if signed else 1)


def test_dco_rejects_absent_range(monkeypatch):
    m = module()
    monkeypatch.delenv('PR_BASE', raising=False)
    monkeypatch.delenv('PR_HEAD', raising=False)
    assert m.main(['dco']) == 1


def test_checksum_refuses_colliding_release_basenames(tmp_path):
    # GitHub flattens uploaded asset names, so colliding names cannot be verified.
    m = module()
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested/same.zip').write_bytes(b'one')
    (tmp_path / 'same.zip').write_bytes(b'two')
    assert m.main(['checksums', '--out', str(tmp_path)]) == 1
    assert not (tmp_path / 'SHA256SUMS').exists()


def test_test_areas_run_in_separate_sessions_and_collect_failures(tmp_path, monkeypatch):
    # A failed area must not hide verification of the remaining skill packages.
    m = module()
    for area in ('first', 'second'):
        folder = tmp_path / 'tests' / area
        folder.mkdir(parents=True)
        (folder / 'test_example.py').write_text('def test_example(): pass\n', encoding="utf-8", newline="\n")
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if len(calls) == 1 else 0)

    monkeypatch.setattr(m.subprocess, 'run', run)
    assert m.main(['area-tests']) == 1
    assert len(calls) == 2
    assert Path(calls[0][-2]).name == 'first'
    assert Path(calls[1][-2]).name == 'second'
