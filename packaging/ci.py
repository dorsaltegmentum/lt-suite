# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run portable CI gates with explicit failures and no account credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def blocking(value):
    """Find blocking findings at any validator nesting depth."""
    if isinstance(value, dict):
        return value.get('blocking') is True or any(blocking(v) for v in value.values())
    return isinstance(value, list) and any(blocking(v) for v in value)


def main(argv=None):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gate', choices=('install-ci', 'area-tests', 'dco', 'claude', 'checksums', 'release-tag'))
    parser.add_argument('--out', type=Path, default=Path('dist'))
    args = parser.parse_args(argv)
    try:
        if args.gate == 'install-ci':
            # Export every transitive dependency and platform marker from the reviewed lock.
            result = subprocess.run(['uv', 'lock', '--check', '--offline'], cwd=ROOT)
            if result.returncode:
                return result.returncode
            with tempfile.TemporaryDirectory(prefix='lt-ci-deps-') as temp:
                requirements = Path(temp) / 'ci-requirements.txt'
                result = subprocess.run(['uv', 'export', '--frozen', '--offline', '--no-dev',
                                         '--only-group', 'ci', '--no-emit-project', '--no-header',
                                         '--output-file', str(requirements)],
                                        encoding='utf-8', cwd=ROOT, capture_output=True, text=True)
                if result.returncode:
                    print(result.stderr, file=sys.stderr)
                    return result.returncode
                return subprocess.run([sys.executable, '-m', 'pip', 'install', '--require-hashes',
                                       '-r', str(requirements)], cwd=ROOT).returncode
        if args.gate == 'area-tests':
            # Skills alter import search paths. Each test area needs a fresh interpreter.
            areas = sorted(p for p in (ROOT / 'tests').iterdir()
                           if p.is_dir() and any(p.glob('test_*.py')))
            if not areas:
                raise ValueError('No test areas found')
            failures = []
            for area in areas:
                result = subprocess.run([sys.executable, '-m', 'pytest', str(area), '-q'], cwd=ROOT)
                print(f'Test area {area.name}: exit {result.returncode}', flush=True)
                if result.returncode:
                    failures.append(area.name)
            if failures:
                print('Failed test areas: ' + ', '.join(failures), file=sys.stderr)
            return 1 if failures else 0
        if args.gate == 'claude':
            executable = shutil.which('npx')
            if not executable:
                raise ValueError('npx is required')
            result = subprocess.run([executable, '-y', '@anthropic-ai/claude-code@2.1.306',
                                     'plugin', 'validate', '.', '--strict', '--json'],
                                    encoding='utf-8', cwd=ROOT, capture_output=True, text=True, timeout=180)
            if result.returncode:
                print(result.stderr, file=sys.stderr)
                return 1
            data = json.loads(result.stdout)
            if data.get('success') is not True or blocking(data):
                raise ValueError('Claude validation reported unsuccessful or blocking findings')
            print('Claude plugin validation passed; no blocking findings')
            return 0
        if args.gate == 'checksums':
            paths = sorted(args.out.rglob('*.zip'), key=lambda p: p.name)
            if not paths:
                raise ValueError('No archives to checksum')
            if len({p.name for p in paths}) != len(paths):
                raise ValueError('Release asset basenames must be unique')
            lines = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name
                     for p in paths]
            (args.out / 'SHA256SUMS').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline="\n")
            return 0
        if args.gate == 'release-tag':
            ref = os.environ.get('GITHUB_REF_NAME', '')
            if not re.fullmatch(r'v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', ref):
                raise ValueError('A semantic version release tag is required')
            result = subprocess.run(['git', 'merge-base', '--is-ancestor', 'HEAD', 'origin/main'], cwd=ROOT)
            if result.returncode:
                raise ValueError('Release tag must point to a commit on origin/main')
            return 0
        base, head = os.environ.get('PR_BASE', ''), os.environ.get('PR_HEAD', '')
        if not all(re.fullmatch(r'[0-9a-f]{40}', ref) for ref in (base, head)):
            raise ValueError('PR_BASE and PR_HEAD must be full commit SHAs')
        result = subprocess.run(['git', 'log', '--no-merges', '--format=%H%x1f%an%x1f%ae%x1f%B%x1e',
                                 f'{base}..{head}'], encoding='utf-8', cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Cannot read pull-request commit range')
        for record in result.stdout.split('\x1e'):
            if not record.strip():
                continue
            sha, name, email, body = record.lstrip('\n').split('\x1f', 3)
            # Require a trailer paragraph and the actual commit author's certificate.
            trailer = body.rstrip().split('\n\n')[-1].splitlines()
            if f'Signed-off-by: {name} <{email}>' not in trailer:
                raise ValueError(f'{sha[:12]} lacks its author DCO sign-off')
        print('Pull-request DCO sign-offs passed')
        return 0
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        print(f'CI gate failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
