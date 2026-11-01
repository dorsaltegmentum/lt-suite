// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* redaction_triggers.js: what one browser tool's output filter blocks in a returned value
 * (skills/lt-module-fill/references/failure-modes.md), as a check for script receipts.
 *
 *   const { redactionHits } = require('../browser/redaction_triggers.js');
 *   assert.deepEqual(redactionHits(receipt), []);
 *
 * Triggers, each seen in a live run:
 *   a field name containing "author"      -> "[BLOCKED: Sensitive key]" (table_author_grids)
 *   a hex value of 32 or more digits      -> "[BLOCKED: Base64 encoded data]" (a full sha256)
 *   a camelCase word of 20 or more letters -> "[BLOCKED: Base64 encoded data]"
 *                                            (allowImageAndAnnotation)
 * A 16-hex prefix passes, so receipts return sha16 values.
 */
'use strict';

const walk = (node, trail, out) => {
  if (Array.isArray(node)) {
    node.forEach((v, i) => walk(v, `${trail}[${i}]`, out));
  } else if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node)) {
      const here = trail ? `${trail}.${k}` : k;
      if (/author/i.test(k)) out.push(`${here}: field name contains "author"`);
      walk(v, here, out);
    }
  } else if (typeof node === 'string') {
    if (/^[0-9a-f]{32,}$/i.test(node)) out.push(`${trail}: ${node.length}-digit hex value`);
    else if (node.length >= 20 && /^[a-z]+(?:[A-Z][a-z]+){2,}$/.test(node)) out.push(`${trail}: long camelCase value`);
  }
  return out;
};

/* Every path in a returned value that the filter would block, after a JSON round trip (what
 * the browser tool sends back). */
const redactionHits = value => walk(JSON.parse(JSON.stringify(value === undefined ? null : value)), '', []);

module.exports = { redactionHits };
