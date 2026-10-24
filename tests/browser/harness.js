// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* harness.js: a fake Lt page for the browser-layer tests (node:test, no dependencies).
 *
 * The real shared/browser/*.js files run in a node `vm` context whose global looks enough
 * like an Lt run page: a fake clock, FakeXHR, fake fetch, fake WebSocket, fake
 * PerformanceObserver, navigator.sendBeacon, fake Backbone models whose save behaviour is a
 * strategy, a Knockout `$root` with the four commit mutators, a small DOM (window, document,
 * elements, capture and bubble dispatch), HTMLFormElement, a Navigation API stand-in, and
 * jQuery.active fixed at 1 (it never moves unless a test calls env.jq()).
 *
 *   const H = require('./harness.js');
 *   const env = H.makeEnv({ origin, path, pages, jq, navigationApi, t0, scripts, serviceWorker,
 *                           root, rootReady });
 *
 * env members:
 *   ctx                 the page global (globalThis inside the scripts)
 *   clock               now(), sleep(ms) and advance(ms) run due events in time order (ties in
 *                       scheduling order), at(t, fn), after(ms, fn), cancel(id); also ctx.__ltClock
 *   load(src, name)     run a classic script in the page; loadShared('lt_guard'|'lt_net')
 *   runRendered(text)   run an ltjs.py render output (either form); returns a Promise
 *   model(key, o)       fake Backbone model. o: strategy 'immediate' | 'debounced' | 'serial' |
 *                       'model-only' | 'never'; latency (150), debounce (600), gap (400),
 *                       status (200, or null for a request that never ends), method ('PUT'),
 *                       path ('/api/panels/<key>/state'), via 'xhr' | 'fetch' |
 *                       'captured-fetch' (Lt kept the fetch reference from before install) |
 *                       'ws' (WebSocket only, no reply), events (true: request/sync/error)
 *   request(m, path, body, o)   page request now; o: latency, status, via ('xhr'|'fetch'),
 *                       response (response body text; default '' for XHR, '{}' for fetch)
 *   at(t, fn) / after(ms, fn)   schedule test actions on the fake clock
 *   writes              [{key, value, version, t, saved}] from model.set()
 *   requests            [{kind, method, url, path, body, t0, t1, status}]
 *   navigate()          aborts every in-flight request (loadend, status 0), drops timers,
 *                       returns {lost, lost_keys}: writes whose save had not completed
 *   jq(n)               set jQuery.active
 *   perf(n)             deliver n PerformanceObserver resource entries
 *   root, commits       fake $root; commits lists real mutator calls (must stay empty)
 *   form()              a fake form element; formSubmits counts real submits
 *   dispatchSubmit()    a native submit event on a new form; returns the event
 *   el(tag, o)          a DOM element under document.body. o: text, attrs {name: value},
 *                       value, title, parent, on {type: handler} (bubble-phase listeners)
 *   key(el, key, type)  dispatch a keyboard event ('keydown' by default); returns the event
 *   dispatch(el, type, init)  dispatch any event through window, document and the ancestors
 *   navigateEvent(url, {cancelable, downloadRequest})  dispatch a Navigation API navigate
 *                       event; returns {canceled}
 *   setLocation(path)   move the page to another path (same document)
 *   replaceRoot(obj)    Lt swaps its $root for obj (makeEnv({root}) sets the first one)
 *   setHidden(bool)     document.hidden; makeEnv({serviceWorker: true}) adds a controller
 *   setScripts([src])   the page's script tags (makeEnv({scripts}) sets the first list)
 *
 * Fake Backbone models pass one options object to `request` and to the `sync` or `error` that
 * answers it, as Backbone.sync does. Element click() runs the default action of a submit
 * button: a submit event on its form, then a real submit unless a listener cancelled it.
 *
 * Page objects for `pages`: H.page({title, current, report, checkpoint}).
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { webcrypto } = require('crypto');

const REPO = path.resolve(__dirname, '..', '..');
const SHARED_BROWSER = path.join(REPO, 'shared', 'browser');
const DEFAULT_ORIGIN = 'https://lt.example.invalid';
const DEFAULT_PATH = '/i/inst0001/student/courses/1/runs/101/run/page/1';

const flush = () => new Promise(r => setImmediate(r));

class Clock {
  constructor(t0) { this.t = t0; this.q = []; this.n = 0; this.errors = []; }
  now() { return this.t; }
  at(t, fn) { const id = ++this.n; this.q.push({ at: t, fn, id }); return id; }
  after(ms, fn) { return this.at(this.t + Math.max(0, Number(ms) || 0), fn); }
  cancel(id) { this.q = this.q.filter(e => e.id !== id); }
  clear() { this.q = []; }
  async advance(ms) {
    const end = this.t + Math.max(0, ms);
    for (;;) {
      await flush();
      let next = null;
      for (const e of this.q) {
        if (e.at <= end && (!next || e.at < next.at || (e.at === next.at && e.id < next.id))) next = e;
      }
      if (!next) break;
      this.q.splice(this.q.indexOf(next), 1);
      if (next.at > this.t) this.t = next.at;
      try { next.fn(); } catch (err) { this.errors.push(err); }
    }
    this.t = end;
    await flush();
  }
  sleep(ms) { return this.advance(ms); }
}

const obs = v => { const f = () => v; f.__obs = true; return f; };
const page = o => {
  o = o || {};
  const p = { title: obs(o.title || 'Page'), current: obs(o.current === true) };
  if (o.report) p.reportInfoPanels = obs([]);
  if (o.checkpoint) { p.committed = obs(false); p.index = obs(1); } else { p.allPanels = obs([]); }
  return p;
};

/* ---- a small DOM: listeners, capture and bubble dispatch ---- */
class FakeEvent {
  constructor(type, init) {
    const i = init || {};
    this.type = type;
    this.isTrusted = i.isTrusted === true;
    this.bubbles = i.bubbles !== false;
    this.cancelable = i.cancelable !== false;
    this.key = i.key;
    this.code = i.code;
    this.button = i.button || 0;
    this.defaultPrevented = false;
    this.target = null;
    this.currentTarget = null;
    this._stop = false;
    this._stopNow = false;
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this._stop = true; }
  stopImmediatePropagation() { this._stop = true; this._stopNow = true; }
}
const isCapture = opt => opt === true || !!(opt && typeof opt === 'object' && opt.capture);
class FakeTarget {
  constructor() { this._l = []; }
  addEventListener(type, fn, opt) {
    if (!fn) return;
    const capture = isCapture(opt);
    if (this._l.some(l => l.type === type && l.fn === fn && l.capture === capture)) return;
    this._l.push({ type, fn, capture });
  }
  removeEventListener(type, fn, opt) {
    const capture = isCapture(opt);
    this._l = this._l.filter(l => !(l.type === type && l.fn === fn && l.capture === capture));
  }
  listenerCount(type) { return this._l.filter(l => l.type === type).length; }
}
function runListeners(node, ev, phase, errors) {
  ev.currentTarget = node;
  for (const l of node._l.slice()) {
    if (l.type !== ev.type) continue;
    if (phase === 'capture' && !l.capture) continue;
    if (phase === 'bubble' && l.capture) continue;
    try {
      if (typeof l.fn === 'function') l.fn.call(node, ev); else l.fn.handleEvent(ev);
    } catch (err) { errors.push(err); }
    if (ev._stopNow) return;
  }
}
/* path: window, document, then the element's ancestors from the top. */
function dispatch(win, target, ev, errors) {
  ev.target = target;
  const path = [];
  for (let n = target.parentNode; n; n = n.parentNode) path.unshift(n);
  if (target !== win) path.unshift(win);
  for (const n of path) {
    runListeners(n, ev, 'capture', errors);
    if (ev._stop) return !ev.defaultPrevented;
  }
  runListeners(target, ev, 'capture', errors);
  if (!ev._stopNow) runListeners(target, ev, 'bubble', errors);
  if (ev.bubbles && !ev._stop) {
    for (let i = path.length - 1; i >= 0; i--) {
      runListeners(path[i], ev, 'bubble', errors);
      if (ev._stop) break;
    }
  }
  return !ev.defaultPrevented;
}

function makeEnv(opts) {
  const o = opts || {};
  const origin = o.origin || DEFAULT_ORIGIN;
  const clock = new Clock(o.t0 == null ? 1000000 : o.t0);
  const env = {
    clock, writes: [], requests: [], commits: [], formSubmits: 0, navigated: false,
    serialBusy: false, serialQueue: [], wsSent: [], domErrors: []
  };
  const loc = {};
  const setLocation = p => {
    const u = new URL(p, origin);
    Object.assign(loc, {
      href: u.href, origin: u.origin, protocol: u.protocol, host: u.host, hostname: u.hostname,
      port: u.port, pathname: u.pathname, search: u.search, hash: u.hash
    });
  };
  setLocation(o.path || DEFAULT_PATH);
  env.setLocation = setLocation;

  /* ---- requests ---- */
  function start(req) {
    const u = new URL(req.url, loc.href);
    const r = Object.assign({ path: u.pathname, t0: clock.now(), t1: null, status: null }, req);
    env.requests.push(r);
    const plan = r.plan || { status: 200, latency: 100 };
    if (plan.status != null) {
      r.timer = clock.after(plan.latency == null ? 100 : plan.latency, () => end(r, plan.status));
    }
    return r;
  }
  function end(r, status) {
    if (r.t1 != null) return;
    r.t1 = clock.now();
    r.status = status;
    const body = r.plan && typeof r.plan.response === 'string' ? r.plan.response : null;
    if (r.xhr) {
      const x = r.xhr;
      x.readyState = 4;
      x.status = status;
      x.responseText = status > 0 && body != null ? body : '';
      x._fire(status > 0 ? 'load' : 'abort');
      x._fire('loadend');
    }
    if (r.resolve) {
      if (status > 0) {
        const text = body == null ? '{}' : body;
        const res = {
          ok: status >= 200 && status < 300, status,
          text: async () => text, json: async () => JSON.parse(text),
          clone() { return Object.assign({}, this); }
        };
        r.resolve(res);
      } else {
        r.reject(new Error('aborted'));
      }
    }
    if (r.onDone) r.onDone(status);
  }

  class FakeXHR {
    constructor() {
      this.readyState = 0; this.status = 0; this._l = {}; this.responseText = ''; this.responseType = '';
      this._plan = null;
    }
    open(method, url) { this._m = String(method).toUpperCase(); this._u = String(url); this.readyState = 1; }
    setRequestHeader() {}
    addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); }
    removeEventListener(t, f) { this._l[t] = (this._l[t] || []).filter(g => g !== f); }
    _fire(t) {
      const ev = { type: t, target: this };
      const h = this['on' + t];
      if (typeof h === 'function') h.call(this, ev);
      for (const f of (this._l[t] || []).slice()) f.call(this, ev);
    }
    send(body) {
      this._req = start({ kind: 'xhr', method: this._m, url: this._u, body, xhr: this, plan: this._plan, onDone: this._onDone });
    }
    abort() { if (this._req) end(this._req, 0); }
  }
  const fetch0 = (input, init) => new Promise((resolve, reject) => {
    const i = init || {};
    start({ kind: 'fetch', method: String(i.method || 'GET').toUpperCase(), url: String(input && input.url ? input.url : input),
      body: i.body, resolve, reject, plan: i.__plan, onDone: i.__onDone });
  });
  class FakeWebSocket {
    constructor(url) { this.url = new URL(url, loc.href.replace(/^http/, 'ws')).href; }
    send(data) { env.wsSent.push({ url: this.url, t: clock.now(), bytes: String(data).length }); }
    close() {}
  }
  const perfObservers = [];
  class FakePerformanceObserver {
    constructor(cb) { this.cb = cb; }
    observe() { perfObservers.push(this); }
    disconnect() { const i = perfObservers.indexOf(this); if (i >= 0) perfObservers.splice(i, 1); }
  }

  /* ---- DOM ---- */
  const win = new FakeTarget();
  win.__name = 'window';
  const fire = (target, ev) => dispatch(win, target, ev, env.domErrors);

  class FakeElement extends FakeTarget {
    constructor(tag, eo) {
      super();
      const e = eo || {};
      this.tagName = String(tag).toUpperCase();
      this.attrs = Object.assign({}, e.attrs || {});
      this.textContent = e.text || '';
      if (e.value !== undefined) this.value = e.value;
      if (e.title !== undefined) this.title = e.title;
      this.type = this.attrs.type || (this.tagName === 'BUTTON' ? 'submit' : '');
      this.parentNode = null;
      this.children = [];
    }
    get parentElement() { return this.parentNode instanceof FakeElement ? this.parentNode : null; }
    getAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n) ? this.attrs[n] : null; }
    setAttribute(n, v) { this.attrs[n] = String(v); }
    hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n); }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    dispatchEvent(ev) { return fire(this, ev); }
    click() {
      const ok = fire(this, new FakeEvent('click'));
      if (!ok) return;
      if (this.tagName === 'BUTTON' && this.type === 'submit') {
        let f = this.parentNode;
        while (f && f.tagName !== 'FORM') f = f.parentNode;
        if (f && fire(f, new FakeEvent('submit'))) env.formSubmits++;
      }
    }
  }
  class HTMLFormElement extends FakeElement {
    constructor() { super('form'); }
  }
  HTMLFormElement.prototype.submit = function () { env.formSubmits++; };
  HTMLFormElement.prototype.requestSubmit = function () { env.formSubmits++; };

  const document = new FakeTarget();
  document.hidden = false;
  document.body = new FakeElement('body');
  document.body.parentNode = document;
  const pageList = new FakeElement('div', { attrs: { class: 'pageList' } });
  document.body.appendChild(pageList);
  document.querySelector = s => (s === 'div.pageList' ? pageList : null);
  document.querySelectorAll = () => [];
  document.dispatchEvent = ev => fire(document, ev);
  const setScripts = list => {
    document.scripts = (list || []).map(src => ({ src: new URL(src, loc.href).href, tagName: 'SCRIPT' }));
  };
  setScripts(o.scripts);
  env.setScripts = setScripts;

  env.el = (tag, eo) => {
    const e = eo || {};
    const el = tag === 'form' ? new HTMLFormElement() : new FakeElement(tag, e);
    if (tag === 'form') { Object.assign(el.attrs, e.attrs || {}); el.textContent = e.text || ''; }
    (e.parent || document.body).appendChild(el);
    for (const [t, fn] of Object.entries(e.on || {})) el.addEventListener(t, fn);
    return el;
  };
  env.dispatch = (el, type, init) => { const ev = new FakeEvent(type, init); fire(el, ev); return ev; };
  env.key = (el, key, type) => env.dispatch(el, type || 'keydown', { key, code: key === ' ' ? 'Space' : key });
  env.form = () => env.el('form');
  env.dispatchSubmit = () => {
    const f = env.form();
    const ev = new FakeEvent('submit');
    if (fire(f, ev)) env.formSubmits++;
    return ev;
  };

  /* ---- Lt page ---- */
  const pages = o.pages || [page({ title: 'Page 1', current: true })];
  const mutators = {
    commitClicked(x) { env.commits.push(['commitClicked', x]); },
    setCheckpointCommitState(x) { env.commits.push(['setCheckpointCommitState', x]); },
    removeCommittedCheckpoints(x) { env.commits.push(['removeCommittedCheckpoints', x]); },
    removePagesAfterUncommittedCheckpoints(x) { env.commits.push(['removePagesAfterUncommittedCheckpoints', x]); }
  };
  env.mutators = mutators;
  let root = typeof o.root === 'function' ? o.root(env, pages) : Object.assign({ lessonFlowPages: obs(pages) }, mutators);
  env.root = root;
  env.replaceRoot = r => { root = r; env.root = r; };
  env.pages = pages;
  let rootReady = o.rootReady !== false;
  env.setRootReady = v => { rootReady = v; };
  const ko = {
    isObservable: f => typeof f === 'function' && f.__obs === true,
    contextFor: el => (el === pageList && rootReady ? { $root: env.root } : null)
  };

  const navListeners = [];
  const navigation = o.navigationApi === false ? undefined : {
    addEventListener: (t, f) => { if (t === 'navigate') navListeners.push(f); },
    removeEventListener: (t, f) => { const i = navListeners.indexOf(f); if (i >= 0) navListeners.splice(i, 1); }
  };
  env.navListenerCount = () => navListeners.length;
  env.setHidden = v => { document.hidden = !!v; };
  env.navigateEvent = (url, eo) => {
    const ev = {
      type: 'navigate', destination: { url: new URL(url, loc.href).href },
      downloadRequest: eo && typeof eo.downloadRequest === 'string' ? eo.downloadRequest : null,
      cancelable: !(eo && eo.cancelable === false), canceled: false,
      preventDefault() { if (this.cancelable) this.canceled = true; }
    };
    for (const f of navListeners.slice()) f(ev);
    return { canceled: ev.canceled };
  };

  const timers = new Map();
  let timerId = 0;
  const sandbox = {
    console, URL, URLSearchParams, TextEncoder, TextDecoder, Blob, FormData,
    atob: s => Buffer.from(String(s), 'base64').toString('latin1'),
    btoa: s => Buffer.from(String(s), 'latin1').toString('base64'),
    crypto: webcrypto,
    location: loc, document, ko, navigation, HTMLFormElement,
    XMLHttpRequest: FakeXHR, fetch: fetch0, WebSocket: FakeWebSocket,
    PerformanceObserver: FakePerformanceObserver,
    navigator: Object.assign({ sendBeacon: (url, data) => { env.requests.push({ kind: 'beacon', method: 'POST', url: String(url), t0: clock.now(), t1: clock.now(), status: 0 }); return true; } },
      o.serviceWorker ? { serviceWorker: { controller: {} } } : {}),
    jQuery: { active: o.jq == null ? 1 : o.jq },
    __ltClock: clock,
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, clock.after(ms, () => { timers.delete(id); fn(); })); return id; },
    clearTimeout: id => { if (timers.has(id)) { clock.cancel(timers.get(id)); timers.delete(id); } },
    addEventListener: (t, f, opt) => win.addEventListener(t, f, opt),
    removeEventListener: (t, f, opt) => win.removeEventListener(t, f, opt),
    dispatchEvent: ev => fire(win, ev)
  };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  env.ctx = ctx;
  env.window = win;
  env.fetch0 = fetch0;

  env.load = (src, name) => vm.runInContext(src, ctx, { filename: name || 'page-script.js' });
  env.loadShared = n => env.load(fs.readFileSync(path.join(SHARED_BROWSER, n + '.js'), 'utf8'), n + '.js');
  /* Rendered output: function form `async () => {...}`, expression form
   * `await (async () => {...})();`. node's vm has no top-level await in scripts, so the leading
   * `await` is dropped and the promise is returned instead. */
  env.runRendered = text => {
    const s = String(text);
    if (/^await \(/.test(s)) return vm.runInContext(s.replace(/^await /, ''), ctx, { filename: 'rendered-expression.js' });
    return vm.runInContext('(' + s + ')', ctx, { filename: 'rendered-function.js' })();
  };
  env.jq = n => { ctx.jQuery.active = n; };
  env.perf = n => {
    const entries = Array.from({ length: n }, (_, i) => ({ name: loc.origin + '/r/' + i, entryType: 'resource' }));
    for (const po of perfObservers.slice()) po.cb({ getEntries: () => entries });
  };
  env.at = (t, fn) => clock.at(t, fn);
  env.after = (ms, fn) => clock.after(ms, fn);

  /* A page request issued by Lt code at the current time, through the page's own XHR or fetch
   * (so LtNet sees it when installed). */
  env.request = (method, p, body, ro) => {
    const r = ro || {};
    const plan = { status: r.status === undefined ? 200 : r.status, latency: r.latency == null ? 100 : r.latency,
      response: typeof r.response === 'string' ? r.response : undefined };
    const url = new URL(p, loc.origin).href;
    if (r.via === 'fetch') {
      const pr = ctx.fetch(url, { method, body, __plan: plan });
      pr.catch(() => {});
      return pr;
    }
    const x = new ctx.XMLHttpRequest();
    x._plan = plan;
    x.open(method, url);
    x.send(body);
    return x;
  };

  /* ---- fake Backbone models ---- */
  class FakeModel {
    constructor(key, mo) {
      this.key = String(key);
      this.o = Object.assign({ strategy: 'immediate', latency: 150, debounce: 600, gap: 400, status: 200,
        method: 'PUT', path: '/api/panels/' + key + '/state', via: 'xhr', events: true }, mo || {});
      this.attributes = {};
      this._ev = {};
      this.version = 0;
      this._timer = null;
    }
    on(name, cb, c) { (this._ev[name] = this._ev[name] || []).push([cb, c]); return this; }
    off(name, cb) {
      if (!name) { this._ev = {}; return this; }
      this._ev[name] = (this._ev[name] || []).filter(([f]) => cb && f !== cb);
      return this;
    }
    trigger(name, ...args) { for (const [f, c] of (this._ev[name] || []).slice()) f.apply(c || this, args); return this; }
    get(k) { return this.attributes[k]; }
    set(k, v) {
      this.attributes[k] = v;
      this.version++;
      env.writes.push({ key: this.key, value: v, version: this.version, t: clock.now(), saved: false });
      schedule(this);
      return this;
    }
  }
  function schedule(m) {
    if (env.navigated) return;
    const s = m.o.strategy;
    if (s === 'never') return;
    if (s === 'debounced') {
      if (m._timer != null) clock.cancel(m._timer);
      m._timer = clock.after(m.o.debounce, () => { m._timer = null; save(m); });
      return;
    }
    if (s === 'serial') {
      if (env.serialQueue.indexOf(m) < 0) env.serialQueue.push(m);
      clock.after(0, pump);
      return;
    }
    m._timer = clock.after(0, () => { m._timer = null; save(m); });
  }
  function pump() {
    if (env.serialBusy || !env.serialQueue.length || env.navigated) return;
    const m = env.serialQueue.shift();
    env.serialBusy = true;
    save(m, () => { clock.after(m.o.gap, () => { env.serialBusy = false; pump(); }); });
  }
  function save(m, after) {
    if (env.navigated) return;
    const carried = m.version;
    const body = JSON.stringify({ uuid: m.key, value: m.attributes });
    const plan = { status: m.o.status, latency: m.o.latency };
    const url = new URL(m.o.path, loc.origin).href;
    const bb = {};
    let transport = null;
    const done = status => {
      if (status >= 200 && status < 300 && status !== 202) {
        for (const w of env.writes) if (w.key === m.key && w.version <= carried) w.saved = true;
      }
      if (m.o.events && status >= 200 && status < 300) {
        m.trigger('sync', m, {}, bb);
      } else if (m.o.events) {
        m.trigger('error', m, {}, bb);
      }
      if (after) after();
    };
    const via = m.o.strategy === 'model-only' ? 'captured-fetch' : m.o.via;
    if (via === 'xhr') {
      const x = new ctx.XMLHttpRequest();
      x._plan = plan;
      x._onDone = done;
      x.open(m.o.method, url);
      x.send(body);
      transport = x;
    } else if (via === 'fetch') {
      transport = ctx.fetch(url, { method: m.o.method, body, __plan: plan, __onDone: done });
      transport.catch(() => {});
    } else if (via === 'captured-fetch') {
      transport = fetch0(url, { method: m.o.method, body, __plan: plan, __onDone: done });
      transport.catch(() => {});
    } else if (via === 'ws') {
      const ws = new ctx.WebSocket('/socket');
      ws.send(body);
      if (after) after();
      if (m.o.events) m.trigger('request', m, transport, bb);
      return;
    }
    if (m.o.events) m.trigger('request', m, transport, bb);
  }
  env.model = (key, mo) => new FakeModel(key, mo);

  env.navigate = () => {
    for (const r of env.requests) if (r.t1 == null && r.kind !== 'beacon') end(r, 0);
    env.navigated = true;
    clock.clear();
    const lost = env.writes.filter(w => !w.saved);
    return { lost: lost.length, lost_keys: Array.from(new Set(lost.map(w => w.key))) };
  };
  return env;
}

const readShared = n => fs.readFileSync(path.join(SHARED_BROWSER, n + '.js'), 'utf8');

module.exports = { makeEnv, page, obs, Clock, FakeEvent, REPO, SHARED_BROWSER, DEFAULT_ORIGIN, DEFAULT_PATH, readShared };
