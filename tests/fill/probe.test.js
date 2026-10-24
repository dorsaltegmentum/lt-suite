// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Test live occupancy handlers and page traversal with synthetic view models. */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const REPO = path.resolve(__dirname, '..', '..');
const SRC = path.join(REPO, 'skills/lt-module-fill/scripts/browser/probe_panels.js');
const START = '  const U = v => (ko.isObservable(v) ? v() : v);';
const END = '  const pages = root.lessonFlowPages();';

const KO = { isObservable: v => typeof v === 'function' && v.__obs === true };
const obs = v => { const f = () => v; f.__obs = true; return f; };
const coll = (...models) => ({ models });
const mdl = attributes => ({ attributes });

function load() {
  const src = fs.readFileSync(SRC, 'utf8');
  const a = src.indexOf(START);
  const b = src.indexOf(END);
  if (a < 0) throw new Error(`start marker not found in probe_panels.js: ${START}`);
  if (b < 0 || b < a) throw new Error(`end marker not found after the start marker: ${END}`);
  // The sliced URL guard needs a synthetic content-page path.
  const body = 'const location = { pathname: "/i/inst0001/student/courses/1/runs/101/run/page/3" };\n'
             + src.slice(a, b) + '\n  return { OCCUPANCY };';
  return new Function('ko', body)(KO);
}

function check(label, got, want) {
  test(`probe: ${label}`, () => {
    assert.deepStrictEqual(got, want);
  });
}

const { OCCUPANCY } = load();

// Preserve unsorted live order so sorting would fail a test.
const labelContent = {
  labelItems: coll(mdl({ label: 'zeta' }), mdl({ label: 'alpha' })),
  labelLocationItems: coll(mdl({}), mdl({}), mdl({}))
};

for (const key of ['kcImageLabelling', 'drag-and-drop']) {
  const fn = OCCUPANCY[key];
  check(`${key}: registered`, typeof fn, 'function');
  check(`${key}: no links is empty`, fn(labelContent, { linkedLabels: coll() }).occupied, false);
  const zero = fn(labelContent, { linkedLabels: coll(mdl({ labelIndex: 0, locationIndex: 0 })) });
  check(`${key}: index 0 is occupied`, zero.occupied, true);
  check(`${key}: evidence keys`, Object.keys(zero.evidence).sort(),
        ['anchor_pool', 'label_pool', 'labels', 'linked', 'locations']);
  check(`${key}: label_pool preserves live order`, zero.evidence.label_pool,
        [{ index: 0, text: 'zeta' }, { index: 1, text: 'alpha' }]);
  check(`${key}: student placements remain counts`,
        Object.keys(zero.evidence).some(k => /link|placed|answer/.test(k) && k !== 'linked'), false);
  const partial = fn(labelContent, {
    linkedLabels: coll(mdl({ labelIndex: 1, locationIndex: 2 }), mdl({ labelIndex: 0 }))
  });
  check(`${key}: a half-written link does not count`, partial.evidence.linked, 1);
  check(`${key}: a complete link keeps the panel occupied`, partial.occupied, true);

  // Cover complete coordinates, partial coordinates and missing coordinates.
  const anchored = fn({
    labelItems: coll(mdl({ label: 'zeta' })),
    labelLocationItems: coll(
      mdl({ anchorX: 0.25, anchorY: 0.5, dropZoneLeftProportion: 0.2, dropZoneTopProportion: 0.45 }),
      mdl({ anchorX: 0, anchorY: 0.75 }),
      mdl({}))
  }, { linkedLabels: coll() });
  check(`${key}: anchor_pool has one entry per location`, anchored.evidence.anchor_pool.length, 3);
  check(`${key}: all four proportions come through`, anchored.evidence.anchor_pool[0],
        { index: 0, x: 0.25, y: 0.5, dz_x: 0.2, dz_y: 0.45 });
  check(`${key}: an anchor of 0 survives`, anchored.evidence.anchor_pool[1].x, 0);
  check(`${key}: a missing component is null`, anchored.evidence.anchor_pool[1].dz_x, null);
  check(`${key}: a location without coordinates remains listed`,
        anchored.evidence.anchor_pool[2], { index: 2, x: null, y: null, dz_x: null, dz_y: null });
  check(`${key}: location count agrees with the pool`,
        anchored.evidence.locations, anchored.evidence.anchor_pool.length);
}

const catContent = {
  categoryItems: coll(mdl({ category: 'Bin A' }), mdl({ category: 'Bin B' })),
  labelItems: coll(mdl({ label: 'alpha' }), mdl({ label: 'beta' }))
};
const cats = OCCUPANCY['categories'];
check('categories: registered', typeof cats, 'function');
check('categories: empty collection', cats(catContent, { categoryAnswerItems: coll() }).occupied, false);

const nestedEmpty = cats(catContent, {
  categoryAnswerItems: coll(mdl({ labelAnswerItems: coll() }), mdl({ labelAnswerItems: coll() }))
});
check('categories: nested shape without placements is empty', nestedEmpty.occupied, false);
check('categories: names the measured shape', nestedEmpty.evidence.shape, 'nested-labelAnswerItems');

const one = cats(catContent, {
  categoryAnswerItems: coll(mdl({ labelAnswerItems: coll(mdl({ labelIndex: 0 })) }),
                            mdl({ labelAnswerItems: coll() }))
});
check('categories: one placement is occupied', one.occupied, true);
check('categories: placement count', one.evidence.placed, 1);

// Unknown answer shapes cannot establish permission to write.
const unknown = cats(catContent, { categoryAnswerItems: coll(mdl({ somethingElse: 1 })) });
check('categories: an unmeasured answer shape is occupied', unknown.occupied, true);
check('categories: identifies the fallback shape', unknown.evidence.shape, 'flat-unmeasured');
check('categories: label_pool preserves live order', one.evidence.label_pool,
      [{ index: 0, text: 'alpha' }, { index: 1, text: 'beta' }]);
check('categories: category_pool preserves live order', one.evidence.category_pool,
      [{ index: 0, text: 'Bin A' }, { index: 1, text: 'Bin B' }]);

const titled = cats({
  categoryItems: coll(mdl({ title: 'Bin A' })),
  labelItems: coll(mdl({ label: 'alpha' }))
}, { categoryAnswerItems: coll() });
check('categories: bin text comes from title',
      titled.evidence.category_pool, [{ index: 0, text: 'Bin A' }]);

const legacy = cats({
  categoryItems: coll(mdl({ category: 'Bin B' })),
  labelItems: coll()
}, { categoryAnswerItems: coll() });
check('categories: bin text falls back to category',
      legacy.evidence.category_pool, [{ index: 0, text: 'Bin B' }]);

check('text: cleared editor markup is empty',
      OCCUPANCY['text-question']({}, { answer: '<p></p>' }).occupied, false);
check('text: visible text is occupied',
      OCCUPANCY['text-question']({}, { answer: '<p>alpha</p>' }).occupied, true);
check('select: option index 0 selected is occupied',
      OCCUPANCY['select']({}, { selectedAnswers: [0] }).occupied, true);
check('select: nothing selected is empty',
      OCCUPANCY['select']({}, { selectedAnswers: [] }).occupied, false);
check('table: a header-only grid is empty',
      OCCUPANCY['table-question']({}, {
        rows: coll(mdl({ headerRow: true, cells: coll(mdl({ content: 'structure A' })) }))
      }).occupied, false);
check('table: a body cell with text is occupied',
      OCCUPANCY['table-question']({}, {
        rows: coll(mdl({ cells: coll(mdl({ content: 'alpha' })) }))
      }).occupied, true);
check('upload+annotate: an image with zero annotations is occupied',
      OCCUPANCY['image-annotation']({ inputMode: obs('allowImageAndAnnotation') },
                                   { annotations: [], imageKey: 'image-alpha' }).occupied, true);
check('annotate-only: zero annotations is empty',
      OCCUPANCY['image-annotation']({ inputMode: obs('allowAnnotation') },
                                   { annotations: [], imageKey: '' }).occupied, false);

// Run the whole source with synthetic document, ko and location objects.
function runProbe(pageObj, pageIndex = 3, pagesList) {
  const src = fs.readFileSync(SRC, 'utf8')
    .replace(/^[ \t]*return await probeLtPanels\(\);[ \t]*\r?\n?$(?![\s\S])/m, '');
  const sandbox = {
    document: {
      querySelector: () => ({}),
      querySelectorAll: () => []
    },
    ko: Object.assign({}, KO, {
      contextFor: () => ({ $root: { lessonFlowPages: () => pagesList || [pageObj] } })
    }),
    location: {
      pathname: `/i/inst0001/student/courses/1/runs/101/run/page/${pageIndex}`
    },
    globalThis: {}
  };
  const fn = new Function('document', 'ko', 'location', 'globalThis',
                          src + '\nreturn probeLtPanels;');
  return fn(sandbox.document, sandbox.ko, sandbox.location, sandbox.globalThis)();
}

function checkProbe(label, result, read, want) {
  test(`probe: ${label}`, async () => {
    const r = await result;
    assert.deepStrictEqual(read(r), typeof want === 'function' ? want(r) : want);
  });
}

const panel = attrs => ({
  attributes: Object.assign({ panelKey: 'p3-1', pluginKey: 'text-question' }, attrs)
});
const textPanel = (extra = {}, inputExtra = {}) => panel(Object.assign({
  panelState: { content: {}, input: Object.assign({ answer: '' }, inputExtra) }
}, extra));

// Checkpoints have committed and lack allPanels.
const checkpoint = runProbe({ pageIndex: 3, committed: true, index: 8 });
checkProbe('checkpoint: refuses to probe', checkpoint, r => r.ok, false);
checkProbe('checkpoint: identifies the boundary', checkpoint, r => r.is_checkpoint, true);
checkProbe('checkpoint: reports committed state', checkpoint, r => r.committed, true);
checkProbe('checkpoint: returns no empty panel list', checkpoint, r => r.panels, undefined);

const content = runProbe({ pageIndex: 3, allPanels: [], title: 'alpha' });
checkProbe('content page: still probed', content, r => r.ok, true);
checkProbe('content page: checkpoint flag is false', content, r => r.is_checkpoint, false);

const report = runProbe({ pageIndex: 3, reportInfoPanels: [], committed: true });
checkProbe('report page: checkpoint flag is false', report, r => r.is_checkpoint, false);

const readOnly = runProbe({ pageIndex: 3, allPanels: [textPanel({ isReadOnly: true })] });
checkProbe('read-only panel: flagged', readOnly, r => r.panels[0].read_only, true);
checkProbe('read-only panel: has its own skip reason', readOnly,
           r => r.panels[0].skip_reason, 'read-only');
checkProbe('read-only panel: input lock remains false', readOnly, r => r.panels[0].locked, false);
checkProbe('read-only panel: counted', readOnly, r => r.summary.read_only, 1);

const writable = runProbe({ pageIndex: 3, allPanels: [textPanel({ isReadOnly: false })] });
checkProbe('writable panel: flag is false', writable, r => r.panels[0].read_only, false);
checkProbe('writable panel: has no skip reason', writable, r => r.panels[0].skip_reason, null);

const absent = runProbe({ pageIndex: 3, allPanels: [textPanel()] });
checkProbe('flag absent: remains null', absent, r => r.panels[0].read_only, null);
checkProbe('flag absent: counted separately', absent, r => r.summary.read_only_unknown, 1);
checkProbe('flag absent: excluded from read-only count', absent, r => r.summary.read_only, 0);

const locked = runProbe({ pageIndex: 3, allPanels: [textPanel({}, { locked: true })] });
checkProbe('locked input: has its own reason', locked, r => r.panels[0].skip_reason, 'locked');

const unmapped = runProbe({
  pageIndex: 3,
  allPanels: [panel({
    pluginKey: 'kcMysteryWidget',
    panelState: { content: {}, input: {} }
  })]
});
checkProbe('unmapped plugin: has its own reason', unmapped,
           r => r.panels[0].skip_reason, 'no-emptiness-test');

const labelPanel = key => ({ attributes: {
  panelKey: key,
  pluginKey: 'kcImageLabelling',
  panelState: {
    // Preserve unsorted live order in the shallow pool projection.
    content: {
      labelItems: coll(mdl({ label: 'zeta' }), mdl({ label: 'alpha' })),
      labelLocationItems: coll(
        mdl({ anchorX: 0.1, anchorY: 0.2, dropZoneLeftProportion: 0.05, dropZoneTopProportion: 0.15 }),
        mdl({ anchorX: 0.6, anchorY: 0.8 }))
    },
    input: { linkedLabels: coll() }
  }
}});

const pooled = runProbe({ pageIndex: 3, allPanels: [labelPanel('p3-2'), textPanel()] });
checkProbe('pools: keyed by panel_key', pooled, r => Object.keys(r.pools), ['p3-2']);
checkProbe('pools: names the source plugin', pooled,
           r => r.pools['p3-2'].plugin_key, 'kcImageLabelling');
checkProbe('pools: labels are flat strings in live order', pooled,
           r => r.pools['p3-2'].labels, ['zeta', 'alpha']);
checkProbe('pools: anchors preserve positions and missing components', pooled,
           r => r.pools['p3-2'].anchors,
           ['0 0.1000 0.2000 0.0500 0.1500', '1 0.6000 0.8000 - -']);
checkProbe('pools: a panel without a pool contributes nothing', pooled,
           r => r.pools['p3-1'], undefined);
checkProbe('pools: labels project the evidence pool', pooled,
           r => r.pools['p3-2'].labels,
           r => r.panels[0].evidence.label_pool.map(e => e.text));

// The shallow projection must survive output serialization at depth 4.
const maxDepth = (o, d) => {
  if (o === null || typeof o !== 'object') return d;
  const vals = Array.isArray(o) ? o : Object.values(o);
  return vals.length ? Math.max(...vals.map(v => maxDepth(v, d + 1))) : d;
};
checkProbe('pools: no leaf exceeds depth 4', pooled, r => maxDepth(r.pools, 1) <= 4, true);
checkProbe('pools: evidence exceeds depth 4 and validates the depth check', pooled,
           r => maxDepth(r.panels, 1) > 4, true);
checkProbe('pools_format: array position is the live label index', pooled,
           r => /position IS the live labelIndex/.test(r.pools_format.labels), true);

const p1 = { pageIndex: 1, title: 'alpha', current: false, allPanels: [] };
const p2 = {
  pageIndex: 2,
  title: 'beta',
  current: true,
  allPanels: [textPanel({ panelKey: 'p2-1' })]
};
const painted = runProbe(p2, 1, [p1, p2]);
checkProbe('URL page 1 with current flow page 2 probes page 2', painted,
           r => [r.ok, r.page_index], [true, 2]);

const q1 = Object.assign({}, p1, { current: undefined });
const q2 = Object.assign({}, p2, { current: undefined });
const fallback = runProbe(q2, 2, [q1, q2]);
checkProbe('missing current flags fall back to the URL number', fallback, r => r.page_index, 2);
