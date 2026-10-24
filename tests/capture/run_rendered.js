// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Run a page script rendered by ltjs.py (guard + __LT_ARGS__ + capture.js) in the vm harness.
 *
 *   node tests/capture/run_rendered.js <rendered.js> <function|expression> [demo|plain]
 *
 * Prints {receipt, names, downloads, cancelled, navigate_listeners, after_commit}. The
 * receipt is what the browser tool would return; `after_commit` is the error a raw call to
 * $root.commitClicked() raises once the script has finished (the guard stays installed).
 */
'use strict';
const fs = require('node:fs');
const H = require('./vm_harness.js');
const { demoEnv } = require('./scenarios.js');

(async () => {
  const [file, form, scenario] = process.argv.slice(2);
  if (!file || !['function', 'expression'].includes(form)) {
    process.stderr.write('usage: run_rendered.js <file> <function|expression> [demo|plain]\n');
    process.exit(2);
  }
  const env = scenario === 'demo' ? demoEnv()
    : { root: H.baseRoot([H.contentPage('a', [H.panel('text-question', '11', { question: H.obs('Q') }, { answer: H.obs('') })]),
                          H.reportPage()], []) };
  const r = await H.runCapture(env, { rendered: { text: fs.readFileSync(file, 'utf8'), form } });
  let after = null;
  try { env.root.commitClicked(1); } catch (e) { after = String(e && e.message); }
  process.stdout.write(JSON.stringify({
    receipt: r.receipt, names: r.names, downloads: r.downloads, cancelled: r.cancelled,
    navigate_listeners: r.navigateListeners, after_commit: after
  }) + '\n');
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(1); });
