---
name: answer-provenance-style
description: The provenance discipline as it is applied to a drafted answer
review_by: 2027-10-24
evidence: [docs/design-notes.md#segments-and-connectives, docs/design-notes.md#reference-quarantine, docs/design-notes.md#model-answer-policy, docs/design-notes.md#question-digest]
status: active
---

# House style for a drafted answer

## Every sentence is tagged, or it is a bug

A drafted answer is a list of segments, and a segment of kind `claim` carries at least one provenance reference. An untagged factual claim is not a stylistic lapse; it is the defect the tier system exists to make visible, because an untagged sentence is indistinguishable from a remembered one.

`connective` is the escape hatch and it is capped twice: per segment, and as a share of the whole answer. Both caps exist because the obvious way to defeat the tagging rule is to relabel claims as connective text, and one cap alone does not catch a long answer made of short unattributed fragments.

## Descending a tier records a reason

The reason is one of `absent`, `insufficient-depth`, `contradicted`. Descending without one is the same class of defect as an untagged claim: it hides whether the higher tier was checked and found wanting, or simply not checked. The tiers, their order and their tag templates come from the `[[tiers.tier]]` entries in the config.

A tier conflict is surfaced, never reconciled. When a lower tier contradicts a higher one, both go in the draft with the conflict named. Silently preferring one is a decision the student did not get to see.

## The last tier needs a resolvable identifier

A peer-reviewed citation without a DOI or PMID that resolves is a fabrication, and it is deleted rather than softened. Softening it ("possibly", "one study suggests") keeps the fabricated object in the answer and removes only the signal that it is checkable.

## Grader reference files are not a source

`reference-*.json` preserves submitted responses for evaluation. Drafting excludes evaluation references. The provenance rail rejects their filenames in corpus entries and citations. Evaluation tasks may inspect this ground truth without modifying it; compare SHA256 snapshots around each run.

The same applies inside the configured notes graph (`notes.graph_path`). Its pages are ordinary course notes except for pages that are this suite's own module output, which carry the stamp property (`notes.stamp_property`, default `lt-module`). Those are quarantined, never cited as Tier 1.

## Lt's model answer

When `model_answer.read` is on, Lt's model answer may be cited with provenance `lt-model-answer`. When it is off, an answers or reference file that cites it fails the `model-answer-policy` rail.

## Four skips are mandated, and each is recorded with its reason

`report-page`, `rat-page`, `already-answered`, `course-feedback` identify excluded items. Record each exclusion in `answers.json` using `status: skipped` and its `skip_reason`. The fill plan preserves these exclusions. The already-answered status protects a response the student has submitted.

## A clean TODO is a success

Degrading to a prepared offline file plus precise placement instructions beats a guessed selector or an invented answer, every time. The TODO carries its blocker and a placement precise enough to act on in a later session with no memory of this one.
