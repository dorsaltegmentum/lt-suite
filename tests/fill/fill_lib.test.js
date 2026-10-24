// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Tests for fill_lib.js and the scripts emit_fill.py builds from it.
 *
 *     python3 tools/node_tests.py tests/fill --expect-file tests/fill/node-expect.json
 *
 * The real fill_lib.js and probe_panels.js run in a vm against synthetic Knockout and
 * Backbone view models. One section per rail in fill_lib.js's header. The save-wait cases
 * run the real shared/browser/lt_net.js in tests/browser/harness.js: each writes 20
 * answers, navigates at once, and counts the writes whose save had not completed. Two
 * controls (no wait, and the rejected "any request" rule) must lose writes, which proves the
 * harness sees the failure the regression cases guard. The last sections run emit_fill.py on
 * a synthetic module and execute the scripts it writes, so what is tested is what runs.
 *
 * Not testable here: whether Lt's live models accept these writes. That needs one live
 * calibration run on a scratch module before first use (docs/speed.md).
 */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const cp = require('child_process');
const F = require('./fill_env.js');
const { obs, coll, mdl, Model, panel, textPanel, tablePanel, selectPanel, labelPanel, catPanel, page,
  makeEnv, L0, hText, CFG, textJob, fourPages, run, byItem, maxDepth, J } = F;

const check = (t, label, got, want) => t.test(label, () => assert.equal(J(got), J(want)));
const yes = (t, label, cond) => check(t, label, !!cond, true);
const pyEnv = tmp => Object.assign({}, process.env, {
  PYTHONPATH: path.join(F.REPO, 'shared'), LT_SUITE_DATA: path.join(tmp, 'data'),
  LT_SUITE_CACHE: path.join(tmp, 'cache'), XDG_CONFIG_HOME: path.join(tmp, 'xdg')
});

/* ======================================================================================= */
test('rail 8: one hash, Python and node agree', async t => {
  const cps = String.fromCodePoint;
  const strings = [
    '', 'plain', '<p>a&amp;b</p><p> x&nbsp; y </p>', 'e&#x1F600;z &#128512;', 'astral ' + cps(0x1F9E0) + cps(0x1F600),
    'nbsp' + cps(0xA0) + 'and' + cps(0x202F) + 'narrow', 'bom' + cps(0xFEFF) + 'mid', 'fs' + cps(0x1C) + 'kept',
    'nel' + cps(0x85) + 'kept', 'ls' + cps(0x2028) + 'sep', '<BR />up<P>CASE</P>', '&AMP; &copy; &#0; &#99999999;',
    'tags <span class="x">in</span><td>cell</td>', 'lone ' + String.fromCharCode(0xD83D) + ' surrogate',
    '\t\n lead and trail \r\n', '1 &lt; 2 &gt; 0', 'deg 37' + cps(0xB0) + 'C ' + cps(0x3BC) + 'm'
  ];
  const titles = ['Page four', '  Page\u00a0 <b>four</b> &amp; more ', 'Line<br>break', 'Alpha &lt;beta&gt;'];
  const pairs = [[[0, 'z'], [1, 'b'], [10, 'a']], [[0, 'x' + cps(0xFF5E)], [0, 'x' + cps(0x1F600)], [0, 'xa']]];
  const code = [
    'import json, sys', `sys.path.insert(0, ${JSON.stringify(path.join(F.SKILL, 'scripts'))})`, 'import emit_fill as E',
    'd = json.loads(sys.stdin.read())',
    'print(json.dumps({"t": [[E.page_text(s), E.djb2(E.page_text(s))] for s in d["s"]],',
    '                  "p": [E.djb2(E.canon_pairs("lab:", [tuple(x) for x in ps])) for ps in d["p"]],',
    '                  "title": [E.title_hash(s) for s in d["titles"]],',
    '                  "table": E.table_hash([1, 22, 333]),',
    '                  "sel": E.djb2(E.canon_select([3, 0, 12]))}))'
  ].join('\n');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ltfill-hash-'));
  const r = cp.spawnSync('python3', ['-c', code], { input: JSON.stringify({ s: strings, p: pairs, titles }), encoding: 'utf8', env: pyEnv(tmp) });
  await check(t, 'python3 ran', r.status, 0);
  const py = JSON.parse(r.stdout || '{"t":[],"p":[],"title":[]}');
  for (const [i, s] of strings.entries()) {
    const js = L0.pageText(s);
    await check(t, `page text and djb2 agree: ${JSON.stringify(s).slice(0, 40)}`, [js, L0.djb2(js)], py.t[i]);
  }
  for (const [i, ps] of pairs.entries()) await check(t, `canonical pair hash agrees (${i})`, L0.djb2(L0.canon.labels(ps)), py.p[i]);
  for (const [i, s] of titles.entries()) await check(t, `title hash agrees: ${JSON.stringify(s)}`, L0.titleHash(s), py.title[i]);
  await check(t, 'table hash agrees', L0.tableHash([1, 22, 333]), py.table);
  await check(t, 'canonical selection hash agrees', L0.djb2(L0.canon.select([3, 0, 12])), py.sel);
  await check(t, 'djb2 of the empty string is 5381', L0.djb2(''), 5381);
  fs.rmSync(tmp, { recursive: true, force: true });
});

/* ======================================================================================= */
test('rail 1: location and run', async t => {
  const cases = [
    ['a /preview run URL', { pathname: '/i/inst0001/student/courses/1/runs/101/preview' }, 'preview-url'],
    ['a published-revisions preview', { pathname: '/i/inst0001/student/courses/1/published-revisions/42/preview' }, 'preview-url'],
    ['?preview=1 on a run page', { search: '?preview=1' }, 'preview-url'],
    ['another run (a redirect)', { pathname: '/i/inst0001/student/courses/1/runs/102/run/page/4' }, 'wrong-run'],
    ['another page', { pathname: '/i/inst0001/student/courses/1/runs/101/run/page/5' }, 'wrong-page'],
    ['another course', { pathname: '/i/inst0001/student/courses/2/runs/101/run/page/4' }, 'wrong-course'],
    ['another instance', { pathname: '/i/inst0002/student/courses/1/runs/101/run/page/4' }, 'wrong-instance'],
    ['another origin', { origin: 'https://other.example.invalid' }, 'wrong-origin'],
    ['the completion page', { pathname: '/i/inst0001/student/courses/1/runs/101/run/complete' }, 'not-a-run-page']
  ];
  for (const [label, loc, want] of cases) {
    const env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]), { location: loc });
    const r = await run(env, {}, [textJob('10', '<p>ours</p>')]);
    await check(t, `${label}: fatal ${want}`, [r.ok, r.fatal], [false, want]);
    await check(t, `${label}: nothing written`, env.writes, 0);
  }
});

test('rail 1: the URL page number is not the flow position', async t => {
  // Exercise the background route where the URL position differs. Check the displayed target using current().
  const shifted = e => { const ps = fourPages(e, [textPanel(e, '10', '')]); ps.forEach((p, i) => { p.current = obs(i === 3); }); return ps; };
  let env = makeEnv(shifted, { location: { pathname: F.RUN_PATH(3) } });
  let r = await run(env, {}, [textJob('10', '<p>ours</p>')]);
  await check(t, 'flow page 4 served at /page/3 passes the location rail', r.fatal === 'wrong-page', false);
  env = makeEnv(e => { const ps = shifted(e); ps.forEach((p, i) => { p.current = obs(i === 2); }); return ps; }, { location: { pathname: F.RUN_PATH(4) } });
  r = await run(env, {}, [textJob('10', '<p>ours</p>')]);
  await check(t, 'a page whose current() is false is the wrong page, whatever the URL says', [r.fatal, env.writes], ['wrong-page', 0]);
});

test('rail 2: the page', async t => {
  let env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]));
  let r = await run(env, { title: 'Page four, renamed' }, [textJob('10', '<p>ours</p>')]);
  await check(t, 'a title that differs from the capture is fatal', r.fatal, 'page-title-mismatch');
  env = makeEnv(e => [page(1, 'One', []), page(2, 'Two', []), page(3, 'Three', []), { pageIndex: 4, title: obs('Checkpoint'), committed: obs(false) }]);
  r = await run(env, { title: 'Checkpoint' }, [textJob('10', '<p>ours</p>')]);
  await check(t, 'a checkpoint page is fatal (no allPanels)', r.fatal, 'checkpoint-page');
  env = makeEnv(e => [page(1, 'One', []), page(2, 'Two', []), page(3, 'Three', []), { pageIndex: 4, title: obs('Report'), reportInfoPanels: [] }]);
  r = await run(env, { title: 'Report' }, [textJob('10', '<p>ours</p>')]);
  await check(t, 'the report page is fatal', r.fatal, 'report-page');
  env = makeEnv(e => [page(1, 'One', [])]);
  r = await run(env, {}, [textJob('10', '<p>ours</p>')]);
  await check(t, 'a page the client does not hold is fatal', r.fatal, 'page-not-found');
  env = makeEnv(e => [{ title: obs('One'), allPanels: [] }, { title: obs('Two'), allPanels: [] }, { title: obs('Three'), allPanels: [] }, { title: obs('Page four'), allPanels: [textPanel(e, '10', '')] }]);
  r = await run(env, {}, [textJob('10', '<p>ours</p>')]);
  await check(t, 'no pageIndex on the pages: position finds page 4', byItem(r, 'p4-10').result, 'written');
});

test('rail 4: the probe', async t => {
  let env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]));
  let r = await run(env, {}, [textJob('10', '<p>ours</p>')], { probe: async () => { throw new Error('boom'); } });
  await check(t, 'a probe that throws is fatal', [r.fatal, env.writes], ['probe-error', 0]);
  r = await run(env, {}, [textJob('10', '<p>ours</p>')], { probe: async () => ({ ok: false, reason: 'x' }) });
  await check(t, 'a probe that refuses is fatal', [r.fatal, env.writes], ['probe-refused', 0]);
  r = await run(env, {}, [textJob('10', '<p>ours</p>')], { probe: async () => ({ ok: true, page_index: 5, panels: [] }) });
  await check(t, 'a probe of another page is fatal', r.fatal, 'probe-page-mismatch');
  r = await run(env, {}, [textJob('10', '<p>ours</p>')], {
    probe: async () => ({ ok: true, page_index: 4, panels: [{ kind: 'answerable', target: { panel_key: '10' }, occupied: null }] })
  });
  await check(t, 'probe occupied null: occupancy-unknown-skipped', [byItem(r, 'p4-10').result, env.writes], ['occupancy-unknown-skipped', 0]);

  // The real probe, on real-shaped panels.
  env = makeEnv(e => fourPages(e, [
    textPanel(e, '10', '<p>SECRET-STUDENT-TEXT</p>'),
    textPanel(e, '11', '', { readOnly: true }),
    textPanel(e, '12', '', { input: { locked: true } }),
    textPanel(e, '13', '', { plugin: 'kcMysteryWidget' }),
    // a subpage container: no input, its child is reached by the recursive walk only
    panel(e, '20', 'subpage', { content: { subpages: coll([mdl({ rows: coll([mdl({ cells: coll([mdl({ panels: coll([textPanel(e, '21', '')]) })]) })]) })]) } })
  ]));
  r = await run(env, {}, [
    textJob('10', '<p>OUR-DRAFT-TEXT</p>'), textJob('11', '<p>x</p>'), textJob('12', '<p>x</p>'),
    textJob('13', '<p>x</p>', { plugin_key: undefined }), textJob('21', '<p>child</p>')
  ]);
  await check(t, 'probe occupied true: skipped by the probe', [byItem(r, 'p4-10').result, byItem(r, 'p4-10').by], ['occupied-skipped', 'probe']);
  await check(t, 'probe read_only true: refused', byItem(r, 'p4-11').result, 'read-only-refused');
  await check(t, 'probe locked: refused', byItem(r, 'p4-12').result, 'locked-refused');
  await check(t, 'an unmapped pluginKey reads null in the probe: skipped as unknown', byItem(r, 'p4-13').result, 'occupancy-unknown-skipped');
  await check(t, 'a subpage child the probe cannot see: the direct test decides', [byItem(r, 'p4-21').result, byItem(r, 'p4-21').probe], ['written', 'absent']);
  await check(t, 'exactly one write landed', env.writes, 1);
  const flatR = J(r);
  await yes(t, 'rail 9: the receipt never carries the student text', !flatR.includes('SECRET-STUDENT-TEXT'));
  await yes(t, 'rail 9: nor ours', !flatR.includes('OUR-DRAFT-TEXT') && !flatR.includes('child'));
  await yes(t, 'rail 9: nested at most 4 levels', maxDepth(r, 1) <= 4);
});

test('rail 3: single attempt, and nothing is ever clicked', async t => {
  let env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]));
  let r = await run(env, { single_attempt_keys: ['10'] }, [textJob('10', '<p>ours</p>')]);
  await check(t, 'the capture marks it single-attempt, no approval: refused', [byItem(r, 'p4-10').result, env.writes], ['refused-single-attempt', 0]);
  env = makeEnv(e => fourPages(e, [textPanel(e, '10', '', { content: { singleAttempt: true } })]));
  r = await run(env, {}, [textJob('10', '<p>ours</p>')]);
  await check(t, 'the live content reads singleAttempt, the capture did not: refused', [byItem(r, 'p4-10').result, env.writes], ['refused-single-attempt', 0]);
  r = await run(env, {}, [textJob('10', '<p>ours</p>', { single_attempt_approved: true })]);
  await check(t, 'approved: written through the model', [byItem(r, 'p4-10').result, byItem(r, 'p4-10').single_attempt], ['written', 'approved-write-only']);
  await check(t, 'approved: no Check Answer, no Submit, no click, no commit', [env.spy.checkAnswer, env.spy.submit, env.clicks, env.commits], [0, 0, 0, 0]);
  r = await run(env, { single_attempt_keys: ['10'] }, [textJob('10', '<p>ours</p>', { single_attempt_approved: 'yes' })]);
  await check(t, 'an approval flag that is not literally true does not count', byItem(r, 'p4-10').result, 'refused-single-attempt');
  // A submitted single-attempt item the plan passed: its page, and every later page, runs
  // only while it reads read-only: the probe on its own page, the view model elsewhere.
  for (const [label, ro, want] of [['reads read-only: the page runs', true, true], ['reads writable: the whole page is refused', false, false]]) {
    const e2 = makeEnv(e => fourPages(e, [textPanel(e, '30', '<p>SUBMITTED</p>', { readOnly: ro, content: { singleAttempt: true } }), textPanel(e, '31', '')]));
    const r2 = await run(e2, { single_attempt_keys: ['30'], single_attempt_locked: [[4, '30']] }, [textJob('31', '<p>ours</p>')]);
    await check(t, `a passed single-attempt item on this page that ${label}`, [r2.ok, r2.ok ? byItem(r2, 'p4-31').result : r2.fatal, e2.writes], want ? [true, 'written', 1] : [false, 'single-attempt-not-locked', 0]);
    const e4 = makeEnv(e => [page(1, 'One', []), page(2, 'Two', [textPanel(e, '30', '<p>SUBMITTED</p>', { readOnly: ro, content: { singleAttempt: true } })]), page(3, 'Three', []), page(4, 'Page four', [textPanel(e, '31', '')])]);
    const r4 = await run(e4, { single_attempt_locked: [[2, '30']] }, [textJob('31', '<p>ours</p>')]);
    await check(t, `a passed single-attempt item on an earlier page that ${label}`, [r4.ok, r4.ok ? byItem(r4, 'p4-31').result : r4.fatal, e4.writes], want ? [true, 'written', 1] : [false, 'single-attempt-not-locked', 0]);
  }
  const e3 = makeEnv(e => fourPages(e, [textPanel(e, '31', '')]));
  const r3 = await run(e3, { single_attempt_locked: [[4, '30']] }, [textJob('31', '<p>ours</p>')]);
  await check(t, 'a passed single-attempt item that cannot be found: refused', [r3.fatal, e3.writes], ['single-attempt-not-locked', 0]);
  const code = F.LIB_SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const [label, re] of [['.click(', /\.click\s*\(/], ['dispatchEvent', /dispatchEvent/], ['submit', /submit/i],
                             ['checkAnswer', /check\s*answer/i], ['commit', /commit\w*\s*\(/i], ['a form', /\.form\b/]]) {
    await yes(t, `fill_lib.js code holds no ${label}`, !re.test(code));
  }
});

test('rail 5: direct occupancy, every write path (a probe that sees none of them)', async t => {
  const env = makeEnv(e => fourPages(e, [
    textPanel(e, '10', '<p>SECRET-STUDENT-TEXT</p>'),
    tablePanel(e, '11', [{ header: true, cells: [{ text: 'Head' }, { text: 'Head2' }] }, { cells: [{ text: 'SECRET' }, {}] }]),
    selectPanel(e, '12', ['A', 'B'], [0]),
    labelPanel(e, '13', ['x'], [[0.1, 0.1]], { links: [mdl({ labelIndex: 0, locationIndex: 0 })] }).p,
    catPanel(e, '14', ['l'], ['Bin'], { placed: [mdl({ labelIndex: 0 })] }).p,
    catPanel(e, '15', ['l'], ['Bin'], { flat: true }).p
  ]));
  const blind = { probe: async () => ({ ok: true, page_index: 4, panels: [] }) };
  const r = await run(env, {}, [
    textJob('10', '<p>ours</p>'),
    { item: 'p4-11', panel_key: '11', kind: 'table', cells: [{ row: 1, col: 0, html: '<p>a</p>', h: hText('<p>a</p>') }] },
    { item: 'p4-12', panel_key: '12', kind: 'select', want: [1], want_text: ['B'], h: L0.djb2(L0.canon.select([1])) },
    { item: 'p4-13', panel_key: '13', kind: 'labels', placements: [{ label: 'x', loc: 0 }], h: 1 },
    { item: 'p4-14', panel_key: '14', kind: 'categories', placements: [{ label: 'l', cat_index: 0, cat: 'Bin' }], h: 1 },
    { item: 'p4-15', panel_key: '15', kind: 'categories', placements: [{ label: 'l', cat_index: 0, cat: 'Bin' }], h: 1 }
  ], blind);
  for (const id of ['p4-10', 'p4-11', 'p4-12', 'p4-13', 'p4-14']) {
    await check(t, `${id}: occupied-skipped by the direct test`, [byItem(r, id).result, byItem(r, id).by, byItem(r, id).probe], ['occupied-skipped', 'direct', 'absent']);
  }
  await check(t, 'categories: an unmeasured answer shape reads unknown, never empty', byItem(r, 'p4-15').result, 'occupancy-unknown-skipped');
  await check(t, 'nothing written over the student work', env.writes, 0);
  await check(t, 'the text skip reports a length, not the text', byItem(r, 'p4-10').chars, 'SECRET-STUDENT-TEXT'.length);
});

test('rail 6: table geometry', async t => {
  const grid = () => [{ header: true, cells: [{ text: 'Column A' }, { text: 'Column B' }] }, { cells: [{ text: 'Label', hcol: true }, {}] }, { cells: [{}, {}] }];
  const cell = (r, c, tx) => ({ row: r, col: c, html: `<p>${tx}</p>`, h: hText(`<p>${tx}</p>`) });
  const env = makeEnv(e => fourPages(e, [tablePanel(e, '11', grid()), tablePanel(e, '12', grid()), tablePanel(e, '13', grid()), tablePanel(e, '14', grid()), tablePanel(e, '15', grid())]));
  const r = await run(env, {}, [
    { item: 'p4-11', panel_key: '11', kind: 'table', cells: [cell(0, 1, 'x')] },
    { item: 'p4-12', panel_key: '12', kind: 'table', cells: [cell(1, 0, 'x')] },
    { item: 'p4-13', panel_key: '13', kind: 'table', cells: [cell(2, 5, 'x')] },
    { item: 'p4-14', panel_key: '14', kind: 'table', cells: [cell(9, 0, 'x')] },
    { item: 'p4-15', panel_key: '15', kind: 'table', cells: [cell(1, 1, 'one'), cell(2, 0, 'two'), cell(2, 1, 'three')] }
  ]);
  await check(t, 'a header row is refused', [byItem(r, 'p4-11').result, byItem(r, 'p4-11').bad], ['geometry-refused', '0,1 header-row']);
  await check(t, 'a header column is refused', byItem(r, 'p4-12').bad, '1,0 header-col');
  await check(t, 'a missing cell is refused', byItem(r, 'p4-13').bad, '2,5 no-cell');
  await check(t, 'a missing row is refused', byItem(r, 'p4-14').bad, '9,0 no-row');
  await check(t, 'a clean table is written, every cell checked', [byItem(r, 'p4-15').result, byItem(r, 'p4-15').cells_ok], ['written', '3/3']);
  await check(t, 'authored cells outside the targets are counted, never touched', byItem(r, 'p4-15').other_cells_filled, 3);
  await check(t, 'three writes, all on the clean table', env.writes, 3);
});

test('write paths: text, multiple choice, labels, drag-and-drop, categories', async t => {
  const env = makeEnv(e => {
    const lab = labelPanel(e, '13', ['zeta', 'alpha', 'mid'], [[0.1, 0.2], [0.5, 0.5], [0.8, 0.9]], { vm: true });
    const dnd = labelPanel(e, '14', ['b', 'a'], [[0.3, 0.3], [0.6, 0.6]], { plugin: 'drag-and-drop' });
    const cat = catPanel(e, '15', ['alpha', 'beta', 'gamma'], ['Bin A', 'Bin B'], { renderMs: 2000 });
    e.lab = lab; e.dnd = dnd; e.cat = cat;
    return fourPages(e, [textPanel(e, '10', '<p></p>'), selectPanel(e, '12', ['First', 'Second', 'Third'], []), lab.p, dnd.p, cat.p]);
  });
  const labJob = { item: 'p4-13', panel_key: '13', plugin_key: 'kcImageLabelling', kind: 'labels',
    placements: [{ label: 'alpha', loc: 2, x: 0.8, y: 0.9 }, { label: 'zeta', loc: 0, x: 0.1, y: 0.2 }, { label: 'mid', loc: 1 }] };
  labJob.h = L0.djb2(L0.canon.labels(labJob.placements.map(p => [p.loc, p.label])));
  const dndJob = { item: 'p4-14', panel_key: '14', kind: 'labels', placements: [{ label: 'a', loc: 0 }, { label: 'b', loc: 1 }] };
  dndJob.h = L0.djb2(L0.canon.labels(dndJob.placements.map(p => [p.loc, p.label])));
  const catJob = { item: 'p4-15', panel_key: '15', kind: 'categories',
    placements: [{ label: 'alpha', cat_index: 0, cat: 'Bin A' }, { label: 'gamma', cat_index: 0, cat: 'Bin A' }, { label: 'beta', cat_index: 1, cat: 'Bin B' }] };
  catJob.h = L0.djb2(L0.canon.categories(catJob.placements.map(p => [p.cat_index, p.label])));
  const r = await run(env, {}, [
    textJob('10', '<p>Delta &amp; epsilon</p><p>second</p>'),
    { item: 'p4-12', panel_key: '12', kind: 'select', want: [1], want_text: ['Second'], h: L0.djb2(L0.canon.select([1])) },
    labJob, dndJob, catJob
  ]);
  await check(t, 'text: cleared editor markup is empty, so written', byItem(r, 'p4-10').result, 'written');
  await check(t, 'text: the receipt hash is the job hash', byItem(r, 'p4-10').h, hText('<p>Delta &amp; epsilon</p><p>second</p>'));
  await check(t, 'select: written through selectedAnswers', [byItem(r, 'p4-12').result, env.pages[3].allPanels[1].attributes.panelState.input.attributes.selectedAnswers], ['written', [1]]);
  await check(t, 'labels: resolved by TEXT against the live pool (not alphabetical)', env.lab.inp.calls, [[1, 2], [0, 0], [2, 1]]);
  await check(t, 'labels: written, via the panel view model', [byItem(r, 'p4-13').result, byItem(r, 'p4-13').via], ['written', 'currentInputModel:panel']);
  await check(t, 'drag-and-drop: the same write path, via the input model', [byItem(r, 'p4-14').result, byItem(r, 'p4-14').via, env.dnd.inp.calls], ['written', 'input', [[1, 0], [0, 1]]]);
  await check(t, 'categories: waited for the page to render, then wrote all three', [byItem(r, 'p4-15').result, env.cat.inp.calls], ['written', [[0, 0], [2, 0], [1, 1]]]);
  await check(t, 'counts', r.counts, { jobs: 5, written: 5, replaced: 0, already_new: 0, skipped: 0, refused: 0, failed: 0, todo: 0 });
  await check(t, 'no click and no commit anywhere', [env.clicks, env.commits, env.spy.checkAnswer, env.spy.submit], [0, 0, 0, 0]);
});

test('write paths: a pool label stored with a numeric entity', async t => {
  const env = makeEnv(e => {
    const dnd = labelPanel(e, '14', ['<p>Token &#43; plus</p>', '<p>TKN</p>'], [[0.3, 0.3], [0.6, 0.6]], { plugin: 'drag-and-drop' });
    e.dnd = dnd;
    return fourPages(e, [dnd.p]);
  });
  const job = { item: 'p4-14', panel_key: '14', kind: 'labels', placements: [{ label: 'Token + plus', loc: 1 }, { label: 'TKN', loc: 0 }] };
  job.h = L0.djb2(L0.canon.labels(job.placements.map(p => [p.loc, p.label])));
  const r = await run(env, {}, [job]);
  await check(t, '"&#43;" in the pool matches "+" in the draft, and the hash verifies', [byItem(r, 'p4-14').result, env.dnd.inp.calls], ['written', [[0, 1], [1, 0]]]);
});

test('write paths: authored table cells and a label in two bins', async t => {
  const env = makeEnv(e => {
    e.cat = catPanel(e, '15', ['alpha', 'beta', 'alpha'], ['Bin A', 'Bin B']);
    return fourPages(e, [tablePanel(e, '11', [{ header: true, cells: [{ text: 'Column A' }, { text: 'Column B' }] }, { cells: [{}, { text: 'authored text' }] }]), e.cat.p]);
  });
  const tab = { item: 'p4-11', panel_key: '11', kind: 'table', cells: [{ row: 1, col: 0, html: '<p>gamma</p>', h: hText('<p>gamma</p>') }] };
  const cat = { item: 'p4-15', panel_key: '15', kind: 'categories',
    placements: [{ label: 'alpha', cat_index: 0, cat: 'Bin A' }, { label: 'alpha', cat_index: 1, cat: 'Bin B' }, { label: 'beta', cat_index: 1, cat: 'Bin B' }] };
  cat.h = L0.djb2(L0.canon.categories(cat.placements.map(p => [p.cat_index, p.label])));
  const r = await run(env, {}, [tab, cat]);
  await check(t, 'table: authored text in a non-target cell does not block the empty target', [byItem(r, 'p4-11').result, byItem(r, 'p4-11').probe], ['written', 'occupied']);
  await check(t, 'categories: a label in two bins takes both pool copies', [byItem(r, 'p4-15').result, env.cat.inp.calls], ['written', [[0, 0], [2, 1], [1, 1]]]);
});

test('write paths: repeated labels on an image', async t => {
  const env = makeEnv(e => {
    e.l1 = labelPanel(e, '13', ['north', 'north', 'south', 'south'], [[0.1, 0.1], [0.9, 0.1], [0.5, 0.3], [0.5, 0.9]], { plugin: 'drag-and-drop' });
    e.l2 = labelPanel(e, '14', ['north', 'north', 'south'], [[0.1, 0.1], [0.9, 0.1], [0.5, 0.3]], { plugin: 'drag-and-drop' });
    return fourPages(e, [e.l1.p, e.l2.p]);
  });
  const all = { item: 'p4-13', panel_key: '13', kind: 'labels', placements: [{ label: 'south', loc: 0 }, { label: 'north', loc: 1 }, { label: 'south', loc: 2 }, { label: 'north', loc: 3 }] };
  all.h = L0.djb2(L0.canon.labels(all.placements.map(p => [p.loc, p.label])));
  const part = { item: 'p4-14', panel_key: '14', kind: 'labels', placements: [{ label: 'north', loc: 0 }, { label: 'south', loc: 2 }], h: 1 };
  const r = await run(env, {}, [all, part]);
  await check(t, 'every copy placed: each takes the next unused copy', [byItem(r, 'p4-13').result, env.l1.inp.calls], ['written', [[2, 0], [0, 1], [3, 2], [1, 3]]]);
  await check(t, 'one of two copies placed: refused as ambiguous', byItem(r, 'p4-14').result, 'label-ambiguous');
});

test('write paths: the refusals', async t => {
  const env = makeEnv(e => {
    e.cat2 = catPanel(e, '18', ['l'], ['Bin'], { never: true });
    e.cat3 = catPanel(e, '19', ['l'], ['Bin'], { silent: true });
    return fourPages(e, [
      selectPanel(e, '12', ['First', 'Second'], []),
      selectPanel(e, '22', ['First', 'Second'], []),
      labelPanel(e, '13', ['zeta', 'alpha'], [[0.1, 0.2], [0.5, 0.5]]).p,
      labelPanel(e, '14', ['zeta', 'alpha'], [[0.1, 0.2], [0.5, 0.5]]).p,
      labelPanel(e, '15', ['zeta', 'alpha'], [[0.1, 0.2]]).p,
      labelPanel(e, '16', ['zeta', 'alpha'], [[0.1, 0.2]], { noAdd: true }).p,
      catPanel(e, '17', ['l'], ['Bin'], { flat: true }).p,
      e.cat2.p, e.cat3.p,
      catPanel(e, '20', ['l'], ['Bin'], {}).p,
      textPanel(e, '21', '', { plugin: 'select' }),
      panel(e, '23', 'text-question', { content: {}, input: { answer: '' } })
    ]);
  });
  const r = await run(env, {}, [
    { item: 'p4-12', panel_key: '12', kind: 'select', want: [1], want_text: ['Zweite'], h: 1 },
    { item: 'p4-22', panel_key: '22', kind: 'select', want: [0, 1], want_text: ['First', 'Second'], h: 1 },
    { item: 'p4-13', panel_key: '13', kind: 'labels', placements: [{ label: 'omega', loc: 0 }], h: 1 },
    { item: 'p4-14', panel_key: '14', kind: 'labels', placements: [{ label: 'alpha', loc: 1, x: 0.55, y: 0.5 }], h: 1 },
    { item: 'p4-15', panel_key: '15', kind: 'labels', placements: [{ label: 'alpha', loc: 1 }], h: 1 },
    { item: 'p4-16', panel_key: '16', kind: 'labels', placements: [{ label: 'alpha', loc: 0 }], h: 1 },
    { item: 'p4-17', panel_key: '17', kind: 'categories', placements: [{ label: 'l', cat_index: 0, cat: 'Bin' }], h: 1 },
    { item: 'p4-18', panel_key: '18', kind: 'categories', placements: [{ label: 'l', cat_index: 0, cat: 'Bin' }], h: 1 },
    { item: 'p4-19', panel_key: '19', kind: 'categories', placements: [{ label: 'l', cat_index: 0, cat: 'Bin' }], h: L0.djb2(L0.canon.categories([[0, 'l']])) },
    { item: 'p4-20', panel_key: '20', kind: 'categories', placements: [{ label: 'l', cat_index: 0, cat: 'Other bin' }], h: 1 },
    textJob('21', '<p>x</p>'),
    textJob('23', '<p>x</p>'),
    textJob('99', '<p>x</p>'),
    { item: 'p4-12b', panel_key: '12', kind: 'hotspot', h: 1 }
  ]);
  await check(t, 'select: the option text on the page differs from the capture', byItem(r, 'p4-12').result, 'option-mismatch');
  await check(t, 'select: two options on a single-select panel', byItem(r, 'p4-22').result, 'single-select-mismatch');
  await check(t, 'labels: a label not in the live pool is a stop, not a guess', byItem(r, 'p4-13').result, 'label-not-in-pool');
  await check(t, 'labels: a drafted anchor 0.05 away from the live one', byItem(r, 'p4-14').result, 'anchor-mismatch');
  await check(t, 'labels: a location the panel does not have', byItem(r, 'p4-15').result, 'anchor-mismatch');
  await check(t, 'labels: no addAnswer anywhere', byItem(r, 'p4-16').result, 'no-add-answer');
  await check(t, 'categories: an unmeasured answer shape reads occupied in the probe', [byItem(r, 'p4-17').result, byItem(r, 'p4-17').by], ['occupied-skipped', 'probe']);
  await check(t, 'categories: entries never appear: not rendered', byItem(r, 'p4-18').result, 'categories-not-rendered');
  await check(t, 'categories: a silent zero after addAnswer is the signal', byItem(r, 'p4-19').result, 'write-not-visible');
  await check(t, 'categories: the bin text on the page differs', byItem(r, 'p4-20').result, 'category-not-in-pool');
  await check(t, 'a panel whose pluginKey differs from the capture', byItem(r, 'p4-21').result, 'plugin-mismatch');
  await check(t, 'an input with no set()', byItem(r, 'p4-23').result, 'no-input-model');
  await check(t, 'a panel key the page does not hold', byItem(r, 'p4-99').result, 'no-panel');
  await check(t, 'a job kind with no write path', byItem(r, 'p4-12b').result, 'no-write-path');
  await yes(t, 'every refusal is listed in failures', r.failures.length === 13);
  await check(t, 'the one write attempted was the silent categories call', [env.writes, env.cat3.inp.calls], [1, [[0, 0]]]);
});

test('write paths: a write that throws is recorded, never rethrown', async t => {
  const env = makeEnv(e => fourPages(e, [textPanel(e, '10', '', { model: { onSet: () => { throw new Error('model refused'); } } }), textPanel(e, '11', '')]));
  const r = await run(env, {}, [textJob('10', '<p>ours</p>'), textJob('11', '<p>ours too</p>')]);
  await check(t, 'the throwing write: write-threw', byItem(r, 'p4-10').result, 'write-threw');
  await check(t, 'the next job still runs', byItem(r, 'p4-11').result, 'written');
  await check(t, 'the receipt is ok and lists the failure', [r.ok, r.failures], [true, ['p4-10 write-threw']]);
});

test('write mismatch, and the read-back waits for the save', async t => {
  const env = makeEnv(e => fourPages(e, [
    textPanel(e, '10', '', { model: { transform: (k, v) => v.replace('ours', 'theirs') } }),
    textPanel(e, '11', '', { model: { visibleAfter: 3500 } }),
    tablePanel(e, '12', [{ cells: [{}, {}] }], { transform: (k, v) => (v.includes('two') ? '' : v) })
  ]));
  const r = await run(env, {}, [textJob('10', '<p>ours</p>'), textJob('11', '<p>late</p>'),
    { item: 'p4-12', panel_key: '12', kind: 'table', cells: [{ row: 0, col: 0, html: '<p>one</p>', h: hText('<p>one</p>') }, { row: 0, col: 1, html: '<p>two</p>', h: hText('<p>two</p>') }] }]);
  await check(t, 'a model that changes the text on set: write-mismatch', byItem(r, 'p4-10').result, 'write-mismatch');
  await check(t, 'a value visible only 3.5 s after the set is read as written', byItem(r, 'p4-11').result, 'written');
  await check(t, 'one of two cells lost: partial', [byItem(r, 'p4-12').result, byItem(r, 'p4-12').cells_ok], ['partial', '1/2']);
  await check(t, 'failures name both', r.failures, ['p4-10 write-mismatch', 'p4-12 partial']);
});

test('rail 7 without LtNet: the old fallback wait', async t => {
  const one = async (o, cfg) => {
    const env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]), o);
    const r = await run(env, cfg || {}, [textJob('10', '<p>ours</p>')]);
    return { r, env };
  };
  let { r } = await one({ saveMs: 100 });
  await check(t, 'a fast save still waits the 4 s floor', [r.waited_ms, r.wait_capped, r.save_signal, r.net], [4000, false, 'fallback', 'absent']);
  ({ r } = await one({ saveMs: 6000 }));
  await check(t, 'a 6 s save is waited out (jQuery.active back at its base)', [r.waited_ms, r.wait_capped], [6000, false]);
  ({ r } = await one({ saveMs: 1e9 }));
  await check(t, 'a save that never ends stops at the 20 s cap, unconfirmed', [r.waited_ms, r.save_signal, r.next != null], [20000, 'unconfirmed', true]);
  ({ r } = await one({ saveMs: 100 }, { save_wait: Object.assign({}, F.SAVE_WAIT, { fallback_ms: 1000 }) }));
  await check(t, 'fallback_ms below 4000 is raised to the floor', r.waited_ms, 4000);
  const quiet = makeEnv(e => fourPages(e, [textPanel(e, '10', '<p>x</p>')]));
  r = await run(quiet, {}, [textJob('10', '<p>ours</p>')]);
  await check(t, 'no write, no wait', [r.writes, r.waited_ms], [0, 0]);
  const { env } = await one({ saveMs: 100 });
  await check(t, 'the last write time is left for verify', typeof env.sandbox.__ltFillLastWrite, 'number');
});

test('replace mode: only text this suite wrote, by hash', async t => {
  const old = '<p>revision one</p>', neu = '<p>revision two</p>';
  const env = makeEnv(e => fourPages(e, [
    textPanel(e, '10', old), textPanel(e, '11', neu), textPanel(e, '12', '<p>the student edited this</p>'), textPanel(e, '13', ''),
    selectPanel(e, '14', ['A'], []),
    tablePanel(e, '15', [{ cells: [{ text: 'cell one' }, {}] }]),
    tablePanel(e, '16', [{ cells: [{ text: 'not ours' }, {}] }])
  ]));
  const oh = hText(old);
  const cell = (c, tx, prev) => ({ row: 0, col: c, html: `<p>${tx}</p>`, h: hText(`<p>${tx}</p>`), oh: prev == null ? null : hText(`<p>${prev}</p>`) });
  const r = await run(env, { mode: 'replace' }, [
    textJob('10', neu, { oh }), textJob('11', neu, { oh }), textJob('12', neu, { oh }), textJob('13', neu, { oh: null }),
    { item: 'p4-14', panel_key: '14', kind: 'select', want: [0], want_text: ['A'], h: 1 },
    { item: 'p4-15', panel_key: '15', kind: 'table', cells: [cell(0, 'cell one v2', 'cell one'), cell(1, 'cell two', null)] },
    { item: 'p4-16', panel_key: '16', kind: 'table', cells: [cell(0, 'v2', 'v1'), cell(1, 'x', null)] }
  ]);
  await check(t, 'live text equal to the recorded earlier fill: replaced', byItem(r, 'p4-10').result, 'replaced');
  await check(t, 'live text equal to the new draft: already-new', byItem(r, 'p4-11').result, 'already-new');
  await check(t, 'any other text: left alone and reported', [byItem(r, 'p4-12').result, byItem(r, 'p4-12').chars], ['changed-since-fill-skipped', 'the student edited this'.length]);
  await check(t, 'an empty target is written', byItem(r, 'p4-13').result, 'written');
  await check(t, 'a non-text type is refused', byItem(r, 'p4-14').result, 'replace-unsupported-type');
  await check(t, 'a table with our cell and an empty cell: replaced', [byItem(r, 'p4-15').result, byItem(r, 'p4-15').cells_ok], ['replaced', '2/2']);
  await check(t, 'a table with a cell that is not ours: skipped whole', byItem(r, 'p4-16').result, 'changed-since-fill-skipped');
  await check(t, 'the probe saw 10 and 11 occupied and replace mode went on to the hash', [byItem(r, 'p4-10').probe, byItem(r, 'p4-11').probe], ['occupied', 'occupied']);
  await check(t, 'counts', [r.counts.replaced, r.counts.written, r.counts.already_new, r.counts.skipped, r.counts.refused], [2, 1, 1, 2, 1]);
  const fill = makeEnv(e => fourPages(e, [textPanel(e, '10', neu), textPanel(e, '11', old)]));
  const r2 = await run(fill, {}, [textJob('10', neu), textJob('11', neu)]);
  await check(t, 'fill mode: our own earlier write reads already-new, anything else stays occupied', [byItem(r2, 'p4-10').result, byItem(r2, 'p4-11').result, fill.writes], ['already-new', 'occupied-skipped', 0]);
});

test('TODO records: uploads are never written here', async t => {
  const env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]));
  const r = await run(env, { todo: [{ item: 'p4-15', kind: 'upload' }] }, [textJob('10', '<p>ours</p>')]);
  await check(t, 'the receipt carries the todo', [r.counts.todo, r.todo], [1, ['p4-15 upload']]);
});

test('verify: the fresh-load check uses the same hash', async t => {
  const env = makeEnv(e => fourPages(e, [textPanel(e, '10', ''), tablePanel(e, '11', [{ cells: [{}, {}] }])]));
  const jobs = [textJob('10', '<p>ours</p>'), { item: 'p4-11', panel_key: '11', kind: 'table', cells: [{ row: 0, col: 1, html: '<p>c</p>', h: hText('<p>c</p>') }] }];
  const t0 = env.clk.t;
  const fr = await run(env, {}, jobs);
  await check(t, 'filled', fr.counts.written, 2);
  const lean = { 4: jobs.map(j => j.kind === 'table' ? { item: j.item, panel_key: j.panel_key, kind: 'table', cells: j.cells.map(c => ({ row: c.row, col: c.col, h: c.h })) } : { item: j.item, panel_key: j.panel_key, kind: j.kind, h: j.h }) };
  env.sandbox.__ltFillLastWrite = env.clk.t - 1000;   // as if verify ran 1 s after the last write
  const before = env.clk.t;
  let v = await env.L.verify(CFG(), lean);
  await check(t, 'same document without LtNet: fresh_load false', [v.fresh_load, v.waited], [false, 'fallback']);
  await yes(t, 'same document without LtNet: waits until 4 s after the last write', env.clk.t - (before - 1000) >= 4000);
  await check(t, 'both ok', [v.counts, v.bad], [{ ok: 2, bad: 0, missing: 0 }, []]);
  await check(t, 'each row carries the observed hash', [v.items[0][3], v.items[1][3]], [hText('<p>ours</p>'), L0.tableHash([hText('<p>c</p>')])]);
  const fresh = makeEnv(null, { pages: env.pages, page: 3 });
  v = await fresh.L.verify(CFG(), lean);
  await check(t, 'a second tab on another page of the run: fresh_load true, both ok, no wait', [v.fresh_load, v.counts.ok, v.waited], [true, 2, null]);
  env.pages[3].allPanels[0].attributes.panelState.input.attributes.answer = '<p>changed</p>';
  v = await fresh.L.verify(CFG(), Object.assign({}, lean, { 5: [textJob('99', '<p>x</p>')] }));
  await check(t, 'a changed answer is BAD, with its length only', [v.items[0].slice(0, 3), v.counts], [['p4-10', 'BAD', 7], { ok: 1, bad: 1, missing: 1 }]);
  await yes(t, 'verify carries no text', !J(v).includes('changed'));
  const pv = makeEnv(null, { pages: env.pages, location: { pathname: '/i/inst0001/student/courses/1/runs/101/preview' } });
  await check(t, 'verify refuses a preview URL', (await pv.L.verify(CFG(), lean)).fatal, 'preview-url');
  const wr = makeEnv(null, { pages: env.pages, location: { pathname: '/i/inst0001/student/courses/1/runs/102/run/page/4' } });
  await check(t, 'verify refuses another run', (await wr.L.verify(CFG(), lean)).fatal, 'wrong-run');
  await yes(t, 'the fake clock never ran backwards', env.clk.t > t0);
});

/* ======================================================================================= */
/* The save wait with the real LtNet: 20 writes, navigate at once, count lost writes.       */
const needNet = () => (F.haveNet() ? false : 'needs tests/browser/harness.js and shared/browser/lt_net.js');
const TWENTY = 20;

async function lostSave(o) {
  const env = F.netEnv({
    net: o.net,
    pages: [{ title: 'Page 1', current: true, panels: e => {
      const r = F.prng(o.seed || 20001);
      const out = Array.from({ length: TWENTY - (o.cat ? 1 : 0) }, (_, i) => F.netTextPanel(e, 10 + i, i + 1, o.model ? o.model(i, r) : {}));
      if (o.cat) out.push(o.cat(e));
      return out;
    } }]
  });
  if (o.before) o.before(env);
  const jobs = Array.from({ length: TWENTY - (o.cat ? 1 : 0) }, (_, i) => F.netJob(10 + i, '<p>draft ' + i + '</p>'));
  if (o.catJob) jobs.push(o.catJob);
  const r = await env.L.fillPage(F.netCfg({}, o.sw), jobs, F.stubProbe(1));
  const nav = env.navigate();
  return { r, nav, env };
}

/* A categories panel whose entries render 4.8 s after the page, so its write is the last. */
function slowCatPanel(env) {
  const uuid = F.uuidFor(99);
  const model = env.model(uuid, { strategy: 'immediate', latency: 300 });
  const born = env.clock.now();
  const entries = [mdl({ labelAnswerItems: coll([]) }), mdl({ labelAnswerItems: coll([]) })];
  Object.defineProperty(model.attributes, 'categoryAnswerItems', { enumerable: true, get: () => coll(env.clock.now() - born < 4800 ? [] : entries) });
  model.addAnswer = (li, ci, a) => {
    if (a === undefined) throw new Error('analytics required');
    entries[ci].attributes.labelAnswerItems.models.push(mdl({ labelIndex: li }));
    model.set('placed', li + ':' + ci);
  };
  const st = { uuids: uuid, content: { labelItems: coll([mdl({ label: 'alpha' }), mdl({ label: 'beta' })]), categoryItems: coll([mdl({ title: 'Bin A' }), mdl({ title: 'Bin B' })]) }, input: model };
  return { p: { attributes: { panelKey: '99', pluginKey: 'categories', panelState: st } }, model };
}
const slowCatJob = (() => {
  const j = { item: 'p1-99', panel_key: '99', plugin_key: 'categories', kind: 'categories', placements: [{ label: 'alpha', cat_index: 0, cat: 'Bin A' }, { label: 'beta', cat_index: 1, cat: 'Bin B' }] };
  j.h = L0.djb2(L0.canon.categories(j.placements.map(p => [p.cat_index, p.label])));
  return j;
})();

test('lost-save regression: seeded latencies 200 to 3,000 ms', { skip: needNet() }, async () => {
  const { r, nav } = await lostSave({ model: (i, rnd) => ({ strategy: 'immediate', latency: 200 + Math.floor(rnd() * 2800), events: false }) });
  assert.equal(r.ok, true);
  assert.equal(r.counts.written, TWENTY);
  assert.equal(nav.lost, 0);
  assert.equal(r.save_signal, 'net');
  assert.equal(r.keys_saved, TWENTY);
});

test('lost-save: 600 ms debounce plus a telemetry POST at 100 ms', { skip: needNet() }, async () => {
  const { r, nav } = await lostSave({
    model: () => ({ strategy: 'debounced', debounce: 600 }),
    before: env => env.after(100, () => env.request('POST', '/api/telemetry', '{"event":"tick"}'))
  });
  assert.equal(nav.lost, 0);
  assert.ok(['model', 'net'].includes(r.save_signal), r.save_signal);
  assert.ok(r.waited_ms >= 600, String(r.waited_ms));
});

test('lost-save: serial save queue with 400 ms gaps', { skip: needNet() }, async () => {
  const { r, nav } = await lostSave({ model: () => ({ strategy: 'serial', gap: 400 }) });
  assert.equal(nav.lost, 0);
  assert.ok(['model', 'net'].includes(r.save_signal), r.save_signal);
  assert.ok(r.waited_ms > 4000, 'the serial queue outlasts the old 4 s floor: ' + r.waited_ms);
});

test('lost-save: 5 s render wait before the last write', { skip: needNet() }, async () => {
  const { r, nav } = await lostSave({ model: () => ({ strategy: 'immediate', latency: 150 }), cat: slowCatPanel, catJob: slowCatJob });
  assert.equal(byItem(r, 'p1-99').result, 'written');
  assert.equal(nav.lost, 0);
  assert.ok(['model', 'net'].includes(r.save_signal), r.save_signal);
});

test('lost-save: WebSocket send before the first write', { skip: needNet() }, async () => {
  const { r, nav, env } = await lostSave({
    model: () => ({ strategy: 'immediate', latency: 400, events: false }),
    before: e => { const ws = new e.ctx.WebSocket('/socket'); ws.send(JSON.stringify({ keys: [F.uuidFor(1), F.uuidFor(2)] })); }
  });
  assert.equal(env.wsSent.length, 1);
  assert.equal(nav.lost, 0);
  assert.equal(r.save_signal, 'net');
});

test('lost-save: GET long-poll producing PerformanceObserver entries', { skip: needNet() }, async () => {
  const { r, nav } = await lostSave({
    model: () => ({ strategy: 'immediate', latency: 900, events: false }),
    before: e => {
      e.request('GET', '/api/poll?panels=' + F.uuidFor(1) + ',' + F.uuidFor(2), null, { latency: 300 });
      e.after(200, () => e.perf(8));
    }
  });
  assert.equal(nav.lost, 0);
  assert.equal(r.save_signal, 'net');
  assert.ok(r.waited_ms >= 900, String(r.waited_ms));
});

test('lost-save: fetch reference captured before install()', { skip: needNet() }, async () => {
  // Lt kept a fetch reference from before LtNet hooked it: LtNet never sees the requests.
  // Backbone's request event names a transport LtNet cannot identify, so its sync
  // acknowledges nothing: the calibrated wait runs to its cap and ends unconfirmed, the
  // receipt sends the caller to the fresh-load verify, and no write is lost.
  const a = await lostSave({ model: () => ({ strategy: 'immediate', latency: 700, via: 'captured-fetch' }) });
  assert.equal(a.nav.lost, 0);
  assert.equal(a.r.save_signal, 'unconfirmed');
  assert.equal(a.r.save_confirmed, false);
  assert.equal(a.r.keys_saved, 0);
  assert.equal(a.r.model_unidentified, TWENTY);
  assert.match(String(a.r.next), /fresh-load verify/);
  // With no model events either nothing acknowledges the writes: never an early ok.
  const b = await lostSave({ model: () => ({ strategy: 'immediate', latency: 700, via: 'captured-fetch', events: false }) });
  assert.equal(b.r.save_signal, 'unconfirmed');
  assert.ok(b.r.waited_ms >= 4000, String(b.r.waited_ms));
});

test('lost-save control: no wait', { skip: F.harness() ? false : 'needs tests/browser/harness.js' }, async () => {
  const stub = () => ({ install() {}, expect() {}, waitForSave: async () => ({ save_signal: 'net', waited_ms: 0 }) });
  const { nav } = await lostSave({ net: stub, model: (i, rnd) => ({ strategy: 'immediate', latency: 200 + Math.floor(rnd() * 2800) }) });
  assert.ok(nav.lost > 0, 'a wait that returns at once must lose writes: ' + nav.lost);
});

test('lost-save control: any-request rule', { skip: F.harness() ? false : 'needs tests/browser/harness.js' }, async () => {
  // The rejected design: the first answered non-GET request clears every key.
  const stub = env => ({
    install() {}, expect() {},
    waitForSave: async () => {
      const t0 = env.clock.now();
      for (;;) {
        if (env.requests.some(q => q.method !== 'GET' && q.t1 != null && q.status >= 200 && q.status < 300 && q.t0 >= t0)) return { save_signal: 'net' };
        if (env.clock.now() - t0 > 20000) return { save_signal: 'unconfirmed' };
        await env.clock.sleep(50);
      }
    }
  });
  const deb = await lostSave({ net: stub, model: () => ({ strategy: 'debounced', debounce: 600 }), before: env => env.after(100, () => env.request('POST', '/api/telemetry', '{"event":"tick"}')) });
  assert.ok(deb.nav.lost > 0, 'debounce: ' + deb.nav.lost);
  const ser = await lostSave({ net: stub, model: () => ({ strategy: 'serial', gap: 400 }) });
  assert.ok(ser.nav.lost > 0, 'serial: ' + ser.nav.lost);
});

/* ======================================================================================= */
/* Which transport may acknowledge a save. LtNet confirms a Backbone sync only when its request */
/* event names an XHR or fetch LtNet observed; a model record needs that request's endpoint.    */
const MODEL_CAL = Object.assign({}, F.NET_CAL, { source: 'model' });
const TPL = F.NET_CAL.path_template;

test('transport: a Backbone sync on an observed request confirms a model calibration', { skip: needNet() }, async t => {
  const { r, nav } = await lostSave({ model: () => ({ strategy: 'immediate', latency: 300 }), sw: { calibration: MODEL_CAL } });
  await check(t, 'transport: model, every key saved, nothing lost, no extra step',
    [r.save_signal, r.save_confirmed, r.keys_saved, r.calibrated, nav.lost, r.next], ['model', true, TWENTY, true, 0, null]);
});

test('transport: a Backbone sync over an unobserved fetch never confirms a model calibration', { skip: needNet() }, async t => {
  const { r, nav } = await lostSave({ model: () => ({ strategy: 'immediate', latency: 300, via: 'captured-fetch' }), sw: { calibration: MODEL_CAL, max_ms: 3000 } });
  await check(t, 'transport: unconfirmed at the cap with every key pending',
    [r.save_signal, r.save_confirmed, r.keys_saved, r.keys_pending, r.waited_ms >= 3000, nav.lost], ['unconfirmed', false, 0, TWENTY, true, 0]);
  await yes(t, 'transport: the next step is the fresh-load verify', /fresh-load verify/.test(r.next || ''));
});

test('transport: a model record without an endpoint is stale; the fill runs the fallback rule', { skip: needNet() }, async t => {
  const { r } = await lostSave({ model: () => ({ strategy: 'immediate', latency: 300 }), sw: { calibration: { version: 1, source: 'model' } } });
  await check(t, 'transport: stale for no endpoint, fallback wait',
    [r.calibration_stale, r.stale_reason, r.calibrated, r.save_signal, r.save_confirmed], [true, 'no-endpoint', false, 'fallback', false]);
});

test('transport: uncalibrated, an unobserved save ends fallback with every key pending', { skip: needNet() }, async t => {
  const { r, nav } = await lostSave({ model: () => ({ strategy: 'immediate', latency: 700, via: 'captured-fetch' }), sw: { calibration: null } });
  await check(t, 'transport: fallback, nothing acknowledged, nothing to calibrate from',
    [r.save_signal, r.keys_saved, r.keys_pending, r.ack_source, r.ack_endpoints, nav.lost], ['fallback', 0, TWENTY, null, [], 0]);
  await yes(t, 'transport: fallback with keys pending sends the caller to the fresh-load verify', /fresh-load verify/.test(r.next || ''));
});

test('transport: uncalibrated, an observed save names its endpoint for the calibration', { skip: needNet() }, async t => {
  const { r } = await lostSave({ model: () => ({ strategy: 'immediate', latency: 300 }), sw: { calibration: null } });
  await check(t, 'transport: every key identified on one endpoint, the first by its Backbone sync',
    [r.save_signal, r.keys_saved, r.keys_pending, r.ack_source, r.ack_method, r.ack_path_template, r.ack_endpoints, r.next],
    ['fallback', TWENTY, 0, 'model', 'PUT', TPL, ['PUT ' + TPL], null]);
});

test('transport: a model or net result with a key pending reads as unconfirmed', { skip: F.harness() ? false : 'needs tests/browser/harness.js' }, async t => {
  const stub = () => ({ install() {}, expect() {}, waitForSave: async () => ({ save_signal: 'model', keys_total: 2, keys_saved: 1, keys_pending: 1, waited_ms: 10 }) });
  const { r } = await lostSave({ net: stub, model: () => ({ strategy: 'immediate', latency: 100 }) });
  await check(t, 'transport: never ok while a key has no identified save', [r.save_signal, r.save_confirmed], ['unconfirmed', false]);
});

/* ======================================================================================= */
/* Several pages in one call.                                                                 */
function batchEnv(o) {
  o = o || {};
  return F.netEnv({
    net: o.net,
    pages: [1, 2, 3].map(n => ({ title: 'Page ' + n, current: n === 1, panels: e => [10, 11, 12].map((k, i) => F.netTextPanel(e, k, n * 10 + i, (o.model && o.model(n)) || {})) }))
  });
}
const batchPlan = (pages, o) => pages.map(n => ({
  page: n, title_hash: (o && o.badHash === n) ? 12345 : L0.titleHash('Page ' + n),
  jobs: [10, 11, 12].map(k => F.netJob(k, '<p>page ' + n + ' item ' + k + '</p>', n)),
  todo: [], single_attempt_keys: [], single_attempt_locked: []
}));
const batchCfg = (pages, extra) => Object.assign(F.netCfg({}), { batch_pages: pages, never_navigated: [], stop_before_page: null }, extra || {});

test('batch: 3 pages, one wait, no lost write, 3 page records', { skip: needNet() }, async t => {
  const env = batchEnv();
  const r = await env.L.fillPages(batchCfg([1, 2, 3]), batchPlan([1, 2, 3]), F.stubProbe(1));
  const nav = env.navigate();
  await check(t, 'batch: ok, 9 written', [r.ok, r.counts.written], [true, 9]);
  await check(t, 'batch: one page record per page', r.pages.map(p => [p.page, p.written]), [[1, 3], [2, 3], [3, 3]]);
  await check(t, 'batch: one wait over 9 keys', [r.keys_total, r.keys_saved], [9, 9]);
  await yes(t, 'batch: the wait ended on the calibrated signal', ['model', 'net'].includes(r.save_signal));
  await check(t, 'batch: no write lost when navigating at once', nav.lost, 0);
  await yes(t, 'batch: the receipt nests at most 4 levels', maxDepth(r, 1) <= 4);
});

test('batch: a third page that never saves returns unconfirmed', { skip: needNet() }, async t => {
  const env = batchEnv({ model: n => (n === 3 ? { strategy: 'never' } : {}) });
  const r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: F.NET_CAL, max_ms: 6000 }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: never ok when a page never saves', [r.save_signal, r.keys_pending], ['unconfirmed', 3]);
  await yes(t, 'batch: the next step is a fresh-load verify', /fresh-load verify/.test(r.next || ''));
});

test('batch: refused targets write nothing', { skip: needNet() }, async t => {
  let env = batchEnv();
  let r = await env.L.fillPages(batchCfg([1, 2]), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: a target not in batch_pages', [r.fatal, r.on_page, env.writes.length], ['page-not-in-batch', 3, 0]);
  env = batchEnv();
  r = await env.L.fillPages(batchCfg([1, 2, 3], { never_navigated: [2] }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: a target in never_navigated', [r.fatal, r.on_page, env.writes.length], ['page-never-navigated', 2, 0]);
  env = batchEnv();
  r = await env.L.fillPages(batchCfg([1, 2, 3]), batchPlan([1, 2, 3], { badHash: 3 }), F.stubProbe(1));
  await check(t, 'batch: a wrong title_hash', [r.fatal, r.on_page, env.writes.length], ['page-title-mismatch', 3, 0]);
  env = batchEnv();
  r = await env.L.fillPages(batchCfg([1, 2, 3], { stop_before_page: 3 }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: a target at or past the first door', [r.fatal, env.writes.length], ['page-at-or-past-boundary', 0]);
  env = batchEnv();
  const plan = batchPlan([1, 2]);
  plan[1].jobs = [{ item: 'p2-10', panel_key: '10', kind: 'labels', placements: [{ label: 'alpha', loc: 0 }], h: 1 }];
  r = await env.L.fillPages(batchCfg([1, 2]), plan, F.stubProbe(1));
  await check(t, 'batch: an addAnswer job on a page that is not displayed', byItem(r, 'p2-10').result, 'needs-displayed-page');
});

test('batch: a stale or missing calibration refuses before any write', { skip: needNet() }, async t => {
  let env = batchEnv({});
  env.ctx.document.scripts = [{ src: F.ORIGIN + '/static/app.1a2b3c.js' }];
  const stale = Object.assign({}, F.NET_CAL, { version: 1, client_sig: 'c1-9-ffffffff' });
  let r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: stale }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: another client build is refused', [r.ok, r.fatal, r.writes, r.calibration_stale, env.writes.length], [false, 'calibration-stale', 0, true, 0]);
  r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: Object.assign({}, F.NET_CAL, { version: 2 }) }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: another record version is refused', [r.fatal, env.writes.length], ['calibration-stale', 0]);
  r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: null }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: no calibration is refused', [r.fatal, env.writes.length], ['batch-uncalibrated', 0]);
  r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: { version: 1, source: 'model' } }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: a model record without a write endpoint is refused',
    [r.fatal, r.calibration_stale, r.stale_reason, env.writes.length], ['calibration-stale', true, 'no-endpoint', 0]);
  r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: Object.assign({}, F.NET_CAL, { method: 'GET' }) }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: a record naming a read method is refused', [r.fatal, r.stale_reason, env.writes.length], ['calibration-stale', 'no-endpoint', 0]);
});

test('batch: Backbone saves over an unobserved fetch leave the batch unconfirmed', { skip: needNet() }, async t => {
  const env = batchEnv({ model: () => ({ via: 'captured-fetch' }) });
  const r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: MODEL_CAL, max_ms: 6000 }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  const nav = env.navigate();
  await check(t, 'batch: unconfirmed, 9 keys pending, no write lost', [r.save_signal, r.keys_saved, r.keys_pending, nav.lost], ['unconfirmed', 0, 9, 0]);
  await yes(t, 'batch: the fresh-load verify comes next', /fresh-load verify/.test(r.next || ''));
});

test('batch: a model calibration on observed requests confirms the batch', { skip: needNet() }, async t => {
  const env = batchEnv();
  const r = await env.L.fillPages(batchCfg([1, 2, 3], { save_wait: Object.assign({}, F.SAVE_WAIT, { calibration: MODEL_CAL }) }), batchPlan([1, 2, 3]), F.stubProbe(1));
  await check(t, 'batch: model, 9 of 9 keys', [r.save_signal, r.keys_saved, r.next], ['model', 9, null]);
});

test('verify: same document waits on the open ledger; a reload reads at once', { skip: needNet() }, async t => {
  const env = F.netEnv({ pages: [{ title: 'Page 1', current: true, panels: e => [F.netTextPanel(e, 10, 1, { strategy: 'immediate', latency: 2500 })] }] });
  const job = F.netJob(10, '<p>ours</p>');
  const fr = await env.L.fillPage(F.netCfg({}, { max_ms: 1000 }), [job], F.stubProbe(1));
  await check(t, 'verify: the fill call capped before the save ended', fr.save_signal, 'unconfirmed');
  const lean = { 1: [{ item: job.item, panel_key: job.panel_key, kind: 'text', h: job.h }] };
  const v = await env.L.verify(F.netCfg({}), lean);
  await check(t, 'verify: same document, fresh_load false after the ledger settles', [v.fresh_load, ['model', 'net'].includes(v.waited), v.counts.ok], [false, true, 1]);
  const v2 = await F.netEnv({ pages: [{ title: 'Page 1', current: true, panels: e => [F.netTextPanel(e, 10, 1)] }] }).L.verify(F.netCfg({}), lean);
  await check(t, 'verify: a fresh document reads at once', [v2.fresh_load, v2.waited], [true, null]);
});

test('verify: after an unobserved save the same document stays unconfirmed', { skip: needNet() }, async t => {
  const env = F.netEnv({ pages: [{ title: 'Page 1', current: true, panels: e => [F.netTextPanel(e, 10, 1, { strategy: 'immediate', latency: 300, via: 'captured-fetch' })] }] });
  const job = F.netJob(10, '<p>ours</p>');
  const fr = await env.L.fillPage(F.netCfg({}, { calibration: MODEL_CAL, max_ms: 2000 }), [job], F.stubProbe(1));
  await check(t, 'verify: the fill call ended unconfirmed with its key pending', [fr.save_signal, fr.keys_pending], ['unconfirmed', 1]);
  const lean = { 1: [{ item: job.item, panel_key: job.panel_key, kind: 'text', h: job.h }] };
  const v = await env.L.verify(F.netCfg({}, { calibration: MODEL_CAL, max_ms: 2000 }), lean);
  await check(t, 'verify: same document, the wait stays unconfirmed and proves nothing',
    [v.fresh_load, v.waited, v.waited_keys_pending], [false, 'unconfirmed', 1]);
});

test('verify: an open LtNet ledger prevents a fresh-load claim without fill bookkeeping', { skip: needNet() }, async () => {
  // Another rendered call can register a write before fillPage has stored its bookkeeping.
  const env = F.netEnv({ pages: [{ title: 'Page 1', current: true, panels: e => [F.netTextPanel(e, 10, 1, { via: 'captured-fetch' })] }] });
  const { uuid, model } = env.made[0][0];
  env.ctx.LtNet.expect(uuid, model);
  model.set('answer', '<p>ours</p>');
  const job = F.netJob(10, '<p>ours</p>');
  const v = await env.L.verify(F.netCfg({}, { calibration: MODEL_CAL, max_ms: 1000 }), { 1: [job] });
  assert.equal(v.counts.ok, 1); // The local value matches but proves no server persistence.
  assert.equal(v.fresh_load, false);
  assert.equal(v.waited, 'unconfirmed');
  assert.equal(v.waited_keys_pending, 1);
});

test('calibration identity: document id, ticket echo and the client build', { skip: needNet() }, async t => {
  const scripts = env => { env.ctx.document.scripts = [{ src: F.ORIGIN + '/static/app.1a2b3c.js' }, { src: 'https://cdn.example.invalid/x.js' }]; };
  const pages = [{ title: 'Page 1', current: true, panels: e => [F.netTextPanel(e, 10, 1)] }];
  const env = F.netEnv({ pages });
  scripts(env);
  const job = F.netJob(10, '<p>ours</p>');
  const fr = await env.L.fillPage(F.netCfg({ ticket: 'T-0011aabb' }), [job], F.stubProbe(1));
  await check(t, 'identity: the fill receipt echoes the ticket', fr.ticket, 'T-0011aabb');
  yes(t, 'identity: the receipt names its document', /^[0-9a-f]{16}$/.test(fr.doc_id));
  await check(t, 'identity: the client build is LtNet\'s signature', fr.client_sig, env.ctx.LtNet.clientSig());
  yes(t, 'identity: the signature exists when the page lists scripts', typeof fr.client_sig === 'string' && fr.client_sig.length > 0);
  const lean = { 1: [{ item: job.item, panel_key: job.panel_key, kind: 'text', h: job.h }] };
  const fresh = F.netEnv({ pages });
  scripts(fresh);
  const v = await fresh.L.verify(F.netCfg({}), lean);
  yes(t, 'identity: a fresh load has another document id', v.doc_id && v.doc_id !== fr.doc_id);
  await check(t, 'identity: the same client build in the fresh load', v.client_sig, fr.client_sig);
  const env2 = F.netEnv({ pages });
  scripts(env2);
  const cal = Object.assign({}, F.NET_CAL, { version: 1, client_sig: 'c1-9-ffffffff' });
  const r2 = await env2.L.fillPage(F.netCfg({}, { calibration: cal }), [F.netJob(10, '<p>ours</p>')], F.stubProbe(1));
  await check(t, 'identity: a record from another client build is stale', [r2.calibration_stale, r2.calibrated, r2.save_signal], [true, false, 'fallback']);
  const env3 = F.netEnv({ pages });
  scripts(env3);
  const r3 = await env3.L.fillPage(F.netCfg({}, { calibration: Object.assign({}, F.NET_CAL, { version: 1, client_sig: fr.client_sig }) }), [F.netJob(10, '<p>ours</p>')], F.stubProbe(1));
  await check(t, 'identity: a record from this client build is used', [r3.calibration_stale, r3.calibrated], [false, true]);
});

test('guard: a write path that calls a commit mutator is refused', { skip: F.haveGuard() && F.haveNet() ? false : 'needs lt_guard.js' }, async t => {
  const env = F.netEnv({ guard: true, pages: [{ title: 'Page 1', current: true, panels: e => {
    const m = F.netTextPanel(e, 10, 1);
    const set0 = m.model.set.bind(m.model);
    m.model.set = (k, v) => { e.root.commitClicked(); return set0(k, v); };
    return [m, F.netTextPanel(e, 11, 2)];
  } }] });
  env.ctx.LtGuard.check();
  const r = await env.L.fillPage(F.netCfg({}), [F.netJob(10, '<p>ours</p>'), F.netJob(11, '<p>ours too</p>')], F.stubProbe(1));
  await check(t, 'guard: the write threw and is recorded', byItem(r, 'p1-10').result, 'write-threw');
  await check(t, 'guard: guard_trips is 1 and no commit reached the page', [r.guard_trips, env.commits.length], [1, 0]);
  await check(t, 'guard: the next job still ran', byItem(r, 'p1-11').result, 'written');
});

/* ======================================================================================= */
test('emit_fill.py end to end: the scripts it writes run, both builds', async t => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ltfill-'));
  const ORIGIN = F.ORIGIN;
  const it = (id, key, type, plugin, extra) => Object.assign({
    item_id: id, panel_key: key, plugin_key: plugin, type, attempts: 'multi', assessment_type: 'noAssessment',
    commit_state: 'open', read_only: false, automation: 'structured-path', answered: false, parent_item_id: null, prompt_text: 'Q ' + id
  }, extra || {});
  const pg = (idx, title, items) => ({ page_index: idx, page_no: idx, route: null, title, is_report_page: false, is_checkpoint: false,
    url: `${ORIGIN}${F.RUN_PATH(idx)}`, items });
  const mod = {
    schema_version: 1, module_id: 'c1-r101', instance: 'inst0001', course_id: 1, run_id: 101,
    source_url: `${ORIGIN}/i/inst0001/student/courses/1/runs/101/run`,
    lesson: { title: 'Synthetic', page_count: 6, has_completion_page: true, completion_route: 'complete', run_locked: false },
    commit_boundaries: { checkpoint_pages: [], checkpoints: [], checkpoints_container_present: true },
    pages: [
      pg(1, 'One', []), pg(2, 'Two', []), pg(3, 'Three', []),
      pg(4, 'Page four', [
        it('p4-10', '10', 'text-response', 'text-question'),
        it('p4-11', '11', 'table-completion', 'table-question', { constraints: { rows: 2, cols: 2 } }),
        it('p4-12', '12', 'multiple-choice', 'select', { options: [{ option_id: 'o1', text: 'First' }, { option_id: 'o2', text: 'Second' }] }),
        it('p4-13', '13', 'image-labeling', 'kcImageLabelling', { options: [{ option_id: 'o1', text: 'alpha' }, { option_id: 'o2', text: 'mid' }, { option_id: 'o3', text: 'zeta' }], constraints: { note: 'presentedRatio=0.5 locations=3' } }),
        it('p4-14', '14', 'categorization', 'categories', { options: [{ option_id: 'o1', text: 'beta' }, { option_id: 'o2', text: 'alpha' }], constraints: { categories: ['Bin A', 'Bin B'] } }),
        it('p4-15', '15', 'upload-and-annotate', 'image-annotation', { input_mode: 'allowImageAndAnnotation', automation: 'resilient-selector' })
      ]),
      pg(5, 'Page five', [it('p5-20', '20', 'text-response', 'text-question', { attempts: 'single' }), it('p5-21', '21', 'text-response', 'text-question')]),
      pg(6, 'Page six', [it('p6-30', '30', 'text-response', 'text-question', { attempts: 'single' }), it('p6-31', '31', 'text-response', 'text-question')])
    ]
  };
  const seg = tx => ({ kind: 'claim', text: tx, provenance_refs: ['p1'] });
  const drafted = (id, extra) => Object.assign({ item_id: id, status: 'drafted', confidence: 'high', segments: [seg('Draft for ' + id)], provenance: [{ ref: 'p1', tier: 1, source_id: 'X', locator: 'slide 1' }] }, extra || {});
  fs.writeFileSync(path.join(tmp, 'module.json'), JSON.stringify(mod));
  const py = args => cp.spawnSync('python3', [F.EMIT].concat(args), { encoding: 'utf8', cwd: tmp, env: pyEnv(tmp) });
  let r = py(['approve', '--module', 'module.json', '--item', 'p5-20', '--quote', 'Yes, write p5-20 as drafted.', '--approvals', 'approvals.json', '--recorded-at', '2000-01-01T00:00:00Z']);
  await check(t, 'approve: exit 0 and an approval id', [r.status, /^A-[0-9a-f]{8}$/.test(r.stdout.trim())], [0, true]);
  const aid = r.stdout.trim();
  const ans = {
    schema_version: 2, module_id: 'c1-r101', run_id: 101, drafted_at: '2000-01-01T00:00:00Z',
    source_module: { path: 'module.json', sha256: '0'.repeat(64) },
    items: [
      drafted('p4-10', { segments: [seg('Delta & epsilon <2>'), seg('Second paragraph.')] }),
      drafted('p4-11', { table_cells: [{ row: 1, col: 0, text: 'A1' }, { row: 1, col: 1, text: 'B1' }] }),
      drafted('p4-12', { proposed_selection: ['o2'] }),
      drafted('p4-13', { label_placements: [
        { label: 'alpha', target: 'the right one, anchor 2 at 80.0% across, 90.0% down' },
        { label: 'zeta', target: 'top left, anchor 0 at 10.0% across, 20.0% down' },
        { label: 'mid', target: 'the middle, anchor 1 at 50.0% across, 50.0% down' }] }),
      drafted('p4-14', { label_placements: [{ label: 'alpha', target: 'Bin A' }, { label: 'beta', target: 'Bin B', target_index: 1 }] }),
      drafted('p4-15', { needs_external_image: true, asset_refs: ['x.jpg'] }),
      drafted('p5-20', { single_attempt_approval: { approved_by: 'user', approval_id: aid } }),
      drafted('p5-21'), drafted('p6-30'), drafted('p6-31')
    ]
  };
  fs.writeFileSync(path.join(tmp, 'answers.json'), JSON.stringify(ans));
  r = py(['payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out', '--approvals', 'approvals.json']);
  await check(t, 'payload: exit 0', r.status, 0);
  if (r.status !== 0) console.log(r.stdout, r.stderr);
  const pay = JSON.parse(fs.readFileSync(path.join(tmp, 'out', 'fill-payload.json'), 'utf8'));
  await check(t, 'payload: jobs per page', Object.fromEntries(Object.entries(pay.pages).map(([k, v]) => [k, v.jobs.map(j => j.item)])),
    { 4: ['p4-10', 'p4-11', 'p4-12', 'p4-13', 'p4-14'], 5: ['p5-20', 'p5-21'], 6: ['p6-31'] });
  await check(t, 'payload: the unapproved single-attempt item is refused before any script exists', pay.refused.map(x => [x.item, x.reason]), [['p6-30', 'refused-single-attempt']]);
  await check(t, 'payload: the page after an unapproved single-attempt item is still emitted', pay.not_emitted.map(x => x.item), []);
  await check(t, 'payload: the upload is a TODO record', pay.todo.map(x => [x.item, x.kind]), [['p4-15', 'upload']]);
  await check(t, 'payload: the approved item carries its flag and its approval id, and page 5 lists it single-attempt',
    [pay.pages[5].jobs[0].single_attempt_approved, pay.pages[5].jobs[0].approval_id, pay.pages[5].single_attempt_keys], [true, aid, ['20']]);
  await check(t, 'payload: each page carries its title hash', pay.pages[4].title_hash, L0.titleHash('Page four'));
  await yes(t, 'payload: plan.json is written for check-url', fs.existsSync(path.join(tmp, 'out', 'plan.json')));
  r = py(['payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out', '--approvals', 'approvals.json']);
  await check(t, 'payload: refuses to overwrite the record of a fill without --force', r.status, 3);
  r = py(['scripts', '--out', 'out', '--no-render', '--pages-per-call', '1']);
  await check(t, 'scripts: exit 0', r.status, 0);
  const p4 = fs.readFileSync(path.join(tmp, 'out', '_fill-c1-r101-p4.js'), 'utf8');
  const lastStmt = p4.trimEnd().split('\n').pop();
  await yes(t, 'the body ends in `return await LtFillLib.fillPage(`, for the renderer to wrap', /^return await LtFillLib\.fillPage\(/.test(lastStmt));

  // The live page for the synthetic module.
  const live = e => {
    const lab = labelPanel(e, '13', ['zeta', 'alpha', 'mid'], [[0.1, 0.2], [0.5, 0.5], [0.8, 0.9]], { vm: true });
    const cat = catPanel(e, '14', ['alpha', 'beta'], ['Bin A', 'Bin B'], { renderMs: 1000 });
    e.lab = lab; e.cat = cat;
    return [page(1, 'One', []), page(2, 'Two', []), page(3, 'Three', []), page(4, 'Page four', [
      textPanel(e, '10', ''), tablePanel(e, '11', [{ header: true, cells: [{ text: 'H1' }, { text: 'H2' }] }, { cells: [{}, {}] }]),
      selectPanel(e, '12', ['First', 'Second'], []), lab.p, cat.p,
      panel(e, '15', 'image-annotation', { content: { inputMode: 'allowImageAndAnnotation' }, input: new Model(e, { annotations: [], imageKey: '' }) })
    ]), page(5, 'Page five', [textPanel(e, '20', '', { content: { singleAttempt: true } }), textPanel(e, '21', '')]),
    page(6, 'Page six', [textPanel(e, '30', '', { content: { singleAttempt: true } }), textPanel(e, '31', '')])];
  };
  const env = makeEnv(live, { load: false });
  let rec = await F.runBody(p4, env.sandbox);
  await check(t, 'full build, page 4: five written, the upload left as a TODO', [rec.ok, rec.counts.written, rec.counts.todo, rec.failures], [true, 5, 1, []]);
  await check(t, 'full build: labels resolved by text', env.lab.inp.calls, [[1, 2], [0, 0], [2, 1]]);
  await check(t, 'full build: categories placed with the bin from text and from target_index', env.cat.inp.calls, [[0, 0], [1, 1]]);
  env.sandbox.location.pathname = F.RUN_PATH(5);
  rec = await F.runBody(fs.readFileSync(path.join(tmp, 'out', '_fill-c1-r101-p5.js'), 'utf8'), env.sandbox);
  await check(t, 'full build, page 5: the approved single-attempt item is written, never submitted', [rec.counts.written, byItem(rec, 'p5-20').single_attempt, env.spy.submit, env.spy.checkAnswer, env.clicks], [2, 'approved-write-only', 0, 0, 0]);
  env.sandbox.location.pathname = F.RUN_PATH(6);
  rec = await F.runBody(fs.readFileSync(path.join(tmp, 'out', '_fill-c1-r101-p6.js'), 'utf8'), env.sandbox);
  await check(t, 'page 6: p6-31 written, the unapproved single-attempt p6-30 never touched or submitted',
    [rec.counts.written, byItem(rec, 'p6-30').item || null, env.spy.submit, env.clicks], [1, null, 0, 0]);
  const vsrc = fs.readFileSync(path.join(tmp, 'out', '_verify-c1-r101.js'), 'utf8');
  const fresh = makeEnv(null, { pages: env.pages, load: false, page: 2 });
  const v = await F.runBody(vsrc, fresh.sandbox);
  await check(t, 'verify script in a fresh tab: all 8 ok', [v.ok, v.fresh_load, v.counts], [true, true, { ok: 8, bad: 0, missing: 0 }]);
  await yes(t, 'a verify file per call is written too', fs.existsSync(path.join(tmp, 'out', '_verify-c1-r101-p4.js')));

  // The served build: the same scripts, with both sources fetched and checked by sha256.
  r = py(['scripts', '--out', 'out', '--no-render', '--pages-per-call', '1', '--lib-mode', 'served', '--lib-url', 'http://127.0.0.1:8733']);
  await check(t, 'served scripts: exit 0', r.status, 0);
  const thin = fs.readFileSync(path.join(tmp, 'out', '_fill-c1-r101-p4.js'), 'utf8');
  await yes(t, 'served build is under 8 KB', thin.length < 8000);
  const serve = tamper => async url => {
    const name = String(url).split('/').pop();
    let body = fs.readFileSync(path.join(F.BROWSER, name), 'utf8');
    if (tamper && name === tamper) body += '\n// tampered';
    return { ok: true, text: async () => body };
  };
  const env2 = makeEnv(live, { load: false, fetch: serve(null) });
  rec = await F.runBody(thin, env2.sandbox);
  await check(t, 'served build: fetched, checked, filled', [rec.ok, rec.counts.written, rec.counts.todo], [true, 5, 1]);
  const env3 = makeEnv(live, { load: false, fetch: serve('probe_panels.js') });
  rec = await F.runBody(thin, env3.sandbox);
  await check(t, 'served build: a source that differs from the build is fatal and nothing runs', [rec.fatal, rec.file, env3.writes], ['lib-integrity', 'probe_panels.js', 0]);
  const env4 = makeEnv(live, { load: false, fetch: async () => { throw new Error('ECONNREFUSED'); } });
  rec = await F.runBody(thin, env4.sandbox);
  await check(t, 'served build: no server is fatal', [rec.fatal, env4.writes], ['lib-unreachable', 0]);

  // Replace mode end to end: revision two of p4-10 against the payload just used.
  const ans2 = JSON.parse(JSON.stringify(ans));
  ans2.items[0].segments = [seg('Delta & epsilon <2>, revised')];
  fs.writeFileSync(path.join(tmp, 'answers2.json'), JSON.stringify(ans2));
  r = py(['payload', '--module', 'module.json', '--answers', 'answers2.json', '--out', 'out', '--approvals', 'approvals.json', '--replace', '--against', 'out/fill-payload.json']);
  await check(t, 'replace payload: exit 0', r.status, 0);
  const rp = JSON.parse(fs.readFileSync(path.join(tmp, 'out', 'replace-payload.json'), 'utf8'));
  await check(t, 'replace payload: only the changed text item, with the hash of what was written', [Object.values(rp.pages).flatMap(p => p.jobs.map(j => j.item)), rp.pages[4].jobs[0].oh], [['p4-10'], hText(pay.pages[4].jobs[0].html)]);
  await check(t, 'replace payload: the non-text items are refused', rp.refused.map(x => x.item).sort(), ['p4-12', 'p4-13', 'p4-14', 'p6-30']);
  r = py(['scripts', '--out', 'out', '--no-render', '--payload', 'out/replace-payload.json']);
  env.sandbox.location.pathname = F.RUN_PATH(4);
  rec = await F.runBody(fs.readFileSync(path.join(tmp, 'out', '_replace-c1-r101-p4.js'), 'utf8'), env.sandbox);
  await check(t, 'replace script: the panel still held our text, so it is replaced', [rec.mode, byItem(rec, 'p4-10').result], ['replace', 'replaced']);
  fs.rmSync(tmp, { recursive: true, force: true });
});

test('rendered end to end: ltjs.py wraps the body with the guard and the save watcher', { skip: F.haveNet() && F.haveGuard() ? false : 'needs harness, lt_net.js and lt_guard.js' }, async t => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ltfill-r-'));
  const mod = {
    schema_version: 1, module_id: 'c1-r101', instance: 'inst0001', course_id: 1, run_id: 101,
    source_url: `${F.ORIGIN}/i/inst0001/student/courses/1/runs/101/run`,
    lesson: { title: 'Synthetic', page_count: 1, has_completion_page: true, completion_route: 'complete', run_locked: false },
    commit_boundaries: { checkpoint_pages: [], checkpoints: [], checkpoints_container_present: true },
    pages: [{ page_index: 1, page_no: 1, route: null, title: 'Page 1', is_report_page: false, is_checkpoint: false, url: `${F.ORIGIN}${F.RUN_PATH(1)}`,
      items: ['10', '11'].map(k => ({ item_id: 'p1-' + k, panel_key: k, plugin_key: 'text-question', type: 'text-response', attempts: 'multi', assessment_type: 'noAssessment', commit_state: 'open', read_only: false, automation: 'structured-path', answered: false, parent_item_id: null, prompt_text: 'Q' })) }]
  };
  const ans = { schema_version: 2, module_id: 'c1-r101', run_id: 101, drafted_at: '2000-01-01T00:00:00Z', source_module: { path: 'module.json', sha256: '0'.repeat(64) },
    items: ['10', '11'].map(k => ({ item_id: 'p1-' + k, status: 'drafted', confidence: 'high', segments: [{ kind: 'claim', text: 'Draft ' + k, provenance_refs: ['p1'] }], provenance: [{ ref: 'p1', tier: 1, source_id: 'X', locator: 'slide 1' }] })) };
  fs.writeFileSync(path.join(tmp, 'module.json'), JSON.stringify(mod));
  fs.writeFileSync(path.join(tmp, 'answers.json'), JSON.stringify(ans));
  fs.writeFileSync(path.join(tmp, 'lt-suite.toml'), `[lt]\norigin = "${F.ORIGIN}"\n[browser]\njs_tmp_dir = "${path.join(tmp, 'js').replace(/\\/g, '/')}"\n`);
  const py = args => cp.spawnSync('python3', [F.EMIT].concat(args), { encoding: 'utf8', cwd: tmp, env: pyEnv(tmp) });
  let r = py(['payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out']);
  await check(t, 'rendered: payload exit 0', r.status, 0);
  r = py(['scripts', '--out', 'out', '--form', 'function']);
  await check(t, 'rendered: scripts exit 0', r.status, 0);
  if (r.status !== 0) console.log(r.stdout, r.stderr);
  const man = JSON.parse(fs.readFileSync(path.join(tmp, 'out', 'scripts.json'), 'utf8'));
  const call = man.calls.find(c => c.kind === 'fill');
  await yes(t, 'rendered: the fill call has a rendered file', call && call.rendered && fs.existsSync(call.rendered));
  const env = F.netEnv({ net: false, pages: [{ title: 'Page 1', current: true, panels: e => [F.netTextPanel(e, 10, 1), F.netTextPanel(e, 11, 2)] }] });
  const rec = await env.runRendered(fs.readFileSync(call.rendered, 'utf8'));
  const nav = env.navigate();
  await check(t, 'rendered: two written through the real probe', [rec.ok, rec.counts && rec.counts.written], [true, 2]);
  await check(t, 'rendered: the guard stamped the receipt', rec.guard_trips, 0);
  await check(t, 'rendered: uncalibrated origin, so the fallback wait', [rec.save_signal, rec.net], ['fallback', 'ltnet']);
  await check(t, 'rendered: nothing lost', nav.lost, 0);
  fs.rmSync(tmp, { recursive: true, force: true });
});

// T30: use the emitted RAT body against an already answered team panel.
test('rail RAT sheet occupancy: generated page 3 keeps an existing team selection', async t => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ltfill-rat-'));
  try {
    const fix = path.join(F.REPO, 'tests', 'fixtures');
    fs.mkdirSync(path.join(tmp, 'sheets'));
    fs.copyFileSync(path.join(fix, 'curriculum', 'Block A', 'RATs', '1.2-team.md'), path.join(tmp, 'sheets', '1.2-team.md'));
    const mod = JSON.parse(fs.readFileSync(path.join(fix, 'modules', 'module-c1-r109.json')));
    const ans = JSON.parse(fs.readFileSync(path.join(fix, 'answers', 'answers-c1-r109.json')));
    fs.writeFileSync(path.join(tmp, 'module.json'), JSON.stringify(mod));
    fs.writeFileSync(path.join(tmp, 'answers.json'), JSON.stringify(ans));
    fs.writeFileSync(path.join(tmp, 'lt-suite.toml'), `[rat]\nanswers_dir = "sheets"\ntrat = "from_dir"\n`);
    const py = (script, args) => cp.spawnSync('python3', [path.join(F.SKILL, 'scripts', script), ...args], { encoding: 'utf8', cwd: tmp, env: Object.assign(pyEnv(tmp), { LT_SUITE_CONFIG: path.join(tmp, 'lt-suite.toml') }) });
    const classified = py('page_kinds.py', ['classify', '--module', 'module.json', '--answers', 'answers.json']);
    assert.equal(classified.status, 1, classified.stderr); // practice pages still ask
    const payload = py('emit_fill.py', ['payload', '--module', 'module.json', '--answers', 'answers.json', '--out', 'out']);
    assert.equal(payload.status, 0, payload.stdout + payload.stderr + classified.stdout);
    const scripts = py('emit_fill.py', ['scripts', '--out', 'out', '--no-render']);
    assert.equal(scripts.status, 0, scripts.stderr);
    const target = mod.pages.find(p => p.page_index === 3);
    const env = makeEnv(e => [page(1, 'Orientation', []), page(2, 'Individual', []), page(3, target.title,
      target.items.map(it => selectPanel(e, it.panel_key, it.options.map(o => o.text), [0], { content: { multiselect: true } })))],
      { location: { pathname: '/i/inst0001/student/courses/1/runs/109/run/page/3' }, page: 3 });
    const body = fs.readFileSync(path.join(tmp, 'out', '_fill-c1-r109-rat-p3.js'), 'utf8');
    const result = await F.runBody(body, env.sandbox);
    assert.equal(result.ok, true);
    assert.equal(env.writes, 0);
    assert.equal(env.clicks, 0);
    assert.ok(result.items.every(i => ['occupied-skipped', 'already-new'].includes(i.result)));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('RAT receipts echo the executed payload identity', async t => {
  const pin = 'a'.repeat(64);
  const env = makeEnv(e => fourPages(e, [textPanel(e, '10', '')]));
  const filled = await run(env, { source_payload_sha256: pin }, [textJob('10', '<p>ours</p>')]);
  await check(t, 'fill echoes payload hash', filled.source_payload_sha256, pin);
  const fresh = makeEnv(e => fourPages(e, [textPanel(e, '10', '<p>ours</p>')]));
  const verified = await fresh.L.verify(CFG({ source_payload_sha256: pin }), { 4: [textJob('10', '<p>ours</p>')] });
  await check(t, 'fresh verification echoes payload hash', verified.source_payload_sha256, pin);
});
