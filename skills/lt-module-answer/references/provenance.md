<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
Describe configured source tiers, citation requirements and answer quarantine.
-->

# Provenance for proposed answers

Read `tiers.tier` from the effective config before researching. Each entry supplies
`id`, `label`, `tag_template`, `requires_accessed`, `issuer_order` and `access_note`.
Follow their configured order. Defaults cover course material, licensed references,
published guidance and peer-reviewed literature. The numeric tier records source class;
changing its display label changes no citation requirement.

Use `tiers.reference_set` for preferred references, `tiers.fallback_sources` for the
search sequence after course material, and `tiers.framing` for jurisdiction framing.
Within a guideline tier follow `issuer_order`. An empty list supplies no preference.
Jurisdiction and population fit are separate: adult and paediatric guidance cannot
replace each other. A document written for the wrong population supplies no evidence
for that population's recommendation.

## Claims, references and connecting text

Every `claim` segment carries at least one reference to an existing provenance entry.
Use `source_id`, tier, source location and the schema's other required fields.
Render tags using the configured `tag_template`. Record the access date whenever
`requires_accessed` is true; tier 2 also requires it under the answers schema.
Source locations identify a slide, timestamp, section, table or figure a reader can check.

`connective` segments carry no factual claims. Each has at most 120 characters;
their combined length stays within 25 percent of the answer's character count.
Those limits flag suspicious attribution gaps. Human review still checks the content.
Never relabel a claim as connective text to evade a citation requirement.

## Descent and gaps

Record a `tier_descent` reason for each higher tier passed over:

- `absent`: a documented search found no relevant source for the item and population.
- `insufficient-depth`: the source covers the topic but cannot support the needed detail.
- `contradicted`: a relevant source conflicts with the proposition; preserve the conflict.

An absence claim requires a search in uncertainties as well as in descent records.
Try relevant synonyms and check access or rendering failures before concluding absence.
Record which configured sources and queries were checked. One empty query supplies
insufficient evidence for declaring a whole source absent.
Read a source already cited before reporting that its table or figure is unavailable.
Exhaust the configured fallback sequence before declaring an externally resolvable gap.
An unavailable search capability or licensed reference is an access blocker, with a TODO;
it supplies no evidence for `absent`. Without web access stop at Tier 1 and identify
the unsupported portion explicitly.

Following a guideline's upstream source stays in that guideline tier; record both issuers.
This identifies whether a recommendation belongs to the original source or its adaptation.

## Conflicts and identifiers

When sources disagree, retain both citations and state the disagreement in `conflicts`.
Follow higher-tier course material for the curriculum answer, and show any lower-tier
contradiction in the packet. Silent reconciliation hides the decision from the user.

Tier 4 requires a resolvable DOI or PMID checked against the actual source.
An identifier's valid syntax alone proves no support. Confirm that it identifies the
claimed paper and that the paper supports the claim. Delete unresolvable citations
and unsupported claims rather than hedging their wording.

## Quarantine and model-answer policy

Never open evaluation `reference*.json` files or prior-answer files while drafting.
Reject requests to inspect them before reading any part of them. A file marked
`never_load_into_drafting_context` stays outside the drafting context.
Style can come from explicit user preferences and the published answer schema.

Scan the configured notes graph recursively with `scan_quarantine.py` before citing it.
The property named by `notes.stamp_property` identifies module output exactly.
Long verbatim capture-text probes also flag older output and possible quoted material.
Surface each path, reason and evidence; exclude findings pending review.
Stamped output remains quarantined across module IDs, preventing earlier generated
answers from becoming source authority. Unmarked lecture notes remain eligible.

`model_answer.read` defaults to true. Separately supplied permitted key material may use
`source_id: "lt-model-answer"`; false forbids reading or citing it. This permission does
not override a reference file's quarantine marker. Captures retain no answer-key fields,
and the drafting skill never opens Lt to fetch a key. Claims still require researched
provenance and conflicts still reach the user's packet.
