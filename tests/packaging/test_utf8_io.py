# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard every Python file against locale-dependent files, pipes and writers."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def python_sources():
    roots = [ROOT / name for name in ('shared', 'tools', 'packaging', 'tests')]
    roots += list((ROOT / 'skills').glob('*/scripts'))
    roots.append(ROOT / 'skills/mechanistic-diagram/engine')
    return sorted({p for root in roots for p in root.rglob('*.py')})


def io_errors(source):
    errors = []
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update((a.asname or a.name, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            aliases.update((a.asname or a.name, (node.module or '') + '.' + a.name) for a in node.names)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        keywords = {k.arg: k.value for k in call.keywords}
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, 'id', '')
        receiver = ast.unparse(call.func.value) if isinstance(call.func, ast.Attribute) else ''
        # These APIs open binary media, archives, sockets or integer descriptors.
        binary_apis = ('Image', 'PILImage', 'image', 'pikepdf', 'mupdf', 'fitz',
                       'tarfile', 'webbrowser', 'opener')
        text_file = name in ('read_text', 'write_text')
        file_open = (name in ('open', 'fdopen', 'NamedTemporaryFile', 'TemporaryFile') and receiver not in binary_apis
                     and not (name == 'open' and receiver == 'os')
                     and not receiver.startswith("__import__('PIL.Image'"))
        mode = keywords.get('mode')
        if mode is None and file_open:
            index = 0 if name in ('NamedTemporaryFile', 'TemporaryFile') or receiver and receiver not in ('io', 'os', 'builtins', 'gzip') else 1
            mode = call.args[index] if len(call.args) > index else ast.Constant(
                'w+b' if name in ('NamedTemporaryFile', 'TemporaryFile') else 'r')
        def binary_mode(value):
            if isinstance(value, ast.IfExp):
                return binary_mode(value.body) and binary_mode(value.orelse)
            return isinstance(value, ast.Constant) and isinstance(value.value, str) and 'b' in value.value
        binary = binary_mode(mode)
        if file_open and not binary:
            text_file = True
        imported = aliases.get(receiver, receiver) + '.' + name if receiver else aliases.get(name, name)
        pipe_call = (imported.startswith('subprocess.') or
                     name in ('run', 'Popen', 'check_output', 'check_call', 'call', 'runner', '_run'))
        text_pipe = pipe_call and any(k in keywords and not (isinstance(keywords[k], ast.Constant)
                        and keywords[k].value in (False, None)) for k in ('text', 'universal_newlines'))
        encoding = keywords.get('encoding')
        def utf8(value):
            return isinstance(value, ast.Constant) and value.value in ('utf-8', 'utf8')
        # privacy_gate.git decodes text as UTF-8 and leaves binary pipes alone.
        explicit_encoding = utf8(encoding) or (text_pipe and isinstance(encoding, ast.IfExp)
                            and utf8(encoding.body) and isinstance(encoding.orelse, ast.Constant)
                            and encoding.orelse.value is None)
        if (text_file or text_pipe) and not explicit_encoding:
            errors.append((call.lineno, 'encoding'))
        def write_mode(value):
            if isinstance(value, ast.IfExp):
                return write_mode(value.body) or write_mode(value.orelse)
            return isinstance(value, ast.Constant) and isinstance(value.value, str) and any(c in value.value for c in 'wax+')
        writer = name == 'write_text' or file_open and not binary and write_mode(mode)
        # Requiring LF for all text writers covers future deterministic callers.
        newline = keywords.get('newline')
        if writer and not (isinstance(newline, ast.Constant) and newline.value == '\n'):
            errors.append((call.lineno, 'newline'))
    return errors


def test_all_python_files_and_pipes_use_utf8_and_lf():
    missing = []
    for path in python_sources():
        missing.extend(f'{path.relative_to(ROOT)}:{line}: missing {kind}'
                       for line, kind in io_errors(path.read_text(encoding='utf-8')))
    assert not missing, '\n' + '\n'.join(missing)


@pytest.mark.parametrize('source,expected', [
    ("open('a')", [(1, 'encoding')]),
    ("open('a', encoding=None)", [(1, 'encoding')]),
    ("p.read_text(encoding='locale')", [(1, 'encoding')]),
    ("Path('a').open('w')", [(1, 'encoding'), (1, 'newline')]),
    ("os.fdopen(fd, 'w', encoding='utf-8')", [(1, 'newline')]),
    ("p.write_text('x', encoding='utf-8')", [(1, 'newline')]),
    ("p.write_text('x', encoding='utf-8', newline=None)", [(1, 'newline')]),
    ("p.read_text()", [(1, 'encoding')]),
    ("subprocess.run(cmd, text=True)", [(1, 'encoding')]),
    ("from subprocess import run as execute\nexecute(cmd, text=True)", [(2, 'encoding')]),
    ("run(cmd, text=not binary)", [(1, 'encoding')]),
    ("open('a', 'rb')", []),
    ("Image.open(p)", []),
    ("dict(text='Unicode data')", []),
    ("tempfile.NamedTemporaryFile(mode='w')", [(1, 'encoding'), (1, 'newline')]),
    ("tempfile.NamedTemporaryFile('w')", [(1, 'encoding'), (1, 'newline')]),
    ("p.write_text('x', encoding='utf-8', newline='\\n')", []),
    ("subprocess.run(cmd, encoding='utf-8')", []),
])
def test_guard_detects_missing_contracts(source, expected):
    assert io_errors(source) == expected


@pytest.mark.parametrize('entry', ['config', 'setup', 'raster', 'ltjs'])
def test_cli_utf8_streams_ignore_cp1252(entry, tmp_path):
    import json
    import os
    import subprocess
    import sys
    import tomllib
    env = dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0',
               PYTHONPATH=str(ROOT / 'shared'))
    word = 'caf\u00e9-\u6f22\u5b57'
    data = json.dumps({'institution': {'name': word}}, ensure_ascii=False).encode('utf-8')
    if entry == 'config':
        args = ['-m', 'ltcore.config', 'write', '--from', '-', '--stdout']
    elif entry == 'setup':
        args = [str(ROOT / 'skills/lt-suite-setup/scripts/setup.py'), 'write',
                '--answers', '-', '--stdout']
    elif entry == 'raster':
        args = ['-m', 'ltcore.raster', '--help']
    else:
        args = ['-c', "from ltcore.ltjs import main; main(['check-url', " + ascii(word) + "])"]
    result = subprocess.run([sys.executable, *args], input=data, capture_output=True,
                            cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    text = result.stdout.decode('utf-8')
    assert b'\r\n' not in result.stdout
    if entry in ('setup', 'config'):
        assert tomllib.loads(text)['institution']['name'] == word
    elif entry == 'ltjs':
        assert json.loads(text)['url'] == word
    else:
        assert '\u00b5' in text


@pytest.mark.parametrize('entry', ['config', 'setup'])
def test_cli_stdin_decodes_utf8_before_reading(entry, tmp_path):
    import json
    import os
    import subprocess
    import sys
    word = 'caf\u00e9-\u6f22\u5b57'
    if entry == 'config':
        args = ['-m', 'ltcore.config', 'write', '--from', '-', '--stdout']
    else:
        args = [str(ROOT / 'skills/lt-suite-setup/scripts/setup.py'), 'write',
                '--answers', '-', '--stdout']
    result = subprocess.run([sys.executable, *args], capture_output=True,
                            input=json.dumps({'institution': {'name': word}}, ensure_ascii=False).encode('utf-8'),
                            env=dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0',
                                     PYTHONPATH=str(ROOT / 'shared')), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    import tomllib
    assert tomllib.loads(result.stdout.decode('utf-8'))['institution']['name'] == word
