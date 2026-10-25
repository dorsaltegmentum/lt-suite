# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic launcher contracts. Never open a browser or an owner's profile."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'shared/lt_chrome/lt-chrome.cjs'
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='Node unavailable')
ARGS = ['-y', 'chrome-devtools-mcp@1.12.0', '--autoConnect',
        '--no-usage-statistics', '--no-performance-crux', '--no-category-input',
        '--no-category-network', '--no-category-memory', '--no-category-performance',
        '--no-category-emulation', '--redact-network-headers']
KEYS = ('adapter', 'chromium', 'chromium_user_data_dir', 'devtools_url')


@pytest.fixture
def sandbox(tmp_path):
    # Every discovery root is temporary; subprocesses inherit no real config locations.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LT_SUITE_', 'XDG_', 'CHROME_'))
           and k not in ('APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'PLUGIN_DATA')}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               APPDATA=str(tmp_path / 'roaming'), LOCALAPPDATA=str(tmp_path / 'local'),
               XDG_CONFIG_HOME=str(tmp_path / 'config'), XDG_CACHE_HOME=str(tmp_path / 'cache'))
    return tmp_path, env


def invoke(sandbox, *args, text=None):
    home, env = sandbox
    return subprocess.run([NODE, str(LAUNCHER), *args], cwd=home, env=env,
                          input=text, capture_output=True, text=True, timeout=15)


def plan(sandbox, browser=None, *args, raw=None):
    home, env = sandbox
    if browser is not None or raw is not None:
        f = home / 'chosen.toml'
        f.write_text(raw if raw is not None else '[browser]\n' + ''.join(
            f'{k} = {json.dumps(v, ensure_ascii=False)}\n' for k, v in browser.items()), encoding='utf-8')
        env['LT_SUITE_CONFIG'] = str(f)
    result = invoke(sandbox, '--plan', *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_config_argv_matches_0_1_0(sandbox):
    # Starting without config must preserve the pinned server's legacy flags.
    p = plan(sandbox)
    assert p['action'] == 'start'
    assert p['argv'][-12:] == ['--no-workspaces', *ARGS]
    assert p['shell'] is False


def test_builtin_defaults_match_defaults_toml(sandbox):
    p = plan(sandbox)
    defaults = tomllib.loads((ROOT / 'shared/config.defaults.toml').read_text())['browser']
    assert p['browser'] == {k: defaults[k] for k in KEYS}


@pytest.mark.parametrize('adapter', ['auto', 'chrome-devtools-mcp', 'antigravity'])
def test_start_adapters_keep_server(sandbox, adapter):
    assert plan(sandbox, {'adapter': adapter})['argv'][-12:] == ['--no-workspaces', *ARGS]


def test_allowed_url_patterns_stay_unwired(sandbox):
    assert plan(sandbox, raw='[lt]\nallowed_url_patterns=["https://example.lt.app/*"]\n')['argv'][-12:] == ['--no-workspaces', *ARGS]


@pytest.mark.parametrize('adapter', ['claude-in-chrome', 'playwright-mcp', 'manual-paste'])
def test_quiet_adapters_serve_zero_tools(sandbox, adapter):
    plan(sandbox, {'adapter': adapter})
    sandbox[1]['PATH'] = ''
    hook = sandbox[0] / 'quiet-hook.cjs'
    hook.write_text("const fs=require('fs'),exists=fs.existsSync;fs.existsSync=function(p){return String(p).endsWith('npx-cli.js')?false:exists(p)};require('child_process').spawn=function(){throw Error('quiet adapter spawned a child')}")
    sandbox[1]['NODE_OPTIONS'] = '--require=' + str(hook)
    messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion': '2025-03-26'}),
                dict(jsonrpc='2.0', method='notifications/initialized'),
                dict(jsonrpc='2.0', id=2, method='tools/list')]
    result = invoke(sandbox, text=''.join(json.dumps(m) + '\n' for m in messages))
    assert result.returncode == 0, result.stderr
    replies = [json.loads(s) for s in result.stdout.splitlines()]
    assert len(replies) == 2
    assert replies[1]['result'] == {'tools': []}
    assert 'instructions' not in replies[0]['result']
    assert result.stderr == ''


def test_zero_tool_server_protocol(sandbox):
    plan(sandbox, {'adapter': 'manual-paste'})
    versions = ['2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25', '2099-01-01']
    requests = [dict(jsonrpc='2.0', id=i, method='initialize', params={'protocolVersion': v})
                for i, v in enumerate(versions)]
    requests += [dict(jsonrpc='2.0', id=5, method='ping'), dict(jsonrpc='2.0', id=6, method='unknown'),
                 dict(jsonrpc='2.0', method='unknown')]
    r = invoke(sandbox, text=''.join(json.dumps(x)+'\n' for x in requests))
    replies = [json.loads(s) for s in r.stdout.splitlines()]
    assert len(replies) == 7
    assert [x['result']['protocolVersion'] for x in replies[:5]] == versions[:4] + ['2025-11-25']
    assert replies[5]['result'] == {}
    assert replies[6]['error']['code'] == -32601


def test_workspace_cannot_start_when_platform_quiet(sandbox):
    home, env = sandbox
    cfg = Path(env['XDG_CONFIG_HOME']) / 'lt-suite/config.toml'
    if sys.platform == 'darwin':
        cfg = home / 'Library/Application Support/lt-suite/config.toml'
    elif sys.platform == 'win32':
        cfg = Path(env['APPDATA']) / 'lt-suite/config.toml'
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[browser]\nadapter="claude-in-chrome"\n')
    (home / 'lt-suite.toml').write_text('[browser]\nadapter="auto"\n')
    p = plan(sandbox)
    assert p['action'] == 'serve-none'
    assert 'platform config chose' in p['reason']
    assert plan(sandbox, {'adapter': 'auto'})['action'] == 'start'
    cfg.write_text('[browser]\nadapter="auto"\n')
    env.pop('LT_SUITE_CONFIG')
    (home / 'lt-suite.toml').write_text('[browser]\nadapter="manual-paste"\n')
    assert plan(sandbox)['action'] == 'serve-none'


@pytest.mark.parametrize('url', ['http://127.0.0.1:9222', 'http://[::1]:9222/', 'http://127.0.0.1:80'])
def test_loopback_urls_accepted(sandbox, url):
    p = plan(sandbox, {'devtools_url': url, 'chromium': 'chrome-beta', 'chromium_user_data_dir': 'unused'})
    assert '--browserUrl=' + url in p['argv']
    import hashlib
    assert p['devtools_url_sha256'] == hashlib.sha256(url.encode('utf-8')).hexdigest()
    assert not any(x.startswith(('--autoConnect', '--channel', '--userDataDir')) for x in p['argv'])


BAD_URLS = ['http://localhost:9222', 'http://0.0.0.0:9222', 'http://127.0.0.2:9222',
            'http://192.168.1.5:9222', 'http://2130706433:9222', 'http://127.0.0.1.nip.io:9222',
            'http://user:pw@127.0.0.1:9222', 'http://127.0.0.1:9222@evil.example',
            'https://127.0.0.1:9222', 'ws://127.0.0.1:9222', 'wss://127.0.0.1:9222',
            'http://127.0.0.1:0', 'http://127.0.0.1:70000', 'http://127.0.0.1:9222\n',
            'http://127.0.\t0.1:9222']


def config_core():
    sys.path.insert(0, str(ROOT / 'shared'))
    from ltcore import config
    return config


@pytest.mark.parametrize('url', BAD_URLS)
def test_non_loopback_urls_refused(sandbox, url):
    p = plan(sandbox, {'devtools_url': url})
    assert p['action'] == 'refuse'
    assert 'browser.devtools_url' in p['reason']
    core = config_core()
    doc = core.defaults()[0]
    doc['browser']['devtools_url'] = url
    errors, _ = core.check(doc)
    assert any('browser.devtools_url' in e for e in errors)


@pytest.mark.parametrize('char', ['"', '%', '!', '\n', '\x00'])
def test_user_data_dir_characters_refused(sandbox, char):
    p = plan(sandbox, {'chromium_user_data_dir': 'folder' + char})
    assert p['action'] == 'refuse'
    core = config_core()
    doc = core.defaults()[0]
    doc['browser']['chromium_user_data_dir'] = 'folder' + char
    assert any('browser.chromium_user_data_dir' in e for e in core.check(doc)[0])


def test_other_without_folder_refuses(sandbox):
    p = plan(sandbox, {'chromium': 'other'})
    assert p['action'] == 'refuse'
    assert 'browser.chromium_user_data_dir' in p['reason']


def test_user_data_dir_overrides_and_resolves(sandbox):
    home, _ = sandbox
    for folder, expected in [('relative', home / 'relative'), ('~/profile', home / 'profile')]:
        p = plan(sandbox, {'chromium': 'chrome-canary', 'chromium_user_data_dir': folder})
        assert '--userDataDir=' + str(expected) in p['argv']
        assert not any(a.startswith('--channel') for a in p['argv'])


def test_values_stay_single_argv_elements(sandbox):
    p = plan(sandbox, {'chromium_user_data_dir': '--isolated x'})
    assert '--userDataDir=' + str(sandbox[0] / '--isolated x') in p['argv']
    assert p['shell'] is False


@pytest.mark.parametrize('version,start', [('19.9.0',False), ('20.18.0',False), ('22.11.0',False),
    ('20.19.0',True), ('21.7.3',True), ('22.12.0',True), ('23.0.0',True), ('26.12.1',True)])
def test_node_gate(sandbox, version, start):
    assert (plan(sandbox, None, '--node-version', version)['action'] == 'start') == start


def test_secret_values_never_echoed(sandbox):
    p = plan(sandbox, {'devtools_url': 'http://u:synthetic-password@10.0.0.1:9222'}, '--redact')
    r = invoke(sandbox, text='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
    output = json.dumps(p) + r.stdout + r.stderr
    output += ''.join(f.read_text() for f in sandbox[0].rglob('last-start-*.json'))
    core = config_core()
    doc = core.defaults()[0]
    doc['browser']['devtools_url'] = 'http://u:synthetic-password@10.0.0.1:9222'
    output += str(core.check(doc))
    assert 'synthetic-password' not in output and '10.0.0.1' not in output
    assert 'value hidden' in str(core.check(doc))


def test_placeholder_values_ignored(sandbox):
    for value in ('$LT_SUITE_CONFIG', '${LT_SUITE_CONFIG}'):
        sandbox[1]['LT_SUITE_CONFIG'] = value
        assert plan(sandbox, None, 'workspace=${Y}')['config_source'] == 'none'


def test_config_lookup_matches_ltcore(sandbox):
    home, env = sandbox
    missing = home / 'missing.toml'
    env['LT_SUITE_CONFIG'] = str(missing)
    p = plan(sandbox)
    assert p['action'] == 'refuse' and p['config'] == str(missing)
    env.pop('LT_SUITE_CONFIG')
    ws = home / 'lt-suite.toml'
    ws.write_text('[browser]\nadapter="auto"\n')
    assert plan(sandbox)['config'] == str(ws)


@pytest.mark.parametrize('raw', [
    'browser = {}', 'browser.adapter = "manual-paste"', '\ufeff[browser]\r\nadapter="auto" # hello\r\n',
    "[browser]\nadapter = 'auto'\nchromium_user_data_dir = 'café'\n",
    '[other]\nx="""\n[browser]\nadapter="manual-paste"\n"""\n[browser]\nadapter="auto"',
    '[[other]]\nx={a=[1,2],b="[browser]"}\n[browser]\nadapter="auto"',
    '[[other]]\n[other.child]\nx=1\n[[other]]\n[other.child]\nx=2\n[browser]\nadapter="manual-paste"',
])
def test_toml_reader_supported_corpus(sandbox, raw):
    p = plan(sandbox, raw=raw)
    assert p['action'] != 'refuse', p['reason']
    expected = tomllib.loads(raw.lstrip('\ufeff')).get('browser', {})
    defaults = tomllib.loads((ROOT / 'shared/config.defaults.toml').read_text())['browser']
    assert p['browser'] == {k: expected.get(k, defaults[k]) for k in KEYS}


@pytest.mark.parametrize('raw', ['browser={adapter="auto"}', '[browser]\nadapter="auto"\nadapter="auto"',
    '[browser]\nadapter=3', '[browser]\nadapter="unterminated'])
def test_toml_reader_refuses_only_listed_forms(sandbox, raw):
    p = plan(sandbox, raw=raw)
    assert p['action'] == 'refuse'
    assert 'line ' in p['reason']


def test_npx_runs_from_neutral_folder(sandbox):
    p = plan(sandbox)
    assert Path(p['cwd']).name == 'run' and Path(p['cwd']).parent.name == 'lt-chrome'
    assert json.loads((Path(p['cwd']) / 'package.json').read_text()) == {}
    if os.name != 'nt':
        assert Path(p['cwd']).stat().st_mode & 0o777 == 0o700


def test_child_env_hardening(sandbox):
    env = sandbox[1]
    env.update(PLUGIN_DATA='ignored', CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY='1',
               CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS='1', CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY='')
    p = plan(sandbox)
    assert p['env_set'] == {'CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY':'1', 'CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS':'1'}
    assert set(p['env_removed']) >= {'PLUGIN_DATA', 'CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY',
                                  'CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS'}


def test_status_file_fields(sandbox):
    plan(sandbox, {'adapter': 'manual-paste'})
    assert invoke(sandbox, 'via=../x', text='').returncode == 0
    files = list(sandbox[0].rglob('last-start-unknown.json'))
    assert len(files) == 1
    assert set(json.loads(files[0].read_text())) == {'time','lt_suite_version','via','config','config_source',
        'adapter','action','chromium','route','reason'}


def bootstrap(sandbox, tags=(), *, event='spawn', code=0, windows=False, hide_npx=False):
    home, env = sandbox
    hook = home / 'spawn-hook.cjs'
    hook.write_text('''const fs=require('fs'), cp=require('child_process'), E=require('events');
if(process.env.STUB_WINDOWS==='1') Object.defineProperty(process,'platform',{value:'win32'});
const exists=fs.existsSync;
fs.existsSync=function(f){if(process.env.HIDE_NPX==='1' && String(f).endsWith('npx-cli.js'))return false;return exists(f)};
cp.spawn=function(command,args,opts){
 if(!Array.isArray(args)){opts=args;args=[]}
 const child=new E();child.kill=function(){};
 const env=Object.fromEntries(Object.entries(opts.env).filter(([k])=>/^CHROME_DEVTOOLS_MCP_/i.test(k)||/^PLUGIN_DATA$/i.test(k)));
 process.stdout.write(JSON.stringify({command,args,cwd:opts.cwd,shell:opts.shell,env})+'\\n');
 setImmediate(function(){child.emit(process.env.STUB_EVENT, new Error('synthetic failure'));setImmediate(function(){child.emit('exit',Number(process.env.STUB_CODE))})});return child;
};''')
    env = dict(env, STUB_EVENT=event, STUB_CODE=str(code), STUB_WINDOWS=str(int(windows)), HIDE_NPX=str(int(hide_npx)))
    generated = json.loads((ROOT / '.mcp.json').read_text())['mcpServers']['lt-chrome']
    return subprocess.run([NODE, '--require', str(hook), '-e', generated['args'][1], '--', *tags],
                          cwd=home, env=env, capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize('tags', [[], ['root=.'], ['root=${CLAUDE_PLUGIN_ROOT}']])
def test_bootstrap_without_root_runs_fallback(sandbox, tags):
    home, env = sandbox
    root = home / 'plant'
    launcher = root / 'skills/lt-suite-setup/scripts/_shared/lt_chrome/lt-chrome.cjs'
    launcher.parent.mkdir(parents=True)
    launcher.write_text("throw new Error('planted launcher ran')")
    env.update(CLAUDE_PLUGIN_ROOT=str(root), PLUGIN_ROOT=str(root),
               CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY='1', PLUGIN_DATA='ignored')
    r = bootstrap(sandbox, [*tags, 'via=synthetic'])
    assert r.returncode == 0, r.stderr
    captured = json.loads(r.stdout)
    assert captured['args'][-12:] == ['--no-workspaces', *ARGS]
    assert captured['shell'] is False
    assert captured['env'] == {'CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY':'1', 'CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS':'1'}
    assert len(r.stderr.splitlines()) == 1
    assert json.loads((Path(captured['cwd']) / 'package.json').read_text()) == {}
    records = list(home.rglob('last-fallback-synthetic.json'))
    assert len(records) == 1
    assert set(json.loads(records[0].read_text())) == {'time','lt_suite_version','via','action','reason'}


def test_bootstrap_fallback_argv_matches_0_1_0(sandbox):
    plan(sandbox, {'adapter':'claude-in-chrome', 'chromium':'brave', 'devtools_url':'refused'})
    r = bootstrap(sandbox)
    assert json.loads(r.stdout)['args'][-12:] == ['--no-workspaces', *ARGS]


def test_bootstrap_failed_spawn_writes_no_record(sandbox):
    r = bootstrap(sandbox, ['via=failure'], event='error')
    assert r.returncode == 1
    assert not list(sandbox[0].rglob('last-fallback-*.json'))


def test_bootstrap_fallback_unwritable_cache(sandbox):
    home, env = sandbox
    blocked = home / 'blocked'
    blocked.write_text('file blocks folder creation')
    env.update(HOME=str(blocked), USERPROFILE=str(blocked), XDG_CACHE_HOME=str(blocked), LOCALAPPDATA=str(blocked))
    r = bootstrap(sandbox, code=7)
    assert r.returncode == 7
    assert json.loads(r.stdout)['cwd'] == str(home)
    assert str(blocked) in r.stderr
    assert not list(home.rglob('last-fallback-*.json'))


def test_bootstrap_root_order(sandbox):
    home, env = sandbox
    roots = []
    for name in ('one', 'two'):
        root = home / name
        target = root / 'skills/lt-suite-setup/scripts/_shared/lt_chrome/lt-chrome.cjs'
        target.parent.mkdir(parents=True)
        target.write_text('exports.main=function(tags){process.stdout.write('+json.dumps(name)+')}')
        roots.append(str(root))
    generated = json.loads((ROOT / '.mcp.json').read_text())['mcpServers']['lt-chrome']
    r = subprocess.run([NODE, '-e', generated['args'][1], '--', 'root='+str(home/'missing'),
                        *['root='+r for r in roots]], env=env, cwd=home, capture_output=True, text=True)
    assert r.stdout == 'one' and r.returncode == 0


def test_bootstrap_text_is_cmd_safe():
    text = json.loads((ROOT / '.mcp.json').read_text())['mcpServers']['lt-chrome']['args'][1]
    assert len(text.encode()) < 4000
    assert not any(c in text for c in '"&|<>^%!\n\r')
    for name in ('mcp.json','mcp_config.json','gemini-extension.json'):
        assert json.loads((ROOT / name).read_text())['mcpServers']['lt-chrome']['args'][1] == text


def test_bootstrap_windows_without_npx_cli_uses_cmd(sandbox):
    sandbox[1].update(chrome_devtools_mcp_category_experimental_third_party='1', Plugin_Data='ignored')
    r = bootstrap(sandbox, windows=True, hide_npx=True, code=7)
    assert r.returncode == 7
    p = json.loads(r.stdout)
    assert p['command'] == 'npx ' + ' '.join(['--no-workspaces', *ARGS])
    assert p['shell'] is True and p['args'] == []
    assert p['env'] == {'CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY':'1', 'CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS':'1'}
    assert len(r.stderr.splitlines()) == 1 and 'DEP0190' not in r.stderr


@pytest.mark.parametrize('platform', ['linux','darwin','win32'])
@pytest.mark.parametrize('xdg', ['', 'relative', 'absolute'])
def test_platform_dirs_mirror_python(sandbox, platform, xdg):
    from ltcore import paths
    home, _ = sandbox
    h = str(home / 'synthetic')
    env = {'HOME': h}
    if xdg:
        env.update(XDG_CONFIG_HOME=(str(home / 'alternate/config') if xdg=='absolute' else 'relative'),
                   XDG_CACHE_HOME=(str(home / 'alternate/cache') if xdg=='absolute' else 'relative'))
    p = plan(sandbox, None, '--platform', platform, '--env-json', json.dumps(env), '--home', h)
    assert p['config_dir'] == str(paths.config_dir(platform, env, h))
    assert p['cache_dir'] == str(paths.cache_dir(platform, env, h))


@pytest.fixture
def setup_module(sandbox, monkeypatch):
    scripts = ROOT / 'skills/lt-suite-setup/scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('ltchrome_setup', scripts / 'setup.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.chdir(sandbox[0])
    for k in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA','XDG_CONFIG_HOME','XDG_CACHE_HOME'):
        monkeypatch.setenv(k, sandbox[1][k])
    monkeypatch.delenv('CHROME_CONFIG_HOME', raising=False)
    return mod


def test_lt_chrome_snippet_and_install(sandbox, setup_module, capsys):
    setup = setup_module
    assert setup.main(['lt-chrome','snippet','--host','codex']) == 0
    cfg = tomllib.loads(capsys.readouterr().out)['mcp_servers']['lt-chrome']
    assert cfg['command'] == 'node'
    assert cfg['startup_timeout_sec'] == 30
    assert cfg['default_tools_approval_mode'] == 'prompt'
    assert cfg['enabled_tools'] == ['list_pages','select_page','navigate_page','evaluate_script','take_screenshot','wait_for']
    assert cfg['env_vars'] == ['LT_SUITE_CONFIG','XDG_CONFIG_HOME','XDG_CACHE_HOME','APPDATA','LOCALAPPDATA']
    assert setup.main(['lt-chrome','install']) == 0
    target = Path(capsys.readouterr().out.strip())
    assert target.read_bytes() == setup.chrome_launcher().read_bytes()
    if os.name != 'nt':
        assert target.stat().st_mode & 0o777 == 0o644
    target.unlink()
    target.symlink_to(sandbox[0] / 'absent')
    assert setup.main(['lt-chrome','install']) == 1
    assert target.is_symlink()


@pytest.mark.parametrize('adapter', ['claude-in-chrome','manual-paste','playwright-mcp'])
def test_doctor_quiet_browser_lines(sandbox, setup_module, monkeypatch, adapter):
    setup = setup_module
    plan(sandbox, {'adapter':adapter, 'chromium':'brave'})
    monkeypatch.setenv('LT_SUITE_CONFIG', sandbox[1]['LT_SUITE_CONFIG'])
    cfg = config_core().load()
    calls = []
    original = setup.run
    def checked(argv, **kw):
        calls.append(argv)
        assert not any('npx' in str(x) for x in argv)
        return original(argv, **kw)
    monkeypatch.setattr(setup,'run',checked)
    results = setup.check_browser({'cfg':cfg})
    assert not any(r.status=='FAIL' for r in results)
    assert any('lt-chrome serves no tools' in r.text for r in results)
    assert not any('DevToolsActivePort' in r.text for r in results)


@pytest.mark.parametrize('browser,content,expected', [
    ('other',None,'no DevToolsActivePort'), ('chrome','9222\n/devtools/browser/abcd-123\n','DevToolsActivePort found, port 9222'),
    ('chrome','9222\n@example.invalid/x\n','unexpected second line'),
    ('other','9222\n/devtools/browser/abcd-123\n','DevToolsActivePort found, port 9222')])
def test_doctor_browser_lines(sandbox, setup_module, monkeypatch, browser, content, expected):
    folder = sandbox[0] / 'profile'
    folder.mkdir()
    if content: (folder/'DevToolsActivePort').write_text(content)
    plan(sandbox, {'adapter':'auto','chromium':browser,'chromium_user_data_dir':str(folder)})
    monkeypatch.setenv('LT_SUITE_CONFIG', sandbox[1]['LT_SUITE_CONFIG'])
    results = setup_module.check_browser({'cfg':config_core().load()})
    assert any(expected in r.text for r in results), [r.text for r in results]
    if '@example' in (content or ''):
        assert any(r.status=='FAIL' for r in results)
        assert not any('@example' in r.text for r in results)


def test_setup_plan_hides_url(sandbox, setup_module, capsys):
    p = plan(sandbox, {'devtools_url':'http://127.0.0.1:9222'})
    assert setup_module.main(['lt-chrome','plan','--config',p['config'],'--json']) == 0
    output = capsys.readouterr().out
    assert 'http://127.0.0.1:9222' not in output
    assert json.loads(output)['action'] == 'start'


@pytest.mark.parametrize('platform', ['linux','darwin','win32'])
def test_chromium_choices_map_to_flags(sandbox, platform):
    # Default folder and channel mapping must not accidentally launch another browser.
    home, env = sandbox
    for browser in ('chrome','chrome-beta','chrome-dev','chrome-canary','chromium','edge','brave','vivaldi','other'):
        values = {'chromium':browser}
        if browser == 'other': values['chromium_user_data_dir'] = str(home/'custom')
        p = plan(sandbox, values, '--platform',platform)
        assert p['action'] == 'start', p['reason']
        if browser == 'chrome':
            assert '--autoConnect' in p['argv']
            assert not any(x.startswith(('--channel','--userDataDir')) for x in p['argv'])
        elif browser.startswith('chrome-'):
            assert '--channel='+browser[7:] in p['argv']
        else:
            folder = next(a.split('=',1)[1] for a in p['argv'] if a.startswith('--userDataDir='))
            expected = {'chromium':'Chromium' if platform!='linux' else 'chromium',
                'edge':'Microsoft' if platform!='linux' else 'microsoft-edge',
                'brave':'BraveSoftware','vivaldi':'Vivaldi' if platform!='linux' else 'vivaldi',
                'other':'custom'}[browser]
            assert expected in folder


@pytest.mark.parametrize('which', ['examples/configs/minimal.toml','examples/usyd-md/config.toml','shared/config.defaults.toml'])
def test_shipped_toml_reader_corpus(sandbox, which):
    raw = (ROOT/which).read_text()
    assert plan(sandbox, raw=raw)['action'] != 'refuse'
    doc = tomllib.loads(raw)
    assert plan(sandbox, raw=raw)['browser'] == {k:doc.get('browser',{}).get(k, config_core().defaults()[0]['browser'][k]) for k in KEYS}


def test_tomlw_reader_corpus(sandbox):
    from ltcore import tomlw
    core = config_core()
    doc = core.defaults()[0]
    doc['browser']['chromium_user_data_dir'] = 'café/space (path)'
    assert plan(sandbox, raw=tomlw.dumps(doc))['action'] == 'start'
    assert plan(sandbox, raw=tomlw.dumps({'browser':{}}))['action'] == 'start'


def test_pins_match_plugin_toml_and_setup(setup_module):
    pins = json.loads(subprocess.check_output([NODE,str(LAUNCHER),'--print-pins'],text=True))
    source = tomllib.loads((ROOT/'packaging/plugin.toml').read_text())
    schema = json.loads((ROOT/'shared/schemas/config.schema.json').read_text())
    assert pins == {k:source['lt_chrome'][k] for k in ('package','fixed_args','node_gate')} | {
        'lt_suite_version':source['version'], 'browser_keys':sorted(schema['properties']['browser']['properties'])}
    assert setup_module.CHROME_HOST_DATA['browser_keys'] == pins['browser_keys']
    assert setup_module.MCP_PACKAGE == pins['package']
    lock = json.loads((ROOT/'packaging/package-lock.json').read_text())
    assert lock['packages']['node_modules/chrome-devtools-mcp']['integrity'] == source['package_integrity']['chrome_devtools_mcp']


@pytest.mark.parametrize('xdg', ['', 'relative', 'absolute'])
def test_bootstrap_cache_dir_matches_python(sandbox, xdg):
    from ltcore import paths
    home, env = sandbox
    env.pop('XDG_CACHE_HOME',None)
    if xdg: env['XDG_CACHE_HOME'] = str(home/'alternate') if xdg=='absolute' else xdg
    r = bootstrap(sandbox)
    assert json.loads(r.stdout)['cwd'] == str(paths.cache_dir(environ=env)/'lt-chrome'/'run')


def test_windows_spawn_needs_npx_cli(sandbox):
    hook = sandbox[0]/'hide-npx.cjs'
    hook.write_text("const fs=require('fs'),orig=fs.existsSync;fs.existsSync=function(p){return String(p).endsWith('npx-cli.js')?false:orig(p)}")
    sandbox[1]['NODE_OPTIONS'] = '--require='+str(hook)
    p = plan(sandbox,None,'--platform','win32')
    assert p['action'] == 'refuse' and 'npx-cli.js' in p['reason']


@pytest.mark.parametrize('host', ['cursor','vscode','copilot','gemini-settings','antigravity'])
def test_host_snippets_keep_filters(setup_module, host, capsys):
    assert setup_module.main(['lt-chrome','snippet','--host',host]) == 0
    d = json.loads(capsys.readouterr().out)
    entry = d.get('servers', d.get('mcpServers'))['lt-chrome']
    assert entry['command'] == 'node'
    if host in ('gemini-settings','antigravity'):
        assert 'click_at' in entry['excludeTools' if host=='gemini-settings' else 'disabledTools']


@pytest.mark.parametrize('adapter,reason', [('auto','this agent did not pass the plugin folder'),
    ('claude-in-chrome','this agent did not pass the plugin folder'),
    ('claude-in-chrome','no lt-chrome launcher under the plugin folder')])
def test_doctor_fallback_records(sandbox, setup_module, monkeypatch, adapter, reason):
    p = plan(sandbox, {'adapter':adapter})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    cache = Path(p['cache_dir'])/'lt-chrome'
    cache.mkdir(parents=True,exist_ok=True)
    (cache/'last-fallback-test.json').write_text(json.dumps({'via':'test','time':'synthetic','action':'fallback','reason':reason}))
    stable = Path(p['chrome_stable_profile_dir'])
    stable.mkdir(parents=True)
    (stable/'DevToolsActivePort').write_text('9222\n@example.invalid/x\n')
    lines = setup_module.check_browser({'cfg':config_core().load()})
    assert any('started the 0.1.0 fallback' in r.text and r.status==('PASS' if adapter=='auto' else 'WARN') for r in lines)
    assert any('unexpected second line' in r.text and r.status=='FAIL' for r in lines)
    if reason.startswith('no lt-chrome'): assert any('reinstall lt-suite' in r.text for r in lines)


def test_doctor_unused_browser_folder_is_not_read(sandbox, setup_module, monkeypatch):
    p = plan(sandbox, {'adapter':'claude-in-chrome','chromium':'brave'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    folder = Path(sandbox[1]['XDG_CONFIG_HOME'])/'BraveSoftware/Brave-Browser'
    folder.mkdir(parents=True)
    (folder/'DevToolsActivePort').write_text('9222\n@example.invalid/x\n')
    lines = setup_module.check_browser({'cfg':config_core().load()})
    assert not any('DevToolsActivePort' in r.text or r.status=='FAIL' for r in lines)


def test_doctor_probe_follows_no_redirects(setup_module):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    class Stub(BaseHTTPRequestHandler):
        websocket = 'ws://127.0.0.1:9222/devtools/browser/stub'
        redirect = False
        def log_message(self,*args): pass
        def do_GET(self):
            if self.redirect:
                self.send_response(302); self.send_header('Location','http://192.0.2.1:9/'); self.end_headers()
            else:
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'webSocketDebuggerUrl':self.websocket}).encode())
    server = HTTPServer(('127.0.0.1',0),Stub)
    worker = Thread(target=server.serve_forever,daemon=True); worker.start()
    url = 'http://127.0.0.1:'+str(server.server_port)
    try:
        assert setup_module.probe_devtools(url).status == 'PASS'
        Stub.websocket='ws://192.0.2.1:9/x'
        assert setup_module.probe_devtools(url).status == 'FAIL'
        Stub.redirect=True
        assert setup_module.probe_devtools(url).status == 'FAIL'
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_doctor_invalid_url_never_echoes(sandbox, setup_module, monkeypatch, capsys):
    p = plan(sandbox, {'devtools_url':'http://u:synthetic-password@10.0.0.1:9222'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    for as_json in ([],['--json']):
        assert setup_module.main(['doctor',*as_json]) == 1
        output = capsys.readouterr()
        assert 'synthetic-password' not in output.out+output.err
        assert '10.0.0.1' not in output.out+output.err


# Real npm boundary tests reuse a cache and never invoke any browser tools.
_probe_spec = importlib.util.spec_from_file_location('ltchrome_probe_tests', Path(__file__).with_name('test_lt_chrome.py'))
_probe_module = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(_probe_module)
configured_server = _probe_module.configured_server
command_for, probe, DOCUMENTED_TOOLS = _probe_module.command_for, _probe_module.probe, _probe_module.DOCUMENTED_TOOLS


@pytest.mark.parametrize('root', [True,False])
@pytest.mark.parametrize('layout', ['sibling','home','workspace-ancestor'])
def test_npx_workspace_isolation(configured_server, tmp_path, root, layout):
    workspace_isolation(configured_server, tmp_path, root, layout)


def workspace_isolation(configured_server, tmp_path, root, layout, *, interpreters=('node', 'sh'), command=None):
    _, _, _, base_env = configured_server
    isolated_env = _probe_module.isolated_env
    home = tmp_path/'workspace'
    home.mkdir()
    env = isolated_env(home)
    env['npm_config_cache'] = base_env['npm_config_cache']
    if layout == 'sibling':
        env['XDG_CACHE_HOME'] = str(tmp_path/'cache')
    if layout == 'workspace-ancestor':
        # Native platform cache layout, with the matching workspace above run.
        from ltcore import paths
        ancestor = Path(str(paths.cache_dir(environ=env)))/'lt-chrome'
        ancestor.mkdir(parents=True)
        (ancestor/'package.json').write_text('{"workspaces":["run"]}')
    else:
        ancestor = home
    package = ancestor/'node_modules/chrome-devtools-mcp'
    package.mkdir(parents=True)
    (package/'package.json').write_text('{"name":"chrome-devtools-mcp","version":"1.12.0","bin":"bin.cjs"}')
    marker = tmp_path/'marker'
    (package/'bin.cjs').write_text("require('fs').writeFileSync("+json.dumps(str(marker))+",'planted')")
    (home/'.npmrc').write_text('registry=http://127.0.0.1:9\n')
    # HOME-as-workspace also makes .npmrc the trusted user config. Separate that
    # npm authority so this test measures project discovery, not user settings.
    userconfig = tmp_path/'user-npmrc'
    userconfig.write_text('')
    env['npm_config_userconfig'] = str(userconfig)
    env['npm_config_fetch_retries'] = '0'
    if os.name!='nt':
        folder = ancestor/'node_modules/.bin'; folder.mkdir()
        for name in interpreters:
            f = folder/name
            f.write_text('#!/bin/sh\necho planted-'+name+' > '+shlex.quote(str(marker))+'\nexit 7\n')
            f.chmod(0o755)
    try:
        assert probe(command if command is not None else command_for(root),home,env) == DOCUMENTED_TOOLS
    finally:
        assert not marker.exists(), 'workspace executable ran: ' + marker.read_text()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX macOS path simulation')
@pytest.mark.parametrize('root', [True, False])
@pytest.mark.parametrize('alias', [True, False])
@pytest.mark.parametrize('interpreter', ['node', 'sh'])
def test_npx_macos_cache_ancestor_interpreters(configured_server, tmp_path, monkeypatch, root, alias, interpreter):
    # Simulate macOS's HOME/Library/Caches even on Linux. Only the launcher sees
    # darwin; npm and the tool-list-only server retain the actual host platform.
    hook = tmp_path / 'darwin.cjs'
    # A Node installation path may itself contain spaces and shell quotes.
    node = tmp_path / "node's bin/node"
    node.parent.mkdir()
    node.write_text('#!/bin/sh\nexec ' + shlex.quote(NODE) + ' "$@"\n')
    node.chmod(0o700)
    hook.write_text("if(process.argv.includes('via=j6')){"
                    "Object.defineProperty(process,'platform',{value:'darwin'});"
                    "Object.defineProperty(process,'execPath',{value:" + json.dumps(str(node)) + "});}")
    monkeypatch.setenv('NODE_OPTIONS', '--require=' + str(hook))
    physical = tmp_path / 'private/var'
    physical.mkdir(parents=True)
    logical = physical
    if alias:
        logical = tmp_path / 'var'
        logical.symlink_to(physical, target_is_directory=True)
    workspace_isolation(configured_server, logical, root, 'sibling',
                        interpreters=(interpreter,), command=[*command_for(root), 'via=j6'])


def test_npx_argv_roundtrip(configured_server, sandbox):
    _, _, _, env = configured_server
    folder = sandbox[0]/"café & ('folder') --isolated x"
    p = plan(sandbox, {'chromium_user_data_dir':str(folder)})
    argv = p['argv']
    index = argv.index('-y')+1
    argv[index] = 'file:'+str(ROOT/'tests/packaging/fixtures/npx_echo')
    r = subprocess.run(argv, env=env, cwd=p['cwd'],capture_output=True,text=True,timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == argv[index+1:]


@pytest.mark.skipif(os.name != 'nt', reason='native cmd.exe fallback')
def test_bootstrap_native_windows_npx_cmd(sandbox):
    home, env = sandbox
    node = home/'node.exe'
    shutil.copyfile(NODE,node)
    bin_dir = home/'bin'
    bin_dir.mkdir()
    capture = home/'received.txt'
    (bin_dir/'npx.cmd').write_text('@echo off\necho %* > "'+str(capture)+'"\nexit /b 7\n')
    env['PATH'] = str(bin_dir)+os.pathsep+env.get('PATH','')
    boot = json.loads((ROOT/'.mcp.json').read_text())['mcpServers']['lt-chrome']['args'][1]
    r = subprocess.run([str(node),'-e',boot,'--','via=windows'],env=env,cwd=home,capture_output=True,text=True,timeout=30)
    assert r.returncode == 7
    assert capture.read_text().strip() == ' '.join(['--no-workspaces',*ARGS])
    assert len(r.stderr.splitlines()) == 1 and 'DEP0190' not in r.stderr


def test_doctor_prefetch_uses_neutral_folder_and_hardened_env(sandbox, setup_module, monkeypatch):
    p = plan(sandbox, {'adapter':'antigravity'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    monkeypatch.setenv('CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY','1')
    monkeypatch.setenv('PLUGIN_DATA','ignored')
    original = setup_module.run
    calls = []
    def run(argv,**kwargs):
        if '--version' in argv:
            calls.append(kwargs)
            assert kwargs['cwd'] == p['cwd']
            assert kwargs['env']['CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY'] == '1'
            assert 'CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY' not in kwargs['env']
            assert 'PLUGIN_DATA' not in kwargs['env']
            return 0,'1.12.0',''
        if 'dist.integrity' in argv:
            return 0,setup_module.CHROME_HOST_DATA['integrity'],' '
        return original(argv,**kwargs)
    monkeypatch.setattr(setup_module,'run',run)
    results = setup_module.check_browser({'cfg':config_core().load()})
    assert len(calls) == 1
    assert any(r.status=='PASS' and 'registry integrity matches' in r.text for r in results)


def test_doctor_reports_version_config_mismatch_and_old_stable_launcher(sandbox, setup_module, monkeypatch):
    p = plan(sandbox, {'adapter':'manual-paste'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    original = setup_module.chrome_plan
    def node21(config_path):
        result=original(config_path)
        result['node_version']='21.7.3'
        return result
    monkeypatch.setattr(setup_module,'chrome_plan',node21)
    folder=Path(p['cache_dir'])/'lt-chrome'; folder.mkdir(parents=True)
    (folder/'last-start-test.json').write_text(json.dumps({'config':str(sandbox[0]/'different.toml'),'action':'serve-none','via':'test','time':'synthetic'}))
    stable=Path(p['config_dir'])/'lt-chrome.cjs'; stable.parent.mkdir(parents=True)
    stable.write_text("process.stdout.write(JSON.stringify({lt_suite_version:'0.0.0'}))")
    results = setup_module.check_browser({'cfg':config_core().load()})
    assert any(r.status=='WARN' and 'node 21.7.3' in r.text for r in results)
    assert any(r.status=='WARN' and 'another folder' in r.text for r in results)
    assert any(r.status=='WARN' and 'lt-chrome install' in r.text for r in results)


def test_plan_overrides_do_not_affect_runtime(sandbox):
    plan(sandbox,{'adapter':'manual-paste'})
    result=invoke(sandbox,'--platform','win32','--home','ignored','--env-json','{}',text='')
    assert result.returncode == 0
    assert len(list(sandbox[0].rglob('last-start-unknown.json'))) == 1


def test_windows_case_insensitive_env_plan(sandbox):
    sandbox[1].update(chrome_devtools_mcp_category_experimental_third_party='1',Plugin_Data='ignored')
    p=plan(sandbox,None,'--platform','win32')
    assert set(p['env_removed']) >= {'chrome_devtools_mcp_category_experimental_third_party','Plugin_Data'}


def test_bootstrap_record_is_absent_before_spawn(sandbox):
    home,env=sandbox
    hook=home/'event-hook.cjs'
    hook.write_text('''const fs=require('fs'),cp=require('child_process'),E=require('events'),path=require('path');
cp.spawn=function(command,args,opts){const child=new E();child.kill=function(){};
setImmediate(function(){const file=path.join(path.dirname(opts.cwd),'last-fallback-event.json');
process.stdout.write(JSON.stringify({before:fs.existsSync(file)})+'\\n');child.emit('spawn');
process.stdout.write(JSON.stringify({after:fs.existsSync(file)})+'\\n');child.emit('exit',0)});return child};''')
    boot=json.loads((ROOT/'.mcp.json').read_text())['mcpServers']['lt-chrome']['args'][1]
    result=subprocess.run([NODE,'--require',str(hook),'-e',boot,'--','via=event'],cwd=home,env=env,capture_output=True,text=True)
    assert result.returncode==0
    assert [json.loads(s) for s in result.stdout.splitlines()] == [{'before':False},{'after':True}]


@pytest.mark.parametrize('layout', ['home-only','profile-first','localappdata'])
def test_bootstrap_home_cache_precedence(sandbox, layout):
    from ltcore import paths
    home, env = sandbox
    env.pop('LOCALAPPDATA', None)
    env.pop('USERPROFILE', None)
    env.pop('XDG_CACHE_HOME', None)
    if layout == 'profile-first': env['USERPROFILE'] = str(home/'profile-home')
    if layout == 'localappdata': env['LOCALAPPDATA'] = str(home/'local-data')
    r=bootstrap(sandbox)
    assert r.returncode == 0
    assert json.loads(r.stdout)['cwd'] == str(paths.cache_dir(environ=env)/'lt-chrome'/'run')


def test_doctor_warns_when_cache_is_unwritable(sandbox, setup_module, monkeypatch):
    p=plan(sandbox, {'adapter':'auto'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    blocked=sandbox[0]/'blocked-cache'
    blocked.write_text('file')
    monkeypatch.setenv('XDG_CACHE_HOME',str(blocked))
    monkeypatch.setenv('LOCALAPPDATA',str(blocked))
    if sys.platform=='darwin':
        # Native macOS cache selection hangs off HOME.
        monkeypatch.setenv('HOME',str(blocked))
    results=setup_module.check_browser({'cfg':config_core().load()})
    assert any(r.status=='WARN' and 'cannot be written' in r.text for r in results)


@pytest.mark.parametrize('platform', ['linux','darwin','win32'])
def test_doctor_chrome_channel_profile_matches_puppeteer(sandbox, platform):
    env=sandbox[1]
    p=plan(sandbox, {'chromium':'chrome-dev'},'--platform',platform)
    suffix={'linux':'google-chrome-unstable','darwin':'Google/Chrome Dev','win32':'Google/Chrome Dev/User Data'}[platform]
    assert p['profile_dir'].replace('\\','/').endswith(suffix)
    assert p['chrome_stable_profile_dir'].replace('\\','/').endswith({'linux':'google-chrome','darwin':'Google/Chrome','win32':'Google/Chrome/User Data'}[platform])
    if platform=='linux':
        env['CHROME_CONFIG_HOME']='relative-chrome'
        p=plan(sandbox, {'chromium':'chrome'},'--platform',platform)
        assert p['profile_dir']==str(Path(p['cwd'])/'relative-chrome/google-chrome')


def test_doctor_detects_stale_node_gate_in_stable_launcher(sandbox, setup_module, monkeypatch):
    p=plan(sandbox, {'adapter':'manual-paste'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    pins=json.loads(subprocess.check_output([NODE,str(LAUNCHER),'--print-pins'],text=True))
    pins['node_gate']={'min_major':18}
    stable=Path(p['config_dir'])/'lt-chrome.cjs'; stable.parent.mkdir(parents=True)
    stable.write_text('process.stdout.write('+json.dumps(json.dumps(pins))+')')
    results=setup_module.check_browser({'cfg':config_core().load()})
    assert any(r.status=='WARN' and 'lt-chrome install' in r.text for r in results)


def test_doctor_compares_hidden_url_readings_exactly(sandbox, setup_module, monkeypatch):
    p=plan(sandbox, {'adapter':'auto','devtools_url':'http://127.0.0.1:9222'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    original=setup_module.chrome_plan
    def different_url(config_path):
        result=original(config_path)
        import hashlib
        result['devtools_url_sha256']=hashlib.sha256(b'http://127.0.0.1:9333').hexdigest()
        return result
    monkeypatch.setattr(setup_module,'chrome_plan',different_url)
    results=setup_module.check_browser({'cfg':config_core().load()})
    assert any(r.status=='FAIL' and 'for browser.devtools_url' in r.text for r in results)
    assert not any('http://127.0.0.1' in r.text for r in results)


def test_doctor_missing_node_is_actionable(sandbox, setup_module, monkeypatch):
    p=plan(sandbox, {'adapter':'manual-paste'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    original=setup_module.shutil.which
    monkeypatch.setattr(setup_module.shutil,'which',lambda name:None if name=='node' else original(name))
    results=setup_module.check_browser({'cfg':config_core().load()})
    assert any(r.status=='WARN' and 'no node on PATH' in r.text for r in results)


def test_doctor_old_node_fails(sandbox, setup_module, monkeypatch):
    p=plan(sandbox, {'adapter':'auto'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    original=setup_module.chrome_plan
    def old_node(config_path):
        result=original(config_path)
        result.update(node_version='20.18.0', action='refuse', reason='Node too old')
        return result
    monkeypatch.setattr(setup_module,'chrome_plan',old_node)
    results=setup_module.check_browser({'cfg':config_core().load()})
    assert any(r.status=='FAIL' and 'node 20.18.0 is too old' in r.text for r in results)


def test_doctor_http_probe_requires_explicit_flag(sandbox, setup_module, monkeypatch):
    p=plan(sandbox, {'adapter':'auto','devtools_url':'http://127.0.0.1:9222'})
    monkeypatch.setenv('LT_SUITE_CONFIG',p['config'])
    calls=[]
    def stub(url):
        calls.append(url)
        return setup_module.Result('browser','PASS','synthetic probe')
    monkeypatch.setattr(setup_module,'probe_devtools',stub)
    cfg=config_core().load()
    setup_module.check_browser({'cfg':cfg})
    assert not calls
    results=setup_module.check_browser({'cfg':cfg,'probe_devtools':True})
    assert calls==['http://127.0.0.1:9222']
    assert any('browser-url on loopback port 9222' in r.text for r in results)
    assert not any(r.status=='FAIL' for r in results)


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('table', [False, True])
def test_scalar_browser_descendants_refused(sandbox, key, table):
    raw = ('[browser]\n' if table else 'browser.') + key + '.mode="manual-paste"\n'
    p = plan(sandbox, raw=raw)
    assert p['action'] == 'refuse'
    assert 'line ' in p['reason']
    assert 'manual-paste' not in p['reason']
    with pytest.raises(config_core().ConfigError):
        config_core().load(p['config'])


@pytest.mark.parametrize('override', [None, 'flag', 'env'])
def test_doctor_preserves_workspace_quiet_rule(sandbox, setup_module, monkeypatch, override):
    home, env = sandbox
    from ltcore import paths
    platform_config = paths.config_dir(environ=env) / 'config.toml'
    platform_config.parent.mkdir(parents=True)
    platform_config.write_text('[browser]\nadapter="manual-paste"\n')
    workspace = home / 'lt-suite.toml'
    workspace.write_text('[browser]\nadapter="auto"\n')
    monkeypatch.delenv('LT_SUITE_CONFIG', raising=False)
    if override == 'env':
        monkeypatch.setenv('LT_SUITE_CONFIG', str(workspace))
    ctx = {'cfg': config_core().load(str(workspace) if override == 'flag' else None),
           'prefetch_mcp': True}
    if override == 'flag':
        ctx['config'] = str(workspace)
    original = setup_module.run
    npm_calls = []
    def run(argv, **kwargs):
        if '--version' in argv or 'dist.integrity' in argv:
            npm_calls.append(argv)
            return 0, setup_module.CHROME_HOST_DATA['integrity'], ''
        return original(argv, **kwargs)
    monkeypatch.setattr(setup_module, 'run', run)
    results = setup_module.check_browser(ctx)
    action = 'start' if override else 'serve-none'
    assert any('lt-chrome plan: ' + action in r.text for r in results)
    assert bool(npm_calls) == bool(override)


@pytest.mark.skipif(sys.platform != 'linux', reason='Puppeteer Linux config environment')
@pytest.mark.parametrize('variable', ['CHROME_CONFIG_HOME', 'XDG_CONFIG_HOME'])
@pytest.mark.parametrize('cache_writable', [True, False])
def test_doctor_relative_chrome_profile_uses_child_cwd(sandbox, setup_module, monkeypatch,
                                                      variable, cache_writable):
    home, env = sandbox
    p = plan(sandbox, {'chromium': 'chrome'})
    monkeypatch.setenv('LT_SUITE_CONFIG', p['config'])
    monkeypatch.setenv(variable, 'relative-chrome')
    if not cache_writable:
        blocked = home / 'blocked-cache'
        blocked.write_text('file')
        monkeypatch.setenv('XDG_CACHE_HOME', str(blocked))
    effective_plan = setup_module.chrome_plan()
    child_profile = Path(effective_plan['cwd']) / 'relative-chrome/google-chrome'
    child_profile.mkdir(parents=True)
    (child_profile / 'DevToolsActivePort').write_text('9222\n@example.invalid/x\n')
    if cache_writable:
        decoy = home / 'relative-chrome/google-chrome'
        decoy.mkdir(parents=True)
        (decoy / 'DevToolsActivePort').write_text('9333\n/devtools/browser/decoy\n')
    results = setup_module.check_browser({'cfg': config_core().load()})
    assert any(r.status == 'FAIL' and 'unexpected second line' in r.text for r in results)
    assert not any(r.status == 'PASS' and 'port 9333' in r.text for r in results)
    assert effective_plan['profile_dir'] == str(child_profile)


@pytest.mark.skipif(sys.platform != 'linux', reason='Puppeteer Linux config environment')
def test_doctor_relative_fallback_profile_uses_child_cwd(sandbox, setup_module, monkeypatch):
    p = plan(sandbox, {'adapter': 'manual-paste'})
    monkeypatch.setenv('LT_SUITE_CONFIG', p['config'])
    monkeypatch.setenv('CHROME_CONFIG_HOME', 'relative-chrome')
    run_folder = Path(p['cache_dir']) / 'lt-chrome/run'
    profile = run_folder / 'relative-chrome/google-chrome'
    profile.mkdir(parents=True)
    (profile / 'DevToolsActivePort').write_text('9222\n@example.invalid/x\n')
    (run_folder.parent / 'last-fallback-test.json').write_text(json.dumps(
        {'action': 'fallback', 'via': 'test', 'time': 'synthetic'}))
    results = setup_module.check_browser({'cfg': config_core().load()})
    assert any(r.status == 'FAIL' and 'unexpected second line' in r.text for r in results)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX account lookup')
@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_named_user_profile_matches_ltcore(sandbox, setup_module, monkeypatch, platform):
    import pwd
    from types import SimpleNamespace
    home, env = sandbox
    account_home = home / 'synthetic-account'
    monkeypatch.setattr(pwd, 'getpwnam', lambda name: SimpleNamespace(pw_dir=str(account_home)))
    hook = home / 'account-hook.cjs'
    hook.write_text('''const cp=require('child_process');
cp.execFileSync=function(command,args){
 if(command==='getent' && args.join(' ')==='passwd -- ltchrome-fixture-user')
   return 'ltchrome-fixture-user:x:12345:12345::'+process.env.FIXTURE_ACCOUNT_HOME+':/bin/sh\\n';
 if(command==='dscacheutil' && args.join(' ')==='-q user -a name ltchrome-fixture-user')
   return 'dir: '+process.env.FIXTURE_ACCOUNT_HOME+'\\n';
 throw Error('unexpected account lookup');
};''')
    env.update(NODE_OPTIONS='--require=' + str(hook), FIXTURE_ACCOUNT_HOME=str(account_home))
    monkeypatch.setenv('NODE_OPTIONS', env['NODE_OPTIONS'])
    monkeypatch.setenv('FIXTURE_ACCOUNT_HOME', str(account_home))
    p = plan(sandbox, {'chromium_user_data_dir': '~ltchrome-fixture-user/profile'},
             '--platform', platform)
    monkeypatch.setenv('LT_SUITE_CONFIG', p['config'])
    cfg = config_core().load()
    assert p['profile_dir'] == cfg.get('browser.chromium_user_data_dir')
    assert '--userDataDir=' + str(account_home / 'profile') in p['argv']
    results = setup_module.check_browser({'cfg': cfg})
    assert not any(r.status == 'FAIL' for r in results)


@pytest.mark.parametrize('unknown', [False, True])
def test_named_user_lookup_failure_is_safe(sandbox, unknown):
    home, env = sandbox
    hook = home / 'missing-account-hook.cjs'
    hook.write_text("require('child_process').execFileSync=function(){const e=Error('private lookup error');"
                    + ("e.status=2;" if unknown else "e.code='ENOENT';") + "throw e};")
    env['NODE_OPTIONS'] = '--require=' + str(hook)
    p = plan(sandbox, {'chromium_user_data_dir': '~ltchrome-fixture-user/profile'},
             '--platform', 'linux')
    assert p['action'] == ('start' if unknown else 'refuse')
    if unknown:
        assert p['profile_dir'] == str(home / '~ltchrome-fixture-user/profile')
    else:
        assert 'use an absolute folder' in p['reason']
        assert 'private lookup error' not in p['reason']


@pytest.mark.parametrize('folder', [r'~\AppData\Local\Custom', '~/Custom',
                                     r'~ltchrome-fixture-user\Custom'])
@pytest.mark.parametrize('profile_env', ['USERPROFILE', 'HOMEPATH'])
def test_windows_profile_tilde_matches_ltcore(sandbox, monkeypatch, folder, profile_env):
    import ntpath
    home = r'C:\synthetic\ltchrome-fixture-user'
    env = {'USERNAME': 'ltchrome-fixture-user', 'HOME': r'C:\ignored'}
    if profile_env == 'USERPROFILE':
        env['USERPROFILE'] = home
    else:
        env.update(HOMEDRIVE='C:', HOMEPATH=r'\synthetic\ltchrome-fixture-user')
    for name in ('USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'HOME'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    plan_env = {k: v for k, v in sandbox[1].items()
                if k not in ('USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'HOME')}
    plan_env['LT_SUITE_CONFIG'] = str(sandbox[0] / 'chosen.toml')
    p = plan(sandbox, {'chromium_user_data_dir': folder}, '--platform', 'win32',
             '--env-json', json.dumps(dict(plan_env, **env)))
    expected = ntpath.normpath(ntpath.expanduser(folder))
    assert p['profile_dir'] == expected
    assert '--userDataDir=' + expected in p['argv']


def test_doctor_compares_effective_profile_path(sandbox, setup_module, monkeypatch):
    p = plan(sandbox, {'chromium_user_data_dir': 'synthetic-profile'})
    monkeypatch.setenv('LT_SUITE_CONFIG', p['config'])
    original = setup_module.chrome_plan
    def wrong_profile(config_path=None):
        result = original(config_path)
        result['profile_dir'] = str(sandbox[0] / 'wrong-profile')
        return result
    monkeypatch.setattr(setup_module, 'chrome_plan', wrong_profile)
    results = setup_module.check_browser({'cfg': config_core().load()})
    assert any(r.status == 'FAIL' and 'for browser.chromium_user_data_dir' in r.text for r in results)


def test_windows_profile_tilde_preserves_drive_root(sandbox):
    env = dict(sandbox[1], USERPROFILE='C:\\', LT_SUITE_CONFIG=str(sandbox[0] / 'chosen.toml'))
    p = plan(sandbox, {'chromium_user_data_dir': '~'}, '--platform', 'win32',
             '--env-json', json.dumps(env))
    assert p['profile_dir'] == 'C:\\'
    assert '--userDataDir=C:\\' in p['argv']

@pytest.mark.parametrize('raw', [
    '[browser]\nadaptr="manual-paste"\n',
    '[browser]\nsettings.adapter="manual-paste"\n',
    'browser.settings.adapter="manual-paste"\n',
    '[browser]\n"settings.adapter"="manual-paste"\n',
    '[browser]\nsettings={adapter="manual-paste"}\n',
    '[browser]\ntransport.mode="download"\n',
])
def test_unknown_browser_keys_refuse_without_spawning(sandbox, raw):
    # A misspelled quiet setting must never silently start a browser server.
    p = plan(sandbox, raw=raw)
    assert p['action'] == 'refuse', p
    assert not p['argv']
    hook = sandbox[0] / 'no-spawn.cjs'
    hook.write_text("require('child_process').spawn=function(){throw Error('unexpected server startup')}")
    sandbox[1]['NODE_OPTIONS'] = '--require=' + str(hook)
    result = invoke(sandbox, text='')
    assert result.returncode == 0, result.stderr
    assert 'did not start' in result.stderr
    assert 'manual-paste' not in result.stderr


def test_known_browser_keys_and_other_profile_remain_supported(sandbox):
    p = plan(sandbox, raw='[browser]\nchromium="other"\nchromium_user_data_dir="custom"\n'
             'transport="download"\nsink_port=8731\nlib_mode="inline"\nlib_port=8733\njs_tmp_dir=""\n')
    assert p['action'] == 'start', p['reason']
    assert '--userDataDir=' + str(sandbox[0] / 'custom') in p['argv']
