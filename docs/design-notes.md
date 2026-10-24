<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Stable evidence anchors for the shipped lexicon. -->

# Design notes

These sections explain the rules cited by the generic lexicon.

## State split

Repository files describe reusable behaviour. The configured data directory holds captures, approvals, receipts, calibration and personal proposals.

Reinstallation must preserve run state without requiring private files in the code repository.

Run review proposes lexicon changes after corroboration across at least two runs. A human reviews and applies the change, then compares evaluations.

## View-model capture

The client view model exposes lesson structure and input state more directly than rendered DOM text.

Capture reads known members, calls the appropriate accessors and recurses through subpage models. It records unknown shapes instead of assuming they are empty.

An unmapped question produces a per-item gap. A boundary blocks navigation independently of question support.

## Model-answer policy

Model-answer reading defaults to enabled and is controlled by `model_answer.read`.

Permitted model-answer citations use `lt-model-answer` provenance. Disabling the setting makes those citations fail validation.

Captured `module.json` files exclude answer-key fields regardless of the setting. Separate storage keeps questions suitable for uncontaminated drafting evaluations.

## Checkpoint detector

Checkpoints, single-attempt items and run completion require separate detectors.

Checkpoint text can be an untranslated template. A committed checkpoint can also alter the remaining checkpoint list.

The boundary map combines explicit state and item properties. An unmapped boundary stops filling.

## Completion page out of bounds

Completion can expose a run-level commit that content pages do not reveal.

The suite excludes the completion route and report page entirely. It does not visit completion to locate a button.

Navigation checks and persistent page guards both enforce the exclusion.

## Two numbering schemes

The ordered content-page list and completion route do not share one reliable numbering scheme.

A numbered final-page URL can redirect to completion. A report page therefore uses its distinct route rather than a guessed page number.

Panel identity also requires a page index because panel keys can repeat across pages.

## Segments and connectives

A factual claim belongs in a `claim` segment with resolving provenance.

A `connective` segment allows short linking prose. Per-segment and whole-answer caps limit the amount of untagged text.

The caps cannot recognise disguised factual claims. Human review remains necessary.

## Member allow-list

Client models can expose answer keys, commit methods and unrelated state beside useful content.

Serialisation reads an explicit member allow-list. It does not recursively copy every property.

Unknown members require inspection and an explicit implementation change.

## Reference quarantine

Evaluation references contain ground truth and must remain outside drafting context.

The same rule excludes the suite's stamped module exports from independent source material in a notes graph.

Reference hashes before and after grading establish that the grader preserved the ground truth.

## Save wait

A local input change does not prove that Lt persisted it.

The save watcher associates each expected write with its own model acknowledgement or keyed request response. A telemetry request, GET poll or unrelated completed save cannot acknowledge every field.

The first filled page uses fallback waiting. Fresh-load verification of every item creates origin calibration automatically.

A verified two-page trial enables batches up to the configured default limit of five pages. A missing or unconfirmed item reduces the origin to one page per call and records why.

## Fresh-load verify

A view model can retain unsaved values in memory.

Verification reloads the page and reads the persisted state in a fresh document. Same-document reads carry a distinct receipt and cannot substitute for this proof.

Missing values remain failures even when an earlier save receipt looked successful.

## Single-attempt approvals

A single-attempt write requires the human's approval for that exact item.

The approvals file records the quoted instruction, item, module, approval kind and identifier. The validator checks those relationships before accepting the draft's approval reference.

Approval never authorises the suite to submit or cross a Checkpoint.

## No bulk harvesting

Capture operates only on modules the human names.

The suite does not enumerate courses or runs, call session-authenticated instance APIs or retrieve bulk image tiles.

Sequential retrieval and bounded operations support this scope. Account terms still govern the captured Content.

## Never submit

Submission remains a human action.

The suite does not click Submit, Commit or Finish, accept a committing dialog, pass an uncommitted Checkpoint or navigate to completion.

Persistent guards block known commit mutators and native form submission. The skill rule remains binding wherever a guard cannot observe an action.

## Text cleaners

Cleaner hooks are optional configured commands. The runner passes an argument vector without a shell and enforces a timeout.

A fill-text cleaner must preserve the payload's keys and job count. Failure stops payload preparation.

Finalisation removes marks and metadata without rewriting wording. Built-in PDF metadata handling does not require an external cleaner service.

## Why drawio

draw.io supplies an editable text format, a free editor and the endpoint geometry needed by the diagram grammar.

The build keeps content, layout and rendering reviewable. SVG, PDF, PNG and standalone HTML provide additional output forms.

Editable output does not establish scientific accuracy. The mechanism still requires source review.

Palette ranking measures each derived theme's text contrast, strokes, fills, simulated colour-vision separation and greyscale separation. Licence form does not affect the score.

The current balanced scores in `shared/palettes/ranking.json` are 57.42 for the light default, `tol-bright`, and 68.23 for the dark default, `petroff-10-on-primer-dark-high-contrast`. The print palette is `tol-bright`. Eligibility gates exclude themes that fail required contrast or separation checks.

Noto Sans and Noto Sans Mono are the bundled font defaults. Atkinson Hyperlegible Next and Mono provide the catalog's low-vision option. Read [fonts and palettes](fonts-and-palettes.md) for measurements, glyph coverage and evidence limits.

## Question digest

A whole-file hash changes when a capture's answer state changes.

The question digest instead covers question identity, type, prompt, options and authored constraints. Its definition belongs in one shared implementation.

Draft validation uses that digest to reject changed questions while distinguishing a state-only recapture.

Media, image geometry and answer state are excluded. A changed stimulus image can preserve the question digest; inspect changed whole-file hashes before accepting the new capture.
