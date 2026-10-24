// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Commit-boundary map and page walk of capture.js, driven through the whole captureLtRun().
 *
 * handlers.test.js covers the readers only; the code that builds the boundary map comes
 * after them and is covered here. The whole script runs against a synthetic
 * ko/document/location, and the module document is taken from the intercepted POST. The
 * key case is a COMMITTED checkpoint: Lt removes it from lessonFlowPages, yet `checkpoints`
 * must still list it while `checkpoint_pages` (the live doors) stays empty.
 */
'use strict';
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const H = require('./vm_harness.js');
const { obs, coll, mdl, contentPage, reportPage, checkpointPage, panel, baseRoot, runCapture, MODULE_ID } = H;

const env = (pages, container, trips) => ({ root: baseRoot(pages, container, trips) });
const cb = r => r.doc.commit_boundaries;

describe('boundaries', () => {
  test('boundaries: a module with no checkpoint reports empty lists that were measured', async () => {
    const trips = [];
    const r = await runCapture(env([contentPage('a'), contentPage('b'), reportPage()], [], trips));
    assert.deepEqual(cb(r).checkpoint_pages, []);
    assert.deepEqual(cb(r).checkpoints, []);
    assert.equal(cb(r).checkpoints_container_present, true);
    assert.deepEqual(trips, [], 'no commit function was called');
  });

  test('boundaries: an uncommitted checkpoint in the flow is a live door', async () => {
    const r = await runCapture(env([contentPage('a'), contentPage('b'), checkpointPage(8, false), contentPage('c')], []));
    assert.deepEqual(cb(r).checkpoint_pages, [3]);
    assert.deepEqual(cb(r).checkpoints, [{ index: 8, page_index: 3, committed: false, in_flow: true, source: 'lessonFlowPages' }]);
    assert.equal(r.doc.pages[2].is_checkpoint, true);
    assert.equal(r.doc.pages[2].page_no, null);
    assert.equal(r.doc.pages[2].url, null);
    assert.equal(r.doc.pages[2].route, 'checkpoint');
    assert.deepEqual(r.doc.pages[2].checkpoint, { index: 8, committed: false });
    assert.equal(r.doc.pages[3].page_no, 3, 'content pages are numbered past the checkpoint');
  });

  test('boundaries: a committed checkpoint dropped from the flow stays in the inventory', async () => {
    const r = await runCapture(env([contentPage('a'), contentPage('b'), contentPage('c'), reportPage()],
                                   [checkpointPage(8, true)]));
    assert.deepEqual(cb(r).checkpoint_pages, []);
    assert.deepEqual(cb(r).checkpoints, [{ index: 8, page_index: null, committed: true, in_flow: false, source: 'checkpointPageContainer' }]);
    assert.equal(r.doc.pages.filter(p => p.is_checkpoint).length, 0);
    assert.equal(r.receipt.summary.checkpoints, 1);
    assert.equal(r.receipt.summary.checkpoints_committed, 1);
  });

  test('boundaries: the same door read by both readers is one entry with source both', async () => {
    const r = await runCapture(env([contentPage('a'), checkpointPage(8, false), contentPage('b')], [checkpointPage(8, false)]));
    assert.equal(cb(r).checkpoints.length, 1);
    assert.equal(cb(r).checkpoints[0].source, 'both');
    assert.deepEqual(cb(r).checkpoint_pages, [2]);
  });

  test('boundaries: an absent container is reported as not measured', async () => {
    const r = await runCapture(env([contentPage('a'), reportPage()], null));
    assert.deepEqual(cb(r).checkpoints, []);
    assert.equal(cb(r).checkpoints_container_present, false);
  });

  test('boundaries: two checkpoints sort by Lt index and keep their flags', async () => {
    const r = await runCapture(env([contentPage('a'), checkpointPage(12, false), contentPage('b')], [checkpointPage(4, true)]));
    assert.deepEqual(cb(r).checkpoints.map(c => c.index), [4, 12]);
    assert.deepEqual(cb(r).checkpoints.map(c => c.committed), [true, false]);
    assert.deepEqual(cb(r).checkpoints.map(c => c.in_flow), [false, true]);
    assert.deepEqual(cb(r).checkpoint_pages, [2]);
  });

  test('boundaries: the report page has route complete, no page_no, and no items', async () => {
    const r = await runCapture(env([contentPage('a'), reportPage()], []));
    const rp = r.doc.pages[1];
    assert.deepEqual([rp.is_report_page, rp.route, rp.page_no, rp.url, rp.items.length], [true, 'complete', null, null, 0]);
    assert.equal(r.doc.lesson.has_completion_page, true);
    assert.equal(cb(r).run_level_commit.present, true);
    assert.equal(cb(r).run_level_commit.route, 'complete');
  });

  test('boundaries: a report page that exposes panels is still never walked', async () => {
    const mirrorPanel = panel('text-question', '11', { question: obs('Q') }, { answer: obs('x') });
    const rp = Object.assign(reportPage(), { allPanels: obs([panel('text-question', '91', { question: obs('Q') }, { answer: obs('x') })]) });
    const r = await runCapture(env([contentPage('a', [mirrorPanel]), rp], []));
    assert.deepEqual(r.doc.pages[1].items, []);
    assert.equal(r.doc.census.captured_total, 1, 'report mirrors would double the census');
  });

  test('boundaries: a single-attempt item is listed, and a single-attempt drag item degrades', async () => {
    const lab = panel('kcImageLabelling', '21', {
      question: obs('Place'), singleAttempt: obs(true), presentedRatio: obs(1.2), imageResourceKey: '30',
      labelItems: coll(mdl({ label: 'one' })), labelLocationItems: coll(mdl({}))
    }, { linkedLabels: coll() });
    const txt = panel('text-question', '22', { question: obs('Why?'), singleAttempt: obs(true) }, { answer: obs('') });
    const r = await runCapture(env([contentPage('a', [lab, txt]), reportPage()], []));
    assert.deepEqual(cb(r).single_attempt_items, ['p1-21', 'p1-22']);
    const it = r.doc.pages[0].items[0];
    assert.equal(it.automation, 'degrade-todo');
    assert.deepEqual([it.degrade.class, it.degrade.reason], ['permanent', 'single-attempt-drag']);
    assert.equal(r.doc.pages[0].items[1].automation, 'structured-path');
  });

  test('boundaries: subpage children are recursed once, and a promoted duplicate is skipped', async () => {
    const child = mdl({ pluginKey: 'text-question', panelKey: '41',
                        panelState: { content: { question: 'Inner' }, input: { answer: 'done' } } });
    const sub = panel('subpage', '40', { subpages: coll(mdl({ rows: coll(mdl({ cells: coll(mdl({ panels: coll(child) })) })) })) });
    // The same child also promoted into allPanels, as Lt does once a subpage was displayed.
    const promoted = panel('text-question', '41', { question: obs('Inner') }, { answer: obs('done') });
    const r = await runCapture(env([contentPage('a', [sub, promoted]), reportPage()], []));
    const items = r.doc.pages[0].items;
    assert.deepEqual(items.map(i => [i.item_id, i.type, i.parent_item_id]),
      [['p1-40', 'subpage', null], ['p1-41', 'text-response', 'p1-40']]);
    assert.equal(r.doc.census.captured_total, 2);
    assert.equal(r.doc.census.answerable_total, 1);
  });

  test('boundaries: read_only is tri-state and read from the panel, its state or its content', async () => {
    const ro = panel('text-question', '51', { question: obs('Q1') }, { answer: obs('') }, { isReadOnly: obs(true) });
    const rw = panel('text-question', '52', { question: obs('Q2'), isReadOnly: obs(false) }, { answer: obs('') });
    const unknown = panel('text-question', '53', { question: obs('Q3') }, { answer: obs('') });
    const r = await runCapture(env([contentPage('a', [ro, rw, unknown]), reportPage()], []));
    assert.deepEqual(r.doc.pages[0].items.map(i => i.read_only), [true, false, null]);
  });

  test('boundaries: a locked input is commit_state locked', async () => {
    const p = panel('text-question', '61', { question: obs('Q') }, { answer: obs('x'), locked: obs(true) });
    const r = await runCapture(env([contentPage('a', [p])], []));
    assert.equal(r.doc.pages[0].items[0].commit_state, 'locked');
  });

  test('boundaries: hotspot panels, unreadable content panels and notebooks all appear in the capture', async () => {
    const hotspot = panel('hotspot', '58', { hotspots: coll(
      mdl({ id: 1, parentHotspot: null, hotspotType: 'CUSTOM', hotspotColour: 'BLUE', labelTitle: '',
            labelDescription: '', imageResourceKey: '59', position: '{"points":[]}' }),
      mdl({ id: 3, parentHotspot: 1, hotspotType: 'POINT', hotspotColour: 'BLUE', labelTitle: 'Stain A',
            labelDescription: 'Stain A colours part one', imageResourceKey: '',
            position: '{"points":[{"x":0.971,"y":0.106}]}' })) });
    const mystery = panel('kcMystery', '91', { widgetText: 'never read', height: 3 });
    const notebook = panel('notebook', '36', { question: obs('Your notes') }, { notes: obs('') });
    const { trow } = { trow: (...t) => mdl({ cells: coll(...t.map(x => mdl({ content: x }))) }) };
    const tbl = panel('table-question', '48', {
      question: obs('Identify A and B'), rows: coll(trow('Label', 'Answer'), trow('A', ''), trow('B', ''))
    }, { rows: coll(trow('Label', 'Answer'), trow('A', ''), trow('B', '')) });
    const r = await runCapture(env([contentPage('p', [hotspot, mystery, notebook, tbl]), reportPage()], []));
    const pg = r.doc.pages[0];

    const hs = pg.content.find(c => c.panel_key === '58') || {};
    assert.deepEqual([hs.plugin_key, hs.kind], ['hotspot', 'hotspot']);
    assert.equal(hs.text, 'Stain A: Stain A colours part one');
    assert.deepEqual((hs.media || []).map(m => m.key), ['59']);
    assert.deepEqual((hs.hotspots || []).map(h => h.points), [[], [{ x: 0.971, y: 0.106 }]]);
    assert.match(hs.capture_note || '', /hotspot reader/);

    const mx = pg.content.find(c => c.panel_key === '91') || {};
    assert.deepEqual([mx.plugin_key, mx.kind], ['kcMystery', 'other']);
    assert.match(mx.capture_note || '', /Content keys: widgetText, height/);
    assert.equal(JSON.stringify(mx).includes('never read'), false, 'content values are never copied');

    const nb = pg.items.find(i => i.panel_key === '36') || {};
    assert.deepEqual([nb.type, nb.automation], ['unmapped', 'degrade-todo']);
    assert.deepEqual([nb.degrade.class, nb.degrade.reason], ['permanent', 'notebook-never-written']);
    assert.match(nb.capture_note || '', /never writes to it/);
    assert.deepEqual(cb(r).unmapped.map(u => u.item_id), ['p1-36']);

    const t = pg.items.find(i => i.panel_key === '48') || {};
    assert.equal(t.answered, false);
    assert.deepEqual((t.answered_evidence || {}).filled_map, ['aa', 'a.', 'a.']);

    const s = r.receipt.summary;
    assert.deepEqual(s.content_unread, ['kcMystery:91']);
    assert.equal(s.hotspot_panels, 1);
    assert.equal(s.notebook_items, 1);
    assert.deepEqual(s.table_author_grids, { read: 1 });
  });

  test('boundaries: an unmapped answerable plugin is a degrade-todo that lifts on capture support', async () => {
    const p = panel('kcNewWidget', '71', { question: obs('New') }, { value: obs(1) });
    const r = await runCapture(env([contentPage('a', [p])], []));
    const it = r.doc.pages[0].items[0];
    assert.deepEqual([it.type, it.automation, it.degrade.class, it.degrade.reason],
      ['unmapped', 'degrade-todo', 'lifts-on-capture-support', 'unmapped-type']);
    assert.match(it.capture_note, /capture allow-list has no entry for pluginKey/);
  });

  test('boundaries: a plugin key named after an Object property is unmapped, not a crash', async () => {
    const p = panel('__proto__', '72', { question: obs('Odd') }, { value: obs(1) });
    const q = panel('constructor', '73', { question: obs('Odd') }, { value: obs(1) });
    const r = await runCapture(env([contentPage('a', [p, q])], []));
    assert.deepEqual(r.doc.pages[0].items.map(i => i.type), ['unmapped', 'unmapped']);
  });

  test('boundaries: an empty upload slot degrades until presented, an annotate-only panel never uploads', async () => {
    const up = panel('image-annotation', '81', { inputMode: obs('allowImageAndAnnotation'), questionText: obs('Upload') },
                     { annotations: obs([]), imageKey: obs(''), presentedRatio: obs(0) });
    const ann = panel('image-annotation', '82', { inputMode: obs('allowAnnotation'), questionText: obs('Mark'),
                                                  predefinedImageKey: obs('5') },
                      { annotations: obs([]) });
    const r = await runCapture(env([contentPage('a', [up, ann])], []));
    const [a, b] = r.doc.pages[0].items;
    assert.deepEqual([a.degrade.class, a.degrade.reason], ['lifts-at-fill', 'geometry-unknown-until-presented']);
    assert.deepEqual([b.degrade.class, b.degrade.reason], ['permanent', 'panel-accepts-no-upload']);
    assert.equal(r.receipt.summary.unknown_geometry, 2);
  });

  test('boundaries: drop_text_patterns removes matching author text and keeps the rest', async () => {
    const keep = panel('kcTextArea', '11', { theText: obs('<p>Read this first.</p>') });
    const drop = panel('kcTextArea', '12', { theText: obs('<p>Copyright © Example Press</p>') });
    const custom = panel('kcTextArea', '13', { theText: obs('<p>Internal note: skip</p>') });
    const def = await runCapture(env([contentPage('a', [keep, drop, custom])], []));
    assert.deepEqual(def.doc.pages[0].content.map(c => c.panel_key), ['11', '13']);
    const own = await runCapture(env([contentPage('a', [keep, drop, custom])], []),
                                 { args: { drop_text_patterns: '["^Internal note"]' } });
    assert.deepEqual(own.doc.pages[0].content.map(c => c.panel_key), ['11', '12']);
    const bad = await runCapture(env([contentPage('a', [keep])], []), { args: { drop_text_patterns: ['(unclosed'] } });
    assert.deepEqual(bad.receipt.bad_drop_patterns, ['(unclosed']);
  });

  test('boundaries: one all-multiple-choice page is flagged as a RAT candidate, never decided', async () => {
    const mcq = k => panel('select', k, { question: obs(`Q${k}`), answers: coll(mdl({ answer: 'A' }), mdl({ answer: 'B' })) },
                           { selectedAnswers: obs([]) });
    const r = await runCapture(env([contentPage('a'), contentPage('check', [mcq('31'), mcq('32')]), reportPage()], []));
    const rc = r.doc.pages[1].rat_candidate;
    assert.ok(rc);
    assert.match(rc.basis, /holds 2 items, every one multiple-choice/);
    assert.match(rc.note, /rat\.answers_dir/);
    assert.equal(r.receipt.summary.rat_candidate_page, 2);
  });

  test('boundaries: module identity comes from the run URL and the page globals', async () => {
    const r = await runCapture(env([contentPage('a'), contentPage('b')], []));
    assert.equal(r.doc.module_id, MODULE_ID);
    assert.deepEqual([r.doc.instance, r.doc.course_id, r.doc.run_id, r.doc.lesson_id], ['inst0001', 1, 101, 1]);
    assert.equal(r.doc.capture_method, 'view-model');
    assert.equal('permission' in r.doc, false, 'no permission field');
    assert.equal(r.doc.pages[1].url, 'https://lt.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/2');
    assert.equal(r.doc.lesson.course_title, 'Test course');
  });

  test('boundaries: a background route still numbers content pages from /run/page/1', async () => {
    const r = await runCapture(Object.assign(env([contentPage('a'), contentPage('b')], []), {
      location: { origin: H.ORIGIN, pathname: '/i/inst0001/student/courses/1/runs/101/run/background' } }));
    assert.equal(r.doc.pages[0].url, 'https://lt.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/1');
  });
});

describe('transport', () => {
  const plain = () => env([contentPage('a'), reportPage()], []);

  test('transport: a first capture posts module then reference, with body authentication and JSON type', async () => {
    const r = await runCapture(plain());
    assert.deepEqual(r.names, [`module-${MODULE_ID}`, `reference-${MODULE_ID}`]);
    assert.deepEqual(r.receipt.transport.map(t => t.ok), [true, true]);
    assert.equal(r.receipt.ok, true);
    for (const p of r.posts) {
      assert.equal(p.headers['X-Lt-Suite-Token'], undefined);
      assert.match(p.headers['X-Lt-Suite-Auth'], /^[0-9a-f]{64}$/);
      assert.equal(p.headers['Content-Type'], 'application/json');
      assert.equal(p.credentials, 'omit');
      assert.ok(p.url.startsWith(H.SINK + '/'));
    }
    assert.equal(r.receipt.transport[0].file, `module-${MODULE_ID}.json`);
  });

  test('transport: the reference holds answered items only and is quarantined', async () => {
    const done = panel('text-question', '15', { question: obs('Q') }, { answer: obs('my answer') });
    const empty = panel('text-question', '16', { question: obs('Q2') }, { answer: obs('') });
    const r = await runCapture(env([contentPage('a', [done, empty])], []));
    assert.equal(r.reference.never_load_into_drafting_context, true);
    assert.equal(r.reference.provenance_of_reference, 'student-submitted');
    assert.deepEqual(r.reference.items.map(i => [i.item_id, i.student_answer]), [['p1-15', 'my answer']]);
    assert.equal(JSON.stringify(r.doc).includes('my answer'), false, 'module.json never carries the answer');
  });

  test('transport: module_only posts the module and nothing else', async () => {
    const r = await runCapture(plain(), { args: { module_only: 'true' } });
    assert.deepEqual(r.names, [`module-${MODULE_ID}`]);
    assert.equal(r.receipt.transport[1].route, 'skipped');
    assert.equal(r.receipt.module_only, true);
    assert.deepEqual(r.downloads, []);
  });

  test('transport: a 409 on the reference is a refusal, never a download', async () => {
    const r = await runCapture(plain(), {
      reply: name => (name.startsWith('reference-')
        ? H.response(409, 'refused: this run already has its reference, which is write-once')
        : H.response(200, { ok: true, file: `${name}.json` }))
    });
    const ref = r.receipt.transport[1];
    assert.equal(r.names[0], `module-${MODULE_ID}`);
    assert.equal(r.receipt.transport[0].ok, true);
    assert.deepEqual([ref.route, ref.ok, ref.refused], ['sink', false, true]);
    assert.match(ref.reason, /write-once/);
    assert.deepEqual(r.downloads, []);
  });

  test('transport: a 401 for a wrong token is reported and not retried by download', async () => {
    const r = await runCapture(plain(), { reply: () => H.response(401, 'bad token') });
    assert.equal(r.receipt.ok, false);
    assert.deepEqual(r.receipt.transport.map(t => t.status), [401, 401]);
    assert.deepEqual(r.downloads, []);
  });

  test('transport: an unreachable sink fails without downloading', async () => {
    const r = await runCapture(plain(), { args: { module_only: true },
      helloReply: () => { throw new TypeError('Failed to fetch'); } });
    assert.equal(r.receipt.fatal, 'sink-unauthenticated');
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.downloads, []);
  });

  test('transport: download transport never touches the sink', async () => {
    const r = await runCapture(plain(), { args: { transport: 'download', token: '' } });
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.downloads, [`module-${MODULE_ID}.json`, `reference-${MODULE_ID}.json`]);
    assert.deepEqual(r.receipt.transport.map(t => [t.route, t.handed_off]), [['download', true], ['download', true]]);
  });

  test('transport: a sink that is not loopback is refused before anything is read', async () => {
    for (const sink of ['https://collector.example.invalid', 'http://10.0.0.5:8731', 'http://127.0.0.1:8731/x']) {
      const r = await runCapture(plain(), { args: { sink } });
      assert.equal(r.receipt.ok, false, sink);
      assert.match(r.receipt.reason, /127\.0\.0\.1/);
      assert.deepEqual(r.posts, []);
    }
  });

  test('transport: a sink URL carrying credentials is refused', async () => {
    for (const sink of ['http://user@127.0.0.1:8731', 'http://:pw@127.0.0.1:8731']) {
      const r = await runCapture(plain(), { args: { sink } });
      assert.equal(r.receipt.ok, false, sink);
    }
  });

  test('transport: a call argument wins over the baked argument in either spelling', async () => {
    const r = await runCapture(plain(), { entry: true, args: { moduleOnly: false }, secondCall: { module_only: true } });
    assert.equal(r.receipt.module_only, true);
    assert.equal(r.names.length, 3, 'entry posted module and reference, the second call the module only');
    const same = await runCapture(plain(), { rawArgs: { transport: 'sink', sink: H.SINK, token: H.TOKEN, module_only: 'true' } });
    assert.equal(same.receipt.module_only, true);
    assert.equal(same.receipt.reference_ok, true);
  });

  test('transport: a tab whose list element has no Knockout context is refused', async () => {
    const e = plain();
    const r = await H.runCapture(Object.assign(e, { noContext: true }));
    assert.equal(r.receipt.ok, false);
  });

  test('transport: the sink route without a token is refused', async () => {
    const r = await runCapture(plain(), { args: { token: '' } });
    assert.equal(r.receipt.ok, false);
    assert.match(r.receipt.reason, /token/);
    assert.deepEqual(r.posts, []);
  });

  test('transport: the rendered entry line runs the capture with __LT_ARGS__', async () => {
    const r = await runCapture(plain(), { entry: true });
    assert.equal(r.receipt.ok, true);
    assert.equal(r.receipt.summary.module_id, MODULE_ID);
    assert.equal(r.names.length, 2);
  });

  test('transport: a tab that is not an Lt run URL is refused', async () => {
    const r = await runCapture(Object.assign(plain(), { location: { origin: H.ORIGIN, pathname: '/dashboard' } }));
    assert.equal(r.receipt.ok, false);
    assert.deepEqual(r.posts, []);
  });
});
