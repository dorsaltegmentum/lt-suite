// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* vm harness for the capture page scripts.
 *
 * capture.js and fetch_media.js expect to run inside an Lt page. These helpers supply
 * synthetic `ko`, `document`, `location` and `fetch` objects in a node vm context and catch
 * each POST, so the tests check exactly the bytes a browser would send.
 * The scripts are loaded the way ltjs.py renders them, as the body of an async function.
 *
 * CAPTURE_JS and FETCH_MEDIA_JS point the tests at other copies of the sources, so an
 * assertion can be watched failing against a reverted file before it is trusted.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {DOMParser} = require('./inert_dom.js');
const { createHmac, webcrypto } = require('node:crypto');

const REPO = path.resolve(__dirname, '..', '..');
const BROWSER = path.join(REPO, 'skills', 'lt-module-capture', 'scripts', 'browser');
const FIXTURES = path.join(REPO, 'tests', 'fixtures');

/* Synthetic run address. The fixture generator uses the same values. */
const ORIGIN = 'https://lt.example.invalid';
const INSTANCE = 'inst0001';
const COURSE = 1;
const RUN = 101;
const PAGE_PATH = `/i/${INSTANCE}/student/courses/${COURSE}/runs/${RUN}/run/page/1`;
const MODULE_ID = `c${COURSE}-r${RUN}`;
const TOKEN = 'test-token-0123456789abcdef';
const SINK = 'http://127.0.0.1:8731';

const source = (name) => {
  const env = name === 'capture' ? process.env.CAPTURE_JS : process.env.FETCH_MEDIA_JS;
  return fs.readFileSync(env || path.join(BROWSER, `${name}.js`), 'utf8');
};

/* Stand-ins for Knockout and Backbone: obs() returns a marked function, coll() an object
 * with `models`, mdl() an object with `attributes`. */
const obs = v => { const f = () => v; f.__obs = true; return f; };
const coll = (...models) => ({ models });
const mdl = attributes => ({ attributes });
const isObs = v => typeof v === 'function' && v.__obs === true;
const KO = { isObservable: isObs };

/* Timers inside the sandbox never hold the process open, and short waits (scroll and
 * settle steps) run at once so a page walk takes milliseconds. Long timers (POST timeouts,
 * blob URL revocation) never fire within a test. */
const fastTimers = () => ({
  setTimeout: (fn, ms) => {
    if (ms >= 1000) { const t = setTimeout(() => {}, 0); clearTimeout(t); return t; }
    // Keep the timer referenced: Node 22 ends the event loop before an unreferenced timer fires.
    return setTimeout(fn, 0);
  },
  clearTimeout: t => clearTimeout(t)
});

/* The readers slice of capture.js: from "readers: begin" to "readers: end". `ko` is its only
 * free variable. The slice fails loudly when a marker moves. */
function sliceReaders(src = source('capture')) {
  const BEGIN = '/* readers: begin';
  const END = '/* readers: end */';
  const a = src.indexOf(BEGIN);
  const b = src.indexOf(END);
  if (a < 0) throw new Error('readers: begin marker not found in capture.js');
  if (b < 0 || b < a) throw new Error('readers: end marker not found after the begin marker');
  const start = src.indexOf('\n', a) + 1;
  const body = src.slice(start, b) + '\nreturn { HANDLERS, AUTOM, DND, pick, readHotspots, NOTEBOOK_NOTE, S };';
  return new Function('ko', 'DOMParser', body)(KO, DOMParser);
}

/* Lt's $root does carry the four commit functions. In the harness each one logs its name
 * and throws: code that unwraps $root member by member fails here, with no run committed. */
function commitTraps(trips) {
  const trap = name => () => { trips.push(name); throw new Error(`capture called ${name}`); };
  return {
    commitClicked: trap('commitClicked'),
    setCheckpointCommitState: trap('setCheckpointCommitState'),
    removeCommittedCheckpoints: trap('removeCommittedCheckpoints'),
    removePagesAfterUncommittedCheckpoints: trap('removePagesAfterUncommittedCheckpoints')
  };
}

/* Build a page view model of each shape the walk discriminates. */
const contentPage = (title, panels) => ({ title: obs(title), allPanels: obs(panels || []), rows: obs([]) });
const reportPage = () => ({ title: obs('Report'), reportInfoPanels: obs([]) });
const checkpointPage = (index, committed) => ({
  title: obs('Checkpoint'), index: obs(index), committed: obs(committed), commitLoading: obs(false)
});
/* Builds a panel in the page.allPanels form: pluginKey and panelKey as functions, and an
 * `input` key in panelState only when the panel takes an answer. */
const panel = (pluginKey, key, content, input, extra) => ({
  pluginKey: obs(pluginKey), panelKey: obs(key),
  panelState: obs(Object.assign({ content }, input === undefined ? {} : { input }, extra || {}))
});

function baseRoot(flowPages, containerPages, trips = []) {
  return Object.assign({
    lessonFlowPages: () => flowPages,
    lessonTitleDisplay: obs('Test module'),
    hasCompletionPage: () => flowPages.some(p => 'reportInfoPanels' in p),
    runLocked: obs(false),
    showPageMenuPageRemaining: obs(false),
    remainingFlowPagesWithoutCheckpoints: obs(0),
    checkpointPageContainer: containerPages === null ? undefined : { pages: obs(containerPages) }
  }, commitTraps(trips));
}

/* A sink response as fetch would return it. */
const response = (status, body) => ({
  status, ok: status >= 200 && status < 300,
  text: async () => (typeof body === 'string' ? body : JSON.stringify(body ?? {})),
  json: async () => (typeof body === 'string' ? JSON.parse(body) : (body ?? {}))
});

/* Run capture.js. `env.root`, `env.location` and `env.globals` describe the page;
 * `opts.args` is passed to captureLtRun (merged over a sink transport with the test token);
 * `opts.reply(name, req)` returns the response for each POST (200 if absent) or throws to
 * simulate a sink nobody can reach; `opts.entry` runs the rendered entry line with
 * `__LT_ARGS__` defined. */
/* A URL class whose blob URLs look like a browser's (blob:<page origin>/<uuid>). */
function pageURL(origin) {
  let n = 0;
  return class PageURL extends URL {
    static createObjectURL() { n++; return `blob:${origin}/00000000-0000-4000-8000-${String(n).padStart(12, '0')}`; }
    static revokeObjectURL() {}
  };
}

/* A Navigation API stand-in: an `<a download>` click dispatches a cancelable navigate event
 * with downloadRequest set, as Chromium does. A listener that cancels it cancels the download. */
function fakeNavigation() {
  const listeners = [];
  return {
    listeners,
    api: { addEventListener: (type, fn) => { if (type === 'navigate') listeners.push(fn); } },
    dispatchDownload(href, file) {
      let cancelled = false;
      const ev = { destination: { url: href }, downloadRequest: file, cancelable: true, canIntercept: false,
                   navigationType: 'push', hashChange: false, preventDefault() { cancelled = true; } };
      for (const fn of listeners) fn(ev);
      return !cancelled;
    }
  };
}

async function runCapture(env, opts = {}) {
  const posts = [];
  const hellos = [];
  const downloads = [];
  const cancelled = [];
  const docs = {};
  const reply = opts.reply || ((name) => response(200, { ok: true, file: `${name}.json` }));
  const loc = env.location || { origin: ORIGIN, pathname: PAGE_PATH };
  const nav = fakeNavigation();
  const listEl = { __el: true };
  const sandbox = {
    console, DOMParser,
    localStorage: opts.storage,
    document: {
      querySelector: sel => (sel === 'div.pageList' ? listEl : null),
      createElement: () => ({
        click() {
          if (nav.dispatchDownload(this.href, this.download)) downloads.push(this.download);
          else cancelled.push(this.download);
        },
        remove() {}
      }),
      body: { appendChild() {} }
    },
    navigation: nav.api,
    URL: pageURL(loc.origin), Blob, AbortController, TextEncoder, TextDecoder, Response, DecompressionStream,
    atob, btoa, crypto: webcrypto,
    ko: Object.assign({ contextFor: () => (env.noContext ? undefined : { $root: env.root }) }, KO),
    location: { pathname: loc.pathname, origin: loc.origin, href: loc.href || `${loc.origin}${loc.pathname}` },
    kuraCloudGlobals: env.globals || { pageLessonId: 1, pageCourseLongName: 'Test course' },
    fetch: async (url, o) => {
      if (String(url).includes('/hello?')) {
        const req = {url: String(url), method: 'GET', headers: o.headers || {}};
        hellos.push(req);
        if (opts.helloReply) return opts.helloReply('hello', req);
        const n = new URL(url).searchParams.get('n');
        const proof = createHmac('sha256', TOKEN).update(`lt-suite-sink|${n}|${loc.origin}`).digest('hex');
        return response(200, {proof});
      }
      const name = String(url).split('/').pop();
      const req = { url: String(url), method: o && o.method, headers: (o && o.headers) || {},
                    credentials: o && o.credentials, body: o && o.body };
      posts.push(req);
      const r = await reply(name, req);
      if (r && r.ok !== false && typeof req.body === 'string') {
        try { docs[name] = JSON.parse(req.body); } catch (e) { /* not JSON */ }
      }
      return r;
    },
    ...fastTimers()
  };
  sandbox.globalThis = sandbox;
  const args = Object.assign({ transport: 'sink', sink: SINK, token: TOKEN }, opts.args || {});
  vm.createContext(sandbox);
  const src = source('capture');
  let receipt;
  if (opts.rendered) {
    /* A file written by ltjs.py render: function form is `async () => {...}`, expression form
     * is `await (async () => {...})();`. */
    const text = opts.rendered.text.trim();
    const code = opts.rendered.form === 'function' ? `(${text})()`
      : `(async () => { return ${text.replace(/;\s*$/, '')}; })()`;
    receipt = await vm.runInContext(code, sandbox);
  } else if (opts.entry) {
    const wrapped = `(async () => {\nconst __LT_ARGS__ = Object.freeze(${JSON.stringify(args)});\n${src}\n})()`;
    receipt = await vm.runInContext(wrapped, sandbox);
    /* A second call in the same tab, with __LT_ARGS__ still baked into the first script's
     * scope: the published function reads those baked values. */
    if (opts.secondCall) receipt = await sandbox.captureLtRun(opts.secondCall);
  } else {
    await vm.runInContext(`(async () => {\n${src}\n})()`, sandbox);
    receipt = await sandbox.captureLtRun(opts.rawArgs !== undefined ? opts.rawArgs : args);
  }
  /* Values made in the vm context carry that context's prototypes; a JSON round trip
   * brings the receipt into this realm so deepStrictEqual compares plain data. */
  if (receipt !== undefined) receipt = JSON.parse(JSON.stringify(receipt));
  const secondReceipt = opts.rendered && opts.secondCall ? await sandbox.captureLtRun(opts.secondCall) : null;
  const moduleName = Object.keys(docs).find(n => n.startsWith('module-'));
  const refName = Object.keys(docs).find(n => n.startsWith('reference-'));
  return {
    receipt, secondReceipt, posts, hellos, downloads, cancelled, navigateListeners: nav.listeners.length,
    doc: moduleName ? docs[moduleName] : null,
    reference: refName ? docs[refName] : null,
    names: posts.map(p => p.url.split('/').pop())
  };
}

/* ---- view-model specs (tests/fixtures/viewmodels/*.json) -------------------------------- */

const isPlain = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const clone = v => JSON.parse(JSON.stringify(v));

/* Wrapped form: every value an observable, every object inside panel content or input a
 * Backbone model, every list an observable array. panelState stays a plain object (Lt hangs
 * it off the panel), and `position` keeps its raw form (a JSON string in Lt). */
function wrapValue(v, key) {
  if (key === 'position') return obs(v);
  if (Array.isArray(v)) return obs(v.map(e => (isPlain(e) ? mdl(wrapObject(e)) : e)));
  if (isPlain(v)) return mdl(wrapObject(v));
  return obs(v);
}
function wrapObject(o) {
  const out = {};
  for (const [k, v] of Object.entries(o)) {
    out[k] = k === 'panelState' ? obs(wrapState(v)) : wrapValue(v, k);
  }
  return out;
}
function wrapState(st) {
  const out = {};
  for (const [k, v] of Object.entries(st || {})) {
    out[k] = (k === 'content' || k === 'input') ? mdl(wrapObject(v || {})) : wrapValue(v, k);
  }
  return out;
}
/* A top-level panel is a view-model object: pluginKey and panelKey are functions. */
function wrapPanel(p) {
  const out = {};
  for (const [k, v] of Object.entries(p)) {
    if (k === 'panelState') out[k] = obs(wrapState(v));
    else out[k] = obs(v);
  }
  return out;
}
function wrapPage(pg) {
  const out = {};
  for (const [k, v] of Object.entries(pg)) {
    out[k] = k === 'allPanels' ? obs((v || []).map(wrapPanel)) : obs(v);
  }
  return out;
}

/* Turn a view-model spec into the env runCapture takes. `wrap` chooses the wrapped form. */
function envFromSpec(spec, { wrap = false, trips = [] } = {}) {
  const s = clone(spec);
  const pages = wrap ? s.pages.map(wrapPage) : s.pages;
  const r = s.root || {};
  const cpc = r.checkpointPageContainer;
  const container = (cpc === null || cpc === undefined) ? undefined
    : { pages: wrap ? obs((cpc.pages || []).map(wrapPage)) : (cpc.pages || []) };
  const root = Object.assign({}, commitTraps(trips), {
    lessonFlowPages: () => pages,
    lessonTitleDisplay: wrap ? obs(r.lessonTitleDisplay ?? '') : (r.lessonTitleDisplay ?? ''),
    hasCompletionPage: () => r.hasCompletionPage === true,
    runLocked: wrap ? obs(r.runLocked === true) : r.runLocked === true,
    showPageMenuPageRemaining: wrap ? obs(r.showPageMenuPageRemaining === true) : r.showPageMenuPageRemaining === true,
    remainingFlowPagesWithoutCheckpoints: wrap ? obs(r.remainingFlowPagesWithoutCheckpoints ?? 0)
                                               : (r.remainingFlowPagesWithoutCheckpoints ?? 0),
    checkpointPageContainer: container
  });
  return { root, location: s.location, globals: s.globals, trips };
}

/* Fixture files. Each loader returns null when the file is missing, so a test can say which
 * dependency is absent instead of crashing. */
function viewmodelSpecs() {
  const d = path.join(FIXTURES, 'viewmodels');
  if (!fs.existsSync(d)) return null;
  return fs.readdirSync(d).filter(f => f.endsWith('.json')).sort()
    .map(f => ({ file: f, spec: JSON.parse(fs.readFileSync(path.join(d, f), 'utf8')) }));
}
/* modules/index.json maps a module id to its file (the demo run is module-demo.json). */
function fixtureModule(id) {
  const dir = path.join(FIXTURES, 'modules');
  let file = `module-${id}.json`;
  const idx = path.join(dir, 'index.json');
  if (fs.existsSync(idx)) {
    const m = (JSON.parse(fs.readFileSync(idx, 'utf8')).modules) || {};
    if (typeof m[id] === 'string') file = m[id];
  }
  const p = path.join(dir, path.basename(file));
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
}
function expectedBoundaries() {
  const p = path.join(FIXTURES, 'modules', 'expected-boundaries.json');
  if (!fs.existsSync(p)) return null;
  const doc = JSON.parse(fs.readFileSync(p, 'utf8'));
  return isPlain(doc.modules) ? doc.modules : doc;
}

/* Normalise a first-stop boundary to [page_index, class, item_id] or null. */
function normStop(v) {
  if (v === null || v === undefined) return null;
  if (Array.isArray(v)) return [v[0], v[1], v[2] ?? null];
  if (isPlain(v)) return [v.page_index, v.class ?? v.kind, v.item_id ?? null];
  return v;
}

/* The fields the roundtrip compares: structure and counts. Timestamps, URLs and prose
 * (notes, `census.confirmed_by`) are left out, because their wording may change freely. */
function shapeOf(doc) {
  const cb = doc.commit_boundaries || {};
  const census = Object.assign({}, doc.census || {});
  delete census.confirmed_by;
  return {
    module_id: doc.module_id,
    page_count: (doc.pages || []).length,
    pages: (doc.pages || []).map(p => ({
      page_index: p.page_index, page_no: p.page_no, route: p.route,
      is_report_page: p.is_report_page, is_checkpoint: p.is_checkpoint,
      rat_candidate: !!p.rat_candidate,
      items: (p.items || []).map(i => ({
        item_id: i.item_id, type: i.type, answered: i.answered, attempts: i.attempts,
        automation: i.automation, read_only: i.read_only === undefined ? null : i.read_only,
        mirror_of: i.mirror_of ? i.mirror_of.item_id : null
      }))
    })),
    checkpoint_pages: cb.checkpoint_pages,
    checkpoints: cb.checkpoints,
    single_attempt_items: cb.single_attempt_items,
    unmapped: (cb.unmapped || []).map(u => u.item_id),
    census
  };
}

module.exports = {
  REPO, BROWSER, FIXTURES, ORIGIN, INSTANCE, COURSE, RUN, PAGE_PATH, MODULE_ID, TOKEN, SINK,
  source, obs, coll, mdl, KO, sliceReaders, commitTraps, contentPage, reportPage,
  checkpointPage, panel, baseRoot, response, runCapture, fastTimers,
  envFromSpec, viewmodelSpecs, fixtureModule, expectedBoundaries, normStop, shapeOf, clone
};
