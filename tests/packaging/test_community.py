# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep the release's legal identity, licence copies and reporting scope consistent."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_license_copy_and_reporting_routes():
    # A changed licence copy or absent maintainer contact invalidates standalone delivery.
    assert (ROOT / 'LICENSE').read_bytes() == (ROOT / 'LICENSES/AGPL-3.0-or-later.txt').read_bytes()
    conduct = (ROOT / 'CODE_OF_CONDUCT.md').read_text(encoding="utf-8")
    assert '/security/advisories/new' in conduct
    assert 'dorsaltegmentum' in conduct
    assert 'CC BY 4.0' in conduct
    security = (ROOT / 'SECURITY.md').read_text(encoding="utf-8")
    for term in ('js_tmp_dir', 'Working-directory', 'Case-folder', 'cleaner', 'serve endpoint'):
        assert term in security
    assert 'releases/tag/v0.1.0' in (ROOT / 'CHANGELOG.md').read_text(encoding="utf-8")
