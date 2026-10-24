// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* ping: prove that the browser tool can run a rendered script in the Lt tab.
 *
 * This file is the BODY of an async function. ltjs.py wraps it (expression or function
 * form), prepends the never-submit guard and bakes arguments into __LT_ARGS__. It reads
 * the page and nothing else: no click, no write, no navigation, no network request, and
 * no page content beyond the origin and one boolean.
 *
 * Result: { ok, origin, path_kind, lt_client, expected_origin_matches }
 *   origin                    location.origin of the tab (scheme, host, port)
 *   path_kind                 "run-page" | "run-background" | "other": which kind of
 *                             address the tab is on, never the address itself
 *   lt_client                 true when the Lt Knockout client (window.ko) is loaded
 *   expected_origin_matches   true/false when __LT_ARGS__.origin was given, else null
 */
const args = (typeof __LT_ARGS__ === 'object' && __LT_ARGS__ !== null) ? __LT_ARGS__ : {};
const here = new URL(String(window.location.href));
const p = here.pathname;
let kind = 'other';
if (/\/student\/courses\/[^/]+\/runs\/[^/]+\/run\/page\/\d+\/?$/.test(p)) kind = 'run-page';
else if (/\/student\/courses\/[^/]+\/runs\/[^/]+\/run\/background\/?$/.test(p)) kind = 'run-background';
const expected = (typeof args.origin === 'string' && args.origin) ? args.origin.replace(/\/+$/, '') : null;
return {
  ok: true,
  origin: here.origin,
  path_kind: kind,
  lt_client: typeof window.ko !== 'undefined' && window.ko !== null,
  expected_origin_matches: expected === null ? null : expected === here.origin
};
