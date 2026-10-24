// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* lt_guard.js tests: never-submit guards persist after a script returns, re-wrap rebound
 * mutators, cancel forbidden navigations and refuse forbidden routes.
 *
 *     node --test tests/browser/lt_guard.test.js
 */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const H = require('./harness.js');

const MUTATORS = ['commitClicked', 'setCheckpointCommitState', 'removeCommittedCheckpoints',
  'removePagesAfterUncommittedCheckpoints'];
const RUN = '/i/inst0001/student/courses/1/runs/101/run';
const POLICY = {
  mode: 'plan', origin: H.DEFAULT_ORIGIN, origin_conflict: false, instance: 'inst0001', course_id: 1, run_id: 101,
  stop_before_page: 5, never_routes: ['complete'], never_indexes: [3], report_indexes: [6],
  checkpoint_indexes: [], page_map: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 9: 9 }, url_map: {}, mapped: true
};

function setup(o) {
  const env = H.makeEnv(o);
  env.loadShared('lt_guard');
  return { env, G: env.ctx.LtGuard };
}
const run = (G, policy, body, opts) => G.run(policy, body || (async () => ({ ok: true })), opts);

test('lt_guard: each commit mutator throws, counts a trip and never reaches Lt', async () => {
  const { env, G } = setup();
  for (const name of MUTATORS) {
    assert.throws(() => env.root[name](true), /lt-suite never-submit/);
  }
  assert.equal(env.commits.length, 0);
  assert.equal(G.trips(), 4);
  const st = G.status();
  for (const name of MUTATORS) assert.equal(st.mutators[name], 'locked');
});

test('lt_guard: the guard stays installed after a script returns; a later raw call still throws', async () => {
  const { env, G } = setup();
  const r = await run(G, null, async () => ({ ok: true, did: 'read' }));
  assert.equal(r.ok, true);
  assert.equal(r.guard_trips, 0);
  assert.throws(() => env.root.commitClicked({}), /never-submit/);
  assert.equal(env.commits.length, 0);
  const r2 = await run(G, null, async () => ({ ok: true }));
  assert.equal(r2.guard_trips, 1);
  assert.equal(r2.guard_last, 'mutator:commitClicked');
});

test('lt_guard: Lt cannot rebind a locked mutator; the assignment is dropped and counted', async () => {
  const { env, G } = setup();
  env.root.commitClicked = function (x) { env.commits.push(['rebound', x]); };
  const r = await run(G, null);
  assert.equal(r.ok, true);
  assert.equal(G.status().mutators.commitClicked, 'locked');
  assert.throws(() => env.root.commitClicked(1), /never-submit/);
  assert.equal(env.commits.length, 0);
  assert.equal(G.status().rebinds_blocked, 1);
});

test('lt_guard: a mutator that cannot be locked is wrapped, and re-wrapped after Lt rebinds it', async () => {
  const { env, G } = setup({
    root: (e, pages) => {
      const r = { lessonFlowPages: H.obs(pages) };
      for (const n of MUTATORS) {
        Object.defineProperty(r, n, { value: e.mutators[n], writable: true, enumerable: true, configurable: false });
      }
      return r;
    }
  });
  assert.equal(G.status().mutators.commitClicked, 'wrapped');
  assert.throws(() => env.root.commitClicked(1), /never-submit/);
  env.root.commitClicked = function (x) { env.commits.push(['rebound', x]); };
  const r = await run(G, null);
  assert.equal(r.ok, true);
  assert.equal(G.status().mutators.commitClicked, 'rewrapped');
  assert.throws(() => env.root.commitClicked(2), /never-submit/);
  assert.equal(env.commits.length, 0);
  assert.ok(G.status().rewraps >= 1);
});

test('lt_guard: a rebinding during the script body is caught when the body returns', async () => {
  const { env, G } = setup();
  await run(G, null, async () => {
    env.root.removeCommittedCheckpoints = () => env.commits.push('rebound');
    return { ok: true };
  });
  assert.throws(() => env.root.removeCommittedCheckpoints(), /never-submit/);
  assert.equal(env.commits.length, 0);
});

test('lt_guard: form submit and requestSubmit throw; a native submit event is cancelled', () => {
  const { env, G } = setup();
  const f = env.form();
  assert.throws(() => f.submit(), /never-submit/);
  assert.throws(() => f.requestSubmit(), /never-submit/);
  const ev = env.dispatchSubmit();
  assert.equal(ev.defaultPrevented, true);
  assert.equal(env.formSubmits, 0);
  assert.equal(G.trips(), 3);
});

test('lt_guard: navigate listener with a plan cancels complete, boundary, never-navigated and report pages', async () => {
  const { env, G } = setup();
  await run(G, POLICY);
  const o = H.DEFAULT_ORIGIN;
  assert.equal(env.navigateEvent(o + RUN + '/complete').canceled, true);
  assert.equal(env.navigateEvent(o + RUN + '/page/5').canceled, true, 'stop_before_page itself');
  assert.equal(env.navigateEvent(o + RUN + '/page/9').canceled, true);
  assert.equal(env.navigateEvent(o + RUN + '/page/3').canceled, true, 'never_navigated index');
  assert.equal(env.navigateEvent(o + RUN + '/page/6').canceled, true, 'report page');
  assert.equal(env.navigateEvent(o + RUN + '/checkpoint/1').canceled, true);
  assert.equal(env.navigateEvent(o + RUN + '/page/2?preview=1').canceled, true);
  assert.equal(env.navigateEvent('https://other.example.invalid' + RUN + '/page/2').canceled, true);
  assert.equal(env.navigateEvent(o + '/i/inst0001/student/courses/1/runs/999/run/page/2').canceled, true);
  assert.equal(env.navigateEvent(o + RUN + '/page/2').canceled, false);
  assert.equal(env.navigateEvent(o + RUN + '/background').canceled, false);
  assert.equal(G.trips(), 9);
});

test('lt_guard: without a plan every page change is cancelled; the same page with a new hash is not', async () => {
  const { env, G } = setup();
  await run(G, null);
  const o = H.DEFAULT_ORIGIN;
  assert.equal(env.navigateEvent(o + RUN + '/page/2').canceled, true);
  assert.equal(env.navigateEvent(o + RUN + '/background').canceled, true);
  assert.equal(env.navigateEvent(o + RUN + '/page/1#panel-3').canceled, false);
  assert.equal(G.trips(), 2);
  assert.equal(G.status().policy, 'block-all');
});

test('lt_guard: an uncancelable navigation still counts as a trip', async () => {
  const { env, G } = setup();
  await run(G, POLICY);
  const r = env.navigateEvent(H.DEFAULT_ORIGIN + RUN + '/complete', { cancelable: false });
  assert.equal(r.canceled, false);
  assert.equal(G.trips(), 1);
  assert.equal(G.status().uncancelable, 1);
});

test('lt_guard: forbidden routes refuse to start and the body never runs', async () => {
  const cases = [
    [RUN + '/complete', 'complete'],
    [RUN + '/checkpoint/1', 'checkpoint'],
    [RUN + '/page/2?preview=1', 'preview'],
    ['/i/inst0001/student/courses/1/runs/101/preview/run/page/1', 'preview']
  ];
  for (const [p, route] of cases) {
    const { G } = setup({ path: p });
    let ran = false;
    const r = await run(G, POLICY, async () => { ran = true; return { ok: true }; });
    assert.equal(r.ok, false, p);
    assert.equal(r.fatal, 'forbidden-route');
    assert.equal(r.route, route);
    assert.equal(ran, false);
    assert.equal(r.guard_trips, 0);
  }
  const rep = setup({ pages: [H.page({ title: 'P1' }), H.page({ title: 'Report', current: true, report: true })], path: RUN + '/page/2' });
  let ran = false;
  const r = await run(rep.G, null, async () => { ran = true; return { ok: true }; });
  assert.equal(r.fatal, 'forbidden-route');
  assert.equal(r.route, 'report-page');
  assert.equal(ran, false);
  const cp = setup({ pages: [H.page({ title: 'P1' }), H.page({ title: 'Gate', current: true, checkpoint: true })], path: RUN + '/page/2' });
  const r3 = await run(cp.G, null);
  assert.equal(r3.route, 'checkpoint', 'a current page shaped like a checkpoint refuses too');
  const byPlan = setup({ path: RUN + '/page/6' });
  const r2 = await run(byPlan.G, POLICY);
  assert.equal(r2.route, 'report-page', 'the plan marks flow page 6 as the report page');
});

test('lt_guard: loading the file twice keeps one navigate listener and one form wrapper', async () => {
  const { env, G } = setup();
  const submit = env.ctx.HTMLFormElement.prototype.submit;
  env.loadShared('lt_guard');
  await run(env.ctx.LtGuard, null);
  assert.equal(env.ctx.LtGuard, G);
  assert.equal(env.navListenerCount(), 1);
  assert.equal(env.ctx.HTMLFormElement.prototype.submit, submit);
  assert.throws(() => env.form().submit(), /never-submit/);
  assert.equal(G.trips(), 1);
});

test('lt_guard: a throwing body becomes a receipt with guard_trips', async () => {
  const { env, G } = setup();
  const r = await run(G, null, async () => { env.root.commitClicked(); return { ok: true }; });
  assert.equal(r.ok, false);
  assert.equal(r.fatal, 'script-threw');
  assert.match(r.error, /never-submit: mutator:commitClicked/);
  assert.equal(r.guard_trips, 1);
  assert.equal(env.commits.length, 0);
});

test('lt_guard: wraps the mutators once Lt paints, through the retry timer', async () => {
  const env = H.makeEnv({ rootReady: false });
  env.loadShared('lt_guard');
  const G = env.ctx.LtGuard;
  assert.equal(G.status().mutators.commitClicked, 'no-root');
  env.setRootReady(true);
  await env.clock.advance(300);
  assert.throws(() => env.root.commitClicked(), /never-submit/);
  assert.equal(env.commits.length, 0);
});

test('lt_guard: no Navigation API is reported, and the other guards still hold', async () => {
  const { env, G } = setup({ navigationApi: false });
  const st = G.status();
  assert.equal(st.navigate, 'no-navigation-api');
  assert.throws(() => env.root.setCheckpointCommitState(true), /never-submit/);
});

test('lt_guard: classify maps URL page numbers through the plan', () => {
  const { G } = setup();
  const o = H.DEFAULT_ORIGIN;
  const mapped = Object.assign({}, POLICY, { page_map: { 1: 1, 2: 2, 3: 4 }, mapped: true, never_indexes: [], report_indexes: [] });
  assert.equal(G.classify(o + RUN + '/page/3', mapped).page_index, 4);
  assert.equal(G.classify(o + RUN + '/page/4', mapped).reason, 'page-not-in-plan');
  assert.equal(G.classify(o + RUN + '/page/2', Object.assign({}, mapped, { origin_conflict: true })).reason, 'origin-conflict');
  assert.equal(G.classify(o + RUN + '/page/2', { mode: 'block-all' }).reason, 'no-plan');
  assert.equal(G.classify('javascript:alert(1)', mapped).reason, 'bad-scheme');
  assert.equal(G.classify('https://u:p' + String.fromCharCode(64) + 'lt.example.invalid' + RUN + '/page/1', mapped).reason, 'credentials-in-url');
});

test('lt_guard: a commit handler Lt bound before the guard fires 0 times on click and on Enter', () => {
  const env = H.makeEnv();
  const orig = env.root.commitClicked;
  let calls = 0;
  const handler = () => { calls++; orig.call(env.root, true); };
  const btn = env.el('button', { text: 'Submit attempt', on: { click: handler, keydown: handler } });
  env.loadShared('lt_guard');
  const G = env.ctx.LtGuard;
  btn.click();
  const k = env.key(btn, 'Enter');
  assert.equal(calls, 0);
  assert.equal(env.commits.length, 0);
  assert.equal(k.defaultPrevented, true);
  assert.equal(G.trips(), 2);
  assert.match(G.status().events, /listening/);
  let saves = 0;
  const save = env.el('button', { text: 'Save', attrs: { type: 'button' }, on: { click: () => { saves++; } } });
  save.click();
  env.key(btn, 'Tab');
  assert.equal(saves, 1, 'an ordinary button still works');
  assert.equal(G.trips(), 2, 'Tab is not an activation key');
});

test('lt_guard: a Knockout binding that names a mutator is blocked whatever the label says', () => {
  const env = H.makeEnv();
  let calls = 0;
  const box = env.el('div', { attrs: { 'data-bind': 'click: $root.setCheckpointCommitState' }, on: { click: () => { calls++; } } });
  const inner = env.el('span', { text: 'Next', parent: box });
  const wrapped = env.el('a', { text: 'Continue', attrs: { href: '#', role: 'button' } });
  const icon = env.el('span', { text: '', parent: env.el('button', { text: 'Finish run', attrs: { type: 'button' }, on: { click: () => { calls++; } } }) });
  env.loadShared('lt_guard');
  const G = env.ctx.LtGuard;
  inner.click();
  icon.click();
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) env.dispatch(icon, t);
  wrapped.click();
  assert.equal(calls, 0);
  assert.equal(G.trips(), 6);
  assert.match(String(env.ctx.LtGuard.status().guard_trips), /6/);
});

test('lt_guard: window.confirm answers No and counts a trip; Lt cannot put it back', () => {
  const { env, G } = setup();
  assert.equal(env.ctx.confirm('Submit your answers?'), false);
  assert.equal(G.trips(), 1);
  env.load('try { window.confirm = function () { return true; }; } catch (e) {}', 'lt-rebind.js');
  assert.equal(env.ctx.confirm('Finish?'), false);
  assert.equal(G.status().confirm, 'answers-no');
  assert.equal(G.trips(), 2);
});

test('lt_guard: a neutral submit button in a form runs no submit', () => {
  const { env, G } = setup();
  const f = env.form();
  const go = env.el('button', { text: 'Go', parent: f });
  go.click();
  assert.equal(env.formSubmits, 0);
  assert.equal(G.trips(), 1);
  assert.equal(G.status().submit_listener, true);
});

test('lt_guard: a navigation that would pass a checkpoint page or the stop is cancelled', async () => {
  const pages = [1, 2, 3, 4, 5, 6].map(i => H.page({ title: 'P' + i, current: i === 2, checkpoint: i === 4 }));
  const { env, G } = setup({ pages, path: RUN + '/page/2' });
  const pol = Object.assign({}, POLICY, { stop_before_page: 9, never_indexes: [], report_indexes: [], checkpoint_indexes: [4],
    page_map: { 1: 1, 2: 2, 3: 3, 4: 5, 5: 6, 7: 8 } });
  await run(G, pol);
  const o = H.DEFAULT_ORIGIN;
  assert.equal(env.navigateEvent(o + RUN + '/page/3').canceled, false, 'flow 3, nothing in between');
  assert.equal(env.navigateEvent(o + RUN + '/page/1').canceled, false, 'back to flow 1');
  assert.equal(env.navigateEvent(o + RUN + '/page/4').canceled, true, 'flow 5 is past the checkpoint at 4');
  assert.equal(G.status().guard_trips, 1);
  const st2 = setup({ pages, path: RUN + '/page/2' });
  await run(st2.G, Object.assign({}, pol, { checkpoint_indexes: [], stop_before_page: 4 }));
  assert.equal(st2.env.navigateEvent(o + RUN + '/page/3').canceled, false);
  assert.equal(G.classify(o + RUN + '/page/4', Object.assign({}, pol, { page_map: { 4: 4 } })).reason, 'checkpoint');
});

test('lt_guard: an unmapped current page in the run forbids navigation; a page outside the run does not', async () => {
  const lost = setup({ rootReady: false, path: RUN + '/page/8' });
  await run(lost.G, POLICY);
  const o = H.DEFAULT_ORIGIN;
  assert.equal(lost.env.navigateEvent(o + RUN + '/page/2').canceled, true);
  const away = setup({ rootReady: false, path: '/i/inst0001/student/courses/1' });
  await run(away.G, POLICY);
  assert.equal(away.env.navigateEvent(o + RUN + '/page/2').canceled, false);
  assert.equal(away.env.navigateEvent(o + RUN + '/page/5').canceled, true);
});

// A neutral nested action must not hide a commit ancestor or its captured handler.
test('lt_guard regression: every actionable ancestor is inspected', () => {
  const env = H.makeEnv();
  let commits = 0;
  const outer = env.el('button', { text: 'Submit attempt', attrs: { type: 'button' },
    on: { click: () => { commits++; }, keydown: () => { commits++; } } });
  const inner = env.el('span', { text: 'Continue', parent: outer, attrs: { onclick: 'noop()' } });
  env.loadShared('lt_guard');
  inner.click();
  env.key(inner, 'Enter');
  assert.equal(commits, 0);
  assert.equal(env.ctx.LtGuard.trips(), 2);
});

// Retry exhaustion must not leave a root that paints late permanently unguarded.
test('lt_guard regression: late roots remain watched and unresolved mutators are degraded', async () => {
  const { env, G } = setup({ rootReady: false });
  const receipt = await run(G, null);
  assert.match(receipt.guard_degraded || '', /mutators:.*no-root/);
  await env.clock.advance(11000);
  env.setRootReady(true);
  await env.clock.advance(2000);
  for (const name of MUTATORS) assert.throws(() => env.root[name](), /never-submit/);
  assert.equal(env.commits.length, 0);
  assert.ok(!String((await run(G, null)).guard_degraded).includes('no-root'));
});

// URL flow mapping must enforce all boundaries before Knockout paints a current page.
for (const [index, reason] of [[3, 'never-navigated'], [5, 'at-or-past-stop'], [4, 'checkpoint']]) {
  test('lt_guard regression: unpainted URL refuses ' + reason, async () => {
    const { G } = setup({ rootReady: false, path: RUN + '/page/2' });
    let ran = false;
    const p = Object.assign({}, POLICY, { page_map: { 2: index }, checkpoint_indexes: [4] });
    const r = await run(G, p, async () => { ran = true; return { ok: true }; });
    assert.equal(r.fatal, 'forbidden-route');
    assert.equal(r.route, reason);
    assert.equal(ran, false);
  });
}

// A bubble-phase submit cancellation runs after Lt's already-bound submit handler.
test('lt_guard regression: submit capture blocks previously bound commit handlers', () => {
  const env = H.makeEnv();
  const form = env.form();
  form.addEventListener('submit', () => { env.commits.push('native-submit'); });
  env.loadShared('lt_guard');
  const ev = env.dispatch(form, 'submit');
  assert.equal(ev.defaultPrevented, true);
  assert.equal(env.commits.length, 0);
  assert.equal(env.ctx.LtGuard.trips(), 1);
});

// The second checkpoint must still block when the first is behind the current page.
test('lt_guard regression: navigation checks every checkpoint', async () => {
  const { env, G } = setup({ rootReady: false, path: RUN + '/page/4' });
  const p = Object.assign({}, POLICY, { stop_before_page: 12, never_indexes: [], report_indexes: [],
    checkpoint_indexes: [3, 7], page_map: { 4: 4, 5: 5, 8: 9 } });
  await run(G, p);
  assert.equal(env.navigateEvent(H.DEFAULT_ORIGIN + RUN + '/page/5').canceled, false);
  assert.equal(env.navigateEvent(H.DEFAULT_ORIGIN + RUN + '/page/8').canceled, true);
});

// Bound mutators can carry neutral labels and survive the root's later replacement.
test('lt_guard regression: bound commit handler on an actionable ancestor is blocked', () => {
  const env = H.makeEnv();
  const bound = env.root.commitClicked.bind(env.root);
  const outer = env.el('button', { text: 'Continue', attrs: { type: 'button' }, on: { click: bound } });
  outer.onclick = bound;
  const inner = env.el('span', { text: 'Next', attrs: { onclick: 'noop()' }, parent: outer });
  env.loadShared('lt_guard');
  inner.click();
  assert.equal(env.commits.length, 0);
  assert.equal(env.ctx.LtGuard.trips(), 1);
});

// A deep DOM wrapper must not hide an actionable ancestor beyond an arbitrary walk cap.
test('lt_guard regression: deeply nested commit ancestor is inspected', () => {
  const env = H.makeEnv();
  let calls = 0;
  let target = env.el('button', { text: 'Submit attempt', attrs: { type: 'button' },
    on: { click: () => { calls++; } } });
  for (let i = 0; i < 20; i++) target = env.el('span', { text: 'Continue', parent: target });
  env.loadShared('lt_guard');
  target.click();
  assert.equal(calls, 0);
  assert.equal(env.ctx.LtGuard.trips(), 1);
});
