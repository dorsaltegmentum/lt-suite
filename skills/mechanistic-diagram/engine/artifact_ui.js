// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Mechanistic diagram Artifact: the page's DOM wiring. Needs FigLogic (artifact_logic.js, inlined before this
   file) and the figure data in <script type="application/json" id="fig-data"> (make_artifact.py writes both).

   Modes: read (the static figure), present (step through the stages), trace (a box's causes and
   consequences), quiz (cover the box texts). Every view state lands on the figure as a data-v attribute
   ("", past, fade, gone, dim) on each stage, arrow, box and extra layer, in each palette
   copy, plus rings and quiz cards in overlay groups appended to each SVG. Nothing of the SVG is removed. */
(function () {
  'use strict';
  var L = window.FigLogic;
  var dataEl = document.getElementById('fig-data');
  var paper = document.getElementById('paper');
  if (!L || !dataEl || !paper) return;
  var data = JSON.parse(dataEl.textContent);
  var graph = L.buildGraph(data);
  var STEPS = L.steps(data);
  var NS = 'http://www.w3.org/2000/svg';
  var VB0 = data.vb.slice();
  var STAGE = {};
  data.stages.forEach(function (s) { STAGE[s.id] = s; });

  function $(id) { return document.getElementById(id); }
  var shell = $('fig-shell'), live = $('fig-live'), area = $('fig-area'), panel = $('trace-panel');

  function store(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } }
  function recall(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  var liveTimer = null;
  function announce(msg) {
    live.textContent = '';
    clearTimeout(liveTimer);
    liveTimer = setTimeout(function () { live.textContent = msg; }, 40);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  /* text with the case's italic terms (binomial names) in <i>, never through innerHTML */
  var TERMS = (data.italic || []).filter(Boolean);
  function rich(target, text) {
    target.textContent = '';
    var rest = String(text);
    while (rest) {
      var at = -1, term = null;
      TERMS.forEach(function (t) {
        var i = rest.indexOf(t);
        if (i >= 0 && (at < 0 || i < at)) { at = i; term = t; }
      });
      if (at < 0) { target.appendChild(document.createTextNode(rest)); break; }
      if (at > 0) target.appendChild(document.createTextNode(rest.slice(0, at)));
      target.appendChild(el('i', null, term));
      rest = rest.slice(at + term.length);
    }
    return target;
  }
  function short(text, max) {
    max = max || 64;
    if (text.length <= max) return text;
    var cut = text.slice(0, max - 1), sp = cut.lastIndexOf(' ');
    return (sp > max * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,;:]+$/, '') + '…';
  }
  function nodeRect(id) { var n = data.nodes[id]; return [n.x, n.y, n.w, n.h, 7]; }
  function stageRect(id) {
    var r = STAGE[id] && STAGE[id].rect;
    return r ? [r[0], r[1], r[2] - r[0], r[3] - r[1], 12] : null;
  }
  function roleName(id) { return data.roles[data.nodes[id].r] || data.nodes[id].r; }

  /* ---------------------------------------------------------------- the two figures and their overlays */
  var figs = Array.prototype.map.call(paper.querySelectorAll('svg.fig'), function (svg) {
    var f = {svg: svg, nodes: {}, edges: [], stages: {}, extras: [], cards: {}, rings: {}};
    Array.prototype.forEach.call(svg.querySelectorAll('g.node'), function (g) { f.nodes[g.getAttribute('data-node')] = g; });
    f.edges = Array.prototype.slice.call(svg.querySelectorAll('g.edge'));
    Array.prototype.forEach.call(svg.querySelectorAll('g.stage'), function (g) { f.stages[g.getAttribute('data-stage')] = g; });
    Array.prototype.forEach.call(svg.querySelectorAll('[data-node], [data-stage], [data-a]'), function (x) {
      var c = x.classList;
      if (c.contains('node') || c.contains('edge') || c.contains('stage')) return;
      var up = x.parentNode;
      while (up && up !== svg) {
        if (up.classList && (up.classList.contains('node') || up.classList.contains('edge') || up.classList.contains('stage'))) return;
        up = up.parentNode;
      }
      f.extras.push({el: x, ref: {node: x.getAttribute('data-node'), stage: x.getAttribute('data-stage'),
                                  a: x.getAttribute('data-a'), b: x.getAttribute('data-b')}});
    });
    /* quiz cards: one per box, inside its node group, over the box's text (and range bars) but under its corner
       badges (g.glyphs), which stay visible as hints; nothing of the box is removed */
    data.order.forEach(function (id) {
      var n = data.nodes[id], g = f.nodes[id];
      if (!g) return;
      var card = svgEl('g', {'class': 'qcard r-' + n.r + (n.b ? ' bold' : ''), 'data-q': id, role: 'button', tabindex: '-1',
                             'aria-label': 'Covered box: ' + roleName(id) + ', ' + STAGE[n.s].title + '. Reveal it.'});
      card.appendChild(svgEl('rect', {x: n.x, y: n.y, width: n.w, height: n.h, rx: 7, ry: 7}));
      var q = svgEl('text', {x: n.x + n.w / 2, y: n.y + n.h / 2 + 6, 'text-anchor': 'middle'});
      q.textContent = '?';
      card.appendChild(q);
      var badges = null;
      for (var c = 0; c < g.childNodes.length; c++) {
        var k = g.childNodes[c];
        if (k.nodeType === 1 && k.classList && k.classList.contains('glyphs')) { badges = k; break; }
      }
      g.insertBefore(card, badges);
      f.cards[id] = card;
    });
    f.ov = svgEl('g', {'class': 'ov', 'aria-hidden': 'true'});
    svg.appendChild(f.ov);
    // rings round stages and boxes sit under the arrows, so arrowheads and tees stay whole where they cross
    f.under = svgEl('g', {'class': 'ov', 'aria-hidden': 'true'});
    var firstEdge = f.edges[0] || svg.querySelector('g.node');
    if (firstEdge && firstEdge.parentNode) firstEdge.parentNode.insertBefore(f.under, firstEdge);
    else svg.insertBefore(f.under, f.ov);
    return f;
  });

  function setV(node, s) {
    s = s || '';
    if (node.getAttribute('data-v') !== s) node.setAttribute('data-v', s);
  }
  /* view: {nodes, edges, stages} from FigLogic, or null for the plain figure */
  function apply(view) {
    figs.forEach(function (f) {
      Object.keys(f.nodes).forEach(function (id) { setV(f.nodes[id], view ? view.nodes[id] : ''); });
      f.edges.forEach(function (g, i) { setV(g, view ? view.edges[i] : ''); });
      Object.keys(f.stages).forEach(function (id) { setV(f.stages[id], view ? view.stages[id] : ''); });
      f.extras.forEach(function (x) { setV(x.el, view ? L.extraState(view, data, x.ref) : ''); });
    });
  }
  /* rings: rects [x, y, w, h, rx] grown by pad; an empty list hides them. The keyboard focus ring goes on top of
     everything, the others under the arrows. */
  function setRings(key, rects, cls, pad) {
    figs.forEach(function (f) {
      var list = f.rings[key] || (f.rings[key] = []);
      while (list.length < rects.length) {
        var r = svgEl('rect', {'class': cls});
        (key === 'focus' ? f.ov : f.under).appendChild(r);
        list.push(r);
      }
      list.forEach(function (ring, i) {
        var r = rects[i];
        if (!r) { ring.setAttribute('display', 'none'); return; }
        ring.removeAttribute('display');
        ring.setAttribute('x', r[0] - pad); ring.setAttribute('y', r[1] - pad);
        ring.setAttribute('width', r[2] + 2 * pad); ring.setAttribute('height', r[3] + 2 * pad);
        ring.setAttribute('rx', r[4] + pad); ring.setAttribute('ry', r[4] + pad);
      });
    });
  }
  function clearRings(except) {
    ['stage', 'sel', 'next', 'focus'].forEach(function (k) { if (k !== except) setRings(k, [], '', 0); });
  }

  function visibleSvg() {
    for (var i = 0; i < figs.length; i++) {
      if (figs[i].svg.getBoundingClientRect().width > 0) return figs[i].svg;
    }
    return null;
  }
  /* scroll the frame so the rect (SVG units) is in view; a rect bigger than the frame shows its top left */
  function scrollToRect(r, smooth) {
    if (!r || state.size === 'zoom') return;
    var svg = visibleSvg();
    if (!svg) return;
    var box = svg.getBoundingClientRect(), pb = paper.getBoundingClientRect();
    var vb = VB0, scale = box.width / vb[2];
    var left = box.left - pb.left + paper.scrollLeft + (r[0] - vb[0]) * scale;
    var top = box.top - pb.top + paper.scrollTop + (r[1] - vb[1]) * scale;
    var w = r[2] * scale, h = r[3] * scale, cw = paper.clientWidth, ch = paper.clientHeight;
    var inX = left >= paper.scrollLeft && left + w <= paper.scrollLeft + cw;
    var inY = top >= paper.scrollTop && top + h <= paper.scrollTop + ch;
    if (inX && inY) return;
    var x = inX ? paper.scrollLeft : (w + 32 > cw ? left - 16 : left + w / 2 - cw / 2);
    var y = inY ? paper.scrollTop : (h + 32 > ch ? top - 16 : top + h / 2 - ch / 2);
    var opts = {left: Math.max(0, x), top: Math.max(0, y), behavior: smooth && !reduceMotion() ? 'smooth' : 'auto'};
    if (paper.scrollTo) paper.scrollTo(opts); else { paper.scrollLeft = opts.left; paper.scrollTop = opts.top; }
  }

  /* zoom: animate both figures' viewBox */
  var anim = null;
  function currentVB() { return figs.length ? figs[0].svg.getAttribute('viewBox').split(/[\s,]+/).map(Number) : VB0; }
  function setViewBox(target, animate) {
    if (anim !== null) { cancelAnimationFrame(anim); anim = null; }
    var from = currentVB();
    function put(vb) { figs.forEach(function (f) { f.svg.setAttribute('viewBox', vb.map(function (v) { return +v.toFixed(1); }).join(' ')); }); }
    if (!animate || reduceMotion() || typeof requestAnimationFrame !== 'function') { put(target); return; }
    var t0 = null, dur = 420;
    function tick(t) {
      if (t0 === null) t0 = t;
      var p = Math.min(1, (t - t0) / dur), e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      put(from.map(function (v, i) { return v + (target[i] - v) * e; }));
      anim = p < 1 ? requestAnimationFrame(tick) : null;
    }
    anim = requestAnimationFrame(tick);
  }

  /* ---------------------------------------------------------------- state */
  var KEY = data.storageKey;
  var saved = recall(KEY);
  var state = {
    mode: 'read', size: 'actual',
    sizePref: (saved === null ? window.innerWidth < 900 : saved === '1') ? 'fit' : 'actual',
    presentSize: 'fit', step: 0, later: 'gone',
    sel: null, depth: 0,
    scope: '', shown: {}
  };

  var sizeBtn = {actual: $('size-actual'), fit: $('size-fit'), zoom: $('size-zoom')};
  var modeBtn = {read: $('mode-read'), present: $('mode-present'), trace: $('mode-trace'), quiz: $('mode-quiz')};
  var bars = {read: $('bar-read'), present: $('bar-present'), trace: $('bar-trace'), quiz: $('bar-quiz')};

  function frame(animate) {
    if (state.mode === 'present' && state.size === 'zoom') {
      var step = STEPS[state.step];
      setViewBox(L.focusRect(data, step.whole ? [] : step.stages, 28), animate);
    } else {
      setViewBox(VB0, false);
    }
  }
  function setSize(size, byUser) {
    if (size === 'zoom' && state.mode !== 'present') size = 'fit';
    state.size = size;
    paper.classList.toggle('fit', size === 'fit');
    paper.classList.toggle('zoom', size === 'zoom');
    Object.keys(sizeBtn).forEach(function (k) { sizeBtn[k].setAttribute('aria-pressed', String(k === size)); });
    if (state.mode === 'present') state.presentSize = size;
    else if (byUser) { state.sizePref = size; store(KEY, size === 'fit' ? '1' : '0'); }
    frame(byUser);
    if (byUser && state.mode === 'present') scrollToStep(false);
  }

  function setMode(mode) {
    var prev = state.mode;
    state.mode = mode;
    paper.setAttribute('data-mode', mode);
    shell.setAttribute('data-mode', mode);
    Object.keys(modeBtn).forEach(function (k) { modeBtn[k].setAttribute('aria-pressed', String(k === mode)); });
    Object.keys(bars).forEach(function (k) { bars[k].hidden = k !== mode; });
    sizeBtn.zoom.hidden = mode !== 'present';
    panel.hidden = mode !== 'trace';
    area.classList.toggle('with-panel', mode === 'trace');
    var interactive = mode === 'trace' || mode === 'quiz';
    figs.forEach(function (f) {
      f.svg.setAttribute('role', interactive ? 'group' : 'img');
      Object.keys(f.nodes).forEach(function (id) {
        var g = f.nodes[id];
        if (mode === 'trace') {
          g.setAttribute('tabindex', '0');
          g.setAttribute('role', 'button');
          g.setAttribute('aria-label', data.nodes[id].t + ' (' + roleName(id) + ')');
        } else {
          g.removeAttribute('tabindex'); g.removeAttribute('role'); g.removeAttribute('aria-label');
        }
      });
    });
    if (mode === 'present') setSize(state.presentSize, false);
    else if (prev === 'present' || state.size === 'zoom') setSize(state.sizePref, false);
    render(true);
    var say = {read: 'Read mode: the whole figure.',
               present: 'Present mode. Use Next and Back, or the arrow keys, to step through the stages.',
               trace: 'Trace mode. Choose a box to see what leads to it and what follows from it.',
               quiz: 'Quiz mode. The boxes are covered; reveal them one at a time.'}[mode];
    if (prev !== mode) announce(mode === 'present' ? say + ' ' + stepText() : say);
  }

  function render(first) {
    if (state.mode === 'present') renderPresent(first);
    else if (state.mode === 'trace') renderTrace();
    else if (state.mode === 'quiz') renderQuiz();
    else { apply(null); clearRings(); }
  }

  /* ---------------------------------------------------------------- present */
  function stepTitle(k) {
    var step = STEPS[k];
    return step.whole ? 'The whole diagram' : step.stages.map(function (id) { return STAGE[id].title; }).join(' / ');
  }
  function stepText() { return 'Step ' + (state.step + 1) + ' of ' + STEPS.length + ': ' + stepTitle(state.step) + '.'; }
  function scrollToStep(smooth) {
    var step = STEPS[state.step];
    if (step.whole) return;
    var rs = step.stages.map(stageRect).filter(Boolean);
    if (!rs.length) return;
    var x0 = Math.min.apply(null, rs.map(function (r) { return r[0]; })), y0 = Math.min.apply(null, rs.map(function (r) { return r[1]; }));
    var x1 = Math.max.apply(null, rs.map(function (r) { return r[0] + r[2]; })), y1 = Math.max.apply(null, rs.map(function (r) { return r[1] + r[3]; }));
    scrollToRect([x0, y0, x1 - x0, y1 - y0], smooth);
  }
  function renderPresent(first) {
    var k = state.step, step = STEPS[k];
    apply(L.presentView(data, STEPS, k, state.later));
    clearRings('stage');
    setRings('stage', step.whole ? [] : step.stages.map(stageRect).filter(Boolean), 'ring', 0);
    $('pv-count').textContent = 'Step ' + (k + 1) + ' of ' + STEPS.length;
    $('pv-title').textContent = stepTitle(k);
    var notes = step.stages.map(function (id) { return STAGE[id].note; }).filter(Boolean).join(' ');
    var noteEl = $('pv-note');
    noteEl.hidden = !notes;
    rich(noteEl, notes);
    var prev = $('pv-prev'), next = $('pv-next'), had = document.activeElement;
    prev.disabled = k === 0;
    next.disabled = k === STEPS.length - 1;
    if (had === prev && prev.disabled) next.focus();       // keep keyboard focus on a working button
    if (had === next && next.disabled) prev.focus();
    $('pv-gone').setAttribute('aria-pressed', String(state.later === 'gone'));
    $('pv-fade').setAttribute('aria-pressed', String(state.later === 'fade'));
    frame(!first);
    scrollToStep(!first);
  }
  function go(k) {
    k = Math.max(0, Math.min(STEPS.length - 1, k));
    if (k === state.step) return;
    state.step = k;
    renderPresent(false);
    announce(stepText());
  }

  /* ---------------------------------------------------------------- trace */
  function kindGlyph(kind) {
    var s = svgEl('svg', {width: 24, height: 12, viewBox: '0 0 24 12', 'class': 'kg', 'aria-hidden': 'true', focusable: 'false'});
    var tee = kind === 'inh' || kind === 'excl';
    var line = svgEl('line', {x1: 1, y1: 6, x2: tee ? 20 : 17, y2: 6, 'class': 'ks-' + kind});
    var dash = (data.kinds || {})[kind];             // the arrow's own dash, scaled to the glyph (make_artifact.py)
    if (dash) { line.setAttribute('stroke-dasharray', dash.dash); if (dash.round) line.setAttribute('stroke-linecap', 'round'); }
    s.appendChild(line);
    if (tee) s.appendChild(svgEl('line', {x1: 21, y1: 1.5, x2: 21, y2: 10.5, 'class': 'ks-' + kind, 'stroke-linecap': 'round', 'stroke-width': 2.2}));
    else s.appendChild(svgEl('polygon', {points: '23,6 16,2.5 16,9.5', 'class': 'kf-' + kind + ' ks-' + kind}));   // stroked too: round joins round its corners
    return s;
  }
  function swatch(id) { return el('span', 'sw r-' + data.nodes[id].r); }
  function chainItem(it) {
    var li = el('li'), b = el('button', 'ci');
    b.type = 'button';
    b.setAttribute('data-id', it.id);
    b.appendChild(swatch(it.id));
    b.appendChild(rich(el('span'), data.nodes[it.id].t));
    var l0 = it.links[0];
    if (l0) {
      var v = el('span', 'cv');
      v.appendChild(kindGlyph(l0.kind));
      v.appendChild(document.createTextNode(l0.verb + ': '));
      v.appendChild(rich(el('span'), short(data.nodes[l0.other].t)));
      if (it.links.length > 1) v.appendChild(document.createTextNode(' (and ' + (it.links.length - 1) + ' more)'));
      b.appendChild(v);
    }
    li.appendChild(b);
    return li;
  }
  function chainSection(body, title, groups, none) {
    body.appendChild(el('h4', null, title));
    if (!groups.length) { body.appendChild(el('p', 'tp-empty', none)); return; }
    groups.forEach(function (g) {
      body.appendChild(el('p', 'tp-dist', g.dist === 1 ? 'Direct' : g.dist + ' steps away'));
      var ul = el('ul');
      g.items.forEach(function (it) { ul.appendChild(chainItem(it)); });
      body.appendChild(ul);
    });
  }
  function renderPanel(tr) {
    var body = $('tp-body');
    body.textContent = '';
    if (!tr) {
      body.appendChild(el('p', 'tp-empty', 'Tap or click a box in the figure, or choose one from the list, to see every cause that leads to it and every consequence that follows.'));
      return;
    }
    var ch = L.chain(data, graph, tr);
    var head = el('div', 'tp-sel');
    head.id = 'tp-sel';
    head.setAttribute('tabindex', '-1');
    head.appendChild(swatch(tr.sel));
    head.appendChild(rich(el('span'), data.nodes[tr.sel].t));
    body.appendChild(head);
    body.appendChild(el('p', 'tp-meta', roleName(tr.sel) + ' · ' + ch.nUp + ' upstream, ' + ch.nDown + ' downstream'
      + (tr.depth ? ' · within ' + tr.depth + (tr.depth === 1 ? ' step' : ' steps') : '')));
    chainSection(body, 'Upstream: what leads to it', ch.up, 'Nothing on the diagram leads to this box.');
    chainSection(body, 'Downstream: what follows from it', ch.down, 'Nothing on the diagram follows from this box.');
  }
  function renderTrace() {
    var tr = state.sel ? L.trace(graph, state.sel, state.depth) : null;
    apply(L.traceView(data, tr));
    clearRings('sel');
    setRings('sel', tr ? [nodeRect(tr.sel)] : [], 'ring', 6);
    renderPanel(tr);
    var status = $('tr-status');                // shown only when the panel sits below the figure (narrow screens)
    status.hidden = !tr;
    if (tr) {
      var ch = L.chain(data, graph, tr);
      status.textContent = 'Tracing “' + short(data.nodes[tr.sel].t, 48) + '”: ' + ch.nUp + ' upstream, ' + ch.nDown
        + ' downstream. The chain is listed below the figure.';
    }
    $('tr-pick').value = state.sel || '';
    $('tr-clear').disabled = !tr;
    ['tr-d1', 'tr-d2', 'tr-d3', 'tr-d0'].forEach(function (id) {
      $(id).setAttribute('aria-pressed', String(+$(id).getAttribute('data-depth') === state.depth));
    });
    return tr;
  }
  function select(id, from) {
    if (!(id in data.nodes)) return;
    state.sel = id;
    var tr = renderTrace();
    var ch = L.chain(data, graph, tr);
    if (from !== 'figure') scrollToRect(nodeRect(id), true);
    if (from === 'panel') { var h = $('tp-sel'); if (h) h.focus(); }
    announce('Tracing ' + data.nodes[id].t + ': ' + ch.nUp + ' upstream, ' + ch.nDown + ' downstream.');
  }
  function clearTrace() {
    state.sel = null;
    renderTrace();
    announce('Trace cleared.');
  }

  /* ---------------------------------------------------------------- quiz */
  function renderQuiz() {
    apply(null);
    clearRings('next');
    var order = L.quizOrder(data, graph, state.scope), inScope = {};
    order.forEach(function (id) { inScope[id] = true; });
    var prog = L.quizProgress(order, state.shown);
    figs.forEach(function (f) {
      Object.keys(f.cards).forEach(function (id) {
        var c = f.cards[id], out = !inScope[id], shown = !!state.shown[id];
        c.classList.toggle('out', out);
        c.classList.toggle('shown', shown);
        c.setAttribute('tabindex', out || shown ? '-1' : '0');
        c.setAttribute('aria-hidden', String(out || shown));
      });
    });
    setRings('next', prog.next ? [nodeRect(prog.next)] : [], 'ring-next', 5);
    $('qz-count').textContent = prog.shown + ' of ' + prog.total + ' shown';
    $('qz-next').disabled = !prog.next;
    $('qz-all').disabled = !prog.next;
    $('qz-reset').disabled = prog.shown === 0;
    return prog;
  }
  function reveal(id) {
    if (!id || state.shown[id]) return;
    state.shown[id] = true;
    var prog = renderQuiz();
    scrollToRect(nodeRect(id), true);
    announce(data.nodes[id].t + '. ' + prog.shown + ' of ' + prog.total + ' shown.');
  }
  function revealAll(on) {
    L.quizOrder(data, graph, state.scope).forEach(function (id) {
      if (on) state.shown[id] = true; else delete state.shown[id];
    });
    var prog = renderQuiz();
    announce(on ? 'All ' + prog.total + ' boxes shown.' : 'All ' + prog.total + ' boxes covered again.');
  }

  /* ---------------------------------------------------------------- controls */
  Object.keys(modeBtn).forEach(function (k) { modeBtn[k].addEventListener('click', function () { setMode(k); }); });
  Object.keys(sizeBtn).forEach(function (k) { sizeBtn[k].addEventListener('click', function () { setSize(k, true); }); });
  $('pv-prev').addEventListener('click', function () { go(state.step - 1); });
  $('pv-next').addEventListener('click', function () { go(state.step + 1); });
  $('pv-gone').addEventListener('click', function () { state.later = 'gone'; renderPresent(false); announce('Later stages hidden.'); });
  $('pv-fade').addEventListener('click', function () { state.later = 'fade'; renderPresent(false); announce('Later stages faded.'); });

  var full = $('pv-full');
  if (!(document.fullscreenEnabled && shell.requestFullscreen)) full.hidden = true;
  full.addEventListener('click', function () {
    if (document.fullscreenElement) { document.exitFullscreen().catch(function () {}); return; }
    var p = shell.requestFullscreen();
    if (p && p.catch) p.catch(function () { full.hidden = true; announce('Full screen is not available in this view.'); });
  });
  document.addEventListener('fullscreenchange', function () {
    var on = document.fullscreenElement === shell;
    full.textContent = on ? 'Exit full screen' : 'Full screen';
    full.setAttribute('aria-pressed', String(on));
  });

  var pick = $('tr-pick'), scopeSel = $('qz-scope');
  var reading = L.readingCmp(data);
  data.stages.forEach(function (s) {
    var ids = data.order.filter(function (id) { return data.nodes[id].s === s.id; }).sort(reading);
    if (!ids.length) return;
    var og = document.createElement('optgroup');
    og.label = s.title;
    ids.forEach(function (id) {
      var o = document.createElement('option');
      o.value = id;
      o.textContent = short(data.nodes[id].t, 90);
      og.appendChild(o);
    });
    pick.appendChild(og);
    var so = document.createElement('option');
    so.value = s.id;
    so.textContent = s.title;
    scopeSel.appendChild(so);
  });
  pick.addEventListener('change', function () { if (pick.value) select(pick.value, 'list'); else clearTrace(); });
  ['tr-d1', 'tr-d2', 'tr-d3', 'tr-d0'].forEach(function (id) {
    $(id).addEventListener('click', function () {
      state.depth = +$(id).getAttribute('data-depth');
      renderTrace();
      announce(state.depth ? 'Showing up to ' + state.depth + (state.depth === 1 ? ' step.' : ' steps.') : 'Showing the whole chain.');
    });
  });
  $('tr-clear').addEventListener('click', clearTrace);
  $('tp-body').addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('button.ci') : null;
    if (b) select(b.getAttribute('data-id'), 'panel');
  });
  scopeSel.addEventListener('change', function () {
    state.scope = scopeSel.value;
    var prog = renderQuiz();
    announce('Covering ' + (state.scope ? STAGE[state.scope].title : 'all stages') + ': ' + prog.shown + ' of ' + prog.total + ' shown.');
  });
  $('qz-next').addEventListener('click', function () { reveal(L.quizProgress(L.quizOrder(data, graph, state.scope), state.shown).next); });
  $('qz-all').addEventListener('click', function () { revealAll(true); });
  $('qz-reset').addEventListener('click', function () { revealAll(false); });

  /* the figure: clicks, keys on focused boxes and cards, focus rings, swipes */
  function hit(target, cls) {
    var n = target;
    while (n && n !== paper) {
      if (n.classList && n.classList.contains(cls)) return n;
      n = n.parentNode;
    }
    return null;
  }
  paper.addEventListener('click', function (e) {
    if (state.mode === 'trace') {
      var g = hit(e.target, 'node');
      if (g) select(g.getAttribute('data-node'), 'figure');
    } else if (state.mode === 'quiz') {
      var c = hit(e.target, 'qcard');
      if (c) reveal(c.getAttribute('data-q'));
    }
  });
  paper.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var g = state.mode === 'trace' ? hit(e.target, 'node') : null, c = state.mode === 'quiz' ? hit(e.target, 'qcard') : null;
    if (!g && !c) return;
    e.preventDefault();
    if (g) select(g.getAttribute('data-node'), 'figure');
    else {
      reveal(c.getAttribute('data-q'));
      // the revealed card leaves the tab order: carry focus to the next covered box, or back to the frame
      var nextId = L.quizProgress(L.quizOrder(data, graph, state.scope), state.shown).next, moved = false;
      figs.forEach(function (f) {
        if (nextId && f.svg.getBoundingClientRect().width > 0 && f.cards[nextId].focus) { f.cards[nextId].focus(); moved = true; }
      });
      if (!moved) paper.focus();
    }
  });
  paper.addEventListener('focusin', function (e) {
    var t = hit(e.target, 'node') || hit(e.target, 'qcard');
    var visible = false;
    try { visible = !!t && t.matches(':focus-visible'); } catch (err) { visible = !!t; }
    if (!visible) { setRings('focus', [], 'ring-focus', 0); return; }
    setRings('focus', [nodeRect(t.getAttribute('data-node') || t.getAttribute('data-q'))], 'ring-focus', 4);
  });
  paper.addEventListener('focusout', function () { setRings('focus', [], 'ring-focus', 0); });

  var swipe = null;
  paper.addEventListener('pointerdown', function (e) {
    if (state.mode !== 'present' || state.size === 'actual' || e.pointerType === 'mouse') return;
    swipe = {x: e.clientX, y: e.clientY, t: Date.now(), id: e.pointerId};
  });
  paper.addEventListener('pointerup', function (e) {
    if (!swipe || e.pointerId !== swipe.id) return;
    var dx = e.clientX - swipe.x, dy = e.clientY - swipe.y, dt = Date.now() - swipe.t;
    swipe = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy) && dt < 800) go(state.step + (dx < 0 ? 1 : -1));
  });
  paper.addEventListener('pointercancel', function () { swipe = null; });

  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target, tag = t && t.tagName ? String(t.tagName).toLowerCase() : '';
    if (tag === 'input' || tag === 'select' || tag === 'textarea' || (t && t.isContentEditable)) return;
    if (state.mode === 'present') {
      var to = {ArrowRight: state.step + 1, PageDown: state.step + 1, ArrowLeft: state.step - 1, PageUp: state.step - 1,
                Home: 0, End: STEPS.length - 1}[e.key];
      if (to !== undefined) { e.preventDefault(); go(to); }
    } else if (state.mode === 'trace' && e.key === 'Escape' && state.sel) {
      e.preventDefault();
      clearTrace();
    }
  });

  /* start in Read mode, at the viewer's remembered size */
  setSize(state.sizePref, false);
  setMode('read');
  live.textContent = '';
  window.FigPage = {state: state, steps: STEPS, setMode: setMode, go: go, select: select, reveal: reveal, revealAll: revealAll,
                    setSize: setSize, figs: figs};
})();
