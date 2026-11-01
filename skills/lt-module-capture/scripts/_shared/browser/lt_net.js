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
 *                                     opts.ids {panel, data, uuid}: the panel's key, dataKey
 *                                     and uuid, for the run-data adapter below (optional).
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
 * and absent outcomes remain pending. A top-level failure in the reply ({"success": false}:
 * Lt answers HTTP 200 with it and keeps nothing) fails every key the request named. A single
 * key without a per-key outcome saves only when the reply carries no failure marker at any
 * depth. On every path (run-data below too), a failure marker inside an item (an object with
 * an identity field) fails that item and leaves sibling items alone; a failure marker outside
 * every item, a failed plain value that names no key of the request, or a reply past 32 levels
 * or 50,000 values (read only in part, `resp_truncated` in net_diag) confirms no key of the
 * request. WebSocket and beacon sends only mark keys sent. This generic matcher never
 * acknowledges a key shorter than 6 characters.
 *
 * Run-data adapter: Lt saves every panel of a run through one
 * PUT /i/<instance>/v/2.0/api/student/courses/<course>/runs/<run>/data. A request counts as
 * this endpoint only when its instance, course and run are the page's own. Its body is parsed
 * as JSON and each expected key is identified by a typed identity field holding exactly one of
 * the ids expect() was given: panelKey or panel_key (the panel key), dataKey or data_key (the
 * dataKey, panelKey - 1 on Lt, so it is never read as a panel key), uuid, panelUuid or
 * panel_uuid, or a property name that is the uuid itself. Values elsewhere, substrings and
 * other property names identify nothing, so 3- and 4-character panel keys can be acknowledged
 * here. A body that is not JSON, or names no expected key this way, leaves its keys pending:
 * the call ends on the fallback rule and a fresh-load verify decides. The generic matcher is
 * not applied to this endpoint. A bare 2xx acknowledges nothing; a key is saved by either
 *   - an explicit per-key outcome in the reply: success for one of the key's typed ids, or
 *   - the atomic-snapshot rule: the PUT carries the run's panel data as one document, so a
 *     reply whose top-level object reads success: true (or ok: true, or status ok, saved or
 *     success), with no failure marker anywhere, saves every key the body identifies. A
 *     failure marker at any depth ({success: true, data: {success: false}}, a nested status
 *     failed, a non-empty errors list) leaves every key pending.
 * A top-level failure, or an explicit failure for a key, fails the key; 202 leaves it pending.
 *
 * Calibration binding: a record that carries `client_sig` applies only while the page runs the
 * same Lt build: clientSig() hashes the origin, path and query of each script element whose
 * resource timing entry started before DOMContentLoaded (the HTML's own bundles). Chunks a page
 * loads later never count. Without navigation and resource timing the page has no signature, and a
 * signed record does not apply. A record with another signature, a missing write endpoint, or
 * a record `version` other than 1, is stale: the wait runs the fallback rule and the receipt
 * says `calibration_stale: true`. scriptsSig() hashes every script element, page chunks
 * included: a diagnostic, never a binding.
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
 * A fallback result carries `save_diag`: up to 20 request rows by method, templated path and
 * status, with `model_sync` true when a written model's Backbone sync answered a request of
 * that row. A run-data row adds `run_data`, a 'name=count ...' string of counts only: bodies by
 * shape, expected keys, keys identified, keys seen by kind of typed field, as a property name
 * or as a plain value, and replies by shape (per-key, success, failure, bare).
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
  /* Reply inspection limits: a reply deeper or larger than this confirms no key. */
  const RESP_DEPTH = 32;
  const RESP_VALUES = 50000;

  const st = {
    installed: false, seq: 0, entries: new Map(), lastWrite: null,
    session: { open: false, startSeq: 0, jqBase: 0, jqMax: 0 },
    reqs: [], transports: new WeakMap(), ours: new WeakSet(), subCount: new WeakMap(),
    diag: { requests: 0, non_get: 0, matched: 0, perf_entries: 0, ws_sends: 0, beacons: 0, model_unidentified: 0, shared_model: 0, multi_key: 0, run_data: 0, resp_truncated: 0 }
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
  const WHOLE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const scalar = v => typeof v === 'string' || (typeof v === 'number' && isFinite(v));

  /* ---- the run-data adapter ---- */
  const RUN_DATA = /^\/i\/([^/]+)\/v\/2\.0\/api\/student\/courses\/(\d+)\/runs\/(\d+)\/data\/?$/;
  const RUN_PAGE = /^\/i\/([^/]+)\/student\/courses\/(\d+)\/runs\/(\d+)(?:\/|$)/;
  /* Typed identity fields by kind. A value counts only under its own kind's names. */
  const ID_FIELDS = { panelKey: 'panel', panel_key: 'panel', dataKey: 'data', data_key: 'data',
    uuid: 'uuid', panelUuid: 'uuid', panel_uuid: 'uuid' };
  const ID_KINDS = ['panel', 'data', 'uuid'];
  const fieldKind = k => (Object.prototype.hasOwnProperty.call(ID_FIELDS, k) ? ID_FIELDS[k] : null);
  /* The current run's PUT .../runs/<run>/data: same origin, and the page's instance, course
   * and run. Another run's data, or any other method, goes to the generic matcher. */
  function isRunData(rec) {
    if (!rec.same || rec.method !== 'PUT' || !rec.url) return false;
    const m = RUN_DATA.exec(rec.url.pathname);
    const h = here();
    const p = h ? RUN_PAGE.exec(h.pathname) : null;
    return !!(m && p && m[1] === p[1] && m[2] === p[2] && m[3] === p[3]);
  }
  /* What a parsed run-data body says, in memory only: `typed` holds 'kind:value' for each
   * typed identity field with a scalar value and 'uuid:<name>' for each property name that is
   * a whole UUID; `props` and `values` (every property name and scalar value) feed the
   * diagnostic counts and nothing else. */
  function bodyIdentity(v) {
    const typed = new Set(), props = new Set(), values = new Set();
    let n = 0;
    const walk = (x, depth) => {
      if (x == null || depth > 8 || ++n > MAX_VALUES) return;
      if (scalar(x)) { values.add(String(x)); return; }
      if (Array.isArray(x)) { for (const y of x) walk(y, depth + 1); return; }
      if (typeof x !== 'object') return;
      for (const [k, y] of Object.entries(x)) {
        props.add(k);
        if (WHOLE_UUID.test(k)) typed.add('uuid:' + k);
        const kind = fieldKind(k);
        if (kind && scalar(y)) typed.add(kind + ':' + String(y));
        walk(y, depth + 1);
      }
    };
    walk(v, 0);
    return { typed, props, values };
  }

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
  /* A body as text: a string, a Promise of one (Blob, Request), or null (none, or a form). */
  function bodyText(body) {
    if (body == null) return null;
    if (typeof body === 'string') return body;
    try {
      if (G.URLSearchParams && body instanceof G.URLSearchParams) return body.toString();
      if (G.FormData && body instanceof G.FormData) return null;
      if (typeof ArrayBuffer !== 'undefined' && (body instanceof ArrayBuffer || ArrayBuffer.isView(body))) return decode(body);
      if (typeof body.then === 'function') return body.then(t => (typeof t === 'string' ? t : null), () => null);
      if (typeof body.text === 'function' && typeof body.size === 'number') return body.text().then(t => t, () => null);
    } catch (e) { return null; }
    return null;
  }
  /* The reply's own verdict, from its top-level object: 'failed' (success or ok false, a
   * failure status, an error field), 'saved' (success or ok true, a success status), or null.
   * A per-item `errors` list is not a top-level verdict. Failure wins. */
  function topVerdict(v) {
    if (!v || typeof v !== 'object' || Array.isArray(v)) return null;
    const status = String(v.status || v.outcome || '').toLowerCase();
    if (v.error || v.success === false || v.ok === false || /^(error|failed|invalid)$/.test(status)) return 'failed';
    if (v.success === true || v.ok === true || /^(ok|saved|success)$/.test(status)) return 'saved';
    return null;
  }
  /* An object's own verdict marker: error, success or ok false, or a failure status. */
  const ownMarker = (v, status) => !!v.error || v.success === false || v.ok === false || /^(error|failed|invalid)$/.test(status);
  /* An object's own failure marker: its own verdict marker, or a non-empty errors list or
   * object. An empty errors list marks nothing. */
  function ownFailure(v, status) {
    const errs = v.errors;
    const hasErrs = Array.isArray(errs) ? errs.length > 0
      : (errs && typeof errs === 'object' ? Object.keys(errs).length > 0 : !!errs);
    return ownMarker(v, status) || hasErrs;
  }
  /* Retain only explicit per-item outcomes, never arbitrary response values. Errors win.
   * An item is an object with an identity field (generic uuid, id, key or panel_key, or a typed
   * one). A failure marker anywhere inside an item fails that item and every item around it,
   * whatever the item's own verdict says; sibling items keep their own outcomes.
   * savedTyped and failedTyped hold 'kind:value' for outcomes named by a typed identity field
   * (typedExplicit), which only the run-data adapter reads; top is topVerdict(). anyFailed is
   * true when any object at any depth carries its own failure marker, or any value sits under
   * a failure field, whether or not it names a key. unattributed is true when an object with
   * its own verdict marker, or an object under a failure field, sits inside no item; an errors
   * list is judged by its entries. failedValues holds the plain values listed under a failure
   * field outside any item: each names a key, and requestOutcome() treats one that names no key
   * of the request as unattributed. truncated is true when the reply ran past RESP_DEPTH levels
   * or RESP_VALUES values, so part of it was never read. */
  function responseOutcomes(value) {
    const saved = new Set(), failed = new Set(), savedTyped = new Set(), failedTyped = new Set();
    const failedValues = new Set();
    if (typeof value === 'string') {
      try { value = JSON.parse(value); } catch (e) {
        return { saved, failed, savedTyped, failedTyped, failedValues, explicit: false, typedExplicit: false,
          anyFailed: false, unattributed: false, truncated: false, top: null };
      }
    }
    let explicit = false, typedExplicit = false, anyFailed = false, unattributed = false, truncated = false;
    let count = 0, items = 0;
    /* Returns true when this value or anything under it carries a failure. */
    function visit(v, context, depth) {
      if (v == null) return false;
      if (depth > RESP_DEPTH || ++count > RESP_VALUES) { truncated = true; return false; }
      if (Array.isArray(v)) {
        let bad = false;
        for (const item of v) bad = visit(item, context, depth + 1) || bad;
        return bad;
      }
      if (typeof v === 'string' || typeof v === 'number') {
        if (context) (context === 'failed' ? failed : saved).add(String(v));
        if (context !== 'failed') return false;
        anyFailed = true;
        if (!items) failedValues.add(String(v));
        return true;
      }
      if (typeof v !== 'object') return false;
      const status = String(v.status || v.outcome || '').toLowerCase();
      let result = context;
      if (v.error || v.errors || v.success === false || v.ok === false || /^(error|failed|invalid)$/.test(status)) result = 'failed';
      else if (v.success === true || v.ok === true || /^(ok|saved|success)$/.test(status)) result = 'saved';
      const generic = ['uuid', 'id', 'key', 'panel_key'].filter(f => typeof v[f] === 'string' || typeof v[f] === 'number');
      const typed = Object.keys(ID_FIELDS).filter(f => scalar(v[f]));
      const item = generic.length > 0 || typed.length > 0;
      if (item) items++;
      let bad = false;
      if (context === 'failed' || ownFailure(v, status)) {
        anyFailed = true;
        bad = true;
        if (!items && (context === 'failed' || ownMarker(v, status))) unattributed = true;
      }
      for (const [field, child] of Object.entries(v)) {
        let next = null;
        if (/^(saved|successes|successful)$/.test(field)) { next = 'saved'; explicit = true; }
        if (/^(errors|failed|failures)$/.test(field)) { next = 'failed'; explicit = true; }
        // Identity fields are recorded below. Arbitrary payload text never inherits success.
        if (next || (child && typeof child === 'object')) bad = visit(child, next, depth + 1) || bad;
      }
      if (item) {
        items--;
        const outcome = bad ? 'failed' : result;
        if (outcome) {
          for (const field of generic) {
            explicit = true;
            (outcome === 'failed' ? failed : saved).add(String(v[field]));
          }
          for (const field of typed) {
            typedExplicit = true;
            (outcome === 'failed' ? failedTyped : savedTyped).add(ID_FIELDS[field] + ':' + String(v[field]));
          }
        }
      }
      return bad;
    }
    visit(value, null, 0);
    for (const k of failed) saved.delete(k);
    for (const k of failedTyped) savedTyped.delete(k);
    return { saved, failed, savedTyped, failedTyped, failedValues, explicit, typedExplicit, anyFailed, unattributed,
      truncated, top: topVerdict(value) };
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
      const op = options && typeof options === 'object' ? ms.opts.get(options) : null;
      // save_diag: a written model's sync marks the request row it answered, and no other.
      if (ok && op && role !== 'parent') op.rec.modelSync = true;
      if ((st.subCount.get(m) || 0) > 1) { st.diag.shared_model++; return; }
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

  /* A key's typed ids, 'kind:value': opts.ids {panel, data, uuid} with scalar values, and the
   * key itself as a uuid when it is one. */
  function idsOf(k, given) {
    const out = new Set();
    const g = given && typeof given === 'object' ? given : {};
    for (const kind of ID_KINDS) if (scalar(g[kind]) && String(g[kind]) !== '') out.add(kind + ':' + String(g[kind]));
    if (WHOLE_UUID.test(k)) out.add('uuid:' + k);
    return Array.from(out);
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
        net: [], wsSent: false, subs: new Map(), contains: [], ids: [] };
      st.entries.set(k, e);
    }
    e.seq = st.seq;
    e.tWrite = t;
    e.ids = idsOf(k, opts && opts.ids);
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

  /* The run-data adapter's match: typed identity only, weak keys included. rec.rd keeps the
   * diagnostic counts; the parsed body is dropped when this returns. */
  function applyRunData(rec, text, cands) {
    const t = typeof text === 'string' ? text.trim() : '';
    let parsed, body = t ? 'unparsed' : 'none';
    if (t && (t[0] === '{' || t[0] === '[')) {
      try { parsed = JSON.parse(t); body = 'json'; } catch (e) { /* stays unparsed */ }
    }
    const d = { body, keys_expected: 0, keys_identified: 0, as_panel_field: 0, as_data_field: 0,
      as_uuid_field: 0, as_prop: 0, as_value: 0 };
    const idn = body === 'json' ? bodyIdentity(parsed) : null;
    for (const e of cands) {
      if (st.entries.get(e.key) !== e || e.seq >= rec.seq) continue;
      d.keys_expected++;
      if (!idn) continue;
      const raw = [e.key].concat(e.ids.map(id => id.slice(id.indexOf(':') + 1)));
      if (raw.some(x => idn.props.has(x))) d.as_prop++;
      if (raw.some(x => idn.values.has(x))) d.as_value++;
      for (const kind of ID_KINDS) if (e.ids.some(id => id.startsWith(kind + ':') && idn.typed.has(id))) d['as_' + kind + '_field']++;
      if (!e.ids.some(id => idn.typed.has(id))) continue;
      if (e.contains.length && !e.contains.every(c => Array.from(idn.values).some(v => v.indexOf(c) >= 0))) continue;
      d.keys_identified++;
      rec.keys.push({ e, gen: e.seq });
    }
    if (rec.keys.length) st.diag.matched++;
    rec.rd = d;
    rec.matched = true;
    settle(rec);
  }

  function startRec(rec, body) {
    st.reqs.push(rec);
    if (st.reqs.length > KEEP_REQS) st.reqs.shift();
    if (!counted(rec)) { rec.matched = true; return rec; }
    st.diag.non_get++;
    if (isRunData(rec)) {
      rec.adapter = 'run-data';
      st.diag.run_data++;
      const all = [];
      for (const e of st.entries.values()) if (e.seq < rec.seq) all.push(e);
      const text = bodyText(body);
      if (text && typeof text.then === 'function') text.then(x => applyRunData(rec, x, all), () => applyRunData(rec, null, all));
      else applyRunData(rec, text, all);
      return rec;
    }
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
    if (rec.resp && rec.resp.truncated) st.diag.resp_truncated++;
    for (const { e, gen } of rec.keys) {
      if (st.entries.get(e.key) !== e || e.seq !== gen) continue;
      e.net.push({ outcome: requestOutcome(rec, e), t1: rec.t1, method: rec.method, tpl: rec.tpl, seq: rec.seq,
        typed: rec.adapter === 'run-data' });
    }
  }
  /* A plain value listed as failed that names no key of the request: a failure no key owns. */
  const keyValues = e => [e.key].concat(e.ids.map(id => id.slice(id.indexOf(':') + 1)));
  const strayFailure = (rec, r) => Array.from(r.failedValues || [])
    .some(v => !rec.keys.some(({ e }) => keyValues(e).indexOf(v) >= 0));
  /* One key's outcome on one ended request. A run-data request (rec.adapter) never accepts a
   * bare 2xx: it needs a per-key success for one of the key's typed ids, or the atomic-snapshot
   * rule (top-level success and no failure marker at any depth). On every path a failure marker
   * no item owns, or a reply read only in part, leaves the key pending. */
  function requestOutcome(rec, e) {
    const s = rec.status, r = rec.resp;
    if (!(s >= 200 && s < 300)) return 'failed';
    if (r && (r.top === 'failed' || r.failed.has(e.key) || e.ids.some(id => r.failedTyped.has(id)))) return 'failed';
    if (s === 202) return 'accepted';
    if (r && (r.unattributed || r.truncated || strayFailure(rec, r))) return 'accepted';
    if (rec.adapter === 'run-data') {
      if (r && (r.explicit || r.typedExplicit)) {
        const named = e.ids.some(id => r.savedTyped.has(id) || (id.startsWith('uuid:') && r.saved.has(id.slice(5))));
        return named ? 'ok' : 'accepted';
      }
      return r && r.top === 'saved' && !r.anyFailed && !r.failed.size && !r.failedTyped.size ? 'ok' : 'accepted';
    }
    if (rec.keys.length > 1 || (r && r.explicit)) {
      return r && r.saved.has(e.key) ? 'ok' : 'accepted';
    }
    /* A single key with no per-key outcome: any failure marker in the reply leaves it pending. */
    return r && r.anyFailed ? 'accepted' : 'ok';
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
  /* Script element URLs in page order: origin and path, plus the query string when `query` is
   * set. clientSig() keeps the query, so /core.js?v=1 and ?v=2 sign as two builds; a signed or
   * cache-busting query then drops the record, the safe direction. scriptsSig() drops it. */
  function scriptPaths(query) {
    const d = G.document;
    if (!d) return [];
    let list = [];
    if (d.scripts && typeof d.scripts.length === 'number') list = Array.from(d.scripts);
    else if (typeof d.querySelectorAll === 'function') list = Array.from(d.querySelectorAll('script[src]') || []);
    const srcs = [];
    for (const s of list) {
      const src = s && (s.src || (typeof s.getAttribute === 'function' ? s.getAttribute('src') : ''));
      const u = src ? parse(src) : null;
      if (u) srcs.push(u.origin + u.pathname + (query ? u.search : ''));
    }
    return srcs;
  }
  /* prefix + count + FNV-1a of the sorted list; null for an empty list. */
  function sigOf(prefix, srcs) {
    if (!srcs.length) return null;
    srcs.sort();
    const text = srcs.join('\n');
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return prefix + srcs.length + '-' + h.toString(16).padStart(8, '0');
  }
  /* Which Lt build this page runs: the script elements the document loaded with, those whose
   * resource timing entry started before domContentLoadedEventStart, by origin, path and query.
   * Lazily loaded page chunks start later and never count. Unique URLs, 'c2-'. null without
   * navigation or resource timing, before DOMContentLoaded, or with no such script: no build
   * marker. A page chunk that starts before DOMContentLoaded still counts, so two pages of one
   * build can sign differently; that only drops a calibration. */
  function clientSig() {
    try {
      const P = G.performance;
      if (!P || typeof P.getEntriesByType !== 'function') return null;
      const nav = (P.getEntriesByType('navigation') || [])[0];
      const dcl = nav ? nav.domContentLoadedEventStart : null;
      if (!(typeof dcl === 'number' && dcl > 0)) return null;
      const early = new Set();
      for (const r of P.getEntriesByType('resource') || []) {
        if (!r || typeof r.startTime !== 'number' || !(r.startTime < dcl)) continue;
        const u = parse(r.name);
        if (u) early.add(u.origin + u.pathname + u.search);
      }
      return sigOf('c2-', Array.from(new Set(scriptPaths(true).filter(s => early.has(s)))));
    } catch (e) { return null; }
  }
  /* Every script element, page chunks included ('c1-', the client_sig of 0.2.3 and earlier):
   * it changes between pages of one build. A receipt diagnostic, never a calibration binding. */
  function scriptsSig() {
    try { return sigOf('c1-', scriptPaths()); } catch (e) { return null; }
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
      const final = ops.filter(op => requestOutcome(op.rec, e) !== 'accepted' && (op.synced || op.failed));
      if (!final.length) return { state: ms.sent ? 'sent' : 'pending' };
      const op = final.reduce((a, b) => b.rec.t1 >= a.rec.t1 ? b : a);
      const r = op.rec;
      return op.failed || requestOutcome(r, e) === 'failed' ? { state: 'failed' } :
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
    // A weak key counts only acknowledgements the run-data adapter made by typed identity.
    const acks = e.net.filter(a => (a.typed || !e.weak) && a.seq > e.seq && netAllowed(a, cal));
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
      if (r.seq <= st.session.startSeq || !counted(r)) continue;
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
      for (const a of e.net) {
        if (a.outcome !== 'ok' || a.seq <= e.seq || (e.weak && !a.typed)) continue;
        if (!first || a.t1 < first.t) first = { source: 'net', method: a.method, path_template: a.tpl, t: a.t1 };
        const id = 'net ' + a.method + ' ' + a.tpl;
        if (!seen.has(id)) { seen.add(id); out.push({ source: 'net', method: a.method, path_template: a.tpl }); }
      }
    }
    return { first, list: out.slice(0, 5) };
  }

  /* The reply's shape, for run-data rows: per_key, success, failure or bare (none read). */
  const replyShape = r => (!r ? 'bare' : (r.explicit || r.typedExplicit) ? 'per_key' : r.top === 'saved' ? 'success' : r.top === 'failed' ? 'failure' : 'bare');
  const RD_COUNTS = ['keys_expected', 'keys_identified', 'as_panel_field', 'as_data_field', 'as_uuid_field', 'as_prop', 'as_value'];
  /* Rows stay 4 levels deep in the receipt (one browser tool truncates deeper), so a run-data
   * row's counts are one 'name=count ...' string, as net_diag is. */
  function saveDiagnostics() {
    const counts = new Map();
    const rows = new Map();
    for (const request of st.reqs) {
      if (request.seq <= st.session.startSeq) continue;
      const path = request.url?.pathname || '';
      const template = path.split('/').map(segment => /^(?:[0-9a-f]+|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i.test(segment) ? ':id' : segment).join('/');
      const key = request.method + ' ' + template + ' ' + request.status + (request.rd ? ' run-data' : '');
      if (!rows.has(key) && rows.size < 20) {
        const row = {method: request.method, path_template: template, status: request.status, count: 0, model_sync: false};
        if (request.rd) {
          const rd = {requests: 0, body_json: 0, body_unparsed: 0, body_none: 0};
          for (const c of RD_COUNTS) rd[c] = 0;
          Object.assign(rd, {reply_per_key: 0, reply_success: 0, reply_failure: 0, reply_bare: 0});
          counts.set(row, rd);
        }
        rows.set(key, row);
      }
      const row = rows.get(key);
      if (!row) continue;
      row.count++;
      if (request.modelSync) row.model_sync = true;
      const rd = request.rd ? counts.get(row) : null;
      if (rd) {
        rd.requests++;
        rd['body_' + request.rd.body]++;
        rd['reply_' + replyShape(request.resp)]++;
        for (const c of RD_COUNTS) rd[c] += request.rd[c];
      }
    }
    for (const [row, rd] of counts) row.run_data = Object.entries(rd).map(([k, v]) => k + '=' + v).join(' ');
    return Array.from(rows.values());
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
        ...(signal === 'fallback' ? {save_diag: saveDiagnostics()} : {}),
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
          ' multi_key=' + d.multi_key + ' run_data=' + d.run_data + ' resp_truncated=' + d.resp_truncated
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
    version: 1, install, expect, waitForSave, pendingKeys, state, pathTemplate, clientSig, scriptsSig,
    lastWrite: () => st.lastWrite
  });
  Object.defineProperty(G, 'LtNet', { value: api, enumerable: false, configurable: false, writable: false });
})(typeof globalThis !== 'undefined' ? globalThis : this);
