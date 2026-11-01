# Failure modes

Use receipt names and fresh-load verification to distinguish refused writes from unsaved writes.

| Symptom | Cause and action |
|---|---|
| A page has text items but nothing to type into. | `div.studentAnswer` displays model content. Editors remain detached until focus. Generated scripts use `input.set('answer', html)` and never use the editor. |
| A returned field reads `[BLOCKED: Sensitive key]`. | One browser tool's output filter replaces values whose field names contain `author`. Rename the returned field. `predefined_image` avoids that name match. |
| A returned field reads `[BLOCKED: Base64 encoded data]`. | The same output filter can flag a long camelCase value such as `allowImageAndAnnotation`. Return a short token instead. |
| A returned hash reads `[BLOCKED: Base64 encoded data]`. | The filter flags a 64-digit hex value such as a full sha256; a 16-digit prefix passes. Receipts return `source_payload_sha16` and upload `sha16`, and `record` binds the full sha256 of the payload file on the host. `record` treats any value starting `[BLOCKED:` as redacted. |
| Checking a new returned field. | `tests/browser/redaction_triggers.js` encodes these three triggers: a field name containing `author`, a hex value of 32 or more digits, a camelCase word of 20 or more letters. The receipt tests for capture, media, fill, verify, probe, Check Answer, upload, dialog and model answer run it. |
| Every panel reports unknown occupancy. | A plugin-key list incorrectly classified content panels as answerable. Test for an `input` key in `panelState`. Dividers and prose are content. |
| A table with a title row appears occupied. | Header rows and header columns contain authored content. Exclude them from occupancy. `cellsOf` must also try `columns` when `cells` is empty. |
| A responsive page's tool call times out at 45 seconds. | A Chromium-family browser may be holding the local-network permission prompt. Ask the user to allow the connection and wait for the answer. |
| The browser connection stops responding. | A native modal may be open. Return control for inspection. Lt's Start Over uses an in-page dialog, but an unknown control may open a native modal. |
| Local read-back matches, but a fresh load shows an empty target. | Lt saves after `set` returns. Backbone sync counts only when its transport matches an observed write request. Each key needs a successful response other than 202 on the calibrated endpoint. On an uncalibrated origin, scripts wait at least 4 seconds and for `jQuery.active` to return to its base. Navigating at once lost 12 of 20 writes in one measured run. |
| A receipt reports `save_signal: unconfirmed`. | The call did not navigate. Run that page's fresh-load verify before any other page. Open a new tab while saves may remain in flight, because reloading the filling tab can cancel them. A matching local model cannot confirm persistence. |
| A receipt reports `save_signal: fallback`. | The origin is uncalibrated. The fallback wait is not persistence proof. The fresh-load verify provides that proof. |
| A receipt reports `calibration_stale: true`. | With `stale_reason: client`, the page loads another Lt build (other core bundles before `DOMContentLoaded`, query strings included) than the save record names: `emit_fill.py record` drops the old records, and the next scripts calibrate again. With `stale_reason: client-unknown`, the page showed no navigation or resource timing, so no build marker: the call ran on the fallback wait and `record` keeps the records. Page chunks never make a record stale. |
| Every run-data key stays pending with `keys_weak` above 0. | The panels carry no `panelState.uuids`, so the save keys are 3- or 4-digit panel keys, which only LtNet's run-data adapter can acknowledge. Read the `run_data` counts in `save_diag`: `keys_identified=0` with `as_prop` or `as_value` above 0 means the body names panels in a shape the adapter does not accept; `reply_bare` above 0 means Lt replied without a success marker. Both keep the fallback wait and the fresh-load verify. Report the counts; do not widen the match by hand. |
| The calibration history shows `client-sig-retired`. | The save record was bound to the `c1-` signature of 0.2.3 and earlier, which page chunks changed. It was dropped once and the next scripts calibrate again. |
| `confirm_dialog.js` returns `refused: no-pending-upload`, `several-dialogs` or `several-ok-buttons`. | No upload of this document raised that dialog, or the dialog is ambiguous. Nothing was clicked. Report it to the user and wait. |
| A browser tool returns `{}` from an IIFE fill script. | The tool did not await the returned promise, so the save wait did not complete. Generated scripts end in a top-level `await`. Evaluate the whole generated file. |
| `check-url` refuses every page with `recapture-needed`, or the plan prints `ROUTES REFUSED`. | The capture has no `route_source`, or the plan has no `route_source` key (built before measured routes). Lt serves a Background page at `/run/background` and flow page N at `/run/page/N-1`, so a captured URL can be one page late and land past a Checkpoint, and nothing in such a capture reliably marks a Background page. Capture the module again with `--arg module_only=true` (one read-only call), then plan again. Never shift URL numbers by hand. |
| `check-url` exits 2 with "both claim" or "numbered pages reach it". | Two plan pages claim one route, or a URL number exceeds the pages before it. Capture the module again; the plan is not edited by hand. |
| A receipt reports `fatal: wrong-run` or `preview-url`. | The tab holds another run or a preview. Open the run identified by the capture. Never write to the preview. |
| A receipt reports `fatal: stub-hash-mismatch`. | The stub's text changed in transit. Render it again and evaluate the file unchanged. |
| A receipt reports `fatal: lib-integrity`. | In served mode, a fetched source differs from the build. Stop and resolve the source mismatch before rendering again. |
| A receipt reports `guard_trips` above 0. | A commit mutator was called and refused. Stop and report the call. Never retry through another mutator. A trip during Lt's start-up shows as `lt-not-painted`, below. |
| A receipt reports `fatal: lt-not-painted`. | No flow page read `current()` true within `fill.save_wait.render_wait_ms`, so the call wrote nothing. When `guard_last` names `removeCommittedCheckpoints` or `removePagesAfterUncommittedCheckpoints`, Lt's own start-up called that mutator after the guard had locked it, and the start-up stopped before Lt painted the page. The guard leaves those 2 mutators open until a page paints or the wait runs out, so this trip needs a start-up slower than the wait. When the receipt carries `guard_late_define_blocked` and no trip, Lt's start-up may have stopped on its own `Object.defineProperty` of a named commit mutator; quote that field in the report. The same document cannot recover. Stop and report it as any trip. When the user agrees, run `check-url`, navigate to the page again and run the guard as the next call. |
| A subpage child reports `no-panel`. | Its model sits under `panelState.content.subpages[].rows[].cells[].panels[]`. A walk limited to the panel's own content can miss undisplayed subpage children. |

See [write-paths.md](write-paths.md) for result handling and replacement rules.


## Check Answer

Check Answer unknown states (`verdict-unread`, `dialog-refused`, `dialog-stuck`, `guard-trip`) end the call without another write. `lt-changed-state`, `shape-changed`, `write-mismatch` and ownership failures stop the item. `save-fallback`, `save-unconfirmed` and `save-error` require a fresh-tab verify before navigation. `not-finished` resumes with prior receipts. Unknown RAT classification holds as `rat-unmarked`; settled RAT evidence refuses as `rat-page`.

An unread verdict leaves a pending dialog whose response cannot identify its originating panel.
`fresh-document-required` blocks all further checks in that document, including after script reloads.
Load a fresh document before resuming remaining `not-finished` items.

A Check Answer classification-changed refusal means the live title, author content, panel identities or attempt settings differ from the capture. Missing pins also refuse the pass. Stop checks, load a fresh document, recapture and classify the page before regenerating check scripts.
