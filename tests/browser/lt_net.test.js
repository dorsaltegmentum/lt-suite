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
  assert.match(sig, /^c1-2-[0-9a-f]{8}$/);
  env.setScripts(['/static/app.3f9a1c.js?v=2', 'https://cdn.example.invalid/lib/vendor.js']);
  assert.equal(N.clientSig(), sig, 'query strings do not change the client');
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
