/*
 * SPDX-FileCopyrightText: 2026 dorsaltegmentum
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * Read live occupancy and placement pools for the current Lt page.
 *
 * Render with `ltjs.py render probe_panels --form <form>`.
 * Run the result with your browser tool's evaluate-file action.
 * emit_fill.py also embeds this probe into every fill script.
 *
 * Return an object because one returned string may be truncated.
 * Read occupancy immediately before every write.
 * module.json is a snapshot and cannot establish current occupancy.
 * Ask the user and wait for the answer before replacing occupied work.
 *
 * Return counts and lengths for student answers.
 * Do not return answer text, placements, model answers or correctness flags.
 * Return authored label pools, category pools and anchor coordinates.
 * Placement writes need live collection indices.
 * Captured label pools are sorted and cannot supply those indices.
 *
 * allPanels may contain Backbone models or plain view-model objects.
 * A() handles both shapes.
 * Table cells may use cells or columns.
 * Header rows and header columns contain authored content.
 * Count only student cells.
 *
 * An answerable panel has an input key in panelState.
 */
const probeLtPanels = async () => {
  const el = document.querySelector('div.pageList');
  if (!el || typeof ko === 'undefined') {
    return { ok: false, reason: 'Not an Lt lesson page, or the Knockout client has not painted yet.' };
  }
  const root = ko.contextFor(el).$root;

  const U = v => (ko.isObservable(v) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const models = c => { const x = U(c); return x && x.models ? x.models : (Array.isArray(x) ? x : []); };
  const S = h => String(h ?? '')
    .replace(/<\s*br\s*\/?>/gi, '\n')
    .replace(/<\/\s*(p|div|li|tr|h[1-6])\s*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'")
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();

  const cellsOf = row => { const ra = A(row) || {}; const c = models(ra.cells); return c.length ? c : models(ra.columns); };
  const cellText = cl => { const ca = A(cl) || {}; return S(U(ca.content ?? ca.text ?? '')); };
  const grid = coll => models(coll).map(row => ({
    header: U((A(row) || {}).headerRow) === true,
    cells: cellsOf(row).map(cl => ({ text: cellText(cl), header: U((A(cl) || {}).headerColumn) === true }))
  }));
  const gridBody = g => g.filter(r => !r.header);

  /* Match capture.js's text selection.
   * Category titles take precedence over fallback keys.
   * Matching strings let placements resolve against captured categories. */
  const LABEL_KEYS = ['label', 'text', 'name', 'title'];
  const CAT_KEYS = ['title', 'category', 'label', 'name', 'text'];
  const pickText = (o, keys) => {
    const a = A(o) || {};
    for (const k of keys) { const v = S(U(a[k])); if (v.length) return v; }
    return '';
  };

  const m = location.pathname.match(/\/i\/([^/]+)\/student\/courses\/(\d+)\/runs\/(\d+)\/run\/page\/(\d+)/);
  if (!m) {
    return { ok: false, reason: 'Not on a /run/page/<n> URL. The report page and /run/complete are ' +
                               'out of bounds, and a target is (page_index, panel_key).' };
  }
  let pageIndex = Number(m[4]);

  /* Each plugin needs its own emptiness test.
   * A zero value alone does not establish occupancy.
   * Evidence contains counts and flags rather than student content. */
  const OCCUPANCY = {
    'text-question': (c, inp) => {
      /* Measure visible text rather than rich-text markup.
       * A cleared field may retain <p></p>. */
      const s = S(U(inp.answer));
      return { occupied: s.length > 0, evidence: { chars: s.length, html_chars: String(U(inp.answer) ?? '').length } };
    },
    'select': (c, inp) => {
      const idx = models(inp.selectedAnswers).filter(n => Number.isInteger(n));
      return { occupied: idx.length > 0, evidence: { selected: idx.length } };
    },
    'table-question': (c, inp) => {
      const body = gridBody(grid(inp.rows));
      let filled = 0, cells = 0;
      for (const row of body) {
        for (const cell of row.cells) {
          if (cell.header) continue;          // Header columns contain authored content.
          cells++;
          if (cell.text.length > 0) filled++;
        }
      }
      return { occupied: filled > 0, evidence: { filled_cells: filled, student_cells: cells } };
    },
    'image-annotation': (c, inp) => {
      /* An uploaded image counts as work even with zero annotations.
       * One browser tool's output filter redacts long camelCase values.
       * Report the input mode with short tokens. */
      const upload = U(c.inputMode) === 'allowImageAndAnnotation';
      const ann = U(inp.annotations) || [];
      const key = String(U(inp.imageKey) || '');
      return {
        occupied: upload ? (ann.length > 0 || key !== '') : ann.length > 0,
        evidence: {
          mode: upload ? 'upload+annotate' : 'annotate-only',
          annotations: ann.length,
          student_image: key !== '',
          // Read Lt's stored naturalHeight/naturalWidth ratio after its 1920px downsampling.
          presented_ratio: Number(U(inp.presentedRatio) ?? 0) || null,
          /* One browser tool's output filter redacts values when field names contain "author".
           * Use predefined_image so this flag survives the output filter. */
          predefined_image: String(U(c.predefinedImageKey) || U(c.imageResourceKey) || '') !== ''
        }
      };
    },
    /* Both labelling plugins use linkedLabels and addAnswer(labelIndex, locationIndex).
     * Register one handler under both keys.
     * Zero is a valid label index and location index.
     * Count a link only when both indices exist. */
    ...(() => {
      /* Preserve live label order for addAnswer indices.
       * Captured label pools are sorted.
       * Return authored labels while keeping student placements as counts.
       * Avoid field names containing "author" because the output filter redacts their values. */
      const pool = coll => models(coll).map((x, i) => ({ index: i, text: S(U((A(x) || {}).label)) }));

      /* Locations contain authored anchor and drop-zone coordinates.
       * Each coordinate is a proportion of the stimulus image.
       * Preserve zero and report missing components as null. */
      const ANCHOR_KEYS = [['x', 'anchorX'], ['y', 'anchorY'],
                           ['dz_x', 'dropZoneLeftProportion'], ['dz_y', 'dropZoneTopProportion']];
      const anchorPool = coll => models(coll).map((x, i) => {
        const a = A(x) || {};
        const o = { index: i };
        for (const [out, key] of ANCHOR_KEYS) {
          const v = U(a[key]);
          const n = (v === null || v === undefined || v === '') ? NaN : Number(v);
          o[out] = Number.isFinite(n) ? n : null;
        }
        return o;
      });

      const links = (c, inp) => {
        const l = models(inp.linkedLabels)
          .map(x => A(x) || {})
          .filter(x => U(x.locationIndex) != null && U(x.labelIndex) != null);
        const labels = pool(c.labelItems);
        const locs = anchorPool(c.labelLocationItems);
        return { occupied: l.length > 0,
                 evidence: { linked: l.length, labels: labels.length,
                             locations: locs.length,
                             label_pool: labels, anchor_pool: locs } };
      };
      return { 'kcImageLabelling': links, 'drag-and-drop': links };
    })(),
    'categories': (c, inp) => {
      /* Empty collections and nested empty labelAnswerItems both count as empty.
       * An unknown flat shape counts as occupied.
       * Unknown shapes cannot establish permission to write. */
      const cans = models(inp.categoryAnswerItems).map(x => A(x) || {});
      const shape = cans.length === 0 ? 'empty-collection'
                  : cans.some(x => U(x.labelAnswerItems) !== undefined) ? 'nested-labelAnswerItems'
                  : 'flat-unmeasured';
      const placed = cans.reduce((n, x) => n + models(x.labelAnswerItems).length, 0);
      /* addAnswer(labelIndex, categoryIndex, analytics) indexes live collections.
       * Return both authored pools in live order.
       * Captured categories preserve live order and may supply target_index.
       * Captured labels are sorted and must resolve by text. */
      const labelPool = models(c.labelItems).map((x, i) => ({ index: i, text: pickText(x, LABEL_KEYS) }));
      const catPool = models(c.categoryItems).map((x, i) => ({ index: i, text: pickText(x, CAT_KEYS) }));
      return {
        // Only the nested shape supports a placement count.
        occupied: shape === 'nested-labelAnswerItems' ? placed > 0 : cans.length > 0,
        evidence: { placed, answer_items: cans.length,
                    labels: labelPool.length,
                    categories: catPool.length,
                    shape,
                    label_pool: labelPool,
                    category_pool: catPool }
      };
    }
  };

  const pages = root.lessonFlowPages();
  const flat = models(pages).map(p => A(p) || p);
  /* The URL page number is a dataKey and may differ from flow position.
   * Prefer the painted page's current() flag.
   * Use the URL number as fallback. */
  const painted = models(pages).findIndex(p => U(p.current) === true);
  if (painted >= 0) pageIndex = painted + 1;
  const page = painted >= 0 ? flat[painted]
    : (flat.find(p => Number(U(p.pageIndex)) === pageIndex) || flat[pageIndex - 1]);
  if (!page) return { ok: false, reason: `page_index ${pageIndex} not found in lessonFlowPages` };

  /* Match capture.js's structural checkpoint detector.
   * A checkpoint has committed and lacks allPanels.
   * Report pages have reportInfoPanels.
   * Refuse checkpoints before an empty panel list can imply an unanswered page. */
  const isReportPage = 'reportInfoPanels' in page;
  const isCheckpointPage = !isReportPage && 'committed' in page && !('allPanels' in page);
  if (isCheckpointPage) {
    return { ok: false, is_checkpoint: true,
             committed: U(page.committed) === true,
             reason: `page_index ${pageIndex} is a CHECKPOINT, not a content page. Never `
                   + `advance past one. It carries no allPanels, so a probe of it would return `
                   + `an empty panel list that reads exactly like an unanswered page.` };
  }

  const panels = [];
  let domOrder = 0;
  for (const raw of models(page.allPanels)) {
    const p = raw, pa = A(raw) || {};
    let pk;
    try { pk = typeof p.pluginKey === 'function' ? String(p.pluginKey()) : String(U(pa.pluginKey)); }
    catch (e) { pk = String(U(pa.pluginKey)); }
    let key;
    try { key = typeof p.panelKey === 'function' ? String(p.panelKey()) : String(U(pa.panelKey)); }
    catch (e) { key = String(U(pa.panelKey)); }

    const st = U(pa.panelState);
    if (!st) continue;
    const c = A(st.content) || {};

    /* A panel is answerable when panelState has an input key.
     * Other panels contain authored content. */
    if (!('input' in st)) {
      panels.push({ dom_order: domOrder++, target: { page_index: pageIndex, panel_key: key },
                    plugin_key: pk, kind: 'content' });
      continue;
    }
    const inp = A(st.input) || {};
    const fn = OCCUPANCY[pk];
    const occ = fn ? fn(c, inp) : null;
    panels.push({
      dom_order: domOrder++,
      // Targets need both page_index and panel_key because panel keys may recur across pages.
      target: { page_index: pageIndex, panel_key: key },
      plugin_key: pk,
      kind: 'answerable',
      known: !!fn,
      occupied: occ ? occ.occupied : null,
      evidence: occ ? occ.evidence : null,
      locked: U(inp.locked) === true,
      /* A panel's isReadOnly flag can be true while input.locked is absent.
       * Run and page flags do not establish panel writability.
       * Missing panel flags remain null and cannot establish permission to write. */
      read_only: (() => {
        for (const v of [U(pa.isReadOnly), U(st.isReadOnly), U(c.isReadOnly)]) {
          if (v === true || v === false) return v;
        }
        return null;
      })(),
      single_attempt: U(c.singleAttempt) === true,
      /* Skip unavailable items individually and continue with other eligible items.
       * Keep read-only, locked and unknown-plugin reasons separate. */
      skip_reason: (() => {
        const ro = [U(pa.isReadOnly), U(st.isReadOnly), U(c.isReadOnly)].find(v => v === true || v === false);
        if (ro === true) return 'read-only';          // The panel is read-only.
        if (U(inp.locked) === true) return 'locked';  // The input has its own lock.
        if (!fn) return 'no-emptiness-test';          // The plugin has no tested handler.
        return null;
      })(),
      note: fn ? null : 'no tested emptiness test for this pluginKey; treat as occupied, skip '
                      + 'this item with result=todo, and continue'
    });
  }

  /* Upload inputs exist in the DOM without opening a native file picker.
   * DOM order maps each upload input to its panel order. */
  const fileInputs = Array.from(document.querySelectorAll('input[type=file].imageUploadInput'))
    .map((n, i) => ({ dom_order: i, accept: n.getAttribute('accept') || null, disabled: n.disabled }));

  /* Browser output serialization may truncate values below about 4 levels.
   * Publish pools as shallow projections of the evidence arrays.
   * Label and category leaves are strings at depth 4.
   * Each anchor becomes 1 string to keep its leaf at depth 4.
   * Array positions preserve the live indices used by addAnswer. */
  const fmt = n => (typeof n === 'number' && Number.isFinite(n)) ? n.toFixed(4) : '-';
  const pools = {};
  for (const pnl of panels) {
    const ev = pnl.evidence;
    if (!ev) continue;
    const block = { plugin_key: pnl.plugin_key };
    if (Array.isArray(ev.label_pool)) block.labels = ev.label_pool.map(e => e.text);
    if (Array.isArray(ev.category_pool)) block.categories = ev.category_pool.map(e => e.text);
    if (Array.isArray(ev.anchor_pool) && ev.anchor_pool.length) {
      block.anchors = ev.anchor_pool.map(a => `${a.index} ${fmt(a.x)} ${fmt(a.y)} ${fmt(a.dz_x)} ${fmt(a.dz_y)}`);
    }
    if (Object.keys(block).length > 1) pools[pnl.target.panel_key] = block;
  }

  const answerable = panels.filter(p => p.kind === 'answerable');
  return {
    ok: true,
    page_index: pageIndex,
    page_title: S(U(page.title)),
    is_checkpoint: isCheckpointPage,   // Checkpoints use structural detection.
    panels,
    pools,
    pools_format: {
      labels: 'array position IS the live labelIndex that addAnswer takes. Resolve a drafted label by TEXT and read its position; never carry an index from module.json because its label pool is sorted.',
      categories: 'array position IS the live categoryIndex. constraints.categories is captured in live order, so target_index is legitimate here and on no other type.',
      anchors: 'one string per location: "<locationIndex> <anchorX> <anchorY> <dropZoneLeft> <dropZoneTop>", all proportions of the stimulus image, "-" where Lt carries no value. Render them onto the image and look; do not read leader lines off a screenshot.'
    },
    file_inputs: fileInputs,
    summary: {
      panels: panels.length,
      content: panels.length - answerable.length,
      answerable: answerable.length,
      occupied: answerable.filter(p => p.occupied === true).length,
      empty: answerable.filter(p => p.occupied === false).length,
      unknown: answerable.filter(p => p.occupied === null).length,
      locked: answerable.filter(p => p.locked).length,
      // Count read-only panels separately from input locks.
      read_only: answerable.filter(p => p.read_only === true).length,
      read_only_unknown: answerable.filter(p => p.read_only === null).length
    }
  };
};

return await probeLtPanels();
