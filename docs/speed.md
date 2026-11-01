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
response. Missing outcomes stay pending; explicit errors fail their keys. A single key
without a per-key outcome saves only when the reply carries no failure marker at any depth.
On every path, the run-data adapter below included, a failure marker inside an item (an
object with an identity field) fails that item whatever its own success says, and sibling
items keep their own outcomes. A failure marker outside every item, a failed plain value that
names no key of the request, or a reply deeper than 32 levels or larger than 50,000 values
(read only in part; `resp_truncated` in `net_diag` counts it) confirms no key of the request. A reply whose
top-level object says `"success": false` fails every key the request named: Lt answers HTTP
200 with it and keeps nothing. A Backbone `sync` through a transport the
watcher cannot identify also stays pending. On calibrated origins, both acknowledgement paths
require the recorded method and path template. Keys shorter than 6 characters never match
by value: a 3- or 4-digit panel key also occurs in unrelated numbers.

### Lt's run-data save

On the Lt deployment measured for this change, every write is saved by one `PUT /i/<instance>/v/2.0/api/student/courses/<course>/runs/<run>/data`
for 1 to 4 panels, with no Backbone `sync` on the written model. Its panels carry no
`panelState.uuids`, so the save key is the 3- or 4-digit panel key. LtNet has a typed adapter
for this request, used only when its instance, course and run are the page's own:

- The body is parsed as JSON. A key is identified only by a typed identity field holding
  exactly one of the panel's ids: `panelKey` or `panel_key` (the panel key), `dataKey` or
  `data_key` (the dataKey), `uuid`, `panelUuid` or `panel_uuid`, or a property name that is
  the uuid itself. Each kind matches only its own ids. Lt's dataKey is the panel key minus 1,
  so a dataKey never reads as a panel key. Plain values, other property names and substrings
  identify nothing.
- A bare 2xx saves nothing. A key is saved by an explicit per-key success in the reply, or by
  the atomic-snapshot rule: the PUT carries the run's panel data as one document, so a reply
  whose top-level object says `"success": true` (or `ok: true`, or a status of ok, saved or
  success), with no failure marker anywhere, saves every key the body identified. A
  top-level failure, or an explicit failure for a key, fails it; 202 leaves it pending.
- A body that is not JSON, or that names no expected key this way, leaves the keys pending.
  The call ends on the fallback rule, and the fresh-load verify decides.

The real body shape is not recorded yet. The first live run after this change records it as
counts in `save_diag` (below), and the fresh-load verify still proves every save before a
calibration is written.

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

### Save diagnostics

A fallback receipt carries `save_diag`: up to 20 request rows by method, templated path and
status. `model_sync` is true on a row when a written model's Backbone `sync` answered a
request of that row; a sync tied to no observed request marks no row. A run-data row adds
`run_data`, counts only: bodies by shape (`body_json`, `body_unparsed`, `body_none`), expected
and identified keys, keys seen in a typed field by kind (`as_panel_field`, `as_data_field`,
`as_uuid_field`), as a property name (`as_prop`) or as a plain value (`as_value`), and replies
by shape (`reply_per_key`, `reply_success`, `reply_failure`, `reply_bare`). Every fill
receipt also carries `keys_weak` (keys shorter than 6 characters), `net_matched` (requests
that named a key) and `save_key_source` (writes keyed by `panelState.uuids` or by the panel
key). `record` copies `save_diag`, `keys_weak` and `net_matched` to `save-waits.json`.

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
   Lt client build makes the record stale: LtNet waits on the fallback rule, and `record`
   drops the `save` record and a passed `cross_page` record.

`client_sig` names the Lt build by its core bundles: the origin, path and query string of
each script element whose resource timing entry started before `DOMContentLoaded`, sorted and
hashed (`c2-<count>-<hash>`). A bundle versioned by its query (`/core.js?v=1`, then `?v=2`)
therefore signs as a new build; a signed or cache-busting query drops the record each load,
which costs a calibration and never trusts a stale one. Chunks a page loads later (page
plugins) never count, so a record holds across pages and across the fill and verify tabs of
one build. A page chunk that starts before `DOMContentLoaded` does count, so two pages of one
build can sign differently and drop the record; a measured Lt build marker would fix that. A page without
navigation or resource timing has no signature: a signed record is set aside for that call
(`stale_reason: client-unknown`), and `record` keeps it. Receipts also carry `scripts_sig`,
the hash of every script element with page chunks included, as a diagnostic only. 0.2.3 and
earlier signed every script (`c1-`), so a page chunk could read as a new build; `scripts` and
`record` drop a save record bound to a `c1-` signature, or to none, once, together with its
`cross_page` record and pending `cross_page` tickets, with the event `client-sig-retired` and
its reason in the history, and the origin calibrates again. Whether
Lt's entry bundle names carry content hashes still needs one live check.

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

## Script transfer: page cache and body server

An inline browser tool sends each page script as model output. A loader of about 2 to 4 KB
runs a body from the origin's page cache; a miss used to mean sending the whole body, 13 KB
(guard) to 65 KB (fill runner), in chunks. In one measured run, misses resent 291,039 bytes in
25 calls, and the 17 inputs over 9 KB took 1,280 s.

Three causes, three changes:

| Cause | Change |
|---|---|
| Every release emptied the cache: each body carried the lt-suite version | Bodies carry no version; an unchanged script keeps its entry |
| The cache kept 6 entries in first-stored order, so a hit did not protect an entry | Least recently used order within 524,288 characters; a hit refreshes its entry |
| A miss had no source but model output, and every loader repeated the plan policy | Optional loopback body server on the host; loaders name a long policy by SHA-256 |

With `browser.body_server` set, `ltjs.py render` writes each cacheable body and long policy
to `<data>/js-bodies/<sha256>.gz.b64`. On a miss the loader fetches `GET /b/<sha256>` from
`127.0.0.1`, checks the SHA-256 as it does for a cached entry, stores the body and runs it.
Any failure returns `need:'payload'` with a `reason`, and the chunk fallback still works. The
server answers only that route, only to `lt.origin`, only with Host `127.0.0.1:<port>`, and
runs on the host from the copy `tools/install_body_server.py` checked against
`packaging/host-files.sha256` (`skills/lt-module-capture/references/transport.md`).

Loader bytes, measured on synthetic inputs: a 22-page plan with run-shaped page URLs for the
guard and `fetch_media`, sink arguments for `capture` and `fetch_media`, and the 5-page
one-text-item-per-page fill fixture for fill and verify (`--form expression --cache`).

| Loader | 0.2.3 | Body server off | Body server on |
|---|---|---|---|
| guard with the plan (capture or fill skill) | 3,652 | 3,775 | 2,882 |
| guard without a plan | 1,731 | 1,854 | 2,795 |
| capture | 1,874 | 1,997 | 2,938 |
| `fetch_media` with the plan | 3,799 | 3,922 | 3,029 |
| fill, per call | 3,116 to 3,144 | 3,239 to 3,267 | 3,655 to 3,683 |
| verify, per page | 2,979 | 3,102 | 3,518 |
| verify, 5 pages at once | 3,611 | 3,734 | 4,150 |

The server-on loader carries about 940 bytes of fetch code, 190 more than before its miss
reasons became a fixed set. With the 22-page plan it drops about 1,900 bytes of policy text,
so the guard and `fetch_media` loaders shrink by 893 bytes; loaders with a short or no policy
grow. A miss then costs one local request, never a 13 to 65 KB resend. `render cache_status`
lists which bodies the cache holds, so a miss can be expected before it happens.

Not measured yet: a live run with the server, the browser's local-network prompt on the Lt
origin, and Lt's current Content-Security-Policy for `http://127.0.0.1`. Each failure mode
returns a named `reason` and falls back to the chunk path.

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
