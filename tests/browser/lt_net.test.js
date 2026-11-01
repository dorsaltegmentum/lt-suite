// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* lt_net.js tests: the save watcher proves each write's own save, by key, on a fake clock.
 *
 * Cases 1 to 9 are the list in the design (calibrated XHR, Backbone sync, uncalibrated old rule,
 * a save that never ends, status 500, quiet window, GET poll, request before the write, double
 * install). The rest cover false acknowledgements found in review: stale Backbone syncs, a
 * shared parent, answer text naming another key, 202, stale payloads, several models under one
 * key, hidden tabs, service workers, and the harness's own lost-save detector.
 *
 *     node --test tests/browser/lt_net.test.js
 */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const H = require('./harness.js');

const K1 = '3f2a9c1e-0b4d-4e8f-9a71-2c5d8e0f1a2b';
const K2 = '7c1d0e2f-5a6b-4c8d-8e9f-0a1b2c3d4e5f';
const TPL = '/api/panels/{uuid}/state';
const CAL = { source: 'net', method: 'PUT', path_template: TPL };
const OPTS = { quiet_ms: 300, max_ms: 20000, fallback_ms: 4000 };
const cal = extra => Object.assign({}, OPTS, { calibration: CAL }, extra || {});
const MODEL_CAL = Object.assign({}, OPTS, { calibration: { source: 'model', method: 'PUT', path_template: TPL } });

function setup(o) {
  const env = H.makeEnv(o);
  env.loadShared('lt_net');
  env.ctx.LtNet.install();
  return { env, N: env.ctx.LtNet, t0: env.clock.now() };
}
const put = (env, key, body, o) => env.request('PUT', '/api/panels/' + key + '/state', body, o);
/* `leaf` under n levels of {details: ...}. */
function deepNest(n, leaf) {
  let v = leaf;
  for (let i = 0; i < n; i++) v = { details: v };
  return v;
}

test('lt_net: 1 calibrated XHR save naming the key, 150 to 950 ms, ends ok at 1250 ms with signal net', async () => {
  const { env, N, t0 } = setup();
  const m = env.model(K1, { strategy: 'debounced', debounce: 150, latency: 800, events: false });
  N.expect(K1);
  m.set('answer', '<p>x</p>');
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'net');
  assert.equal(r.state, 'ok');
  assert.equal(r.save_confirmed, true);
  assert.equal(r.waited_ms, 1250);
  assert.equal(r.keys_saved, 1);
  assert.equal(r.keys_pending, 0);
  assert.equal(r.ack_net, 1);
  assert.equal(r.ack_method, 'PUT');
  assert.equal(r.ack_path_template, TPL);
  assert.deepEqual(JSON.parse(JSON.stringify(r.acks)), [{ source: 'net', method: 'PUT', path_template: TPL }]);
  assert.equal(env.clock.now() - t0, 1250);
  assert.equal(env.navigate().lost, 0);
});

test('lt_net: 2 Backbone sync correlated with a validated write ends ok with signal model', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', latency: 400 });
  N.expect(K1, m);
  m.set('answer', 'x');
  assert.equal(env.requests.filter(q => q.kind === 'xhr').length, 0, 'save is scheduled after set');
  const r = await N.waitForSave([K1], MODEL_CAL);
  assert.equal(r.save_signal, 'model');
  assert.equal(r.ack_model, 1);
  assert.equal(r.ack_source, 'model');
  assert.equal(r.waited_ms, 700);
  assert.equal(env.navigate().lost, 0);
});

test('lt_net: 3 uncalibrated keeps the old rule: at least 4000 ms and jQuery.active back at base', async () => {
  const { env, N, t0 } = setup();
  const m = env.model(K1, { strategy: 'immediate', latency: 150 });
  N.expect(K1, m);
  m.set('answer', 'x');
  env.jq(2);
  env.at(t0 + 5000, () => env.jq(1));
  const r = await N.waitForSave([K1], OPTS);
  assert.equal(r.save_signal, 'fallback');
  assert.equal(r.save_confirmed, false);
  assert.equal(r.waited_ms, 5000);
  assert.equal(r.jq, '1/2');
  assert.equal(r.calibrated, false);
  assert.equal(r.ack_source, 'model', 'acknowledgements are still recorded for calibration');

  const b = setup();
  const m2 = b.env.model(K2, { strategy: 'immediate', latency: 150 });
  b.N.expect(K2, m2);
  m2.set('answer', 'y');
  const r2 = await b.N.waitForSave([K2], OPTS);
  assert.equal(r2.save_signal, 'fallback');
  assert.equal(r2.waited_ms, 4000, 'never before fallback_ms, even with the save acknowledged at 150 ms');
});

test('lt_net: 4 a save request that never ends returns unconfirmed at max_ms from the last write', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', status: null, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.waited_ms, 20000);
  assert.equal(r.keys_pending, 1);
  assert.deepEqual([...r.pending_keys], [K1]);
  assert.equal(env.navigate().lost, 1, 'the harness sees the lost write the wait refused to hide');
});

test('lt_net: 5 status 500 on the key\'s own request returns error', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', status: 500, latency: 200, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'error');
  assert.equal(r.state, 'error');
  assert.equal(r.save_errors, 1);
  assert.deepEqual([...r.failed_keys], [K1]);
  assert.ok(r.waited_ms <= 250, `error is reported when it happens, got ${r.waited_ms} ms`);
});

test('lt_net: 6 a second key\'s request starting inside the quiet window extends the wait past its end', async () => {
  const { env, N, t0 } = setup();
  const m1 = env.model(K1, { strategy: 'debounced', debounce: 150, latency: 800, events: false });
  N.expect(K1);
  m1.set('answer', 'x');
  env.at(t0 + 1100, () => put(env, K2, JSON.stringify({ uuid: K2 }), { latency: 500 }));
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'net');
  assert.equal(r.waited_ms, 1900, 'quiet counts from the end of the second request (1600 ms)');
});

test('lt_net: 7 a GET poll whose URL names the key never acknowledges it', async () => {
  const { env, N, t0 } = setup();
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'x');
  env.at(t0 + 100, () => env.request('GET', '/api/panels/' + K1 + '/state?poll=1', null, { latency: 100 }));
  env.at(t0 + 300, () => env.request('GET', '/api/poll?key=' + K1, null, { latency: 100, via: 'fetch' }));
  const r = await N.waitForSave([K1], cal({ max_ms: 3000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.ack_net, 0);
  assert.equal(r.keys_pending, 1);
  assert.equal(N.state().diag.non_get, 0);
  assert.equal(N.state().diag.requests, 2);
});

test('lt_net: 8 a request that started before the write and names the key is ignored', async () => {
  const { env, N, t0 } = setup();
  put(env, K1, JSON.stringify({ uuid: K1, value: 'old' }), { latency: 400 });
  await env.clock.advance(100);
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'new');
  const r = await N.waitForSave([K1], cal({ max_ms: 2000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.ack_net, 0);
  assert.equal(env.clock.now() - t0, 2100);
});

test('lt_net: 9 install() twice, and the file loaded twice, keep one wrapper layer', async () => {
  const { env, N } = setup();
  const send = env.ctx.XMLHttpRequest.prototype.send;
  const fetch = env.ctx.fetch;
  N.install();
  env.loadShared('lt_net');
  env.ctx.LtNet.install();
  assert.equal(env.ctx.XMLHttpRequest.prototype.send, send);
  assert.equal(env.ctx.fetch, fetch);
  assert.equal(env.ctx.LtNet, N);
  env.request('PUT', '/api/other', '{}', { latency: 10 });
  await env.clock.advance(20);
  assert.equal(N.state().diag.requests, 1, 'one request counted once');
  assert.equal(N.state().installed, true);
  assert.equal(env.ctx.__ltNet.installed, true);
});

test('lt_net: deadline counts from the last expect, so a late write moves it', async () => {
  const { env, N, t0 } = setup();
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'x');
  await env.clock.advance(5000);
  N.expect(K2);
  env.model(K2, { strategy: 'never' }).set('answer', 'y');
  const r = await N.waitForSave([K1, K2], cal());
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(env.clock.now() - t0, 25000);
  assert.equal(r.waited_ms, 20000);
  assert.equal(r.keys_total, 2);
});

test('lt_net: fetch save acknowledged through the fetch hook', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', latency: 250, via: 'fetch', events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'net');
  assert.equal(r.waited_ms, 550);
});

test('lt_net: fetch reference captured before install, with no model, never ends early', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', latency: 250, via: 'captured-fetch', events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r1 = await N.waitForSave([K1], cal());
  assert.equal(r1.save_signal, 'unconfirmed');
  const b = setup();
  const m2 = b.env.model(K1, { strategy: 'immediate', latency: 250, via: 'captured-fetch', events: false });
  b.N.expect(K1);
  m2.set('answer', 'x');
  const r2 = await b.N.waitForSave([K1], OPTS);
  assert.equal(r2.save_signal, 'fallback');
  assert.equal(r2.waited_ms, 4000);
});

test('lt_net: a WebSocket send marks the key sent and never saved', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', via: 'ws', events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal({ max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(N.state().diag.ws_sends, 1);
  assert.equal(r.keys_pending, 1);
});

test('lt_net: a short key is never acknowledged by the network', async () => {
  const { env, N } = setup();
  const m = env.model('12', { strategy: 'immediate', latency: 100, events: false, path: '/api/panels/12/state' });
  N.expect('12');
  m.set('answer', 'x');
  const r = await N.waitForSave(['12'], cal({ max_ms: 1500, calibration: { source: 'net', method: 'PUT', path_template: '/api/panels/{n}/state' } }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.keys_weak, 1);
});

test('lt_net: a save to another path template does not acknowledge a calibrated key', async () => {
  const { env, N } = setup();
  env.request('POST', '/api/telemetry/1', JSON.stringify({ panel: K1 }), { latency: 1 });
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'x');
  env.request('POST', '/api/telemetry/1', JSON.stringify({ panel: K1 }), { latency: 100 });
  const r = await N.waitForSave([K1], cal({ max_ms: 1500 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.ack_source, 'net', 'the acknowledgement is recorded for calibration');
  assert.equal(r.ack_path_template, '/api/telemetry/{n}');
});

test('lt_net: a Backbone fetch (visible GET plus request and sync) is not a save', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'never' });
  N.expect(K1, m);
  m.set('answer', 'x');
  const bb = {};
  env.request('GET', '/api/panels/' + K1 + '/state', null, { latency: 50 });
  m.trigger('request', m, {}, bb);
  await env.clock.advance(60);
  m.trigger('sync', m, {}, bb);
  const r = await N.waitForSave([K1], Object.assign({}, MODEL_CAL, { max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.ack_model, 0);
});

test('lt_net: a sync for a request from before the latest write does not acknowledge it', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'never' });
  N.expect(K1, m);
  m.set('answer', 'v1');
  const first = {};
  m.trigger('request', m, {}, first);
  await env.clock.advance(100);
  N.expect(K1, m);
  m.set('answer', 'v2');
  const second = {};
  m.trigger('request', m, {}, second);
  m.trigger('sync', m, {}, first);
  const r = await N.waitForSave([K1], Object.assign({}, MODEL_CAL, { max_ms: 500 }));
  assert.equal(r.save_signal, 'unconfirmed', 'the old request\'s sync proves nothing about v2');
  const c = setup();
  const m2 = c.env.model(K2, { strategy: 'never' });
  c.N.expect(K2, m2);
  m2.set('answer', 'v');
  m2.trigger('sync', m2, {}, {});
  const r2 = await c.N.waitForSave([K2], Object.assign({}, MODEL_CAL, { max_ms: 500 }));
  assert.equal(r2.save_signal, 'unconfirmed', 'a sync with no matching request is ignored');
});

test('lt_net: a parent shared by two keys acknowledges neither', async () => {
  const { env, N } = setup();
  const parent = env.model('parent-model-x', { strategy: 'never' });
  const c1 = env.model(K1, { strategy: 'never' });
  const c2 = env.model(K2, { strategy: 'never' });
  N.expect(K1, c1, parent);
  c1.set('answer', 'a');
  N.expect(K2, c2, parent);
  c2.set('answer', 'b');
  const bb = {};
  parent.trigger('request', parent, {}, bb);
  parent.trigger('sync', parent, {}, bb);
  const r = await N.waitForSave([K1, K2], Object.assign({}, MODEL_CAL, { max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.ack_model, 0);
  assert.match(r.net_diag, /shared_model=[1-9]/);
});

test('lt_net: a parent sync after the write acknowledges a key it alone carries', async () => {
  const { env, N } = setup();
  const parent = env.model('parent-model-y', { strategy: 'never' });
  const c1 = env.model(K1, { strategy: 'never' });
  N.expect(K1, c1, parent);
  c1.set('answer', 'a');
  const bb = {};
  const xhr = env.request('PUT', '/api/parent/1', '{}', { latency: 200 });
  parent.trigger('request', parent, xhr, bb);
  await env.clock.advance(200);
  parent.trigger('sync', parent, {}, bb);
  const r = await N.waitForSave([K1], cal({ calibration: {
    source: 'model', method: 'PUT', path_template: '/api/parent/{n}' } }));
  assert.equal(r.save_signal, 'model');
  assert.equal(r.ack_method, 'PUT');
  assert.equal(r.ack_path_template, '/api/parent/{n}');
});

test('lt_net: several models written under one key all need their own correlated sync', async () => {
  const opts = cal({ max_ms: 600, calibration: { source: 'model', method: 'PUT', path_template: '/api/cells/{n}' } });
  for (const all of [false, true]) {
    const { env, N } = setup();
    const c1 = env.model('cell-model-1', { strategy: 'never' });
    const c2 = env.model('cell-model-2', { strategy: 'never' });
    N.expect(K1, c1);
    c1.set('content', 'a');
    N.expect(K1, c2);
    c2.set('content', 'b');
    for (const [cell, path] of (all ? [[c1, '/api/cells/1'], [c2, '/api/cells/2']] : [[c2, '/api/cells/2']])) {
      const options = {};
      const xhr = env.request('PUT', path, '{}', { latency: 100 });
      cell.trigger('request', cell, xhr, options);
      await env.clock.advance(100);
      cell.trigger('sync', cell, {}, options);
    }
    const r = await N.waitForSave([K1], opts);
    assert.equal(r.save_signal, all ? 'model' : 'unconfirmed');
  }
});

test('lt_net: answer text that mentions another key does not acknowledge it', async () => {
  const { env, N } = setup();
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'x');
  N.expect(K2);
  put(env, K2, JSON.stringify({ uuid: K2, answer: '<p>see ' + K1 + '</p>' }), { latency: 100 });
  const r = await N.waitForSave([K1, K2], cal({ max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.keys_saved, 1);
  assert.deepEqual([...r.pending_keys], [K1]);
});

test('lt_net: the key in the URL path alone names the request', async () => {
  const { env, N } = setup();
  N.expect(K1);
  put(env, K1, JSON.stringify({ answer: 'x' }), { latency: 100 });
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'net');
  const b = setup();
  b.N.expect(K1);
  b.env.request('PUT', '/api/panels/x/state', 'uuid=' + K1 + '&answer=y', { latency: 100 });
  const r2 = await b.N.waitForSave([K1], cal({ calibration: { source: 'net', method: 'PUT', path_template: '/api/panels/x/state' } }));
  assert.equal(r2.save_signal, 'net', 'a form-encoded body names the key too');
});

test('lt_net: HTTP 202 leaves the key pending', async () => {
  const { env, N } = setup();
  N.expect(K1);
  put(env, K1, JSON.stringify({ uuid: K1 }), { latency: 100, status: 202 });
  const r = await N.waitForSave([K1], cal({ max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.keys_pending, 1);
});

test('lt_net: expect(..., {contains}) refuses a payload without the written value', async () => {
  const { env, N } = setup();
  N.expect(K1, null, null, { contains: 'NEW-VALUE' });
  put(env, K1, JSON.stringify({ uuid: K1, answer: '<p>OLD-VALUE</p>' }), { latency: 100 });
  const r = await N.waitForSave([K1], cal({ max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  const b = setup();
  b.N.expect(K1, null, null, { contains: 'NEW-VALUE' });
  put(b.env, K1, JSON.stringify({ uuid: K1, answer: '<p>NEW-VALUE</p>' }), { latency: 100 });
  const r2 = await b.N.waitForSave([K1], cal());
  assert.equal(r2.save_signal, 'net');
});

test('lt_net: a page hidden during a fallback wait returns unconfirmed', async () => {
  const { env, N, t0 } = setup();
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'x');
  env.at(t0 + 1000, () => env.setHidden(true));
  env.at(t0 + 2000, () => env.setHidden(false));
  const r = await N.waitForSave([K1], OPTS);
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.hidden_during_wait, true);
  assert.equal(r.waited_ms, 4000);
});

test('lt_net: a service worker controlling the page forces the fallback rule', async () => {
  const { env, N } = setup({ serviceWorker: true });
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'fallback');
  assert.equal(r.sw_controlled, true);
  assert.equal(r.calibrated, false);
  assert.equal(r.waited_ms, 4000);
});

test('lt_net: the whole calibration record for an origin is accepted', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], Object.assign({}, OPTS, { calibration: { save: CAL, dialog: {}, cross_page: {} } }));
  assert.equal(r.save_signal, 'net');
  assert.equal(r.calibrated, true);
});

test('lt_net: request bodies are never kept in state or receipts', async () => {
  const { env, N } = setup();
  const secret = 'BODY-SECRET-' + 'x'.repeat(8);
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  N.expect(K1);
  m.set('answer', secret);
  const r = await N.waitForSave([K1], cal());
  const dump = JSON.stringify(r) + JSON.stringify(N.state()) + JSON.stringify(N.pendingKeys());
  assert.ok(!dump.includes(secret));
  assert.ok(!Object.getOwnPropertyNames(env.ctx.__ltNet).some(k => JSON.stringify(env.ctx.__ltNet[k]).includes(secret)));
});

test('lt_net: PerformanceObserver entries reach the diagnostic count only', async () => {
  const { env, N } = setup();
  N.expect(K1);
  env.model(K1, { strategy: 'never' }).set('answer', 'x');
  env.perf(7);
  const r = await N.waitForSave([K1], cal({ max_ms: 500 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.match(r.net_diag, /perf=7/);
});

test('lt_net: path templates type UUIDs, long hex ids and digit runs', () => {
  const { N } = setup();
  assert.equal(N.pathTemplate('/api/v2/panels/' + K1 + '/state'), '/api/v{n}/panels/{uuid}/state');
  assert.equal(N.pathTemplate('/x/5f3c2b1a9e/y/12'), '/x/{hex}/y/{n}');
  assert.equal(N.pathTemplate('/runs/101/pages/7/commit'), '/runs/{n}/pages/{n}/commit');
  assert.equal(N.pathTemplate('/x/acceded/y'), '/x/acceded/y', 'a word made of hex letters stays');
});

test('lt_net: harness detects lost saves (20 debounced writes, navigate at once vs after the wait)', async () => {
  const { env, N } = setup();
  const keys = [];
  for (let i = 0; i < 20; i++) {
    const k = K1.slice(0, -2) + String(i).padStart(2, '0');
    keys.push(k);
    const m = env.model(k, { strategy: 'debounced', debounce: 600, latency: 200 + i * 100 });
    N.expect(k, m);
    m.set('answer', 'v' + i);
  }
  const r = await N.waitForSave(keys, cal());
  assert.equal(r.save_signal, 'model');
  assert.equal(r.keys_saved, 20);
  assert.equal(env.navigate().lost, 0);

  const b = setup();
  for (let i = 0; i < 20; i++) {
    const k = K2.slice(0, -2) + String(i).padStart(2, '0');
    b.env.model(k, { strategy: 'debounced', debounce: 600, latency: 300 }).set('answer', 'v' + i);
  }
  assert.equal(b.env.navigate().lost, 20, 'control: navigating at once loses every write');
});

test('lt_net: waitForSave with no keys in a fresh document returns at once', async () => {
  const { N } = setup();
  const r = await N.waitForSave([], cal());
  assert.equal(r.save_signal, 'none');
  assert.equal(r.waited_ms, 0);
  assert.equal(N.lastWrite(), null);
  assert.deepEqual([...N.pendingKeys()], []);
});

test('lt_net: a request naming 2 keys saves only the keys its response names', async () => {
  for (const via of ['xhr', 'fetch']) {
    const { env, N } = setup();
    N.expect(K1);
    N.expect(K2);
    const body = JSON.stringify({ items: [{ uuid: K1, answer: 'a' }, { uuid: K2, answer: 'b' }] });
    const tpl = { source: 'net', method: 'POST', path_template: '/api/panels/batch' };
    env.request('POST', '/api/panels/batch', body, { latency: 100, via, response: JSON.stringify({ saved: [K1] }) });
    const r = await N.waitForSave([K1, K2], cal({ max_ms: 1500, calibration: tpl }));
    assert.equal(r.save_signal, 'unconfirmed', via);
    assert.equal(r.keys_saved, 1, via);
    assert.deepEqual([...r.pending_keys], [K2], via);
    assert.match(r.net_diag, /multi_key=1/);
    const b = setup();
    b.N.expect(K1);
    b.N.expect(K2);
    b.env.request('POST', '/api/panels/batch', body, { latency: 100, via, response: JSON.stringify({ saved: [K1, K2] }) });
    const r2 = await b.N.waitForSave([K1, K2], cal({ calibration: tpl }));
    assert.equal(r2.save_signal, 'net', via);
    assert.equal(r2.keys_saved, 2, via);
  }
});

test('lt_net: a request naming 2 keys with an empty response saves neither', async () => {
  const { env, N } = setup();
  N.expect(K1);
  N.expect(K2);
  env.request('PUT', '/api/panels/batch', JSON.stringify([K1, K2]), { latency: 100 });
  const r = await N.waitForSave([K1, K2], cal({ max_ms: 1000, calibration: { source: 'net', method: 'PUT', path_template: '/api/panels/batch' } }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.keys_saved, 0);
});

test('lt_net: a calibration bound to another Lt client is stale and the old rule runs', async () => {
  const scripts = ['/static/app.3f9a1c.js?v=1', 'https://cdn.example.invalid/lib/vendor.js'];
  const { env, N } = setup({ scripts });
  const sig = N.clientSig();
  assert.match(sig, /^c2-2-[0-9a-f]{8}$/);
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal({ calibration: Object.assign({}, CAL, { client_sig: sig }) }));
  assert.equal(r.save_signal, 'net');
  assert.equal(r.client_sig, sig);
  assert.equal(r.calibration_stale, false);

  const b = setup({ scripts: ['/static/app.77aa01.js', 'https://cdn.example.invalid/lib/vendor.js'] });
  const m2 = b.env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  b.N.expect(K1);
  m2.set('answer', 'x');
  const r2 = await b.N.waitForSave([K1], cal({ calibration: Object.assign({}, CAL, { client_sig: sig }) }));
  assert.equal(r2.save_signal, 'fallback');
  assert.equal(r2.calibration_stale, true);
  assert.equal(r2.calibrated, false);
  assert.equal(r2.waited_ms, 4000);

  const c = setup();
  c.N.expect(K1);
  c.env.model(K1, { strategy: 'immediate', latency: 100, events: false }).set('answer', 'x');
  const r3 = await c.N.waitForSave([K1], cal({ calibration: Object.assign({}, CAL, { version: 2 }) }));
  assert.equal(r3.save_signal, 'fallback');
  assert.equal(r3.calibration_stale, true);
  assert.equal(r3.client_sig, null);
});

// Dropping the query let /core.js?v=2 keep the calibration made on /core.js?v=1.
test('lt_net: a core bundle versioned by its query string signs as a new build and drops the calibration', async () => {
  const v1 = setup({ scripts: ['/core.js?v=1', 'https://cdn.example.invalid/lib/vendor.js'] });
  const sig = v1.N.clientSig();
  assert.match(sig, /^c2-2-[0-9a-f]{8}$/);
  assert.equal(setup({ scripts: ['/core.js?v=1', 'https://cdn.example.invalid/lib/vendor.js'] }).N.clientSig(), sig,
    'the same query signs the same');
  const { env, N } = setup({ scripts: ['/core.js?v=2', 'https://cdn.example.invalid/lib/vendor.js'] });
  assert.notEqual(N.clientSig(), sig);
  assert.equal(N.scriptsSig(), v1.N.scriptsSig(), 'the diagnostic fingerprint still drops the query');
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal({ calibration: Object.assign({}, CAL, { client_sig: sig }) }));
  assert.equal(r.calibration_stale, true);
  assert.equal(r.calibrated, false);
  assert.equal(r.save_signal, 'fallback');
});

test('lt_net: page chunks keep the client signature and change only the script fingerprint', () => {
  const common = ['/static/app.3f9a1c.js', '/static/vendor.123abc.js'];
  const first = setup({scripts: common});
  const sig = first.N.clientSig();
  const page = first.N.scriptsSig();
  assert.match(sig, /^c2-2-[0-9a-f]{8}$/);
  assert.match(page, /^c1-2-[0-9a-f]{8}$/);
  first.env.addChunks(['/static/page-a.111aaa.js']);
  assert.equal(first.N.clientSig(), sig, 'a chunk loaded after DOMContentLoaded is not the build');
  assert.notEqual(first.N.scriptsSig(), page, 'the diagnostic fingerprint sees the chunk');
  const second = setup({scripts: common});
  second.env.addChunks(['/static/page-b.222bbb.js', '/static/lazy.333ccc.js']);
  assert.equal(second.N.clientSig(), sig, 'another page of the same build signs the same');
  assert.notEqual(second.N.scriptsSig(), first.N.scriptsSig());
  const ordered = setup({scripts: [...common].reverse()});
  assert.equal(ordered.N.clientSig(), sig, 'script order does not matter');
});

test('lt_net: a changed core bundle changes the client signature', () => {
  const sig = setup({scripts: ['/static/app.3f9a1c.js', '/static/vendor.123abc.js']}).N.clientSig();
  const rebuilt = setup({scripts: ['/static/app.77aa01.js', '/static/vendor.123abc.js']});
  assert.notEqual(rebuilt.N.clientSig(), sig);
  rebuilt.env.addChunks(['/static/app.3f9a1c.js']);
  assert.notEqual(rebuilt.N.clientSig(), sig, 'the old bundle loaded late does not restore the old build');
});

test('lt_net: without navigation timing the page has no client signature', () => {
  const { N } = setup({scripts: ['/static/app.3f9a1c.js'], timing: false});
  assert.equal(N.clientSig(), null);
  assert.match(N.scriptsSig(), /^c1-1-[0-9a-f]{8}$/);
});

test('lt_net: a calibration record survives page-chunk loads', async () => {
  const { env, N } = setup({scripts: ['/static/app.3f9a1c.js', 'https://cdn.example.invalid/lib/vendor.js']});
  const record = Object.assign({}, CAL, { client_sig: N.clientSig() });
  env.addChunks(['/static/page-7.abc123.js']);
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal({ calibration: record }));
  assert.equal(r.calibration_stale, false);
  assert.equal(r.calibrated, true);
  assert.equal(r.save_signal, 'net');
  assert.equal(r.client_sig, record.client_sig);
});

// Backbone sync must not turn an unvalidated request into permission to navigate.
for (const [name, mo] of [
  ['202', { status: 202 }],
  ['captured GET', { method: 'GET', via: 'captured-fetch' }],
  ['wrong method', { method: 'POST' }],
  ['wrong endpoint', { path: '/api/telemetry/1' }],
  ['unidentified', { strategy: 'model-only' }]
]) {
  test('lt_net regression: model sync leaves ' + name + ' unconfirmed', async () => {
    const { env, N } = setup();
    const m = env.model(K1, Object.assign({ strategy: 'immediate', latency: 100 }, mo));
    let syncs = 0;
    m.on('sync', () => { syncs++; });
    N.expect(K1, m);
    m.set('answer', 'x');
    const r = await N.waitForSave([K1], cal({ max_ms: 700 }));
    assert.equal(syncs, 1, 'Backbone emits sync for every HTTP success, including 202');
    assert.equal(r.save_confirmed, false, name);
    assert.equal(r.save_signal, 'unconfirmed', name);
    if (name === '202') assert.equal(env.navigate().lost, 1);
  });
}

// Identifiers in errors or arbitrary payload echoes do not prove a per-key save.
for (const via of ['xhr', 'fetch']) {
  for (const response of [
    { saved: [K1], errors: [{ uuid: K2, error: 'invalid' }] },
    { saved: [K1, K2], errors: [{ uuid: K2, error: 'invalid' }] },
    { saved: [K1], items: [{ uuid: K2, value: 'echo' }] }
  ]) {
    test('lt_net regression: batch ' + via + ' outcomes ' + JSON.stringify(response), async () => {
      const { env, N } = setup();
      N.expect(K1);
      N.expect(K2);
      env.request('POST', '/api/panels/batch', JSON.stringify([K1, K2]), {
        via, latency: 100, response: JSON.stringify(response)
      });
      const r = await N.waitForSave([K1, K2], cal({ max_ms: 700,
        calibration: { source: 'net', method: 'POST', path_template: '/api/panels/batch' } }));
      assert.equal(r.save_confirmed, false);
      assert.equal(r.keys_saved, 1);
      assert.equal(r.save_signal, response.errors ? 'error' : 'unconfirmed');
      assert.deepEqual([...(response.errors ? r.failed_keys : r.pending_keys)], [K2]);
    });
  }
}

// Vacuous every(), or checking only a subset, must not hide an outstanding expectation.
for (const keys of [[], [K2]]) {
  test('lt_net regression: confirmation set covers pending expectations ' + keys.length, async () => {
    const { env, N } = setup();
    N.expect(K1);
    N.expect(K2);
    put(env, K2, '{}', { latency: 100 });
    const r = await N.waitForSave(keys, cal({ max_ms: 700 }));
    assert.equal(r.save_confirmed, false);
    assert.equal(r.save_signal, 'unconfirmed');
    assert.ok(N.pendingKeys().includes(K1));
    assert.ok(r.pending_keys.includes(K1), 'receipt reports the omitted pending key');
  });
}

// Method filtering must apply even without a model event path.
test('lt_net regression: matching path with uncalibrated method stays unconfirmed', async () => {
  const { env, N } = setup();
  N.expect(K1);
  env.request('POST', '/api/panels/' + K1 + '/state', '{}', { latency: 100 });
  const r = await N.waitForSave([K1], cal({ max_ms: 700 }));
  assert.equal(r.save_confirmed, false);
});

// A key added during the wait, or saved by a wrong method, remains outstanding under calibration.
for (const late of [false, true]) {
  test('lt_net regression: confirmation covers calibrated pending keys, late=' + late, async () => {
    const { env, N } = setup();
    if (!late) {
      N.expect(K1);
      env.request('POST', '/api/panels/' + K1 + '/state', '{}', { latency: 10 });
      await env.clock.advance(50);
    }
    N.expect(K2);
    put(env, K2, '{}', { latency: 100 });
    if (late) env.after(50, () => N.expect(K1));
    const r = await N.waitForSave([K2], cal({ max_ms: 700 }));
    assert.equal(r.save_confirmed, false);
    assert.ok(r.pending_keys.includes(K1));
  });
}

// The request event must reference its own observed transport, never a nearby request.
test('lt_net regression: unrelated same-tick request cannot validate a model sync', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'never' });
  N.expect(K1, m);
  env.request('PUT', '/api/panels/' + K2 + '/state', '{}', { latency: 100 });
  const options = {};
  m.trigger('request', m, {}, options);
  await env.clock.advance(100);
  m.trigger('sync', m, {}, options);
  const r = await N.waitForSave([K1], cal({ max_ms: 700 }));
  assert.equal(r.save_confirmed, false);
});

// A model event cannot shortcut transport completion or the calibrated method/endpoint filter.
for (const via of ['xhr', 'fetch']) {
  test('lt_net regression: model validates its own ' + via + ' transport to completion', async () => {
    const { env, N } = setup();
    const m = env.model(K1, { strategy: 'never' });
    N.expect(K1, m);
    const xhr = env.request('PUT', '/api/cells/1', '{}', { via, latency: 300 });
    const options = { xhr };
    m.trigger('request', m, xhr, options);
    m.trigger('sync', m, {}, options);
    const r = await N.waitForSave([K1], cal({ calibration: {
      source: 'model', method: 'PUT', path_template: '/api/cells/{n}' } }));
    assert.equal(r.save_signal, 'model');
    assert.equal(r.waited_ms, 600);
    assert.equal(r.ack_method, 'PUT');
    assert.equal(r.ack_path_template, '/api/cells/{n}');
  });
}

test('lt_net: fallback diagnostics aggregate sanitized paths without request content', async () => {
  const env = H.makeEnv();
  env.loadShared('lt_net');
  const net = env.ctx.LtNet;
  net.install();
  const model = env.model('written-key', {strategy: 'never'});
  net.expect('written-key', model);
  const secret = 'private-answer-secret';
  for (let i = 0; i < 2; i++) env.request('POST', '/api/save/123/deadbeef?token=' + secret, secret);
  env.request('GET', '/api/read/456/abcdef', secret);
  model.trigger('sync', model, {}, {});
  const pending = net.waitForSave(['written-key'], {fallback_ms: 4000});
  await env.clock.advance(4500);
  const result = await pending;
  assert.equal(result.signal, 'fallback');
  const diag = JSON.parse(JSON.stringify(result.save_diag));
  // The sync answered no observed request, so no row claims it.
  assert.deepEqual(diag, [
    {method: 'POST', path_template: '/api/save/:id/:id', status: 200, count: 2, model_sync: false},
    {method: 'GET', path_template: '/api/read/:id/:id', status: 200, count: 1, model_sync: false}
  ]);
  assert.ok(!JSON.stringify(diag).includes(secret));
  assert.ok(!JSON.stringify(diag).includes('?'));
});

test('lt_net: fallback diagnostics keep at most twenty rows', async () => {
  const env = H.makeEnv();
  env.loadShared('lt_net');
  const net = env.ctx.LtNet;
  net.install(); net.expect('written-key');
  for (let i = 0; i < 25; i++) env.request('POST', '/api/' + 'route'.repeat(i + 1), 'private');
  const pending = net.waitForSave(['written-key'], {fallback_ms: 4000});
  await env.clock.advance(4500);
  const result = await pending;
  assert.equal(result.save_diag.length, 20);
});

test('lt_net: save_diag model_sync marks only the row whose request the sync answered', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'never' });
  N.expect(K1, m);
  const xhr = env.request('PUT', '/api/cells/1', '{}', { latency: 100 });
  const options = { xhr };
  m.trigger('request', m, xhr, options);
  env.request('POST', '/api/telemetry/1', '{}', { latency: 100 });
  await env.clock.advance(150);
  m.trigger('sync', m, {}, options);
  const r = await N.waitForSave([K1], OPTS);
  assert.equal(r.save_signal, 'fallback');
  assert.deepEqual(JSON.parse(JSON.stringify(r.save_diag)), [
    { method: 'PUT', path_template: '/api/cells/:id', status: 200, count: 1, model_sync: true },
    { method: 'POST', path_template: '/api/telemetry/:id', status: 200, count: 1, model_sync: false }
  ]);
});

test('lt_net: a single-key 200 reply of {"success": false} fails the key', async () => {
  for (const via of ['xhr', 'fetch']) {
    const { env, N } = setup();
    N.expect(K1);
    put(env, K1, JSON.stringify({ uuid: K1, answer: 'x' }), { via, latency: 100, response: JSON.stringify({ success: false }) });
    const r = await N.waitForSave([K1], cal({ max_ms: 1000 }));
    assert.equal(r.save_signal, 'error', via);
    assert.deepEqual([...r.failed_keys], [K1], via);
  }
});

test('lt_net: a typed field echoed in a single-key reply leaves the generic rule as it was', async () => {
  const { env, N } = setup();
  const m = env.model(K1, { strategy: 'immediate', latency: 100, events: false,
    response: JSON.stringify({ success: true, panelKey: 4162 }) });
  N.expect(K1);
  m.set('answer', 'x');
  const r = await N.waitForSave([K1], cal());
  assert.equal(r.save_signal, 'net');
});

// The generic single-key path returned ok without consulting nested failure markers.
for (const [name, response] of [
  ['a nested {"success": false}', { success: true, data: { success: false } }],
  ['a nested status failed', { success: true, data: { status: 'failed' } }],
  ['a top-level errors list beside a per-key success', { saved: [K1], errors: [{ code: 1 }] }],
  ['an error message listed beside a per-key success', { saved: [K1], errors: ['quota exceeded'] }],
  ['a failure deeper than the inspection limit', { success: true, data: deepNest(40, { success: true }) }]
]) {
  test('lt_net: a single-key 200 reply with ' + name + ' leaves the key unconfirmed', async () => {
    const { env, N } = setup();
    N.expect(K1);
    put(env, K1, JSON.stringify({ uuid: K1, answer: 'x' }), { latency: 100, response: JSON.stringify(response) });
    const r = await N.waitForSave([K1], cal({ max_ms: 1500 }));
    assert.equal(r.save_confirmed, false);
    assert.equal(r.keys_saved, 0);
    assert.notEqual(r.save_signal, 'net');
  });
}

test('lt_net: a single-key 200 reply with a nested success still saves', async () => {
  const { env, N } = setup();
  N.expect(K1);
  put(env, K1, JSON.stringify({ uuid: K1, answer: 'x' }), { latency: 100,
    response: JSON.stringify({ success: true, data: { success: true, error: null, items: deepNest(20, { ok: true }) } }) });
  const r = await N.waitForSave([K1], cal({ max_ms: 1500 }));
  assert.equal(r.save_signal, 'net');
  assert.equal(r.keys_saved, 1);
});

/* ---- the run-data adapter: Lt's PUT of the current run's data ---- */
const RD_PATH = '/i/inst0001/v/2.0/api/student/courses/1/runs/101/data';
const rdCal = (N, extra) => Object.assign({}, OPTS, {
  calibration: { source: 'net', method: 'PUT', path_template: N.pathTemplate(RD_PATH) } }, extra || {});
const SUCCESS = JSON.stringify({ success: true });
// save_diag's run_data string, 'name=count ...', as an object.
const counts = text => Object.fromEntries(String(text).split(' ').map(p => p.split('=')).map(([k, v]) => [k, Number(v)]));
const short2 = N => {
  N.expect('209', null, null, { ids: { panel: '209', data: '208' } });
  N.expect('2838', null, null, { ids: { panel: '2838' } });
};

test('lt_net run-data: typed panelKey fields and a success reply save 3- and 4-character keys', async () => {
  for (const via of ['xhr', 'fetch']) {
    const { env, N } = setup();
    short2(N);
    const body = JSON.stringify({ panels: [{ panelKey: 209, dataKey: 208, state: { answer: 'a' } }, { panel_key: '2838', state: {} }] });
    env.request('PUT', RD_PATH, body, { via, latency: 100, response: SUCCESS });
    const r = await N.waitForSave(['209', '2838'], rdCal(N));
    assert.equal(r.save_signal, 'net', via);
    assert.equal(r.keys_saved, 2, via);
    assert.equal(r.keys_weak, 2, via);
    assert.equal(r.ack_path_template, N.pathTemplate(RD_PATH), via);
    assert.match(r.net_diag, /matched=1 /);
    assert.match(r.net_diag, /run_data=1 resp_truncated=0$/);
  }
});

test('lt_net run-data: a dataKey or uuid field and a uuid-keyed object identify their own kind', async () => {
  const { env, N } = setup();
  N.expect('209', null, null, { ids: { panel: '209', data: '208' } });
  N.expect(K1, null, null, { ids: { panel: '4162' } });
  env.request('PUT', RD_PATH, JSON.stringify({ data: [{ dataKey: '208' }], [K1]: { answer: 'b' } }), { latency: 100, response: SUCCESS });
  const r = await N.waitForSave(['209', K1], rdCal(N));
  assert.equal(r.save_signal, 'net');
  assert.equal(r.keys_saved, 2);
});

for (const [name, o, signal] of [
  ['an empty reply', { response: '' }, 'unconfirmed'],
  ['an empty object', { response: '{}' }, 'unconfirmed'],
  ['a reply that is not JSON', { response: 'OK' }, 'unconfirmed'],
  ['202 with success', { status: 202, response: SUCCESS }, 'unconfirmed'],
  ['{"success": false}', { response: JSON.stringify({ success: false }) }, 'error'],
  ['status 500', { status: 500, response: SUCCESS }, 'error'],
  ['success with a nested {"success": false}', { response: JSON.stringify({ success: true, data: { success: false } }) }, 'unconfirmed'],
  ['success with a nested status failed', { response: JSON.stringify({ success: true, result: { status: 'failed' } }) }, 'unconfirmed'],
  ['success with a nested errors list', { response: JSON.stringify({ success: true, data: { errors: [{ code: 1 }] } }) }, 'unconfirmed']
]) {
  test('lt_net run-data: ' + name + ' never acknowledges', async () => {
    const { env, N } = setup();
    short2(N);
    env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }, { panelKey: 2838 }]), Object.assign({ latency: 100 }, o));
    const r = await N.waitForSave(['209', '2838'], rdCal(N, { max_ms: 1000 }));
    assert.equal(r.save_signal, signal);
    assert.equal(r.keys_saved, 0);
    assert.equal(r.save_confirmed, false);
  });
}

test('lt_net run-data: a nested null error or success status is no failure marker', async () => {
  for (const response of [{ success: true, data: { error: null, success: true } }, { success: true, result: { status: 'saved' } }]) {
    const { env, N } = setup();
    short2(N);
    env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }, { panelKey: 2838 }]),
      { latency: 100, response: JSON.stringify(response) });
    const r = await N.waitForSave(['209', '2838'], rdCal(N, { max_ms: 1000 }));
    assert.equal(r.save_signal, 'net', JSON.stringify(response));
    assert.equal(r.keys_saved, 2);
  }
});

test('lt_net run-data: per-key reply outcomes decide each key, errors win', async () => {
  const { env, N } = setup();
  short2(N);
  env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }, { panelKey: 2838 }]), { latency: 100,
    response: JSON.stringify({ success: true, results: [{ panelKey: 209, success: true }, { panelKey: 2838, error: 'invalid' }] }) });
  const r = await N.waitForSave(['209', '2838'], rdCal(N, { max_ms: 1000 }));
  assert.equal(r.save_signal, 'error');
  assert.equal(r.keys_saved, 1);
  assert.deepEqual([...r.failed_keys], ['2838']);
  const b = setup();
  short2(b.N);
  b.env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }, { panelKey: 2838 }]), { latency: 100,
    response: JSON.stringify({ saved: [{ panelKey: 209 }] }) });
  const r2 = await b.N.waitForSave(['209', '2838'], rdCal(b.N, { max_ms: 1000 }));
  assert.equal(r2.save_signal, 'unconfirmed', 'a per-key reply that omits a key leaves it pending');
  assert.deepEqual([...r2.pending_keys], ['2838']);
});

// A failure nested under an item with its own success, or past the depth limit, was confirmed.
test('lt_net run-data: a failure nested under an item fails that item; its sibling still saves', async () => {
  const { env, N } = setup();
  short2(N);
  env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }, { panelKey: 2838 }]), { latency: 100,
    response: JSON.stringify({ results: [{ panelKey: 209, success: true, result: { success: false } },
      { panelKey: 2838, success: true, result: { success: true } }] }) });
  const r = await N.waitForSave(['209', '2838'], rdCal(N, { max_ms: 1000 }));
  assert.equal(r.save_signal, 'error');
  assert.deepEqual([...r.failed_keys], ['209']);
  assert.equal(r.keys_saved, 1);
  const one = setup();
  one.N.expect('209', null, null, { ids: { panel: '209' } });
  one.env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }]), { latency: 100,
    response: JSON.stringify({ results: [{ panelKey: 209, success: true, result: { success: false } }] }) });
  const r1 = await one.N.waitForSave(['209'], rdCal(one.N, { max_ms: 1000 }));
  assert.equal(r1.save_confirmed, false);
  assert.equal(r1.save_signal, 'error');
  assert.deepEqual([...r1.failed_keys], ['209']);
});

test('lt_net run-data: a failure no item owns leaves per-key successes unconfirmed', async () => {
  const { env, N } = setup();
  short2(N);
  env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }, { panelKey: 2838 }]), { latency: 100,
    response: JSON.stringify({ results: [{ panelKey: 209, success: true }, { panelKey: 2838, success: true }],
      warnings: { success: false } }) });
  const r = await N.waitForSave(['209', '2838'], rdCal(N, { max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.keys_saved, 0);
});

for (const [name, depth] of [['10 levels down', 10], ['past the depth limit', 40]]) {
  test('lt_net run-data: a success reply with a failure ' + name + ' never acknowledges', async () => {
    const { env, N } = setup();
    N.expect('209', null, null, { ids: { panel: '209' } });
    env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }]), { latency: 100,
      response: JSON.stringify({ success: true, data: deepNest(depth, { success: depth > 32 }) }) });
    const r = await N.waitForSave(['209'], rdCal(N, { max_ms: 1000 }));
    assert.equal(r.save_confirmed, false);
    assert.equal(r.keys_saved, 0);
    assert.equal(r.save_signal, 'unconfirmed');
    if (depth > 32) assert.match(r.net_diag, /resp_truncated=1$/);
  });
}

for (const [name, path, method, body] of [
  ['the key as a property name only', RD_PATH, 'PUT', { panels: { 209: { answer: 'a' }, 2838: { answer: 'b' } } }],
  ['the key as a plain value only', RD_PATH, 'PUT', { items: [209, '2838'], key: 209 }],
  ['a longer key that contains it', RD_PATH, 'PUT', { panels: [{ panelKey: '2090' }, { panelKey: 'x2838' }] }],
  ['another panel whose dataKey is the key', RD_PATH, 'PUT', { panels: [{ dataKey: 209 }, { dataKey: 2838 }] }],
  ['another run\'s data', '/i/inst0001/v/2.0/api/student/courses/1/runs/102/data', 'PUT', [{ panelKey: 209 }, { panelKey: 2838 }]],
  ['another API version', '/i/inst0001/v/3.0/api/student/courses/1/runs/101/data', 'PUT', [{ panelKey: 209 }, { panelKey: 2838 }]],
  ['a POST to the same path', RD_PATH, 'POST', [{ panelKey: 209 }, { panelKey: 2838 }]]
]) {
  test('lt_net run-data: ' + name + ' identifies no short key', async () => {
    const { env, N } = setup();
    short2(N);
    env.request(method, path, JSON.stringify(body), { latency: 100, response: SUCCESS });
    const calib = { source: 'net', method, path_template: N.pathTemplate(path) };
    const r = await N.waitForSave(['209', '2838'], Object.assign({}, OPTS, { calibration: calib, max_ms: 1000 }));
    assert.equal(r.save_signal, 'unconfirmed');
    assert.equal(r.keys_saved, 0);
  });
}

test('lt_net run-data: a request that started before the write identifies nothing', async () => {
  const { env, N } = setup();
  env.request('PUT', RD_PATH, JSON.stringify([{ panelKey: 209 }]), { latency: 300, response: SUCCESS });
  N.expect('209', null, null, { ids: { panel: '209' } });
  const r = await N.waitForSave(['209'], rdCal(N, { max_ms: 1000 }));
  assert.equal(r.save_signal, 'unconfirmed');
  assert.equal(r.keys_saved, 0);
});

test('lt_net run-data: an unparsed body stays unidentified and the fallback rule runs', async () => {
  const { env, N } = setup();
  short2(N);
  env.request('PUT', RD_PATH, 'panelKey=209&panelKey=2838', { latency: 100, response: SUCCESS });
  const r = await N.waitForSave(['209', '2838'], OPTS);
  assert.equal(r.save_signal, 'fallback');
  assert.equal(r.keys_pending, 2);
  const rd = counts(r.save_diag.find(row => row.run_data).run_data);
  assert.equal(rd.body_unparsed, 1);
  assert.equal(rd.keys_expected, 2);
  assert.equal(rd.keys_identified, 0);
});

test('lt_net run-data: fallback diagnostics count identities by kind without body content', async () => {
  const { env, N } = setup();
  short2(N);
  const secret = 'private-answer-secret';
  const body = { panels: { 209: { panelKey: 209, answer: secret } }, recent: [2838] };
  env.request('PUT', RD_PATH, JSON.stringify(body), { latency: 100, response: SUCCESS });
  env.request('POST', '/i/inst0001/v/2.0/api/student/courses/1/runs/101/completed/answerentered', secret, { latency: 50 });
  const r = await N.waitForSave(['209', '2838'], OPTS);
  assert.equal(r.save_signal, 'fallback');
  const diag = JSON.parse(JSON.stringify(r.save_diag));
  assert.ok(!JSON.stringify(diag).includes(secret));
  const row = diag.find(d => d.method === 'PUT');
  assert.equal(row.path_template, '/i/inst0001/v/2.0/api/student/courses/:id/runs/:id/data');
  assert.equal(row.model_sync, false);
  assert.equal(row.run_data, 'requests=1 body_json=1 body_unparsed=0 body_none=0 keys_expected=2 keys_identified=1 ' +
    'as_panel_field=1 as_data_field=0 as_uuid_field=0 as_prop=1 as_value=2 ' +
    'reply_per_key=0 reply_success=1 reply_failure=0 reply_bare=0');
  assert.equal(diag.find(d => d.method === 'POST').run_data, undefined);
  // On the fallback rule the identified key is still recorded for calibration.
  assert.equal(r.ack_source, 'net');
  assert.equal(r.ack_path_template, N.pathTemplate(RD_PATH));
});
