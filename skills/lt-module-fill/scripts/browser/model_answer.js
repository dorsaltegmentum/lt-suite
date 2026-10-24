/* SPDX-FileCopyrightText: 2026 dorsaltegmentum
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * lt-module-fill: read Lt's model answer for named panels. Read-only.
 *
 * Lt keeps a model answer in each panel's page data (`panelState.modelAnswer`). Reading it
 * is on by default; `model_answer.read = false` in the config turns it off, and then
 * ltjs.py refuses to render this file (exit 4). This file is the body of an async function.
 * Render it with `ltjs.py render model_answer --arg keys=<k1,k2> [--arg page=<n>]`, run it in the Lt tab
 * with the browser tool's evaluate-file action, and save the JSON it returns with
 * `emit_fill.py save-model-answer`, which writes <data>/runs/<module_id>/model-answer.json.
 *
 * It writes nothing, clicks nothing and reads no student answer. A panel key can repeat
 * across pages (the report page mirrors real panels), so report pages are skipped, and a
 * key found on two content pages without `page` is reported ambiguous.
 *
 * The result is flat (rows of strings) because one browser tool truncates objects nested
 * deeper than four levels and single strings longer than about 950 characters; long text
 * comes back in chunks of 800 characters.
 * Row: [panel_key, page, kind, status, ...values]. kind: text, select, labels, categories,
 * table, unknown. status: ok, none (no model answer), empty, no-panel, ambiguous.
 */
'use strict';
{
const __ltMaG = (typeof globalThis !== 'undefined') ? globalThis : window;

const ltModelAnswer = async (args) => {
  const G = __ltMaG;
  args = args || {};
  const keys = (Array.isArray(args.keys) ? args.keys : String(args.keys || '').split(','))
    .map(k => String(k).trim()).filter(Boolean);
  const onlyPage = args.page == null || args.page === '' ? null : Number(args.page);
  if (!keys.length) return { ok: false, fatal: 'no-keys' };
  const el = G.document && G.document.querySelector('div.pageList');
  if (!el || !G.ko) return { ok: false, fatal: 'no-lt-client' };
  const ctx = G.ko.contextFor(el);
  const root = ctx ? ctx.$root : null;
  if (!root) return { ok: false, fatal: 'no-lt-client' };

  const U = v => (G.ko.isObservable(v) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const models = c => { const x = U(c); return x && x.models ? x.models : (Array.isArray(x) ? x : []); };
  const S = h => String(h ?? '')
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\/\s*(p|div|li|tr|h[1-6])\s*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'")
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  const pick = (o, ks) => { const a = A(o) || {}; for (const k of ks) { const v = S(U(a[k])); if (v.length) return v; } return ''; };
  const keyOf = raw => {
    const pa = A(raw) || {};
    try { return typeof raw.panelKey === 'function' ? String(raw.panelKey()) : String(U(pa.panelKey)); }
    catch (e) { return String(U(pa.panelKey)); }
  };
  const panelsOf = page => {
    const byKey = {};
    const walk = (raw, depth) => {
      if (!raw || depth > 5) return;
      const pa = A(raw) || {};
      const k = keyOf(raw);
      if (k && !byKey[k]) byKey[k] = raw;
      for (const key of ['panels', 'allPanels', 'childPanels', 'subPanels', 'children']) for (const ch of models(pa[key] ?? raw[key])) walk(ch, depth + 1);
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
  const chunks = (s, n) => { const out = []; for (let i = 0; i < s.length; i += n) out.push(s.slice(i, i + n)); return out.length ? out : ['']; };

  const flowList = models(root.lessonFlowPages());
  const flat = flowList.map(pg => A(pg) || pg);
  const pageNo = (p, i) => { const v = Number(U(p.pageIndex)); return Number.isInteger(v) && v > 0 ? v : i + 1; };
  const content = flat.map((p, i) => ({ p, idx: pageNo(p, i) }))
    .filter(({ p }) => !('reportInfoPanels' in p) && 'allPanels' in p)
    .filter(({ idx }) => onlyPage == null || idx === onlyPage);

  const rows = [];
  for (const key of keys) {
    const hits = [];
    for (const { p, idx } of content) { const raw = panelsOf(p)[key]; if (raw) hits.push({ raw, idx }); }
    if (!hits.length) { rows.push([key, onlyPage, 'unknown', 'no-panel']); continue; }
    if (hits.length > 1) { rows.push([key, null, 'unknown', 'ambiguous'].concat(hits.map(h => 'page ' + h.idx))); continue; }
    const { raw, idx } = hits[0];
    const st = A(U((A(raw) || {}).panelState)) || {};
    const c = A(U(st.content)) || {};
    const ma = A(U(st.modelAnswer));
    if (!ma || typeof ma !== 'object') { rows.push([key, idx, 'unknown', 'none']); continue; }
    if (ma.answer !== undefined) {
      const t = S(U(ma.answer));
      rows.push([key, idx, 'text', t.length ? 'ok' : 'empty'].concat(t.length ? chunks(t, 800) : []));
    } else if (ma.selectedAnswers !== undefined) {
      const opts = models(c.answers).map(o => S(U((A(o) || {}).answer)));
      const sel = models(ma.selectedAnswers).filter(n => Number.isInteger(n));
      rows.push([key, idx, 'select', sel.length ? 'ok' : 'empty'].concat(sel.map(i => i + ': ' + (opts[i] ?? '?'))));
    } else if (ma.linkedLabels !== undefined) {
      const pool = models(c.labelItems).map(x => pick(x, ['label', 'text', 'name', 'title']));
      const linkAttrs = models(ma.linkedLabels).map(link => A(link) || {});
      const links = linkAttrs.map(la => [U(la.labelIndex), U(la.locationIndex)]).filter(p => p[0] != null && p[1] != null);
      rows.push([key, idx, 'labels', links.length ? 'ok' : 'empty'].concat(links.map(([li, lo]) => 'loc ' + lo + ': ' + (pool[li] ?? '#' + li))));
    } else if (ma.categoryAnswerItems !== undefined) {
      const pool = models(c.labelItems).map(x => pick(x, ['label', 'text', 'name', 'title']));
      const cats = models(c.categoryItems).map(x => pick(x, ['title', 'category', 'label', 'name', 'text']));
      const out = [];
      models(ma.categoryAnswerItems).map(x => A(x) || {}).forEach((e, pos) => {
        const ci = Number.isInteger(U(e.categoryIndex)) ? U(e.categoryIndex) : pos;
        for (const l of models(e.labelAnswerItems)) { const li = U((A(l) || {}).labelIndex); if (li != null) out.push('cat ' + ci + ' ' + (cats[ci] ?? '?') + ': ' + (pool[li] ?? '#' + li)); }
      });
      rows.push([key, idx, 'categories', out.length ? 'ok' : 'empty'].concat(out));
    } else if (ma.rows !== undefined || ma.cells !== undefined) {
      let grid = models(ma.rows); if (!grid.length) grid = models(ma.cells);
      const out = [];
      grid.forEach((row, r) => {
        const ra = A(row) || {};
        let cs = models(ra.cells); if (!cs.length) cs = models(ra.columns);
        cs.forEach((cell, ci) => { const t = S(U((A(cell) || {}).content)); if (t.length) out.push(r + ',' + ci + ': ' + t.slice(0, 780)); });
      });
      rows.push([key, idx, 'table', out.length ? 'ok' : 'empty'].concat(out));
    } else {
      rows.push([key, idx, 'unknown', 'none']);
    }
  }
  const ck = G.__ltClock || { now: () => Date.now() };
  return { ok: true, read_at: new Date(ck.now()).toISOString(), keys: keys.length, rows };
};

__ltMaG.ltModelAnswer = ltModelAnswer;
}

return await globalThis.ltModelAnswer(typeof __LT_ARGS__ !== 'undefined' ? __LT_ARGS__ : {});
