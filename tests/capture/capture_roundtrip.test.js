// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* capture.js over every synthetic view model (tests/fixtures/viewmodels/*.json) must give
 * the module of the same id in tests/fixtures/modules/: same pages, items, flags, boundary
 * map and census. Each view model also runs a second time with every value wrapped in
 * observables, collections and models, and both runs must agree, so the readers' unwrapping
 * (U, A, models) is exercised on both shapes Lt uses. No commit function may be called.
 */
'use strict';
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const H = require('./vm_harness.js');

const specs = H.viewmodelSpecs();

const strip = doc => {
  const d = H.clone(doc);
  delete d.captured_at;
  return d;
};

describe('roundtrip', () => {
  if (!specs || !specs.length) {
    test('roundtrip: synthetic view models', t => t.skip('not run: WP03 fixtures missing (tests/fixtures/viewmodels/*.json)'));
    return;
  }

  for (const { file, spec } of specs) {
    const id = spec.module_id || file.replace(/\.json$/, '');

    test(`roundtrip: ${id} capture equals modules/module-${id}.json`, async (t) => {
      const mod = H.fixtureModule(id);
      if (!mod) { t.skip(`not run: WP03 fixture module-${id}.json missing`); return; }
      const trips = [];
      const r = await runCapture(H.envFromSpec(spec, { trips }));
      assert.equal(r.receipt.ok, true, JSON.stringify(r.receipt));
      assert.deepEqual(trips, [], 'no commit function was called');
      assert.deepEqual(H.shapeOf(r.doc), H.shapeOf(mod));
      assert.deepEqual(r.doc.pages.map(p => p.items.map(i => i.prompt_text)),
        mod.pages.map(p => p.items.map(i => i.prompt_text)), 'visible prompt bytes stay stable');
    });

    test(`roundtrip: ${id} wrapped and plain view models give the same documents`, async () => {
      const plain = await runCapture(H.envFromSpec(spec));
      const wrapped = await runCapture(H.envFromSpec(spec, { wrap: true }));
      assert.deepEqual(strip(wrapped.doc), strip(plain.doc));
      assert.deepEqual(strip(wrapped.reference || {}), strip(plain.reference || {}));
    });

    test(`roundtrip: ${id} module.json carries no free-text answer`, async () => {
      /* Free text is the student's own words; a chosen option or a placed label is stimulus
       * text that module.json already carries as options, so only free text is checked. */
      const r = await runCapture(H.envFromSpec(spec));
      const moduleText = JSON.stringify(r.doc);
      const types = new Map(r.doc.pages.flatMap(p => p.items).map(i => [i.item_id, i.type]));
      for (const it of (r.reference || { items: [] }).items) {
        if (types.get(it.item_id) !== 'text-response') continue;
        const a = String(it.student_answer || '');
        if (a.length >= 12) assert.equal(moduleText.includes(JSON.stringify(a).slice(1, -1)), false,
                                         `${it.item_id} answer leaked into module.json`);
      }
    });
  }
});

function runCapture(env) { return H.runCapture(env); }
