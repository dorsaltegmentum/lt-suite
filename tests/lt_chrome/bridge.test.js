// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
// browser.devtools_socket routes against synthetic listeners. No browser starts.
const test = require('node:test');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const EventEmitter = require('node:events');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const LAUNCHER = path.join(__dirname, '..', '..', 'shared', 'lt_chrome', 'lt-chrome.cjs');
const chrome = require(LAUNCHER);
const unix = process.platform !== 'win32';

function listen(server, where) {
  return new Promise((resolve, reject) => { server.once('error', reject); server.listen(where, () => resolve(server)); });
}
function close(server) {
  return new Promise((resolve) => { if (!server || !server.listening) resolve(); else server.close(() => resolve()); });
}
async function freePort() {
  const server = await listen(net.createServer(), {host: '127.0.0.1', port: 0});
  const port = server.address().port;
  await close(server);
  return port;
}
// Answers like a browser: webSocketDebuggerUrl names the request's Host header.
function fakeDevtools(hosts) {
  return http.createServer((req, res) => {
    hosts.push(req.headers.host);
    if (req.url !== '/json/version') { res.writeHead(404); res.end(); return; }
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({Browser: 'Synthetic/1.0', webSocketDebuggerUrl: 'ws://' + req.headers.host + '/devtools/browser/synthetic'}));
  });
}
// Stands in for the host proxy: each socket connection goes on to a TCP port.
function socketProxy(port) {
  return net.createServer((conn) => {
    const up = net.connect(port, '127.0.0.1');
    conn.on('error', () => up.destroy()); up.on('error', () => conn.destroy());
    conn.pipe(up); up.pipe(conn);
  });
}
function get(port) {
  return new Promise((resolve, reject) => {
    http.get({host: '127.0.0.1', port: port, path: '/json/version', agent: false}, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({status: res.statusCode, body: body}));
    }).on('error', reject);
  });
}
function listenerPort(plan) { return chrome.listener(plan).port; }
function sandbox(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ltc-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  return dir;
}
function env(dir) {
  return {HOME: dir, XDG_CONFIG_HOME: path.join(dir, 'config'), XDG_CACHE_HOME: path.join(dir, 'cache'), PATH: process.env.PATH || ''};
}
function planFor(dir, port, socket) {
  fs.writeFileSync(path.join(dir, 'lt-suite.toml'), '[browser]\ndevtools_url = "http://127.0.0.1:' + port + '"\ndevtools_socket = ' + JSON.stringify(socket) + '\n');
  return chrome.makePlan(['--plan', '--cwd', dir, '--env-json', JSON.stringify(env(dir))]);
}
// A real socket in a temp folder forwarding to a synthetic /json/version on another port.
async function bridgeSetup(t) {
  const dir = sandbox(t), hosts = [];
  const devtools = await listen(fakeDevtools(hosts), {host: '127.0.0.1', port: 0});
  const file = path.join(dir, 'devtools.sock');
  const proxy = await listen(socketProxy(devtools.address().port), file);
  t.after(() => Promise.all([close(devtools), close(proxy)]));
  return {dir: dir, hosts: hosts, file: file, devtools: devtools};
}

test('lt-chrome socket: plan resolves the socket path and keeps browserUrl', {skip: !unix}, (t) => {
  const dir = sandbox(t);
  const plan = planFor(dir, 9, 'run/devtools.sock');
  assert.equal(plan.action, 'start');
  assert.equal(plan.route, 'direct');
  assert.equal(plan.socket_path, path.join(dir, 'run', 'devtools.sock'));
  assert.ok(plan.argv.includes('--browserUrl=http://127.0.0.1:9'));
  assert.ok(!plan.argv.includes('--autoConnect'));
});
test('lt-chrome socket: needs devtools_url and plain path characters', (t) => {
  const dir = sandbox(t);
  fs.writeFileSync(path.join(dir, 'lt-suite.toml'), '[browser]\ndevtools_socket = "devtools.sock"\n');
  const plan = chrome.makePlan(['--plan', '--cwd', dir, '--env-json', JSON.stringify(env(dir))]);
  assert.equal(plan.action, 'refuse');
  assert.match(plan.reason, /needs browser\.devtools_url/);
  for (const bad of ['a%b.sock', 'a!b.sock', 'a"b.sock', 'a\u0007b.sock']) {
    const refused = planFor(dir, 9222, bad);
    assert.equal(refused.action, 'refuse');
    assert.match(refused.reason, /browser\.devtools_socket contains unsupported characters/);
  }
});
test('lt-chrome socket: the bridge address matches devtools_url, default port included', () => {
  assert.deepEqual(chrome.listener({browser: {devtools_url: 'http://127.0.0.1:80'}}), {host: '127.0.0.1', port: 80, header: '127.0.0.1'});
  assert.deepEqual(chrome.listener({browser: {devtools_url: 'http://[::1]:9222/'}}), {host: '::1', port: 9222, header: '[::1]:9222'});
});
test('lt-chrome socket: direct route when the DevTools port answers', {skip: !unix}, async (t) => {
  const dir = sandbox(t), hosts = [];
  const devtools = await listen(fakeDevtools(hosts), {host: '127.0.0.1', port: 0});
  t.after(() => close(devtools));
  const port = devtools.address().port;
  const plan = planFor(dir, port, path.join(dir, 'absent.sock'));
  const bridge = await chrome.prepare(plan, false);
  assert.equal(bridge, null);
  assert.equal(plan.action, 'start');
  assert.equal(plan.route, 'direct');
  assert.deepEqual(hosts, ['127.0.0.1:' + port]);
  assert.ok(plan.argv.includes('--browserUrl=http://127.0.0.1:' + port));
});
test('lt-chrome socket: socket route forwards a free loopback port and leaves the configured port alone', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t), port = await freePort();
  const plan = planFor(s.dir, port, s.file);
  const bridge = await chrome.prepare(plan, false);
  t.after(() => close(bridge));
  assert.equal(plan.action, 'start', plan.reason);
  assert.equal(plan.route, 'socket');
  assert.ok(bridge && bridge.listening);
  const own = bridge.address().port;
  assert.notEqual(own, port);
  assert.equal(bridge.address().address, '127.0.0.1');
  assert.ok(plan.argv.includes('--browserUrl=http://127.0.0.1:' + own), plan.argv.join(' '));
  assert.ok(!plan.argv.includes('--browserUrl=http://127.0.0.1:' + port));
  const answer = await get(own);
  assert.equal(answer.status, 200);
  assert.equal(JSON.parse(answer.body).webSocketDebuggerUrl, 'ws://127.0.0.1:' + own + '/devtools/browser/synthetic');
  // Route probe with the configured Host, then the bridge check and the client with the bridge's Host.
  assert.deepEqual(s.hosts, ['127.0.0.1:' + port, '127.0.0.1:' + own, '127.0.0.1:' + own]);
  await close(await listen(net.createServer(), {host: '127.0.0.1', port: port}));
});
test('lt-chrome socket: two launchers in one sandbox get separate bridges', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t), port = await freePort();
  const a = planFor(s.dir, port, s.file), first = await chrome.prepare(a, false);
  const b = planFor(s.dir, port, s.file), second = await chrome.prepare(b, false);
  t.after(() => Promise.all([close(first), close(second)]));
  assert.equal(a.route, 'socket');
  assert.equal(b.route, 'socket', 'the second launcher borrowed the first bridge');
  assert.notEqual(first.address().port, second.address().port);
  await close(first);
  assert.equal((await get(second.address().port)).status, 200);
});
test('lt-chrome socket: a browser that stops after the route probe refuses and frees the bridge', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t);
  const plan = planFor(s.dir, await freePort(), s.file);
  await chrome.chooseRoute(plan);
  assert.equal(plan.route, 'socket');
  await close(s.devtools);
  await assert.rejects(chrome.openBridge(plan), /the bridge does not reach DevTools through/);
  assert.ok(plan.argv.some((a) => a.indexOf('--browserUrl=http://127.0.0.1:' + listenerPort(plan)) === 0));
});
test('lt-chrome socket: a normal upstream close keeps the bytes still queued for the client', async () => {
  const {Duplex} = require('node:stream');
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const got = [];
  // The client takes bytes only after the upstream has closed.
  const conn = new Duplex({read() {}, write(chunk, enc, cb) { gate.then(() => { got.push(chunk); cb(); }); }, allowHalfOpen: true, highWaterMark: 1 << 20});
  const up = new Duplex({read() {}, write(chunk, enc, cb) { cb(); }, allowHalfOpen: true});
  let done = false;
  chrome.join(conn, up, () => { done = true; });
  conn.push(null);
  up.push(Buffer.alloc(32768, 7)); up.push(null);
  await new Promise((resolve) => up.on('close', resolve));
  release();
  const finished = await Promise.race([new Promise((resolve) => conn.on('finish', () => resolve(true))),
    new Promise((resolve) => setTimeout(() => resolve(false), 2000))]);
  assert.ok(finished, 'the client side never finished');
  assert.equal(Buffer.concat(got).length, 32768);
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(done);
});
test('lt-chrome socket: .. segments in a relative socket path stay as written', {skip: !unix}, (t) => {
  const dir = sandbox(t);
  const plan = planFor(dir, 9, 'link/../devtools.sock');
  assert.equal(plan.socket_path, dir + path.sep + 'link/../devtools.sock');
});
test('lt-chrome socket: missing socket refuses without auto-connect', {skip: !unix}, async (t) => {
  const dir = sandbox(t), port = await freePort();
  const plan = planFor(dir, port, path.join(dir, 'absent.sock'));
  assert.equal(await chrome.prepare(plan, false), null);
  assert.equal(plan.action, 'refuse');
  assert.match(plan.reason, /no socket exists at .*absent\.sock/);
  assert.deepEqual(plan.argv, []);
});
test('lt-chrome socket: a file that is not a socket refuses', {skip: !unix}, async (t) => {
  const dir = sandbox(t), port = await freePort(), file = path.join(dir, 'plain.sock');
  fs.writeFileSync(file, '');
  const plan = planFor(dir, port, file);
  await chrome.prepare(plan, false);
  assert.equal(plan.action, 'refuse');
  assert.match(plan.reason, /is not a Unix socket/);
});
test('lt-chrome socket: a socket without a DevTools listener refuses before any bind', {skip: !unix}, async (t) => {
  const dir = sandbox(t), port = await freePort(), file = path.join(dir, 'dead.sock');
  const proxy = await listen(socketProxy(await freePort()), file);
  t.after(() => close(proxy));
  const plan = planFor(dir, port, file);
  assert.equal(await chrome.prepare(plan, false), null);
  assert.equal(plan.action, 'refuse');
  assert.match(plan.reason, /does not reach a DevTools listener/);
  // Nothing holds the port afterwards: lt-chrome never listened.
  await close(await listen(net.createServer(), {host: '127.0.0.1', port: port}));
});
test('lt-chrome socket: a non-DevTools program on the configured port leaves the bridge on a free port', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t);
  // Holds the port but answers no HTTP, so the direct probe fails.
  const holder = await listen(net.createServer((conn) => conn.destroy()), {host: '127.0.0.1', port: 0});
  t.after(() => close(holder));
  const plan = planFor(s.dir, holder.address().port, s.file);
  const bridge = await chrome.prepare(plan, false);
  t.after(() => close(bridge));
  assert.equal(plan.action, 'start', plan.reason);
  assert.equal(plan.route, 'socket');
  assert.notEqual(bridge.address().port, holder.address().port);
  assert.ok(holder.listening);
});
test('lt-chrome socket: the bridge closes when the child exits', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t);
  const plan = planFor(s.dir, await freePort(), s.file);
  const bridge = await chrome.prepare(plan, false);
  t.after(() => close(bridge));
  assert.ok(bridge.listening);
  const code = await new Promise((resolve) => {
    chrome.runChild({argv: [process.execPath, '-e', 'process.exitCode = 3'], cwd: s.dir}, bridge,
      {process: new EventEmitter(), exit: resolve});
  });
  assert.equal(code, 3);
  assert.equal(bridge.listening, false);
});
test('lt-chrome socket: a signal closes the bridge and stops the child', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t);
  const plan = planFor(s.dir, await freePort(), s.file);
  const bridge = await chrome.prepare(plan, false);
  t.after(() => close(bridge));
  const host = new EventEmitter();
  const code = await new Promise((resolve) => {
    const child = chrome.runChild({argv: [process.execPath, '-e', 'setInterval(() => {}, 1000)'], cwd: s.dir}, bridge,
      {process: host, exit: resolve});
    child.on('spawn', () => host.emit('SIGTERM'));
  });
  assert.equal(code, 1);
  assert.equal(bridge.listening, false);
});
test('lt-chrome socket: --plan reports the route and hides the URL', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t), port = await freePort();
  planFor(s.dir, port, s.file);
  // Asynchronous: the synthetic listeners in this process must keep answering.
  const out = await new Promise((resolve, reject) => {
    cp.execFile(process.execPath, [LAUNCHER, '--plan', '--redact'], {cwd: s.dir, env: env(s.dir), encoding: 'utf8'},
      (error, stdout) => (error ? reject(error) : resolve(stdout)));
  });
  const plan = JSON.parse(out);
  assert.equal(plan.action, 'start');
  assert.equal(plan.route, 'socket');
  assert.equal(plan.socket_path, s.file);
  assert.equal(plan.browser.devtools_url, '<hidden>');
  assert.ok(!out.includes(':' + port), 'the plan printed the DevTools port');
  // --plan never binds: the port stays free for the real start.
  await close(await listen(net.createServer(), {host: '127.0.0.1', port: port}));
});
test('lt-chrome socket: another account\'s socket or a folder others can change is unsafe', () => {
  const sock = {uid: 1000};
  // Folders map to {uid, mode}; realpath leaves paths alone unless a test links them.
  function fsx(folders, links) {
    return {overflow: 65534, realpath: (d) => (links && links[d]) || d,
      stat: (d) => { if (!folders[d]) throw Object.assign(new Error('missing'), {code: 'ENOENT'}); return folders[d]; }};
  }
  const safe = {'/run/a': {uid: 1000, mode: 0o40700}, '/run': {uid: 0, mode: 0o40755}, '/': {uid: 65534, mode: 0o40755}};
  assert.equal(chrome.socketUnsafe(sock, '/run/a/devtools.sock', 1000, fsx(safe)), '');
  assert.match(chrome.socketUnsafe(sock, '/run/a/devtools.sock', 1001, fsx(safe)), /belongs to another account \(uid 1000\)/);
  assert.equal(chrome.socketUnsafe(sock, '/tmp/devtools.sock', 1000, fsx({'/tmp': {uid: 0, mode: 0o41777}, '/': {uid: 0, mode: 0o40755}})), '');
  assert.match(chrome.socketUnsafe(sock, '/srv/shared/devtools.sock', 1000, fsx({'/srv/shared': {uid: 1000, mode: 0o40777}, '/srv': {uid: 0, mode: 0o40755}, '/': {uid: 0, mode: 0o40755}})),
    /\/srv\/shared is writable by other accounts/);
  // A sticky folder owned by another account still lets that account replace the socket.
  assert.match(chrome.socketUnsafe(sock, '/srv/drop/devtools.sock', 1000, fsx({'/srv/drop': {uid: 1001, mode: 0o41777}, '/srv': {uid: 0, mode: 0o40755}, '/': {uid: 0, mode: 0o40755}})),
    /\/srv\/drop belongs to another account \(uid 1001\)/);
  // An ancestor that others can write lets them rename the socket's folder.
  assert.match(chrome.socketUnsafe(sock, '/srv/open/a/devtools.sock', 1000, fsx({'/srv/open/a': {uid: 1000, mode: 0o40700}, '/srv/open': {uid: 1000, mode: 0o40777}, '/srv': {uid: 0, mode: 0o40755}, '/': {uid: 0, mode: 0o40755}})),
    /\/srv\/open is writable by other accounts/);
  // The resolved path is checked as well as the written one.
  assert.match(chrome.socketUnsafe(sock, '/run/a/devtools.sock', 1000, fsx(Object.assign({'/var/x': {uid: 1001, mode: 0o40700}, '/var': {uid: 0, mode: 0o40755}}, safe), {'/run/a': '/var/x'})),
    /\/var\/x belongs to another account/);
  assert.match(chrome.socketUnsafe(sock, '/gone/devtools.sock', 1000, fsx({'/': {uid: 0, mode: 0o40755}})), /\/gone cannot be checked \(ENOENT\)/);
  // A symlinked socket is checked where it points.
  assert.match(chrome.socketUnsafe(sock, '/run/a/devtools.sock', 1000, fsx(Object.assign({'/srv/open': {uid: 1000, mode: 0o40777}, '/srv': {uid: 0, mode: 0o40755}}, safe), {'/run/a/devtools.sock': '/srv/open/real.sock'})),
    /\/srv\/open is writable by other accounts/);
});
test('lt-chrome socket: a kernel-chosen port equal to the configured port is replaced', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t), port = await freePort();
  const plan = planFor(s.dir, port, s.file);
  await chrome.chooseRoute(plan);
  const bridge = await chrome.openBridge(plan, [port]);
  t.after(() => close(bridge));
  assert.notEqual(bridge.address().port, port);
  assert.ok(plan.argv.includes('--browserUrl=http://127.0.0.1:' + bridge.address().port));
  await close(await listen(net.createServer(), {host: '127.0.0.1', port: port}));
});
test('lt-chrome socket: a socket in a folder others can write refuses before any bind', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t);
  const plan = planFor(s.dir, await freePort(), s.file);
  fs.chmodSync(s.dir, 0o777);
  try { assert.equal(await chrome.prepare(plan, false), null); } finally { fs.chmodSync(s.dir, 0o700); }
  assert.equal(plan.action, 'refuse');
  assert.equal(plan.route, 'socket');
  assert.match(plan.reason, /is writable by other accounts/);
});
test('lt-chrome socket: a path longer than sun_path refuses at plan time', {skip: !unix}, (t) => {
  const dir = sandbox(t);
  const plan = planFor(dir, 9, '/' + 'd'.repeat(120) + '/devtools.sock');
  assert.equal(plan.action, 'refuse');
  assert.equal(plan.route, 'socket');
  assert.match(plan.reason, /longer than a Unix socket path allows \(10[37] bytes\)/);
});
test('lt-chrome socket: Windows refuses the setting', (t) => {
  const dir = sandbox(t);
  fs.writeFileSync(path.join(dir, 'lt-suite.toml'), '[browser]\ndevtools_url = "http://127.0.0.1:9222"\ndevtools_socket = "C:\\\\run\\\\devtools.sock"\n');
  // A child process in the temp folder: Windows paths on this host are relative and land in its cwd.
  const out = cp.execFileSync(process.execPath, [LAUNCHER, '--plan', '--platform', 'win32', '--cwd', dir, '--home', dir,
    '--env-json', JSON.stringify(Object.assign(env(dir), {LT_SUITE_CONFIG: path.join(dir, 'lt-suite.toml')}))], {cwd: dir, encoding: 'utf8'});
  const plan = JSON.parse(out);
  assert.equal(plan.action, 'refuse', out);
  assert.match(plan.reason, /Linux and macOS only/);
});
test('lt-chrome socket: a stat error other than ENOENT is named', {skip: !unix || process.getuid() === 0}, async (t) => {
  const dir = sandbox(t), locked = path.join(dir, 'locked');
  fs.mkdirSync(locked, {mode: 0o000});
  const plan = planFor(dir, await freePort(), path.join(locked, 'devtools.sock'));
  try { assert.equal(await chrome.prepare(plan, false), null); } finally { fs.chmodSync(locked, 0o700); }
  assert.equal(plan.action, 'refuse');
  assert.match(plan.reason, /cannot be checked \(EACCES\)/);
});
test('lt-chrome socket: the bridge drops connections over BRIDGE_MAX and keeps the rest', {skip: !unix}, async (t) => {
  const s = await bridgeSetup(t);
  const plan = planFor(s.dir, await freePort(), s.file);
  const bridge = await chrome.prepare(plan, false);
  t.after(() => close(bridge));
  const port = bridge.address().port, held = [];
  t.after(() => held.forEach((c) => c.destroy()));
  // The bridge's own check has closed once no connection is left.
  for (let i = 0; i < 50 && await new Promise((r) => bridge.getConnections((e, n) => r(n))) > 0; i++) await new Promise((r) => setTimeout(r, 20));
  for (let i = 0; i < chrome.BRIDGE_MAX; i++) {
    const c = net.connect(port, '127.0.0.1');
    held.push(c);
    await new Promise((r) => c.once('connect', r));
  }
  for (let i = 0; i < 50 && await new Promise((r) => bridge.getConnections((e, n) => r(n))) < chrome.BRIDGE_MAX; i++) await new Promise((r) => setTimeout(r, 20));
  const extra = net.connect(port, '127.0.0.1');
  const closed = await Promise.race([new Promise((r) => extra.on('close', () => r(true))), new Promise((r) => setTimeout(() => r(false), 2000))]);
  extra.destroy();
  assert.ok(closed, 'connection over the cap stayed open');
  assert.ok(held.every((c) => !c.destroyed));
});
test('lt-chrome socket: an upstream that disappears after the route probe closes only that connection', {skip: !unix}, async (t) => {
  const dir = sandbox(t), hosts = [];
  const devtools = await listen(fakeDevtools(hosts), {host: '127.0.0.1', port: 0});
  const file = path.join(dir, 'devtools.sock');
  const proxy = await listen(socketProxy(devtools.address().port), file);
  t.after(() => close(devtools));
  const plan = planFor(dir, await freePort(), file);
  const bridge = await chrome.prepare(plan, false);
  t.after(() => close(bridge));
  await close(proxy);
  const c = net.connect(bridge.address().port, '127.0.0.1');
  c.on('error', () => {});
  const closed = await Promise.race([new Promise((r) => c.on('close', () => r(true))), new Promise((r) => setTimeout(() => r(false), 2000))]);
  assert.ok(closed);
  assert.ok(bridge.listening);
});
test('lt-chrome socket: a refused start records route socket and serves no tools', {skip: !unix}, async (t) => {
  const dir = sandbox(t), port = await freePort();
  planFor(dir, port, path.join(dir, 'absent.sock'));
  const result = await new Promise((resolve) => {
    const child = cp.execFile(process.execPath, [LAUNCHER], {cwd: dir, env: env(dir), encoding: 'utf8'}, (error, stdout, stderr) => resolve({stdout: stdout, stderr: stderr}));
    child.stdin.end();
  });
  assert.match(result.stderr, /lt-chrome did not start: browser\.devtools_socket: the DevTools port does not answer here and no socket exists/);
  const folder = path.join(dir, 'cache');
  const record = JSON.parse(fs.readFileSync(fs.readdirSync(folder, {recursive: true}).map((f) => path.join(folder, f)).find((f) => /last-start-.*\.json$/.test(f)), 'utf8'));
  assert.equal(record.action, 'refuse');
  assert.equal(record.route, 'socket');
});
test('lt-chrome socket: ~ expands to the home folder', {skip: !unix}, (t) => {
  const dir = sandbox(t);
  const plan = planFor(dir, 9, '~/state/devtools.sock');
  assert.equal(plan.socket_path, path.join(dir, 'state', 'devtools.sock'));
});
