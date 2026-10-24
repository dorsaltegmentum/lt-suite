// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Smoke test of the page's DOM wiring (artifact_ui.js) without a browser or npm: a small DOM is built from the
   worked example's page (page_fixture.js, with the engine's current scripts), the page scripts run in it, and
   the test clicks through the four modes.

     node --test tests/diagram/artifact_ui.test.js
     FIG_PAGE=<case_dir>/<page>.html node --test tests/diagram/artifact_ui.test.js   (another built page)

   The DOM here is deliberately minimal (the selectors and properties the page uses); it checks wiring and state,
   not layout or paint. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {pageHtml} = require('./page_fixture.js');

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const ENT = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' '};
const decode = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => e[0] === '#'
  ? String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : +e.slice(1)) : (ENT[e] ?? m));

class Text { constructor(t) { this.data = t; this.parentNode = null; this.nodeType = 3; } get textContent() { return this.data; } }
class El {
  constructor(tag, doc) { this.tagName = tag; this.attrs = {}; this.childNodes = []; this.parentNode = null; this.nodeType = 1;
    this.listeners = {}; this.ownerDocument = doc; this.style = {}; }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  get id() { return this.getAttribute('id') || ''; } set id(v) { this.setAttribute('id', v); }
  get className() { return this.getAttribute('class') || ''; } set className(v) { this.setAttribute('class', v); }
  get hidden() { return this.hasAttribute('hidden'); } set hidden(v) { if (v) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
  get disabled() { return this.hasAttribute('disabled'); } set disabled(v) { if (v) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); }
  get classList() {
    const el = this, list = () => el.className.split(/\s+/).filter(Boolean);
    return {contains: (c) => list().includes(c),
      add: (c) => { if (!list().includes(c)) el.className = list().concat(c).join(' '); },
      remove: (c) => { el.className = list().filter((x) => x !== c).join(' '); },
      toggle: (c, on) => { const has = list().includes(c); on = on === undefined ? !has : !!on;
        if (on && !has) el.className = list().concat(c).join(' '); if (!on && has) el.className = list().filter((x) => x !== c).join(' '); return on; }};
  }
  appendChild(n) { if (n.parentNode) n.parentNode.childNodes.splice(n.parentNode.childNodes.indexOf(n), 1);
    n.parentNode = this; this.childNodes.push(n); return n; }
  insertBefore(n, ref) { if (!ref) return this.appendChild(n);
    if (n.parentNode) n.parentNode.childNodes.splice(n.parentNode.childNodes.indexOf(n), 1);
    const i = this.childNodes.indexOf(ref); if (i < 0) throw new Error('insertBefore: not a child');
    n.parentNode = this; this.childNodes.splice(i, 0, n); return n; }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(t) { this.childNodes.forEach((n) => { n.parentNode = null; }); this.childNodes = [];
    if (t !== '' && t != null) this.appendChild(new Text(String(t))); }
  get value() { if (this.tagName === 'select') return this._value || ''; return this._value ?? this.getAttribute('value') ?? ''; }
  set value(v) { this._value = String(v); }
  *walk() { for (const c of this.childNodes) if (c.nodeType === 1) { yield c; yield* c.walk(); } }
  matches(sel) {
    if (sel === ':focus-visible') return true;
    return sel.split(',').some((one) => {
      const m = one.trim().match(/^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/);
      if (!m) throw new Error('selector not supported: ' + one);
      if (m[1] && this.tagName.toLowerCase() !== m[1].toLowerCase()) return false;
      const cls = (m[2] || '').split('.').filter(Boolean);
      if (!cls.every((c) => this.classList.contains(c))) return false;
      return (m[3].match(/\[[^\]]+\]/g) || []).every((a) => {
        const am = a.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
        return am[2] === undefined ? this.hasAttribute(am[1]) : this.getAttribute(am[1]) === am[2];
      });
    });
  }
  querySelectorAll(sel) { return [...this.walk()].filter((e) => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { let n = this; while (n && n.nodeType === 1) { if (n.matches(sel)) return n; n = n.parentNode; } return null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  focus() { this.ownerDocument.activeElement = this; }
  getBoundingClientRect() {
    const visible = this.classList.contains(this.ownerDocument.visibleFig) || this.id === 'paper';   // the first palette's figure
    return visible ? {left: 0, top: 0, width: 1000, height: 600} : {left: 0, top: 0, width: 0, height: 0};
  }
  get clientWidth() { return 1000; } get clientHeight() { return 600; }
  scrollTo(o) { this.scrollLeft = o.left; this.scrollTop = o.top; }
}

function parse(src, doc) {
  const root = new El('#root', doc);
  let cur = root, i = 0;
  while (i < src.length) {
    if (src.startsWith('<!--', i)) { i = src.indexOf('-->', i) + 3; continue; }
    if (src[i] === '<' && src[i + 1] === '/') {
      const j = src.indexOf('>', i), tag = src.slice(i + 2, j).trim().toLowerCase();
      let n = cur;
      while (n && n !== root && n.tagName.toLowerCase() !== tag) n = n.parentNode;
      if (n && n !== root) cur = n.parentNode;
      i = j + 1; continue;
    }
    if (src[i] === '<' && /[a-zA-Z]/.test(src[i + 1])) {
      let j = i + 1, q = null;
      while (j < src.length) { const c = src[j]; if (q) { if (c === q) q = null; } else if (c === '"' || c === "'") q = c; else if (c === '>') break; j++; }
      const body = src.slice(i + 1, j), selfClose = body.endsWith('/');
      const tag = body.match(/^[\w:-]+/)[0];
      const el = new El(tag, doc);
      const re = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
      re.lastIndex = tag.length;
      let m;
      while ((m = re.exec(body.replace(/\/$/, '')))) el.setAttribute(m[1], decode(m[2] ?? m[3] ?? m[4] ?? ''));
      cur.appendChild(el);
      i = j + 1;
      const low = tag.toLowerCase();
      if (low === 'script' || low === 'style') {
        const end = src.toLowerCase().indexOf('</' + low, i);
        el.appendChild(new Text(src.slice(i, end)));
        i = src.indexOf('>', end) + 1; continue;
      }
      if (!selfClose && !VOID.has(low)) cur = el;
      continue;
    }
    const j = src.indexOf('<', i + 1), end = j < 0 ? src.length : j;
    cur.appendChild(new Text(decode(src.slice(i, end))));
    i = end;
  }
  return root;
}

function boot() {
  const html = pageHtml();
  const doc = {activeElement: null, listeners: {}, fullscreenEnabled: false,
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    createElement: (t) => new El(t, doc), createElementNS: (ns, t) => new El(t, doc), createTextNode: (t) => new Text(t)};
  const root = parse(html, doc);
  doc.root = root;
  const firstPal = root.querySelectorAll('button').map((b) => b.getAttribute('data-pal')).find((p) => p && p !== 'auto');
  doc.visibleFig = 'fig-' + firstPal;
  doc.getElementById = (id) => [...root.walk()].find((e) => e.getAttribute('id') === id) || null;
  doc.querySelectorAll = (s) => root.querySelectorAll(s);
  const store = {};
  const win = {document: doc, innerWidth: 1280, FigLogic: undefined,
    matchMedia: () => ({matches: true}),            // reduced motion: no animation frames to wait for
    localStorage: {getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }},
    requestAnimationFrame: (f) => setTimeout(() => f(Date.now()), 0), cancelAnimationFrame: clearTimeout,
    setTimeout, clearTimeout, Promise, atob: (s) => Buffer.from(s, 'base64').toString('binary'), Blob: class {}, Uint8Array};
  win.window = win; win.self = win;
  const ctx = vm.createContext(win);
  const scripts = root.querySelectorAll('script').filter((s) => !s.getAttribute('type'));
  scripts.forEach((s) => vm.runInContext(s.textContent, ctx));
  return {doc, win, root, store, $: (id) => doc.getElementById(id)};
}

function fire(doc, target, type, extra) {
  const ev = Object.assign({type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }}, extra || {});
  let n = target;
  while (n) { (n.listeners && n.listeners[type] || []).forEach((f) => f(ev)); n = n.parentNode; }
  (doc.listeners[type] || []).forEach((f) => f(ev));
  return ev;
}
const click = (doc, el) => fire(doc, el, 'click');
const tick = () => new Promise((r) => setTimeout(r, 60));

test('page boots in Read mode with the remembered size', () => {
  const {win, root, $} = boot();
  const P = win.FigPage;
  assert.ok(P, 'the page script ran');
  assert.equal(P.state.mode, 'read');
  assert.equal($('mode-read').getAttribute('aria-pressed'), 'true');
  assert.equal($('bar-present').hidden, true);
  assert.equal($('size-zoom').hidden, true);
  assert.equal($('pv-full').hidden, true, 'no fullscreen in this view: the button hides');
  const pals = root.querySelectorAll('button').filter((b) => b.getAttribute('data-pal'));
  assert.ok(P.figs.length >= 1);
  assert.equal(P.figs.length, pals.length, 'one figure per configured palette (style.palettes)');
  P.figs.forEach((f) => {
    assert.ok(Object.keys(f.nodes).length > 0);
    assert.ok(Object.values(f.nodes).every((g) => (g.getAttribute('data-v') || '') === ''));
    assert.equal(f.svg.querySelectorAll('g.qcard').length, Object.keys(f.nodes).length);
  });
  click(win.document, $('size-fit'));
  assert.ok($('paper').classList.contains('fit'));
});

test('present: steps, later stages, keys, zoom', async () => {
  const {win, doc, $} = boot();
  const P = win.FigPage, f = P.figs[0];
  click(doc, $('mode-present'));
  assert.equal(P.state.mode, 'present');
  assert.equal($('bar-present').hidden, false);
  assert.equal($('size-zoom').hidden, false);
  assert.match($('pv-count').textContent, /^Step 1 of \d+$/);
  const first = P.steps[0].stages[0];
  const firstNode = Object.keys(f.nodes).find((id) => f.nodes[id].getAttribute('data-stage') === first);
  const laterNode = Object.keys(f.nodes).find((id) => !P.steps[0].stages.includes(f.nodes[id].getAttribute('data-stage')));
  assert.equal(f.nodes[firstNode].getAttribute('data-v'), '');
  assert.equal(f.nodes[laterNode].getAttribute('data-v'), 'gone');
  assert.equal($('pv-prev').disabled, true);
  assert.ok($('pv-title').textContent.length > 0);
  click(doc, $('pv-next'));
  assert.equal(P.state.step, 1);
  assert.equal(f.nodes[firstNode].getAttribute('data-v'), 'past');
  f.extras.forEach((x) => {                 // extra layers (e.g. the time-course markers) follow what they name
    if (x.ref.stage && !x.ref.node && !x.ref.a) assert.equal(x.el.getAttribute('data-v'), f.stages[x.ref.stage].getAttribute('data-v'));
    if (x.ref.node) assert.equal(x.el.getAttribute('data-v'), f.nodes[x.ref.node].getAttribute('data-v'));
  });
  fire(doc, doc.root, 'keydown', {key: 'ArrowRight'});
  assert.equal(P.state.step, 2);
  fire(doc, doc.root, 'keydown', {key: 'ArrowLeft'});
  assert.equal(P.state.step, 1);
  click(doc, $('pv-fade'));
  assert.equal(f.nodes[laterNode].getAttribute('data-v') === 'fade' || P.steps[1].stages.includes(f.nodes[laterNode].getAttribute('data-stage')), true);
  fire(doc, doc.root, 'keydown', {key: 'End'});
  assert.equal(P.state.step, P.steps.length - 1);
  assert.equal($('pv-next').disabled, true);
  assert.ok(Object.values(f.nodes).every((g) => g.getAttribute('data-v') === ''), 'the last step shows everything');
  fire(doc, doc.root, 'keydown', {key: 'Home'});
  click(doc, $('size-zoom'));
  assert.ok($('paper').classList.contains('zoom'));
  const vb = f.svg.getAttribute('viewBox').split(' ').map(Number);
  assert.ok(vb[2] < 2000, 'zoomed to the first stage: ' + vb);
  const ring = f.svg.querySelectorAll('rect.ring').filter((r) => r.getAttribute('display') !== 'none');
  assert.equal(ring.length, P.steps[0].stages.length, 'one ring per current stage');
  assert.ok($('pv-note').hidden === false || $('pv-note').textContent === '');
  click(doc, $('mode-read'));
  assert.equal(P.state.size === 'zoom', false, 'leaving Present leaves the zoom');
  assert.equal(f.svg.getAttribute('viewBox'), P.figs[1].svg.getAttribute('viewBox'));
  assert.ok(Object.values(f.nodes).every((g) => g.getAttribute('data-v') === ''));
  await tick();
});

test('trace: click a box, the panel lists the chain, depth, Esc', async () => {
  const {win, doc, $} = boot();
  const P = win.FigPage, f = P.figs[0];
  click(doc, $('mode-trace'));
  assert.equal($('trace-panel').hidden, false);
  assert.ok($('fig-area').classList.contains('with-panel'));
  const ids = Object.keys(f.nodes);
  // the box with the most links makes a good test
  const data = JSON.parse($('fig-data').textContent);
  const deg = (id) => data.edges.filter((e) => e.a === id || e.b === id).length;
  const sel = ids.sort((a, b) => deg(b) - deg(a))[0];
  assert.equal(f.nodes[sel].getAttribute('tabindex'), '0');
  assert.equal(f.nodes[sel].getAttribute('role'), 'button');
  click(doc, f.nodes[sel].querySelector('rect'));
  assert.equal(P.state.sel, sel);
  assert.equal($('tr-status').hidden, false);
  assert.match($('tr-status').textContent, /^Tracing “.+”: \d+ upstream, \d+ downstream\./);
  const items = $('tp-body').querySelectorAll('button.ci');
  assert.ok(items.length >= deg(sel), 'the panel lists at least the direct links');
  assert.ok(Object.values(f.nodes).some((g) => g.getAttribute('data-v') === 'dim'));
  assert.equal(f.nodes[sel].getAttribute('data-v'), '');
  const selRing = f.svg.querySelectorAll('rect.ring').filter((r) => r.getAttribute('display') !== 'none');
  assert.equal(selRing.length, 1);
  const kids = selRing[0].parentNode.parentNode.childNodes;
  assert.ok(kids.indexOf(selRing[0].parentNode) < kids.indexOf(f.edges[0]), 'the ring sits under the arrows');
  click(doc, $('tr-d1'));
  assert.equal(P.state.depth, 1);
  assert.equal($('tr-d1').getAttribute('aria-pressed'), 'true');
  const direct = $('tp-body').querySelectorAll('button.ci').length;
  assert.ok(direct <= items.length);
  click(doc, items.length ? $('tp-body').querySelectorAll('button.ci')[0].querySelector('span') : $('tp-body'));
  assert.notEqual(P.state.sel, sel, 'clicking a chain entry traces it');
  assert.equal(doc.activeElement && doc.activeElement.id, 'tp-sel');
  fire(doc, doc.root, 'keydown', {key: 'Escape'});
  assert.equal(P.state.sel, null);
  assert.equal($('tr-status').hidden, true);
  assert.ok(Object.values(f.nodes).every((g) => g.getAttribute('data-v') === ''));
  $('tr-pick').value = sel;
  fire(doc, $('tr-pick'), 'change');
  assert.equal(P.state.sel, sel);
  fire(doc, f.nodes[ids[3]], 'keydown', {key: 'Enter'});
  assert.equal(P.state.sel, ids[3], 'Enter on a focused box traces it');
  click(doc, $('mode-read'));
  assert.equal(f.nodes[sel].getAttribute('tabindex'), null);
  assert.equal(f.svg.getAttribute('role'), 'img');
  await tick();
});

test('quiz: cover, reveal, next, all, reset, one stage', async () => {
  const {win, doc, $} = boot();
  const P = win.FigPage, f = P.figs[0];
  click(doc, $('mode-quiz'));
  const total = Object.keys(f.nodes).length;
  assert.equal($('qz-count').textContent, `0 of ${total} shown`);
  assert.equal($('qz-reset').disabled, true);
  const cards = f.svg.querySelectorAll('g.qcard');
  assert.ok(cards.every((c) => c.getAttribute('tabindex') === '0'));
  cards.forEach((c) => {                    // each card sits in its own box's group, under any corner badges
    const g = c.parentNode, kids = g.childNodes.filter((k) => k.nodeType === 1);
    assert.equal(g.getAttribute('data-node'), c.getAttribute('data-q'));
    const badges = kids.find((k) => k.classList.contains('glyphs'));
    if (badges) assert.ok(kids.indexOf(c) < kids.indexOf(badges));
    assert.ok(g.querySelectorAll('text').length > 1, 'the box text is still there');
  });
  click(doc, cards[5].querySelector('rect'));
  assert.equal($('qz-count').textContent, `1 of ${total} shown`);
  assert.ok(cards[5].classList.contains('shown'));
  assert.ok(P.figs[1].cards[cards[5].getAttribute('data-q')].classList.contains('shown'), 'both figures agree');
  click(doc, $('qz-next'));
  assert.equal($('qz-count').textContent, `2 of ${total} shown`);
  const next = f.svg.querySelectorAll('rect.ring-next').filter((r) => r.getAttribute('display') !== 'none');
  assert.equal(next.length, 1, 'the next covered box is marked');
  click(doc, $('qz-all'));
  assert.equal($('qz-count').textContent, `${total} of ${total} shown`);
  assert.equal($('qz-next').disabled, true);
  click(doc, $('qz-reset'));
  assert.equal($('qz-count').textContent, `0 of ${total} shown`);
  const stage = JSON.parse($('fig-data').textContent).stages[1].id;
  $('qz-scope').value = stage;
  fire(doc, $('qz-scope'), 'change');
  const inStage = Object.keys(f.nodes).filter((id) => f.nodes[id].getAttribute('data-stage') === stage).length;
  assert.equal($('qz-count').textContent, `0 of ${inStage} shown`);
  assert.equal(cards.filter((c) => !c.classList.contains('out')).length, inStage);
  const firstCard = cards.find((c) => !c.classList.contains('out'));
  fire(doc, firstCard, 'keydown', {key: ' '});
  assert.equal($('qz-count').textContent, `1 of ${inStage} shown`);
  assert.ok(doc.activeElement && doc.activeElement.classList.contains('qcard') && !doc.activeElement.classList.contains('shown'),
    'focus moves on to the next covered box');
  click(doc, $('mode-read'));
  assert.ok(Object.values(f.nodes).every((g) => g.getAttribute('data-v') === ''));
  await tick();
});

test('the Palette switch shows one figure outright and remembers it', () => {
  const {win, store, root, $} = boot();
  const shell = $('fig-shell');
  const ids = root.querySelectorAll('button').map((b) => b.getAttribute('data-pal')).filter(Boolean);
  assert.ok(ids.length >= 2, 'at least two configured palettes on the worked example');
  const [first, second] = ids;
  assert.ok(!ids.some((p) => shell.classList.contains('pal-' + p)), 'Auto: the page theme decides');
  assert.equal($('pal-auto').getAttribute('aria-pressed'), 'true');
  click(win.document, $('pal-' + second));
  assert.ok(shell.classList.contains('pal-' + second));
  assert.equal($('pal-' + second).getAttribute('aria-pressed'), 'true');
  assert.equal($('pal-auto').getAttribute('aria-pressed'), 'false');
  assert.ok(Object.values(store).includes(second), 'the choice is stored');
  click(win.document, $('pal-' + first));
  assert.ok(shell.classList.contains('pal-' + first) && !shell.classList.contains('pal-' + second));
  click(win.document, $('pal-auto'));
  assert.ok(!shell.classList.contains('pal-' + first));
});
