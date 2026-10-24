# Write paths

Generated scripts write through Lt models. They never use an editor or simulate a drag.

Resolve every target by `(page_index, panel_key)`. Apply the plan's boundaries before writing.

Probe occupancy immediately before each write. The captured `answered` flag cannot establish live emptiness.
An occupied target requires approval before replacement. Unknown occupancy skips that item.

## Paths by item type

| Type | Write path | Checks |
|---|---|---|
| `text-response` | `input.set('answer', html)` | Write the panel's input model. No focus, editor, change event or blur is needed. |
| `table-completion` | `cell.set('content', html)` per body cell | Never write a header row or header column. Refuse the whole table if a target cell is missing or authored as a header. |
| `multiple-choice` | `input.set('selectedAnswers', [i])` | Check each option's text before using its index. Clear with `input.set('selectedAnswers', [])`. Generated scripts never click a radio control. |
| `image-labeling`, `drag-and-drop` | `currentInputModel().addAnswer(labelIndex, locationIndex)` | Resolve labels against the live pool. Resolve locations from marked stimulus anchors. See [placement.md](placement.md). |
| `categories` | `currentInputModel().addAnswer(labelIndex, categoryIndex, analytics)` | The third argument is required. Omitting it can throw after the write lands. Resolve labels by text. |
| `upload-and-annotate` | `$SKILL_DIR/scripts/browser/upload.js` | Set the panel's own file input from embedded bytes and dispatch `change`. The script never clicks. See [micrographs.md](micrographs.md). |
| `image-annotation` with `allowAnnotation` | No upload path | Record a TODO. This panel accepts no upload. |
| Any unmapped type | No write | Record a TODO for that item and continue within the plan. |

`selectedAnswers` holds option indices. Check option text because an index alone cannot identify an answer.

For categories, `target_index` may supply `categoryIndex`. Captured categories retain their displayed order.
`labelIndex` always comes from the live label array.

Upload items remain TODO records in generated payloads and receipts until the separate upload path handles them.

## CKEditor background

Generated scripts never use CKEditor. These notes describe the older fallback path.

A text panel's `div.studentAnswer` displays the answer. It is not a textarea.
Focusing that element attaches a detached inline editor.

The older text fallback wrote through the attached editor API and blurred it.
Verification read `input.answer`, followed by a fresh load in another tab.

A table panel shares one inline editor across its cells. Attachment happens asynchronously.
Writing immediately after focusing a cell can change the previously focused cell.

The older table fallback waited for `td.contains(editor.element.$)` before calling
`setData`, `fire('change')` and `focusManager.blur(true)`. It skipped cells where attachment failed.

That fallback used 2 cells per tool call. A 12-cell loop exceeded the 45-second budget.
A timeout during the loop could leave later writes targeting the wrong cell.

Do not introduce this fallback into generated scripts. Report any model write that fails fresh-load verification.

## Replace mode

Replace mode uses a second payload. It does not remove occupancy guards.

Generate it with `--replace --against <earlier-fill-payload.json>`.
Only text and table items support replacement. Unchanged items are not emitted.

Each replacement job carries `oh`, the hash of the earlier fill's value.

| Live value | Result |
|---|---|
| Hash matches `oh` | Write the new draft and report `replaced`. |
| Value already matches the new draft | Leave it unchanged and report `already-new`. |
| Any other nonempty value | Leave it unchanged and report `changed-since-fill-skipped`. |

An empty target may be written and reported as `written`.
An earlier payload cannot authorize replacement of unrelated work.

## Receipts and verification

Receipts contain counts, per-item results, hashes and lengths. They never contain answer text.

| Result group | Names | Action |
|---|---|---|
| Successful write or match | `written`, `replaced`, `already-new` | Confirm persistence from a fresh load. |
| Skipped | `occupied-skipped`, `occupancy-unknown-skipped`, `changed-since-fill-skipped` | Preserve the target. Ask the user and wait for an answer before any replacement. |
| Refused | Every other per-item result name outside these successful, skipped and failed groups | Fix the cause or retain a TODO. Never bypass the guard. |
| Failed | `write-mismatch`, `partial`, `write-not-visible`, `write-threw`, `save-error` | Resolve any pending save verification. Re-run that page once within the same guards, then verify from a fresh load. |

Named refusals include `refused-single-attempt`, `single-attempt-not-locked` and `no-panel`.

Route refusals include `preview-url`, `wrong-run`, `wrong-page`, `wrong-course`,
`wrong-instance`, `wrong-origin` and `not-a-run-page`. A fatal receipt writes nothing.

Read-only or locked targets, forbidden pages, changed page titles and failed probes also refuse writes.
Header geometry, missing cells and unresolved placement pools must not be bypassed.

A local read-back cannot prove persistence. Verify from a second tab with a fresh run load.
See [failure-modes.md](failure-modes.md) for save signals that require verification before navigation.
