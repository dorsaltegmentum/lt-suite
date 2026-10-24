/* SPDX-FileCopyrightText: 2026 dorsaltegmentum
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * lt-module-fill: accept the one Lt confirmation dialog that an upload of this suite raised,
 * and refuse every dialog that is not on the accept list.
 *
 * This file is the body of an async function: `ltjs.py render confirm_dialog --form <form>
 * --arg action=accept --arg panel=<panel_key>` wraps it with the guard and the save watcher.
 * Run the rendered file in the Lt tab with the browser tool's evaluate-file action right
 * after upload.js reported `dialog_open: true` for that panel. Arguments (__LT_ARGS__):
 * action `text` (read only) or `accept` (one dialog); panel, the panel key upload.js wrote;
 * `max_ms`, `fallback_ms`, `quiet_ms`; `calibration`, the origin's `dialog` record or null.
 *
 * WHY. The decision to overwrite is the user's and is taken before the run. Uploading into
 * a filled upload-and-annotate panel raises "Replace existing picture?" and waits for a
 * click. That click is the only one this suite makes, and only on that dialog.
 *
 * WHY A TEXT LIST AND NOT A SELECTOR. Lt's dialogs are all `[role=dialog].CustomDialog` with
 * the same Cancel / OK pair, so nothing in the markup tells "Replace existing picture?" from
 * a dialog that commits an attempt. Only the text identifies a dialog, so the text is
 * matched, and an unrecognised dialog is refused.
 *
 * The rules, in this order. Any refusal returns before the click:
 *   1. Exactly one visible dialog. Two or more are refused: the text read and the button
 *      clicked must come from the same node.
 *   2. DENY on the raw text, button labels included: a dialog that reads like a commit is
 *      refused, even when someone adds it to ACCEPT by mistake.
 *   3. ACCEPT on the body text: the raw text with the lines that equal a button label
 *      removed. Words that contain "No", "OK" or "Yes" stay whole.
 *   4. Exactly one button in that node reads OK or Yes.
 *   5. One pending upload of this document for `panel`, recorded by upload.js and not yet
 *      bound to a dialog. Accepting binds it. Dialogs are never drained in a loop: each
 *      accepted dialog answers one recorded upload.
 *
 * THE WAIT. After the click, Lt saves. With a `dialog` calibration record for the origin the
 * script waits for this upload's own request: it matches the recorded method and path
 * template, carries the uploaded file (a body part of exactly its size), starts after the
 * click and ends 2xx (202 excluded); then `quiet_ms` with no matching request open. Every
 * matching request that ended decides: a failure is `error`, a 202 or a cap reached before
 * the quiet window is `unconfirmed`. A matching request for another file never confirms
 * this one. A request this script cannot see (sent through a fetch or XHR reference Lt
 * kept from before the hooks below) confirms nothing, so the wait ends `unconfirmed` and
 * the fresh-load verify decides. Without a record, or under a service worker, it waits
 * `fallback_ms` (6,000 ms), the old fixed settle, and reports the one request that carried
 * the uploaded file for calibration; an unseen request leaves nothing to calibrate from.
 * A tab hidden for any part of the fallback wait reports `unconfirmed`, because hidden
 * tabs stall timers.
 */
'use strict';
{
/* Refused unconditionally. Broad on purpose: a false refusal costs one human click, a
 * false acceptance can destroy the learning material this project exists to protect. */
const LT_DIALOG_DENY = [
  /submit/i, /commit/i, /finish/i, /finalis|finaliz/i, /hand\s*in/i,
  /checkpoint/i, /complete\s+(this|the)/i, /finish\s+attempt/i,
  /cannot\s+be\s+(undone|changed)/i, /final\s+answer/i, /once\s+you\s+(submit|continue)/i,
];

/* Read on a real page. Body text only; the button lines are removed first. */
const LT_DIALOG_ACCEPT = [
  /^replace existing picture\?$/i,   // upload into a filled upload-and-annotate panel
];

const OK_LABELS = ['ok', 'yes'];
const NON_SAVE = { GET: 1, HEAD: 1, OPTIONS: 1 };
const __ltDlgG = (typeof globalThis !== 'undefined') ? globalThis : window;

/* Is this element on screen? Not `offsetParent === null`: offsetParent is null for every
 * `position: fixed` element, and Lt's CustomDialog is fixed. getClientRects() is empty for
 * a hidden element and non-empty for a fixed one. */
const ltDialogVisible = (el) => {
  if (!el || typeof el.getClientRects !== 'function' || el.getClientRects().length === 0) return false;
  const cs = __ltDlgG.getComputedStyle(el);
  return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0;
};

/* Every visible dialog node. */
const ltVisibleDialogs = () => {
  const d = __ltDlgG.document;
  let list = [];
  if (d && typeof d.querySelectorAll === 'function') list = Array.from(d.querySelectorAll('[role=dialog]') || []);
  if (!list.length && d && typeof d.querySelector === 'function') { const one = d.querySelector('[role=dialog]'); if (one) list = [one]; }
  return list.filter(ltDialogVisible);
};

const flat = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
const labelOf = b => flat(b && (b.innerText != null ? b.innerText : b.textContent));

/* One dialog node read once: raw text, body text and its buttons. */
const ltReadDialog = (dlg) => {
  const raw = String((dlg.innerText != null ? dlg.innerText : dlg.textContent) || '');
  const buttons = typeof dlg.querySelectorAll === 'function' ? Array.from(dlg.querySelectorAll('button') || []) : [];
  const labels = new Set(buttons.map(labelOf).filter(Boolean));
  const body = flat(raw.split(/\r?\n/).map(flat).filter(line => line && !labels.has(line)).join(' '));
  return { raw: flat(raw), body, buttons };
};

/* The open dialog's body text, or null when none or several are open. */
const ltDialogText = () => {
  const open = ltVisibleDialogs();
  return open.length === 1 ? ltReadDialog(open[0]).body : null;
};

/* Method, path template and status of each request that starts after this script first ran
 * in the document. Bodies are never read. Installed once, never removed. */
const ltDialogNet = () => {
  const G = __ltDlgG;
  if (G.__ltDialogNet) return G.__ltDialogNet;
  const log = { all: [] };
  const clk = () => ck().now();
  const parse = u => { try { return new URL(String(u), G.location.href); } catch (e) { return null; } };
  /* Sizes of the file parts a body carries (a Blob, or Blob values of a FormData); never content. */
  const fileSizes = body => {
    const out = [];
    try {
      const isBlob = v => v && typeof v === 'object' && typeof v.size === 'number' && typeof v.slice === 'function';
      if (isBlob(body)) out.push(body.size);
      else if (body && typeof body.forEach === 'function' && G.FormData && body instanceof G.FormData) {
        body.forEach(v => { if (isBlob(v)) out.push(v.size); });
      }
    } catch (e) { /* unreadable body: no sizes */ }
    return out;
  };
  const rec = (method, url, body) => {
    const u = parse(url);
    const r = { method: String(method || 'GET').toUpperCase(), path: u ? u.pathname : String(url),
      same: !!(u && u.origin === G.location.origin), start: clk(), end: null, status: null,
      files: fileSizes(body) };
    log.all.push(r);
    if (log.all.length > 200) log.all.shift();
    return r;
  };
  const X = G.XMLHttpRequest && G.XMLHttpRequest.prototype;
  if (X && typeof X.open === 'function' && typeof X.send === 'function') {
    const open0 = X.open, send0 = X.send, info = new WeakMap();
    X.open = function (method, url) {
      try { info.set(this, { method, url }); } catch (e) { /* frozen */ }
      return open0.apply(this, arguments);
    };
    X.send = function (body) {
      const i = info.get(this);
      if (i) {
        const r = rec(i.method, i.url, body);
        const xhr = this;
        if (typeof xhr.addEventListener === 'function') xhr.addEventListener('loadend', () => { if (r.end == null) { r.end = clk(); r.status = xhr.status; } });
      }
      return send0.apply(this, arguments);
    };
  }
  if (typeof G.fetch === 'function') {
    const fetch0 = G.fetch;
    G.fetch = function (input, init) {
      const url = (input && typeof input === 'object' && 'url' in input) ? input.url : input;
      const r = rec((init && init.method) || (input && input.method) || 'GET', url, init && init.body);
      const p = fetch0.apply(this, arguments);
      Promise.resolve(p).then(res => { r.end = clk(); r.status = res ? res.status : 0; },
        () => { r.end = clk(); r.status = 0; });
      return p;
    };
  }
  G.__ltDialogNet = log;
  return log;
};

/* Monotonic clock (performance.now) unless a test injects one. */
const ck = () => __ltDlgG.__ltClock || {
  now: () => ((__ltDlgG.performance && typeof __ltDlgG.performance.now === 'function') ? __ltDlgG.performance.now() : Date.now()),
  sleep: ms => new Promise(r => setTimeout(r, ms))
};
const hidden = () => { try { return !!(__ltDlgG.document && __ltDlgG.document.hidden === true); } catch (e) { return false; } };
const swControlled = () => { try { const n = __ltDlgG.navigator; return !!(n && n.serviceWorker && n.serviceWorker.controller); } catch (e) { return false; } };

/* The path template LtNet records: {uuid}, then whole hex segments of 8+ characters with a
 * digit as {hex}, then digit runs as {n}. LtNet's own function wins when present. */
const ltPathTemplate = p => {
  const n = __ltDlgG.LtNet;
  if (n && typeof n.pathTemplate === 'function') return n.pathTemplate(p);
  const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
  return String(p || '').split('/').map(seg => {
    if (/^[0-9a-f]{8,}$/i.test(seg) && /\d/.test(seg)) return '{hex}';
    return seg.replace(UUID, '{uuid}').replace(/\d+/g, '{n}');
  }).join('/');
};

/* The pending upload this dialog answers: upload.js records one per panel and document. */
const pendingFor = (panel) => {
  const list = Array.isArray(__ltDlgG.__ltPendingUploads) ? __ltDlgG.__ltPendingUploads : [];
  return list.filter(p => p && p.state === 'pending' && p.panel === panel);
};

/* Accept the one open dialog if, and only if, every rule in the header holds. Returns
 * {accepted, text_len, refused?, upload?, still_open, save}. Never throws for a refusal. */
const acceptLtDialog = async (opts) => {
  const G = __ltDlgG;
  opts = opts || {};
  const net = ltDialogNet();
  const c = ck();
  const open = ltVisibleDialogs();
  if (open.length === 0) return { accepted: false, refused: 'no-dialog', text_len: 0 };
  if (open.length > 1) return { accepted: false, refused: 'several-dialogs', dialogs: open.length, text_len: 0 };
  const dlg = open[0];
  const d = ltReadDialog(dlg);
  const denied = LT_DIALOG_DENY.find(re => re.test(d.raw));
  if (denied) return { accepted: false, refused: 'deny-pattern', pattern: String(denied), text_len: d.body.length };
  if (!LT_DIALOG_ACCEPT.some(re => re.test(d.body))) return { accepted: false, refused: 'not-on-accept-list', text_len: d.body.length };
  const oks = d.buttons.filter(b => OK_LABELS.includes(labelOf(b).toLowerCase()));
  if (oks.length === 0) return { accepted: false, refused: 'no-ok-button', text_len: d.body.length };
  if (oks.length > 1) return { accepted: false, refused: 'several-ok-buttons', buttons: oks.length, text_len: d.body.length };
  const panel = opts.panel == null ? '' : String(opts.panel);
  if (!panel) return { accepted: false, refused: 'no-panel-key', text_len: d.body.length };
  const pend = pendingFor(panel);
  if (pend.length !== 1) return { accepted: false, refused: pend.length ? 'pending-upload-ambiguous' : 'no-pending-upload', panel, text_len: d.body.length };
  const up = pend[0];

  const calRec = opts.calibration ? (opts.calibration.dialog || opts.calibration) : null;
  const sw = swControlled();
  const cal = !sw && calRec && calRec.method && !NON_SAVE[String(calRec.method).toUpperCase()] && calRec.path_template
    ? { method: String(calRec.method).toUpperCase(), tpl: String(calRec.path_template) } : null;
  const maxMs = Math.max(Number(opts.max_ms ?? 15000), 0);
  const quietMs = Math.max(Number(opts.quiet_ms ?? 300), 0);
  const fallbackMs = Math.max(Number(opts.fallback_ms ?? 6000), 0);
  const t0 = c.now();
  up.state = 'bound';
  up.bound_at = t0;
  oks[0].click();
  let wasHidden = hidden();
  const onVis = () => { if (hidden()) wasHidden = true; };
  const doc = G.document;
  try { if (doc && typeof doc.addEventListener === 'function') doc.addEventListener('visibilitychange', onVis); } catch (e) { /* no events */ }
  const after = r => r.start >= t0 && r.same;
  const matches = r => after(r) && r.method === cal.method && ltPathTemplate(r.path) === cal.tpl;
  // This upload's own request: it carries a file part of exactly the uploaded size.
  const carries = r => typeof up.bytes === 'number' && r.files.indexOf(up.bytes) >= 0;
  const okStatus = s => s >= 200 && s < 300 && s !== 202;
  const failStatus = s => s === 0 || s >= 400;
  let save;
  if (cal) {
    let hit = null;
    for (;;) {
      onVis();
      hit = net.all.find(r => matches(r) && carries(r) && r.end != null);
      if (hit || c.now() - t0 >= maxMs) break;
      await c.sleep(50);
    }
    if (hit && okStatus(hit.status)) {
      // Quiet window: no request matching the record left open for quiet_ms, within max_ms.
      let quietFrom = c.now();
      let quiet = false;
      while (c.now() - t0 < maxMs) {
        onVis();
        if (net.all.some(r => matches(r) && r.end == null)) quietFrom = c.now();
        if (c.now() - quietFrom >= quietMs) { quiet = true; break; }
        await c.sleep(50);
      }
      // Every matching request that ended decides, not only the first one.
      const ended = net.all.filter(r => matches(r) && r.end != null);
      const failed = ended.find(r => failStatus(r.status));
      const weak = ended.find(r => !okStatus(r.status));
      const waited = c.now() - t0;
      if (failed) save = { signal: 'error', waited_ms: waited, capped: !quiet, status: failed.status };
      else if (!quiet) save = { signal: 'unconfirmed', waited_ms: waited, capped: true, status: hit.status };
      else if (weak) save = { signal: 'unconfirmed', waited_ms: waited, capped: false, status: weak.status };
      else save = { signal: 'net', waited_ms: waited, capped: false, status: hit.status, requests: ended.length };
    } else if (hit && failStatus(hit.status)) {
      save = { signal: 'error', waited_ms: c.now() - t0, capped: false, status: hit.status };
    } else {
      save = { signal: 'unconfirmed', waited_ms: c.now() - t0, capped: !hit, status: hit ? hit.status : null };
    }
  } else {
    // The fallback sleeps in 50 ms steps so a tab hidden for any part of it is noticed.
    while (c.now() - t0 < fallbackMs) {
      onVis();
      await c.sleep(Math.min(50, fallbackMs - (c.now() - t0)));
    }
    onVis();
    /* The request that carried this upload's file after the click (a body part of exactly the
     * uploaded size), so `emit_fill.py record --dialog` can calibrate the dialog wait from it.
     * Method, path template and status only. Any other request (telemetry, a poll) is never
     * taken as the acknowledgement. */
    const carried = net.all.filter(r => after(r) && !NON_SAVE[r.method] && r.end != null
      && typeof up.bytes === 'number' && r.files.indexOf(up.bytes) >= 0);
    const seen = carried.length === 1 ? carried[0] : null;
    save = { signal: wasHidden ? 'unconfirmed' : 'fallback', waited_ms: c.now() - t0, capped: false,
      sw_controlled: sw, ack_method: seen ? seen.method : null,
      ack_path_template: seen ? ltPathTemplate(seen.path) : null, ack_status: seen ? seen.status : null,
      ack_reason: seen ? 'carried-the-file' : (carried.length > 1 ? 'several-requests-carried-the-file' : 'no-request-carried-the-file') };
  }
  try { if (doc && typeof doc.removeEventListener === 'function') doc.removeEventListener('visibilitychange', onVis); } catch (e) { /* no events */ }
  save.hidden_during_wait = wasHidden;
  return { accepted: true, text_len: d.body.length, upload: { panel: up.panel, sha256: up.sha256 },
    still_open: ltVisibleDialogs().indexOf(dlg) >= 0, save };
};

/* Setting the same file on a file input twice fires no `change` event, so Lt never sees the
 * second upload; upload.js clears input.value first. A size check cannot verify a replace
 * when the new image has the old one's dimensions; the image key tells them apart. */

const ltConfirmDialog = async (args) => {
  args = args || {};
  const opts = {
    panel: args.panel,
    max_ms: args.max_ms, fallback_ms: args.fallback_ms, quiet_ms: args.quiet_ms,
    calibration: typeof args.calibration === 'string' ? JSON.parse(args.calibration) : (args.calibration || null)
  };
  const action = String(args.action || 'accept');
  if (action === 'text') {
    const open = ltVisibleDialogs();
    return { ok: true, open: open.length, text: open.length === 1 ? ltReadDialog(open[0]).body : null };
  }
  if (action !== 'accept') return { ok: false, fatal: 'unknown-action', action };
  const r = await acceptLtDialog(opts);
  const s = r.save || {};
  // ack_* at the top level as well, for `emit_fill.py record --dialog`.
  return Object.assign({ ok: true }, r, {
    ack_method: s.ack_method || null, ack_path_template: s.ack_path_template || null,
    ack_status: s.ack_status ?? null,
    next: (r.accepted && (s.signal === 'fallback' || s.signal === 'unconfirmed' || s.signal === 'error'))
      ? 'fresh-load verify of this panel before any other page' : null
  });
};

__ltDlgG.ltDialogText = ltDialogText;
__ltDlgG.acceptLtDialog = acceptLtDialog;
__ltDlgG.ltConfirmDialog = ltConfirmDialog;
// The live lists, for a test that adds a pattern and checks that DENY still wins.
__ltDlgG.LT_DIALOG_DENY = LT_DIALOG_DENY;
__ltDlgG.LT_DIALOG_ACCEPT = LT_DIALOG_ACCEPT;
__ltDlgG.ltDialogPathTemplate = ltPathTemplate;
}

return await globalThis.ltConfirmDialog(typeof __LT_ARGS__ !== 'undefined' ? __LT_ARGS__ : {});
