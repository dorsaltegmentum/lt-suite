<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Fill speed: event-driven save waits and batching

A fill call ends when Lt has saved each write, and no sooner. A calibrated origin waits for
each write's own save request; an uncalibrated origin keeps the old fixed floor. Once a
2-page batch passes a fresh-load check, one browser call fills up to 5 pages.

## Why a wait at all

Lt starts its save request only after a model `set` has returned, and a navigation before
that request completes cancels it. In one measured run, 12 of 20 writes were lost when the
script navigated straight away; written again with a wait, all 12 saved.

`jQuery.active` idles at 1 on Lt, which points to a long-lived request. Global network idle
therefore cannot prove a save, and the old scripts used fixed floors instead:

| Old wait | Where | Length |
|---|---|---|
| save floor | `fill_lib.js`, after the last write | at least 4,000 ms, cap 20,000 ms |
| verify floor | `fill_lib.js` `verify()` in the filling document | 4,000 ms |
| dialog settle | `confirm_dialog.js`, after the click | 6,000 ms |

## The new wait

`shared/browser/lt_net.js` (global `LtNet`) keeps a ledger of keys. `fill_lib.js` calls
`LtNet.expect(key, model)` immediately before each write, then one
`LtNet.waitForSave(keys, cfg.save_wait)` covers every key of the call.

A key is saved when either:

- the written Backbone model fires `sync` for a transport correlated with an observed
  same-origin write request that ends with a 2xx status other than 202, or
- a same-origin request other than GET, HEAD or OPTIONS, started after the key's write, names
  the key exactly (a path segment, a query value, or a string value in a JSON or form body),
  and ends with a 2xx status other than 202.

A request naming several keys saves only keys with explicit successful outcomes in its
response. Missing outcomes stay pending; explicit errors fail their keys. A Backbone `sync`
through a transport the watcher cannot identify also stays pending. On calibrated origins,
both acknowledgement paths require the recorded method and path template.

A WebSocket send or a `sendBeacon` call marks a key sent, never saved. A status of 0 or 400
and above marks it failed. GET polls, telemetry and requests that started before the write
change nothing. Request bodies are read in memory to match keys and are never stored or
returned.

End states, in `save_signal`:

| Signal | Rule |
|---|---|
| `model`, `net` | calibrated origin: every key saved, then no counted request open for `quiet_ms` (300) |
| `fallback` | uncalibrated origin, or a page a service worker controls: at least `fallback_ms` (4,000) after the last write and `jQuery.active` back at its base |
| `unconfirmed` | `max_ms` (20,000) after the last write with keys pending, or the tab was hidden during a fallback wait |
| `error` | any key failed |

`unconfirmed` and `error` stop the call without navigation. The next step is a fresh-load
verify of that call's pages. Open a new tab when a save may remain in flight; reloading the
filling tab could cancel it. Every timeout counts from the last write, on `performance.now()`.

The dialog wait works the same way: with a dialog record, it waits for one request matching
the recorded write method and path template, carrying this upload's file, started after the
click and answered 2xx other than 202, then the quiet window; otherwise 6,000 ms. An unseen
transport stays unconfirmed under calibration. A fallback timer always directs the caller
to fresh-load verification.

## Calibration

Calibration decides which requests may acknowledge a key on an origin. It is recorded in
`<data>/state/calibration.json`, per Lt origin, with a format version.

1. The first filled page of an origin runs on the fallback wait. Its fill script carries a
   `save` ticket, a pending record written to `calibration.json` before the script runs.
2. `emit_fill.py record` reads that call's receipt and a verify receipt from a fresh load of
   another document on the same Lt client build. When every ticket item was written, every
   written item (exact ids, at least one) reads `ok`, and every key has an identified save on
   one write endpoint, it writes the `save` record:
   source (`model` or `net`), method, path template with typed parameters (`{uuid}`, `{hex}`,
   `{n}`), the Lt client build (`client_sig`) and the format version. From then on only
   requests matching that method and template count. An unidentified transport leaves the
   origin on the fallback wait even when the fresh-load verification succeeds.
3. The next scripts include one 2-page trial batch with a `cross_page` ticket. A fresh-load
   verify with every ticket item written and `ok`, plus acknowledgement of every key on the
   calibrated wait, writes `cross_page`, and later calls batch
   up to `fill.max_pages_per_call` pages (default 5). Items left unwritten make the trial
   inconclusive, and the next scripts emit another. A new `save` record clears a passed
   `cross_page` record, which must be proved again.
4. Any unconfirmed or failed wait, or any item missing from the fresh load, writes
   `cross_page` failed: one page per call from then on, with the reason in the origin's
   history. A miss after a wait that claimed saved also drops the `save` record. A changed
   Lt client build (the origin and path of every script the page loads, hashed into
   `client_sig`) makes the record stale: LtNet waits on the fallback rule, and `record` drops
   the `save` record and a passed `cross_page` record.

Each ticket is used once, and a client change voids every pending ticket. A batch script
refuses before its first write when its record is missing, of another version, taken on
another client build, or when a service worker controls the page. `emit_fill.py calibrate
--item <id>` runs the same `save` calibration on one named item: success also clears a failed
`cross_page` record, failure drops the `save` record.

The dialog wait calibrates the same way, from a confirm-dialog receipt on the 6,000 ms
fallback: the one request after the click that carried the uploaded file (a body part of
exactly its size) and answered 2xx other than 202 gives the method and path template. Telemetry and polls
never count.

Only text, table and multiple-choice pages join a batch: their writes are model `set` calls
that work on a page the tab is not showing. Label and category writes need the page shown.
A panel on a page that never sends a save stays pending, so a batch returns `unconfirmed`,
never `ok`.

## Expected effect (estimate, not measured)

| Step | Uncalibrated | Calibrated |
|---|---|---|
| save wait per call | at least 4,000 ms | save request time plus 300 ms |
| dialog wait | 6,000 ms | save request time plus 300 ms |
| browser calls for N pages | N | about N / 5 after the trial batch |

The fresh-load verify is unchanged: it is the proof of persistence on every origin.

## Measured

No calibrated run has been measured for this document yet. Every recorded call keeps its
`save_signal` and `waited_ms` in `<data>/runs/<module_id>/save-waits.json`, and
`make_fill_report.py` copies them into `fill-report.json`. The first calibrated run adds a
table here: calls, pages per call, median and maximum `waited_ms` per signal, and verify
misses.

## Tests

The lost-save regression stays covered with a fake clock and fake network
(`tests/browser/harness.js`). `tests/fill/fill_lib.test.js` runs 20 writes, navigates at
once, and requires 0 lost writes under seeded latencies of 200 to 3,000 ms, a 600 ms debounce
with telemetry, a serial save queue, a 5 s render wait, a WebSocket send, a GET long poll and
a fetch reference taken before the hooks. Two controls (no wait, and a rule that clears every
key on the first request) must lose writes. `tests/fill/test_emit_fill.py` covers the
calibration outcomes: auto-calibration, a failed calibration that keeps the fallback, a
cross-page success and a cross-page miss that falls back to one page per call.


## Check Answer

Check Answer processes one displayed page per call with one press in flight. There is no press cap. Finite candidates, never-repeated states and the decided-part progress rule bound each item. Event waits end at a 90,000 ms deadline; each write or press reserves 10,000 ms plus dialog and save caps. Retry saves must end model or net. Check calls never grant calibration; a failed post-write verify drops the save record.
