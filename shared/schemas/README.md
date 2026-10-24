<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Handoff contracts

Each skill hands its work to the next as one JSON file. These schemas define the files; the validator (`ltcore.validate`) adds the rails that JSON Schema cannot express. Skills that agree on a schema can be rebuilt one at a time.

| File | Edge | Written by | Read by |
|---|---|---|---|
| `module.schema.json` | capture to answer, diagram, fill, export | `lt-module-capture` | `lt-module-answer`, `mechanistic-diagram`, `lt-module-fill`, `lt-module-export` |
| `answers.schema.json` | answer to fill, diagram, export | `lt-module-answer` | `mechanistic-diagram`, `lt-module-fill`, `lt-module-export` |
| `fill-report.schema.json` | fill to the human, then the improvement loop | `lt-module-fill` | the human, the run review |
| `run.schema.json` | every skill to the improvement loop | every skill | the run review in `lt-suite-setup` |
| `reference.schema.json` | eval ground truth | `lt-module-capture` on a completed module | the eval grader only |
| `approvals.schema.json` | the user's recorded approvals for one run | `ltcore.approvals` (`emit_fill.py approve`) | `lt-module-fill`, the validator |
| `config.schema.json` | the config file | `lt-suite-setup` | every script (see `docs/configuration.md`) |

Validate any file:

```bash
python3 -m ltcore.validate <file.json>                          # schema inferred from the file name
python3 -m ltcore.validate fill-report.json --module module.json # adds the capture cross-checks
python3 -m ltcore.validate fill-report.json --module module.json --approvals approvals.json
python3 -m ltcore.validate reference.json --model-answer-read false
```

Run it from a skill folder as `python3 scripts/_shared/ltcore/validate.py <file.json>`, or from the repository with `PYTHONPATH=shared`. The file name picks the schema: `module*`, `answers*`, `reference*`, `fill-report*`, `run*`, `approvals*`. Exit codes: `0` valid, `1` rail violation, `2` schema violation, `3` usage, file or config error, or `jsonschema` not installed.

## Two capture files: `module.json` and `reference.json`

A completed module's page data holds the student's own submitted answers. If they sat in `module.json`, every eval of `lt-module-answer` would run with the reference answer already in context, and the eval would measure nothing. So capture writes two files:

- `module.json`: structure, prompts and boundaries. Item state is an `answered` boolean and nothing more.
- `reference.json`: the student's own answers and any feedback Lt returned after a Check Answer. Its flag `never_load_into_drafting_context` is true, and only the grader opens it. A draft that cites a `reference-*.json` file fails the validator.

Lt's model answer (`panelState.modelAnswer`) is readable from page data. Reading it is on by default; the config key `model_answer.read = false` turns it off. It never enters `module.json`, so captures stay shareable as eval fixtures. Capture stores it in `model-answer.json` in the run folder when the setting is on. A reference built from it records `provenance_of_reference: "lt-model-answer"`, and an answer that cites it uses `source_id: "lt-model-answer"`. With `model_answer.read = false` the `model-answer-policy` rail rejects both, and a fill report whose `lt_conflict` was observed through the model answer. A reference built from the model answer grades against Lt's key, not against the student, so evals should prefer the student's own answers.

## A draft is segments, not prose

Every sentence of a drafted answer must trace to a source tag. Checking that over a prose blob would mean splitting sentences with a regex, which breaks on abbreviations and decimal numbers. So `answers.json` makes the draft a list of segments, each `claim` or `connective`, and a `claim` cannot exist without a provenance reference that resolves.

No validator can tell a factual claim from linking prose, so a claim relabelled `connective` escapes citation. Two caps limit that: 120 characters per connective segment, and 25% of an answer's characters in total. The review packet shows connective segments apart, so the human sees what carries no tag.

## `pluginKey` to `type`

`pluginKey` is a function on Lt's view model; capture calls it. A generic `ko.unwrap` returns its source text.

| `pluginKey` | `type` | Default `automation` |
|---|---|---|
| `text-question` | `text-response` | structured-path |
| `select` | `multiple-choice` | structured-path |
| `table-question`, `table` | `table-completion` | resilient-selector |
| `image-annotation` with `inputMode: allowAnnotation` | `image-annotation` | resilient-selector |
| `image-annotation` with `inputMode: allowImageAndAnnotation` | `upload-and-annotate` | structured-path |
| `kcImageLabelling` | `image-labeling` | resilient-selector; degrade-todo if single-attempt |
| `drag-and-drop` | `drag-and-drop-labeling` | resilient-selector; degrade-todo if single-attempt |
| `categories` | `categorization` | resilient-selector; degrade-todo if single-attempt |
| `notebook` | `unmapped` (never written) | degrade-todo, permanent `notebook-never-written` |
| `subpage` | `subpage` (container) | structured-path |
| anything else | `unmapped` | degrade-todo, `capture_note` required |

`kcTextArea`, `image`, `divider`, `whitespace`, `button`, `audio`, `video` and `hotspot` are author content, not questions. They go in `pages[].content[]`, the stimulus `lt-module-answer` drafts against. A no-input panel of any other `pluginKey` goes there as `kind: "other"` with a `capture_note` naming its content keys.

A `subpage` is a container, not a question. Its children are absent from the parent's `allPanels`. Store recursively captured children in `items[]` on their containing page. Set `parent_item_id` for each child. `census` carries `captured_total` and `answerable_total` (containers excluded), and the validator recomputes both.

## Commit boundaries

`ltcore.boundaries` holds the one implementation the fill planner and the validator share.

| Class | Severity | What a fill run does |
|---|---|---|
| `checkpoint-page` | stop | stops before the page; the validator rejects a write at or past it |
| `single-attempt-item` | per-item | never submits the item and writes it only with a recorded single-attempt approval; later pages are filled, because moving between pages submits nothing |
| `unmapped` | per-item | the item becomes a TODO; the run continues |
| `run-level-commit` | never-navigate | the completion route is never visited |
| `report-page` | never-navigate | never visited; its panels mirror real ones |

A fill report may stop at any one-way door (`checkpoint-page`, `single-attempt-item`, `run-level-commit`); `unmapped` is never a reason to stop.

## Approvals

The user's yes is a record in `<data>/runs/<module_id>/approvals.json`, with the reply verbatim. `approval_id` is `"A-" + sha256(module_id + item_id + quote + recorded_at)[:8]`, so an edited record no longer matches its id. Two kinds:

- `single-attempt`: lets the fill write one single-attempt item. `answers.json` names it in `single_attempt_approval.approval_id`, and the fill report names it in the item's `approval_id`. Nothing ever clicks Submit.
- `waiver`: lets one waivable rail pass (`approval-manual-at-boundary`, `write-past-first-door`, `passed-single-approval`). The fill report's `user_decisions[]` entry names the rail, the items and the `approval_id`.

`python3 -m ltcore.approvals add|check|show` records and checks them. With `--approvals` the validator checks every id against the file; without it, only the id format.

Approval files reject malformed dates, duplicate item scopes and edited records.
An append that reuses an id for different contents fails without changing the file.
The validator checks parsed approvals with the same rules as approval files.
Capture cross-checks require matching module ids and use captured item types and attempts.

## Save waits

Fill reports may retain `save_waits[]` from browser receipts. Each entry requires
`pages`, `save_signal` and `waited_ms`. Optional fields record the receipt name and
SHA-256, key counts, save errors, calibration, success and a fatal reason.

Signals are `model`, `net`, `fallback`, `unconfirmed` and `error`. A null signal
records a call that ended before waiting. Missing measurements use null values.
Fresh-load receipts determine verification; a save signal alone proves no write.

## Rails

Schema violations are structural. Rails reject documents that parse but break a rule. `tests/rails` holds a document built to trip each one.

| Rail | Rule |
|---|---|
| `module-has-no-answer-key`: recursive scan for `modelAnswer`, `answerKey`, `isCorrect`, `correctAnswer`, `checked` | Lt's answer key never enters a capture or handoff file, including through free-form maps |
| `model-answer-policy` | with `model_answer.read = false`, nothing may cite `lt-model-answer` |
| Report page has `page_no: null`, `route: "complete"` and no items | `/page/<last>` redirects; its panels are mirrors and would double every count |
| Checkpoint page has `route: "checkpoint"`, no `page_no`, no `url`, no items, and a `checkpoint.committed` boolean | Lt routes it as `/run/checkpoint/<n>`; its index is not a URL segment |
| `checkpoint_pages` and the `checkpoints` inventory agree | Lt drops a committed checkpoint from the page flow; the inventory is what remembers it |
| `census` recomputed from the items | every count is confirmed a second way |
| Completion page implies `run_level_commit.present` | the run-level Commit is invisible from content pages |
| Single-attempt drag item implies `automation: degrade-todo` | a drag on a one-way door cannot be undone |
| `rat_candidate` only on an all multiple-choice page | a flag whose basis is false invites a rubber stamp |
| `degrade.reason` implies one `degrade.class`; `accepts_upload` matches `input_mode` | a TODO must say truthfully whether it will ever go away |
| `answered_evidence.filled_map` recomputes `answered` | the flag and its evidence must agree |
| `never_navigated` lists the completion route; `pages_filled` excludes report and policy-skipped pages | the completion page is out of bounds, not merely stop-before-Commit |
| No `pages_filled` entry or write at or past the first checkpoint | a run never advances through a live checkpoint |
| A single-attempt item written by automation carries an `approval_id`; never a drag | the user approves each single-attempt write |
| `result: filled` requires `verified: true`; a receipt decides both | every item is verified by a fresh load before the run moves on |
| Boundary stop implies `approval_mode: manual` | a page with a boundary is filled under manual approval |
| A waiver rests on a recorded approval and waives a real violation | an idle waiver would pass the next violation unread |
| Tier-4 `source_id` is a DOI or `PMID:` | an unresolvable identifier is a fabrication |
| A reason is recorded for every tier skipped down to the deepest cited | descending a tier needs a reason |
| Every claim cites; every citation is cited; conflicts resolve with the higher tier on top | untagged claims are bugs; uncited citations are decoration |
| `table_cells` and `label_placements` match the captured grid and label pool | a summary in prose is not an answer to a grid or a placement item |
| `source_module.question_digest` matches the recorded and supplied captures | missing source files fail closed |
| `source_answers.sha256` matches the referenced answers file | a report proves which draft it used |
| Answer and fill-report `item_id` values are unique | dictionary consumers cannot discard an earlier entry |
| `steered_run_wrong` requires before and after text | only evidence drives a skill edit |
| Timestamps are ISO 8601 with an offset | runs span machines; the `date-time` format checker needs extra packages, so the check runs in code |

## Conventions

- `schema_version` is `1` everywhere (`answers.json` also accepts `2`, which requires `label_placements` on placement items). Bump it on any breaking change and keep the old schema file.
- `additionalProperties: false` throughout: an unknown field is a skill drifting from the contract.
- `answers.json` binds to its capture by `sha256` (which capture) and `question_digest` (whether it still asks the same questions: `item_id`, `type`, `prompt_text`, `options[]` and the authored half of `constraints`, defined in `ltcore.digest`). The digest decides; a file-hash change under a matching digest is a note, because a post-fill re-capture always moves the file hash. `fill-report.json` binds to its answers file by `sha256`. Relative paths resolve against the file's own folder.
- Every approval requires the capture's `question_digest`. IDs hash the complete record and module ID using canonical JSON. Editing kind, rail, item scope, digest or reply invalidates the ID. Changed questions require a new approval; state changes preserve the question digest. Legacy approvals without a digest require a fresh recorded reply.
- `approvals.append(..., question_digest=question_digest(module))` records the binding. `approval_id` accepts scope keywords `kind`, `rail`, `item_ids`, `question_digest`, `approved_by` and `note`. Waiver verification also needs the current capture digest. Fill reports checked against approval records require the supplied capture.
- Policy-skipped pages forbid every written item, including failed writes through the view model, even when `pages_filled` omits that page.
- `python3 -m ltcore.digest --fix-pins <dir>` rewrites stale pins; `--fix-pins --check <dir>` reports them and exits 1.
- `tests/fixtures/examples/*.example.json` are synthetic: an invented microscopy practical, not a captured module. They are the template a skill emits against and the input to the rails tests. `module.example.json` exercises a report page, a policy-skipped RAT page, a single-attempt drag item, an upload-and-annotate panel with target geometry, a subpage and its child, and an unmapped type.
- Older files that still carry `permission` (module) or `permission_check` (run) validate after `ltcore.validate.drop_legacy_fields()` removes them, with one note. The permission gate those fields recorded is retired.
