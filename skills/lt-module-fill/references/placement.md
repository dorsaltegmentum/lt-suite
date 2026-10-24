# Resolving placements

Mapping items connect label text to a location or category.

The write types are `image-labeling`, `drag-and-drop` and `categories`.
Drafts may call the latter types `drag-and-drop-labeling` and `categorization`.

`answers.json` supplies `label_placements` as `{label, target}` entries.
`label` holds exact option text. `target` describes the destination.

A draft at `schema_version: 1` may describe its mapping in `segments`.
Read that prose and apply the same resolution checks.

## Read the live pools

Never take a label index from the capture.

The capture sorts label options alphabetically. Its positional `option_id` does not identify
the live collection position used by `addAnswer`.

Probe the target page and read the top-level `pools` block:

| Field | Meaning |
|---|---|
| `pools[panel_key].labels` | Label strings in live array order. |
| `pools[panel_key].categories` | Category strings in live array order. |
| `pools[panel_key].anchors` | Location indices and stimulus coordinates. |

Read `pools` rather than nested `evidence` fields.
Browser tools can truncate deeply nested values, including label and anchor pools.

`labelIndex` is the live array position whose string exactly equals the draft's `label`.

If no exact match exists, skip that item and request a new capture.
Do not choose a similar label or infer an index from option order.

## Resolve a location

The capture cannot supply a reliable `locationIndex`. Matching label and location indices is unsafe.

Each anchor entry has this shape:

```text
<locationIndex> <anchorX> <anchorY> <dropZoneLeft> <dropZoneTop>
```

Coordinates are proportions of the stimulus image. A `-` marks a missing value.

Open the captured stimulus from `media[].local_path` or the capture's media folder.
Multiply anchor coordinates by the image's pixel dimensions.

Draw numbered anchors onto the image with
`python3 "$SKILL_DIR/scripts/annotate_micrograph.py"`, `gridref.py` or another image tool.
Open the image in your image viewer.

Match the draft's target description to a numbered anchor.
Use that anchor's `locationIndex` in the write.

An identification read from an image remains a hypothesis. Cap confidence at `medium`.
Name the competing structure when an alternative remains plausible.

For categories, use the draft's `target_index` when present.
Otherwise resolve the category text against `pools[panel_key].categories`.

## Settle uncertainty

Read Lt's model answer with `$SKILL_DIR/scripts/browser/model_answer.js`.
Reading is enabled by default. Set `model_answer.read = false` to disable it.

For location mappings, `modelAnswer.linkedLabels` supplies label and location indices.
For categories, `modelAnswer.categoryAnswerItems` supplies category placements.

Resolve uncertain placements against that evidence.
After writing, read back `linkedLabels` or `categoryAnswerItems` and verify persistence from a fresh load.

Lt feedback describes what the platform accepts. It does not become a cited research tier.

A multiple-choice panel's `modelAnswer.selectedAnswers` reads empty.
Check Answer exposes correctness feedback when the user presses it during review, or when the check pass presses it with `fill.check_answer.enabled = true`.

When rendered feedback exists, split the class list into words.
Test exact membership of `correctAnswer` or `incorrectAnswer`.

Never search by substring. `"labelLocation incorrectAnswer"` contains `"correctAnswer"`.
A substring test once classified 11 incorrect placements as correct.

## Preserve cited disagreements

With `fill.check_answer.enabled` on, the check pass may change the on-page answer.
answers.json keeps the cited draft. The report keeps every press. The answer skill's
reconcile step re-reads each citation and records any supported dispute. The following
`lt_conflict` route applies with the setting off or when reviewing a model answer.


Re-read the citation when Lt feedback contradicts a cited draft.
If the citation still supports the answer, keep that answer written.

Do not clear or revert the answer to match Lt feedback.
Never submit to resolve a disagreement.

Record the disagreement only in `items[].lt_conflict` in `fill-report.json`:

| Field | Content |
|---|---|
| `observed_via` | `model-answer` or `check-answer`. |
| `held_answer` | The cited answer that remains written. |
| `lt_indicated` | The answer or feedback Lt indicated. |
| `draft_ref` | The citation supporting the held answer. |

For multiple-choice conflicts, use `observed_via: "check-answer"`.
Record that the model answer's selection was empty and feedback followed a user review click.

Do not put Lt disagreements in `answers.conflicts`.
That field holds conflicts between cited research tiers.

A verified item may remain `filled` while Lt marks it incorrect.
The conflict record must accompany the answer that remains written.
