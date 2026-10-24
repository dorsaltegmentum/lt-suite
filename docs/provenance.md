<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Claim attribution, source ordering and reference quarantine. -->

# Provenance

Every factual claim in a drafted answer must resolve to a recorded source.

## Configured tiers

The `[[tiers.tier]]` entries define source labels, identifiers and tag templates.

The neutral source order is:

1. Course material.
2. A licensed reference or guideline service.
3. Published clinical or institutional guidance.
4. Peer-reviewed literature.

Issuer ordering, fallback searches and jurisdiction framing come from configuration. See [configuration](configuration.md).

Read the highest applicable tier first. A configured source name does not prove access.

When a tier requires an access record, record actual access. If a licensed source is unavailable, state that limit and follow the configured fallback order.

## Descent reasons

Each descent records one reason:

| Reason | Meaning |
|---|---|
| `absent` | The checked tier contains no applicable material |
| `insufficient-depth` | Available material does not support the needed detail |
| `contradicted` | Available material conflicts with the claim under review |

A descent reason records why a higher tier did not settle the claim. It cannot stand in for checking that tier.

## Segments

An answer contains `claim` and `connective` segments.

Every `claim` segment carries at least one resolving provenance reference. Connective text links the answer without introducing unsupported facts.

The documented connective caps are 120 characters per segment and 25 percent of an answer's characters.

`shared/schemas/answers.schema.json` sets the segment limit. `ltcore.validate` enforces the 25 percent share.

These caps detect bulk relabelling. They cannot prove that a connective segment contains no factual claim.

## Conflicts

Record both sources when tiers conflict. Name the disagreement and identify the higher tier.

With Check Answer retries off, keep a supported answer and record the disagreement. With retries on, the page may move to Lt’s key; answers.json keeps the cited draft, the report keeps every press, and reconciled.json identifies each segment’s origin.

A Check Answer verdict is evidence for review.

## Literature identifiers

A peer-reviewed citation requires a resolvable DOI or PMID.

Check that the identifier identifies the cited work and supports the associated claim. Remove fabricated or unresolved citations.

## Reference quarantine

`reference-*.json` contains evaluation ground truth. Drafting never reads or cites these files.

The same exclusion applies to the suite's exported module pages in the configured notes graph. They carry `notes.stamp_property`, whose default is `lt-module`.

Ordinary notes can be source material. Stamped module outputs cannot return as independent Tier 1 evidence.

Evaluation graders may read reference files. Preserve their bytes and compare hashes before and after grading.

## Model-answer policy

`model_answer.read` defaults to `true`.

When enabled, a separate read can store model-answer material in the run directory and cite it as `lt-model-answer`.

When disabled, answers and references citing that provenance fail the `model-answer-policy` rail.

`module.json` excludes answer-key fields under either setting. Keep captured questions separate from keys.

## Binding and skips

The draft records its source capture hash and question digest.

Answer skips retain a schema-defined reason, including `irat-policy`, `trat-answers-unavailable`, `already-answered`, `course-feedback` and `read-only-mirror`. Report-page exclusions belong to the fill plan rather than the answer skip enum.

The fill plan preserves those skips. A skipped item cannot become a write target during handoff.

See [architecture](architecture.md), [academic integrity](academic-integrity.md) and [design notes](design-notes.md).

RAT sheets and answer-bearing run files never serve as drafting or reconciliation sources. The `rat-root-quarantine` rail checks corpus paths, source ids and locators. Sheet-filled items use the supported `rat-sheet` skip. Report pages stay held by the fill plan. See [RAT answers](rat-answers.md).
