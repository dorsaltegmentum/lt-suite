// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* lt-module-capture page script. It reads a single Lt run from the page's Knockout model.
 *
 * Rendered by `ltcore/ltjs.py render capture`, which prepends the never-submit guard and
 * defines the frozen `__LT_ARGS__` object, then run in the user's signed-in Lt tab on a
 * CONTENT page of the run (`/run/page/<n>` or `/run/background`). The rendered file is the
 * body of an async function: the last line returns the receipt.
 *
 * __LT_ARGS__ (every key optional):
 *   transport           'sink' (POST to the loopback sink) or 'download' (browser download)
 *   sink                loopback base URL printed by sink.py, e.g. http://127.0.0.1:8731
 *   token               session secret printed by sink.py; used locally for HMAC proofs
 *   module_only         true for a re-capture: post module-<id> only, never the reference
 *   drop_text_patterns  regexes (list or JSON list) for author text that is not stimulus
 *   post_ms             abort a sink POST after this many milliseconds
 *
 * Safety rails, each one load-bearing:
 *  - This file never reads panelState.modelAnswer. Lt sends its answer key to the browser,
 *    and module.json must stay free of it; the per-plugin handler allow-list enforces that.
 *    An option object in `content` holds just `answer`, its visible text. Which option is
 *    correct is recorded in modelAnswer alone, so copying option text leaks no key.
 *  - Four members of $root commit work: commitClicked, setCheckpointCommitState,
 *    removeCommittedCheckpoints and removePagesAfterUncommittedCheckpoints. Each is a plain
 *    function of one argument, and JavaScript runs it happily with none. Code that calls
 *    every function it finds, to unwrap values, would therefore commit the run. Here only
 *    explicitly named members are read, and nothing is unwrapped by a generic loop.
 *  - On a view-model panel, pluginKey and panelKey are FUNCTIONS (not observables) and are
 *    called. lessonTitle is a React element; the plain string is lessonTitleDisplay.
 *  - Nothing is written, clicked or navigated at any point.
 *
 * Output: module-<id> and reference-<id>. The reference keeps the student's answers from
 * the FIRST capture only; sink.py replies 409 when one exists. For a re-capture after a
 * fill, module_only is set, and then neither route sends any reference bytes.
 */

const LT_CAPTURE_DEFAULTS = Object.freeze({
  transport: 'sink',
  sink: 'http://127.0.0.1:8731',
  drop_text_patterns: ['^Copyright ©'],
  post_ms: 30000
});

/* Argument normalisation. The renderer may pass a value typed or as a string, so this is
 * the single place where every value gets coerced. */
const ltCaptureOptions = (opts) => {
  const baked = (typeof __LT_ARGS__ === 'object' && __LT_ARGS__) ? __LT_ARGS__ : {};
  // One spelling per key before merging, so a call argument always wins over a baked one.
  const snake = x => {
    const y = Object.assign({}, x || {});
    if (y.moduleOnly !== undefined && y.module_only === undefined) y.module_only = y.moduleOnly;
    delete y.moduleOnly;
    return y;
  };
  const g = Object.assign({}, snake(baked), snake(opts));
  const bool = v => v === true || v === 1 || v === '1' || v === 'true' || v === 'yes';
  const num = (v, d) => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : d; };
  const list = (v, d) => {
    if (Array.isArray(v)) return v.map(String);
    if (typeof v === 'string' && v.trim().startsWith('[')) {
      try { const x = JSON.parse(v); if (Array.isArray(x)) return x.map(String); } catch (e) { /* fall through */ }
    }
    if (typeof v === 'string' && v.length) return [v];
    return d;
  };
  const patterns = list(g.drop_text_patterns, LT_CAPTURE_DEFAULTS.drop_text_patterns);
  const drop = [];
  const badPatterns = [];
  for (const p of patterns) {
    try { drop.push(new RegExp(p)); } catch (e) { badPatterns.push(p); }
  }
  return {
    moduleOnly: bool(g.module_only),
    transport: String(g.transport || LT_CAPTURE_DEFAULTS.transport) === 'download' ? 'download' : 'sink',
    sink: String(g.sink || LT_CAPTURE_DEFAULTS.sink).replace(/\/+$/, ''),
    token: String(g.token || ''),
    drop, badPatterns,
    postMs: num(g.post_ms, LT_CAPTURE_DEFAULTS.post_ms)
  };
};

/* The sink receives the student's own answers, so the page may only send them to loopback.
 * A rendered script that names any other host is refused before anything is read. */
const ltIsLoopbackSink = (u) => {
  try {
    const x = new URL(u);
    return x.protocol === 'http:' && x.hostname === '127.0.0.1'
      && x.pathname.replace(/\/+$/, '') === '' && !x.search && !x.hash && !x.username && !x.password;
  } catch (e) { return false; }
};

/* The sink proves possession of the secret before the page sends any content.
 * Each upload then authenticates its exact destination and bytes without sending the secret. */
const ltSinkSession = async (base, token, postMs) => {
  const bytes = s => new TextEncoder().encode(s);
  const hex = b => Array.from(new Uint8Array(b), n => n.toString(16).padStart(2, '0')).join('');
  const key = await crypto.subtle.importKey('raw', bytes(token), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
  const nonce = hex(crypto.getRandomValues(new Uint8Array(16)));
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), postMs);
  try {
    const res = await fetch(`${base}/hello?n=${nonce}`, {
      method: 'GET', mode: 'cors', credentials: 'omit', cache: 'no-store', signal: ac.signal
    });
    if (!res.ok) throw new Error('sink proof refused');
    const {proof} = await res.json();
    if (typeof proof !== 'string' || !/^[0-9a-f]{64}$/.test(proof)) throw new Error('invalid sink proof');
    const signature = Uint8Array.from(proof.match(/../g), n => parseInt(n, 16));
    const valid = await crypto.subtle.verify('HMAC', key, signature,
      bytes(`lt-suite-sink|${nonce}|${location.origin}`));
    if (!valid) throw new Error('wrong sink proof');
  } finally { clearTimeout(timer); }
  return async (name, body) => {
    const raw = typeof body === 'string' ? bytes(body) : await body.arrayBuffer();
    const digest = hex(await crypto.subtle.digest('SHA-256', raw));
    return hex(await crypto.subtle.sign('HMAC', key, bytes(`${name}|${digest}`)));
  };
};

const captureLtRun = async (opts) => {
  const o = ltCaptureOptions(opts);
  const moduleOnly = o.moduleOnly;
  if (o.transport === 'sink' && !ltIsLoopbackSink(o.sink)) {
    return { ok: false, reason: 'The sink address must be http://127.0.0.1:<port>; nothing was read.' };
  }
  if (o.transport === 'sink' && !o.token) {
    return { ok: false, reason: 'No sink token. Start sink.py and pass its token to the renderer (--arg token=...), or render with transport=download.' };
  }
  let authenticate;
  if (o.transport === 'sink') {
    try { authenticate = await ltSinkSession(o.sink, o.token, o.postMs); }
    catch (e) { return {ok: false, fatal: 'sink-unauthenticated'}; }
  }
  const el = document.querySelector('div.pageList');
  if (!el || typeof ko === 'undefined') {
    return { ok: false, reason: 'No Lt lesson view model on this page (wrong tab, or Lt is still loading).' };
  }
  const ctx = (() => { try { return ko.contextFor(el); } catch (e) { return null; } })();
  if (!ctx || !ctx.$root) {
    return { ok: false, reason: 'No Lt lesson view model on this page (wrong tab, or Lt is still loading).' };
  }
  const root = ctx.$root;

  /* readers: begin (tests/capture/handlers.test.js slices from here to "readers: end") */
  const U = v => (ko.isObservable(v) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const models = c => { const x = U(c); return x && x.models ? x.models : (Array.isArray(x) ? x : []); };

  /* Authored prose arrives as HTML. Plain textContent would glue paragraphs together, so
   * each block boundary turns into a newline before the tags go. */
  let hiddenTextDropped = 0;
  const S = value => {
    let h = String(value ?? '').replace(/\p{Cf}/gu, '')
      .replace(/&#(x[0-9a-f]+|[0-9]+);?/gi, (entity, code) => {
        const point = code[0].toLowerCase() === 'x' ? parseInt(code.slice(1), 16) : Number(code);
        return point <= 0x10ffff && /\p{Cf}/u.test(String.fromCodePoint(point)) ? '' : entity;
      })
      .replace(/&(?:shy|zwnj|zwj|lrm|rlm|ZeroWidthSpace|NoBreak|ApplyFunction|af|InvisibleTimes|it|InvisibleComma|ic|NegativeMediumSpace|NegativeThickSpace|NegativeThinSpace|NegativeVeryThinSpace);/g, '');
    if (h.includes('<') || h.includes('&')) {
      const doc = new DOMParser().parseFromString(h, 'text/html');
      let removed = 0;
      const visit = parent => {
        for (const node of Array.from(parent.childNodes)) {
          if (node.nodeType === 8) { node.remove(); removed++; continue; }
          if (node.nodeType !== 1) continue;
          const style = String(node.getAttribute('style') || '').replace(/\/\*[\s\S]*?\*\//g, '');
          const css = property => node.style ? node.style.getPropertyValue(property).trim().toLowerCase()
            : ((style.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, 'i')) || [])[1] || '')
              .replace(/!important/gi, '').trim().toLowerCase();
          const hidden = node.hasAttribute('hidden') || node.getAttribute('aria-hidden')?.toLowerCase() === 'true'
            || css('display') === 'none' || css('visibility') === 'hidden'
            || /^(?:0|0px|0em|0rem|0%)$/.test(css('font-size')) || /^0(?:\.0+)?$/.test(css('opacity'));
          if (hidden || ['script', 'style', 'template', 'noscript'].includes(node.localName)) {
            node.remove(); removed++;
          } else visit(node);
        }
      };
      visit(doc);
      hiddenTextDropped += removed;
      // Keep the historical formatter byte-for-byte for normal authored markup.
      // A modified tree is serialized only after unsafe subtrees have been discarded.
      if (removed) h = (doc.body?.innerHTML || '').replace(/\u00a0/g, ' ').replace(/\u2019/g, "'");
    }
    return String(h)
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\s*li[^>]*>/gi, '\n- ')
    .replace(/<\s*\/\s*(p|div|li|ul|ol|h[1-6]|tr|table|blockquote)\s*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&#39;|&rsquo;/g, "'").replace(/&quot;/g, '"').replace(/&#43;/g, '+')
    .replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  };

  const suspectText = record => {
    const strings = value => typeof value === 'string' ? [value]
      : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];
    const text = strings(record).join('\n').replace(/\p{Cf}/gu, '');
    return [
      ['instruction-override', /\b(?:ignore|disregard|forget)\b[\s\S]{0,40}\b(?:instructions?|rules|prompt)\b/i],
      ['agent-address', /\byou are (?:an?|the) (?:ai|assistant|agent|model)\b/i],
      ['agent-imperative', /\b(?:ai|assistant|agent|model)[,:]?\s+(?:please\s+)?(?:run|execute|call|use|open|send|ignore|reveal|write|delete)\b/i],
      ['tool-reference', /\b(?:evaluate_script|javascript_tool|navigate_page|powershell|Invoke-WebRequest)\b|\b(?:curl|wget)\s|\bbash\s+-c\b/i],
      ['commit-route', /run\/complete/i]
    ].filter(([, pattern]) => pattern.test(text)).map(([reason]) => reason);
  };
  const decorate = (record, before) => {
    if (hiddenTextDropped > before) record.hidden_text_dropped = hiddenTextDropped - before;
    const flags = suspectText(record);
    if (flags.length) record.suspect_text = flags;
  };

  /* A table row has one of two shapes, and nothing signals which. One shape uses `cells`;
   * the other uses `headerRow` with `columns`, each column {headerColumn, content, ...}.
   * Looking at `cells` only gives an empty grid, so a completed table would look blank.
   * Header rows and header columns come from the author and never count as answers. */
  const cellsOf = row => { const ra = A(row) || {}; const c = models(ra.cells); return c.length ? c : models(ra.columns); };
  const cellText = cl => { const ca = A(cl) || {}; return S(U(ca.content ?? ca.text ?? '')); };
  const grid = coll => models(coll).map(row => ({
    header: U((A(row) || {}).headerRow) === true,
    cells: cellsOf(row).map(cl => ({ text: cellText(cl), header: U((A(cl) || {}).headerColumn) === true }))
  }));
  const gridBody = g => g.filter(r => !r.header);
  const gridText = g => gridBody(g).map(r => r.cells.map(c => c.text).join(' | '))
    .filter(s => s.replace(/[ |]/g, '').length).join('\n');

  /* Grid as the AUTHOR wrote it, for a table-question panel. The student's view, `inp.rows`,
   * also shows labels the author typed into plain body cells, and Lt does not mark those as
   * headers. `content.rows` holds the author's version in the same row shapes. Since
   * `content` is the lesson definition every student shares, learner text is not expected
   * there (so far it was only seen on panels without answers). Three outcomes guard
   * against a wrong "unanswered": `absent` (no content.rows), `same-object` (content.rows
   * is inp.rows itself) and `shape-mismatch` (row or cell counts differ). Each of them
   * falls back to counting all non-empty body cells, the side that favours answered. */
  const authorGridOf = (c, inp, g) => {
    const raw = U(c.rows);
    if (raw === undefined || raw === null) return { status: 'absent' };
    if (raw === U(inp.rows)) return { status: 'same-object' };
    const a = grid(c.rows);
    if (!a.length) return { status: 'absent' };
    const fits = a.length === g.length && a.every((r, ri) => r.cells.length === g[ri].cells.length);
    return fits ? { status: 'read', grid: a } : { status: 'shape-mismatch' };
  };

  /* Hotspot panels. `content.hotspots` lists a CUSTOM entry for the base image
   * (imageResourceKey, parentHotspot null) and a POINT entry for every popup: labelTitle,
   * labelDescription, hotspotColour, and `position`, which is a JSON STRING of fractions
   * like '{"points":[{"x":0.05,"y":0.106}]}'. These panels accept no answer. Only named
   * members are read. If `position` fails to parse, the popup stays with `points: null`. */
  const hotspotList = coll => {
    const ms = models(coll);
    return ms.length ? ms : (Array.isArray(U(coll)) ? U(coll) : []);
  };
  const pointsOf = v => {
    const raw = U(v);
    let p = raw;
    if (typeof raw === 'string') { try { p = JSON.parse(raw); } catch (e) { return null; } }
    const pts = p && Array.isArray(p.points) ? p.points : null;
    return pts ? pts.filter(q => q && q.x != null && q.y != null && q.x !== '' && q.y !== '')
                    .map(q => ({ x: Number(q.x), y: Number(q.y) }))
                    .filter(q => Number.isFinite(q.x) && Number.isFinite(q.y)) : null;
  };
  // In popup text, a line that contains just U+00A0 marks a paragraph break.
  const T = v => S(String(U(v) ?? '').replace(/ /g, ' '));
  const readHotspots = c => hotspotList(c.hotspots).map(h => {
    const a = A(h) || {};
    const parent = U(a.parentHotspot);
    return {
      id: U(a.id) ?? null,
      parent_id: parent === undefined ? null : parent,
      hotspot_type: S(U(a.hotspotType)) || null,
      colour: S(U(a.hotspotColour)) || null,
      title: T(a.labelTitle),
      text: T(a.labelDescription),
      image_key: String(U(a.imageResourceKey) || '') || null,
      alt: S(U(a.altTextDescription)) || null,
      points: pointsOf(a.position)
    };
  });

  /* Returns the first non-empty string among `keys` together with the key that held it.
   * Labelling plugins keep their text in `label`. Nobody has documented the inner field
   * names of `categories`, so several likely names are tried and the one that matched goes
   * into the note. Because the guess is written down, the next real capture checks it. */
  const pick = (o, keys) => {
    const a = A(o) || {};
    for (const k of keys) { const v = S(U(a[k])); if (v.length) return { k, v }; }
    return { k: null, v: '' };
  };
  const LABEL_KEYS = ['label', 'text', 'name', 'title'];
  // `title` first: that is where the category text has been found. The rest are fallbacks.
  const CAT_KEYS = ['title', 'category', 'label', 'name', 'text'];

  /* Classifies a categories answer collection. An EMPTY one reveals no shape. A filled one
   * whose entries lack labelAnswerItems is a shape not yet observed, and calling both of
   * these "flat" would mask that case. When only part of the entries are nested, the shape
   * is also unobserved, and the answered test falls back to the collection length. */
  const catShape = cans => {
    if (cans.length === 0) return 'empty-collection';
    const nested = cans.filter(x => U(x.labelAnswerItems) !== undefined).length;
    return nested === cans.length ? 'nested-labelAnswerItems'
      : nested === 0 ? 'flat-unmeasured' : 'mixed-unmeasured';
  };

  /* LABEL POOL, written out SORTED. In kcImageLabelling, drag-and-drop and categories the
   * draggable labels sit in `content.labelItems[].label`. A label item has only {label}, a
   * location item has only geometry, and no field links the two. The panels still mark
   * answers instantly, so the browser must hold the key somewhere, and the likeliest place
   * is the position of each labelItem relative to labelLocationItems. Copying the pool in
   * its original order might thus put Lt's key into module.json. Sorting keeps the words
   * and breaks that link. At fill time a label is found by its text on the live page; an
   * index taken from a capture is never used. */
  const poolOf = texts => texts
    .filter(t => t && t.length)
    .slice()
    .sort()
    .map((t, i) => ({ option_id: `o${i + 1}`, text: t }));
  const labelPool = coll => poolOf(models(coll).map(x => S(U(A(x).label))));

  /* Two pluginKeys, one plugin: Image Labeling and Drag-and-Drop Labeling share
   * labelItems / labelLocationItems / linkedLabels and the write call
   * `currentInputModel().addAnswer(labelIndex, locationIndex)`. A single builder makes both
   * readers, so they stay identical. */
  const labelling = (key, type) => ({
    [key]: (c, inp) => {
      const labels = models(c.labelItems).map(x => S(U(A(x).label)));
      const links = models(inp.linkedLabels)
        .map(x => A(x) || {})
        .filter(x => U(x.locationIndex) != null && U(x.labelIndex) != null);
      const raw = Number(U(c.presentedRatio) ?? 0);
      return {
        type, prompt: S(U(c.question)), answered: links.length > 0,
        media: [{ kind: 'image', key: String(U(c.imageResourceKey) || '') || null,
                  alt: S(U(c.altTextDescription)) || null, url: null, width: null, height: null, local_path: null }],
        /* The note carries the number of locations, because addAnswer's second argument is
         * an index into labelLocationItems; the validator parses `locations=<n>` here. */
        constraints: { options_count: labels.length,
                       note: (raw > 0 ? `presentedRatio=${raw.toFixed(4)}` : 'presentedRatio unavailable')
                           + ` locations=${models(c.labelLocationItems).length}` },
        options: labelPool(c.labelItems),
        ref: {
          student_answer: links.map(x => `${labels[U(x.labelIndex)] ?? '?'} -> location ${U(x.locationIndex)}`).join('\n'),
          note: `linked=${links.length}/${labels.length}`
        }
      };
    }
  });

  /* Allow-list of readers, keyed by answerable pluginKey. A reader returns the item fields
   * specific to its type and the student's answer (for reference.json only). A pluginKey
   * missing here is captured as `unmapped` with degrade-todo: the item is listed as manual
   * work and nothing is silently skipped. `answered` always comes from a length or an
   * emptiness check. Truthiness is never used: presentedRatio 0 is no answer, and neither
   * is an option string resolved from an index. */
  const HANDLERS = {
    'text-question': (c, inp) => {
      const s = S(U(inp.answer));
      return { type: 'text-response', prompt: S(U(c.question)), answered: s.length > 0, ref: { student_answer: s } };
    },

    'select': (c, inp) => {
      /* input.selectedAnswers is an array of option INDICES that start at 0. The answered
       * test counts those numbers; mapping them to option text is for display. A count of
       * mapped strings once made every completed multiple-choice item look unanswered. */
      const opts = models(c.answers).map((o, i) => ({ option_id: `o${i + 1}`, text: S(U(A(o).answer)) }));
      const idx = models(inp.selectedAnswers).filter(n => Number.isInteger(n));
      const sel = idx.map(i => (opts[i] || {}).text || `option ${i + 1}`);
      return {
        type: 'multiple-choice', prompt: S(U(c.question)), answered: idx.length > 0,
        options: opts,
        constraints: { options_count: opts.length, note: U(c.multiselect) === true ? 'multiselect' : 'single-select' },
        ref: { student_answer: sel.join('; '), student_selection: sel }
      };
    },

    'table-question': (c, inp) => {
      const g = grid(inp.rows);
      const body = gridBody(g);
      /* Row and column counts do not tell a drafter what each column wants. Header cells are
       * written by the author, so they are copied into module.json next to prompt_text.
       * Body cells hold the learner's entries: module.json gets the `answered` boolean only. */
      const colHeaders = (g.find(r => r.header) || { cells: [] }).cells.map(x => x.text);
      const rowHeaders = body.map(r => r.cells.filter(x => x.header).map(x => x.text));
      const cons = { rows: g.length, cols: g.length ? g[0].cells.length : 0 };
      if (colHeaders.some(t => t.length)) cons.column_headers = colHeaders;
      if (rowHeaders.some(a => a.some(t => t.length))) cons.row_headers = rowHeaders;

      /* When a body cell matches the author's cell at the same position, the text belongs to
       * the author and is not an answer. Every other non-empty body cell is learner work, an
       * edited pre-filled cell included. */
      const ag = authorGridOf(c, inp, g);
      const authored = (ri, ci) => {
        if (ag.status !== 'read') return false;
        const a = ag.grid[ri].cells[ci];
        const x = g[ri].cells[ci];
        return !x.header && a.text.length > 0 && a.text === x.text;
      };
      const bodyIdx = g.map((r, ri) => (r.header ? -1 : ri)).filter(ri => ri >= 0);

      /* Text the author put in body cells is stimulus, the same as column_headers, and comes
       * from content.rows alone. `row` and `col` count over the WHOLE grid with header rows,
       * matching how table_cells in answers.json and the fill step locate a cell. */
      if (ag.status === 'read') {
        const ac = [];
        bodyIdx.forEach(ri => ag.grid[ri].cells.forEach((a, ci) => {
          if (!g[ri].cells[ci].header && a.text.length) ac.push({ row: ri, col: ci, text: a.text });
        }));
        if (ac.length) cons.authored_cells = ac;
      }

      /* `filled_map` has a string per body row with one character per cell: '#' learner
       * text, 'a' text identical to the author's cell, '.' empty, 'h' a cell with Lt's
       * headerColumn flag. Answered means at least one '#'. The map shows which cells hold
       * something and never what they hold. With no author grid, an author label in an
       * unmarked body cell shows as '#', which leans `answered` towards true. Skipping a
       * table by mistake costs a TODO; calling it empty by mistake invites an overwrite. */
      const map = bodyIdx.map(ri => g[ri].cells.map((x, ci) =>
        x.header ? 'h' : (authored(ri, ci) ? 'a' : (x.text.length ? '#' : '.'))).join(''));
      const answered = map.some(s => s.includes('#'));
      const ev = { filled_map: map, author_grid: {
        status: ag.status, source: 'content.rows',
        authored_cells: map.reduce((n, s) => n + s.split('').filter(ch => ch === 'a').length, 0)
      } };

      /* Raises a FLAG and decides nothing. A single body column with text in all body rows
       * fits author row labels, and it equally fits a real answer in a one-column table.
       * The flag therefore records its basis and keeps `answered` as computed. It applies
       * only when no author grid was read, since the grid already marks author cells 'a'. */
      const heuristics = ag.status !== 'read';
      const nCols = g.length ? g[0].cells.length : 0;
      const filledCols = [];
      for (let ci = 0; ci < nCols; ci++) {
        const col = body.map(r => r.cells[ci]).filter(Boolean);
        if (col.length && col.every(x => !x.header && x.text.length)) filledCols.push(ci);
      }
      const anyOther = body.some(r => r.cells.some((x, ci) => !x.header && x.text.length && !filledCols.includes(ci)));
      if (heuristics && body.length > 1 && nCols > 1 && filledCols.length === 1 && !anyOther) {
        ev.answered_suspect = {
          basis: `text appears in body column ${filledCols[0]} alone, in each of the ${body.length} body rows; author row labels and a one-column answer both produce this`,
          note: 'Flagged, not decided. `answered` keeps its value. Check the table against the author\'s version before counting its text as learner work.'
        };
      }

      /* Shape three: the author typed headers into body row 0 and labels into body column 0,
       * and Lt marks neither as a header. Row 0 then has text beyond column 0, so the check
       * above passes a grid with an empty interior. The authored text forms an L shape (top
       * row plus left column). As above: record the basis, leave `answered` alone. */
      if (heuristics && !ev.answered_suspect && body.length > 1 && nCols > 1) {
        const filled = [];
        body.forEach((r, ri) => r.cells.forEach((x, ci) => {
          if (!x.header && x.text.length) filled.push([ri, ci]);
        }));
        const onlyL = filled.length > 0 && filled.every(([ri, ci]) => ri === 0 || ci === 0);
        const topFull = body[0].cells.every((x, ci) => ci === 0 || x.header || x.text.length);
        // The corner of an L is EMPTY, so neither side requires text at [0][0].
        const leftFull = body.every((r, ri) => ri === 0 || !r.cells[0] || r.cells[0].header || r.cells[0].text.length);
        if (onlyL && topFull && leftFull) {
          ev.answered_suspect = {
            basis: `text sits only in body row 0 and body column 0; both are complete and neither carries Lt's header flag; interior ${body.length - 1}x${nCols - 1} holds no text, so the text looks like an author L shape`,
            note: 'Flagged, not decided. `answered` keeps its value. In `filled_map`, an interior made only of dots means the table has NO answer, whatever `answered` says.'
          };
        }
      }

      return {
        type: 'table-completion', prompt: S(U(c.question)), answered,
        constraints: cons, answered_evidence: ev,
        ref: { student_answer: gridText(g), note: `rows=${g.length} body_rows=${body.length}` }
      };
    },

    /* One reader serves both annotation modes. inputMode tells them apart; pluginKey is the
     * same for both. The panel can hold TWO images. `c` is the AUTHOR side (the fixed image
     * on an annotate-only panel); `inp` is the STUDENT side (the file put into an
     * upload-and-annotate slot). Geometry describes whichever image is the TARGET: the
     * learner's slot on upload panels, the author's picture on annotate-only panels.
     *
     * presentedRatio = naturalHeight / naturalWidth of the image Lt stored. The on-screen
     * width over the natural width is a different number: it is layout scale and changes
     * with window size and zoom. Lt shrinks an upload to 1920 px on its longest side before
     * saving, so the fill step reads the ratio after an upload. Lt reports 0 for an empty
     * slot; 0 is no ratio and 1 would be made up, so capture writes null. */
    'image-annotation': (c, inp) => {
      const mode = U(c.inputMode);
      const upload = mode === 'allowImageAndAnnotation';
      const type = upload ? 'upload-and-annotate' : 'image-annotation';
      const ann = U(inp.annotations) || [];
      const geomSrc = upload ? inp : c;
      const raw = Number(U(geomSrc.presentedRatio) ?? 0);
      const known = Number.isFinite(raw) && raw > 0;

      const authorKey = String(U(c.predefinedImageKey) || U(c.imageResourceKey) || '') || null;
      const studentKey = String(U(inp.imageKey) || '') || null;
      const alt = S(U(c.altTextDescription)) || null;
      const media = [];
      if (authorKey) {
        media.push({ kind: 'image', role: 'author-supplied', key: authorKey,
                     alt, url: null, width: null, height: null, local_path: null });
      }
      if (studentKey) {
        media.push({ kind: 'image', role: 'student-upload', key: studentKey,
                     alt: null, url: null, width: null, height: null, local_path: null });
      }
      if (!media.length) {
        media.push({ kind: 'image', role: upload ? 'student-upload' : 'author-supplied', key: null,
                     alt, url: null, width: null, height: null, local_path: null });
      }

      /* The answered rule depends on inputMode. Under allowAnnotation, annotations make up
       * the answer. Under allowImageAndAnnotation, an uploaded picture without annotations is
       * already learner work. Counting only annotations there would call a submitted image
       * an empty panel, and a fill trusting that would replace it. */
      const answered = upload ? (ann.length > 0 || studentKey !== null) : ann.length > 0;

      return {
        type, prompt: S(U(c.questionText)),      // questionText, not question
        answered,
        input_mode: upload ? 'allowImageAndAnnotation' : 'allowAnnotation',
        media,
        target_geometry: {
          presented_ratio: known ? raw : null,
          accepts_upload: upload,
          geometry_source: known ? 'captured' : 'unavailable-until-presented',
          image_key: upload ? studentKey : authorKey,
          annotations_key: String(U(inp.annotationsKey) || '') || null
        },
        automation_override: known ? null : 'degrade-todo',
        ref: {
          student_answer: ann.map(a => {
            const x = A(a) || {};
            const pos = A(x.position) || {};
            return `${S(U(x.type)) || 'mark'}${S(U(x.text)) ? ': ' + S(U(x.text)) : ''}` +
                   ` @(${(+U(pos.x) || 0).toFixed(3)},${(+U(pos.y) || 0).toFixed(3)})`;
          }).join('\n'),
          note: `student_image=${studentKey ? 'yes' : 'no'} annotations=${ann.length}`
        }
      };
    },

    /* kcImageLabelling and drag-and-drop: same data shape, same write call. Only the CSS
     * class in Lt's markup differs (`module-dragAndDrop`). Nothing here uses it, because
     * panels are located by panelKey() and never by class. */
    ...labelling('kcImageLabelling', 'image-labeling'),
    ...labelling('drag-and-drop', 'drag-and-drop-labeling'),

    /* `categories` (Categorization). The author defines content.categoryItems and
     * content.labelItems. The learner's placements live in input.categoryAnswerItems, one
     * entry per category, each with labelAnswerItems. An answer can be empty in two ways:
     * no entries at all, or entries whose labelAnswerItems are empty. Counting placements
     * handles both. If entries have no labelAnswerItems key, the collection length decides,
     * so an unobserved shape leans to answered (only an "empty" verdict permits a write). */
    'categories': (c, inp) => {
      const cats = models(c.categoryItems).map(x => pick(x, CAT_KEYS).v);
      const labels = models(c.labelItems).map(x => pick(x, LABEL_KEYS).v);
      const cans = models(inp.categoryAnswerItems).map(x => A(x) || {});
      const shape = catShape(cans);
      const placed = cans.map(x => models(x.labelAnswerItems));
      const n = placed.reduce((t, a) => t + a.length, 0);
      // Which key carried the text, recorded rather than assumed.
      const keys = [pick(models(c.categoryItems)[0], CAT_KEYS).k,
                    pick(models(c.labelItems)[0], LABEL_KEYS).k];
      return {
        type: 'categorization', prompt: S(U(c.question)),
        answered: shape === 'nested-labelAnswerItems' ? n > 0 : cans.length > 0,
        constraints: { options_count: labels.length,
                       /* Category names keep their on-screen order. Labels have no
                        * positional tie to categories, and only the labels get sorted. */
                       categories: cats.filter(t => t && t.length),
                       note: `categories=${cats.length} labels=${labels.length} `
                           + `shape=${shape} answer_items=${cans.length} `
                           + `text_keys=${keys.map(k => k || 'none').join('/')}` },
        options: poolOf(labels),
        ref: {
          student_answer: cans.map((x, i) => {
            const into = cats[i] ?? `category ${i}`;
            const got = placed[i].map(l => {
              const li = U((A(l) || {}).labelIndex);
              return labels[li] ?? (pick(l, LABEL_KEYS).v || `label ${li}`);
            });
            return got.length ? `${into}: ${got.join(', ')}` : null;
          }).filter(Boolean).join('\n'),
          note: `placed=${n}/${labels.length} categories=${cats.length}`
        }
      };
    }
  };

  /* All three drag-style types are written through `currentInputModel().addAnswer(...)`, the
   * widget's keyboard route, so they count as structured paths. Picking a panel by CSS class
   * instead could hit an already answered panel on the same page.
   *
   * HANDLERS has no `notebook` entry on purpose. A notebook panel is private to the learner,
   * and no lt-suite skill writes to it. It is captured as `unmapped` with a permanent degrade
   * and the note below; the module loses one item and nothing else. */
  const NOTEBOOK_NOTE = 'pluginKey notebook: a notebook panel private to the learner. lt-suite '
    + 'never writes to it: the item is recorded as unmapped (permanent degrade) and remains '
    + 'manual work. Its input format is unknown, and capture reads none of it.';
  const AUTOM = {
    'text-response': 'structured-path', 'multiple-choice': 'structured-path',
    'table-completion': 'resilient-selector', 'image-annotation': 'resilient-selector',
    'upload-and-annotate': 'structured-path', 'image-labeling': 'structured-path',
    'drag-and-drop-labeling': 'structured-path', 'categorization': 'structured-path',
    'notebook': 'structured-path', 'subpage': 'structured-path', 'unmapped': 'degrade-todo'
  };
  /* Drag-style items that allow one attempt stay manual: the riskiest write path would sit
   * right on a commit boundary. */
  const DND = new Set(['image-labeling', 'drag-and-drop-labeling', 'categorization']);
  /* readers: end */
  const ASSESS = new Set(['noAssessment', 'instantFeedback', 'graded']);

  const refItems = [];
  const keepText = t => !o.drop.some(re => re.test(t));

  const walk = (p, pageIdx, out, parentId) => {
    /* Panels come in two shapes with no marker between them. Via page.allPanels a panel is
     * a view-model object, and pluginKey and panelKey are FUNCTIONS to call. Via a subpage
     * it is a bare Backbone model: those names are ATTRIBUTES, and panelState sits under
     * .attributes. A() gives both the same interface. */
    const pa = A(p) || {};
    let pk;
    try { pk = typeof p.pluginKey === 'function' ? String(p.pluginKey()) : String(U(pa.pluginKey)); }
    catch (e) { pk = 'unknown'; }
    let key;
    try { key = typeof p.panelKey === 'function' ? String(p.panelKey()) : String(U(pa.panelKey)); }
    catch (e) { key = `${pageIdx}-${out.items.length}-${out.content.length}`; }
    if (!key || key === 'undefined') key = `${pageIdx}-${out.items.length}-${out.content.length}`;
    const st = U(pa.panelState);
    if (!st) return;
    const c = A(st.content) || {};
    const id = `p${pageIdx}-${key}`;

    /* The same panel may turn up TWICE. After Lt displays a subpage, it also lists the
     * subpage's children in the page's allPanels, so the subpage recursion and the allPanels
     * loop meet the same child and the census would double it. Only the FIRST record stays;
     * the recursion gets there first, and its record has parent_item_id set. */
    if (out.seen.has(id)) return;
    out.seen.add(id);
    const hiddenBefore = hiddenTextDropped;
    const contentBefore = out.content.length;

    /* A subpage holds other panels and asks nothing itself. The page's allPanels lists the
     * children only after the subpage was shown once, so the walk descends on its own into
     * content.subpages[].rows[].cells[].panels[]. */
    if (pk === 'subpage') {
      out.items.push({
        item_id: id, panel_key: key, plugin_key: pk, type: 'subpage', prompt_text: '',
        attempts: 'multi', assessment_type: 'noAssessment', commit_state: 'open',
        automation: 'structured-path', answered: false, parent_item_id: parentId || null
      });
      models(c.subpages).forEach(sp => models(A(sp).rows).forEach(r =>
        models(A(r).cells).forEach(cell => {
          const ca = A(cell) || {};
          models(ca.panels).forEach(child => walk(child, pageIdx, out, id));
          // Columns may sit inside a cell, next to its panels.
          models(ca.columns).forEach(col => models(A(col).panels)
            .forEach(child => walk(child, pageIdx, out, id)));
        })));
      return;
    }

    /* `input` present in panelState: the panel takes an answer. Absent: it is content. */
    if (!('input' in st)) {
      if (pk === 'kcTextArea') {
        const t = S(U(c.theText));                       // theText, not text/content/html
        if (t && keepText(t)) out.content.push({ panel_key: key, plugin_key: pk, kind: 'text', text: t });
      } else if (pk === 'image') {
        out.content.push({
          panel_key: key, plugin_key: pk, kind: 'image', text: S(U(c.caption)),
          media: [{ kind: 'image', key: String(U(c.imageResourceKey) || '') || null,
                    alt: S(U(c.altTextDescription)) || null, url: null, width: null, height: null, local_path: null }]
        });
      } else if (pk === 'table') {
        // An author-content table is stimulus, header row included.
        const g = grid(c.rows);
        const t = g.map(r => r.cells.map(x => x.text).join(' | '))
                   .filter(x => x.replace(/[ |]/g, '').length).join('\n');
        if (t) out.content.push({ panel_key: key, plugin_key: pk, kind: 'other', text: t });
      } else if (['divider', 'whitespace', 'button', 'video', 'audio'].includes(pk)) {
        out.content.push({ panel_key: key, plugin_key: pk, kind: pk === 'whitespace' ? 'whitespace' : pk });
      } else if (pk === 'hotspot') {
        /* Picture plus popups. `text` concatenates each popup's title and description,
         * `hotspots` keeps the structured list, and the base picture is listed in `media`;
         * fetch_media.js and the exporter then handle it as one more author image. */
        const hs = readHotspots(c);
        const keys = [...new Set(hs.map(h => h.image_key).filter(Boolean))];
        const popups = hs.filter(h => h.title || h.text);
        out.content.push({
          panel_key: key, plugin_key: pk, kind: 'hotspot',
          text: popups.map(h => (h.title && h.text ? `${h.title}: ${h.text}` : (h.title || h.text))).join('\n\n'),
          media: keys.map(k => ({ kind: 'image', key: k,
                                  alt: (hs.find(h => h.image_key === k) || {}).alt || null,
                                  url: null, width: null, height: null, local_path: null })),
          hotspots: hs,
          capture_note: `hotspot reader: ${keys.length} image key(s); ${hs.length} hotspot(s), `
            + `${popups.length} of them carrying popup text.`
        });
      } else {
        /* Content panel of a plugin this file cannot read. The record lists the NAMES of its
         * content keys and none of the values. Someone writing a reader later can start from
         * the capture and needs no live session. */
        let names = [];
        try { names = Object.keys(c).slice(0, 20); } catch (e) { names = []; }
        out.content.push({
          panel_key: key, plugin_key: pk, kind: 'other',
          capture_note: `capture.js has no reader for pluginKey ${pk}. The panel accepts no answer `
            + `(no input key in panelState), and its content stayed unread. Content keys: `
            + `${names.length ? names.join(', ') : '(none)'}.`
        });
      }
      if (out.content.length > contentBefore) decorate(out.content.at(-1), hiddenBefore);
      return;
    }

    const isNotebook = pk === 'notebook';
    const inp = A(st.input) || {};
    const h = Object.prototype.hasOwnProperty.call(HANDLERS, pk) ? HANDLERS[pk] : null;
    const r = h ? h(c, inp) : null;
    const type = r ? r.type : 'unmapped';
    const attempts = U(c.singleAttempt) === true ? 'single' : 'multi';
    const at = String(U(c.assessmentType) || 'noAssessment');

    const item = {
      item_id: id, panel_key: key, plugin_key: pk, type,
      prompt_text: r ? r.prompt : S(U(c.question ?? c.questionText)),
      attempts,
      // Unknown values pass through so the schema rejects them loudly.
      assessment_type: ASSESS.has(at) ? at : at,
      maximum_grade: U(c.maximumGrade) ?? null,
      commit_state: U(inp.locked) === true ? 'locked' : 'open',
      /* Once a checkpoint is committed, panels ahead of it report isReadOnly=true but have
       * no `inp.locked`, and nothing at run level separates the two situations. The value
       * is true, false, or null where the panel offers no flag; null means unknown and
       * blocks writing. The flag is looked up in three places because only one of them has
       * ever been seen to hold it. */
      read_only: (() => {
        for (const v of [U(pa.isReadOnly), U(st.isReadOnly), U(c.isReadOnly)]) {
          if (v === true || v === false) return v;
        }
        return null;
      })(),
      automation: 'degrade-todo',
      answered: r ? r.answered : false,
      parent_item_id: parentId || null
    };
    if (r) {
      if (r.input_mode) item.input_mode = r.input_mode;
      if (r.media) item.media = r.media;
      if (r.options) item.options = r.options;
      if (r.constraints) item.constraints = r.constraints;
      if (r.answered_evidence) item.answered_evidence = r.answered_evidence;
      if (r.target_geometry) item.target_geometry = r.target_geometry;
      item.automation = r.automation_override
        || ((attempts === 'single' && DND.has(type)) ? 'degrade-todo' : (AUTOM[type] || 'degrade-todo'));
    } else if (isNotebook) {
      item.capture_note = NOTEBOOK_NOTE;
    } else {
      item.capture_note = `The capture allow-list has no entry for pluginKey ${pk}; lt-suite has no tested way to write it`;
    }

    /* A degrade-todo item gets a degrade block, and every reason belongs to exactly one
     * class. There is deliberately no default branch: an unforeseen route to degrade-todo
     * leaves the block out, and the validator then rejects the module. */
    if (item.automation === 'degrade-todo') {
      const tg = item.target_geometry || {};
      let deg = null;
      if (type === 'unmapped' && isNotebook) {
        deg = { class: 'permanent', reason: 'notebook-never-written',
                note: 'Notebook panels are never written by lt-suite. This TODO stays whatever capture learns.' };
      } else if (type === 'unmapped') {
        deg = { class: 'lifts-on-capture-support', reason: 'unmapped-type',
                note: 'capture.js has no reader for this pluginKey yet. Adding one makes the item automatable.' };
      } else if (attempts === 'single' && DND.has(type)) {
        deg = { class: 'permanent', reason: 'single-attempt-drag',
                note: 'Drag-style items with one attempt stay manual: the riskiest write path would sit on a commit boundary.' };
      } else if (tg.accepts_upload === false) {
        deg = { class: 'permanent', reason: 'panel-accepts-no-upload',
                note: 'In allowAnnotation mode the learner annotates the picture Lt provides; uploads are not possible.' };
      } else if (tg.accepts_upload === true && tg.presented_ratio === null) {
        deg = { class: 'lifts-at-fill', reason: 'geometry-unknown-until-presented',
                note: 'Nothing is uploaded yet, so there is no stored picture and no ratio. presentedRatio '
                    + 'comes from the uploaded file itself (Lt scales uploads to 1920 px on the longest side, '
                    + 'with some rounding), so the fill step reads it after uploading. The on-screen '
                    + 'width over natural width is layout scale and a different number.' };
      }
      if (deg) item.degrade = deg;
    }

    decorate(item, hiddenBefore);
    out.items.push(item);
    if (item.answered && r) refItems.push(Object.assign({ item_id: id, maximum_grade: item.maximum_grade }, r.ref));
  };

  const m = location.pathname.match(/\/i\/([^/]+)\/student\/courses\/(\d+)\/runs\/(\d+)/) || [];
  if (!m.length) {
    return { ok: false, reason: 'The tab is not on an Lt run URL (/i/<instance>/student/courses/<c>/runs/<r>/...).' };
  }
  const pages = root.lessonFlowPages();
  const modPages = [];
  let pno = 0;

  pages.forEach((pg, pi) => {
    /* Structure, not the title, identifies the report page: its page object owns a
     * reportInfoPanels property and has no rows/numberOfPanels. */
    const isReport = 'reportInfoPanels' in pg;

    /* Checkpoint pages are one-way doors without panels. No other page shape has
     * `committed`/`commitLoading` while lacking `allPanels`, so that test identifies them;
     * the title "Checkpoint" is never compared. U() unwraps the observables `committed` and
     * `index`. The four commit FUNCTIONS are plain functions, and U() never calls them. */
    const isCheckpoint = !isReport && 'committed' in pg && !('allPanels' in pg);
    const isContent = !isReport && !isCheckpoint;
    if (isContent) pno++;

    const out = { content: [], items: [], seen: new Set() };
    if (isContent) (U(pg.allPanels) || []).forEach(p => walk(p, pi + 1, out, null));

    /* Checkpoints live at /run/checkpoint/<n> and have no /run/page/<n> address; their
     * index() is a different number again. Like the report page, a checkpoint therefore gets
     * page_no null and no url, since any URL here would be a guess. */
    const page = {
      page_index: pi + 1,
      page_no: isContent ? pno : null,
      route: isReport ? 'complete' : (isCheckpoint ? 'checkpoint' : null),
      url: isContent ? `${location.origin}${location.pathname.replace(/\/run\/.*$/, '/run')}/page/${pno}` : null,
      title: S(U(pg.title)), is_report_page: isReport, is_checkpoint: isCheckpoint,
      content: out.content, items: out.items   // the report page repeats earlier panels, so its items stay empty
    };
    if (isCheckpoint) {
      page.checkpoint = { index: U(pg.index) ?? null, committed: U(pg.committed) === true };
    }
    modPages.push(page);
  });

  /* Possible readiness test page: FLAG it, decide nothing. A page made only of
   * multiple-choice items could be a team test (answers come from a folder the user
   * provides) or an individual one (closed-book learner work). The view model looks the
   * same for both, and guessing from the title is the mistake this avoids. Captures are
   * never edited afterwards, so the verdict goes into answers.json, not here. */
  const allMcqPages = modPages.filter(p => p.items.length >= 2 && p.items.every(i => i.type === 'multiple-choice'));
  if (allMcqPages.length === 1) {
    const p = allMcqPages[0];
    p.rat_candidate = {
      basis: `this page holds ${p.items.length} items, every one multiple-choice; no other page in the module is like that`,
      note: 'Flagged, not decided. Team tests take their answers from the folder set in rat.answers_dir. '
          + 'Individual tests are closed-book learner work. The config keys rat.trat and rat.irat set the handling '
          + 'of each. The view model cannot distinguish them: decide from the text shown on this page and write the '
          + 'verdict to answers.json, because a capture stays as captured. Without a verdict the page is left out '
          + 'and listed by name in the report; it is neither drafted nor dropped quietly.'
    };
  }

  const byType = {};
  let captured = 0;
  modPages.forEach(p => p.items.forEach(i => { captured++; byType[i.type] = (byType[i.type] || 0) + 1; }));
  const now = new Date().toISOString();
  const single = modPages.flatMap(p => p.items.filter(i => i.attempts === 'single').map(i => i.item_id));
  const hasCompletion = (typeof root.hasCompletionPage === 'function' ? root.hasCompletionPage() : U(root.hasCompletionPage)) === true;
  const G = typeof kuraCloudGlobals !== 'undefined' ? kuraCloudGlobals : {};

  /* lessonFlowPages loses a checkpoint once it is COMMITTED: Lt removes the page from the
   * flow and shows what follows it. The page-shape test above then finds no live door, which
   * is right, although the module did have one. Lt still lists the checkpoint under
   * `root.checkpointPageContainer.pages`.
   *
   * Hence two lists. `checkpoints` is the full INVENTORY; `checkpoint_pages` holds only the
   * LIVE doors. Merging them would be wrong: a fill halts before a checkpoint page, and
   * halting before a door already opened would end the run early with no benefit. Whether
   * the container lists UNCOMMITTED checkpoints too is not known yet, so entries from both
   * readers are combined and `source` says where each came from.
   *
   * `checkpoints_container_present` distinguishes "container read, empty" from "container
   * unreadable". Accessing `.pages` only reads: U() calls an observableArray to unwrap it,
   * and it never gets near the commit functions, which are plain functions. */
  const cpc = root.checkpointPageContainer;
  const containerPresent = !!cpc && typeof cpc === 'object' && 'pages' in cpc;
  const containerPages = containerPresent ? (U(cpc.pages) || []) : [];
  const checkpoints = [];
  const cpByIndex = new Map();
  modPages.filter(p => p.is_checkpoint).forEach(p => {
    const e = { index: p.checkpoint.index, page_index: p.page_index,
                committed: p.checkpoint.committed, in_flow: true, source: 'lessonFlowPages' };
    checkpoints.push(e);
    if (e.index !== null) cpByIndex.set(e.index, e);
  });
  containerPages.forEach(cp => {
    const idx = U(cp.index) ?? null;
    const committed = U(cp.committed) === true;
    const seen = idx === null ? null : cpByIndex.get(idx);
    if (seen) { seen.source = 'both'; return; }   // same door, two readers
    const e = { index: idx, page_index: null, committed, in_flow: false,
                source: 'checkpointPageContainer' };
    checkpoints.push(e);
    if (idx !== null) cpByIndex.set(idx, e);
  });
  checkpoints.sort((a, b) => (a.index ?? 1e9) - (b.index ?? 1e9));

  /* MIRROR panels, which cannot be edited. A team-based module may show tables from earlier
   * pages again later, for the learner to consult while writing. Such copies reject input,
   * yet the panel gives no sign of it: `read_only` is null, and because Lt creates a cell
   * editor only when a cell is clicked, the markup matches a writable table. Detection
   * compares across pages:
   *   - type, prompt_text and constraints equal those of a panel on an EARLIER page (never
   *     the same page);
   *   - answered_evidence equals that panel's, whether filled or empty;
   *   - read_only is null on this panel and a boolean on the earlier one.
   * The result is a FLAG ONLY; `read_only` keeps its measured value. Prose types count too. */
  const MIRRORABLE = new Set(['table-completion', 'text-response']);
  const markMirrors = (ps) => {
    const firstSeen = new Map();
    const J = (v) => JSON.stringify(v ?? null);
    for (const pg of ps) {
      for (const it of pg.items) {
        // Without prompt text any two panels would look alike, so such panels are skipped.
        if (!MIRRORABLE.has(it.type) || !it.prompt_text) continue;
        const k = J([it.type, it.prompt_text, it.constraints]);
        const src = firstSeen.get(k);
        if (!src) { firstSeen.set(k, { page: pg, item: it }); continue; }
        // A repeat on one page is just a repeated question. Mirrors live on later pages.
        if (src.page.page_index >= pg.page_index) continue;
        const basis = ['duplicate-prompt-and-constraints'];
        if (J(it.answered_evidence) === J(src.item.answered_evidence)) {
          basis.push(it.answered ? 'identical-content' : 'both-empty');
        }
        if (it.read_only === null && typeof src.item.read_only === 'boolean') {
          basis.push('read-only-flag-absent-here-present-on-source');
        }
        it.mirror_of = {
          item_id: src.item.item_id, page_index: src.page.page_index, basis,
          note: 'Flagged, not decided. `read_only` keeps its value and can still be null. '
              + 'Handle the panel as a non-editable copy of the item named here and draft no '
              + 'answer for it. A click on one of its cells, by the user, settles the question.'
        };
      }
    }
  };
  markMirrors(modPages);

  const module = {
    schema_version: 1, module_id: `c${m[2]}-r${m[3]}`, instance: m[1],
    course_id: +m[2], run_id: +m[3], lesson_id: +(G.pageLessonId || 0),
    source_url: location.href.split('?')[0], captured_at: now, capture_method: 'view-model',
    lesson: {
      title: S(U(root.lessonTitleDisplay)),                // lessonTitle holds a React element
      course_title: S(G.pageCourseLongName || G.pageCourseShortName || ''),
      page_count: pages.length,
      has_completion_page: hasCompletion,
      run_locked: U(root.runLocked) === true,
      show_page_menu_page_remaining: U(root.showPageMenuPageRemaining) === true,
      remaining_flow_pages_without_checkpoints: U(root.remainingFlowPagesWithoutCheckpoints) ?? 0,
      completion_route: hasCompletion ? 'complete' : null
    },
    commit_boundaries: {
      checkpoint_pages: modPages.filter(p => p.is_checkpoint).map(p => p.page_index),
      checkpoints,
      checkpoints_container_present: containerPresent,
      single_attempt_items: single,
      run_level_commit: {
        present: hasCompletion, route: hasCompletion ? 'complete' : null,
        note: 'Read from hasCompletionPage; capture never opened the completion page.'
      },
      unmapped: modPages.flatMap(p => p.items.filter(i => i.type === 'unmapped')
        .map(i => ({ page_index: p.page_index, item_id: i.item_id, reason: i.capture_note })))
    },
    census: {
      pages_total: modPages.length, captured_total: captured,
      answerable_total: captured - (byType.subpage || 0), by_type: byType,
      confirmed_by: 'walk of lessonFlowPages panels, recursing into subpages; answerable means an input key exists in panelState'
    },
    pages: modPages
  };

  // Format controls can also arrive in plain labels or metadata outside HTML readers.
  const stripFormats = value => {
    if (typeof value === 'string') return value.replace(/\p{Cf}/gu, '');
    if (Array.isArray(value)) return value.map(stripFormats);
    if (value && typeof value === 'object') {
      for (const key of Object.keys(value)) value[key] = stripFormats(value[key]);
    }
    return value;
  };
  stripFormats(module);
  stripFormats(refItems);

  const reference = {
    schema_version: 1, module_id: module.module_id, run_id: module.run_id, captured_at: now,
    provenance_of_reference: 'student-submitted', never_load_into_drafting_context: true,
    items: refItems
  };

  /* TRANSPORT. Documents travel from the page to disk and never through the agent's browser
   * tool. Submitted answers thus cannot reach the agent's context: the quarantine follows
   * from the data path itself. The tool receives a short summary only.
   *
   * Route 1, the loopback sink. Browsers treat http://127.0.0.1 as potentially trustworthy,
   * so no mixed-content rule blocks it. Chromium asks the user once per site before a public
   * page may reach a local address; until someone answers, the first request just waits.
   * Each POST therefore has an AbortController deadline (post_ms).
   * Only for the Lt origin does the sink accept the Private Network Access preflight, and it
   * verifies the X-Lt-Suite-Auth body proof on every POST.
   *
   * Route 2, the download folder, uses a Blob with `<a download>`. Data still goes straight
   * from page to disk, and no shell sandbox can get in the way (a sandboxed shell may own a
   * separate loopback that the page cannot reach). collect_downloads.py picks the files up
   * afterwards. Chromium permits ONE scripted download per site and silently drops later
   * ones until the user allows automatic downloads, so the caller verifies every file.
   * Chromium also adds " (1)" to a taken name instead of replacing the file.
   *
   * A refusal from the sink is a real reply, so it does not trigger the download route. For
   * a reference, 409 says the single permitted copy is on disk already; downloading anyway
   * would sneak a second copy past that rule. */
  const saveViaDownload = (name, body) => {
    const file = `${name}.json`;
    const url = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = file;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    return { name, route: 'download', ok: true, handed_off: true, file, bytes: new Blob([body]).size };
  };

  const post = async (name, doc) => {
    const body = JSON.stringify(doc);
    if (o.transport === 'download') return saveViaDownload(name, body);
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), o.postMs);
    try {
      const res = await fetch(`${o.sink}/${name}`, {
        method: 'POST', mode: 'cors', signal: ac.signal, credentials: 'omit',
        headers: { 'Content-Type': 'application/json', 'X-Lt-Suite-Auth': await authenticate(name, body) },
        body
      });
      const ok = res.ok === undefined ? (res.status >= 200 && res.status < 300) : !!res.ok;
      const r = { name, route: 'sink', status: res.status, ok, bytes: new Blob([body]).size };
      if (ok) {
        // The sink replies with the name it saved under (module-<id>.json or a numbered copy).
        try {
          const j = typeof res.json === 'function' ? await res.json() : null;
          if (j && typeof j.file === 'string') r.file = j.file.slice(0, 120);
        } catch (e) { /* the status is the answer */ }
      } else {
        // A refusal text is written by the sink, never taken from the page, so returning it is safe.
        let why = '';
        try { why = typeof res.text === 'function' ? String(await res.text()).slice(0, 300) : ''; }
        catch (e) { why = ''; }
        r.refused = true;
        r.reason = why || `HTTP ${res.status}`;
      }
      return r;
    } catch (e) {
      const sink_error = `${e && e.name}: ${String((e && e.message) || e).slice(0, 120)}`;
      return {name, route: 'sink', ok: false, sink_error};
    } finally { clearTimeout(timer); }
  };

  /* The module is sent first and reported in its own entry. A refused reference therefore
   * cannot cost the module file. */
  const transport = [await post(`module-${module.module_id}`, module)];
  transport.push(moduleOnly
    ? { name: `reference-${module.module_id}`, route: 'skipped', ok: true,
        note: 'module_only: no reference sent. The reference from the first capture stays the only one.' }
    : await post(`reference-${module.module_id}`, reference));

  const tables = modPages.flatMap(p => p.items).filter(i => i.type === 'table-completion');
  const gridStatus = {};
  tables.forEach(i => {
    const s = ((i.answered_evidence || {}).author_grid || {}).status || 'none';
    gridStatus[s] = (gridStatus[s] || 0) + 1;
  });

  return {
    ok: !!transport[0].ok,               // the module document reached the sink or the browser
    reference_ok: !!transport[1].ok,
    transport,
    module_only: moduleOnly,
    bad_drop_patterns: o.badPatterns,
    untrusted_flags: (() => {
      const ids = modPages.flatMap(p => [
        ...p.items.filter(i => i.suspect_text?.length).map(i => i.item_id),
        ...p.content.filter(c => c.suspect_text?.length).map(c => `p${p.page_index}-${c.panel_key}`)
      ]);
      return {count: ids.length, item_ids: ids};
    })(),
    summary: {
      module_id: module.module_id, lesson_id: module.lesson_id, title: module.lesson.title,
      pages: modPages.length, captured, answerable: module.census.answerable_total,
      by_type: byType, answered: refItems.length,
      single_attempt: single.length, run_locked: module.lesson.run_locked,
      checkpoints: checkpoints.length, checkpoints_committed: checkpoints.filter(c => c.committed).length,
      has_completion_page: hasCompletion,
      unknown_geometry: modPages.flatMap(p => p.items).filter(
        i => i.target_geometry && i.target_geometry.presented_ratio === null).length,
      content_panels: modPages.reduce((n, p) => n + p.content.length, 0),
      // Content panels the walk listed without a reader for them.
      content_unread: modPages.flatMap(p => p.content)
        .filter(c => c.kind === 'other' && c.capture_note).map(c => `${c.plugin_key}:${c.panel_key}`)
        .slice(0, 20),
      hotspot_panels: modPages.reduce((n, p) => n + p.content.filter(c => c.kind === 'hotspot').length, 0),
      notebook_items: modPages.flatMap(p => p.items).filter(i => i.plugin_key === 'notebook').length,
      table_author_grids: gridStatus,
      rat_candidate_page: (modPages.find(p => p.rat_candidate) || {}).page_no ?? null
    }
  };
};

/* `const` inside eval or Function code is scoped to that code, so the entry point is also
 * published on globalThis for a later call in the same tab. */
if (typeof globalThis !== 'undefined') globalThis.captureLtRun = captureLtRun;

/* Entry: the rendered file is an async function body, so this returns the receipt. Loaded
 * without __LT_ARGS__ (tests, a manual paste of the bare file), it only defines the function. */
if (typeof __LT_ARGS__ !== 'undefined') return await captureLtRun(__LT_ARGS__);
