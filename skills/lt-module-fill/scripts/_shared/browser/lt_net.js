// Generated from shared/browser/lt_net.js by tools/sync_shared.py. Edit the source.
// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* lt_net.js: save-request watcher for Lt fill scripts (global `LtNet`).
 *
 * Lt sends a save after a model `set` returns, and navigating before that request completes
 * loses the write. `jQuery.active` idles at 1 on Lt (a long-lived request), so global network
 * idle cannot prove a save. This file proves each write's own save, by key, and keeps the old
 * fixed wait whenever it cannot.
 *
 *   LtNet.install()                   hooks XHR, fetch, WebSocket.send, sendBeacon and a
 *                                     PerformanceObserver once per document; never removed.
 *   LtNet.expect(key, models, parent, opts)
 *                                     call immediately BEFORE each write. `models` is the
 *                                     Backbone model written (or an array of them); `parent`
 *                                     is the panel's panelState model. opts.contains: strings
 *                                     a network save of this key must carry (optional).
 *   LtNet.waitForSave(keys, opts)     opts {quiet_ms, max_ms, fallback_ms, calibration}; polls
 *                                     every 50 ms through clock() (tests inject a fake clock).
 *
 * Model acknowledgement: Backbone request events must identify an XHR or fetch transport
 * observed by this watcher. The same operation's options object must accompany sync/error.
 * Only a same-origin write with final 2xx status other than 202 can acknowledge a model;
 * a calibrated wait also requires its exact method and endpoint template. Captured transports
 * that the watcher cannot identify remain unconfirmed. Every written model must sync, or a
 * unique parent must sync; a model shared by two keys acknowledges neither.
 *
 * Network acknowledgement: a same-origin write started after the key's last expect must name
 * the key exactly in its URL or body. A 2xx response other than 202 acknowledges a single key.
 * A multi-key response requires an explicit saved outcome for each key; errors override success
 * and absent outcomes remain pending. WebSocket and beacon sends only mark keys sent.
 * Keys shorter than 6 characters are never acknowledged by the network.
 *
 * Calibration binding: a record that carries `client_sig` applies only while the page loads
 * the same Lt scripts (origin and path of every script src, hashed). A record with another
 * signature, a missing write endpoint, or a record `version` other than 1, is stale: the wait runs the fallback rule
 * and the receipt says `calibration_stale: true`.
 *
 * End states (`save_signal`; `state` is the same with `model` and `net` reported as `ok`):
 *   error        any key failed
 *   model / net  calibrated origin: every key saved and no counted request in flight for
 *                quiet_ms (counted: same-origin non-GET started after the first expect; with a
 *                network calibration, only requests matching its method and path template)
 *   fallback     uncalibrated origin, a stale calibration, or a page a service worker
 *                controls (its replies can come from a cache): the old rule, at
 *                least fallback_ms after the last write and jQuery.active back at or below its
 *                value at the first expect. `save_confirmed` is false.
 *   unconfirmed  max_ms after the last write with the rule above unmet, or the page was hidden
 *                during a fallback wait (timers stall in hidden tabs). The caller must not
 *                navigate and runs a fresh-load verify next.
 *
 * Every timeout counts from the latest expect, on performance.now() when the page has it.
 * Request bodies are parsed in memory to match keys and are never stored, logged or returned.
 * Request content, return values and exceptions pass through unchanged.
 */
(function (G) {
  'use strict';
  if (!G || (G.LtNet && G.LtNet.version === 1)) return;

  const NON_ACK = { GET: 1, HEAD: 1, OPTIONS: 1 };
  const WEAK_LEN = 6;
  const POLL_MS = 50;
  const KEEP_REQS = 500;
  const MAX_VALUES = 5000;

  const st = {
    installed: false, seq: 0, entries: new Map(), lastWrite: null,
    session: { open: false, startSeq: 0, jqBase: 0, jqMax: 0 },
    reqs: [], transports: new WeakMap(), ours: new WeakSet(), subCount: new WeakMap(),
    diag: { requests: 0, non_get: 0, matched: 0, perf_entries: 0, ws_sends: 0, beacons: 0, model_unidentified: 0, shared_model: 0, multi_key: 0 }
  };
  Object.defineProperty(G, '__ltNet', {
    value: Object.freeze({ installed: true, version: 1 }), enumerable: false, configurable: false, writable: false
  });

  const realClock = {
    now: () => ((G.performance && typeof G.performance.now === 'function') ? G.performance.now() : Date.now()),
    sleep: ms => new Promise(r => G.setTimeout(r, ms))
  };
  const clock = () => G.__ltClock || realClock;
  const now = () => clock().now();
  const jqActive = () => ((G.jQuery && typeof G.jQuery.active === 'number') ? G.jQuery.active : 0);
  const num = (v, d) => (typeof v === 'number' && isFinite(v) && v >= 0 ? v : d);
  const hidden = () => { try { return !!(G.document && G.document.hidden === true); } catch (e) { return false; } };
  const swControlled = () => { try { return !!(G.navigator && G.navigator.serviceWorker && G.navigator.serviceWorker.controller); } catch (e) { return false; } };

  /* ---- URL helpers ---- */
  const here = () => { try { return new G.URL(String(G.location.href)); } catch (e) { return null; } };
  const parse = url => { const h = here(); try { return new G.URL(String(url), h ? h.href : undefined); } catch (e) { return null; } };
  const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
  /* Path template with typed parameters: {uuid}, then whole hex segments of 8+ chars as {hex},
   * then digit runs as {n}. */
  const pathTemplate = path => String(path || '').split('/').map(seg => {
    if (/^[0-9a-f]{8,}$/i.test(seg) && /\d/.test(seg) && !UUID.test(seg)) { UUID.lastIndex = 0; return '{hex}'; }
    UUID.lastIndex = 0;
    return seg.replace(UUID, '{uuid}').replace(/\d+/g, '{n}');
  }).join('/');

  /* ---- identifiers named by a request, in memory only ---- */
  function collect(v, out, depth) {
    if (out.length > MAX_VALUES || depth > 8 || v == null) return;
    if (typeof v === 'string') { out.push(v); return; }
    if (typeof v === 'number' && isFinite(v)) { out.push(String(v)); return; }
    if (Array.isArray(v)) { for (const x of v) collect(x, out, depth + 1); return; }
    if (typeof v === 'object') { for (const k of Object.keys(v)) collect(v[k], out, depth + 1); }
  }
  function valuesOfText(text) {
    const out = [];
    const t = String(text || '').trim();
    if (!t) return out;
    if (t[0] === '{' || t[0] === '[' || t[0] === '"') {
      try { collect(JSON.parse(t), out, 0); return out; } catch (e) { /* not JSON */ }
    }
    if (G.URLSearchParams && /^[^\s{}[\]"]*=/.test(t)) {
      try { new G.URLSearchParams(t).forEach(v => { out.push(v); valuesOfText(v).forEach(x => out.push(x)); }); } catch (e) { /* not a form */ }
    }
    return out;
  }
  const decode = buf => { try { return new G.TextDecoder().decode(buf); } catch (e) { return ''; } };
  /* Values carried by a body: an array, or a Promise of one for a Blob or a Request body. */
  function bodyValues(body) {
    if (body == null) return [];
    if (typeof body === 'string') return valuesOfText(body);
    try {
      if (G.URLSearchParams && body instanceof G.URLSearchParams) return valuesOfText(body.toString());
      if (G.FormData && body instanceof G.FormData) {
        const out = [];
        body.forEach(v => { if (typeof v === 'string') { out.push(v); valuesOfText(v).forEach(x => out.push(x)); } });
        return out;
      }
      if (typeof ArrayBuffer !== 'undefined' && (body instanceof ArrayBuffer || ArrayBuffer.isView(body))) return valuesOfText(decode(body));
      if (typeof body.then === 'function') return body.then(t => valuesOfText(t), () => []);
      if (typeof body.text === 'function' && typeof body.size === 'number') return body.text().then(t => valuesOfText(t), () => []);
    } catch (e) { return []; }
    return [];
  }
  /* Retain only explicit per-item outcomes, never arbitrary response values. Errors win. */
  function responseOutcomes(value) {
    const saved = new Set(), failed = new Set();
    if (typeof value === 'string') {
      try { value = JSON.parse(value); } catch (e) { return { saved, failed, explicit: false }; }
    }
    let explicit = false, count = 0;
    function visit(v, context, depth) {
      if (v == null || depth > 8 || ++count > MAX_VALUES) return;
      if (Array.isArray(v)) { for (const item of v) visit(item, context, depth + 1); return; }
      if (typeof v === 'string' || typeof v === 'number') {
        if (context) (context === 'failed' ? failed : saved).add(String(v));
        return;
      }
      if (typeof v !== 'object') return;
      const status = String(v.status || v.outcome || '').toLowerCase();
      let result = context;
      if (v.error || v.errors || v.success === false || v.ok === false || /^(error|failed|invalid)$/.test(status)) result = 'failed';
      else if (v.success === true || v.ok === true || /^(ok|saved|success)$/.test(status)) result = 'saved';
      for (const field of ['uuid', 'id', 'key', 'panel_key']) {
        if (result && (typeof v[field] === 'string' || typeof v[field] === 'number')) {
          explicit = true;
          (result === 'failed' ? failed : saved).add(String(v[field]));
        }
      }
      for (const [field, child] of Object.entries(v)) {
        let next = null;
        if (/^(saved|successes|successful)$/.test(field)) { next = 'saved'; explicit = true; }
        if (/^(errors|failed|failures)$/.test(field)) { next = 'failed'; explicit = true; }
        // Identity fields were handled above. Arbitrary payload text never inherits success.
        if (next || (child && typeof child === 'object')) visit(child, next, depth + 1);
      }
    }
    visit(value, null, 0);
    return { saved, failed, explicit };
  }
  function xhrOutcomes(xhr) {
    try {
      const rt = xhr.responseType;
      if (!rt || rt === 'text') return responseOutcomes(xhr.responseText);
      if (rt === 'json') return responseOutcomes(xhr.response);
    } catch (e) { /* unreadable response */ }
    return null;
  }
  function urlValues(u) {
    const out = [];
    if (!u) return out;
    for (const seg of u.pathname.split('/')) { if (seg) { try { out.push(decodeURIComponent(seg)); } catch (e) { out.push(seg); } } }
    try { u.searchParams.forEach(v => out.push(v)); } catch (e) { /* no params */ }
    return out;
  }

  /* ---- ledger ---- */
  const modelState = () => ({ sent: false, opts: new WeakMap(), ops: [] });
  function addSub(e, m, role) {
    if (!m || typeof m.on !== 'function') return;
    const ms = modelState();
    const onRequest = (a, b, options) => {
      if (st.entries.get(e.key) !== e || !e.subs.has(m)) return;
      if (!options || typeof options !== 'object') { st.diag.model_unidentified++; return; }
      // Backbone passes the actual transport as its second request-event argument.
      const r = st.transports.get(b) || st.transports.get(options.xhr);
      if (!r || !counted(r) || r.seq <= e.seq) { st.diag.model_unidentified++; return; }
      const op = { rec: r, synced: false, failed: false };
      ms.opts.set(options, op);
      ms.ops.push(op);
      ms.sent = true;
    };
    const onDone = ok => (a, b, options) => {
      if (st.entries.get(e.key) !== e || !e.subs.has(m)) return;
      if ((st.subCount.get(m) || 0) > 1) { st.diag.shared_model++; return; }
      const op = options && typeof options === 'object' ? ms.opts.get(options) : null;
      if (!op) return;
      if (ok) op.synced = true;
      else op.failed = true;
    };
    const handlers = [['request', onRequest], ['sync', onDone(true)], ['error', onDone(false)]];
    const done = [];
    for (const [ev, fn] of handlers) {
      try { m.on(ev, fn); done.push([ev, fn]); } catch (x) { /* not a Backbone model */ }
    }
    if (!done.length) return;
    st.subCount.set(m, (st.subCount.get(m) || 0) + 1);
    e.subs.set(m, { role, ms, handlers: done });
  }
  function dropSub(e, m) {
    const s = e.subs.get(m);
    if (!s) return;
    for (const [ev, fn] of s.handlers) { try { m.off(ev, fn); } catch (x) { /* model gone */ } }
    st.subCount.set(m, Math.max(0, (st.subCount.get(m) || 1) - 1));
    e.subs.delete(m);
  }

  function expect(key, models, parent, opts) {
    install();
    const k = String(key);
    const t = now();
    if (!st.session.open) {
      const jq = jqActive();
      st.session = { open: true, startSeq: st.seq, jqBase: jq, jqMax: jq };
      st.reqs = [];
    }
    st.seq++;
    let e = st.entries.get(k);
    const fresh = !e || e.sessionStart !== st.session.startSeq || keyState(e, null).state !== 'pending';
    if (fresh) {
      if (e) for (const m of Array.from(e.subs.keys())) dropSub(e, m);
      e = { key: k, seq: st.seq, sessionStart: st.session.startSeq, tWrite: t, weak: k.length < WEAK_LEN,
        net: [], wsSent: false, subs: new Map(), contains: [] };
      st.entries.set(k, e);
    }
    e.seq = st.seq;
    e.tWrite = t;
    const list = Array.isArray(models) ? models : (models ? [models] : []);
    for (const m of list) { dropSub(e, m); addSub(e, m, 'model'); }
    for (const [m, s] of Array.from(e.subs.entries())) if (s.role === 'parent') dropSub(e, m);
    if (parent && list.indexOf(parent) < 0) addSub(e, parent, 'parent');
    const c = opts && opts.contains;
    e.contains = (Array.isArray(c) ? c : (c == null ? [] : [c])).map(String).filter(Boolean);
    st.lastWrite = t;
    return { key: k, seq: e.seq };
  }

  /* ---- requests ---- */
  function newRec(kind, method, url) {
    const u = parse(url);
    const h = here();
    st.seq++;
    const rec = {
      kind, seq: st.seq, t0: now(), t1: null, status: null, done: false, ended: false, matched: false,
      resp: null, respPending: false,
      method: String(method || 'GET').toUpperCase(),
      same: !!(u && h && u.origin === h.origin),
      tpl: u ? pathTemplate(u.pathname) : '', url: u, keys: []
    };
    st.diag.requests++;
    return rec;
  }
  const counted = rec => rec.same && !NON_ACK[rec.method];

  function applyMatch(rec, values, cands) {
    const set = new Set(values);
    for (const e of cands) {
      if (st.entries.get(e.key) !== e || e.seq >= rec.seq || e.weak) continue;
      if (!set.has(e.key)) continue;
      if (e.contains.length && !e.contains.every(c => values.some(v => typeof v === 'string' && v.indexOf(c) >= 0))) continue;
      rec.keys.push({ e, gen: e.seq });
    }
    if (rec.keys.length) st.diag.matched++;
    rec.matched = true;
    settle(rec);
  }

  function startRec(rec, body) {
    if (!counted(rec)) { rec.matched = true; return rec; }
    st.diag.non_get++;
    st.reqs.push(rec);
    if (st.reqs.length > KEEP_REQS) st.reqs.shift();
    const cands = [];
    for (const e of st.entries.values()) if (e.seq < rec.seq && !e.weak) cands.push(e);
    if (!cands.length) { rec.matched = true; return rec; }
    const fromUrl = urlValues(rec.url);
    const vals = bodyValues(body);
    if (vals && typeof vals.then === 'function') {
      vals.then(v => applyMatch(rec, fromUrl.concat(v || []), cands), () => applyMatch(rec, fromUrl, cands));
    } else {
      applyMatch(rec, fromUrl.concat(vals), cands);
    }
    return rec;
  }

  /* `resp`: explicit response outcomes, a Promise of outcomes, or null when unread. */
  function endRec(rec, status, resp) {
    if (!rec || rec.ended) return;
    rec.ended = true;
    rec.t1 = now();
    rec.status = typeof status === 'number' ? status : 0;
    if (resp && typeof resp.then === 'function') {
      rec.respPending = true;
      resp.then(v => { rec.resp = v; }, () => { rec.resp = null; })
        .then(() => { rec.respPending = false; settle(rec); });
    } else {
      rec.resp = resp || null;
    }
    settle(rec);
  }
  /* An explicit per-key error can override even a single-key HTTP success. */
  const wantsResponse = rec => !!rec && counted(rec);

  /* Record the outcome on each named key once the match, the end and any response read are
   * known. A batch saves only keys with an explicit successful response outcome. */
  function settle(rec) {
    if (!rec.matched || !rec.ended || rec.done || rec.respPending) return;
    rec.done = true;
    if (rec.keys.length > 1) st.diag.multi_key++;
    for (const { e, gen } of rec.keys) {
      if (st.entries.get(e.key) !== e || e.seq !== gen) continue;
      e.net.push({ outcome: requestOutcome(rec, e.key), t1: rec.t1, method: rec.method, tpl: rec.tpl, seq: rec.seq });
    }
  }
  function requestOutcome(rec, key) {
    const s = rec.status;
    if (!(s >= 200 && s < 300)) return 'failed';
    if (rec.resp && rec.resp.failed.has(key)) return 'failed';
    if (s === 202) return 'accepted';
    if (rec.keys.length > 1 || (rec.resp && rec.resp.explicit)) {
      return rec.resp && rec.resp.saved.has(key) ? 'ok' : 'accepted';
    }
    return 'ok';
  }

  function markSent(url, data) {
    const u = parse(url);
    const h = here();
    if (!u || !h || u.host !== h.host) return;
    const vals = bodyValues(data);
    if (!Array.isArray(vals)) return;
    const all = urlValues(u).concat(vals);
    for (const e of st.entries.values()) if (!e.weak && all.indexOf(e.key) >= 0) e.wsSent = true;
  }

  /* ---- hooks ---- */
  function hookXHR() {
    const XP = G.XMLHttpRequest && G.XMLHttpRequest.prototype;
    if (!XP || typeof XP.open !== 'function' || typeof XP.send !== 'function') return;
    if (st.ours.has(XP.open) || st.ours.has(XP.send)) return;
    const info = new WeakMap();
    const open0 = XP.open;
    const send0 = XP.send;
    const open1 = function (method, url) {
      try { info.set(this, { method, url }); } catch (e) { /* frozen object */ }
      return open0.apply(this, arguments);
    };
    const send1 = function (body) {
      let rec = null;
      try {
        const i = info.get(this) || {};
        rec = startRec(newRec('xhr', i.method, i.url), body);
        st.transports.set(this, rec);
        const xhr = this;
        xhr.addEventListener('loadend', () => endRec(rec, xhr.status, wantsResponse(rec) ? xhrOutcomes(xhr) : null));
      } catch (e) { rec = null; }
      try {
        return send0.apply(this, arguments);
      } catch (e) {
        endRec(rec, 0);
        throw e;
      }
    };
    st.ours.add(open1); st.ours.add(send1);
    XP.open = open1;
    XP.send = send1;
  }

  function hookFetch() {
    if (typeof G.fetch !== 'function' || st.ours.has(G.fetch)) return;
    const f0 = G.fetch;
    const f1 = function (input, init) {
      let rec = null;
      try {
        const isReq = !!(G.Request && input instanceof G.Request);
        const method = (init && init.method) || (isReq ? input.method : 'GET');
        const url = isReq ? input.url : String(input);
        let body = init && init.body !== undefined ? init.body : null;
        if (body == null && isReq && !NON_ACK[String(method).toUpperCase()]) {
          try { body = input.clone().text(); } catch (e) { body = null; }
        }
        rec = startRec(newRec('fetch', method, url), body);
      } catch (e) { rec = null; }
      let p;
      try {
        p = f0.apply(this, arguments);
      } catch (e) {
        endRec(rec, 0);
        throw e;
      }
      if (rec && p && typeof p.then === 'function') {
        st.transports.set(p, rec);
        p.then(res => {
          let rv = null;
          if (wantsResponse(rec) && res && typeof res.clone === 'function') {
            try { rv = res.clone().text().then(t => responseOutcomes(t), () => null); } catch (e) { rv = null; }
          }
          endRec(rec, res && typeof res.status === 'number' ? res.status : 0, rv);
        }, () => endRec(rec, 0));
      }
      return p;
    };
    st.ours.add(f1);
    G.fetch = f1;
  }

  function hookWebSocket() {
    const WP = G.WebSocket && G.WebSocket.prototype;
    if (!WP || typeof WP.send !== 'function' || st.ours.has(WP.send)) return;
    const s0 = WP.send;
    const s1 = function (data) {
      try { st.diag.ws_sends++; markSent(this.url, data); } catch (e) { /* ignore */ }
      return s0.apply(this, arguments);
    };
    st.ours.add(s1);
    WP.send = s1;
  }

  function hookBeacon() {
    const nav = G.navigator;
    if (!nav || typeof nav.sendBeacon !== 'function' || st.ours.has(nav.sendBeacon)) return;
    const b0 = nav.sendBeacon;
    const b1 = function (url, data) {
      try { st.diag.beacons++; markSent(url, data); } catch (e) { /* ignore */ }
      return b0.apply(this, arguments);
    };
    st.ours.add(b1);
    try { nav.sendBeacon = b1; } catch (e) { /* read-only navigator */ }
  }

  function hookPerf() {
    if (typeof G.PerformanceObserver !== 'function') return;
    try {
      const po = new G.PerformanceObserver(list => {
        try { st.diag.perf_entries += list.getEntries().length; } catch (e) { /* ignore */ }
      });
      po.observe({ type: 'resource', buffered: false });
    } catch (e) { /* unsupported entry type */ }
  }

  function install() {
    if (st.installed) return false;
    st.installed = true;
    hookXHR();
    hookFetch();
    hookWebSocket();
    hookBeacon();
    hookPerf();
    return true;
  }

  /* ---- the wait ---- */
  /* Which Lt client this page runs: origin and path of every script src (queries dropped,
   * so signed or cache-busting parameters do not change it), sorted and hashed (FNV-1a). */
  function clientSig() {
    try {
      const d = G.document;
      if (!d) return null;
      let list = [];
      if (d.scripts && typeof d.scripts.length === 'number') list = Array.from(d.scripts);
      else if (typeof d.querySelectorAll === 'function') list = Array.from(d.querySelectorAll('script[src]') || []);
      const srcs = [];
      for (const s of list) {
        const src = s && (s.src || (typeof s.getAttribute === 'function' ? s.getAttribute('src') : ''));
        const u = src ? parse(src) : null;
        if (u) srcs.push(u.origin + u.pathname);
      }
      if (!srcs.length) return null;
      srcs.sort();
      const text = srcs.join('\n');
      let h = 0x811c9dc5;
      for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
      return 'c1-' + srcs.length + '-' + h.toString(16).padStart(8, '0');
    } catch (e) { return null; }
  }

  /* {cal, stale}: the usable save calibration, or null with the reason it is not usable. */
  function normCal(c, sig) {
    if (!c || typeof c !== 'object') return { cal: null, stale: false };
    if (c.save && typeof c.save === 'object') c = c.save;
    if (c.version != null && c.version !== 1) return { cal: null, stale: true };
    if (c.client_sig != null && c.client_sig !== sig) return { cal: null, stale: true };
    const method = c.method ? String(c.method).toUpperCase() : null;
    const tpl = c.path_template ? String(c.path_template) : null;
    const source = c.source === 'model' ? 'model' : 'net';
    if (!(method && tpl) || NON_ACK[method]) return { cal: null, stale: true };
    return { cal: { source, method, tpl }, stale: false };
  }
  const netAllowed = (a, cal) => !cal || !!(cal.method && cal.tpl && a.method === cal.method && a.tpl === cal.tpl);

  function modelPath(e, cal) {
    function outcome(ms) {
      const ops = ms.ops.filter(op => op.rec.done && netAllowed(op.rec, cal));
      const final = ops.filter(op => requestOutcome(op.rec, e.key) !== 'accepted' && (op.synced || op.failed));
      if (!final.length) return { state: ms.sent ? 'sent' : 'pending' };
      const op = final.reduce((a, b) => b.rec.t1 >= a.rec.t1 ? b : a);
      const r = op.rec;
      return op.failed || requestOutcome(r, e.key) === 'failed' ? { state: 'failed' } :
        { state: 'saved', t: r.t1, method: r.method, tpl: r.tpl };
    }
    let parentSaved = null;
    const written = [];
    for (const s of e.subs.values()) {
      const result = outcome(s.ms);
      if (s.role === 'parent') parentSaved = result;
      else written.push(result);
    }
    if (written.some(ms => ms.state === 'failed') || (parentSaved && parentSaved.state === 'failed')) return { state: 'failed' };
    if (parentSaved && parentSaved.state === 'saved') return parentSaved;
    if (written.length && written.every(ms => ms.state === 'saved')) {
      return written.reduce((a, ms) => ms.t > a.t ? ms : a);
    }
    const sent = written.some(ms => ms.state === 'sent') || (parentSaved && parentSaved.state === 'sent');
    return { state: sent ? 'sent' : 'pending' };
  }

  function keyState(e, cal) {
    if (!e) return { state: 'pending' };
    const mp = modelPath(e, cal);
    if (mp.state === 'saved') return { state: 'saved', by: 'model', t: mp.t };
    if (mp.state === 'failed') return { state: 'failed', by: 'model' };
    const acks = e.weak ? [] : e.net.filter(a => a.seq > e.seq && netAllowed(a, cal));
    const final = acks.filter(a => a.outcome !== 'accepted');
    if (final.length) {
      const last = final.reduce((a, b) => (b.t1 >= a.t1 ? b : a));
      return last.outcome === 'ok' ? { state: 'saved', by: 'net', t: last.t1 } : { state: 'failed', by: 'net' };
    }
    return { state: (mp.state === 'sent' || acks.length || e.wsSent) ? 'sent' : 'pending' };
  }

  function quietRef(cal) {
    let end = null;
    for (const r of st.reqs) {
      if (r.seq <= st.session.startSeq) continue;
      if (cal && cal.method && cal.tpl && !(r.method === cal.method && r.tpl === cal.tpl)) continue;
      if (!r.ended) return { busy: true };
      if (end === null || r.t1 > end) end = r.t1;
    }
    return { busy: false, end };
  }

  /* Acknowledgements by source, before the calibration filter: calibration records them. */
  function ackList(entries) {
    const seen = new Set();
    const out = [];
    let first = null;
    for (const e of entries) {
      if (!e) continue;
      const mp = modelPath(e);
      if (mp.state === 'saved') {
        if (!first || mp.t < first.t) first = { source: 'model', method: mp.method, path_template: mp.tpl, t: mp.t };
        const id = 'model ' + mp.method + ' ' + mp.tpl;
        if (!seen.has(id)) { seen.add(id); out.push({ source: 'model', method: mp.method, path_template: mp.tpl }); }
      }
      if (e.weak) continue;
      for (const a of e.net) {
        if (a.outcome !== 'ok' || a.seq <= e.seq) continue;
        if (!first || a.t1 < first.t) first = { source: 'net', method: a.method, path_template: a.tpl, t: a.t1 };
        const id = 'net ' + a.method + ' ' + a.tpl;
        if (!seen.has(id)) { seen.add(id); out.push({ source: 'net', method: a.method, path_template: a.tpl }); }
      }
    }
    return { first, list: out.slice(0, 5) };
  }

  async function waitForSave(keys, opts) {
    install();
    const o = opts || {};
    const quiet = num(o.quiet_ms, 300);
    const maxMs = num(o.max_ms, 20000);
    const fallbackMs = num(o.fallback_ms, 4000);
    const sw = swControlled();
    const sig = clientSig();
    const nc = normCal(o.calibration, sig);
    const cal = sw ? null : nc.cal;
    const requested = new Set((keys || []).map(String));
    // Include omitted outstanding keys in diagnostics; an empty/subset call cannot clear them.
    const ks = Array.from(requested);
    const includeOutstanding = () => {
      for (const e of st.entries.values()) {
        if (keyState(e, cal).state !== 'saved' && !ks.includes(e.key)) ks.push(e.key);
      }
    };
    includeOutstanding();
    const ck = clock();
    const tStart = ck.now();
    let wasHidden = hidden();
    let tPrev = tStart;

    const finish = (signal, states) => {
      const t = ck.now();
      const ref = st.lastWrite == null ? tStart : st.lastWrite;
      const entries = ks.map(k => st.entries.get(k));
      const saved = states.filter(s => s.st.state === 'saved');
      const failed = states.filter(s => s.st.state === 'failed');
      const pending = states.filter(s => s.st.state === 'pending' || s.st.state === 'sent');
      const acks = ackList(entries);
      st.session.open = false;
      const d = st.diag;
      const confirmed = signal === 'model' || signal === 'net';
      return {
        save_signal: signal,
        signal,
        state: confirmed ? 'ok' : signal,
        save_confirmed: confirmed,
        waited_ms: Math.max(0, Math.round(t - ref)),
        keys_total: ks.length,
        keys_saved: saved.length,
        keys_pending: pending.length,
        save_errors: failed.length,
        jq: st.session.jqBase + '/' + st.session.jqMax,
        calibrated: !!cal,
        calibration_stale: nc.stale,
        client_sig: sig,
        sw_controlled: sw,
        hidden_during_wait: wasHidden,
        ack_model: saved.filter(s => s.st.by === 'model').length,
        ack_net: saved.filter(s => s.st.by === 'net').length,
        ack_source: acks.first ? acks.first.source : null,
        ack_method: acks.first ? acks.first.method : null,
        ack_path_template: acks.first ? acks.first.path_template : null,
        acks: acks.list,
        keys_weak: entries.filter(e => e && e.weak).length,
        pending_keys: pending.slice(0, 5).map(s => s.k),
        failed_keys: failed.slice(0, 5).map(s => s.k),
        net_diag: 'req=' + d.requests + ' nonget=' + d.non_get + ' matched=' + d.matched +
          ' perf=' + d.perf_entries + ' ws=' + d.ws_sends + ' beacon=' + d.beacons +
          ' model_unidentified=' + d.model_unidentified + ' shared_model=' + d.shared_model +
          ' multi_key=' + d.multi_key
      };
    };

    if (!ks.length && st.lastWrite == null) return finish('none', []);
    for (;;) {
      let t = ck.now();
      if (t < tPrev) t = tPrev;
      tPrev = t;
      if (hidden()) wasHidden = true;
      const jq = jqActive();
      if (jq > st.session.jqMax) st.session.jqMax = jq;
      const ref = st.lastWrite == null ? tStart : st.lastWrite;
      const elapsed = t - ref;
      includeOutstanding();
      const states = ks.map(k => ({ k, st: keyState(st.entries.get(k), cal) }));
      if (states.some(s => s.st.state === 'failed')) return finish('error', states);
      if (cal) {
        if (requested.size > 0 && states.length > 0 && ks.every(k => requested.has(k)) && states.every(s => s.st.state === 'saved')) {
          const q = quietRef(cal);
          if (!q.busy) {
            let since = ref;
            if (q.end !== null && q.end > since) since = q.end;
            for (const s of states) if (s.st.t != null && s.st.t > since) since = s.st.t;
            if (t - since >= quiet) {
              const allModel = states.length > 0 && states.every(s => s.st.by === 'model');
              return finish(allModel ? 'model' : 'net', states);
            }
          }
        }
      } else if (elapsed >= fallbackMs && jq <= st.session.jqBase) {
        return finish(wasHidden ? 'unconfirmed' : 'fallback', states);
      }
      if (elapsed >= maxMs) return finish('unconfirmed', states);
      await ck.sleep(POLL_MS);
    }
  }

  function pendingKeys() {
    const out = [];
    for (const e of st.entries.values()) {
      const s = keyState(e, null).state;
      if (s === 'pending' || s === 'sent') out.push(e.key);
    }
    return out;
  }

  function state() {
    return {
      installed: st.installed, entries: st.entries.size, pending: pendingKeys().length,
      last_write: st.lastWrite, session_open: st.session.open, diag: Object.assign({}, st.diag)
    };
  }

  const api = Object.freeze({
    version: 1, install, expect, waitForSave, pendingKeys, state, pathTemplate, clientSig,
    lastWrite: () => st.lastWrite
  });
  Object.defineProperty(G, 'LtNet', { value: api, enumerable: false, configurable: false, writable: false });
})(typeof globalThis !== 'undefined' ? globalThis : this);
