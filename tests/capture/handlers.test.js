// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Unit tests for the plugin handlers in capture.js (the "readers" slice).
 *
 * When an `answered` test breaks, it breaks quietly: it checks truthiness, or measures the
 * wrong collection, and the census still looks believable. These tests feed synthetic
 * Knockout view models to the shipped readers, cut out of capture.js between its two
 * markers; if a marker moves, loading fails at once.
 */
'use strict';
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const { sliceReaders, obs, coll, mdl } = require('./vm_harness.js');

const { HANDLERS, AUTOM, DND, readHotspots, NOTEBOOK_NOTE } = sliceReaders();
const opt = r => (r && r.options) || [];
const ev = r => (r && r.answered_evidence) || {};
const ag = r => ev(r).author_grid || {};

describe('handlers', () => {
  for (const [key, type] of [['kcImageLabelling', 'image-labeling'], ['drag-and-drop', 'drag-and-drop-labeling']]) {
    const content = {
      question: '<p>Place the <b>labels</b>.</p>',
      labelItems: coll(mdl({ label: 'alpha' }), mdl({ label: 'beta' })),
      labelLocationItems: coll(mdl({ anchorX: 0.1 }), mdl({ anchorX: 0.2 }), mdl({ anchorX: 0.3 })),
      presentedRatio: obs(1.5),
      imageResourceKey: 'img-42', altTextDescription: 'a figure'
    };

    test(`handlers: ${key} with no links is unanswered and keeps its stimulus`, () => {
      const r = HANDLERS[key](content, { linkedLabels: coll() });
      assert.equal(r.type, type);
      assert.equal(r.answered, false);
      assert.equal(r.prompt, 'Place the labels.');
      assert.match(r.constraints.note, /locations=3/);
      assert.match(r.constraints.note, /presentedRatio=1\.5000/);
      assert.equal(r.media[0].key, 'img-42');
    });

    test(`handlers: ${key} with one link is answered and resolves the label text`, () => {
      const r = HANDLERS[key](content, { linkedLabels: coll(mdl({ labelIndex: 1, locationIndex: 2 })) });
      assert.equal(r.answered, true);
      assert.equal(r.ref.student_answer, 'beta -> location 2');
      assert.equal(r.ref.note, 'linked=1/2');
    });

    test(`handlers: ${key} index 0 on both sides is an answer, not a falsy value`, () => {
      const r = HANDLERS[key](content, { linkedLabels: coll(mdl({ labelIndex: 0, locationIndex: 0 })) });
      assert.equal(r.answered, true);
    });

    test(`handlers: ${key} emits the label pool sorted, with positional ids`, () => {
      // 'zeta' first in source order: a source-order regression is visible on this list.
      const r = HANDLERS[key]({ ...content,
        labelItems: coll(mdl({ label: 'zeta' }), mdl({ label: 'eta' }), mdl({ label: 'delta' })) },
        { linkedLabels: coll() });
      assert.deepEqual(opt(r).map(o => o.text), ['delta', 'eta', 'zeta']);
      assert.deepEqual(opt(r).map(o => o.option_id), ['o1', 'o2', 'o3']);
      assert.equal(r.constraints.options_count, 3);
    });

    test(`handlers: ${key} drops blank labels from the pool`, () => {
      const r = HANDLERS[key]({ ...content, labelItems: coll(mdl({ label: 'kappa' }), mdl({ label: '' })) },
        { linkedLabels: coll() });
      assert.deepEqual(opt(r).map(o => o.text), ['kappa']);
    });
  }

  const catContent = {
    question: 'Sort these.',
    categoryItems: coll(mdl({ category: 'Group one' }), mdl({ category: 'Group two' })),
    labelItems: coll(mdl({ label: 'item p' }), mdl({ label: 'item q' }))
  };
  const cats = HANDLERS.categories;

  test('handlers: categories with an empty answer collection is unanswered', () => {
    const r = cats(catContent, { categoryAnswerItems: coll() });
    assert.equal(r.type, 'categorization');
    assert.equal(r.answered, false);
    assert.match(r.constraints.note, /shape=empty-collection answer_items=0/);
  });

  test('handlers: categories entries with empty labelAnswerItems give answered false', () => {
    const r = cats(catContent, {
      categoryAnswerItems: coll(mdl({ labelAnswerItems: coll() }), mdl({ labelAnswerItems: coll() })) });
    assert.equal(r.answered, false);
    assert.match(r.constraints.note, /shape=nested-labelAnswerItems/);
  });

  test('handlers: categories one placement is answered and resolves bin and label', () => {
    const r = cats(catContent, {
      categoryAnswerItems: coll(mdl({ labelAnswerItems: coll(mdl({ labelIndex: 0 })) }),
                                mdl({ labelAnswerItems: coll() })) });
    assert.equal(r.answered, true);
    assert.equal(r.ref.student_answer, 'Group one: item p');
    assert.match(r.ref.note, /placed=1\/2/);
  });

  test('handlers: categories emits sorted labels and bins in display order', () => {
    const r = cats({ ...catContent,
      labelItems: coll(mdl({ label: 'mu' }), mdl({ label: 'nu' }), mdl({ label: 'lambda' })) },
      { categoryAnswerItems: coll() });
    assert.deepEqual(opt(r).map(o => o.text), ['lambda', 'mu', 'nu']);
    assert.deepEqual(r.constraints.categories, ['Group one', 'Group two']);
  });

  test('handlers: categories unknown answer shape fails safe as answered and says so', () => {
    const r = cats(catContent, { categoryAnswerItems: coll(mdl({ somethingElse: 1 })) });
    assert.equal(r.answered, true);
    assert.match(r.constraints.note, /shape=flat-unmeasured/);
  });

  test('handlers: categories with only some nested entries is mixed and counts as answered', () => {
    const r = cats(catContent, { categoryAnswerItems: coll(mdl({ labelAnswerItems: coll() }), mdl({ placed: 1 })) });
    assert.equal(r.answered, true);
    assert.match(r.constraints.note, /shape=mixed-unmeasured/);
  });

  test('handlers: categories records which inner keys carried the text', () => {
    const titled = cats({ question: 'Sort.', categoryItems: coll(mdl({ title: 'Bin' })),
                          labelItems: coll(mdl({ label: 'thing' })) }, { categoryAnswerItems: coll() });
    assert.match(titled.constraints.note, /text_keys=title\/label/);
    assert.ok(titled.constraints.note.startsWith('categories=1 labels=1'));
    const other = cats({ question: 'Sort.', categoryItems: coll(mdl({ name: 'Bin' })),
                         labelItems: coll(mdl({ text: 'thing' })) }, { categoryAnswerItems: coll() });
    assert.match(other.constraints.note, /text_keys=name\/text/);
  });

  test('handlers: select counts selected indices, not resolved text', () => {
    const r = HANDLERS.select({ question: 'Pick one', answers: coll(mdl({ answer: 'First' }), mdl({ answer: 'Second' })) },
                              { selectedAnswers: obs([0]) });
    assert.equal(r.type, 'multiple-choice');
    assert.equal(r.answered, true);
    assert.deepEqual(r.options, [{ option_id: 'o1', text: 'First' }, { option_id: 'o2', text: 'Second' }]);
    assert.equal(r.constraints.note, 'single-select');
    assert.deepEqual(r.ref.student_selection, ['First']);
    const none = HANDLERS.select({ question: 'Pick', answers: coll(mdl({ answer: 'A' })), multiselect: true },
                                 { selectedAnswers: obs([]) });
    assert.equal(none.answered, false);
    assert.equal(none.constraints.note, 'multiselect');
  });

  test('handlers: text-question answered is an emptiness test on the de-HTMLed text', () => {
    assert.equal(HANDLERS['text-question']({ question: 'Q' }, { answer: '<p> </p>' }).answered, false);
    const r = HANDLERS['text-question']({ question: '<p>Line one</p><p>Line two</p>' }, { answer: 'Yes' });
    assert.equal(r.answered, true);
    assert.equal(r.prompt, 'Line one\nLine two');
  });

  test('handlers: image-annotation geometry follows the input mode', () => {
    const annotateOnly = HANDLERS['image-annotation'](
      { inputMode: 'allowAnnotation', questionText: 'Mark it', predefinedImageKey: '7', presentedRatio: 0.75 },
      { annotations: [] });
    assert.equal(annotateOnly.type, 'image-annotation');
    assert.equal(annotateOnly.answered, false);
    assert.equal(annotateOnly.target_geometry.presented_ratio, 0.75);
    assert.equal(annotateOnly.target_geometry.image_key, '7');
    assert.equal(annotateOnly.target_geometry.accepts_upload, false);
    assert.equal(annotateOnly.automation_override, null);

    const emptySlot = HANDLERS['image-annotation'](
      { inputMode: 'allowImageAndAnnotation', questionText: 'Upload', presentedRatio: 1.2 },
      { annotations: [], imageKey: '', presentedRatio: 0 });
    assert.equal(emptySlot.type, 'upload-and-annotate');
    assert.equal(emptySlot.answered, false);
    assert.equal(emptySlot.target_geometry.presented_ratio, null, 'an empty slot has no ratio, never 1');
    assert.equal(emptySlot.target_geometry.geometry_source, 'unavailable-until-presented');
    assert.equal(emptySlot.automation_override, 'degrade-todo');
  });

  test('handlers: an uploaded image with no annotations is already an answer', () => {
    const r = HANDLERS['image-annotation'](
      { inputMode: 'allowImageAndAnnotation', questionText: 'Upload', predefinedImageKey: '8' },
      { annotations: [], imageKey: '9', presentedRatio: 1.65 });
    assert.equal(r.answered, true);
    assert.deepEqual(r.media.map(m => [m.role, m.key]), [['author-supplied', '8'], ['student-upload', '9']]);
    assert.equal(r.target_geometry.image_key, '9');
    assert.equal(r.target_geometry.presented_ratio, 1.65);
  });

  const table = HANDLERS['table-question'];
  const row = (...texts) => mdl({ cells: coll(...texts.map(t =>
    (t && t.h) ? mdl({ content: t.t, headerColumn: true }) : mdl({ content: t }))) });
  const hdr = (...texts) => mdl({ headerRow: true, cells: coll(...texts.map(t => mdl({ content: t }))) });

  test('handlers: authored row labels in an unflagged column read as answered and are flagged', () => {
    const r = table({ question: 'Plan' }, { rows: coll(row('Row A:', '', ''), row('Row B:', '', ''), row('Row C:', '', '')) });
    assert.equal(r.answered, true);
    assert.deepEqual(ev(r).filled_map, ['#..', '#..', '#..']);
    assert.ok(ev(r).answered_suspect);
  });

  test('handlers: an authored L of headers and labels is flagged with an empty interior', () => {
    const r = table({ question: 'Compare' }, { rows: coll(
      row('', 'Col 1', 'Col 2'), row('Row A', '', ''), row('Row B', '', ''), row('Row C', '', '')) });
    assert.equal(r.answered, true, 'answered is never flipped by a heuristic');
    assert.deepEqual(ev(r).filled_map, ['.##', '#..', '#..', '#..']);
    assert.match((ev(r).answered_suspect || {}).basis || '', /interior 3x2 holds no text/);
  });

  test('handlers: learner text across the whole grid raises no authored flag', () => {
    const r = table({ question: 'Compare' }, { rows: coll(
      row('', 'Col 1', 'Col 2'), row('Row A', 'x1', 'x2'), row('Row B', 'y1', 'y2')) });
    assert.equal(r.answered, true);
    assert.equal(ev(r).answered_suspect, undefined);
  });

  test('handlers: a flagged header row over empty cells stays unanswered', () => {
    const r = table({ question: 'Compare' }, { rows: coll(hdr('Head 1', 'Head 2'), row('', ''), row('', '')) });
    assert.equal(r.answered, false);
    assert.equal(ev(r).answered_suspect, undefined);
    assert.deepEqual(r.constraints.column_headers, ['Head 1', 'Head 2']);
  });

  const authorRows = () => coll(row('Head 1', 'Head 2'), row('Row A', ''), row('Row B', ''), row('Row C', ''));

  test('handlers: the author grid turns authored labels into a marks', () => {
    const r = table({ question: 'Grid', rows: authorRows() }, { rows: authorRows() });
    assert.equal(r.answered, false);
    assert.deepEqual(ev(r).filled_map, ['aa', 'a.', 'a.', 'a.']);
    assert.equal(ag(r).status, 'read');
    assert.equal(ag(r).authored_cells, 5);
    assert.equal(ev(r).answered_suspect, undefined);
    assert.deepEqual(r.constraints.authored_cells, [
      { row: 0, col: 0, text: 'Head 1' }, { row: 0, col: 1, text: 'Head 2' },
      { row: 1, col: 0, text: 'Row A' }, { row: 2, col: 0, text: 'Row B' }, { row: 3, col: 0, text: 'Row C' }]);
  });

  test('handlers: one student cell beside authored labels is answered', () => {
    const r = table({ question: 'Grid', rows: authorRows() }, { rows: coll(
      row('Head 1', 'Head 2'), row('Row A', 'student text'), row('Row B', ''), row('Row C', '')) });
    assert.equal(r.answered, true);
    assert.deepEqual(ev(r).filled_map, ['aa', 'a#', 'a.', 'a.']);
  });

  test('handlers: a pre-filled cell edited by the learner counts as learner text', () => {
    const r = table({ question: 'Grid', rows: authorRows() }, { rows: coll(
      row('Head 1', 'Head 2'), row('Row A edited', ''), row('Row B', ''), row('Row C', '')) });
    assert.equal(r.answered, true);
    assert.equal(ev(r).filled_map[1], '#.');
  });

  test('handlers: header row and header column stay out of filled_map and authored_cells', () => {
    const grid = () => coll(hdr('Key', 'Left', 'Right'), row('Row A', '', ''), row({ t: 'Row B', h: true }, '', ''));
    const r = table({ question: 'Sides', rows: grid() }, { rows: grid() });
    assert.deepEqual(ev(r).filled_map, ['a..', 'h..']);
    assert.equal(r.answered, false);
    assert.deepEqual(r.constraints.authored_cells, [{ row: 1, col: 0, text: 'Row A' }]);
  });

  test('handlers: a missing author grid falls back to the old count', () => {
    const r = table({ question: 'Plan' }, { rows: coll(row('Row A:', '', ''), row('Row B:', '', '')) });
    assert.equal(ag(r).status, 'absent');
    assert.equal(r.answered, true);
    assert.ok(ev(r).answered_suspect);
    assert.equal(r.constraints.authored_cells, undefined);
  });

  test('handlers: content.rows that is inp.rows itself is same-object', () => {
    const shared = coll(row('Row A:', '', ''), row('Row B:', '', ''));
    const r = table({ question: 'Plan', rows: shared }, { rows: shared });
    assert.equal(ag(r).status, 'same-object');
    assert.equal(r.answered, true);
  });

  test('handlers: an author grid of another shape is shape-mismatch', () => {
    const r = table({ question: 'Plan', rows: coll(row('Row A:', ''), row('Row B:', '')) },
                    { rows: coll(row('Row A:', '', ''), row('Row B:', '', '')) });
    assert.equal(ag(r).status, 'shape-mismatch');
    assert.equal(r.answered, true);
    assert.deepEqual(ev(r).filled_map, ['#..', '#..']);
  });

  test('handlers: table rows given as headerRow plus columns are read too', () => {
    const colRow = (header, ...texts) => mdl({ headerRow: header, columns: coll(...texts.map(t => mdl({ content: t }))) });
    const r = table({ question: 'Cols' }, { rows: coll(colRow(true, 'H1', 'H2'), colRow(false, '', 'filled')) });
    assert.equal(r.answered, true);
    assert.deepEqual(ev(r).filled_map, ['.#']);
  });

  test('handlers: hotspot popups are read with parsed positions', () => {
    const hs = readHotspots({ hotspots: coll(
      mdl({ id: 1, parentHotspot: null, hotspotType: 'CUSTOM', hotspotColour: 'BLUE', labelTitle: '',
            labelDescription: '', imageResourceKey: '55', position: '{"points":[]}' }),
      mdl({ id: 2, parentHotspot: 1, hotspotType: 'POINT', hotspotColour: 'RED', labelTitle: 'Stain A',
            imageResourceKey: '', labelDescription: 'part one\npart two\n \nlast part',
            position: '{"points":[{"x":0.05,"y":0.106}]}' }),
      mdl({ id: 3, parentHotspot: 1, hotspotType: 'POINT', hotspotColour: 'YELLOW', labelTitle: 'Stain B',
            labelDescription: 'only part', position: 'not json' })) });
    assert.equal(hs.length, 3);
    assert.equal(hs[0].image_key, '55');
    assert.equal(hs[0].parent_id, null);
    assert.equal(hs[1].title, 'Stain A');
    assert.equal(hs[1].text, 'part one\npart two\n\nlast part');
    assert.deepEqual(hs[1].points, [{ x: 0.05, y: 0.106 }]);
    assert.deepEqual([hs[1].colour, hs[1].hotspot_type], ['RED', 'POINT']);
    assert.deepEqual([hs[2].points, hs[2].title], [null, 'Stain B']);
  });

  test('handlers: a null hotspot point is dropped, never read as the origin', () => {
    const hs = readHotspots({ hotspots: [{ id: 4, labelTitle: 'P', position: { points: [null, { x: null, y: 1 }, { x: 0.2, y: 0.3 }] } }] });
    assert.deepEqual(hs[0].points, [{ x: 0.2, y: 0.3 }]);
  });

  test('handlers: hotspots given as a plain array or absent are handled', () => {
    assert.deepEqual(readHotspots({ hotspots: [{ id: 9, labelTitle: 'Plain', labelDescription: 'array' }] })
      .map(h => h.title), ['Plain']);
    assert.equal(readHotspots({}).length, 0);
  });

  test('handlers: drag-family types write through the view model and degrade when single-attempt', () => {
    for (const t of ['image-labeling', 'drag-and-drop-labeling', 'categorization']) {
      assert.equal(AUTOM[t], 'structured-path');
      assert.equal(DND.has(t), true);
    }
  });

  test('handlers: notebook and hotspot have no handler, and the notebook note says why', () => {
    assert.equal(HANDLERS.notebook, undefined);
    assert.equal(HANDLERS.hotspot, undefined);
    assert.match(NOTEBOOK_NOTE, /never\s+writes to it/);
  });

  test('handlers: the handler table holds seven plugin keys (subpage recursion is the eighth reader)', () => {
    assert.deepEqual(Object.keys(HANDLERS).sort(), ['categories', 'drag-and-drop', 'image-annotation',
      'kcImageLabelling', 'select', 'table-question', 'text-question'].sort());
  });
});
