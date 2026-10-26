// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* fill_env.js: synthetic Lt pages for the fill tests (no test of its own).
 *
 * Two builders:
 *  - makeEnv(build, o): a small standalone vm page (Knockout and Backbone stand-ins, a fake
 *    clock, jQuery.active that rises while a save is in flight). No LtNet is loaded, so
 *    fill_lib.js takes its fallback wait; the rail tests use it.
 *  - netEnv(o): tests/browser/harness.js (FakeXHR, fake fetch, WebSocket, Backbone models
 *    with save strategies, env.navigate() counting lost writes) with the real
 *    shared/browser/lt_net.js loaded, for the save-wait and batch tests.
 * Every id is synthetic: origin https://lt.example.invalid, instance inst0001, course 1,
 * run 101, module c1-r101.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO = path.resolve(__dirname, '..', '..');
const SKILL = path.join(REPO, 'skills', 'lt-module-fill');
const BROWSER = path.join(SKILL, 'scripts', 'browser');
const EMIT = path.join(SKILL, 'scripts', 'emit_fill.py');
const LIB_SRC = '(function () {\n' + fs.readFileSync(process.env.FILL_LIB_JS || path.join(BROWSER, 'fill_lib.js'), 'utf8') + '\nglobalThis.LtFillLib = LtFillLib;})();';
const PROBE_FILE = fs.readFileSync(path.join(BROWSER, 'probe_panels.js'), 'utf8');
/* probe_panels.js is a function body ending in `return await probeLtPanels();`; for loading
 * it inside a plain function that line goes, as emit_fill.py drops it. */
const PROBE_BODY = PROBE_FILE.replace(/\n[ \t]*return await probeLtPanels\(\);[ \t]*\n?$/, '\n');
const PROBE_LOADER = 'LtFillLib.loadProbe = function () {\n' + PROBE_BODY + '\nreturn probeLtPanels;\n};';
const ORIGIN = 'https://lt.example.invalid';
const RUN_PATH = n => `/i/inst0001/student/courses/1/runs/101/run/page/${n}`;

/* ---- Knockout / Backbone stand-ins ---------------------------------------------------- */
const obs = v => { const f = () => v; f.__obs = true; return f; };
const coll = arr => ({ models: arr });
const mdl = a => ({ attributes: a });

class Model {
  constructor(env, attrs, opts) { this.env = env; this.attributes = Object.assign({}, attrs); this.opts = opts || {}; this.sets = 0; }
  set(k, v) {
    this.sets++; this.env.writes++;
    if (this.opts.onSet) this.opts.onSet(k, v);
    const val = this.opts.transform ? this.opts.transform(k, v) : v;
    if (this.opts.visibleAfter != null) {
      const at = this.env.clk.t + this.opts.visibleAfter, env = this.env;
      Object.defineProperty(this.attributes, k, { configurable: true, enumerable: true, get: () => (env.clk.t >= at ? val : '') });
    } else this.attributes[k] = val;
    this.env.save();
  }
}
class LabelInput extends Model {
  constructor(env, links) { super(env, { linkedLabels: coll(links || []) }); this.calls = []; }
  addAnswer(li, lo) { this.calls.push([li, lo]); this.env.writes++; if (this.env.moveMode) this.attributes.linkedLabels.models = this.attributes.linkedLabels.models.filter(x => x.attributes.labelIndex !== li && x.attributes.locationIndex !== lo); this.attributes.linkedLabels.models.push(mdl({ labelIndex: li, locationIndex: lo })); this.env.save(); }
}
class CatInput extends Model {
  constructor(env, nCats, o) {
    super(env, {});
    o = o || {};
    this.calls = [];
    this.silent = !!o.silent;
    const born = env.clk.t, renderAt = o.renderMs == null ? 0 : o.renderMs;
    const entries = Array.from({ length: nCats }, () => (o.flat ? mdl({ other: 1 }) : mdl({ labelAnswerItems: coll((o.placed || []).slice()) })));
    this.entries = entries;
    Object.defineProperty(this.attributes, 'categoryAnswerItems', {
      enumerable: true, get: () => coll(o.never || env.clk.t - born < renderAt ? [] : entries)
    });
  }
  addAnswer(li, ci, analytics) {
    // Lt's categories addAnswer throws without its third argument, after the write.
    if (arguments.length < 3 || analytics === undefined) throw new Error('analytics required');
    this.calls.push([li, ci]); this.env.writes++;
    if (this.env.moveMode) for (const e of this.entries) e.attributes.labelAnswerItems.models = e.attributes.labelAnswerItems.models.filter(x => x.attributes.labelIndex !== li);
    if (!this.silent) this.entries[ci].attributes.labelAnswerItems.models.push(mdl({ labelIndex: li }));
    this.env.save();
  }
}

const spies = () => ({ checkAnswer: 0, submit: 0 });
function panel(env, key, plugin, st, own) {
  const p = { attributes: { panelKey: key, pluginKey: plugin, panelState: st } };
  // A panel VM carries Check Answer and Submit handlers on the live page; none may be called.
  p.checkAnswer = () => { env.spy.checkAnswer++; };
  p.submitAnswer = () => { env.spy.submit++; };
  return Object.assign(p, own || {});
}
const textPanel = (env, key, answer, o) => {
  o = o || {};
  const st = { content: Object.assign({ question: 'Q' }, o.content || {}), input: new Model(env, Object.assign({ answer: answer || '' }, o.input || {}), o.model) };
  if (o.readOnly != null) st.isReadOnly = o.readOnly;
  return panel(env, key, o.plugin || 'text-question', st);
};
const tableInput = (env, grid, o) => new Model(env, {
  rows: coll(grid.map(r => mdl({ headerRow: !!r.header, cells: coll(r.cells.map(c => new Model(env, { content: c.text || '', headerColumn: !!c.hcol }, o))) })))
});
const tablePanel = (env, key, grid, o) => panel(env, key, 'table-question', { content: {}, input: tableInput(env, grid, o) });
const selectPanel = (env, key, opts, sel, o) => panel(env, key, 'select', {
  content: Object.assign({ answers: coll(opts.map(t => mdl({ answer: t }))) }, (o || {}).content || {}),
  input: new Model(env, { selectedAnswers: sel || [] })
});
const labelPanel = (env, key, labels, anchors, o) => {
  o = o || {};
  const inp = new LabelInput(env, o.links);
  const p = panel(env, key, o.plugin || 'kcImageLabelling', {
    content: { labelItems: coll(labels.map(l => mdl({ label: l }))), labelLocationItems: coll(anchors.map(a => mdl({ anchorX: a[0], anchorY: a[1] }))) },
    input: inp
  });
  if (o.vm) p.currentInputModel = () => inp;
  if (o.noAdd) inp.addAnswer = undefined;
  return { p, inp };
};
const catPanel = (env, key, labels, cats, o) => {
  const inp = new CatInput(env, cats.length, o);
  const p = panel(env, key, 'categories', {
    content: { labelItems: coll(labels.map(l => mdl({ label: l }))), categoryItems: coll(cats.map(c => mdl({ title: c }))) },
    input: inp
  });
  return { p, inp };
};
const page = (idx, title, panels) => ({ pageIndex: idx, title: obs(title), allPanels: panels });

/* ---- the standalone environment (no LtNet: the fallback wait) ---------------------------- */
function makeEnv(build, o) {
  o = o || {};
  const env = { writes: 0, saves: [], saveMs: o.saveMs == null ? 500 : o.saveMs, clicks: 0, commits: 0, spy: spies() };
  const clk = { t: 1000000, now() { return clk.t; }, async sleep(ms) { clk.t += ms; env.tick(); } };
  env.clk = clk;
  env.tick = () => { env.sandbox.jQuery.active = 1 + env.saves.filter(t => t > clk.t).length; };
  env.save = () => { env.saves.push(clk.t + env.saveMs); env.tick(); };
  const btn = { click: () => { env.clicks++; }, getAttribute: () => null, disabled: false };
  const sandbox = {
    console, TextEncoder, AbortController, setTimeout, clearTimeout, crypto: globalThis.crypto,
    location: Object.assign({ pathname: RUN_PATH(o.page == null ? 4 : o.page), search: '', origin: ORIGIN }, o.location || {}),
    document: { querySelector: s => (s === 'div.pageList' ? { __el: true } : null), querySelectorAll: () => [btn] },
    jQuery: { active: 1 }, __ltClock: clk, fetch: o.fetch
  };
  env.sandbox = sandbox;
  const pages = o.pages || build(env);
  const root = {
    lessonFlowPages: () => pages,
    commitClicked: () => { env.commits++; }, setCheckpointCommitState: () => { env.commits++; },
    removeCommittedCheckpoints: () => { env.commits++; }
  };
  env.root = root;
  sandbox.ko = { isObservable: v => typeof v === 'function' && v.__obs === true, contextFor: () => ({ $root: root }) };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  if (o.load !== false) {
    vm.runInContext(LIB_SRC, sandbox);
    vm.runInContext(PROBE_LOADER, sandbox);
  }
  env.pages = pages;
  env.L = sandbox.LtFillLib;
  return env;
}

/* The hash helpers, from a bare load of the library: pure functions, no page needed. */
const L0 = (() => { const sb = {}; sb.globalThis = sb; vm.createContext(sb); vm.runInContext(LIB_SRC, sb); return sb.LtFillLib; })();
const hText = h => L0.djb2(L0.pageText(h));
const SAVE_WAIT = { quiet_ms: 300, max_ms: 20000, fallback_ms: 4000, render_wait_ms: 5000, calibration: null };
const CFG = o => Object.assign({
  module_id: 'c1-r101', run_id: 101, instance: 'inst0001', course_id: 1, origin: ORIGIN,
  mode: 'fill', page: 4, title: 'Page four', single_attempt_keys: [], todo: [],
  save_wait: Object.assign({}, SAVE_WAIT)
}, o || {});
const textJob = (key, html, extra) => Object.assign({ item: 'p4-' + key, panel_key: key, plugin_key: 'text-question', kind: 'text', html, h: hText(html) }, extra || {});
const fourPages = (env, panels) => [page(1, 'One', []), page(2, 'Two', []), page(3, 'Three', []), page(4, 'Page four', panels)];
const run = async (env, cfg, jobs, deps) => env.L.fillPage(CFG(cfg), jobs, deps);
const byItem = (r, id) => (r.items || []).find(x => x.item === id) || {};
const maxDepth = (x, d) => (x === null || typeof x !== 'object') ? d
  : (Object.values(x).length ? Math.max(...Object.values(x).map(v => maxDepth(v, d + 1))) : d);
/* An emitted body (a function body ending in `return await ...;`) as a promise in a context. */
const runBody = (src, ctx) => vm.runInContext('(async () => {\n' + src + '\n})()', ctx);
/* JSON comparison: values from the vm realm and the test realm compare by content. */
const J = v => JSON.stringify(v);

/* ---- the harness environment with the real LtNet ----------------------------------------- */
let H = null;
const harness = () => {
  if (H === null) {
    const p = path.join(REPO, 'tests', 'browser', 'harness.js');
    H = fs.existsSync(p) ? require(p) : false;
  }
  return H;
};
const LT_NET = path.join(REPO, 'shared', 'browser', 'lt_net.js');
const LT_GUARD = path.join(REPO, 'shared', 'browser', 'lt_guard.js');
const haveNet = () => !!harness() && fs.existsSync(LT_NET);
const haveGuard = () => !!harness() && fs.existsSync(LT_GUARD);
const uuidFor = n => '9f1c2e4a-0000-4000-8000-' + String(n).padStart(12, '0');
/* The calibration a network save of the harness models produces, in LtNet's own template
 * form (read from lt_net.js, so the tests follow its format). */
const pathTemplate = p => {
  if (!fs.existsSync(LT_NET)) return p;
  const sb = { URL, TextDecoder, TextEncoder, console };
  sb.globalThis = sb;
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(LT_NET, 'utf8'), sb);
  return sb.LtNet && typeof sb.LtNet.pathTemplate === 'function' ? sb.LtNet.pathTemplate(p) : p;
};
const NET_CAL = { source: 'net', method: 'PUT', path_template: pathTemplate('/api/panels/' + uuidFor(1) + '/state') };

/* A seeded PRNG (mulberry32), so the latencies repeat run to run. */
const prng = seed => () => {
  seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

/* A text panel whose input is a harness model saving under its uuid. */
function netTextPanel(env, key, n, mo) {
  const uuid = uuidFor(n);
  const model = env.model(uuid, mo);
  model.attributes.answer = '';
  const st = { uuids: uuid, content: { question: 'Q' }, input: model };
  return { p: { attributes: { panelKey: String(key), pluginKey: 'text-question', panelState: st } }, model, uuid };
}

/* netEnv({pages: [{title, current, panels: env => [...]}], net: true|false|stubFn, guard}) */
function netEnv(o) {
  o = o || {};
  const h = harness();
  const specs = o.pages || [{ title: 'Page 1', current: true, panels: () => [] }];
  const pages = specs.map(s => h.page({ title: s.title, current: s.current === true }));
  const env = h.makeEnv({ pages, path: o.path || RUN_PATH(o.shown || 1), jq: o.jq });
  env.made = specs.map((s, i) => {
    const made = s.panels(env, i + 1);
    pages[i].allPanels = obs(made.map(m => m.p || m));
    return made;
  });
  if (o.guard) env.load(fs.readFileSync(LT_GUARD, 'utf8'), 'lt_guard.js');
  if (typeof o.net === 'function') env.ctx.LtNet = o.net(env);
  else if (o.net !== false) env.load(fs.readFileSync(LT_NET, 'utf8'), 'lt_net.js');
  env.load(LIB_SRC, 'fill_lib.js');
  env.load(PROBE_LOADER, 'probe_loader.js');
  env.L = env.ctx.LtFillLib;
  return env;
}
const stubProbe = pageIndex => ({ probe: async () => ({ ok: true, page_index: pageIndex, panels: [] }) });
const netCfg = (o, sw) => Object.assign({
  module_id: 'c1-r101', run_id: 101, instance: 'inst0001', course_id: 1, origin: ORIGIN,
  mode: 'fill', page: 1, title: 'Page 1', single_attempt_keys: [], todo: [],
  save_wait: Object.assign({}, SAVE_WAIT, { calibration: NET_CAL }, sw || {})
}, o || {});
const netJob = (key, html, page) => Object.assign({ item: 'p' + (page || 1) + '-' + key, panel_key: String(key), plugin_key: 'text-question', kind: 'text', html, h: hText(html) });

module.exports = {
  REPO, SKILL, BROWSER, EMIT, LIB_SRC, PROBE_FILE, PROBE_BODY, PROBE_LOADER, ORIGIN, RUN_PATH,
  obs, coll, mdl, Model, LabelInput, CatInput, panel, textPanel, tablePanel, selectPanel, labelPanel,
  catPanel, page, makeEnv, L0, hText, SAVE_WAIT, CFG, textJob, fourPages, run, byItem, maxDepth,
  runBody, J, harness, haveNet, haveGuard, uuidFor, NET_CAL, pathTemplate, prng, netTextPanel, netEnv, stubProbe,
  netCfg, netJob
};
