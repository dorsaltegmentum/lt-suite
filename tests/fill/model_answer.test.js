// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Tests for model_answer.js: reads panelState.modelAnswer for named panels, skips the report
 * page, flags a key found on two content pages, writes nothing, and returns flat rows.
 */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const F = require('./fill_env.js');
const { coll, mdl, makeEnv, page, panel, obs } = F;

const SRC = fs.readFileSync(path.join(F.BROWSER, 'model_answer.js'), 'utf8');
const runMa = (env, args) => vm.runInContext('(async (__LT_ARGS__) => {\n' + SRC + '\n})', env.sandbox)(args);
const long = 'alpha beta gamma '.repeat(80).trim();

const withMa = (env, key, plugin, content, ma, input) => panel(env, key, plugin, { content: content || {}, input: input || new F.Model(env, {}), modelAnswer: ma });
const build = e => [
  page(1, 'One', [
    withMa(e, '10', 'text-question', { question: 'Q' }, mdl({ answer: '<p>' + long + '</p>' })),
    withMa(e, '11', 'select', { answers: coll([mdl({ answer: 'First' }), mdl({ answer: 'Second' })]) }, mdl({ selectedAnswers: [1] })),
    withMa(e, '12', 'kcImageLabelling', { labelItems: coll([mdl({ label: 'zeta' }), mdl({ label: 'alpha' })]) },
      mdl({ linkedLabels: coll([mdl({ labelIndex: 1, locationIndex: 0 }), mdl({ labelIndex: 0, locationIndex: 1 })]) })),
    withMa(e, '13', 'categories', { labelItems: coll([mdl({ label: 'alpha' }), mdl({ label: 'beta' })]), categoryItems: coll([mdl({ title: 'Bin A' }), mdl({ title: 'Bin B' })]) },
      mdl({ categoryAnswerItems: coll([mdl({ labelAnswerItems: coll([mdl({ labelIndex: 1 })]) }), mdl({ labelAnswerItems: coll([mdl({ labelIndex: 0 })]) })]) })),
    withMa(e, '14', 'text-question', {}, undefined),
    withMa(e, '20', 'text-question', {}, mdl({ answer: 'on page one' }))
  ]),
  page(2, 'Two', [withMa(e, '20', 'text-question', {}, mdl({ answer: 'on page two' }))]),
  { pageIndex: 3, title: obs('Report'), reportInfoPanels: [], allPanels: [withMa(e, '30', 'text-question', {}, mdl({ answer: 'mirror' }))] }
];
const row = (r, key) => r.rows.find(x => x[0] === key);
/* Values from the vm realm compare by content. */
const same = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b));

test('model-answer: a text answer comes back in chunks of 800 characters', async () => {
  const env = makeEnv(build, { page: 1 });
  const r = await runMa(env, { keys: '10' });
  const x = row(r, '10');
  same(x.slice(0, 4), ['10', 1, 'text', 'ok']);
  assert.equal(x.slice(4).join(''), long);
  assert.ok(x.slice(4).every(s => s.length <= 800));
});

test('model-answer: a selection names its option text', async () => {
  const r = await runMa(makeEnv(build, { page: 1 }), { keys: ['11'] });
  same(row(r, '11'), ['11', 1, 'select', 'ok', '1: Second']);
});

test('model-answer: labels come back as location and label text', async () => {
  const r = await runMa(makeEnv(build, { page: 1 }), { keys: '12' });
  same(row(r, '12'), ['12', 1, 'labels', 'ok', 'loc 0: alpha', 'loc 1: zeta']);
});

test('model-answer: categories come back as bin and label text', async () => {
  const r = await runMa(makeEnv(build, { page: 1 }), { keys: '13' });
  same(row(r, '13'), ['13', 1, 'categories', 'ok', 'cat 0 Bin A: beta', 'cat 1 Bin B: alpha']);
});

test('model-answer: a panel without a model answer reads none', async () => {
  const r = await runMa(makeEnv(build, { page: 1 }), { keys: '14' });
  same(row(r, '14'), ['14', 1, 'unknown', 'none']);
});

test('model-answer: the report page is never read', async () => {
  const r = await runMa(makeEnv(build, { page: 1 }), { keys: '30' });
  same(row(r, '30').slice(0, 4), ['30', null, 'unknown', 'no-panel']);
});

test('model-answer: a key on two content pages is ambiguous until a page is named', async () => {
  const env = makeEnv(build, { page: 1 });
  let r = await runMa(env, { keys: '20' });
  same(row(r, '20'), ['20', null, 'unknown', 'ambiguous', 'page 1', 'page 2']);
  r = await runMa(env, { keys: '20', page: 2 });
  same(row(r, '20'), ['20', 2, 'text', 'ok', 'on page two']);
});

test('model-answer: no keys is refused', async () => {
  const r = await runMa(makeEnv(build, { page: 1 }), {});
  assert.equal(r.fatal, 'no-keys');
});

test('model-answer: writes nothing and nests at most 4 levels', async () => {
  const env = makeEnv(build, { page: 1 });
  const r = await runMa(env, { keys: '10,11,12,13,14' });
  assert.equal(env.writes, 0);
  assert.ok(F.maxDepth(r, 1) <= 4);
});
