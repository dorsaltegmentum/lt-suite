# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep portable UTF-8 files and pipes independent of the Windows code page."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULES = [
    'shared/ltcore/fonts.py',
    'shared/ltcore/raster.py',
    'packaging/ci.py',
    'tools/build_dist.py',
    'tools/install_tests/make_proof_assets.py',
    'skills/lt-suite-setup/scripts/setup.py',
    'skills/lt-module-answer/scripts/reconcile.py',
    'skills/lt-module-fill/scripts/emit_fill.py',
    'skills/mechanistic-diagram/engine/fontset.py',
    'skills/mechanistic-diagram/engine/make_artifact.py',
    'tests/conftest.py',
    'tests/packaging/test_build_dist.py',
    'tests/packaging/test_gen_manifests.py',
    'tests/packaging/test_lt_chrome.py',
    'tests/packaging/test_lt_chrome_launcher.py',
]


@pytest.mark.parametrize('module', MODULES)
def test_utf8_files_and_subprocesses_have_explicit_encoding(module):
    tree = ast.parse((ROOT / module).read_bytes())
    missing = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        keywords = {k.arg: k.value for k in call.keywords}
        text_file = (isinstance(call.func, ast.Attribute)
                     and call.func.attr in ('read_text', 'write_text'))
        # Include injected subprocess runners, which must obey the same contract.
        text_pipe = any(isinstance(keywords.get(k), ast.Constant) and keywords[k].value is True
                        for k in ('text', 'universal_newlines'))
        if (text_file or text_pipe) and 'encoding' not in keywords:
            missing.append(call.lineno)
    assert not missing, f'{module}: implicit locale encoding at lines {missing}'
