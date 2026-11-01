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

## Page-script bodies: the body server

The body server carries code into the page; it never receives page content. With
`browser.body_server` set, a page-cache miss fetches the rendered body from
`http://127.0.0.1:<port>/b/<sha256>` in place of the chunk fallback
(`references/_shared/browser-adapters.md`, section Body server).

- **Where it runs.** The Lt page in the user's browser fetches from the host's loopback, so
  the server runs on the host. An agent jail with its own network namespace cannot reach it
  and does not need to: the agent only renders and sends the short loader.
- **What it answers.** `GET /b/<64 lowercase hex>` and the preflight, nothing else: no write,
  no listing. A Host other than `127.0.0.1:<port>` gets 421. A missing or foreign Origin gets
  403 and no CORS headers. Only `lt.origin` gets `Access-Control-Allow-Origin` and
  `Access-Control-Allow-Private-Network: true`. It serves `<data>/js-bodies/<sha256>.gz.b64`
  only when that is a regular file directly in the folder, opened without following a link,
  at most 1 MiB, and its text decodes to bytes whose SHA-256 is the name (409 otherwise). The
  page checks the SHA-256 again before it runs anything, so a planted file cannot run.
- **The local-network prompt.** Chromium asks once per site, and the answer stays with the
  site in the browser profile. A long-lived server therefore adds no prompts; restarting it
  changes nothing. While the prompt waits, the fetch hangs without an error, so the loader
  gives up after 8 s with `server-timeout` and the chunk fallback runs. After the user allows
  the site, later loaders fetch normally. A site the user refused, or a page whose
  Content-Security-Policy blocks `http://127.0.0.1`, fails at once with `server-unreachable`.
  Earlier runs fetched scripts from the loopback sink on the Lt page itself, so Lt's policy
  allowed it then.
- **Stale connections.** A single-threaded loopback server once held a stale connection after
  hours idle and froze the page's requests. The body server handles each connection in its own
  thread, closes every connection after one reply and drops a connection idle for 10 s.

## Host helpers run from a host-only copy

`sink.py`, `emit_fill.py serve` and the body server run on the host, outside every agent
jail. An agent that works in the repository or the data folder can edit files there, and a
helper started from such a folder would run that edit with the user's full rights.

- Install the body server with `python3 tools/install_body_server.py --dest <folder>`. It
  checks the server against `packaging/host-files.sha256` (and `--expect-sha256`, taken from a
  signed release), copies it read-only into a folder outside the repository and the data
  folder, and writes `lt-body-server.service` beside it. It never enables or starts the unit.
  The unit runs `python3 -I`, so neither the working folder nor `PYTHON*` variables add code.
- Start `sink.py` and `emit_fill.py serve` only from a skill folder unpacked on the host from
  a release archive whose `SHA256SUMS` line you checked, with `python3 -I`.

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
