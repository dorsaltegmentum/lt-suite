# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Library imports preserve host streams; CLI setup tolerates unusable streams."""
import pytest


@pytest.mark.parametrize("module", ["ltcore", "_bootstrap", "setup"])
@pytest.mark.parametrize("state", ["open", "closed", "detached", "read", "none"])
def test_library_import_preserves_host_streams(run_py, repo_root, module, state):
    engine = repo_root / "skills/mechanistic-diagram/engine"
    setup = repo_root / "skills/lt-suite-setup/scripts/setup.py"
    code = f'''
import io, os, sys, importlib.util
sys.path.insert(0, {str(engine)!r})
stream = io.TextIOWrapper(io.BytesIO(b'caf\\xe9'), encoding='cp1252')
if {state!r} == 'closed': stream.close()
if {state!r} == 'detached': stream.detach()
if {state!r} == 'read': stream.read(1)
if {state!r} == 'none': stream = None
original = (sys.stdin, sys.stdout, sys.stderr)
before = os.environ.get('PYTHONIOENCODING')
sys.stdin = sys.stdout = sys.stderr = stream
try:
    if {module!r} == 'setup':
        spec = importlib.util.spec_from_file_location('setup', {str(setup)!r})
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    else:
        __import__({module!r})
    assert sys.stdin is stream and sys.stdout is stream and sys.stderr is stream
    if stream is not None: assert stream.encoding == 'cp1252'
    assert os.environ.get('PYTHONIOENCODING') == before
finally:
    sys.stdin, sys.stdout, sys.stderr = original
'''
    result = run_py('-c', code, env={'PYTHONIOENCODING': 'cp1252'})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("state", ["closed", "detached", "read", "none"])
def test_cli_setup_skips_unusable_streams(run_py, state):
    result = run_py('-c', f'''
import io, sys
from ltcore.streams import configure_utf8
stream = io.TextIOWrapper(io.BytesIO(b'caf\\xe9'), encoding='cp1252')
if {state!r} == 'closed': stream.close()
if {state!r} == 'detached': stream.detach()
if {state!r} == 'read': stream.read(1)
if {state!r} == 'none': stream = None
original = (sys.stdin, sys.stdout, sys.stderr)
sys.stdin = sys.stdout = sys.stderr = stream
try:
    configure_utf8()
finally:
    sys.stdin, sys.stdout, sys.stderr = original
''')
    assert result.returncode == 0, result.stderr
