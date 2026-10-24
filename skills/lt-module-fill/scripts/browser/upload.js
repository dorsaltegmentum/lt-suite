/* SPDX-FileCopyrightText: 2026 dorsaltegmentum
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * lt-module-fill: put one prepared image into one upload panel's own file input.
 *
 * This file is the body of an async function. Render it with
 * `ltjs.py render upload --arg panel=<panel_key> --file <png or jpeg>`; the renderer checks
 * the file (inside the data folder, at most 8 MB, PNG or JPEG) and embeds its bytes as
 * base64 with their sha256. Run the rendered file in the Lt tab with the
 * browser tool's evaluate-file action, on the page that holds the panel.
 *
 * What it does, in order, and nothing else:
 *   1. Finds the panel's own root: the outermost element whose Knockout data is the panel
 *      with this key. None, or more than one, refuses.
 *   2. Finds exactly one input[type=file] inside that root. Zero or several refuses: a
 *      wrong guess here writes an image into another student panel.
 *   3. Decodes the bytes and checks their sha256 against the embedded one.
 *   4. Clears input.value (setting the same file twice fires no change event), builds a
 *      File through DataTransfer, sets input.files and dispatches `change`.
 *   5. Records a pending upload for this panel in the document (globalThis.__ltPendingUploads:
 *      panel key, sha256 and byte count only). An older pending upload of the same panel is marked
 *      superseded, so at most one per panel waits for a dialog.
 * It never clicks. A filled panel then raises Lt's "Replace existing picture?" dialog;
 * confirm_dialog.js accepts it by its text, once, and binds it to that pending upload. The
 * receipt carries the sha256 of the bytes set, never the bytes.
 *
 * Arguments (__LT_ARGS__): panel; file = {name, type, b64, sha256}, or flat file_name,
 * file_type, file_b64, file_sha256.
 */
'use strict';
{
const __ltUpG = (typeof globalThis !== 'undefined') ? globalThis : window;

const ltUploadArgs = (args) => {
  args = args || {};
  const f = (args.file && typeof args.file === 'object') ? args.file : {};
  return {
    panel: args.panel == null ? '' : String(args.panel),
    name: String(f.name || args.file_name || 'image.png'),
    type: String(f.type || args.file_type || ''),
    b64: String(f.b64 || args.file_b64 || ''),
    sha256: String(f.sha256 || args.file_sha256 || '').toLowerCase()
  };
};

const ltUpload = async (rawArgs) => {
  const G = __ltUpG;
  const a = ltUploadArgs(rawArgs);
  const ck = G.__ltClock || { now: () => Date.now(), sleep: ms => new Promise(r => setTimeout(r, ms)) };
  const head = { panel: a.panel };
  const refuse = (reason, extra) => Object.assign({ ok: false, fatal: reason }, head, extra || {}, { set: false });
  const path = String((G.location && G.location.pathname) || '');
  if (!/\/run\/page\/\d+\/?$/.test(path) || /\/preview(\/|$)/.test(path)) return refuse('not-a-run-page');
  if (!a.panel) return refuse('no-panel-key');
  if (!a.b64 || !/^[0-9a-f]{64}$/.test(a.sha256)) return refuse('no-file-bytes');
  if (!/^image\/(png|jpeg)$/.test(a.type)) return refuse('not-png-or-jpeg', { type: a.type });
  if (!G.ko || typeof G.ko.dataFor !== 'function') return refuse('no-lt-client');

  const U = v => (G.ko.isObservable && G.ko.isObservable(v) ? v() : v);
  const A = o => { const x = U(o); return x && x.attributes ? x.attributes : x; };
  const keyOf = d => {
    if (!d || typeof d !== 'object') return null;
    try { if (typeof d.panelKey === 'function') return String(d.panelKey()); } catch (e) { /* plain attribute below */ }
    const k = U((A(d) || {}).panelKey);
    return k == null ? null : String(k);
  };
  const dataOf = el => { try { return el ? G.ko.dataFor(el) : null; } catch (e) { return null; } };

  // 1. The panel's own root.
  const roots = [];
  for (const el of Array.from(G.document.querySelectorAll('*'))) {
    const d = dataOf(el);
    if (!d || keyOf(d) !== a.panel) continue;
    if (dataOf(el.parentElement) === d) continue;
    roots.push(el);
  }
  if (roots.length === 0) return refuse('no-panel-root');
  if (roots.length > 1) return refuse('panel-root-ambiguous', { roots: roots.length });
  const root = roots[0];
  // Lt renders these inputs lazily; bring the panel into view and give it a moment.
  if (typeof root.scrollIntoView === 'function') { try { root.scrollIntoView({ block: 'center' }); } catch (e) { /* not fatal */ } }
  let inputs = Array.from(root.querySelectorAll('input[type=file]'));
  for (let i = 0; i < 6 && inputs.length === 0; i++) { await ck.sleep(250); inputs = Array.from(root.querySelectorAll('input[type=file]')); }

  // 2. Exactly one file input inside it.
  if (inputs.length !== 1) return refuse(inputs.length ? 'several-file-inputs' : 'no-file-input', { inputs: inputs.length });
  const input = inputs[0];

  // 3. The bytes, checked.
  let bytes;
  try {
    const bin = G.atob(a.b64);
    bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  } catch (e) { return refuse('bad-base64'); }
  const digest = await G.crypto.subtle.digest('SHA-256', bytes);
  const hex = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
  if (hex !== a.sha256) return refuse('bytes-hash-mismatch');

  // 4. Set the files and tell Lt. No click, anywhere.
  try { input.value = ''; } catch (e) { /* some engines refuse; change still fires for a new file */ }
  const dt = new G.DataTransfer();
  dt.items.add(new G.File([bytes], a.name, { type: a.type }));
  input.files = dt.files;
  input.dispatchEvent(new G.Event('change', { bubbles: true }));

  // 5. The pending upload a confirmation dialog may answer.
  const pend = Array.isArray(G.__ltPendingUploads) ? G.__ltPendingUploads : (G.__ltPendingUploads = []);
  for (const p of pend) if (p && p.panel === a.panel && p.state === 'pending') p.state = 'superseded';
  pend.push({ panel: a.panel, sha256: hex, bytes: bytes.length, t: ck.now(), state: 'pending' });
  if (pend.length > 50) pend.shift();

  await ck.sleep(300);
  const dlg = G.document.querySelector('[role=dialog]');
  const dialogOpen = !!(dlg && typeof dlg.getClientRects === 'function' && dlg.getClientRects().length);
  return Object.assign({ ok: true }, head, {
    set: true, sha256: hex, bytes: bytes.length, files_set: input.files ? input.files.length : 0,
    dialog_open: dialogOpen, next: dialogOpen ? 'confirm_dialog action=accept panel=' + a.panel : null
  });
};

__ltUpG.ltUpload = ltUpload;
}

return await globalThis.ltUpload(typeof __LT_ARGS__ !== 'undefined' ? __LT_ARGS__ : {});
