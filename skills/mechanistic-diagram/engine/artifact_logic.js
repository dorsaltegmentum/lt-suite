// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Mechanistic diagram Artifact: the page's pure logic (no DOM), shared by the page and its tests.

   make_artifact.py inlines this file into the page ahead of artifact_ui.js; tests/diagram/artifact_logic.test.js (lt-suite repository)
   loads it with require(). Everything works on the page's embedded figure data:

     data.nodes   {id: {t: text, r: role, s: stage, b: bold, x, y, w, h}}   x, y: the box's top-left corner
     data.order   node ids in declaration order
     data.edges   [{a, b, k}]  in the SVG's order (edge-I is data.edges[I]); k: stim | req | inh | excl
     data.stages  [{id, title, n, rect: [x0, y0, x1, y1]}]  in story order; stages with the same n form one step

   The functions return plain objects; the page turns them into data-v attributes and overlay rects. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FigLogic = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var FINDING = {sign: true, inv: true};

  /* Adjacency both ways: out[id] = [{id: target, kind, i: edge index}], inn[id] = [{id: source, kind, i}]. */
  function buildGraph(data) {
    var out = {}, inn = {};
    Object.keys(data.nodes).forEach(function (id) { out[id] = []; inn[id] = []; });
    data.edges.forEach(function (e, i) {
      if (!(e.a in out) || !(e.b in out)) return;
      out[e.a].push({id: e.b, kind: e.k, i: i});
      inn[e.b].push({id: e.a, kind: e.k, i: i});
    });
    return {out: out, inn: inn};
  }

  function limitOf(depth) {
    return (depth == null || !(depth > 0)) ? Infinity : depth;     // 0, null or "all": no limit
  }

  /* Breadth-first from start over adj, at most depth steps. Returns {dist: {id: steps}} without start. */
  function walk(adj, start, depth) {
    var limit = limitOf(depth), dist = {}, seen = {}, frontier = [start], d = 0;
    seen[start] = true;
    while (frontier.length && d < limit) {
      d += 1;
      var next = [];
      for (var f = 0; f < frontier.length; f++) {
        var links = adj[frontier[f]] || [];
        for (var j = 0; j < links.length; j++) {
          var v = links[j].id;
          if (!seen[v]) { seen[v] = true; dist[v] = d; next.push(v); }
        }
      }
      frontier = next;
    }
    return dist;
  }

  /* Every upstream cause and downstream consequence of id, transitively, within depth steps (0 = all).
     Returns {sel, depth, up: {id: steps}, down: {id: steps}, edges: [edge indices on a path to or from sel]}.
     An edge u -> v is on the upstream chain when v reaches sel in d steps, u is upstream and d + 1 <= depth,
     and the mirror for downstream; so every highlighted arrow lies on a highlighted path. */
  function trace(graph, id, depth) {
    if (!(id in graph.out)) return null;
    var limit = limitOf(depth);
    var up = walk(graph.inn, id, depth), down = walk(graph.out, id, depth), edges = {};
    function upD(n) { return n === id ? 0 : up[n]; }
    function downD(n) { return n === id ? 0 : down[n]; }
    Object.keys(graph.inn).forEach(function (v) {
      var dv = upD(v);
      if (dv === undefined) return;
      graph.inn[v].forEach(function (l) {
        if (l.id !== id && up[l.id] !== undefined && dv + 1 <= limit) edges[l.i] = true;
      });
    });
    Object.keys(graph.out).forEach(function (u) {
      var du = downD(u);
      if (du === undefined) return;
      graph.out[u].forEach(function (l) {
        if (l.id !== id && down[l.id] !== undefined && du + 1 <= limit) edges[l.i] = true;
      });
    });
    delete up[id];
    delete down[id];
    return {sel: id, depth: limit === Infinity ? 0 : limit, up: up, down: down,
            edges: Object.keys(edges).map(Number).sort(function (a, b) { return a - b; })};
  }

  /* The words for an arrow, read from either end (the key's wording: causes or leads to, normal anatomy or
     physiology it depends on, evidence argues against it, treatment blocks it). A finding that points into a
     diagnosis supports it rather than causing it. */
  function verb(data, kind, fromId, toId, backward) {
    var from = data.nodes[fromId], to = data.nodes[toId];
    if (kind === 'stim' && to && to.r === 'dx' && from && FINDING[from.r]) return backward ? 'supported by' : 'supports';
    var V = {stim: ['leads to', 'follows from'], req: ['is needed for', 'depends on'],
             inh: ['blocks', 'is blocked by'], excl: ['argues against', 'is argued against by']}[kind];
    return V ? V[backward ? 1 : 0] : (backward ? 'linked from' : 'links to');
  }

  function stageRank(data) {
    var r = {};
    data.stages.forEach(function (s, i) { r[s.id] = i; });
    return r;
  }

  /* Reading order: story order of the stages, then top to bottom (box centres), then left to right. */
  function readingCmp(data) {
    var rank = stageRank(data);
    return function (a, b) {
      var A = data.nodes[a], B = data.nodes[b];
      return ((rank[A.s] || 0) - (rank[B.s] || 0)) || (A.y + A.h / 2 - B.y - B.h / 2) || (A.x - B.x)
        || (a < b ? -1 : a > b ? 1 : 0);
    };
  }

  /* The trace as the side panel lists it: upstream farthest first, then downstream nearest first; each entry
     names the arrows that put it on the chain ({kind, other, verb}), nearest link first. */
  function chain(data, graph, tr) {
    if (!tr) return null;
    var onPath = {};
    tr.edges.forEach(function (i) { onPath[i] = true; });
    var cmp = readingCmp(data);
    function dist(map, id) { return id === tr.sel ? 0 : map[id]; }
    function side(map, upstream) {
      var groups = {};
      Object.keys(map).forEach(function (id) { (groups[map[id]] = groups[map[id]] || []).push(id); });
      var ds = Object.keys(groups).map(Number).sort(function (a, b) { return upstream ? b - a : a - b; });
      return ds.map(function (d) {
        return {dist: d, items: groups[d].sort(cmp).map(function (id) {
          var links = (upstream ? graph.out[id] : graph.inn[id]).filter(function (l) {
            return onPath[l.i] && dist(map, l.id) !== undefined;
          }).sort(function (x, y) { return dist(map, x.id) - dist(map, y.id) || x.i - y.i; });
          return {id: id, links: links.map(function (l) {
            return {kind: l.kind, other: l.id, verb: upstream ? verb(data, l.kind, id, l.id, false)
                                                              : verb(data, l.kind, l.id, id, true)};
          })};
        })};
      });
    }
    return {sel: tr.sel, up: side(tr.up, true), down: side(tr.down, false),
            nUp: Object.keys(tr.up).length, nDown: Object.keys(tr.down).length};
  }

  /* Present steps: stages in story order; consecutive stages that share a number n are shown together.
     The last step is the whole diagram. */
  function steps(data) {
    var out = [];
    data.stages.forEach(function (s) {
      var last = out[out.length - 1];
      if (last && s.n != null && last.n === s.n) last.stages.push(s.id);
      else out.push({n: s.n == null ? null : s.n, stages: [s.id]});
    });
    out.push({n: null, stages: [], whole: true});
    return out;
  }

  function stageStep(st) {
    var m = {};
    st.forEach(function (step, k) { step.stages.forEach(function (id) { m[id] = k; }); });
    return m;
  }

  /* View states for present step k ("later": "gone" hides later stages, "fade" fades them).
     States: "" normal (current stage and the whole-diagram step), "past" (earlier stages), and the later state.
     An arrow takes the later of its two ends; it is normal when either end is in the current step. */
  function presentView(data, st, k, later) {
    var laterState = later === 'fade' ? 'fade' : 'gone';
    var whole = !st[k] || st[k].whole;
    var at = stageStep(st);
    function stateOf(sk) {
      if (whole || sk === undefined || sk === k) return '';
      return sk < k ? 'past' : laterState;
    }
    var view = {nodes: {}, edges: [], stages: {}, focus: whole ? [] : st[k].stages.slice()};
    data.stages.forEach(function (s) { view.stages[s.id] = stateOf(at[s.id]); });
    Object.keys(data.nodes).forEach(function (id) { view.nodes[id] = stateOf(at[data.nodes[id].s]); });
    data.edges.forEach(function (e, i) {
      var a = data.nodes[e.a], b = data.nodes[e.b];
      var sa = a ? at[a.s] : undefined, sb = b ? at[b.s] : undefined;
      if (whole || sa === undefined || sb === undefined) { view.edges[i] = ''; return; }
      var m = Math.max(sa, sb);
      view.edges[i] = m > k ? laterState : (sa === k || sb === k) ? '' : 'past';
    });
    return view;
  }

  /* View states for a trace: the chain and the selected box stay, everything else is "dim". */
  function traceView(data, tr) {
    var view = {nodes: {}, edges: [], stages: {}, focus: []};
    data.stages.forEach(function (s) { view.stages[s.id] = ''; });
    var on = {}, path = {};
    if (tr) {
      on[tr.sel] = true;
      Object.keys(tr.up).forEach(function (id) { on[id] = true; });
      Object.keys(tr.down).forEach(function (id) { on[id] = true; });
      tr.edges.forEach(function (i) { path[i] = true; });
    }
    Object.keys(data.nodes).forEach(function (id) { view.nodes[id] = !tr || on[id] ? '' : 'dim'; });
    data.edges.forEach(function (e, i) { view.edges[i] = !tr || path[i] ? '' : 'dim'; });
    return view;
  }

  /* Longest-path depth from a source (a box nothing points to); boxes on a cycle keep the depth reached. */
  function depths(data, graph) {
    var ids = Object.keys(data.nodes), indeg = {}, depth = {}, queue = [];
    ids.forEach(function (id) {
      indeg[id] = graph.inn[id].length; depth[id] = 0;
      if (!indeg[id]) queue.push(id);
    });
    for (var q = 0; q < queue.length; q++) {
      var u = queue[q];
      graph.out[u].forEach(function (l) {
        depth[l.id] = Math.max(depth[l.id], depth[u] + 1);
        indeg[l.id] -= 1;
        if (!indeg[l.id]) queue.push(l.id);
      });
    }
    return depth;
  }

  /* Quiz order: stages in story order; within a stage, causes before effects (a topological order of the
     stage's own arrows, taking the top-left box first whenever several are free), with a treatment after the
     box it blocks. A cycle is broken at its top-left box. scope: a stage id, or "" / null for every stage. */
  function quizOrder(data, graph, scope) {
    var out = [];
    data.stages.forEach(function (s) {
      if (scope && s.id !== scope) return;
      var ids = data.order.filter(function (id) { return data.nodes[id].s === s.id; });
      var inStage = {}, indeg = {}, next = {};
      ids.forEach(function (id) { inStage[id] = true; indeg[id] = 0; next[id] = []; });
      ids.forEach(function (id) {
        graph.out[id].forEach(function (l) {
          if (!inStage[l.id] || l.id === id) return;
          var from = l.kind === 'inh' ? l.id : id, to = l.kind === 'inh' ? id : l.id;   // blocked box first
          next[from].push(to);
          indeg[to] += 1;
        });
      });
      var left = ids.slice(), pos = function (a, b) {          // by centre: boxes on one grid row share it
        var A = data.nodes[a], B = data.nodes[b];
        return (A.y + A.h / 2 - B.y - B.h / 2) || (A.x - B.x) || (a < b ? -1 : a > b ? 1 : 0);
      };
      while (left.length) {
        var free = left.filter(function (id) { return !indeg[id]; });
        var pick = (free.length ? free : left).sort(pos)[0];
        out.push(pick);
        left.splice(left.indexOf(pick), 1);
        indeg[pick] = -1;
        next[pick].forEach(function (v) { if (indeg[v] > 0) indeg[v] -= 1; });
      }
    });
    return out;
  }

  /* Quiz progress for a scope: {total, shown, next: the first hidden box in quiz order or null}. */
  function quizProgress(order, shown) {
    var n = 0, next = null;
    order.forEach(function (id) {
      if (shown[id]) n += 1;
      else if (next === null) next = id;
    });
    return {total: order.length, shown: n, next: next};
  }

  /* The rectangle a present step frames when zoomed: its stages' union plus a margin (whole canvas otherwise). */
  function focusRect(data, stageIds, margin) {
    var m = margin == null ? 24 : margin, box = null;
    data.stages.forEach(function (s) {
      if (stageIds.indexOf(s.id) < 0 || !s.rect) return;
      var r = s.rect;
      box = box ? [Math.min(box[0], r[0]), Math.min(box[1], r[1]), Math.max(box[2], r[2]), Math.max(box[3], r[3])] : r.slice();
    });
    var vb = data.vb || [0, 0, data.W, data.H];
    if (!box) return vb.slice();
    var x0 = Math.max(vb[0], box[0] - m), y0 = Math.max(vb[1], box[1] - m);
    var x1 = Math.min(vb[0] + vb[2], box[2] + m), y1 = Math.min(vb[1] + vb[3], box[3] + m);
    return [x0, y0, x1 - x0, y1 - y0];
  }

  /* An extra layer element follows what it names: a box (data-node), an arrow (data-a and data-b) or a stage. */
  function extraState(view, data, ref) {
    if (ref.node != null) return view.nodes[ref.node] || '';
    if (ref.a != null && ref.b != null) {
      for (var i = 0; i < data.edges.length; i++) {
        if (data.edges[i].a === ref.a && data.edges[i].b === ref.b) return view.edges[i] || '';
      }
      var sa = view.nodes[ref.a] || '', sb = view.nodes[ref.b] || '';
      return sa === 'gone' || sb === 'gone' ? 'gone' : sa === 'fade' || sb === 'fade' ? 'fade'
        : sa === 'dim' || sb === 'dim' ? 'dim' : sa || sb;
    }
    if (ref.stage != null) return view.stages[ref.stage] || '';
    return '';
  }

  return {buildGraph: buildGraph, walk: walk, trace: trace, verb: verb, chain: chain, steps: steps,
          presentView: presentView, traceView: traceView, depths: depths, quizOrder: quizOrder,
          quizProgress: quizProgress, focusRect: focusRect, extraState: extraState, readingCmp: readingCmp};
});
