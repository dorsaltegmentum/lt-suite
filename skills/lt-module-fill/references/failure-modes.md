# Failure modes

Use receipt names and fresh-load verification to distinguish refused writes from unsaved writes.

| Symptom | Cause and action |
|---|---|
| A page has text items but nothing to type into. | `div.studentAnswer` displays model content. Editors remain detached until focus. Generated scripts use `input.set('answer', html)` and never use the editor. |
| A returned field reads `[BLOCKED: Sensitive key]`. | One browser tool's output filter replaces values whose field names contain `author`. Rename the returned field. `predefined_image` avoids that name match. |
| A returned field reads `[BLOCKED: Base64 encoded data]`. | The same output filter can flag a long camelCase value such as `allowImageAndAnnotation`. Return a short token instead. |
| Every panel reports unknown occupancy. | A plugin-key list incorrectly classified content panels as answerable. Test for an `input` key in `panelState`. Dividers and prose are content. |
| A table with a title row appears occupied. | Header rows and header columns contain authored content. Exclude them from occupancy. `cellsOf` must also try `columns` when `cells` is empty. |
| A responsive page's tool call times out at 45 seconds. | A Chromium-family browser may be holding the local-network permission prompt. Ask the user to allow the connection and wait for the answer. |
| The browser connection stops responding. | A native modal may be open. Return control for inspection. Lt's Start Over uses an in-page dialog, but an unknown control may open a native modal. |
| Local read-back matches, but a fresh load shows an empty target. | Lt saves after `set` returns. Backbone sync counts only when its transport matches an observed write request. Each key needs a successful response other than 202 on the calibrated endpoint. On an uncalibrated origin, scripts wait at least 4 seconds and for `jQuery.active` to return to its base. Navigating at once lost 12 of 20 writes in one measured run. |
| A receipt reports `save_signal: unconfirmed`. | The call did not navigate. Run that page's fresh-load verify before any other page. Open a new tab while saves may remain in flight, because reloading the filling tab can cancel them. A matching local model cannot confirm persistence. |
| A receipt reports `save_signal: fallback`. | The origin is uncalibrated. The fallback wait is not persistence proof. The fresh-load verify provides that proof. |
| A receipt reports `calibration_stale: true`. | The page loads another Lt client build than the one the save record names. The call ran on the fallback wait. `emit_fill.py record` drops the old records, and the next scripts calibrate again. |
| `confirm_dialog.js` returns `refused: no-pending-upload`, `several-dialogs` or `several-ok-buttons`. | No upload of this document raised that dialog, or the dialog is ambiguous. Nothing was clicked. Report it to the user and wait. |
| A browser tool returns `{}` from an IIFE fill script. | The tool did not await the returned promise, so the save wait did not complete. Generated scripts end in a top-level `await`. Evaluate the whole generated file. |
| A receipt reports `fatal: wrong-run` or `preview-url`. | The tab holds another run or a preview. Open the run identified by the capture. Never write to the preview. |
| A receipt reports `fatal: stub-hash-mismatch`. | The stub's text changed in transit. Render it again and evaluate the file unchanged. |
| A receipt reports `fatal: lib-integrity`. | In served mode, a fetched source differs from the build. Stop and resolve the source mismatch before rendering again. |
| A receipt reports `guard_trips` above 0. | A commit mutator was called and refused. Stop and report the call. Never retry through another mutator. |
| A subpage child reports `no-panel`. | Its model sits under `panelState.content.subpages[].rows[].cells[].panels[]`. A walk limited to the panel's own content can miss undisplayed subpage children. |

See [write-paths.md](write-paths.md) for result handling and replacement rules.


## Check Answer

Check Answer unknown states (`verdict-unread`, `dialog-refused`, `dialog-stuck`, `guard-trip`) end the call without another write. `lt-changed-state`, `shape-changed`, `write-mismatch` and ownership failures stop the item. `save-fallback`, `save-unconfirmed` and `save-error` require a fresh-tab verify before navigation. `not-finished` resumes with prior receipts. Unknown RAT classification holds as `rat-unmarked`; settled RAT evidence refuses as `rat-page`.

An unread verdict leaves a pending dialog whose response cannot identify its originating panel.
`fresh-document-required` blocks all further checks in that document, including after script reloads.
Load a fresh document before resuming remaining `not-finished` items.

A Check Answer classification-changed refusal means the live title, author content, panel identities or attempt settings differ from the capture. Missing pins also refuse the pass. Stop checks, load a fresh document, recapture and classify the page before regenerating check scripts.
