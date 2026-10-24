// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Tests for upload.js: the panel's own file input only, exactly one of it, bytes checked by
 * sha256, `change` dispatched, and never a click. A small fake DOM with Knockout's dataFor.
 */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const F = require('./fill_env.js');

const SRC = fs.readFileSync(path.join(F.BROWSER, 'upload.js'), 'utf8');
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d5a1300000000049454e44ae426082', 'hex');
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const fileArg = (o) => Object.assign({ name: 'p4-15.png', type: 'image/png', bytes: PNG.length, sha256: sha(PNG), b64: PNG.toString('base64') }, o || {});

/* Elements: a tiny tree with parentElement, querySelectorAll and event spies. */
function dom() {
  const all = [];
  const events = [];
  let clicks = 0;
  const el = (tag, attrs, data) => {
    const e = { tag, attrs: attrs || {}, children: [], parentElement: null, __data: data, _value: 'C:\\fakepath\\old.png', _files: null };
    e.appendChild = c => { c.parentElement = e; e.children.push(c); all.push(c); return c; };
    e.querySelectorAll = sel => {
      const out = [];
      const walk = n => { for (const c of n.children) { if (sel === 'input[type=file]' && c.tag === 'input' && c.attrs.type === 'file') out.push(c); walk(c); } };
      walk(e);
      return out;
    };
    e.scrollIntoView = () => {};
    e.click = () => { clicks++; };
    e.dispatchEvent = ev => { events.push({ el: e, type: ev.type }); if (ev.type === 'click') clicks++; return true; };
    Object.defineProperty(e, 'value', { get: () => e._value, set: v => { e._value = v; e.valueCleared = v === ''; } });
    Object.defineProperty(e, 'files', { get: () => e._files, set: v => { e._files = v; } });
    return e;
  };
  const body = el('body');
  const d = { all, events, el, body, clicks: () => clicks };
  return d;
}
const panelVm = key => ({ attributes: { panelKey: key, pluginKey: 'image-annotation' } });

function uploadEnv(build, o) {
  o = o || {};
  const d = dom();
  build(d);
  let dialog = null;
  class DataTransfer { constructor() { this._f = []; this.items = { add: f => this._f.push(f) }; } get files() { return Object.assign(this._f.slice(), { item: i => this._f[i] }); } }
  class FakeFile { constructor(parts, name, opts) { this.parts = parts; this.name = name; this.type = (opts || {}).type; this.size = parts.reduce((s, p) => s + p.length, 0); } }
  class Event { constructor(type, opts) { this.type = type; this.bubbles = !!(opts && opts.bubbles); } }
  const ctx = {
    console, atob: s => Buffer.from(String(s), 'base64').toString('latin1'), crypto: crypto.webcrypto,
    location: { pathname: o.path || F.RUN_PATH(4), origin: F.ORIGIN },
    document: {
      querySelectorAll: s => (s === '*' ? [d.body].concat(d.all) : []),
      querySelector: s => (s === '[role=dialog]' ? dialog : null)
    },
    ko: {
      isObservable: v => typeof v === 'function' && v.__obs === true,
      dataFor: e => { let n = e; while (n) { if (n.__data !== undefined) return n.__data; n = n.parentElement; } return undefined; }
    },
    DataTransfer, File: FakeFile, Event,
    __ltClock: { now: () => 0, sleep: async () => { if (o.dialogOnChange && d.events.some(e => e.type === 'change')) dialog = { getClientRects: () => [{}] }; } }
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  d.ctx = ctx;
  d.run = args => vm.runInContext('(async (__LT_ARGS__) => {\n' + SRC + '\n})', ctx)(args);
  return d;
}

/* Page: panel 14 (one input), panel 15 (the target, one input), panel 16 (two inputs), panel 17 (none). */
const build = d => {
  for (const [key, n] of [['14', 1], ['15', 1], ['16', 2], ['17', 0]]) {
    const root = d.body.appendChild(d.el('div', { class: 'panel' }, panelVm(key)));
    const inner = root.appendChild(d.el('div', {}));
    for (let i = 0; i < n; i++) inner.appendChild(d.el('input', { type: 'file', class: 'imageUploadInput' }));
    d['p' + key] = root;
  }
};
const inputOf = (d, key) => d['p' + key].querySelectorAll('input[type=file]')[0];

test('upload: sets the files of the panel\'s own input and dispatches change', async () => {
  const d = uploadEnv(build);
  const r = await d.run({ panel: '15', file: fileArg() });
  assert.equal(r.ok, true);
  assert.equal(r.sha256, sha(PNG));
  assert.equal(r.bytes, PNG.length);
  const inp = inputOf(d, '15');
  assert.equal(inp.files.length, 1);
  assert.equal(inp.files[0].name, 'p4-15.png');
  assert.deepEqual(d.events.map(e => [e.el === inp, e.type]), [[true, 'change']]);
});

test('upload: the other panels\' inputs stay untouched', async () => {
  const d = uploadEnv(build);
  await d.run({ panel: '15', file: fileArg() });
  assert.equal(inputOf(d, '14').files, null);
});

test('upload: input.value is cleared first, so a repeated file still fires change', async () => {
  const d = uploadEnv(build);
  await d.run({ panel: '15', file: fileArg() });
  assert.equal(inputOf(d, '15').valueCleared, true);
});

test('upload: never clicks, never dispatches a click', async () => {
  const d = uploadEnv(build);
  await d.run({ panel: '15', file: fileArg() });
  assert.equal(d.clicks(), 0);
  assert.equal(d.events.filter(e => e.type === 'click').length, 0);
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!/\.click\s*\(/.test(code), 'upload.js code holds no .click(');
});

test('upload: refuses a panel with two file inputs', async () => {
  const d = uploadEnv(build);
  const r = await d.run({ panel: '16', file: fileArg() });
  assert.equal(r.fatal, 'several-file-inputs');
  assert.equal(d.events.length, 0);
});

test('upload: refuses a panel with no file input', async () => {
  const d = uploadEnv(build);
  const r = await d.run({ panel: '17', file: fileArg() });
  assert.equal(r.fatal, 'no-file-input');
});

test('upload: refuses a panel key that is not on the page', async () => {
  const d = uploadEnv(build);
  const r = await d.run({ panel: '99', file: fileArg() });
  assert.equal(r.fatal, 'no-panel-root');
});

test('upload: refuses bytes whose sha256 differs from the embedded one', async () => {
  const d = uploadEnv(build);
  const r = await d.run({ panel: '15', file: fileArg({ sha256: sha(Buffer.from('other')) }) });
  assert.equal(r.fatal, 'bytes-hash-mismatch');
  assert.equal(inputOf(d, '15').files, null);
});

test('upload: refuses a file that is not PNG or JPEG', async () => {
  const d = uploadEnv(build);
  const r = await d.run({ panel: '15', file: fileArg({ type: 'image/svg+xml' }) });
  assert.equal(r.fatal, 'not-png-or-jpeg');
});

test('upload: refuses to run off a run page', async () => {
  const d = uploadEnv(build, { path: '/i/inst0001/student/courses/1/runs/101/run/complete' });
  const r = await d.run({ panel: '15', file: fileArg() });
  assert.equal(r.fatal, 'not-a-run-page');
});

test('upload: reports Lt\'s replace dialog for confirm_dialog.js', async () => {
  const d = uploadEnv(build, { dialogOnChange: true });
  const r = await d.run({ panel: '15', file_name: 'x.png', file_type: 'image/png', file_b64: PNG.toString('base64'), file_sha256: sha(PNG) });
  assert.equal(r.ok, true);
  assert.equal(r.dialog_open, true);
  assert.equal(r.next, 'confirm_dialog action=accept panel=15');
  assert.deepEqual(JSON.parse(JSON.stringify(d.ctx.__ltPendingUploads.map(p => [p.panel, p.state]))), [['15', 'pending']]);
});

test('upload: a second upload of one panel supersedes its first pending record', async () => {
  const d = uploadEnv(build);
  await d.run({ panel: '15', file: fileArg() });
  await d.run({ panel: '15', file: fileArg() });
  await d.run({ panel: '14', file: fileArg() });
  assert.deepEqual(JSON.parse(JSON.stringify(d.ctx.__ltPendingUploads.map(p => [p.panel, p.state]))),
    [['15', 'superseded'], ['15', 'pending'], ['14', 'pending']]);
});
