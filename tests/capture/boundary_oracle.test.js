// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Independent oracle for the first one-way door.
 *
 * Three sources must agree for every synthetic module that has a view model:
 *   1. capture.js's boundary detector, run over tests/fixtures/viewmodels/<id>.json;
 *   2. tests/fixtures/modules/expected-boundaries.json, written by hand from the spec;
 *   3. ltcore.boundaries.first_stop_boundary(), run on capture.js's output and on the
 *      fixture module.
 * Comparing first_stop_boundary with itself would prove nothing; the JavaScript detector
 * and the hand-written table are the two independent readings.
 */
'use strict';
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const H = require('./vm_harness.js');

const SHARED = path.join(H.REPO, 'shared');
const PY = process.env.PYTHON || 'python3';
const BOUNDARIES = path.join(SHARED, 'ltcore', 'boundaries.py');

/* first_stop_boundary over each module, in one Python call, as [page_index, class, item_id]. */
function pyFirstStop(modules) {
  const code = [
    'import json, sys',
    'from ltcore.boundaries import first_stop_boundary',
    'out = []',
    'for m in json.load(sys.stdin):',
    '    r = first_stop_boundary(m)',
    '    if r is not None and not isinstance(r, (list, tuple, dict)):',
    '        r = [getattr(r, "page_index", None), getattr(r, "kind", getattr(r, "class_", None)), getattr(r, "item_id", None)]',
    '    out.append(r)',
    'print(json.dumps(out))'
  ].join('\n');
  const r = spawnSync(PY, ['-c', code], {
    input: JSON.stringify(modules), encoding: 'utf8',
    env: Object.assign({}, process.env, { PYTHONPATH: SHARED })
  });
  if (r.status !== 0) throw new Error(`ltcore.boundaries failed: ${r.stderr || r.error}`);
  return JSON.parse(r.stdout).map(H.normStop);
}

const specs = H.viewmodelSpecs();
const expected = H.expectedBoundaries();

describe('boundary-oracle', () => {
  const missing = [];
  if (!specs || !specs.length) missing.push('WP03 tests/fixtures/viewmodels');
  if (!expected) missing.push('WP03 tests/fixtures/modules/expected-boundaries.json');
  if (!fs.existsSync(BOUNDARIES)) missing.push('WP02 shared/ltcore/boundaries.py');
  if (missing.length) {
    test('boundary-oracle: capture.js against the hand-written table and ltcore.boundaries',
         t => t.skip(`not run: ${missing.join(', ')} missing`));
    return;
  }

  const cases = specs.filter(s => expected[s.spec.module_id]);
  test('boundary-oracle: at least one view model has a hand-written expectation', () => {
    assert.ok(cases.length > 0, 'no view model id appears in expected-boundaries.json');
  });

  for (const { spec } of cases) {
    const id = spec.module_id;
    const want = expected[id];

    test(`boundary-oracle: ${id} capture.js detector equals the hand-written boundaries`, async () => {
      const r = await H.runCapture(H.envFromSpec(spec));
      const cb = r.doc.commit_boundaries;
      if (want.checkpoint_pages !== undefined) assert.deepEqual(cb.checkpoint_pages, want.checkpoint_pages);
      if (want.single_attempt_items !== undefined) assert.deepEqual(cb.single_attempt_items, want.single_attempt_items);
      const [fromCapture] = pyFirstStop([r.doc]);
      assert.deepEqual(fromCapture, H.normStop(want.first_stop));
    });

    test(`boundary-oracle: ${id} ltcore.boundaries on the fixture module equals the same answer`, (t) => {
      const mod = H.fixtureModule(id);
      if (!mod) { t.skip(`not run: WP03 fixture module-${id}.json missing`); return; }
      const [fromFixture] = pyFirstStop([mod]);
      assert.deepEqual(fromFixture, H.normStop(want.first_stop));
    });
  }
});
