# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""List MCP tools only. No test invokes a browser tool or reads a real profile."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCUMENTED_TOOLS = {
    'close_page', 'evaluate_script', 'get_console_message', 'get_css_styles',
    'lighthouse_audit', 'list_console_messages', 'list_pages', 'navigate_page',
    'new_page', 'screencast_start', 'screencast_stop', 'select_page',
    'take_screenshot', 'take_snapshot', 'wait_for',
}
FLAGS = {'--no-category-input':'--categoryInput', '--no-category-network':'--categoryNetwork',
         '--no-category-memory':'--categoryMemory', '--no-category-performance':'--categoryPerformance',
         '--no-category-emulation':'--categoryEmulation', '--redact-network-headers':'--redactNetworkHeaders'}
NETWORK_ERRORS = ('ENOTFOUND','EAI_AGAIN','ENETUNREACH','ECONNREFUSED','ETIMEDOUT','ENETDOWN')


def isolated_env(home):
    env = {k:v for k,v in os.environ.items() if not k.startswith(('LT_SUITE_', 'CHROME_', 'XDG_'))
           and k not in ('APPDATA','LOCALAPPDATA','PLUGIN_DATA')}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home/'config'),
               XDG_CACHE_HOME=str(home/'cache'), APPDATA=str(home/'roaming'), LOCALAPPDATA=str(home/'local'),
               npm_config_cache=os.environ.get('LT_CHROME_TEST_NPM_CACHE', str(home/'npm-cache')))
    return env


def command_for(root=True):
    entry = json.loads((REPO/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']['lt-chrome']
    args = [a.replace('root=${CLAUDE_PLUGIN_ROOT}', 'root='+str(REPO)) if root else a for a in entry['args']]
    return [shutil.which('node'), *args]


@pytest.fixture(scope='module')
def configured_server():
    unavailable = pytest.fail if os.environ.get('CI') else pytest.skip
    executable = shutil.which('npx')
    if executable is None:
        unavailable('not run: npx is unavailable; configured MCP runtime cannot start')
    with tempfile.TemporaryDirectory(prefix='lt-chrome-probe-', dir=os.environ.get('TMPDIR')) as scratch:
        home = Path(scratch)
        env = isolated_env(home)
        pins = json.loads(subprocess.check_output([shutil.which('node'), str(REPO/'shared/lt_chrome/lt-chrome.cjs'), '--print-pins'], encoding='utf-8', env=env, text=True))
        command = [executable, '--no-workspaces', '-y', pins['package'], '--autoConnect', *pins['fixed_args']]
        try:
            result = subprocess.run(command+['--help'], encoding='utf-8', cwd=scratch, env=env, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            unavailable('not run: npx package lookup timed out; network/package availability is unverified')
        if result.returncode and any(code in result.stderr for code in NETWORK_ERRORS):
            unavailable('not run: npx cannot reach the package registry and the pinned package is unavailable')
        assert result.returncode == 0, result.stderr[-1500:]
        yield command, scratch, result.stdout, env


def probe(command, cwd, env):
    """Initialize and list tools, then close the host's stdin and require clean exit."""
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, encoding='utf-8', cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
        lines = queue.Queue()
        def read_lines():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        def send(msg):
            process.stdin.write(json.dumps(msg)+'\n'); process.stdin.flush()
        def response(request_id):
            end = time.monotonic()+60
            while True:
                try:
                    line = lines.get(timeout=max(0,end-time.monotonic()))
                except queue.Empty:
                    stderr.seek(0)
                    pytest.fail('MCP response timed out: '+stderr.read().decode()[-1500:])
                if line is None:
                    stderr.seek(0)
                    pytest.fail('MCP exited before response: '+stderr.read().decode()[-1500:])
                item = json.loads(line)
                if item.get('id') == request_id:
                    assert 'error' not in item, item
                    return item['result']
        try:
            send(dict(jsonrpc='2.0', id=1, method='initialize', params={
                'protocolVersion':'2025-03-26', 'capabilities':{},
                'clientInfo':{'name':'lt-suite-tool-list-test','version':'1.0'}}))
            assert response(1)['protocolVersion']
            send(dict(jsonrpc='2.0', method='notifications/initialized'))
            send(dict(jsonrpc='2.0', id=2, method='tools/list', params={}))
            result = response(2)
            assert not result.get('nextCursor')
            names = {tool['name'] for tool in result['tools']}
            process.stdin.close()
            assert process.wait(timeout=30) == 0
            return names
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            if not process.stdin.closed: process.stdin.close()
            reader.join(timeout=2)
            process.stdout.close()


def test_server_flags_match_pinned_cli_help(configured_server):
    command, _, help_text, _ = configured_server
    for flag, option in FLAGS.items():
        assert flag in command
        assert option in help_text


def test_runtime_tools_list_equals_documented_set(configured_server):
    _, scratch, _, env = configured_server
    assert probe(command_for(), scratch, env) == DOCUMENTED_TOOLS


def test_generated_mcp_json_quiet_end_to_end(tmp_path):
    env = isolated_env(tmp_path)
    config = tmp_path/'quiet.toml'
    config.write_text('[browser]\nadapter="claude-in-chrome"\n', encoding='utf-8', newline="\n")
    env.update(LT_SUITE_CONFIG=str(config), PATH='', npm_config_registry='http://127.0.0.1:9')
    assert probe(command_for(), tmp_path, env) == set()


def test_generated_mcp_json_fallback_end_to_end(configured_server):
    _, scratch, _, env = configured_server
    config = Path(scratch)/'quiet.toml'
    config.write_text('[browser]\nadapter="claude-in-chrome"\n', encoding='utf-8', newline="\n")
    env = dict(env, LT_SUITE_CONFIG=str(config))
    assert probe(command_for(False), scratch, env) == DOCUMENTED_TOOLS
    if os.name == 'posix' and Path('/proc').is_dir():
        run = Path(scratch)/'cache/lt-suite/lt-chrome/run'
        for cwd in Path('/proc').glob('[0-9]*/cwd'):
            try: assert cwd.resolve() != run
            except (OSError, RuntimeError): pass


@pytest.mark.parametrize('root', [True, False])
def test_user_cd4a_config_cannot_widen_tools(configured_server, root):
    _, scratch, _, env = configured_server
    folder = Path(env['XDG_CONFIG_HOME'])/'cd4a'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'config.json').write_text(json.dumps({'categoryExtensions':True,'wsEndpoint':'ws://192.0.2.1:9/x'}), encoding='utf-8', newline="\n")
    env = dict(env, CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY='1')
    assert probe(command_for(root), scratch, env) == DOCUMENTED_TOOLS


def test_probe_fails_in_ci_when_npx_is_missing(monkeypatch):
    monkeypatch.setenv('CI','true')
    monkeypatch.setattr(shutil,'which',lambda name: None)
    with pytest.raises(pytest.fail.Exception, match='npx is unavailable'):
        next(configured_server.__wrapped__())


def test_probe_uses_the_resolved_executable(monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda name:'synthetic-npx.cmd')
    monkeypatch.setattr(subprocess, 'check_output', lambda *a,**kw: json.dumps({'package':'chrome-devtools-mcp@1.12.0','fixed_args':[]}))
    called = []
    def run(command,**kwargs):
        called.append(command)
        return subprocess.CompletedProcess(command,0,'synthetic help','')
    monkeypatch.setattr(subprocess,'run',run)
    fixture = configured_server.__wrapped__()
    try:
        command, _, _, _ = next(fixture)
        assert command[0] == called[0][0] == 'synthetic-npx.cmd'
    finally: fixture.close()


def test_probe_timeout_fails_in_ci(monkeypatch):
    monkeypatch.setenv('CI','true')
    monkeypatch.setattr(shutil,'which',lambda name:'synthetic-npx.cmd')
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**kw: json.dumps({'package':'chrome-devtools-mcp@1.12.0','fixed_args':[]}))
    def timeout(command,**kwargs): raise subprocess.TimeoutExpired(command,kwargs['timeout'])
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(pytest.fail.Exception, match='timed out'):
        next(configured_server.__wrapped__())
