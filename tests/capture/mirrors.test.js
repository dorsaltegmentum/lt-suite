// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Read-only MIRROR detection in capture.js.
 *
 * A team-based module may show a table from an earlier page again on a later page, for the
 * learner to choose from. Such a copy is not writable, and the panel itself carries no sign
 * of that. Detection therefore compares pages: an earlier panel with equal type, prompt,
 * constraints and answered evidence, a boolean `read_only` there and null on the copy.
 * Part 1 drives the whole captureLtRun() on synthetic pages. Part 2 checks the synthetic
 * fixture module c1-r105 (three mirror pairs and one near-duplicate that must not flag).
 */
'use strict';
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const H = require('./vm_harness.js');
const { obs, baseRoot, runCapture } = H;

/* A row as inp.rows holds it; headerRow and the cells' content are what grid() reads. */
const row = (cells, header) => ({ headerRow: obs(!!header), cells: obs(cells.map(t => ({ content: obs(t) }))) });
const tablePanel = (key, question, rows, readOnly) => ({
  pluginKey: obs('table-question'), panelKey: obs(key),
  panelState: obs({ input: { rows: obs(rows) }, content: { question: obs(question) },
                    isReadOnly: readOnly === undefined ? undefined : obs(readOnly) })
});
const textPanel = (key, question, answer, readOnly) => ({
  pluginKey: obs('text-question'), panelKey: obs(key),
  panelState: obs({ input: { answer: obs(answer) }, content: { question: obs(question) },
                    isReadOnly: readOnly === undefined ? undefined : obs(readOnly) })
});
const page = (title, panels) => ({ title: obs(title), allPanels: obs(panels || []), rows: obs([]) });
const run = async pages => (await runCapture({ root: baseRoot(pages, []) })).doc;
const byKey = (doc, k) => doc.pages.flatMap(p => p.items).find(i => i.panel_key === k) || {};
const mo = it => it.mirror_of || {};

const gridRows = filled => [
  row(['Finding', 'Why it matters', ''], true),
  row([filled ? 'finding one' : '', filled ? 'reason one' : '', '']),
  row([filled ? 'finding two' : '', filled ? 'reason two' : '', ''])
];

describe('mirrors', () => {
  test('mirrors: a later copy of a filled table names its source and all three facts', async () => {
    const doc = await run([
      page('Early page', [tablePanel('11', 'List the findings', gridRows(true), false)]),
      page('Later page', [tablePanel('12', 'List the findings', gridRows(true))])
    ]);
    const src = byKey(doc, '11'), mir = byKey(doc, '12');
    assert.equal(src.mirror_of, undefined);
    assert.equal(mo(mir).item_id, src.item_id);
    assert.equal(mo(mir).page_index, 1);
    assert.deepEqual(mo(mir).basis,
      ['duplicate-prompt-and-constraints', 'identical-content', 'read-only-flag-absent-here-present-on-source']);
    assert.equal(src.read_only, false);
    assert.equal(mir.read_only, null, 'read_only on the mirror stays as measured');
  });

  test('mirrors: an empty pair records both-empty', async () => {
    const doc = await run([
      page('Early page', [tablePanel('11', 'List the findings', gridRows(false), false)]),
      page('Later page', [tablePanel('12', 'List the findings', gridRows(false))])
    ]);
    const mir = byKey(doc, '12');
    assert.ok(mir.mirror_of);
    assert.equal(mo(mir).basis[1], 'both-empty');
    assert.equal(mir.answered, false);
  });

  test('mirrors: two equal panels on one page are not mirrors', async () => {
    const doc = await run([page('Only page', [tablePanel('21', 'Same question', gridRows(false), false),
                                              tablePanel('22', 'Same question', gridRows(false), false)])]);
    assert.equal(byKey(doc, '22').mirror_of, undefined);
  });

  test('mirrors: the same prompt over a different grid is not a mirror', async () => {
    const doc = await run([
      page('Early page', [tablePanel('31', 'Same question', gridRows(false), false)]),
      page('Later page', [tablePanel('32', 'Same question', [row(['x'], true), row([''])], false)])
    ]);
    assert.equal(byKey(doc, '32').mirror_of, undefined);
  });

  test('mirrors: an empty prompt never mirrors', async () => {
    const doc = await run([
      page('Early page', [tablePanel('41', '', gridRows(false), false)]),
      page('Later page', [tablePanel('42', '', gridRows(false), false)])
    ]);
    assert.equal(byKey(doc, '42').mirror_of, undefined);
  });

  test('mirrors: the basis drops the flag fact when both panels carry read_only', async () => {
    const doc = await run([
      page('Early page', [tablePanel('51', 'List the findings', gridRows(true), false)]),
      page('Later page', [tablePanel('52', 'List the findings', gridRows(true), true)])
    ]);
    const mir = byKey(doc, '52');
    assert.ok(mir.mirror_of);
    assert.deepEqual(mo(mir).basis, ['duplicate-prompt-and-constraints', 'identical-content']);
    assert.equal(mir.read_only, true);
  });

  test('mirrors: different answered evidence keeps only the duplicate fact', async () => {
    const doc = await run([
      page('Early page', [tablePanel('61', 'List the findings', gridRows(true), false)]),
      page('Later page', [tablePanel('62', 'List the findings', gridRows(false))])
    ]);
    assert.deepEqual(mo(byKey(doc, '62')).basis,
      ['duplicate-prompt-and-constraints', 'read-only-flag-absent-here-present-on-source']);
  });

  test('mirrors: text questions repeated on a later page are flagged as well', async () => {
    const doc = await run([
      page('Early page', [textPanel('71', 'State the working diagnosis', 'condition x', false)]),
      page('Later page', [textPanel('72', 'State the working diagnosis', 'condition x')])
    ]);
    assert.equal(mo(byKey(doc, '72')).item_id, byKey(doc, '71').item_id);
  });

  /* tests/fixtures/modules/expected-mirrors.json lists, per module, the mirror pairs (all
   * three facts) and the near-duplicates (same prompt and grid, different evidence and flags:
   * flagged with the duplicate fact only). Both are written from the fixture spec. */
  const expectedMirrors = () => {
    const p = require('node:path').join(H.FIXTURES, 'modules', 'expected-mirrors.json');
    if (!require('node:fs').existsSync(p)) return null;
    const d = JSON.parse(require('node:fs').readFileSync(p, 'utf8'));
    return d.modules || d;
  };
  const wanted = (exp) => [...(exp.pairs || []), ...(exp.near_duplicates || [])]
    .map(e => [e.mirror, e.source, e.basis]).sort();

  test('mirrors: the fixture module c1-r105 holds three mirror pairs and one near-duplicate', (t) => {
    const mod = H.fixtureModule('c1-r105');
    const exp = expectedMirrors();
    if (!mod || !exp || !exp['c1-r105']) { t.skip('not run: WP03 fixtures missing (module-c1-r105.json, expected-mirrors.json)'); return; }
    assert.equal((exp['c1-r105'].pairs || []).length, 3);
    assert.equal((exp['c1-r105'].near_duplicates || []).length, 1);
    for (const e of exp['c1-r105'].pairs) {
      assert.deepEqual(e.basis, ['duplicate-prompt-and-constraints', 'identical-content',
                                 'read-only-flag-absent-here-present-on-source']);
    }
    assert.ok(!exp['c1-r105'].near_duplicates[0].basis.includes('identical-content'),
              'the near-duplicate does not share its evidence');
    const got = mod.pages.flatMap(p => p.items).filter(i => i.mirror_of)
      .map(i => [i.item_id, i.mirror_of.item_id, i.mirror_of.basis]).sort();
    assert.deepEqual(got, wanted(exp['c1-r105']));
  });

  test('mirrors: capture.js over every view model flags exactly the expected mirrors', async (t) => {
    const specs = H.viewmodelSpecs();
    const exp = expectedMirrors();
    if (!specs || !exp) { t.skip('not run: WP03 fixtures missing (viewmodels, expected-mirrors.json)'); return; }
    let checked = 0;
    for (const { spec } of specs) {
      const e = exp[spec.module_id];
      if (!e) continue;
      const r = await runCapture(H.envFromSpec(spec));
      const got = r.doc.pages.flatMap(p => p.items).filter(i => i.mirror_of)
        .map(i => [i.item_id, i.mirror_of.item_id, i.mirror_of.basis]).sort();
      assert.deepEqual(got, wanted(e), spec.module_id);
      checked++;
    }
    assert.ok(checked >= 1, 'no view model has an expected-mirrors entry');
  });
});
