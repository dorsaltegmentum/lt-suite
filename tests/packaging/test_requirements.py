# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reject floating Python requirements and missing dependency update coverage."""

from pathlib import Path
import re
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_standalone_requirements_are_pinned_with_hashes():
    # A clean install must reproduce the reviewed closure instead of fetching newest floors.
    for path in sorted((ROOT / 'skills').glob('*/requirements.txt')):
        text = path.read_text(encoding="utf-8")
        entries = re.split(r'\n(?=[A-Za-z0-9])', text)
        requirements = [e for e in entries if re.match(r'^[A-Za-z0-9]', e)]
        assert requirements, path
        for entry in requirements:
            assert re.match(r'^[A-Za-z0-9_.-]+==[^\s;\\]+', entry), path
            assert re.search(r'--hash=sha256:[0-9a-f]{64}', entry), path


def test_lock_and_updater_cover_runtime_dependencies():
    # An ignored lock and empty root dependencies leave actual skill dependencies unreviewed.
    assert (ROOT / 'uv.lock').is_file()
    ignored = (ROOT / '.gitignore').read_text(encoding="utf-8").splitlines()
    assert 'uv.lock' not in ignored
    config = yaml.safe_load((ROOT / '.github/dependabot.yml').read_text(encoding="utf-8"))
    updates = config['updates']
    assert any(u['package-ecosystem'] == 'uv' and u['directory'] == '/' for u in updates)
    assert any(u['package-ecosystem'] == 'pip' and '/skills/*' in u['directories'] for u in updates)
    assert any(u['package-ecosystem'] == 'npm' and u['directory'] == '/packaging' for u in updates)
    source = tomllib.loads((ROOT / 'packaging/plugin.toml').read_text(encoding="utf-8"))
    assert source['package_integrity']['chrome_devtools_mcp'].startswith('sha512-')


def test_npm_integrity_and_update_metadata_match_manifest_pins():
    # The dependency bot must update the package whose integrity the manifests declare.
    import json
    metadata = json.loads((ROOT / 'packaging/package.json').read_text(encoding="utf-8"))
    lock = json.loads((ROOT / 'packaging/package-lock.json').read_text(encoding="utf-8"))
    source = tomllib.loads((ROOT / 'packaging/plugin.toml').read_text(encoding="utf-8"))
    assert metadata['dependencies'] == {'chrome-devtools-mcp': '1.12.0', '@playwright/mcp': '0.0.83'}
    package = lock['packages']['node_modules/chrome-devtools-mcp']
    assert package['version'] == '1.12.0'
    assert package['integrity'] == source['package_integrity']['chrome_devtools_mcp']


def test_requirement_versions_and_hashes_come_from_the_reviewed_lock():
    # Exact-looking pins must still match the reviewed lock's versions and artifact hashes.
    lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding="utf-8"))
    artifacts = {}
    for package in lock['package']:
        key = (re.sub(r'[-_.]+', '-', package['name']).lower(), package.get('version'))
        rows = package.get('wheels', []) + ([package['sdist']] if 'sdist' in package else [])
        artifacts[key] = {row['hash'].removeprefix('sha256:') for row in rows}
    for path in sorted((ROOT / 'skills').glob('*/requirements.txt')):
        for line in path.read_text(encoding="utf-8").replace('\\\n', ' ').splitlines():
            match = re.match(r'^([A-Za-z0-9_.-]+)==([^\s;]+)', line)
            if not match:
                continue
            key = (re.sub(r'[-_.]+', '-', match[1]).lower(), match[2])
            hashes = set(re.findall(r'--hash=sha256:([0-9a-f]{64})', line))
            assert key in artifacts and hashes and hashes <= artifacts[key], path
