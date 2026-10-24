// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Tests for confirm_dialog.js: one visible dialog, DENY on the raw text before ACCEPT on the
 * body, one OK button, one pending upload per accepted dialog, no drain loop, no click on any
 * refusal, and the wait after the click (6,000 ms uncalibrated; the matching request plus a
 * quiet window calibrated). Runs in tests/browser/harness.js (fake clock, FakeXHR).
 */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const F = require('./fill_env.js');

const SRC = fs.readFileSync(path.join(F.BROWSER, 'confirm_dialog.js'), 'utf8');
const need = F.harness() ? false : 'needs tests/browser/harness.js';

/* A page with Lt dialogs. Only the first is visible unless o.stacked; clicking OK closes it.
 * o.buttons replaces the [Cancel, OK] pair of every dialog. */
function dialogEnv(texts, o) {
  o = o || {};
  const H = F.harness();
  const env = H.makeEnv({ pages: [H.page({ title: 'Page 1', current: true })], serviceWorker: !!o.serviceWorker });
  const stack = [];
  env.clicks = 0;
  env.cancels = 0;
  const make = text => {
    const d = { open: true, style: { display: 'flex', visibility: 'visible', opacity: '1' } };
    d.getClientRects = () => (d.open ? [{ width: 900, height: 600 }] : []);
    const labels = o.buttons || ['Cancel', 'OK'];
    const buttons = labels.map(label => ({
      innerText: label,
      click: () => {
        if (/^(ok|yes)$/i.test(label)) { env.clicks++; d.open = false; stack.splice(stack.indexOf(d), 1); if (o.onClick) o.onClick(env); } else env.cancels++;
      }
    }));
    Object.defineProperty(d, 'innerText', { get: () => [text].concat(labels).join('\n') });
    d.querySelectorAll = sel => (sel === 'button' ? buttons : []);
    return d;
  };
  for (const tx of texts) stack.push(make(tx));
  const visible = () => (o.stacked ? stack.slice() : stack.slice(0, 1));
  const q0 = env.ctx.document.querySelector;
  const qa0 = env.ctx.document.querySelectorAll;
  env.ctx.document.querySelector = s => (s === '[role=dialog]' ? (visible()[0] || null) : q0(s));
  env.ctx.document.querySelectorAll = s => (s === '[role=dialog]' ? visible() : qa0(s));
  env.ctx.getComputedStyle = el => el.style;
  env.stack = stack;
  env.pending = (panel, sha) => {
    const list = env.ctx.__ltPendingUploads || (env.ctx.__ltPendingUploads = []);
    list.push({ panel: String(panel), sha256: sha || 'a'.repeat(64), bytes: BYTES, t: env.clock.now(), state: 'pending' });
  };
  if (o.pending !== false) env.pending('15');
  env.run = args => vm.runInContext('(async (__LT_ARGS__) => {\n' + SRC + '\n})', env.ctx)(Object.assign({ panel: '15' }, args));
  return env;
}
const CAL = { method: 'PUT', path_template: '/api/images/{n}/replace' };
const BYTES = 1234;
/* A Blob-like body: the uploaded file's size, no content. */
const blob = n => ({ size: n, type: 'image/png', slice() { return this; } });
const saveAfter = (delay, status, latency) => env => env.after(delay, () => env.request('PUT', '/api/images/12/replace', blob(BYTES), { status, latency: latency || 300 }));

test('confirm-dialog: no dialog open is refused, nothing clicked', { skip: need }, async () => {
  const env = dialogEnv([]);
  const r = await env.run({ action: 'accept' });
  assert.equal(r.accepted, false);
  assert.equal(r.refused, 'no-dialog');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: action text returns the body without the button lines', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?']);
  const r = await env.run({ action: 'text' });
  assert.equal(r.text, 'Replace existing picture?');
  assert.equal(r.open, 1);
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: words holding No, OK or Yes stay whole in the body text', { skip: need }, async () => {
  const env = dialogEnv(['Note: Bookmark this before Yesterday ends?']);
  const r = await env.run({ action: 'text' });
  assert.equal(r.text, 'Note: Bookmark this before Yesterday ends?');
});

test('confirm-dialog: a deny-pattern dialog is refused and never clicked', { skip: need }, async () => {
  const env = dialogEnv(['Submit your answers now?']);
  const r = await env.run({ action: 'accept' });
  assert.equal(r.accepted, false);
  assert.equal(r.refused, 'deny-pattern');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: DENY wins over an ACCEPT pattern that also matches', { skip: need }, async () => {
  const env = dialogEnv(['Finish this attempt? It cannot be undone.']);
  await env.run({ action: 'text' });
  env.ctx.LT_DIALOG_ACCEPT.push(/.*/);   // someone adds a pattern by mistake
  const r = await env.ctx.acceptLtDialog({ panel: '15' });
  assert.equal(r.accepted, false);
  assert.equal(r.refused, 'deny-pattern');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: DENY reads the raw text, button labels included', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { buttons: ['Cancel', 'Submit', 'OK'] });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.refused, 'deny-pattern');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: an unrecognised dialog is refused and never clicked', { skip: need }, async () => {
  const env = dialogEnv(['Leave this page?']);
  const r = await env.run({ action: 'accept' });
  assert.equal(r.refused, 'not-on-accept-list');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: two visible dialogs are refused, nothing clicked', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?', 'Replace existing picture?'], { stacked: true });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.refused, 'several-dialogs');
  assert.equal(env.clicks, 0);
  const t = await env.run({ action: 'text' });
  assert.equal(t.text, null);
});

test('confirm-dialog: two OK buttons in one dialog are refused, nothing clicked', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { buttons: ['OK', 'Cancel', 'OK'] });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.refused, 'several-ok-buttons');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: no pending upload for the panel is refused, nothing clicked', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { pending: false });
  let r = await env.run({ action: 'accept' });
  assert.equal(r.refused, 'no-pending-upload');
  env.pending('14');
  r = await env.run({ action: 'accept' });
  assert.equal(r.refused, 'no-pending-upload');
  r = await env.run({ action: 'accept', panel: '' });
  assert.equal(r.refused, 'no-panel-key');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: one accepted dialog binds one upload; the next dialog is refused', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?', 'Replace existing picture?']);
  const r1 = await env.run({ action: 'accept' });
  assert.equal(r1.accepted, true);
  assert.deepEqual(JSON.parse(JSON.stringify(r1.upload)), { panel: '15', sha256: 'a'.repeat(64) });
  assert.equal(env.ctx.__ltPendingUploads[0].state, 'bound');
  const r2 = await env.run({ action: 'accept' });
  assert.equal(r2.accepted, false);
  assert.equal(r2.refused, 'no-pending-upload');
  assert.equal(env.clicks, 1);
  assert.equal(env.stack.length, 1);
});

test('confirm-dialog: the drain action is gone', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?', 'Replace existing picture?']);
  const r = await env.run({ action: 'accept-all' });
  assert.equal(r.ok, false);
  assert.equal(r.fatal, 'unknown-action');
  assert.equal(env.clicks, 0);
});

test('confirm-dialog: uncalibrated, the click is followed by the 6,000 ms wait', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: saveAfter(200, 200) });
  const t0 = env.clock.now();
  const r = await env.run({ action: 'accept' });
  assert.equal(r.accepted, true);
  assert.equal(env.clicks, 1);
  assert.equal(r.save.signal, 'fallback');
  assert.equal(r.save.waited_ms, 6000);
  assert.equal(env.clock.now() - t0, 6000);
  assert.equal(r.ack_method, 'PUT');
  assert.equal(r.ack_path_template, CAL.path_template);
  assert.equal(r.ack_status, 200);
  assert.equal(r.save.ack_reason, 'carried-the-file');
  assert.equal(r.still_open, false);
});

test('confirm-dialog: uncalibrated, telemetry is never taken as the acknowledgement', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => e.after(50, () => e.request('POST', '/api/telemetry', '{"e":1}', { latency: 50 })) });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.save.signal, 'fallback');
  assert.equal(r.ack_method, null);
  assert.equal(r.save.ack_reason, 'no-request-carried-the-file');
});

test('confirm-dialog: uncalibrated, the file-carrying request wins over earlier telemetry', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => { e.after(20, () => e.request('POST', '/api/telemetry', '{}', { latency: 30 })); saveAfter(200, 200)(e); } });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.ack_method, 'PUT');
  assert.equal(r.ack_path_template, CAL.path_template);
});

test('confirm-dialog: uncalibrated, a file of another size is not this upload', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => e.after(100, () => e.request('PUT', '/api/images/12/replace', blob(99), {})) });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.ack_method, null);
});

test('confirm-dialog: a tab hidden for part of the fallback wait reports unconfirmed', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => { e.after(100, () => e.setHidden(true)); e.after(500, () => e.setHidden(false)); } });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.save.signal, 'unconfirmed');
  assert.equal(r.save.hidden_during_wait, true);
});

test('confirm-dialog: a tab hidden during the fallback wait reports unconfirmed', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => e.after(100, () => e.setHidden(true)) });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.save.signal, 'unconfirmed');
  assert.equal(r.save.hidden_during_wait, true);
});

test('confirm-dialog: calibrated, the wait ends on the matching request plus the quiet window', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: saveAfter(200, 200, 300) });
  const r = await env.run({ action: 'accept', calibration: CAL, quiet_ms: 300 });
  assert.equal(r.save.signal, 'net');
  assert.ok(r.save.waited_ms >= 800 && r.save.waited_ms < 1200, String(r.save.waited_ms));
});

test('confirm-dialog: calibrated, a long poll does not hold the quiet window open', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], {
    onClick: e => { saveAfter(200, 200, 300)(e); e.after(50, () => e.request('GET', '/api/poll', null, { status: null })); }
  });
  const r = await env.run({ action: 'accept', calibration: CAL, quiet_ms: 300, max_ms: 15000 });
  assert.equal(r.save.signal, 'net');
  assert.equal(r.save.capped, false);
});

test('confirm-dialog: calibrated, a matching request still open at max_ms is unconfirmed, not net', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], {
    onClick: e => { saveAfter(100, 200, 100)(e); e.after(250, () => e.request('PUT', '/api/images/13/replace', blob(BYTES), { status: null })); }
  });
  const r = await env.run({ action: 'accept', calibration: CAL, quiet_ms: 300, max_ms: 1500 });
  assert.equal(r.save.signal, 'unconfirmed');
  assert.equal(r.save.capped, true);
});

test('confirm-dialog: calibrated, a later matching failure is an error', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], {
    onClick: e => { saveAfter(100, 200, 100)(e); e.after(250, () => e.request('PUT', '/api/images/13/replace', blob(BYTES), { status: 500, latency: 100 })); }
  });
  const r = await env.run({ action: 'accept', calibration: CAL, quiet_ms: 300 });
  assert.equal(r.save.signal, 'error');
  assert.equal(r.save.status, 500);
});

test('confirm-dialog: calibrated with no matching request ends unconfirmed at max_ms', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => e.after(100, () => e.request('POST', '/api/telemetry', '{}')) });
  const r = await env.run({ action: 'accept', calibration: { dialog: CAL }, max_ms: 5000 });
  assert.equal(r.save.signal, 'unconfirmed');
  assert.ok(r.save.waited_ms >= 5000);
});

test('confirm-dialog: calibrated, 202 is not proof of a save', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: saveAfter(100, 202) });
  const r = await env.run({ action: 'accept', calibration: CAL, max_ms: 5000 });
  assert.equal(r.save.signal, 'unconfirmed');
  assert.equal(r.save.status, 202);
});

test('confirm-dialog: a read-method calibration cannot confirm an upload', { skip: need }, async () => {
  // A malformed record must never turn a file-carrying GET, HEAD or OPTIONS into proof.
  for (const method of ['GET', 'HEAD', 'OPTIONS']) {
    const env = dialogEnv(['Replace existing picture?'], {
      onClick: e => e.after(100, () => e.request(method, '/api/images/12/replace', blob(BYTES), { latency: 100 }))
    });
    const r = await env.run({ action: 'accept', calibration: { method, path_template: CAL.path_template } });
    assert.equal(r.save.signal, 'fallback', method);
    assert.equal(r.ack_method, null, method);
    assert.match(String(r.next), /fresh-load verify/);
  }
});

test('confirm-dialog: calibrated with a failed save request reports error', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: saveAfter(100, 500) });
  const r = await env.run({ action: 'accept', calibration: CAL });
  assert.equal(r.save.signal, 'error');
  assert.equal(r.save.status, 500);
});

test('confirm-dialog: under a service worker the calibrated record is ignored', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: saveAfter(100, 200), serviceWorker: true });
  const r = await env.run({ action: 'accept', calibration: CAL });
  assert.equal(r.save.signal, 'fallback');
  assert.equal(r.save.sw_controlled, true);
  assert.equal(r.save.waited_ms, 6000);
});

/* A save Lt sends through a fetch reference it kept from before this script hooked fetch:
 * the script never sees the request. */
const unseenSave = (delay, size) => e => e.after(delay, () => {
  const p = e.fetch0(new URL('/api/images/12/replace', F.ORIGIN).href,
    { method: 'PUT', body: blob(size == null ? BYTES : size), __plan: { status: 200, latency: 300 } });
  p.catch(() => {});
});

test('confirm-dialog: calibrated, a save the script cannot see stays unconfirmed', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: unseenSave(100) });
  const r = await env.run({ action: 'accept', calibration: CAL, max_ms: 3000 });
  assert.equal(r.accepted, true);
  assert.equal(r.save.signal, 'unconfirmed');
  assert.equal(r.save.capped, true);
  assert.match(String(r.next), /fresh-load verify/);
});

test('confirm-dialog: uncalibrated, a save the script cannot see leaves nothing to calibrate from', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: unseenSave(100) });
  const r = await env.run({ action: 'accept' });
  assert.equal(r.save.signal, 'fallback');
  assert.equal(r.ack_method, null);
  assert.equal(r.save.ack_reason, 'no-request-carried-the-file');
  // A fallback timer cannot identify the transport or authorize another page.
  assert.match(String(r.next), /fresh-load verify/);
});

test('confirm-dialog: calibrated, a matching request for another file is not this upload\'s save', { skip: need }, async () => {
  const env = dialogEnv(['Replace existing picture?'], { onClick: e => e.after(100, () => e.request('PUT', '/api/images/12/replace', blob(99), { latency: 300 })) });
  const r = await env.run({ action: 'accept', calibration: CAL, max_ms: 3000 });
  assert.equal(r.save.signal, 'unconfirmed');
  assert.equal(r.save.capped, true);
});
