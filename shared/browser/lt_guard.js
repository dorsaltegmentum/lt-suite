// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* lt_guard.js: never-submit guards for an Lt run page (global `LtGuard`).
 *
 * ltcore/ltjs.py prepends this file to every rendered page script, and renders it alone
 * (shared/browser/guard.js) for the run right after each navigation. It installs once per
 * document and never removes itself; only a reload does. Raw browser tools used between
 * scripts therefore still hit it.
 *
 * What it does:
 *  - HTMLFormElement.prototype.submit and .requestSubmit throw Error('lt-suite never-submit'),
 *    and a capture-phase `submit` listener on window cancels native form submission (Enter in
 *    a field, a submit button).
 *  - The four commit mutators on Lt's Knockout `$root` (commitClicked, setCheckpointCommitState,
 *    removeCommittedCheckpoints, removePagesAfterUncommittedCheckpoints) become non-configurable
 *    accessors that return a throwing function, on `$root` and on every prototype that defines
 *    them. Lt's later assignments are ignored and counted. Where an accessor cannot be defined,
 *    a plain throwing function is assigned and re-checked every second and on every run.
 *  - Capture-phase listeners on window cancel pointer, mouse, touch and click events, and
 *    Enter or Space key events, aimed at a control whose label reads like a commit (submit,
 *    commit, finish, finalise, hand in, complete, checkpoint, final answer) or whose Knockout
 *    binding names a commit mutator. They stop handlers Lt bound before the guard ran.
 *  - window.confirm answers No (returns false) and counts a trip.
 *  - A Navigation API `navigate` listener cancels navigation to /run/complete, checkpoint
 *    routes, preview URLs, report pages, checkpoint pages, flow pages at or past
 *    `stop_before_page` or in `never_navigated`, and any move that would pass a checkpoint
 *    page or the stop on the way. Without a plan (capture, setup) it cancels every page
 *    change. Downloads of blob: and data: URLs pass. Policies only add up: a later script
 *    cannot relax an earlier one in the same document.
 *  - `LtGuard.run()` refuses to start a script on a forbidden route, on a painted flow page the
 *    plan forbids, or when a wrapper could not be installed, and adds `guard_trips` to the
 *    receipt.
 *
 * Limit: these guards contain accidents in lt-suite's own scripts and in raw tool calls made
 * in the same document. Code that kept a reference to a mutator before the guard installed,
 * code in another realm (an iframe's HTMLFormElement), direct HTTP requests and uncancelable
 * history traversals are out of their reach. The hard controls sit outside the page: the agent
 * runs only rendered lt-suite scripts, and the browser server exposes no raw input tools.
 *
 * The URL rules match `ltjs.py classify()`; tests/browser/test_ltjs.py runs both over one URL
 * table. Every blocked action counts as a trip.
 */
(function (G) {
  'use strict';
  if (!G || G.LtGuard) return;

  const ERR = 'lt-suite never-submit';
  const MUTATORS = ['commitClicked', 'setCheckpointCommitState',
    'removeCommittedCheckpoints', 'removePagesAfterUncommittedCheckpoints'];
  const RUN_RE = /^\/i\/([^/]+)\/student\/courses\/(\d+)\/runs\/(\d+)\/run\/(.*)$/;
  const PAGE_RE = /^page\/([1-9]\d*)$/;
  const RETRY_MS = 250;
  const RETRY_MAX = 40;
  const WATCH_MS = 1000;
  const COMMIT_LABEL = /\b(submit|commit|finish|finali[sz]e|hand\s*in|complete|checkpoint|final\s+answer)\b/i;
  const ACTION_EVENTS = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click', 'auxclick',
    'dblclick', 'touchstart', 'touchend', 'keydown', 'keypress', 'keyup'];
  const ACTION_KEYS = { Enter: 1, NumpadEnter: 1, ' ': 1, Spacebar: 1, Space: 1 };

  const st = {
    installed: false, trips: 0, last: null, log: [], rewraps: 0, rebinds_blocked: 0,
    active: null, token: null, policies: [], mutators: {}, form: 'absent', submit_listener: false,
    events: 'absent', confirm: 'absent',
    navigate: 'absent', uncancelable: 0, retries: 0, watching: false,
    ours: new WeakSet(), guarded: new WeakSet(), roots: new WeakSet(), originals: new WeakSet()
  };
  Object.defineProperty(G, '__ltGuard', {
    value: Object.freeze({ installed: true, version: 2 }), enumerable: false, configurable: false, writable: false
  });

  const trip = reason => {
    st.trips++;
    st.last = String(reason).slice(0, 80);
    st.log.push(st.last);
    if (st.log.length > 20) st.log.shift();
  };
  const thrower = label => {
    const f = function () { trip(label); throw new Error(ERR + ': ' + label); };
    st.ours.add(f);
    return f;
  };

  /* ---- Knockout access, as capture.js reads it: only named members, never a generic walk ---- */
  const U = v => ((G.ko && typeof G.ko.isObservable === 'function' && G.ko.isObservable(v)) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const models = c => { const x = U(c); return x && x.models ? x.models : (Array.isArray(x) ? x : []); };
  const has = (o, k) => !!o && typeof o === 'object' && k in o;
  const rootOf = () => {
    try {
      const el = G.document && G.document.querySelector('div.pageList');
      if (!el || !G.ko || typeof G.ko.contextFor !== 'function') return null;
      const ctx = G.ko.contextFor(el);
      return ctx && ctx.$root ? ctx.$root : null;
    } catch (e) { return null; }
  };
  const flowPages = root => {
    try {
      const lf = root.lessonFlowPages;
      return models(typeof lf === 'function' ? lf.call(root) : lf);
    } catch (e) { return []; }
  };
  const isReport = p => has(p, 'reportInfoPanels') || has(A(p), 'reportInfoPanels');
  /* A checkpoint page carries `committed` and no `allPanels` (capture.js's structural test). */
  const isCheckpoint = p => (has(p, 'committed') || has(A(p), 'committed')) && !has(p, 'allPanels') && !has(A(p), 'allPanels');
  const isCurrent = p => {
    if (!p) return false;
    try { return U(p.current) === true || (has(A(p), 'current') && U(A(p).current) === true); } catch (e) { return false; }
  };

  /* ---- form wrappers ---- */
  function wrapForm() {
    const FP = G.HTMLFormElement && G.HTMLFormElement.prototype;
    if (!FP) { st.form = 'absent'; return; }
    for (const name of ['submit', 'requestSubmit']) {
      const cur = FP[name];
      if (typeof cur !== 'function' || st.ours.has(cur)) continue;
      const again = st.form === 'wrapped' || st.form === 'rewrapped';
      try {
        Object.defineProperty(FP, name, { value: thrower('form:' + name), writable: true, configurable: true, enumerable: false });
      } catch (e) { st.form = 'failed'; continue; }
      if (again) { st.rewraps++; st.form = 'rewrapped'; } else if (st.form !== 'rewrapped') { st.form = 'wrapped'; }
    }
    const target = eventTarget();
    if (!st.submit_listener && target) {
      target.addEventListener('submit', ev => {
        trip('form:submit-event');
        cancel(ev);
      }, true);
      st.submit_listener = true;
    }
  }

  /* window when it takes listeners (its capture phase runs first), else document. */
  function eventTarget() {
    if (typeof G.addEventListener === 'function') return G;
    if (G.document && typeof G.document.addEventListener === 'function') return G.document;
    return null;
  }
  function cancel(ev) {
    try { ev.preventDefault(); } catch (e) { /* not cancelable */ }
    try { ev.stopImmediatePropagation(); } catch (e) { /* older event shape */ }
  }

  /* ---- pointer, click and key events on commit controls ---- */
  const attr = (el, n) => { try { return typeof el.getAttribute === 'function' ? el.getAttribute(n) : null; } catch (e) { return null; } };
  const tagOf = el => String((el && el.tagName) || '').toUpperCase();
  function isAction(el) {
    const tag = tagOf(el);
    if (tag === 'BUTTON' || tag === 'SUMMARY') return true;
    if (tag === 'A' && (attr(el, 'href') != null || attr(el, 'role'))) return true;
    if (tag === 'INPUT') return /^(submit|button|image|reset)$/i.test(String(attr(el, 'type') || el.type || ''));
    if (/^(button|link|menuitem|tab|checkbox|switch)$/i.test(String(attr(el, 'role') || ''))) return true;
    if (/\b(click|event)\s*:/.test(String(attr(el, 'data-bind') || ''))) return true;
    return attr(el, 'onclick') != null || typeof el.onclick === 'function';
  }
  function labelOf(el) {
    const parts = [];
    for (const v of [el.textContent, attr(el, 'aria-label'), el.value, attr(el, 'value'), el.title, attr(el, 'title'), attr(el, 'alt')]) {
      if (typeof v === 'string' && v) parts.push(v);
    }
    return parts.join(' ').replace(/\s+/g, ' ').slice(0, 300);
  }
  /* The reason an event target is a commit control, or null. Walks up from the target: any
   * Knockout binding or bound onclick on the way that names a commit mutator counts.
   * Inspect each actionable ancestor's label, including nested controls. */
  function commitReason(target) {
    let el = target;
    while (el) {
      if (typeof el.getAttribute === 'function' || el.tagName) {
        const bind = String(attr(el, 'data-bind') || '');
        for (const n of MUTATORS) if (bind.indexOf(n) >= 0) return 'binding:' + n;
        if (typeof el.onclick === 'function' && (st.originals.has(el.onclick) ||
            MUTATORS.includes(String(el.onclick.name || '').replace(/^(bound )+/, '')))) return 'onclick:mutator';
        if (isAction(el)) {
          const m = labelOf(el).match(COMMIT_LABEL);
          if (m) return 'label:' + m[1].toLowerCase().replace(/\s+/g, ' ');
        }
      }
      el = el.parentNode;
      if (el === G.document) break;
    }
    return null;
  }
  const now = () => G.__ltClock ? G.__ltClock.now() : Date.now();
  function panelKey(raw) {
    const a = A(U(raw)) || {};
    return a.panelKey == null ? null : String(U(a.panelKey));
  }
  function contextPanel(el) {
    try {
      const c = G.ko.contextFor(el);
      for (const raw of c ? [c.$data, c.$parent, ...(c.$parents || [])] : []) {
        if (panelKey(raw) != null && A(U(raw)).panelState) return raw;
      }
    } catch (e) { /* absent context refuses */ }
    return null;
  }
  function checkButton(target) {
    for (let el = target; el && el !== G.document; el = el.parentNode) {
      if (isAction(el) && /^\s*check\s+answer\s*$/i.test(String(el.textContent || ''))) return el;
    }
    return null;
  }
  function checkable(raw) {
    const state = A(U((A(raw) || {}).panelState)) || {};
    const c = A(U(state.content)) || {}, inp = A(U(state.input)) || {};
    return U(c.singleAttempt) === false && U(c.assessmentType) === 'instantFeedback' && U(inp.locked) !== true;
  }
  function armCheck(page, key) {
    st.token = null;
    const pol = st.active, painted = paintedPage();
    if (!pol || !Array.isArray(pol.check_answer_keys) ||
        !pol.check_answer_keys.some(pair => pair[0] === page && String(pair[1]) === String(key)) ||
        !painted || painted.idx !== page || routeRefusal()) return false;
    const raw = models(U(painted.p.allPanels)).find(p => panelKey(p) === String(key));
    if (!raw || !checkable(raw)) return false;
    st.token = {page, key: String(key), until: now() + 2000, policy: pol};
    return true;
  }
  function commitControl(el) {
    if (!el) return false;
    const nodes = [el];
    if (typeof el.querySelectorAll === 'function') nodes.push(...el.querySelectorAll('*'));
    else { for (let i = 0; i < nodes.length; i++) nodes.push(...(nodes[i].children || [])); }
    return nodes.some(node => !!commitReason(node));
  }
  function onAction(ev) {
    if (!ev || !ev.target) return;
    if (/^key/.test(String(ev.type))) {
      if (!ACTION_KEYS[ev.key] && !ACTION_KEYS[ev.code]) return;
    }
    let why = null;
    try { why = commitReason(ev.target); } catch (e) { why = null; }
    if (!why && ev.type === 'click' && ev.isTrusted !== true) {
      const button = checkButton(ev.target);
      if (button) {
        const token = st.token, raw = contextPanel(button), painted = paintedPage();
        st.token = null;
        if (token && token.policy === st.active && now() < token.until && painted &&
            painted.idx === token.page && panelKey(raw) === token.key && checkable(raw)) return;
        why = 'check-answer-token';
      }
    }
    if (!why) return;
    trip('event:' + ev.type + ':' + why);
    cancel(ev);
  }
  function guardEvents() {
    if (st.events === 'listening') return;
    const target = eventTarget();
    if (!target) { st.events = 'no-event-target'; return; }
    for (const t of ACTION_EVENTS) target.addEventListener(t, onAction, true);
    st.events = 'listening';
  }

  /* ---- native confirm() answers No ---- */
  function guardConfirm() {
    const cur = G.confirm;
    if (typeof cur === 'function' && st.ours.has(cur)) return;
    const f = function () { trip('dialog:confirm'); return false; };
    st.ours.add(f);
    try {
      Object.defineProperty(G, 'confirm', { value: f, writable: false, configurable: false, enumerable: true });
    } catch (e) {
      try { G.confirm = f; } catch (x) { /* read-only */ }
    }
    st.confirm = G.confirm === f ? 'answers-no' : 'failed';
  }

  /* ---- commit mutators ---- */
  /* A non-configurable accessor: reads return the thrower, writes are dropped and counted. */
  function lock(obj, name) {
    const t = thrower('mutator:' + name);
    try {
      Object.defineProperty(obj, name, {
        get: () => t,
        set: () => { st.rebinds_blocked++; },
        enumerable: false, configurable: false
      });
    } catch (e) { return false; }
    let v;
    try { v = obj[name]; } catch (e) { return false; }
    return v === t;
  }
  const guardedDesc = (obj, name) => {
    const d = Object.getOwnPropertyDescriptor(obj, name);
    return !!d && typeof d.get === 'function' && d.configurable === false && st.ours.has(d.get());
  };

  function guardOne(root, name) {
    /* every object on the chain that defines the mutator, root first */
    const owners = [];
    for (let o = root; o && o !== Object.prototype; o = Object.getPrototypeOf(o)) {
      if (Object.prototype.hasOwnProperty.call(o, name)) owners.push(o);
    }
    if (!owners.length) return 'absent';
    if (owners.indexOf(root) < 0) owners.unshift(root);
    let state = 'locked';
    for (const o of owners) {
      if (guardedDesc(o, name)) continue;
      try {
        const v = o[name];
        if (typeof v === 'function' && !st.ours.has(v)) st.originals.add(v);
      } catch (e) { /* a getter that throws */ }
      if (lock(o, name)) continue;
      /* The property is non-configurable or the object is sealed: assign when writable. */
      let cur;
      try { cur = o[name]; } catch (e) { cur = undefined; }
      if (typeof cur === 'function' && st.ours.has(cur)) {
        if (state === 'locked') state = st.mutators[name] === 'rewrapped' ? 'rewrapped' : 'wrapped';
        continue;
      }
      const w = thrower('mutator:' + name);
      try { o[name] = w; } catch (e) { /* reported below */ }
      let after;
      try { after = o[name]; } catch (e) { after = undefined; }
      if (after !== w) return 'failed';
      if (st.mutators[name] === 'wrapped' || st.mutators[name] === 'rewrapped') { st.rewraps++; state = 'rewrapped'; }
      else if (state === 'locked') state = 'wrapped';
    }
    return state;
  }

  function wrapMutators() {
    const root = rootOf();
    if (!root) {
      for (const n of MUTATORS) st.mutators[n] = 'no-root';
      return false;
    }
    st.roots.add(root);
    for (const name of MUTATORS) st.mutators[name] = guardOne(root, name);
    return true;
  }

  /* Lt may not have painted when guard.js runs right after a navigation: retry for 10 s, then
   * keep a 1 s watch for a replaced $root or a rebound mutator. */
  function scheduleRetry() {
    if (typeof G.setTimeout !== 'function') return;
    if (st.retries >= RETRY_MAX) { watch(); return; }
    st.retries++;
    G.setTimeout(() => { if (!wrapMutators()) scheduleRetry(); else watch(); }, RETRY_MS);
  }
  function watch() {
    if (st.watching || typeof G.setTimeout !== 'function') return;
    st.watching = true;
    const tick = () => {
      try { wrapForm(); wrapMutators(); } catch (e) { /* keep watching */ }
      G.setTimeout(tick, WATCH_MS);
    };
    G.setTimeout(tick, WATCH_MS);
  }

  /* ---- URL rules: the twin of ltjs.py classify() ---- */
  const no = (reason, idx) => ({ allowed: false, reason, page_index: idx == null ? null : idx });
  const own = (o, k) => !!o && Object.prototype.hasOwnProperty.call(o, k);
  const isPreview = u => /\/preview(\/|$)/i.test(u.pathname) || u.searchParams.has('preview');

  function classify(href, policy, base) {
    const p = policy || { mode: 'block-all' };
    let u;
    try { u = new G.URL(String(href), base); } catch (e) { return no('bad-url'); }
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return no('bad-scheme');
    if (u.username || u.password) return no('credentials-in-url');
    if (isPreview(u)) return no('preview');
    if (p.origin_conflict) return no('origin-conflict');
    if (p.origin && u.origin !== p.origin) return no('other-origin');
    const m = u.pathname.match(RUN_RE);
    if (!m) return no('not-a-run-page');
    if (p.instance != null && m[1] !== String(p.instance)) return no('wrong-instance');
    if (p.course_id != null && Number(m[2]) !== Number(p.course_id)) return no('wrong-course');
    if (p.run_id != null && Number(m[3]) !== Number(p.run_id)) return no('wrong-run');
    const rest = m[4].replace(/\/+$/, '');
    if (rest === 'complete' || rest.indexOf('complete/') === 0) return no('complete');
    if (rest === 'checkpoint' || rest.indexOf('checkpoint/') === 0) return no('checkpoint');
    let n = null;
    if (rest !== 'background') {
      const pm = rest.match(PAGE_RE);
      if (!pm) return no('not-a-run-page');
      n = Number(pm[1]);
    }
    if (p.mode !== 'plan') return no('no-plan');
    if (!p.origin) return no('no-origin');
    if ((p.never_routes || []).indexOf(rest) >= 0) return no('never-navigated');
    const idx = mapIndex(u.pathname, rest, n, p);
    if (idx == null) return no(p.mapped ? 'page-not-in-plan' : 'plan-has-no-page-map');
    return indexRule(idx, p);
  }
  /* Flow index of a run URL through the plan's own map: exact path first, then the URL page
   * number. URL page numbers count content pages only, so without a map there is no index. */
  function mapIndex(pathname, rest, n, p) {
    const path = String(pathname).replace(/\/+$/, '');
    if (own(p.url_map, path)) return p.url_map[path];
    if (rest === 'background') return 1;
    if (n != null && own(p.page_map, String(n))) return p.page_map[String(n)];
    return null;
  }
  function indexRule(idx, p) {
    if ((p.report_indexes || []).indexOf(idx) >= 0) return no('report-page', idx);
    if ((p.never_indexes || []).indexOf(idx) >= 0) return no('never-navigated', idx);
    if ((p.checkpoint_indexes || []).indexOf(idx) >= 0) return no('checkpoint', idx);
    if (p.stop_before_page != null && idx >= p.stop_before_page) return no('at-or-past-stop', idx);
    return { allowed: true, reason: 'allowed', page_index: idx };
  }
  /* Every flow position strictly between the current page and the destination: none may be a
   * checkpoint page or at or past the stop. Report and never-navigated pages may be passed. */
  function crossRule(from, to, p) {
    const lo = Math.min(from, to);
    const hi = Math.max(from, to);
    for (let i = lo + 1; i < hi; i++) {
      if ((p.checkpoint_indexes || []).indexOf(i) >= 0) return no('crosses-checkpoint', i);
      if (p.stop_before_page != null && i >= p.stop_before_page) return no('crosses-stop', i);
    }
    return null;
  }
  /* The current flow index under policy p: {idx} when known, {outside: true} when the URL is
   * not a page of the plan's run, or {idx: null} when it is in the run but cannot be mapped. */
  function currentIndex(p, painted) {
    if (painted) return { idx: painted.idx };
    const cur = here();
    if (!cur) return { idx: null };
    const m = cur.pathname.match(RUN_RE);
    if (!m || (p.origin && cur.origin !== p.origin) ||
        (p.instance != null && m[1] !== String(p.instance)) ||
        (p.course_id != null && Number(m[2]) !== Number(p.course_id)) ||
        (p.run_id != null && Number(m[3]) !== Number(p.run_id))) return { outside: true };
    const rest = m[4].replace(/\/+$/, '');
    const pm = rest.match(PAGE_RE);
    return { idx: mapIndex(cur.pathname, rest, pm ? Number(pm[1]) : null, p) };
  }

  const here = () => {
    try { return new G.URL(String(G.location && G.location.href)); } catch (e) { return null; }
  };
  const blockAll = () => !st.policies.length || st.policies.some(p => p.mode !== 'plan');

  function decideNav(href, download) {
    const cur = here();
    let u;
    try { u = new G.URL(String(href), cur ? cur.href : undefined); } catch (e) { return no('bad-url'); }
    if (download && (u.protocol === 'blob:' || u.protocol === 'data:')) {
      return { allowed: true, reason: 'download', page_index: null };
    }
    if (cur && u.origin === cur.origin && u.pathname === cur.pathname && u.search === cur.search) {
      return { allowed: true, reason: 'same-page', page_index: null };
    }
    if (blockAll()) return no('block-all');
    const painted = paintedPage();
    let r = null;
    for (const p of st.policies) {
      r = classify(u.href, p);
      if (!r.allowed) return r;
      const c = currentIndex(p, painted);
      if (c.outside) continue;
      if (c.idx == null) return no('current-not-in-plan', r.page_index);
      const x = crossRule(c.idx, r.page_index, p);
      if (x) return x;
    }
    return r || no('block-all');
  }
  /* The flow page Lt painted, {p, idx} with idx 1-based as capture numbers it, or null. */
  function paintedPage() {
    const root = rootOf();
    const pages = root ? flowPages(root) : [];
    for (let i = 0; i < pages.length; i++) if (isCurrent(pages[i])) return { p: pages[i], idx: i + 1 };
    return null;
  }

  function onNavigate(ev) {
    const dest = ev && ev.destination ? ev.destination.url : '';
    const dl = !!ev && typeof ev.downloadRequest === 'string';
    const d = decideNav(dest, dl);
    if (d.allowed) return;
    trip('navigate:' + d.reason);
    if (ev.cancelable) {
      try { ev.preventDefault(); } catch (e) { st.uncancelable++; }
    } else {
      st.uncancelable++;
    }
  }

  function listenNavigate() {
    const nav = G.navigation;
    if (!nav || typeof nav.addEventListener !== 'function') { st.navigate = 'no-navigation-api'; return; }
    nav.addEventListener('navigate', onNavigate);
    st.navigate = 'listening';
  }

  /* ---- route refusal at script start ---- */
  function routeRefusal() {
    const loc = G.location || {};
    const cur = here();
    if (cur && isPreview(cur)) return 'preview';
    if (!cur && /\/preview(\/|$)/i.test(String(loc.pathname || ''))) return 'preview';
    const m = String(cur ? cur.pathname : loc.pathname || '').match(RUN_RE);
    if (m) {
      const rest = m[4].replace(/\/+$/, '');
      if (rest === 'complete' || rest.indexOf('complete/') === 0) return 'complete';
      if (rest === 'checkpoint' || rest.indexOf('checkpoint/') === 0) return 'checkpoint';
    }
    const painted = paintedPage();
    if (painted) {
      if (isReport(painted.p)) return 'report-page';
      if (isCheckpoint(painted.p)) return 'checkpoint';
    }
    for (const pol of st.policies) {
      if (pol.mode !== 'plan') continue;
      /* The flow page Lt painted decides, whatever number the URL carries. */
      if (painted) {
        const r = indexRule(painted.idx, pol);
        if (!r.allowed) return r.reason;
      }
      if (cur && m) {
        const c = classify(cur.href, pol);
        if (!c.allowed && (!painted || c.reason === 'report-page')) return c.reason;
      }
    }
    return null;
  }

  /* ---- public API ---- */
  const clone = v => JSON.parse(JSON.stringify(v));
  function addPolicy(policy) {
    const p = (!policy || typeof policy !== 'object' || policy.mode !== 'plan') ? { mode: 'block-all' } : clone(policy);
    const key = JSON.stringify(p);
    if (!st.policies.some(q => JSON.stringify(q) === key)) {
      st.policies.push(Object.freeze(p));
      if (st.policies.length > 20) st.policies.splice(1, 1);
    }
  }

  function install() {
    if (st.installed) return false;
    st.installed = true;
    wrapForm();
    guardEvents();
    guardConfirm();
    listenNavigate();
    if (!wrapMutators()) scheduleRetry(); else watch();
    return true;
  }

  /* Re-check every wrapper; Lt may have rebound a mutator or replaced $root. */
  function check() {
    install();
    wrapForm();
    guardConfirm();
    if (!wrapMutators()) scheduleRetry(); else watch();
  }

  const failedParts = () => {
    const bad = MUTATORS.filter(n => st.mutators[n] === 'failed');
    if (st.form === 'failed') bad.push('form');
    return bad;
  };

  function stamp(r) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) return r;
    const out = Object.assign({}, r, { guard_trips: st.trips });
    if (st.trips) out.guard_last = st.last;
    const degraded = [];
    if (st.navigate !== 'listening') degraded.push(st.navigate);
    if (st.events !== 'listening') degraded.push('events:' + st.events);
    if (st.confirm !== 'answers-no') degraded.push('confirm:' + st.confirm);
    for (const n of MUTATORS) {
      const state = st.mutators[n] || 'no-root';
      if (!['locked', 'wrapped', 'rewrapped'].includes(state)) degraded.push('mutators:' + n + ':' + state);
    }
    if (degraded.length) out.guard_degraded = degraded.join(',');
    return out;
  }

  async function run(policy, body, opts) {
    addPolicy(policy);
    check();
    const bad = failedParts();
    if (bad.length) return stamp({ ok: false, fatal: 'guard-install-failed', detail: bad.join(',') });
    const refused = routeRefusal();
    if (refused) return stamp({ ok: false, fatal: 'forbidden-route', route: refused });
    if (opts && opts.net) {
      if (!G.LtNet || typeof G.LtNet.install !== 'function') return stamp({ ok: false, fatal: 'net-missing' });
      G.LtNet.install(G);
    }
    if (st.active) return stamp({ok: false, fatal: 'guard-busy'});
    st.active = clone(policy || {});
    let r;
    try {
      r = await body();
    } catch (e) {
      r = { ok: false, fatal: 'script-threw', error: String((e && e.message) || e).slice(0, 200) };
    }
    st.token = null;
    st.active = null;
    check();
    return stamp(r);
  }

  function status() {
    check();
    const muts = {};
    for (const n of MUTATORS) muts[n] = st.mutators[n] || 'no-root';
    const bad = failedParts();
    return {
      ok: bad.length === 0, installed: st.installed, version: 2, guard_trips: st.trips,
      mutators: muts, form: st.form, submit_listener: st.submit_listener, navigate: st.navigate,
      events: st.events, confirm: st.confirm,
      policy: blockAll() ? 'block-all' : 'plan', policies: st.policies.length,
      rewraps: st.rewraps, rebinds_blocked: st.rebinds_blocked, uncancelable: st.uncancelable
    };
  }

  const api = Object.freeze({
    version: 2, install, check, run, stamp, status, classify, armCheck, commitControl,
    allowed: (href, download) => decideNav(href, !!download),
    trips: () => st.trips,
    policies: () => clone(st.policies)
  });
  Object.defineProperty(G, 'LtGuard', { value: api, enumerable: false, configurable: false, writable: false });
  install();
})(typeof globalThis !== 'undefined' ? globalThis : this);
