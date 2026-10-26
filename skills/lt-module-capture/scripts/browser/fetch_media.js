// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* lt-module-capture page script: save the CURRENT PAGE's full-resolution images.
 *
 * Rendered by `ltcore/ltjs.py render fetch_media` like capture.js and run once per content
 * page. The rendered file is the body of an async function; the last line returns an OBJECT
 * (some browser tools truncate a returned string near 1,000 characters).
 *
 * __LT_ARGS__ (every key optional): transport ('sink' or 'download'), sink (loopback base
 * URL from sink.py), token (sink.py session token), module_id (defaults to c<course>-r<run>
 * from the URL), post_ms (abort a sink POST after this many milliseconds).
 *
 * WHY THE PAGE DOES THE FETCHING. Lt hands out images through signed URLs. The page holds
 * a signed-in session and shows the images already, so it downloads them itself and passes
 * the bytes to disk (to the loopback sink or the download folder). The bytes never travel
 * through the model's context.
 *
 * WHY ONE PAGE AT A TIME. The view model knows an `imageResourceKey` but no URL, and Lt
 * does not document how a key becomes a URL. The one reliable mapping is the `src` that Lt
 * already set on a rendered <img>, and only the page on screen has those. Each record keeps
 * the URL's SHAPE and drops the URL itself, since a signed URL works like a credential;
 * real shapes can later show whether a URL template would do.
 *
 * KNOWN LIMIT. When images come from another origin that sends no
 * Access-Control-Allow-Origin, the page gets an opaque response, and reading it back via a
 * canvas throws. The image is then reported as `blocked-cross-origin`, with no workaround;
 * the user can still save it by hand with "Save image as".
 *
 * RAILS. Nothing in Lt changes: no field write, no click, no navigation. The script starts
 * only on /run/page/<n> or /run/background, which keeps it off the report page and
 * /run/complete. It scrolls to make lazy images load; scrolling sends no data and touches
 * no commit control.
 */

const LT_MEDIA_DEFAULTS = Object.freeze({ transport: 'sink', sink: 'http://127.0.0.1:8731', post_ms: 30000 });

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

const fetchLtMedia = async (opts) => {
  const baked = (typeof __LT_ARGS__ === 'object' && __LT_ARGS__) ? __LT_ARGS__ : {};
  const g = Object.assign({}, baked, opts || {});
  const transport = String(g.transport || LT_MEDIA_DEFAULTS.transport) === 'download' ? 'download' : 'sink';
  const sinkBase = String(g.sink || LT_MEDIA_DEFAULTS.sink).replace(/\/+$/, '');
  const token = String(g.token || '');
  const postMs = (n => (Number.isFinite(n) && n > 0 ? n : LT_MEDIA_DEFAULTS.post_ms))(Number(g.post_ms));
  if (transport === 'sink') {
    let loop = false;
    try {
      const x = new URL(sinkBase);
      loop = x.protocol === 'http:' && x.hostname === '127.0.0.1'
        && x.pathname.replace(/\/+$/, '') === '' && !x.search && !x.hash && !x.username && !x.password;
    } catch (e) { loop = false; }
    if (!loop) return { ok: false, reason: 'The sink address must be http://127.0.0.1:<port>; nothing was fetched.' };
    if (!token) return { ok: false, reason: 'No sink token. Pass the token sink.py printed (--arg token=...), or render with transport=download.' };
  }

  let authenticate;
  if (transport === 'sink') {
    try { authenticate = await ltSinkSession(sinkBase, token, postMs); }
    catch (e) { return {ok: false, fatal: 'sink-unauthenticated'}; }
  }
  const el = document.querySelector('div.pageList');
  if (!el || typeof ko === 'undefined') {
    return { ok: false, reason: 'No Lt lesson view model on this page (wrong tab, or Lt is still loading).' };
  }

  /* /run/background is a content route with no page number; current() picks the page. */
  const m = location.pathname.match(/\/i\/([^/]+)\/student\/courses\/(\d+)\/runs\/(\d+)\/run\/(?:page\/(\d+)\/?$|background\/?$)/);
  if (!m) {
    return { ok: false, reason: 'Not on a /run/page/<n> or /run/background URL. The report page and '
                              + '/run/complete are out of bounds, and this never fetches from them.' };
  }
  const pageIndex = m[4] !== undefined ? Number(m[4]) : 0;
  const moduleId = String(g.module_id || `c${m[2]}-r${m[3]}`);
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(moduleId)) return { ok: false, reason: 'module_id must match ^[A-Za-z0-9_-]{1,64}$.' };

  const U = v => (ko.isObservable(v) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const models = c => { const x = U(c); return x && x.models ? x.models : (Array.isArray(x) ? x : []); };

  /* Which panel owns a given <img>. Knockout's binding context answers it without CSS
   * classes: resolving an Lt target by class can pick the wrong panel. */
  const ownerPanelKey = node => {
    for (let n = node; n; n = n.parentElement) {
      let d;
      try { d = ko.dataFor(n); } catch (e) { continue; }
      if (!d) continue;
      const a = A(d) || {};
      const raw = (typeof d.panelKey === 'function') ? (() => { try { return d.panelKey(); } catch (e) { return undefined; } })()
                                                    : U(a.panelKey);
      if (raw != null && String(raw) !== '') return String(raw);
    }
    return null;
  };

  /* Which page is on screen. The number in /page/<n> is that page's dataKey and can differ
   * from its flow position: when a module starts with /run/background, every later page is
   * off by one. Lt sets current() === true on the displayed page, so that wins; dataKey is
   * the fallback. */
  const ctx = (() => { try { return ko.contextFor(el); } catch (e) { return null; } })();
  if (!ctx || !ctx.$root) return { ok: false, reason: 'No Lt lesson view model on this page (wrong tab, or Lt is still loading).' };
  const root = ctx.$root;
  const flowRaw = models(root.lessonFlowPages());
  let flowPos = flowRaw.findIndex(p => { try { return U(p.current) === true; } catch (e) { return false; } });
  if (flowPos < 0) flowPos = flowRaw.findIndex(p => String(U(p.dataKey)) === String(pageIndex));
  if (flowPos < 0) flowPos = pageIndex - 1;
  const flat = flowRaw.map(p => A(p) || p);
  const page = flat[flowPos];
  if (!page) return { ok: false, reason: `page_index ${pageIndex} not found in lessonFlowPages` };
  const capturePageIndex = flowPos + 1;   // page_index in module.json counts flow pages from 1

  /* Every image key the capture records for this page, keyed by panel, with the same fields
   * and precedence as capture.js: content.predefinedImageKey over content.imageResourceKey for
   * the author's image, input.imageKey for the student's upload (a DIFFERENT image), and a
   * hotspot's base image on content.hotspots[].imageResourceKey. Subpages recurse the way
   * capture.js does (content.subpages[].rows[].cells[].panels[] and cells[].columns[].panels[]),
   * or their images would be missing from keys_not_fetched as well. */
  const keysByPanel = new Map();
  const walk = (panels, depth) => {
    if (depth > 4) return;                       // a depth limit, though real subpages stay shallow
    for (const raw of models(panels)) {
      const pa = A(raw) || {};
      let key;
      try { key = typeof raw.panelKey === 'function' ? String(raw.panelKey()) : String(U(pa.panelKey)); }
      catch (e) { key = String(U(pa.panelKey)); }
      const st = U(pa.panelState);
      if (!st) continue;
      const c = A(st.content) || {};
      const inp = ('input' in st) ? (A(st.input) || {}) : {};
      const entries = [];
      const author = String(U(c.predefinedImageKey) || U(c.imageResourceKey) || '');
      const student = String(U(inp.imageKey) || '');
      if (author) entries.push({ key: author, role: 'author-supplied' });
      if (student) entries.push({ key: student, role: 'student-upload' });
      const hsColl = models(c.hotspots);
      const hs = hsColl.length ? hsColl : (Array.isArray(U(c.hotspots)) ? U(c.hotspots) : []);
      for (const h of hs) {
        const hk = String(U((A(h) || {}).imageResourceKey) || '');
        if (hk && !entries.some(e => e.key === hk)) entries.push({ key: hk, role: 'author-supplied' });
      }
      if (entries.length) keysByPanel.set(key, entries);
      for (const sp of models(c.subpages)) {
        for (const r of models(A(sp).rows)) {
          for (const cell of models(A(r).cells)) {
            const ca = A(cell) || {};
            walk(ca.panels, depth + 1);
            for (const col of models(ca.columns)) walk(A(col).panels, depth + 1);
          }
        }
      }
    }
  };
  walk(page.allPanels, 0);

  /* Images load lazily, so the DOM holds only what has been scrolled into view; at first
   * that may be just the logo and some icons. The script scrolls down the whole page, then
   * polls the count of loaded images until three polls in a row agree. A fixed pause would
   * miss images without any warning. */
  const settle = ms => new Promise(r => setTimeout(r, ms));
  const loaded = () => Array.from(document.querySelectorAll('img'))
    .filter(n => n.complete && n.naturalWidth > 0).length;

  const before = document.querySelectorAll('img').length;
  const height = document.body.scrollHeight;
  const scrollY0 = window.scrollY;
  for (let y = 0; y < height; y += 400) { window.scrollTo(0, y); await settle(150); }
  window.scrollTo(0, scrollY0);

  let stable = 0, last = -1;
  for (let i = 0; i < 40 && stable < 3; i++) {
    await settle(400);
    const n = loaded();
    stable = (n === last) ? stable + 1 : 0;
    last = n;
  }
  const revealed = document.querySelectorAll('img').length - before;

  const imgs = Array.from(document.querySelectorAll('img'))
    .filter(n => n.currentSrc || n.src)
    .map(n => ({ node: n, src: n.currentSrc || n.src,
                 w: n.naturalWidth || null, h: n.naturalHeight || null }))
    .filter(x => !/^data:/.test(x.src));

  /* Records how a URL is built and leaves the URL out. Signed URLs act as credentials, and
   * some browser tools mask long opaque strings in results anyway. Counting path segments
   * and listing query-parameter NAMES is enough for the measurement. */
  const shape = u => {
    try {
      const p = new URL(u, location.href);
      const segs = p.pathname.split('/').filter(Boolean);
      return { same_origin: p.origin === location.origin, scheme: p.protocol,
               segments: segs.length, first_segment_len: (segs[0] || '').length,
               last_segment_len: (segs[segs.length - 1] || '').length,
               query_keys: Array.from(p.searchParams.keys()).sort() };
    } catch (e) { return { same_origin: false, scheme: null, segments: 0, query_keys: [] }; }
  };

  const EXT = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/jpg': '.jpg',
                'image/gif': '.gif', 'image/webp': '.webp', 'image/bmp': '.bmp' };

  /* Matches each rendered <img> to a key and records how:
   *   by-key-in-url   the key appears unchanged inside src
   *   by-position     its panel has exactly one key and this is the img for it
   *   unpaired-*      no match; listed under its panel in the receipt */
  const keyInSrc = (key, src) => {
    const esc = String(key).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`(^|[^A-Za-z0-9])${esc}([^A-Za-z0-9]|$)`).test(src);
  };
  const planned = [];
  const seenSrc = new Set();
  for (const im of imgs) {
    if (seenSrc.has(im.src)) continue;
    seenSrc.add(im.src);
    const panel = ownerPanelKey(im.node);
    const entries = (panel && keysByPanel.get(panel)) || [];
    let hit = entries.find(e => keyInSrc(e.key, im.src));
    let how = hit ? 'by-key-in-url' : null;
    if (!hit && entries.length === 1) { hit = entries[0]; how = 'by-position'; }
    planned.push({
      panel_key: panel, src: im.src, url_shape: shape(im.src),
      key: hit ? hit.key : null, role: hit ? hit.role : null,
      paired: how || (panel ? 'unpaired-ambiguous' : 'unpaired-no-owner-panel'),
      natural: { w: im.w, h: im.h }
    });
  }

  /* The page never learns whether a download finished, so on this route `saved` only
   * means HANDED TO THE BROWSER. collect_downloads.py then checks the download folder. Each
   * image is a separate download; Chromium allows ONE scripted download per site and asks
   * the user to allow automatic downloads before any more go through. */
  const gap = Number.isFinite(Number(g.download_gap_ms)) && Number(g.download_gap_ms) >= 0
    ? Number(g.download_gap_ms) : 250;
  const pause = Number.isFinite(Number(g.download_pause_ms)) && Number(g.download_pause_ms) > gap
    ? Number(g.download_pause_ms) : Math.max(2000, gap * 8);
  let handoffs = 0;
  const saveViaDownload = async (name, blob) => {
    if (handoffs) await new Promise(ok => setTimeout(ok, handoffs % 8 === 0 ? pause : gap));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    handoffs++;
    return { status: null, ok: true, route: 'download', handed_off: true };
  };

  /* Downloads require an explicit transport choice. Upload failures stay failures. */

  const post = async (name, blob) => {
    if (transport === 'download') return saveViaDownload(name, blob);
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), postMs);
    try {
      const res = await fetch(`${sinkBase}/media/${name}`, {
        method: 'POST', mode: 'cors', signal: ac.signal, credentials: 'omit',
        headers: { 'Content-Type': blob.type || 'application/octet-stream', 'X-Lt-Suite-Auth': await authenticate(`media/${name}`, blob) },
        body: blob
      });
      const ok = res.ok === undefined ? (res.status >= 200 && res.status < 300) : !!res.ok;
      let why = null;
      if (!ok) {
        try { why = typeof res.text === 'function' ? String(await res.text()).slice(0, 200) : null; }
        catch (e) { why = null; }
      }
      return { status: res.status, ok, route: 'sink', error: ok ? null : (why || `sink ${res.status}`) };
    } catch (e) {
      return {ok: false, route: 'sink',
        error: 'sink network failure', sink_error: `${e && e.name}: ${String((e && e.message) || e).slice(0, 120)}`};
    } finally { clearTimeout(timer); }
  };

  const out = [];
  const unpairedSeen = new Map();
  for (const p of planned) {
    /* File name = lt-<module_id>-<image key>.<ext>; stamp_capture.py and the exporter look
     * images up by it. Lt numbers uploads and author images with independent counters, so an
     * upload gets a `u` before its key (lt-<module_id>-u<key>). An image without a key takes
     * its panel's name, which still leads back to (page_index, panel_key). */
    let blob = null, err = null;
    try {
      const res = await fetch(p.src, { credentials: 'include', mode: 'cors' });
      if (res.type === 'opaque') err = 'blocked-cross-origin: opaque response, bytes unreadable';
      else if (!res.ok) err = `http ${res.status}`;
      else blob = await res.blob();
    } catch (e) {
      err = (p.url_shape.same_origin ? '' : 'blocked-cross-origin: ')
          + `${e && e.name}: ${String((e && e.message) || e).slice(0, 100)}`;
    }
    if (!blob) {
      out.push({ ...p, result: 'failed', error: err, bytes: 0, file: null });
      continue;
    }
    /* Some author figures are SVG, while the sink takes raster files only (checked by their
     * first bytes). The page converts them to PNG itself; drawing from a blob: URL counts as
     * same-origin, so the canvas stays readable. The longer side ends up 1920 px or more,
     * with limits of 3840 px and 4x. svg_bytes keeps the size of the SVG. */
    let svgBytes = null;
    if (/^image\/svg\+xml/.test(blob.type)) {
      try {
        svgBytes = blob.size;
        const url = URL.createObjectURL(new Blob([await blob.text()], { type: 'image/svg+xml' }));
        const img = new Image();
        await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error('svg decode failed')); img.src = url; });
        const w0 = img.naturalWidth || p.natural.w || 1000, h0 = img.naturalHeight || p.natural.h || 1000;
        const s = Math.min(3840 / Math.max(w0, h0), 4, Math.max(1, 1920 / Math.max(w0, h0)));
        const cv = document.createElement('canvas');
        cv.width = Math.round(w0 * s); cv.height = Math.round(h0 * s);
        const g2 = cv.getContext('2d');
        g2.fillStyle = '#ffffff'; g2.fillRect(0, 0, cv.width, cv.height);
        g2.drawImage(img, 0, 0, cv.width, cv.height);
        URL.revokeObjectURL(url);
        blob = await new Promise(ok => cv.toBlob(ok, 'image/png'));
        if (!blob) throw new Error('canvas encoding returned no image');
      } catch (e) {
        out.push({ ...p, result: 'failed', bytes: svgBytes, file: null,
                   error: `svg rasterise failed: ${String((e && e.message) || e).slice(0, 80)}` });
        continue;
      }
    }
    const ext = EXT[blob.type] || '';
    if (!ext) {
      out.push({ ...p, result: 'failed', bytes: blob.size, file: null,
                 error: `unsupported content type ${blob.type || '(none)'}: only raster images are `
                      + `saved, and no file extension is guessed` });
      continue;
    }
    const safe = s => String(s).replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 64);
    let stem = p.key ? (p.role === 'student-upload' ? `u${safe(p.key)}` : safe(p.key)) : `panel-${safe(p.panel_key)}`;
    if (!p.key) {
      const n = (unpairedSeen.get(stem) || 0) + 1;
      unpairedSeen.set(stem, n);
      if (n > 1) stem = `${stem}-${n}`;
    }
    const file = `lt-${moduleId}-${stem}${ext}`;
    const r = await post(file, blob);
    out.push({ ...p, result: r.ok ? 'saved' : 'failed', bytes: blob.size, file,
               mime: blob.type, route: r.route || null, svg_bytes: svgBytes,
               handed_off: r.handed_off === true, sink_error: r.sink_error || null,
               error: r.ok ? null : (r.error || `sink ${r.status}`) });
  }

  const tag = (key, role) => (role === 'student-upload' ? `u${key}` : String(key));
  const keysOnPage = new Set();
  for (const es of keysByPanel.values()) for (const e of es) keysOnPage.add(tag(e.key, e.role));
  const fetched = new Set(out.filter(o => o.result === 'saved' && o.key).map(o => tag(o.key, o.role)));

  return {
    ok: true,
    module_id: moduleId,
    page_index: capturePageIndex,
    url_page: pageIndex,
    transport,
    ...(transport === 'download' ? {downloads_confirmed: false,
      download_note: 'The browser cannot confirm files landed. Run collect_downloads.py and stamp_capture.py.',
      download_gap_ms: gap, download_pause_ms: pause, download_batch_size: 8} : {}),
    images: out.map(o => ({ panel_key: o.panel_key, key: o.key, role: o.role, paired: o.paired,
                            result: o.result, route: o.route || null, file: o.file, bytes: o.bytes,
                            svg_bytes: o.svg_bytes || null, handed_off: o.handed_off === true,
                            sink_error: o.sink_error || null,
                            mime: o.mime || null, natural: o.natural, error: o.error || null })),
    url_shape: Array.from(new Set(out.map(o => JSON.stringify(o.url_shape)))).map(s => JSON.parse(s)),
    summary: {
      imgs_in_dom: imgs.length,
      // <img> elements that scrolling added to the DOM.
      imgs_revealed_by_scrolling: revealed,
      unique_srcs: planned.length,
      saved: out.filter(o => o.result === 'saved').length,
      failed: out.filter(o => o.result === 'failed').length,
      cross_origin_blocked: out.filter(o => /blocked-cross-origin/.test(o.error || '')).length,
      paired_by_key: out.filter(o => o.paired === 'by-key-in-url').length,
      paired_by_position: out.filter(o => o.paired === 'by-position').length,
      unpaired: out.filter(o => /^unpaired/.test(o.paired)).length,
      /* keys_not_fetched: keys module.json lists for this page with no saved image (uploads
       * appear as u<key>, their file stem). imgs_without_a_captured_key: <img> elements no
       * captured key matched, such as pictures inside authored prose. */
      keys_on_page: keysOnPage.size,
      keys_not_fetched: Array.from(keysOnPage).filter(k => !fetched.has(k)),
      imgs_without_a_captured_key: out.filter(o => !o.key).length
    }
  };
};

if (typeof globalThis !== 'undefined') globalThis.fetchLtMedia = fetchLtMedia;

/* Entry: the rendered file is an async function body, so this returns the receipt. */
if (typeof __LT_ARGS__ !== 'undefined') return await fetchLtMedia(__LT_ARGS__);
