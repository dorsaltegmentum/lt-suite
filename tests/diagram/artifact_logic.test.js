// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Unit tests for the diagram engine's artifact_logic.js (node, no npm):

     node --test tests/diagram/artifact_logic.test.js
     FIG_PAGE=<case_dir>/<page>.html node --test tests/diagram/artifact_logic.test.js   (another built page)

   The synthetic graph below covers the rules; the last test checks the same rules on the worked example's
   figure data (page_fixture.js). */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {ENGINE, pageHtml} = require('./page_fixture.js');
const L = require(path.join(ENGINE, 'artifact_logic.js'));

/*  stages s1 -> s2 -> s3 (story order), s4 shares n with s3
      r(risk,s1) -> m1(mech,s1) -> m2(mech,s2) -> f1(sign,s2) -> d(dx,s3)
      a(anat,s1) ..req..> m1
      t(tx,s2) --| m2
      m2 -> i1(inv,s3) -> d
      e(sign,s4) --| x(dx,s4)            (excl)
      c1(mech,s3) -> c2(mech,s3) -> c1   (a cycle)                                  */
function box(r, s, x, y) { return {t: r + ' ' + s + ' ' + x + ',' + y, r: r, s: s, b: false, x: x, y: y, w: 100, h: 40}; }
const data = {
  W: 1000, H: 800, vb: [0, 0, 1000, 800],
  nodes: {
    r: box('risk', 's1', 0, 0), m1: box('mech', 's1', 200, 0), a: box('anat', 's1', 200, 100),
    m2: box('mech', 's2', 400, 0), t: box('tx', 's2', 400, 100), f1: box('sign', 's2', 600, 0),
    i1: box('inv', 's3', 600, 200), d: box('dx', 's3', 800, 200),
    e: box('sign', 's4', 0, 600), x: box('dx', 's4', 200, 600),
    c1: box('mech', 's3', 400, 300), c2: box('mech', 's3', 600, 300),
  },
  order: ['r', 'm1', 'a', 'm2', 't', 'f1', 'i1', 'd', 'e', 'x', 'c1', 'c2'],
  edges: [
    {a: 'r', b: 'm1', k: 'stim'}, {a: 'a', b: 'm1', k: 'req'}, {a: 'm1', b: 'm2', k: 'stim'},   // 0 1 2
    {a: 't', b: 'm2', k: 'inh'}, {a: 'm2', b: 'f1', k: 'stim'}, {a: 'f1', b: 'd', k: 'stim'},   // 3 4 5
    {a: 'm2', b: 'i1', k: 'stim'}, {a: 'i1', b: 'd', k: 'stim'}, {a: 'e', b: 'x', k: 'excl'},   // 6 7 8
    {a: 'c1', b: 'c2', k: 'stim'}, {a: 'c2', b: 'c1', k: 'stim'},                               // 9 10
  ],
  stages: [
    {id: 's1', title: 'ONE', n: 1, rect: [0, 0, 320, 160]},
    {id: 's2', title: 'TWO', n: 2, rect: [380, 0, 720, 160]},
    {id: 's3', title: 'THREE', n: 3, rect: [380, 180, 920, 360]},
    {id: 's4', title: 'FOUR', n: 3, rect: [0, 580, 320, 660]},
  ],
  roles: {}, italic: [],
};
const G = L.buildGraph(data);

test('graph: adjacency both ways, with kinds and edge indices', () => {
  assert.deepEqual(G.out.m2.map((l) => l.id), ['f1', 'i1']);
  assert.deepEqual(G.inn.m2.map((l) => [l.id, l.kind, l.i]), [['m1', 'stim', 2], ['t', 'inh', 3]]);
  assert.deepEqual(G.inn.r, []);
});

test('trace: all steps, both ways, edges only on paths', () => {
  const tr = L.trace(G, 'm2', 0);
  assert.deepEqual(tr.up, {m1: 1, t: 1, r: 2, a: 2});
  assert.deepEqual(tr.down, {f1: 1, i1: 1, d: 2});
  assert.deepEqual(tr.edges, [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.equal(tr.depth, 0);
});

test('trace: depth limit keeps direct links only', () => {
  const tr = L.trace(G, 'm2', 1);
  assert.deepEqual(tr.up, {m1: 1, t: 1});
  assert.deepEqual(tr.down, {f1: 1, i1: 1});
  assert.deepEqual(tr.edges, [2, 3, 4, 6]);
  const tr2 = L.trace(G, 'm2', 2);
  assert.deepEqual(tr2.edges, [0, 1, 2, 3, 4, 5, 6, 7]);
});

test('trace: a box with no links, an unknown box, a cycle', () => {
  const lone = L.trace(G, 'e', 0);
  assert.deepEqual(lone.up, {});
  assert.deepEqual(lone.down, {x: 1});
  assert.equal(L.trace(G, 'nope', 0), null);
  const cyc = L.trace(G, 'c1', 0);           // terminates; c2 is both upstream and downstream
  assert.deepEqual(cyc.up, {c2: 1});
  assert.deepEqual(cyc.down, {c2: 1});
  assert.deepEqual(cyc.edges, [9, 10]);
});

test('chain: upstream farthest first, downstream nearest first, with verbs', () => {
  const ch = L.chain(data, G, L.trace(G, 'm2', 0));
  assert.deepEqual(ch.up.map((g) => g.dist), [2, 1]);
  assert.deepEqual(ch.down.map((g) => g.dist), [1, 2]);
  assert.deepEqual(ch.up[1].items.map((i) => i.id), ['m1', 't']);          // reading order: stage s1 before s2
  const t = ch.up[1].items.find((i) => i.id === 't');
  assert.deepEqual(t.links, [{kind: 'inh', other: 'm2', verb: 'blocks'}]);
  const a = ch.up[0].items.find((i) => i.id === 'a');
  assert.equal(a.links[0].verb, 'is needed for');
  const d = ch.down[1].items[0];
  assert.equal(d.id, 'd');
  assert.deepEqual(d.links.map((l) => [l.other, l.verb]), [['f1', 'supported by'], ['i1', 'supported by']]);
  assert.equal(ch.nUp, 4);
  assert.equal(ch.nDown, 3);
  assert.equal(L.chain(data, G, null), null);
});

test('verb: the key wording from either end', () => {
  assert.equal(L.verb(data, 'stim', 'r', 'm1', false), 'leads to');
  assert.equal(L.verb(data, 'stim', 'r', 'm1', true), 'follows from');
  assert.equal(L.verb(data, 'stim', 'f1', 'd', false), 'supports');
  assert.equal(L.verb(data, 'excl', 'e', 'x', false), 'argues against');
  assert.equal(L.verb(data, 'excl', 'e', 'x', true), 'is argued against by');
  assert.equal(L.verb(data, 'req', 'a', 'm1', true), 'depends on');
  assert.equal(L.verb(data, 'inh', 't', 'm2', true), 'is blocked by');
});

test('steps: story order, a shared n is one step, the last step is the whole diagram', () => {
  const st = L.steps(data);
  assert.deepEqual(st.map((s) => s.stages), [['s1'], ['s2'], ['s3', 's4'], []]);
  assert.equal(st[3].whole, true);
  const noN = Object.assign({}, data, {stages: data.stages.map((s) => Object.assign({}, s, {n: null}))});
  assert.deepEqual(L.steps(noN).map((s) => s.stages), [['s1'], ['s2'], ['s3'], ['s4'], []]);
});

test('presentView: current, past, later hidden or faded; arrows take the later end', () => {
  const st = L.steps(data);
  const v = L.presentView(data, st, 1, 'gone');
  assert.deepEqual(v.stages, {s1: 'past', s2: '', s3: 'gone', s4: 'gone'});
  assert.equal(v.nodes.r, 'past');
  assert.equal(v.nodes.m2, '');
  assert.equal(v.nodes.d, 'gone');
  assert.equal(v.edges[0], 'past');      // r -> m1, both earlier
  assert.equal(v.edges[2], '');          // m1 -> m2 reaches the current stage
  assert.equal(v.edges[5], 'gone');      // f1 -> d ends in a later stage
  assert.deepEqual(v.focus, ['s2']);
  const f = L.presentView(data, st, 1, 'fade');
  assert.equal(f.nodes.d, 'fade');
  assert.equal(f.edges[5], 'fade');
  const whole = L.presentView(data, st, st.length - 1, 'gone');
  assert.ok(Object.values(whole.nodes).every((s) => s === ''));
  assert.ok(whole.edges.every((s) => s === ''));
  assert.deepEqual(whole.focus, []);
  const first = L.presentView(data, st, 0, 'gone');
  assert.ok(Object.keys(first.nodes).filter((id) => data.nodes[id].s !== 's1').every((id) => first.nodes[id] === 'gone'));
});

test('traceView: the chain stays, the rest dims; no trace dims nothing', () => {
  const v = L.traceView(data, L.trace(G, 'm2', 1));
  assert.equal(v.nodes.m2, '');
  assert.equal(v.nodes.m1, '');
  assert.equal(v.nodes.r, 'dim');
  assert.equal(v.edges[2], '');
  assert.equal(v.edges[0], 'dim');
  const none = L.traceView(data, null);
  assert.ok(Object.values(none.nodes).every((s) => s === ''));
});

test('quiz: causes before effects within a stage; progress and next', () => {
  const all = L.quizOrder(data, G, '');
  assert.equal(all.length, 12);
  assert.deepEqual(all.slice(0, 3), ['r', 'a', 'm1']);            // s1: r and a are free (r is higher), then m1
  assert.deepEqual(L.quizOrder(data, G, 's2'), ['m2', 'f1', 't']);  // the treatment after the box it blocks
  assert.deepEqual(L.quizOrder(data, G, 's3'), ['i1', 'd', 'c1', 'c2']);   // the cycle broken at its top-left box
  assert.deepEqual(L.quizOrder(data, G, 's4'), ['e', 'x']);
  const p = L.quizProgress(['t', 'm2', 'f1'], {t: true});
  assert.deepEqual(p, {total: 3, shown: 1, next: 'm2'});
  assert.equal(L.quizProgress(['t'], {t: true}).next, null);
  const dep = L.depths(data, G);
  assert.equal(dep.d, 4);
  assert.equal(dep.r, 0);
});

test('focusRect: the step stages plus a margin, clamped to the canvas; whole canvas otherwise', () => {
  assert.deepEqual(L.focusRect(data, ['s2'], 20), [360, 0, 380, 180]);
  assert.deepEqual(L.focusRect(data, ['s3', 's4'], 0), [0, 180, 920, 480]);
  assert.deepEqual(L.focusRect(data, [], 20), [0, 0, 1000, 800]);
});

test('extraState: extra layers follow their box, arrow or stage', () => {
  const v = L.presentView(data, L.steps(data), 1, 'gone');
  assert.equal(L.extraState(v, data, {node: 'd'}), 'gone');
  assert.equal(L.extraState(v, data, {a: 'm1', b: 'm2'}), '');
  assert.equal(L.extraState(v, data, {a: 'r', b: 'd'}), 'gone');   // no such arrow: the stronger of its ends
  assert.equal(L.extraState(v, data, {stage: 's1'}), 'past');
  assert.equal(L.extraState(v, data, {}), '');
});

/* ---------------------------------------------------------------- the worked example's page (or FIG_PAGE) */
test('built page: figure data is consistent and the logic runs on it', () => {
  const html = pageHtml();
  const m = html.match(/<script type="application\/json" id="fig-data">([\s\S]*?)<\/script>/);
  assert.ok(m, 'fig-data script');
  const D = JSON.parse(m[1]);
  const g = L.buildGraph(D);
  const ids = Object.keys(D.nodes);
  assert.deepEqual([...ids].sort(), [...D.order].sort());
  D.edges.forEach((e, i) => { assert.ok(e.a in D.nodes && e.b in D.nodes, `edge ${i}`); });
  const st = L.steps(D);
  assert.equal(st[st.length - 1].whole, true);
  const staged = st.flatMap((s) => s.stages);
  assert.deepEqual([...staged].sort(), D.stages.map((s) => s.id).sort(), 'every stage in one step');
  ids.forEach((id) => {
    const tr = L.trace(g, id, 0);
    assert.ok(!(id in tr.up) && !(id in tr.down));
    tr.edges.forEach((i) => {
      const e = D.edges[i];
      const onUp = (e.a in tr.up) && (e.b === id || e.b in tr.up);
      const onDown = (e.b in tr.down) && (e.a === id || e.a in tr.down);
      assert.ok(onUp || onDown, `edge ${i} on the chain of ${id}`);
    });
    const direct = L.trace(g, id, 1);
    assert.deepEqual(Object.keys(direct.up).sort(), [...new Set(g.inn[id].map((l) => l.id))].sort());
    assert.deepEqual(Object.keys(direct.down).sort(), [...new Set(g.out[id].map((l) => l.id))].sort());
  });
  for (let k = 0; k < st.length; k++) {
    const v = L.presentView(D, st, k, 'gone');
    assert.equal(Object.keys(v.nodes).length, ids.length);
    assert.equal(v.edges.length, D.edges.length);
  }
  assert.equal(L.quizOrder(D, g, '').length, ids.length);
  const expect = process.env.FIG_EXPECT || (process.env.FIG_PAGE ? '' : 'm_low:rf_gene,m_build,i_assay,dx_main,s_tired');
  if (expect) {                             // e.g. on the worked example, m_low reaches dx_main
    const [sel, want] = expect.split(':');
    const tr = L.trace(g, sel, 0);
    want.split(',').forEach((w) => assert.ok(w in tr.down || w in tr.up, `${w} on the chain of ${sel}`));
  }
});
