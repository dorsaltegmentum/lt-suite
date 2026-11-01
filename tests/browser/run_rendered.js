// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* run_rendered.js: run an ltjs.py output, or the guard's URL rule, in the fake Lt page and
 * print JSON. test_ltjs.py drives it; it is not a node:test file.
 *
 *     node tests/browser/run_rendered.js <spec.json>
 *
 * spec.mode "run":      {file, path?, pages?, navigate?: [url...], raw_commit?: bool}
 *   prints {result, ran, trips, navigate: [{url, canceled}], commits, raw_commit_threw}
 * spec.mode "classify": {policy, urls: [...]}
 *   prints [{url, href (the URL as the browser normalizes it), allowed, reason, page_index}]
 * spec.mode "decide":   {policy, from, urls: [...]}: the guard's navigate decision in a tab at
 *   `from` whose painted page is unknown; prints [{url, allowed, reason, page_index}]
 * spec.mode "sequence": {files: [...], path?, fetch_origin?}: run each file in turn in one page
 *   with an in-memory localStorage. With fetch_origin, fetch is node's own and carries that
 *   Origin header, as a browser does on a cross-origin request. Prints {results, keys}.
 */
'use strict';
const fs = require('fs');
const H = require('./harness.js');

async function main() {
  const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  if (spec.mode === 'classify') {
    const env = H.makeEnv();
    env.loadShared('lt_guard');
    const G = env.ctx.LtGuard;
    const out = spec.urls.map(u => {
      let href = null;
      try { href = new URL(u).href; } catch (e) { href = null; }
      return Object.assign({ url: u, href }, JSON.parse(JSON.stringify(G.classify(u, spec.policy))));
    });
    process.stdout.write(JSON.stringify(out));
    return;
  }
  if (spec.mode === 'decide') {
    const env = H.makeEnv({ path: spec.from, rootReady: false });
    env.loadShared('lt_guard');
    const G = env.ctx.LtGuard;
    await G.run(spec.policy, async () => ({ ok: true }));
    const out = spec.urls.map(u => Object.assign({ url: u }, JSON.parse(JSON.stringify(G.allowed(u)))));
    process.stdout.write(JSON.stringify(out));
    return;
  }
  if (spec.mode === 'sequence') {
    const env = H.makeEnv({ path: spec.path });
    const data = new Map();
    env.ctx.localStorage = { get length() { return data.size; }, key: i => [...data.keys()][i] ?? null,
      getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => { data.set(k, String(v)); },
      removeItem: k => { data.delete(k); } };
    if (spec.fetch_origin) {
      env.ctx.AbortController = AbortController;
      env.ctx.fetch = (url, init) => fetch(url, { ...init, headers: { Origin: spec.fetch_origin } })
        .then(r => ({ status: r.status, text: () => r.text() }));
    }
    const results = [];
    for (const f of spec.files) {
      const r = await env.runRendered(fs.readFileSync(f, 'utf8'));
      results.push(JSON.parse(JSON.stringify(r === undefined ? null : r)));
    }
    process.stdout.write(JSON.stringify({ results, keys: [...data.keys()].sort() }));
    return;
  }
  const pages = spec.pages ? spec.pages.map(p => H.page(p)) : undefined;
  const env = H.makeEnv({ path: spec.path, pages });
  const text = fs.readFileSync(spec.file, 'utf8');
  const result = await env.runRendered(text);
  const nav = (spec.navigate || []).map(u => ({ url: u, canceled: env.navigateEvent(u).canceled }));
  let rawThrew = null;
  if (spec.raw_commit) {
    try { env.root.commitClicked(true); rawThrew = false; } catch (e) { rawThrew = true; }
  }
  const G = env.ctx.LtGuard;
  process.stdout.write(JSON.stringify({
    result: JSON.parse(JSON.stringify(result === undefined ? null : result)),
    ran: env.ctx.__ran === true,
    trips: G ? G.trips() : null,
    net_installed: !!(env.ctx.__ltNet && env.ctx.__ltNet.installed),
    navigate: nav,
    commits: env.commits.length,
    raw_commit_threw: rawThrew
  }));
}

main().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(3); });
