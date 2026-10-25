// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const cp = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { gzipSync, gunzipSync } = require('node:zlib');
const { createHash } = require('node:crypto');
const H = require('./harness.js');
const hash = s => createHash('sha256').update(s).digest('hex');
const BODY = 'globalThis.__ran = (globalThis.__ran || 0) + 1; return {ok:true, n:__LT_ARGS__.n};';
const PREFIX = 'lt-suite:js:';
const INDEX = PREFIX + 'index';
const storage = () => {
  const data = new Map();
  return { data, get length() { return data.size; }, key: i => [...data.keys()][i] ?? null,
    getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k) };
};
function env(store = storage()) {
  const e = H.makeEnv();
  e.ctx.localStorage = store;
  return e;
}
function render(options = {}, body = BODY) {
  const r = cp.spawnSync(process.env.PYTHON || 'python3', ['-c',
    'import sys,json; from ltcore.ltjs import render_source; s=json.load(sys.stdin); print(render_source(s["body"],name="probe",**s["options"]))'],
  { input: JSON.stringify({ body, options: { stub: true, cacheable: true, ...options } }), encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: H.REPO + '/shared', PYTHONIOENCODING: 'utf-8' } });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
}
const payload = s => /const B64 = "([^"]*)";/.exec(s)[1];
const want = s => /const WANT = "([a-f0-9]{64})";/.exec(s)[1];
const replace = (s, b64) => s.replace(/const B64 = "[^"]*";/, 'const B64 = ' + JSON.stringify(b64) + ';');

test('inline: gzip round trip verifies decompressed bytes and executes both forms', async () => {
  for (const form of ['expression', 'function']) {
    const src = render({ form, args: { n: 7 } });
    assert.deepEqual([...Buffer.from(payload(src), 'base64').subarray(0, 2)], [31, 139]);
    const raw = gunzipSync(Buffer.from(payload(src), 'base64'));
    assert.equal(hash(raw), want(src));
    const e = env();
    assert.equal((await e.runRendered(src)).n, 7);
    assert.equal(e.ctx.__ran, 1);
  }
});

test('inline: cache miss and unreadable storage execute nothing', async () => {
  const src = render({ cache: true });
  for (const store of [storage(), { getItem() { throw Error('blocked'); } }]) {
    const e = env(store);
    const r = await e.runRendered(src);
    assert.deepEqual(JSON.parse(JSON.stringify(r)), { ok: false, need: 'payload', want: want(src).slice(0, 12) });
    assert.equal(e.ctx.__ran, undefined);
    assert.equal(e.ctx.LtGuard, undefined);
  }
});

test('inline: cache hit uses new arguments and stores only compressed code', async () => {
  const e = env();
  await e.runRendered(render({ args: { n: 1, token: 'sink-secret-A' }, policy: { mode: 'block-all', marker: 'policy-secret-A' } }));
  const r = await e.runRendered(render({ cache: true, args: { n: 2, token: 'sink-secret-B' }, policy: { mode: 'block-all', marker: 'policy-secret-B' } }));
  assert.equal(r.n, 2);
  assert.equal(e.ctx.__ran, 2);
  const saved = JSON.stringify([...e.ctx.localStorage.data]);
  assert.ok(!saved.includes('sink-secret'));
  for (const [k, v] of e.ctx.localStorage.data) {
    if (k === INDEX) continue;
    const raw = gunzipSync(Buffer.from(v, 'base64')).toString();
    assert.ok(!raw.includes('sink-secret'));
    assert.ok(!raw.includes('policy-secret'));
  }
  assert.ok(!JSON.stringify(r).includes('sink-secret'));
  assert.equal(e.ctx.__LT_ARGS__, undefined);
});

for (const [name, alter] of [
  ['empty payload', () => ''],
  ['bad base64', () => '!!'],
  ['bad gzip stream', () => Buffer.from('plain').toString('base64')],
  ['truncated payload', b => b.slice(0, -8)],
  ['wrong decompressed hash', b => gzipSync(Buffer.from(gunzipSync(Buffer.from(b, 'base64')).toString().replace('return {ok:true', 'return {ok:false'))).toString('base64')],
  ['invalid UTF8', () => gzipSync(Buffer.from([255])).toString('base64')],
]) {
  test('inline: ' + name + ' refuses stub and evicts cache without execution', async () => {
    const src = render();
    const bad = alter(payload(src));
    const e = env();
    assert.equal((await e.runRendered(replace(src, bad))).fatal, 'stub-hash-mismatch');
    assert.equal(e.ctx.__ran, undefined);
    const key = PREFIX + want(src);
    e.ctx.localStorage.setItem(key, bad);
    e.ctx.localStorage.setItem(INDEX, JSON.stringify([key]));
    const r = await e.runRendered(render({ cache: true }));
    assert.equal(r.need, 'payload');
    assert.equal(e.ctx.localStorage.getItem(key), null);
    assert.deepEqual(JSON.parse(e.ctx.localStorage.getItem(INDEX)), []);
    assert.equal(e.ctx.__ran, undefined);
  });
}

test('inline: missing decompression refuses and legacy uncompressed stub still works', async () => {
  const e = env();
  e.ctx.DecompressionStream = undefined;
  assert.equal((await e.runRendered(render())).fatal, 'stub-no-decompression');
  assert.equal(e.ctx.__ran, undefined);
  assert.equal((await e.runRendered(render({ compress: false }))).ok, true);
  assert.equal(e.ctx.localStorage.length, 0);
});

test('inline: cache hit without decompression refuses before execution', async () => {
  const e = env();
  const src = render();
  e.ctx.localStorage.setItem(PREFIX + want(src), payload(src));
  e.ctx.DecompressionStream = undefined;
  assert.equal((await e.runRendered(render({ cache: true }))).fatal, 'stub-no-decompression');
  assert.equal(e.ctx.__ran, undefined);
});

test('inline: valid hash with invalid UTF8 refuses before compilation', async () => {
  const e = env();
  const bytes = Buffer.from([255]);
  const src = render();
  const bad = replace(src, gzipSync(bytes).toString('base64')).replace(want(src), hash(bytes));
  assert.equal((await e.runRendered(bad)).fatal, 'stub-hash-mismatch');
  assert.equal(e.ctx.__ran, undefined);
});

test('inline: missing crypto refuses before execution and cache returns payload request', async () => {
  const e = env();
  const src = render();
  e.ctx.localStorage.setItem(PREFIX + want(src), payload(src));
  e.ctx.crypto = undefined;
  assert.equal((await e.runRendered(src)).fatal, 'stub-hash-mismatch');
  assert.equal((await e.runRendered(render({ cache: true }))).need, 'payload');
  assert.equal(e.ctx.__ran, undefined);
});

test('inline: blocked writes and quota errors preserve verified execution', async () => {
  const store = storage();
  store.setItem = () => { throw Error('quota'); };
  const e = env(store);
  assert.equal((await e.runRendered(render())).ok, true);
  Object.defineProperty(e.ctx, 'localStorage', { get() { throw Error('blocked'); } });
  assert.equal((await e.runRendered(render())).ok, true);
  assert.equal((await e.runRendered(render({ cache: true }))).need, 'payload');
});

test('inline: cache pruning tracks six entries across targets and repairs lost index', async () => {
  const e = env();
  e.ctx.localStorage.setItem('unrelated', 'keep');
  const keys = [];
  for (let i = 0; i < 8; i++) {
    const src = render({}, BODY + '\n/* variant ' + i + ' */');
    keys.push(PREFIX + want(src));
    await e.runRendered(src);
    const index = JSON.parse(e.ctx.localStorage.getItem(INDEX));
    assert.equal(index.length, Math.min(i + 1, 6));
    assert.ok(index.every(k => e.ctx.localStorage.getItem(k)));
    assert.equal(e.ctx.localStorage.length, index.length + 2);
  }
  assert.equal(e.ctx.localStorage.getItem(keys[0]), null);
  assert.equal(e.ctx.localStorage.getItem(keys[1]), null);
  assert.deepEqual(JSON.parse(e.ctx.localStorage.getItem(INDEX)), keys.slice(2));
  e.ctx.localStorage.setItem(INDEX, 'bad-json');
  await e.runRendered(render({}, BODY + '\n/* another target */'));
  assert.equal(JSON.parse(e.ctx.localStorage.getItem(INDEX)).length, 6);
  assert.equal(e.ctx.localStorage.length, 8);
  assert.equal(e.ctx.localStorage.getItem('unrelated'), 'keep');
});

test('inline: frozen inputs and current guard policy precede cached body execution', async () => {
  const body = 'let blocked=false; try { globalThis.__root.commitClicked(true); } catch(e) {blocked=true;} return {blocked, args:Object.isFrozen(__LT_ARGS__.nested), policy:Object.isFrozen(__LT_POLICY__.never_indexes), n:__LT_ARGS__.nested.n, stop:__LT_POLICY__.stop_before_page};';
  const store = storage();
  for (const [stop, cache] of [[5, false], [3, true]]) {
    const e = env(store);
    e.ctx.__root = e.root;
    const policy = { mode: 'plan', origin: H.DEFAULT_ORIGIN, mapped: true, page_map: {1:1, 2:2, 3:3, 4:4, 5:5}, never_indexes: [], stop_before_page: stop };
    const r = await e.runRendered(render({ cache, args: { nested: { n: stop } }, policy }, body));
    assert.equal(r.blocked, true);
    assert.equal(r.args, true);
    assert.equal(r.policy, true);
    assert.equal(r.n, stop);
    assert.equal(r.stop, stop);
    assert.equal(e.commits.length, 0);
    assert.equal(e.ctx.LtGuard.allowed(H.DEFAULT_ORIGIN + '/i/inst0001/student/courses/1/runs/101/run/page/4').allowed, stop > 4);
  }
});

test('inline: real capture cache excludes sink token and published entry cannot reuse it', async () => {
  const C = require('../capture/vm_harness.js');
  const { demoEnv } = require('../capture/scenarios.js');
  const store = storage();
  for (const cache of [false, true]) {
    const text = render({ cache, args: { transport: 'sink', sink: C.SINK, token: C.TOKEN } }, C.source('capture'));
    const r = await C.runCapture(demoEnv(), { rendered: {text, form:'expression'}, storage: store, secondCall: {module_only:true} });
    assert.equal(r.receipt.ok, true);
    assert.equal(r.secondReceipt.ok, false);
    assert.match(r.secondReceipt.reason, /No sink token/);
    assert.ok(!JSON.stringify([r.receipt, r.secondReceipt]).includes(C.TOKEN));
    assert.ok(!JSON.stringify([...store.data]).includes(C.TOKEN));
    for (const [key, value] of store.data) {
      if (key !== INDEX) assert.ok(!gunzipSync(Buffer.from(value, 'base64')).toString().includes(C.TOKEN));
    }
  }
});

test('inline: published closures release completed run arguments and policy', async () => {
  const e = env();
  const body = 'globalThis.later = () => ({args:typeof __LT_ARGS__, policy:typeof __LT_POLICY__}); return {ok:true};';
  await e.runRendered(render({ args: { token: 'completed-secret' } }, body));
  assert.deepEqual(JSON.parse(JSON.stringify(e.ctx.later())), { args: 'undefined', policy: 'undefined' });
});

function chunks(form = 'expression', bundled = false) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lt-inline-'));
  const source = path.join(dir, 'probe.js');
  fs.writeFileSync(source, BODY);
  const config = path.join(dir, 'config.toml');
  fs.writeFileSync(config, '');
  const target = bundled ? path.join(H.SHARED_BROWSER, 'guard.js') : source;
  const p = cp.spawnSync(process.env.PYTHON || 'python3', ['-m', 'ltcore.ltjs', 'render', target,
    '--stub', '--form', form, '--chunk-bytes', '2000', '--out-dir', path.join(dir, 'out')],
  { encoding: 'utf8', env: { ...process.env, LT_SUITE_CONFIG: config, PYTHONPATH: H.REPO + '/shared', PYTHONIOENCODING: 'utf-8' } });
  assert.equal(p.status, 0, p.stderr);
  return { dir, info: JSON.parse(p.stdout) };
}

test('inline: chunk assembly checks receipts and clears payload after execution', async () => {
  for (const form of ['expression', 'function']) {
    const { dir, info } = chunks(form, true);
    try {
      assert.ok(info.chunks.length > 1);
      const e = env();
      for (const part of info.chunks) {
        const r = await e.runRendered(fs.readFileSync(part.path, 'utf8'));
        assert.deepEqual(JSON.parse(JSON.stringify(r)), { i: part.i, len: part.len, sha16: part.sha16 });
        assert.equal(e.ctx.__ran, undefined);
      }
      assert.equal((await e.runRendered(fs.readFileSync(info.path, 'utf8'))).ok, true);
      assert.equal(e.ctx.__ltSuitePayload[info.body_sha256], undefined);
      assert.ok(e.ctx.localStorage.getItem(PREFIX + info.body_sha256));
    } finally { fs.rmSync(dir, { recursive: true }); }
  }
});

test('inline: wrong chunk and missing chunk refuse without body execution', async () => {
  const { dir, info } = chunks();
  try {
    const e = env();
    const first = fs.readFileSync(info.chunks[0].path, 'utf8');
    const bad = first.replace(/B64 = "./, 'B64 = "!');
    assert.equal((await e.runRendered(bad)).fatal, 'stub-chunk-mismatch');
    assert.equal((await e.runRendered(fs.readFileSync(info.path, 'utf8'))).fatal, 'stub-hash-mismatch');
    await e.runRendered(first);
    assert.equal((await e.runRendered(fs.readFileSync(info.path, 'utf8'))).fatal, 'stub-hash-mismatch');
    assert.equal(e.ctx.__ltSuitePayload[info.body_sha256], undefined);
    assert.equal(e.ctx.__ran, undefined);
    assert.equal(e.ctx.localStorage.length, 0);
  } finally { fs.rmSync(dir, { recursive: true }); }
});

test('inline: reordered chunks and changed global assembly refuse and clear bytes', async () => {
  const { dir, info } = chunks();
  try {
    const e = env();
    assert.equal((await e.runRendered(fs.readFileSync(info.chunks[1].path, 'utf8'))).fatal, 'stub-chunk-mismatch');
    for (const part of info.chunks) await e.runRendered(fs.readFileSync(part.path, 'utf8'));
    e.ctx.__ltSuitePayload[info.body_sha256][0] = 'AAAA';
    assert.equal((await e.runRendered(fs.readFileSync(info.path, 'utf8'))).fatal, 'stub-hash-mismatch');
    assert.equal(e.ctx.__ltSuitePayload[info.body_sha256], undefined);
    assert.equal(e.ctx.__ran, undefined);
  } finally { fs.rmSync(dir, { recursive: true }); }
});
