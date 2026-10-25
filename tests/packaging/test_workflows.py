# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep dependency installation and release safety gates executable on fresh runners."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text(encoding="utf-8"))


def runs(job):
    return [(i, s['run']) for i, s in enumerate(job.get('steps', [])) if 'run' in s]


def test_actions_are_pinned_and_permissions_default_to_read():
    # Mutable tags or workflow-wide write access expose contributor workflows.
    for name in ('ci.yml', 'release.yml'):
        data = workflow(name)
        assert data['permissions'] == {'contents': 'read'}
        assert 'concurrency' in data
        for job_name, job in data['jobs'].items():
            if job_name != 'publish':
                assert job.get('permissions', {'contents': 'read'}) == {'contents': 'read'}
            if 'steps' in job:
                assert 1 <= job['timeout-minutes'] <= 60
            uses = [s['uses'] for s in job.get('steps', []) if 'uses' in s]
            if 'uses' in job:
                uses.append(job['uses'])
            for action in uses:
                assert action.startswith('./') or re.fullmatch(r'[^@]+@[0-9a-f]{40}', action), action


def test_ci_has_all_jobs_and_installs_before_python_checks():
    # Bare Python cannot run schema generators or parse workflow YAML.
    jobs = workflow('ci.yml')['jobs']
    assert set(jobs) == {'privacy', 'drift', 'py', 'py-ascii', 'py-os', 'node', 'isolation', 'reuse',
                         'skills', 'validators', 'lexicon', 'dist', 'release-check', 'dco'}
    for name in ('drift', 'release-check', 'py', 'py-ascii', 'py-os'):
        steps = runs(jobs[name])
        install = next(i for i, cmd in steps if 'packaging/ci.py install-ci' in cmd)
        check = next(i for i, cmd in steps if any(s in cmd for s in ('--check', 'pytest', 'check_release.py', 'area-tests')))
        assert install < check, name
    commands = '\n'.join(cmd for _, cmd in runs(jobs['drift']))
    assert 'tools/sync_shared.py --check --strict' in commands
    for tool in ('gen_manifests', 'gen_fixtures', 'gen_config_docs'):
        assert f'tools/{tool}.py --check' in commands
    assert 'tools/build_dist.py --out dist --check' in '\n'.join(c for _, c in runs(jobs['dist']))
    assert 'area-tests' in '\n'.join(c for _, c in runs(jobs['py']))
    assert jobs['py']['strategy']['matrix']['python'] == ['3.11', '3.13', '3.14']
    ascii_step = next(s for s in jobs['py-ascii']['steps'] if 'area-tests' in s.get('run', ''))
    assert ascii_step['env'] == {'LC_ALL': 'C', 'LANG': 'C', 'PYTHONUTF8': '0',
                                 'PYTHONCOERCECLOCALE': '0'}
    later = jobs['py-os']['steps'][-3:]
    assert [s['if'] for s in later] == ['${{ !cancelled() }}', '${{ !cancelled() }}',
                                       "${{ !cancelled() && runner.os == 'macOS' }}"]


def test_pr_jobs_run_public_mode_and_dco_only_for_pull_requests():
    # Contributor DCO trailers and identities must not trigger initial-release rules.
    jobs = workflow('ci.yml')['jobs']
    for name in ('privacy', 'release-check'):
        cmd = '\n'.join(c for _, c in runs(jobs[name]))
        assert '--public-history' in cmd
        assert '--require-private' not in cmd
        assert '--shingles' not in cmd
        assert jobs[name]['env']['LT_REQUIRE_PRIVATE'] == '0'
    dco = jobs['dco']
    assert dco['if'] == "github.event_name == 'pull_request'"
    assert any('packaging/ci.py dco' in c for _, c in runs(dco))
    assert dco['env']['PR_BASE'] == '${{ github.event.pull_request.base.sha }}'
    assert dco['env']['PR_HEAD'] == '${{ github.event.pull_request.head.sha }}'


def test_validators_check_blocking_findings_and_gemini():
    commands = '\n'.join(c for _, c in runs(workflow('ci.yml')['jobs']['validators']))
    assert 'packaging/ci.py claude' in commands
    assert '@google/gemini-cli@0.65.0 extensions validate .' in commands


def test_release_checksums_and_attestation_precede_upload():
    # A release without digest assets or provenance cannot be authenticated.
    jobs = workflow('release.yml')['jobs']
    job = jobs['publish']
    assert job['permissions'] == {'contents': 'write', 'id-token': 'write', 'attestations': 'write'}
    steps = job['steps']
    checksum = next(i for i,s in enumerate(steps) if 'checksums' in s.get('run', ''))
    attest = next(i for i,s in enumerate(steps) if s.get('uses','').startswith('actions/attest-build-provenance@'))
    upload = next(i for i,s in enumerate(steps) if s.get('uses','').startswith('softprops/action-gh-release@'))
    assert checksum < attest < upload
    assert 'dist/SHA256SUMS' in steps[upload]['with']['files']
    assert 'dist/**/*.zip' in steps[attest]['with']['subject-path']
    assert any('--tag' in c and 'check_release.py' in c for _, c in runs(job))
    assert any('release-tag' in c for _, c in runs(job))
