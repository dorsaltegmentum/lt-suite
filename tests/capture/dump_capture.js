// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Run capture.js over a synthetic view model and print what it sent, for the Python tests.
 *
 *   node tests/capture/dump_capture.js demo                 # built-in scenario
 *   node tests/capture/dump_capture.js --spec <viewmodel.json> [--wrap]
 *   node tests/capture/dump_capture.js demo --sink URL --token T --origin O [--module-only]
 *
 * Without --sink, POSTs are intercepted and stdout is {"module", "reference", "receipt"}.
 * With --sink, the page's fetch goes to the real sink over HTTP with the Origin header a
 * browser would add, and stdout is {"receipt"}.
 */
'use strict';
const fs = require('node:fs');
const H = require('./vm_harness.js');
const { demoEnv } = require('./scenarios.js');

function parse(argv) {
  const a = { scenario: null, spec: null, wrap: false, sink: null, token: null, origin: null, moduleOnly: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--spec') a.spec = argv[++i];
    else if (k === '--wrap') a.wrap = true;
    else if (k === '--sink') a.sink = argv[++i];
    else if (k === '--token') a.token = argv[++i];
    else if (k === '--origin') a.origin = argv[++i];
    else if (k === '--module-only') a.moduleOnly = true;
    else a.scenario = k;
  }
  return a;
}

(async () => {
  const a = parse(process.argv.slice(2));
  let env;
  if (a.spec) env = H.envFromSpec(JSON.parse(fs.readFileSync(a.spec, 'utf8')), { wrap: a.wrap });
  else if (a.scenario === 'demo') env = demoEnv();
  else { process.stderr.write('usage: dump_capture.js demo | --spec FILE [--wrap] [--sink URL --token T --origin O]\n'); process.exit(2); }

  const args = { module_only: a.moduleOnly };
  let reply;
  if (a.sink) {
    Object.assign(args, { transport: 'sink', sink: a.sink, token: a.token });
    reply = async (name, req) => {
      const r = await fetch(req.url, { method: req.method, body: req.body,
        headers: Object.assign({}, req.headers, { Origin: a.origin }) });
      const text = await r.text();
      return { status: r.status, ok: r.ok, text: async () => text, json: async () => JSON.parse(text) };
    };
  }
  const r = await H.runCapture(env, { args, reply, helloReply: reply });
  const out = a.sink ? { receipt: r.receipt } : { module: r.doc, reference: r.reference, receipt: r.receipt };
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(1); });
