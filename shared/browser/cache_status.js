// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* cache_status.js: report this origin's lt-suite page cache without running any cached code.
 *
 *     python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render cache_status --form <form> [--cache] [--arg names=guard,capture]
 *
 * ltjs.py adds __LT_ARGS__.expect = {name: body sha256} for the names (default: every cacheable
 * script this skill renders), computed for the same form as that skill's loaders, plus the
 * cache budget and the lt-suite version. Per name the receipt gives `state` (present: stored
 * and its sha256 checks; missing; corrupt: stored but fails the check), `at` (position in the
 * index, 0 = least recently used, -1 = not indexed) and `chars`. It also counts every lt-suite
 * entry and its characters, and says whether storage takes a write. Hashes appear as 12-hex
 * prefixes; stored code and inputs never appear in the receipt.
 */
const PRE = 'lt-suite:js:', INDEX = PRE + 'index';
const expect = __LT_ARGS__.expect || {};
const out = { ok: true, version: __LT_ARGS__.version || null, storage: 'writable', entries: 0,
  chars: 0, budget: __LT_ARGS__.budget || null, other: 0, index_ok: true, bodies: {} };
let s = null;
try { s = localStorage; void s.length; } catch (e) { s = null; out.storage = 'blocked'; }
if (s) {
  try { s.setItem('lt-suite:probe', '1'); s.removeItem('lt-suite:probe'); } catch (e) { out.storage = 'read-only'; }
}
const check = async (b64, want) => {
  try {
    if (typeof DecompressionStream !== 'function') return false;
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(b64)) return false;
    const bin = atob(b64);
    let u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    u = new Uint8Array(await new Response(new Blob([u]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());
    const d = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', u)), x => x.toString(16).padStart(2, '0')).join('');
    return d === want;
  } catch (e) { return false; }
};
let index = [];
const values = new Map();
if (s) {
  try {
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (typeof k === 'string' && /^lt-suite:js:[a-f0-9]{64}$/.test(k)) {
        const v = s.getItem(k);
        if (v !== null) values.set(k, v);
      }
    }
  } catch (e) { out.storage = 'blocked'; }
  try {
    const a = JSON.parse(s.getItem(INDEX));
    if (Array.isArray(a)) index = a.filter(k => typeof k === 'string'); else out.index_ok = false;
  } catch (e) { out.index_ok = false; }
}
out.entries = values.size;
for (const v of values.values()) out.chars += v.length;
const known = new Set();
for (const [name, want] of Object.entries(expect)) {
  if (typeof want !== 'string' || !/^[a-f0-9]{64}$/.test(want)) continue;
  const key = PRE + want;
  known.add(key);
  const v = values.get(key);
  out.bodies[name] = { want: want.slice(0, 12), state: v === undefined ? 'missing' : (await check(v, want) ? 'present' : 'corrupt'),
    at: index.indexOf(key), chars: v === undefined ? 0 : v.length };
}
for (const k of values.keys()) if (!known.has(k)) out.other++;
return out;
