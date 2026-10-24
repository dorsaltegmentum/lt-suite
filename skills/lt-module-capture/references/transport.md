<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Getting the capture from the page to disk

The page writes `module-<id>`, `reference-<id>` and the images to disk itself. The browser
tool gets back a short receipt only. The submitted answers therefore stay out of the agent's
context, and a browser tool that cuts long return values loses nothing.

## Route 1: the loopback sink

`sink.py` listens on `127.0.0.1:<browser.sink_port>` and saves into `<data>/incoming/`.

- **Who may write.** Requests must come from `lt.origin` or an explicit `--allow-origin`.
  The page first requests `/hello?n=<random nonce>`, without sending content or the token.
  The sink returns an HMAC-SHA256 proof bound to that nonce and the page origin.
  The page checks the proof before uploading anything. Each POST carries
  `X-Lt-Suite-Auth`, an HMAC over the destination name and SHA256 of the exact body.
  The token stays in the page and the sink process; HTTP never carries it.
  The token file has mode 0600 and is removed when the sink stops.
  Missing or foreign Origin gets 403; wrong authentication gets 401.
  A Host other than `127.0.0.1:<port>` gets 421.
  The scripts accept only `http://127.0.0.1:<port>`.
  A failed handshake returns `sink-unauthenticated` and sends no content.
- **What it writes.** The body must be a JSON object whose `module_id` equals the id in the
  path. It lands as `incoming/module-<id>.json`, or as `module-<id>.<n>.json` when that name
  is taken; `stamp_capture.py` picks the highest `n`. Images land in `incoming/media/`. They
  must be raster (png, jpg, gif, webp, bmp), and their first bytes must fit the extension.
  SVG is refused; `fetch_media.js` turns SVG into PNG inside the page first. A body over
  64 MB gets 413.
- **The browser's local-network prompt.** Browsers count `http://127.0.0.1` as potentially
  trustworthy, so mixed-content blocking does not apply. Chromium still asks the user, once
  per site, before a public page may contact a local address. Until the user answers, the
  request waits without any error, and the browser tool times out on a healthy page. Each
  POST therefore has a deadline (`post_ms`, 30 s by default) and fails with a named error
  when it passes. Only the Lt origin gets `Access-Control-Allow-Private-Network: true` in the
  preflight answer.
- **Config.** `sink.py --print-args` adds `drop_text_patterns` and `post_ms` to the render
  arguments when `capture.drop_text_patterns` or `capture.timeouts.post_ms` differ from
  their defaults.

## Route 2: the browser's download folder

With `--arg transport=download` the page saves each file through a Blob and `<a download>`.
Choose this route explicitly when the sink cannot be reached. Authentication and network failures never trigger downloads.

- Pick it for a sandboxed agent shell. Some sandboxes put the shell in a network namespace of
  its own, so the browser cannot reach a sink started there. A background process may also
  end together with the command that launched it.
- Chromium lets a site start one download by script; further ones are dropped until the user
  allows automatic downloads for that site. Make sure every file arrived.
- Chromium adds `" (1)"`, `" (2)"` to a name that is taken. An old file with the expected
  name therefore means the fresh bytes sit in a numbered copy. `collect_downloads.py` picks
  the newest copy of each file, moves or copies it into `<data>/incoming/` according to
  `paths.downloads_mode`, and overwrites nothing.

## One reference per run

`reference-<id>.json` keeps the student's answers from the FIRST capture of a run. The sink
returns 409 when `incoming/` or `captures/` already has one, and `collect_downloads.py` and
`stamp_capture.py` also refuse to replace it. A refusal from the sink does not trigger the
download route, so no second copy can slip in that way. A re-capture after a fill renders
with `module_only=true`, and the page then never builds the reference.

## Validation before anything moves

`stamp_capture.py` checks the incoming module against `module.schema.json` and the module
rails, and validates any incoming reference before moving any files into `captures/`. A failing capture stays
in `incoming/`; the exit code gives the reason (1 rail, 2 schema). The reference is checked
from the same bytes that are then moved. Invalid JSON, non-object references and mismatched module identities leave incoming files untouched.

## When neither route works

Stop and tell the user which route failed and why. Do not ask the page to return the module
or the reference through the browser tool: the reference would then pass through the agent's
context, which the quarantine forbids.

## Captured text

The inert HTML parser drops comments, executable elements and hidden subtrees.
Unicode format characters are removed from captured strings.
Items and authored-content panels record removed nodes in `hidden_text_dropped`.
Agent-directed visible text keeps its wording and receives `suspect_text` reason codes.
The receipt reports only their count and identifiers in `untrusted_flags`.
Show those flagged items to the user before drafting any answers.

Collection creates data directories with mode 0700 and independent files with mode 0600.
Copy mode preserves browser downloads and their permissions.
After the module is stamped, collection accepts that existing module for the image pass.
