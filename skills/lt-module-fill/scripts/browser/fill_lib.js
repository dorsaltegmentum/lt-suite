/* SPDX-FileCopyrightText: 2026 dorsaltegmentum
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * lt-module-fill: fill_lib.js, the one write path into a live Lt run.
 *
 * emit_fill.py pastes this file, probe_panels.js and one call's jobs into each generated
 * `_fill-<module>-p<N>.js` (one page) or `_fill-<module>-b<K>.js` (a batch of pages), and
 * this file plus the pages' hashes into `_verify-*.js`. ltjs.py render prepends the
 * never-submit guard (lt_guard.js) and the save watcher (lt_net.js, global LtNet).
 * Comment-only lines are stripped on the way out; the code is byte-identical to this file.
 *
 * Every rail below has a test in tests/fill/fill_lib.test.js:
 *
 *  1. LOCATION AND RUN. The script refuses unless location is
 *     /i/<instance>/student/courses/<course>/runs/<run>/run/page/<page> on the captured
 *     origin. A /preview URL is a different run that holds no work. For one page, the page
 *     Lt has painted must be the job's flow page. For a batch, every target page must be in
 *     cfg.batch_pages, outside cfg.never_navigated and before cfg.stop_before_page, match
 *     its captured title hash and hold a reachable flow model; any miss refuses the batch.
 *  2. THE PAGE. The page must be a content page with the captured title. A checkpoint
 *     carries no allPanels and the report page mirrors real panels.
 *  3. SINGLE ATTEMPT. A panel the capture marks single-attempt, or whose live content reads
 *     singleAttempt true, is never written unless its job carries single_attempt_approved,
 *     which emit_fill.py sets only from an approval recorded in the run's approvals.json.
 *     Nothing in this file clicks anything: no Submit, no Check Answer, no radio. Writes go
 *     through the models. On the page of a submitted single-attempt item the plan passed,
 *     or any later page, the call is refused unless that panel still reads read-only.
 *  4. PROBE OCCUPANCY. probe_panels.js runs first on the displayed page. occupied true
 *     skips the item, occupied null skips it as unknown, read_only or locked refuses it.
 *     Subpage children and panels on other pages of a batch are not in the probe, so the
 *     view model's read-only and locked flags and the direct test below decide for them.
 *  5. DIRECT OCCUPANCY. The exact answer, cells, selection or placements about to be
 *     written must be empty. In replace mode a non-empty target is overwritten only when
 *     its text hashes to the `oh` recorded from the earlier fill: replaced, already-new or
 *     changed-since-fill-skipped. Only text and table jobs can be replaced.
 *  6. GEOMETRY. A table target in a header row, a header column or a missing cell refuses
 *     the whole item. A label resolves by TEXT against the live pool, and a drafted anchor
 *     coordinate must match the live anchor within 0.005.
 *  7. THE SAVE WAIT. Lt sends the save after `set` returns; navigating at once lost 12 of
 *     20 writes in one measured run. LtNet.expect(key, model) runs immediately before each
 *     write, and one LtNet.waitForSave(keys, cfg.save_wait) covers every key of the call.
 *     A calibrated origin ends the wait on each key's own save plus a quiet window. A save
 *     counts only through a request LtNet observed: a Backbone sync whose request event
 *     names an XHR or fetch LtNet hooked, or a keyed same-origin write, either one with the
 *     record's method and path template and a 2xx status other than 202. A save sent
 *     through a transport LtNet cannot identify (a fetch reference Lt kept from before the
 *     hooks, a socket) stays pending, so the call ends `unconfirmed`. An uncalibrated origin
 *     keeps the old rule: at least fallback_ms after the last write and jQuery.active back
 *     at its base. `unconfirmed` (cap reached), `error`, and `fallback` with keys still
 *     pending stop the call; the script never navigates, and the next step is a fresh-load
 *     verify of these pages. A batch needs a save record that holds on this page: format
 *     version 1, this Lt client build, and a write endpoint.
 *  8. ONE HASH. djb2 over UTF-16 code units of the page text, the twin of emit_fill.py's.
 *     The write receipt and the fresh-load verify use the same function and the same `h`.
 *  9. THE RECEIPT. Counts, hashes, lengths and failure strings, never the student's text
 *     and never ours, nested at most 4 levels (one browser tool truncates deeper).
 *
 * The generated script ends in a top-level `await`, so a tool that evaluates an
 * expression gets the receipt after the save wait instead of an unawaited promise.
 */
(function () {
  'use strict';
  const G = (typeof globalThis !== 'undefined') ? globalThis : this;

  /* ---- Knockout and Backbone access, as capture.js and probe_panels.js read them ---- */
  /* Comparison only: capture and live text differ in non-breaking spaces and whitespace
   * runs, and Lt stores some characters as numeric entities ("&#43;" for "+"). */
  const NT = t => String(t == null ? '' : t)
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/[\u00a0\s]+/g, ' ').trim();
  const U = v => ((G.ko && G.ko.isObservable(v)) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const models = c => { const x = U(c); return x && x.models ? x.models : (Array.isArray(x) ? x : []); };

  /* capture.js's S(), verbatim from probe_panels.js. Label and category text in module.json
   * went through it, so an equality test against it resolves a drafted label. */
  const S = h => String(h ?? '')
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\/\s*(p|div|li|tr|h[1-6])\s*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'")
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  const LABEL_KEYS = ['label', 'text', 'name', 'title'];
  const CAT_KEYS = ['title', 'category', 'label', 'name', 'text'];
  const pickText = (o, keys) => {
    const a = A(o) || {};
    for (const k of keys) { const v = S(U(a[k])); if (v.length) return v; }
    return '';
  };

  /* ---- page text and the hash: emit_fill.py's page_text() and djb2() are the twins ----
   * The whitespace class is spelled out on both sides because JS \s and Python \s differ
   * (U+FEFF, U+001C to U+001F, U+0085). Entities decode in one pass from the same table. */
  const WS = '[\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]';
  const RE_BR = new RegExp('<' + WS + '*br' + WS + '*/?>', 'gi');
  const RE_CLOSE = new RegExp('</' + WS + '*(p|div|li|tr|td|h[1-6])' + WS + '*>', 'gi');
  const RE_TAG = /<[^>]+>/g;
  const RE_ENT = /&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z]+);/g;
  const RE_WS_RUN = new RegExp(WS + '+', 'g');
  const RE_WS_EDGE = new RegExp('^' + WS + '+|' + WS + '+$', 'g');
  const NAMED = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0' };
  const unescapeHtml = s => s.replace(RE_ENT, (m, e) => {
    if (e[0] === '#') {
      const n = (e[1] === 'x' || e[1] === 'X') ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return (n > 0 && n <= 0x10FFFF) ? String.fromCodePoint(n) : m;
    }
    return Object.prototype.hasOwnProperty.call(NAMED, e) ? NAMED[e] : m;
  });
  const pageText = h => unescapeHtml(String(h ?? '').replace(RE_BR, ' ').replace(RE_CLOSE, ' ').replace(RE_TAG, ''))
    .replace(RE_WS_RUN, ' ').replace(RE_WS_EDGE, '');
  const djb2 = s => {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = (Math.imul(h, 33) + s.charCodeAt(i)) >>> 0;
    return h;
  };
  /* Structured answers hash a canonical string. Lines sort by UTF-16 code unit, which is
   * JS's default sort and Python's sort keyed on utf-16-be bytes. */
  const canon = {
    select: idx => 'sel:' + idx.slice().sort((a, b) => a - b).join(','),
    labels: pairs => 'lab:' + pairs.map(p => p[0] + '\t' + NT(p[1])).sort().join('\n'),
    categories: pairs => 'cat:' + pairs.map(p => p[0] + '\t' + NT(p[1])).sort().join('\n')
  };
  /* A page title as the capture recorded it, whitespace collapsed; batches compare its hash. */
  const titleText = t => S(t).replace(/\s+/g, ' ').trim();
  const titleHash = t => djb2(titleText(t));
  /* A table's cells hash as one string, the twin of emit_fill.py's table_hash(). */
  const tableHash = hs => 'cells:' + hs.join(',');

  /* ---- clock, network idle and the save watcher; tests replace them through globalThis ---- */
  const clock = () => G.__ltClock || { now: () => Date.now(), sleep: ms => new Promise(r => setTimeout(r, ms)) };
  const jqActive = () => ((G.jQuery && typeof G.jQuery.active === 'number') ? G.jQuery.active : 0);
  const net = () => {
    const n = G.LtNet;
    if (!n || typeof n.expect !== 'function' || typeof n.waitForSave !== 'function') return null;
    if (typeof n.install === 'function') { try { n.install(G); } catch (e) { /* already installed */ } }
    return n;
  };
  const guardTrips = () => {
    if (G.LtGuard && typeof G.LtGuard.trips === 'function') { try { return G.LtGuard.trips(); } catch (e) { /* below */ } }
    const g = G.__ltGuard;
    if (g && typeof g.trips === 'number') return g.trips;
    if (g && typeof g.guard_trips === 'number') return g.guard_trips;
    return typeof G.__ltGuardTrips === 'number' ? G.__ltGuardTrips : null;
  };
  /* This document's id: a fresh load gets a new one, so `emit_fill.py record` can tell the
   * filling document from the verifying one. Random, never derived from the page. */
  const docId = () => {
    if (typeof G.__ltFillDocId === 'string') return G.__ltFillDocId;
    let hex = '';
    try {
      const b = new Uint8Array(8);
      G.crypto.getRandomValues(b);
      hex = Array.from(b).map(x => x.toString(16).padStart(2, '0')).join('');
    } catch (e) {
      hex = (Math.floor(Math.random() * 0xffffffff) >>> 0).toString(16).padStart(8, '0') + (clock().now() >>> 0).toString(16).padStart(8, '0');
    }
    G.__ltFillDocId = hex;
    return hex;
  };
  /* The Lt client build, as LtNet signs it (origin and path of every script src, hashed);
   * null without LtNet or without scripts. LtNet sets a save record taken on another build
   * aside by itself (calibration_stale). */
  const clientSig = () => {
    const n = G.LtNet;
    if (!n || typeof n.clientSig !== 'function') return null;
    try { return n.clientSig(); } catch (e) { return null; }
  };
  /* Why a save record cannot drive a calibrated wait on this page, by LtNet's own rule:
   * 'version' (another format version), 'client' (another Lt client build), 'no-endpoint'
   * (no write method and path template of the request the calibration identified). null
   * when the record holds. */
  const NON_WRITE = { GET: 1, HEAD: 1, OPTIONS: 1 };
  const staleReason = cal => {
    if (!cal || typeof cal !== 'object') return null;
    const c = (cal.save && typeof cal.save === 'object') ? cal.save : cal;
    if (c.version != null && c.version !== 1) return 'version';
    if (c.client_sig != null && c.client_sig !== clientSig()) return 'client';
    const m = c.method ? String(c.method).toUpperCase() : '';
    if (!m || NON_WRITE[m] || !c.path_template) return 'no-endpoint';
    return null;
  };
  /* cfg.save_wait from config fill.save_wait.*, plus `calibration`: the origin's `save` record
   * from calibration.json (with its version and client_sig), or null. The uncalibrated floor
   * never drops below 4 s. */
  const saveWaitOf = cfg => {
    const sw = Object.assign({}, cfg.save_wait || {});
    return {
      quiet_ms: sw.quiet_ms ?? 300,
      max_ms: Math.max(sw.max_ms ?? 20000, 1000),
      fallback_ms: Math.max(sw.fallback_ms ?? 4000, 4000),
      render_wait_ms: sw.render_wait_ms ?? 5000,
      calibration: sw.calibration || null
    };
  };
  /* Identity fields every receipt carries: the document, the Lt client build, and the
   * calibration ticket emit_fill.py issued for this call (or null). */
  const ids = cfg => ({ doc_id: docId(), client_sig: clientSig(), ticket: cfg.ticket || null });

  /* ---- location: rail 1 ---- */
  const RUN_PAGE = /^\/i\/([^/]+)\/student\/courses\/(\d+)\/runs\/(\d+)\/run\/page\/(\d+)\/?$/;
  const checkLocation = (cfg, wantPage) => {
    const loc = G.location || {};
    const path = String(loc.pathname || '');
    if (/\/preview(\/|$)/.test(path) || /(^|[?&])preview(=|&|$)/.test(String(loc.search || '').replace(/^\?/, ''))) {
      return { fatal: 'preview-url' };
    }
    const m = path.match(RUN_PAGE);
    if (!m) return { fatal: 'not-a-run-page' };
    if (cfg.origin && loc.origin !== cfg.origin) return { fatal: 'wrong-origin' };
    if (cfg.instance && m[1] !== cfg.instance) return { fatal: 'wrong-instance' };
    if (cfg.course_id != null && Number(m[2]) !== Number(cfg.course_id)) return { fatal: 'wrong-course' };
    if (Number(m[3]) !== Number(cfg.run_id)) return { fatal: 'wrong-run', run_in_url: Number(m[3]) };
    if (wantPage != null) {
      /* The URL can lag behind the page displayed by Lt. Use current() to identify
       * the rendered target. Background routes shift the URL page number relative
       * to the flow index. Require the selected flow page to read current() true;
       * compare URL position only when the current flag is unavailable. */
      const root = rootOf();
      const raw = root ? models(root.lessonFlowPages())[Number(wantPage) - 1] : null;
      const cur = raw && typeof raw.current !== 'undefined' ? U(raw.current) : undefined;
      if (cur === false || (cur === undefined && Number(m[4]) !== Number(wantPage))) {
        return { fatal: 'wrong-page', page_in_url: Number(m[4]), want_flow_page: Number(wantPage) };
      }
    }
    return { page: Number(m[4]) };
  };

  /* ---- pages and panels ---- */
  const rootOf = () => {
    const el = G.document && G.document.querySelector('div.pageList');
    if (!el || !G.ko) return null;
    const ctx = G.ko.contextFor(el);
    return ctx ? ctx.$root : null;
  };
  const flatPages = root => models(root.lessonFlowPages()).map(p => A(p) || p);
  // lessonFlowPages() carries no pageIndex; position is the fallback, as in the probe.
  const pageAt = (flat, idx) => flat.find(p => Number(U(p.pageIndex)) === idx) || flat[idx - 1];
  /* The flow page Lt has painted, by its current() flag; null when no page carries one. */
  const currentPageIndex = root => {
    const raws = models(root.lessonFlowPages());
    for (let i = 0; i < raws.length; i++) {
      const r = raws[i];
      if (r && typeof r.current !== 'undefined' && U(r.current) === true) {
        const pi = Number(U((A(r) || {}).pageIndex));
        return Number.isInteger(pi) && pi > 0 ? pi : i + 1;
      }
    }
    return null;
  };
  const keyOf = raw => {
    const pa = A(raw) || {};
    try { return typeof raw.panelKey === 'function' ? String(raw.panelKey()) : String(U(pa.panelKey)); }
    catch (e) { return String(U(pa.panelKey)); }
  };
  const pluginOf = raw => {
    const pa = A(raw) || {};
    try { return typeof raw.pluginKey === 'function' ? String(raw.pluginKey()) : String(U(pa.pluginKey)); }
    catch (e) { return String(U(pa.pluginKey)); }
  };
  /* Recursive: subpage children live under panelState.content.subpages[].rows[].cells[] and
   * their columns, as capture.js walks them; pa.content is kept from an older walk. */
  const panelsOf = page => {
    const byKey = Object.create(null);
    const walk = (raw, depth) => {
      if (!raw || depth > 5) return;
      const pa = A(raw) || {};
      const k = keyOf(raw);
      if (k && !byKey[k]) byKey[k] = raw;
      for (const key of ['panels', 'allPanels', 'childPanels', 'subPanels', 'children']) {
        for (const ch of models(pa[key] ?? raw[key])) walk(ch, depth + 1);
      }
      const st = A(U(pa.panelState)) || {};
      for (const ca of [A(U(pa.content)) || {}, A(U(st.content)) || {}]) {
        for (const sp of models(ca.subpages)) for (const row of models((A(sp) || {}).rows)) for (const cell of models((A(row) || {}).cells)) {
          const cla = A(cell) || {};
          for (const p of models(cla.panels)) walk(p, depth + 1);
          for (const col of models(cla.columns)) for (const p of models((A(col) || {}).panels)) walk(p, depth + 1);
        }
      }
    };
    for (const raw of models(page.allPanels)) walk(raw, 0);
    return byKey;
  };
  const stateModelOf = raw => U((A(raw) || {}).panelState);
  const stateOf = raw => A(stateModelOf(raw)) || {};
  const contentOf = raw => A(stateOf(raw).content) || {};
  const inputOf = raw => U(stateOf(raw).input);
  const readOnlyOf = raw => {
    const pa = A(raw) || {}, st = stateOf(raw), c = contentOf(raw);
    return [U(pa.isReadOnly), U(st.isReadOnly), U(c.isReadOnly)].find(v => v === true || v === false);
  };
  const lockedOf = raw => U((A(inputOf(raw)) || {}).locked) === true;
  /* The key a save request names: panelState.uuids (first non-empty string when it is a
   * list or an object), else the panel key. */
  const saveKeyOf = raw => {
    const u = U(stateOf(raw).uuids);
    if (typeof u === 'string' && u) return u;
    const vals = Array.isArray(u) ? u : (u && typeof u === 'object' ? Object.values(A(u) || u) : []);
    for (const v of vals) { const s = U(v); if (typeof s === 'string' && s) return s; }
    return keyOf(raw);
  };
  const gridOf = inp => { const ia = A(inp) || {}; let rows = models(ia.rows); if (!rows.length) rows = models(ia.cells); return rows; };
  const cellsOfRow = row => { const ra = A(row) || {}; let cs = models(ra.cells); if (!cs.length) cs = models(ra.columns); return cs; };
  const cellAt = (rows, r, c) => {
    const row = rows[r];
    if (!row) return { err: 'no-row' };
    const ra = A(row) || {};
    const cell = cellsOfRow(row)[c];
    if (!cell) return { err: 'no-cell' };
    if (U(ra.headerRow) === true) return { err: 'header-row' };
    if (U((A(cell) || {}).headerColumn) === true) return { err: 'header-col' };
    if (typeof cell.set !== 'function') return { err: 'no-cell-model' };
    return { cell };
  };
  const cellText = cell => pageText(U((A(cell) || {}).content));

  /* ---- live answers as canonical hashes, shared by the write receipt and verify ---- */
  const liveSelection = inp => models((A(inp) || {}).selectedAnswers).filter(n => Number.isInteger(n));
  const liveLinks = inp => models((A(inp) || {}).linkedLabels).map(x => A(x) || {})
    .map(x => [U(x.labelIndex), U(x.locationIndex)]).filter(p => p[0] != null && p[1] != null);
  const liveCatEntries = inp => models((A(inp) || {}).categoryAnswerItems).map(x => A(x) || {});
  const livePlacements = inp => {
    const out = [];
    liveCatEntries(inp).forEach((e, pos) => {
      const ci = Number.isInteger(U(e.categoryIndex)) ? U(e.categoryIndex) : pos;
      for (const l of models(e.labelAnswerItems)) { const li = U((A(l) || {}).labelIndex); if (li != null) out.push([ci, li]); }
    });
    return out;
  };
  const labelPool = c => models(c.labelItems).map(x => pickText(x, LABEL_KEYS));
  const categoryPool = c => models(c.categoryItems).map(x => pickText(x, CAT_KEYS));
  const liveHash = (job, raw) => {
    const inp = inputOf(raw);
    const c = contentOf(raw);
    if (job.kind === 'text') { const t = pageText(U((A(inp) || {}).answer)); return { h: djb2(t), n: t.length }; }
    if (job.kind === 'select') { const s = liveSelection(inp); return { h: djb2(canon.select(s)), n: s.length }; }
    if (job.kind === 'labels') {
      const pool = labelPool(c);
      const l = liveLinks(inp);
      return { h: djb2(canon.labels(l.map(p => [p[1], pool[p[0]] ?? ('#' + p[0])]))), n: l.length };
    }
    if (job.kind === 'categories') {
      const pool = labelPool(c);
      const pl = livePlacements(inp);
      return { h: djb2(canon.categories(pl.map(p => [p[0], pool[p[1]] ?? ('#' + p[1])]))), n: pl.length };
    }
    return { h: null, n: 0 };
  };

  /* The keyboard route Lt ships for labelling is currentInputModel().addAnswer. Which
   * object carries currentInputModel is not measured for every page shape, so the panel's
   * own view model is tried first and the input model second; `via` names the one used. */
  const liveIndexState = (job, raw) => {
    const inp = inputOf(raw);
    if (job.kind === 'select') return liveSelection(inp).slice().sort((a,b) => a-b).join(',');
    const pairs = job.kind === 'labels' ? liveLinks(inp) : livePlacements(inp).map(p => [p[1],p[0]]);
    return pairs.slice().sort((a,b) => a[0]-b[0] || a[1]-b[1]).map(p => p.join(':')).join(',');
  };
  const addAnswerTarget = raw => {
    const pa = A(raw) || {};
    for (const [via, o] of [['panel', raw], ['panel-attributes', pa]]) {
      if (o && typeof o.currentInputModel === 'function') {
        try { const t = o.currentInputModel(); if (t && typeof t.addAnswer === 'function') return { t, via: 'currentInputModel:' + via }; }
        catch (e) { /* fall through */ }
      }
    }
    const inp = inputOf(raw);
    if (inp && typeof inp.addAnswer === 'function') return { t: inp, via: 'input' };
    return null;
  };

  /* True only when the panel already holds exactly this job's answer. Used where a write is
   * refused anyway, so a re-run reports what is there instead of a bare occupied-skipped. */
  const alreadyNew = (job, raw) => {
    if (job.kind === 'table') {
      const rows = gridOf(inputOf(raw));
      return job.cells.every(t => { const r = cellAt(rows, t.row, t.col); return r.cell && djb2(cellText(r.cell)) === t.h; });
    }
    const live = liveHash(job, raw);
    return live.h != null && live.n > 0 && live.h === job.h;
  };

  const SKIPS = new Set(['occupied-skipped', 'occupancy-unknown-skipped', 'changed-since-fill-skipped']);
  const FAILS = new Set(['write-mismatch', 'partial', 'write-not-visible', 'write-threw', 'save-error']);
  /* Job kinds written with model.set alone; only these may sit on a batch page that is not
   * displayed. addAnswer writes are measured on a rendered page only. */
  const SET_KINDS = new Set(['text', 'table', 'select']);

  /* ---- the probe: rail 4 ---- */
  const runProbe = async deps => {
    let fn = deps && deps.probe;
    const lib = G.LtFillLib || {};
    if (!fn && typeof lib.loadProbe === 'function') fn = lib.loadProbe();
    if (!fn && typeof G.probeLtPanels === 'function') fn = G.probeLtPanels;
    if (typeof fn !== 'function') throw new Error('probe_panels.js is not loaded');
    return await fn();
  };

  /* ---- rail 7 without LtNet: the old rule, used only when lt_net.js is absent ---- */
  const legacyWait = async (ck, last, base, sw) => {
    let maxActive = base;
    for (;;) {
      const a = jqActive();
      if (a > maxActive) maxActive = a;
      const el = ck.now() - last;
      if (el >= sw.fallback_ms && a <= base) return { save_signal: 'fallback', waited_ms: el, jq: base + '/' + maxActive };
      if (el >= sw.max_ms) return { save_signal: 'unconfirmed', waited_ms: el, jq: base + '/' + maxActive };
      await ck.sleep(250);
    }
  };
  const SIGNALS = new Set(['model', 'net', 'fallback', 'unconfirmed', 'error']);
  /* LtNet's result in the receipt's terms; unknown shapes read as unconfirmed, never ok. A
   * `model` or `net` result with a key still pending, or with save_confirmed false, reads
   * as unconfirmed too: every key needs its own identified save. */
  const normWait = (w, nKeys) => {
    w = w || {};
    let sig = w.signal || w.save_signal;
    const st = w.state || w.status || w.result;
    if (st === 'error' || st === 'unconfirmed') sig = st;
    if (!SIGNALS.has(sig)) sig = (st === 'ok' && (w.source === 'model' || w.source === 'net')) ? w.source : 'unconfirmed';
    const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
    const errs = Array.isArray(w.save_errors) ? w.save_errors.length : num(w.save_errors);
    const total = num(w.keys_total) ?? nKeys;
    const saved = num(w.keys_saved);
    const pending = num(w.keys_pending);
    if ((sig === 'model' || sig === 'net') &&
        (w.save_confirmed === false || (pending != null && pending > 0) || (saved != null && total != null && saved < total))) {
      sig = 'unconfirmed';
    }
    // Backbone request events whose transport LtNet could not identify (net_diag counter).
    const mu = /\bmodel_unidentified=(\d+)/.exec(typeof w.net_diag === 'string' ? w.net_diag : '');
    // Distinct write endpoints that acknowledged a key, as "METHOD /path/{template}".
    const endpoints = Array.from(new Set((Array.isArray(w.acks) ? w.acks : [])
      .filter(a => a && typeof a.method === 'string' && typeof a.path_template === 'string')
      .map(a => a.method + ' ' + a.path_template))).slice(0, 5);
    return {
      save_signal: sig,
      save_confirmed: sig === 'model' || sig === 'net',
      waited_ms: num(w.waited_ms),
      keys_total: total,
      keys_saved: saved,
      keys_pending: pending,
      save_errors: errs ?? 0,
      jq: typeof w.jq === 'string' ? w.jq : null,
      // The first acknowledgement, which emit_fill.py record turns into the calibration.
      ack_source: typeof w.ack_source === 'string' ? w.ack_source : null,
      ack_method: typeof w.ack_method === 'string' ? w.ack_method : null,
      ack_path_template: typeof w.ack_path_template === 'string' ? w.ack_path_template : null,
      ack_model: num(w.ack_model), ack_net: num(w.ack_net),
      ack_endpoints: endpoints,
      model_unidentified: mu ? Number(mu[1]) : null,
      calibrated: typeof w.calibrated === 'boolean' ? w.calibrated : null,
      calibration_stale: w.calibration_stale === true
    };
  };

  /* ---- the writer shared by fillPage and fillPages: rails 3 to 6 per job ----
   * ctx: {replace, singleKeys, occ (probe entries by key, or null), onPage, ck, sw, w (write
   * state)}. Returns the job's record; a write registers its key and a read-back closure. */
  const writeModel = (w, ck, key, model, parent, fn) => {
    if (w.net) w.net.expect(key, model, parent);
    w.keys.add(key);
    w.last = ck.now();
    G.__ltFillLastWrite = w.last;
    w.writes++;
    try { fn(); return true; } catch (e) { w.threw++; return false; }
  };
  const writeJob = async (ctx, job, byKey) => {
    const rec = { item: job.item, result: null };
    if (ctx.pageTag != null) rec.page = ctx.pageTag;
    const key = String(job.panel_key);
    const raw = byKey[key];
    const { replace, occ, w, ck } = ctx;
    if (replace && job.kind !== 'text' && job.kind !== 'table') { rec.result = 'replace-unsupported-type'; return rec; }
    if (!raw) { rec.result = 'no-panel'; return rec; }
    if (job.plugin_key && pluginOf(raw) !== job.plugin_key) { rec.result = 'plugin-mismatch'; return rec; }
    const c = contentOf(raw);
    const pe = occ ? occ[key] : undefined;
    // rail 3: the capture's flag, the live flag and the probe's flag, any one of them.
    const single = ctx.singleKeys.has(key) || U(c.singleAttempt) === true || (pe && pe.single_attempt === true);
    if (single && job.single_attempt_approved !== true) { rec.result = 'refused-single-attempt'; return rec; }
    if (single) rec.single_attempt = 'approved-write-only';
    if ((pe && pe.read_only === true) || readOnlyOf(raw) === true) { rec.result = 'read-only-refused'; return rec; }
    if ((pe && pe.locked === true) || lockedOf(raw)) { rec.result = 'locked-refused'; return rec; }
    if (!ctx.onPage && !SET_KINDS.has(job.kind)) { rec.result = 'needs-displayed-page'; return rec; }
    // rail 4: the probe. Absent means a subpage child or a page the probe did not read.
    rec.probe = pe ? (pe.occupied === true ? 'occupied' : pe.occupied === false ? 'empty' : 'unknown') : 'absent';
    if (pe && pe.occupied === null) { rec.result = 'occupancy-unknown-skipped'; return rec; }
    /* Lt does not flag every authored table cell, so the probe counts authored text as an
     * answer. A table's own target cells decide its occupancy below. */
    if (pe && pe.occupied === true && !replace && job.kind !== 'table') {
      // Nothing is written either way; a re-run reports our own earlier write as already-new.
      if (alreadyNew(job, raw)) { rec.result = 'already-new'; return rec; }
      rec.result = 'occupied-skipped'; rec.by = 'probe'; return rec;
    }
    const inp = inputOf(raw);
    if (!inp || typeof inp.set !== 'function') { rec.result = 'no-input-model'; return rec; }
    const parent = stateModelOf(raw);
    const skey = saveKeyOf(raw);
    /* Every write goes through here: the ledger entry first, then the write. A throw (a guard
     * refusing a commit mutator, a model rejecting the value) is recorded, never rethrown. */
    const write = (model, fn) => writeModel(w, ck, skey, model, parent, fn);

    if (job.kind === 'text') {
      const before = pageText(U((A(inp) || {}).answer));
      if (before.length) {
        const bh = djb2(before);
        if (bh === job.h) { rec.result = 'already-new'; rec.h = bh; return rec; }
        if (!replace) { rec.result = 'occupied-skipped'; rec.by = 'direct'; rec.chars = before.length; return rec; }
        if (job.oh == null || bh !== job.oh) { rec.result = 'changed-since-fill-skipped'; rec.chars = before.length; return rec; }
        rec.replacing = true;
      }
      if (!write(inp, () => inp.set('answer', job.html))) { rec.result = 'write-threw'; delete rec.replacing; return rec; }
      w.pending.push(() => {
        const live = liveHash(job, raw);
        rec.h = live.h; rec.n = live.n;
        rec.result = live.h === job.h ? (rec.replacing ? 'replaced' : 'written') : 'write-mismatch';
        delete rec.replacing;
      });
    } else if (job.kind === 'table') {
      const rows = gridOf(inp);
      const targets = job.cells.map(t => Object.assign({ job: t }, cellAt(rows, t.row, t.col)));
      const bad = targets.filter(t => t.err);
      if (bad.length) { rec.result = 'geometry-refused'; rec.bad = bad.map(t => t.job.row + ',' + t.job.col + ' ' + t.err).join('; '); rec.rows_live = rows.length; return rec; }
      let changed = 0, filled = 0;
      for (const t of targets) {
        const now = cellText(t.cell);
        t.state = !now.length ? 'empty' : djb2(now) === t.job.h ? 'new' : (t.job.oh != null && djb2(now) === t.job.oh) ? 'ours' : 'other';
        if (t.state !== 'empty') filled++;
        if (t.state === 'other' || (!replace && t.state !== 'empty')) changed++;
      }
      if (targets.every(t => t.state === 'new')) { rec.result = 'already-new'; rec.cells_ok = targets.length + '/' + targets.length; return rec; }
      if (!replace && filled) { rec.result = 'occupied-skipped'; rec.by = 'direct'; rec.filled_cells = filled; return rec; }
      if (replace && changed) { rec.result = 'changed-since-fill-skipped'; rec.filled_cells = changed; return rec; }
      const todo = targets.filter(t => t.state === 'empty' || t.state === 'ours');
      if (!todo.length) { rec.result = 'already-new'; rec.cells_ok = targets.length + '/' + targets.length; return rec; }
      const tset = new Set(job.cells.map(t => t.row + ',' + t.col));
      let others = 0;
      rows.forEach((row, r) => cellsOfRow(row).forEach((cell, ci) => { if (!tset.has(r + ',' + ci) && cellText(cell).length) others++; }));
      rec.other_cells_filled = others;
      const anyOurs = todo.some(t => t.state === 'ours');
      let threw = 0;
      for (const t of todo) { if (!write(t.cell, () => t.cell.set('content', t.job.html))) threw++; }
      w.pending.push(() => {
        let ok = 0;
        for (const t of targets) if (djb2(cellText(t.cell)) === t.job.h) ok++;
        rec.cells_ok = ok + '/' + targets.length;
        rec.result = ok === targets.length ? (anyOurs ? 'replaced' : 'written') : (threw ? 'write-threw' : (ok ? 'partial' : 'write-mismatch'));
        if (threw) rec.threw = threw;
      });
    } else if (job.kind === 'select') {
      if (liveSelection(inp).length) { rec.result = alreadyNew(job, raw) ? 'already-new' : 'occupied-skipped'; if (rec.result !== 'already-new') rec.by = 'direct'; return rec; }
      const live = models(c.answers).map(o => S(U((A(o) || {}).answer)));
      if (job.want.some((ix, i) => NT(live[ix]) !== NT(job.want_text[i]))) { rec.result = 'option-mismatch'; rec.options_live = live.length; return rec; }
      if (job.want.length > 1 && U(c.multiselect) !== true) { rec.result = 'single-select-mismatch'; return rec; }
      if (!write(inp, () => inp.set('selectedAnswers', job.want.slice()))) { rec.result = 'write-threw'; return rec; }
      w.pending.push(() => {
        const live2 = liveHash(job, raw);
        rec.h = live2.h; rec.n = live2.n;
        if (job.kind === 'labels' || job.kind === 'categories') {
          rec.pool_h = labelPool(c).map(t => djb2(pageText(t)));
          rec.index_state = liveIndexState(job, raw);
        }
        rec.result = live2.h === job.h ? 'written' : 'write-mismatch';
      });
    } else if (job.kind === 'labels') {
      if (liveLinks(inp).length) { rec.result = alreadyNew(job, raw) ? 'already-new' : 'occupied-skipped'; if (rec.result !== 'already-new') rec.by = 'direct'; return rec; }
      const pool = labelPool(c);
      const locs = models(c.labelLocationItems).map(x => A(x) || {});
      const calls = [];
      let why = null;
      /* A repeated pool label is placed only when the job places every copy; each placement
       * takes the next unused copy. Lt keys the copies apart. */
      const npool = pool.map(NT), used = new Set();
      const nIn = (arr, t) => arr.filter(v => v === t).length;
      const wanted = job.placements.map(pl => NT(pl.label));
      for (const pl of job.placements) {
        const want = NT(pl.label);
        let li = npool.indexOf(want);
        if (li < 0) { why = 'label-not-in-pool'; break; }
        if (nIn(npool, want) > 1 && nIn(wanted, want) !== nIn(npool, want)) { why = 'label-ambiguous'; break; }
        while (li >= 0 && used.has(li)) li = npool.indexOf(want, li + 1);
        if (li < 0) { why = 'label-ambiguous'; break; }
        used.add(li);
        const at = locs[pl.loc];
        if (!at) { why = 'anchor-mismatch'; break; }
        if (pl.x != null && pl.y != null) {
          const ax = Number(U(at.anchorX)), ay = Number(U(at.anchorY));
          if (!(Math.abs(ax - pl.x) <= 0.005 && Math.abs(ay - pl.y) <= 0.005)) { why = 'anchor-mismatch'; break; }
        }
        calls.push([li, pl.loc]);
      }
      if (why) { rec.result = why; rec.pool = pool.length; rec.locations = locs.length; return rec; }
      const tg = addAnswerTarget(raw);
      if (!tg) { rec.result = 'no-add-answer'; return rec; }
      rec.via = tg.via;
      let threw = 0;
      for (const [li, lo] of calls) { if (!write(inp, () => tg.t.addAnswer(li, lo))) threw++; }
      w.pending.push(() => {
        const live2 = liveHash(job, raw);
        rec.h = live2.h; rec.n = live2.n;
        if (job.kind === 'labels' || job.kind === 'categories') {
          rec.pool_h = labelPool(c).map(t => djb2(pageText(t)));
          rec.index_state = liveIndexState(job, raw);
        }
        rec.result = live2.h === job.h ? 'written' : (threw ? 'write-threw' : (live2.n ? 'write-mismatch' : 'write-not-visible'));
        if (threw) rec.threw = threw;
      });
    } else if (job.kind === 'categories') {
      /* Render first: addAnswer does not create the category entries, and on a page that
       * has not rendered it stored nothing. Wait for the entries, and treat a silent zero
       * as the signal. */
      const t0 = ck.now();
      let entries = liveCatEntries(inputOf(raw));
      while (!entries.length && ck.now() - t0 < ctx.sw.render_wait_ms) { await ck.sleep(250); entries = liveCatEntries(inputOf(raw)); }
      if (!entries.length) { rec.result = 'categories-not-rendered'; return rec; }
      if (entries.some(e => U(e.labelAnswerItems) === undefined)) { rec.result = 'occupancy-unknown-skipped'; rec.by = 'direct'; return rec; }
      if (livePlacements(inputOf(raw)).length) { rec.result = alreadyNew(job, raw) ? 'already-new' : 'occupied-skipped'; if (rec.result !== 'already-new') rec.by = 'direct'; return rec; }
      const pool = labelPool(c), cats = categoryPool(c);
      const calls = [];
      let why = null;
      /* A label that belongs in two bins sits in the pool twice; each placement takes the
       * next unused copy, or the second move would take the first copy out of its bin. */
      const npool = pool.map(NT), used = new Set();
      for (const pl of job.placements) {
        let li = npool.indexOf(NT(pl.label));
        while (li >= 0 && used.has(li)) li = npool.indexOf(NT(pl.label), li + 1);
        if (li < 0) { why = npool.includes(NT(pl.label)) ? 'label-copies-exhausted' : 'label-not-in-pool'; break; }
        if (NT(cats[pl.cat_index]) !== NT(pl.cat)) { why = 'category-not-in-pool'; break; }
        used.add(li);
        calls.push([li, pl.cat_index]);
      }
      if (why) { rec.result = why; rec.pool = pool.length; rec.categories = cats.length; return rec; }
      const tg = addAnswerTarget(raw);
      if (!tg) { rec.result = 'no-add-answer'; return rec; }
      rec.via = tg.via;
      let threw = 0;
      // The third argument is required: without it addAnswer throws after the write lands.
      for (const [li, ci] of calls) { if (!write(inputOf(raw), () => tg.t.addAnswer(li, ci, {}))) threw++; }
      w.pending.push(() => {
        const live2 = liveHash(job, raw);
        rec.h = live2.h; rec.n = live2.n;
        if (job.kind === 'labels' || job.kind === 'categories') {
          rec.pool_h = labelPool(c).map(t => djb2(pageText(t)));
          rec.index_state = liveIndexState(job, raw);
        }
        rec.result = live2.h === job.h ? 'written' : (live2.n ? (threw ? 'write-threw' : 'write-mismatch') : 'write-not-visible');
        if (threw) rec.threw = threw;
      });
    } else {
      rec.result = 'no-write-path';
    }
    return rec;
  };

  /* Rail 3, the submitted single-attempt items the plan passed: each must still read
   * read-only, through the probe on its own displayed page and the view model elsewhere. */
  const checkPassedSingles = (pairs, flat, occ, probePage) => {
    for (const [pg, k] of (pairs || [])) {
      let ro;
      if (occ && probePage != null && Number(pg) === Number(probePage)) ro = occ[String(k)] ? occ[String(k)].read_only : undefined;
      else { const other = pageAt(flat, Number(pg)); const raw = other && panelsOf(other)[String(k)]; ro = raw ? readOnlyOf(raw) : undefined; }
      if (ro !== true) return { fatal: 'single-attempt-not-locked', panel_key: String(k), on_page: Number(pg) };
    }
    return null;
  };

  /* Rail 7 over every key of the call, then the read-back. */
  const settle = async (ctx) => {
    const { w, ck, sw } = ctx;
    let wait = { save_signal: null, save_confirmed: false, waited_ms: 0, keys_total: 0, keys_saved: 0, keys_pending: 0, save_errors: 0, jq: null, ack_source: null, ack_method: null, ack_path_template: null, ack_model: null, ack_net: null, ack_endpoints: [], model_unidentified: null };
    if (w.writes) {
      const keys = Array.from(w.keys);
      G.__ltFillKeys = Array.from(new Set((G.__ltFillKeys || []).concat(keys)));
      if (w.net) {
        let res;
        try { res = await w.net.waitForSave(keys, sw); }
        catch (e) { res = { signal: 'unconfirmed', detail: 'wait-threw' }; }
        wait = normWait(res, keys.length);
      } else {
        const lw = await legacyWait(ck, w.last, w.base, sw);
        wait = Object.assign(wait, lw, { keys_total: keys.length, keys_saved: null, keys_pending: null, net: 'absent' });
      }
      if (wait.waited_ms == null) wait.waited_ms = ck.now() - w.last;
      G.__ltFillWaitedMs = wait.waited_ms;
    }
    for (const f of w.pending) f();
    return wait;
  };

  const countOf = (records, todoN) => {
    const counts = { jobs: records.length, written: 0, replaced: 0, already_new: 0, skipped: 0, refused: 0, failed: 0, todo: todoN };
    const failures = [];
    for (const r of records) {
      if (r.result === 'written') counts.written++;
      else if (r.result === 'replaced') counts.replaced++;
      else if (r.result === 'already-new') counts.already_new++;
      else if (SKIPS.has(r.result)) counts.skipped++;
      else if (FAILS.has(r.result)) { counts.failed++; failures.push(r.item + ' ' + r.result); }
      else { counts.refused++; failures.push(r.item + ' ' + r.result); }
    }
    return { counts, failures };
  };

  /* A save error marks every write of the call: the server refused at least one, and which
   * one is not knowable from here. The fresh-load verify names them. */
  const applySaveError = (records, wait) => {
    if (wait.save_signal !== 'error') return;
    for (const r of records) if (r.result === 'written' || r.result === 'replaced') { r.result = 'save-error'; }
  };
  /* A wait that proved nothing for some key sends the caller to the fresh-load verify before
   * any other page: unconfirmed, error, or the fallback rule with keys whose save LtNet
   * never identified (a transport it cannot see). */
  const nextStep = wait => (wait.save_signal === 'unconfirmed' || wait.save_signal === 'error'
    || (wait.save_signal === 'fallback' && wait.keys_pending > 0))
    ? 'fresh-load verify of these pages before any other page' : null;
  /* The receipt's save fields, shared by fillPage and fillPages. */
  const waitFields = (wait, sw, n, w) => ({
    save_signal: wait.save_signal, save_confirmed: wait.save_confirmed === true, waited_ms: wait.waited_ms,
    wait_capped: wait.save_signal === 'unconfirmed',
    keys_total: wait.keys_total, keys_saved: wait.keys_saved, keys_pending: wait.keys_pending,
    save_errors: wait.save_errors, jq: wait.jq || (w.base + '/' + jqActive()),
    ack_source: wait.ack_source, ack_method: wait.ack_method, ack_path_template: wait.ack_path_template,
    ack_model: wait.ack_model, ack_net: wait.ack_net, ack_endpoints: wait.ack_endpoints || [],
    model_unidentified: wait.model_unidentified ?? null,
    net: n ? 'ltnet' : 'absent', calibrated: wait.calibrated ?? (!!sw.calibration && !!n),
    calibration_stale: !!wait.calibration_stale,
    stale_reason: wait.calibration_stale ? (staleReason(sw.calibration) || 'unknown') : null,
    next: nextStep(wait)
  });

  /* One page. cfg: {module_id, run_id, instance, course_id, origin, page, title, mode,
   * single_attempt_keys, single_attempt_locked: [[page, panel_key]], todo, save_wait}.
   * jobs: emit_fill.py's. deps.probe replaces the probe in tests. */
  async function fillPage(cfg, jobs, deps) {
    deps = deps || {};
    const ck = clock();
    const sw = saveWaitOf(cfg);
    const head = Object.assign({ module: cfg.module_id, run: cfg.run_id, page: cfg.page, mode: cfg.mode || 'fill' }, ids(cfg));
    if (cfg.source_payload_sha256) head.source_payload_sha256 = cfg.source_payload_sha256;
    const fatal = (reason, extra) => Object.assign({ ok: false, fatal: reason }, head, extra || {}, { writes: 0, guard_trips: guardTrips() });

    const loc = checkLocation(cfg, cfg.page);
    if (loc.fatal) return fatal(loc.fatal, loc.run_in_url != null ? { run_in_url: loc.run_in_url } : (loc.page_in_url != null ? { page_in_url: loc.page_in_url } : {}));
    const root = rootOf();
    if (!root) return fatal('no-lt-client');
    const flat = flatPages(root);
    const page = pageAt(flat, cfg.page);
    if (!page) return fatal('page-not-found', { pages: flat.length });
    if ('reportInfoPanels' in page) return fatal('report-page');
    if ('committed' in page && !('allPanels' in page)) return fatal('checkpoint-page');
    const liveTitle = titleText(U(page.title));
    if (cfg.title && liveTitle && liveTitle !== String(cfg.title).replace(/\s+/g, ' ')) return fatal('page-title-mismatch');

    let probe;
    try { probe = await runProbe(deps); }
    catch (e) { return fatal('probe-error', { detail: String(e && e.message || e).slice(0, 80) }); }
    if (!probe || probe.ok !== true) return fatal(probe && probe.is_checkpoint ? 'checkpoint-page' : 'probe-refused');
    if (Number(probe.page_index) !== Number(cfg.page)) return fatal('probe-page-mismatch');
    const occ = {};
    for (const p of probe.panels || []) if (p.kind === 'answerable') occ[p.target.panel_key] = p;
    const passed = checkPassedSingles(cfg.single_attempt_locked, flat, occ, cfg.page);
    if (passed) return fatal(passed.fatal, { panel_key: passed.panel_key, on_page: passed.on_page });

    const n = net();
    const w = { net: n, keys: new Set(), pending: [], writes: 0, threw: 0, last: null, base: jqActive() };
    const ctx = { replace: (cfg.mode || 'fill') === 'replace', singleKeys: new Set((cfg.single_attempt_keys || []).map(String)), occ, onPage: true, ck, sw, w, pageTag: null };
    const byKey = panelsOf(page);
    const records = [];
    for (const job of jobs) records.push(await writeJob(ctx, job, byKey));
    const wait = await settle(ctx);
    applySaveError(records, wait);

    const { counts, failures } = countOf(records, (cfg.todo || []).length);
    return Object.assign({ ok: true }, head, {
      counts, failures, items: records,
      todo: (cfg.todo || []).map(t => t.item + ' ' + t.kind),
      writes: w.writes
    }, waitFields(wait, sw, n, w), {
      guard_trips: guardTrips(),
      probe: (probe.summary ? probe.summary.answerable : Object.keys(occ).length) + ' answerable panels probed'
    });
  }

  /* Several pages in one call. cfg: as fillPage without page, title and the per-page keys,
   * plus batch_pages, never_navigated and stop_before_page. plan: [{page, title_hash, jobs,
   * todo, single_attempt_keys, single_attempt_locked}]. Every rail-1 batch check runs
   * before the first write; one wait covers every key of every page. */
  async function fillPages(cfg, plan, deps) {
    deps = deps || {};
    const ck = clock();
    const sw = saveWaitOf(cfg);
    const pagesWanted = (plan || []).map(e => Number(e.page));
    const head = Object.assign({ module: cfg.module_id, run: cfg.run_id, batch: pagesWanted, mode: cfg.mode || 'fill' }, ids(cfg));
    if (cfg.source_payload_sha256) head.source_payload_sha256 = cfg.source_payload_sha256;
    const fatal = (reason, extra) => Object.assign({ ok: false, fatal: reason }, head, extra || {}, { writes: 0, guard_trips: guardTrips() });

    const loc = checkLocation(cfg, null);
    if (loc.fatal) return fatal(loc.fatal, loc.run_in_url != null ? { run_in_url: loc.run_in_url } : {});
    if (!pagesWanted.length) return fatal('empty-batch');
    /* A batch needs a save calibration that holds on this page: LtNet present, a record of
     * format version 1 taken on this Lt client build, and no service worker (whose answers
     * prove nothing). Anything else refuses before the first write. */
    if (!net()) return fatal('batch-needs-ltnet');
    const cal = sw.calibration;
    if (!cal) return fatal('batch-uncalibrated');
    const stale = staleReason(cal);
    if (stale) return fatal('calibration-stale', { calibration_stale: true, stale_reason: stale });
    try {
      if (G.navigator && G.navigator.serviceWorker && G.navigator.serviceWorker.controller) return fatal('batch-under-service-worker');
    } catch (e) { /* no navigator */ }
    const root = rootOf();
    if (!root) return fatal('no-lt-client');
    const flat = flatPages(root);
    const allowed = new Set((cfg.batch_pages || []).map(Number));
    const never = new Set((cfg.never_navigated || []).map(Number));
    const stop = cfg.stop_before_page == null ? null : Number(cfg.stop_before_page);
    const resolved = [];
    for (const e of plan) {
      const pg = Number(e.page);
      if (!allowed.has(pg)) return fatal('page-not-in-batch', { on_page: pg });
      if (never.has(pg)) return fatal('page-never-navigated', { on_page: pg });
      if (stop != null && pg >= stop) return fatal('page-at-or-past-boundary', { on_page: pg });
      const page = pageAt(flat, pg);
      if (!page) return fatal('page-not-found', { on_page: pg, pages: flat.length });
      if ('reportInfoPanels' in page) return fatal('report-page', { on_page: pg });
      if ('committed' in page && !('allPanels' in page)) return fatal('checkpoint-page', { on_page: pg });
      if (e.title_hash != null && titleHash(U(page.title)) !== Number(e.title_hash)) return fatal('page-title-mismatch', { on_page: pg });
      if (!models(page.allPanels).length) return fatal('page-not-reachable', { on_page: pg });
      resolved.push({ e, pg, page });
    }

    /* The probe reads the displayed page only. When that page is in the batch its entries
     * decide occupancy there; the other pages use the view model and the direct test. */
    let probe;
    try { probe = await runProbe(deps); }
    catch (err) { return fatal('probe-error', { detail: String(err && err.message || err).slice(0, 80) }); }
    if (!probe || probe.ok !== true) return fatal(probe && probe.is_checkpoint ? 'checkpoint-page' : 'probe-refused');
    const shown = currentPageIndex(root) ?? Number(probe.page_index);
    if (Number(probe.page_index) !== Number(shown)) return fatal('probe-page-mismatch');
    const occ = {};
    for (const p of probe.panels || []) if (p.kind === 'answerable') occ[p.target.panel_key] = p;
    for (const { e } of resolved) {
      const passed = checkPassedSingles(e.single_attempt_locked, flat, occ, shown);
      if (passed) return fatal(passed.fatal, { panel_key: passed.panel_key, on_page: passed.on_page });
    }

    const n = net();
    const w = { net: n, keys: new Set(), pending: [], writes: 0, threw: 0, last: null, base: jqActive() };
    const records = [];
    const perPage = [];
    for (const { e, pg, page } of resolved) {
      const onPage = Number(shown) === pg;
      const ctx = { replace: (cfg.mode || 'fill') === 'replace', singleKeys: new Set((e.single_attempt_keys || []).map(String)), occ: onPage ? occ : null, onPage, ck, sw, w, pageTag: pg };
      const byKey = panelsOf(page);
      const recs = [];
      for (const job of e.jobs || []) recs.push(await writeJob(ctx, job, byKey));
      records.push(...recs);
      perPage.push({ pg, recs, todo: e.todo || [] });
    }
    const wait = await settle({ w, ck, sw });
    applySaveError(records, wait);

    const pages = perPage.map(({ pg, recs, todo }) => {
      const { counts } = countOf(recs, todo.length);
      return { page: pg, written: counts.written, replaced: counts.replaced, already_new: counts.already_new, skipped: counts.skipped, refused: counts.refused, failed: counts.failed, todo: counts.todo };
    });
    const { counts, failures } = countOf(records, perPage.reduce((s, p) => s + p.todo.length, 0));
    return Object.assign({ ok: true }, head, {
      counts, failures, pages, items: records,
      todo: perPage.flatMap(p => p.todo.map(t => t.item + ' ' + t.kind)),
      writes: w.writes, shown_page: shown
    }, waitFields(wait, sw, n, w), {
      guard_trips: guardTrips()
    });
  }

  /* The fresh-load check. pages: {"<page>": [lean jobs with h]}. Run it in a tab loaded
   * after the fill: the local model can be right while the server is wrong. In a fresh
   * document (no ledger) it reads at once and reports fresh_load true. In the document
   * that filled it first waits on the open ledger (or the old floor without LtNet) and
   * reports fresh_load false, which proves nothing about the server; a key whose save went
   * through a transport LtNet cannot identify keeps that wait unconfirmed
   * (waited_keys_pending above 0). Open the fresh load in a new tab while such a save may
   * be in flight: reloading the filling tab would cancel it. */
  async function verify(cfg, pages) {
    const ck = clock();
    const sw = saveWaitOf(cfg);
    const head = { module: cfg.module_id, run: cfg.run_id, mode: cfg.mode || 'fill', doc_id: docId(), client_sig: clientSig() };
    if (cfg.source_payload_sha256) head.source_payload_sha256 = cfg.source_payload_sha256;
    const loc = checkLocation(cfg, null);
    if (loc.fatal) return Object.assign({ ok: false, fatal: loc.fatal }, head);
    const root = rootOf();
    if (!root) return Object.assign({ ok: false, fatal: 'no-lt-client' }, head);
    const n = net();
    const ledgerLast = n && typeof n.lastWrite === 'function' ? n.lastWrite() : null;
    const last = typeof G.__ltFillLastWrite === 'number' ? G.__ltFillLastWrite : ledgerLast;
    const fresh = typeof last !== 'number';
    let waited = null, waitedPending = null;
    if (!fresh) {
      const pending = n && typeof n.pendingKeys === 'function' ? n.pendingKeys() : [];
      const keys = Array.from(new Set((G.__ltFillKeys || []).concat(pending)));
      if (n && keys.length) {
        try {
          const wv = normWait(await n.waitForSave(keys, sw), keys.length);
          waited = wv.save_signal;
          waitedPending = wv.keys_pending;
        } catch (e) { waited = 'unconfirmed'; }
      } else {
        while (ck.now() - last < sw.fallback_ms) await ck.sleep(250);
        waited = 'fallback';
      }
    }
    const flat = flatPages(root);
    const items = [], bad = [];
    const counts = { ok: 0, bad: 0, missing: 0 };
    for (const [pg, jobs] of Object.entries(pages)) {
      const page = pageAt(flat, Number(pg));
      if (!page || 'reportInfoPanels' in page) {
        for (const j of jobs) { items.push([j.item, 'no-page']); counts.missing++; bad.push(j.item); }
        continue;
      }
      const byKey = panelsOf(page);
      for (const job of jobs) {
        const raw = byKey[String(job.panel_key)];
        if (!raw) { items.push([job.item, 'no-panel']); counts.missing++; bad.push(job.item); continue; }
        if (job.kind === 'table') {
          const rows = gridOf(inputOf(raw));
          let ok = 0; const off = [], hs = [];
          for (const t of job.cells) {
            const r = cellAt(rows, t.row, t.col);
            const txt = r.cell ? cellText(r.cell) : '';
            const h = djb2(txt);
            hs.push(h);
            if (h === t.h) ok++; else off.push(t.row + ',' + t.col + ':' + txt.length);
          }
          const good = ok === job.cells.length;
          items.push([job.item, good ? 'ok' : 'BAD', ok + '/' + job.cells.length, tableHash(hs)].concat(off.length ? [off.join(' ')] : []));
          if (good) counts.ok++; else { counts.bad++; bad.push(job.item); }
        } else {
          const live = liveHash(job, raw);
          const good = live.h === job.h;
          items.push([job.item, good ? 'ok' : 'BAD', live.n, live.h]);
          if (good) counts.ok++; else { counts.bad++; bad.push(job.item); }
        }
      }
    }
    return Object.assign({ ok: true }, head, {
      fresh_load: fresh, waited: waited, waited_keys_pending: waitedPending,
      observed_at: new Date(ck.now()).toISOString(),
      counts, bad, items
    });
  }

  const LtFillLib = {
    version: 2, fillPage, fillPages, verify,
    // exposed for the parity test and for the emitted scripts; nothing here writes
    pageText, djb2, canon, checkLocation, titleHash, tableHash,
    write: writeModel, liveSelection, liveLinks, livePlacements, labelPool, liveIndexState,
    rootOf, flatPages, pageAt, panelsOf, inputOf, contentOf, stateOf, stateModelOf,
    saveKeyOf, addAnswerTarget, liveHash, clientSig, staleReason, ids, models, A, U, checkPassedSingles
  };
  G.LtFillLib = Object.assign(G.LtFillLib || {}, LtFillLib);
})();
