<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Synthetic fixtures

These 7 modules describe invented reactions, glands, stores and question types.
Vorlase, tessin, quillic acid and the quillen gland have no clinical meaning.
Every question and submitted response was written for these tests.

All captures use `https://lt.example.invalid`, instance `inst0001`, course `1`,
lesson `1`, unit label `X101` and run numbers `101` through `107`.
Every timestamp is `2000-01-01T00:00:00Z`; access dates use `2000-01-01`.
Panel keys stay below `100`.

| Module file | Coverage |
| --- | --- |
| `modules/module-c1-r101.json` | Locked, completed run; submitted single-attempt items; completion route. |
| `modules/module-c1-r102.json` | Writable questions; unmapped rating scale; unapproved single-attempt item; model-answer conflict. |
| `modules/module-c1-r103.json` | Upload and annotation geometry; image labels; categories; single-attempt drag; no completion route. |
| `modules/module-c1-r104.json` | Committed checkpoint outside the flow; locked pages 3 through 8; null child locks; submitted single attempt; 15 team selections. |
| `modules/module-c1-r105.json` | 3 mirror pairs and 1 near-duplicate with different responses. |
| `modules/module-c1-r106.json` | Tables with 17 and 6 rows; a live checkpoint on page 3. |
| `modules/module-demo.json` | Run `c1-r107`: 2 pages, subpage child, null upload prompt, notebook and readiness-test candidate. |

`modules/index.json` maps module identifiers to filenames.
`modules/expected-mirrors.json` lists the intended duplicate relationships.
`modules/expected-boundaries.json` is a separate, handwritten oracle containing
7 base cases and 14 mutations. Its expectations come from the boundary rules.
The generator never rewrites that oracle.

`viewmodels/<module_id>.json` holds the corresponding browser-model inputs.
The capture harness wraps these inputs as plain values and as observables,
collections and models. Both forms must produce the same captured structure.

`answers/` holds matching drafts with module hashes and question digests.
`references/` holds invented submitted responses, with
`never_load_into_drafting_context: true`. Evaluation may read these references
after drafting. Drafting must keep them quarantined. The untouched demo has an
empty reference, which retains the same quarantine flag.

`fill-reports/` covers 6 simulated fills. `runs/` holds their notes, hash receipts,
save waits and run records. The mirror-only run `c1-r105` has no simulated fill.
Five of the six reports carry `save_waits`, copied to `runs/<id>/save-waits.json`.
Together they use every save signal: `fallback` (first page of an origin, not calibrated),
`unconfirmed`, `error` followed by a retried `model` row, `net`, and a two-page call.
Items filled on a page with a `fallback`, `unconfirmed` or `error` wait carry the matching
sentence in `stopped_reason`. The locked run `c1-r101` filled nothing and records no wait.
Each wait names a browser receipt (`fill-result-<n>.json`) that is not shipped; its digest
is taken over the invented name.
`runs/c1-r104/approvals.json` contains 3 invented approval records, including the
2 waivers its report cites. These records authorize no real browser operation.
The single-attempt record matches the schema package's read-only fixture in
`approvals/approvals-c1-r104.json`.

`curriculum/` contains 4 tiny week folders across 2 blocks, including an ambiguous
week number, slides in Markdown, a caption file and `ILO.md`.
`media/` contains seeded PNG placeholders below 5 KB, with no text or metadata
chunks. Author images and student uploads can share a key while retaining
distinct filenames. `pages/page.md` is a 14-line export input; its asset name
resolves to the corresponding placeholder in `media/` during export tests.

Regenerate and check from the repository root:

```sh
python3 tools/gen_fixtures.py --seed 20001
python3 tools/gen_fixtures.py --check
uv run --python 3.11 --group ci python -m pytest tests/fixtures_check -q
```

`--out DIR` writes an isolated generated tree. `--check` regenerates in a temporary
directory and compares bytes against this fixture tree. Missing, changed and
unexpected files fail the check. Regeneration preserves unexpected files and
reports them for review. `--no-validate` explicitly skips schema and rail checks;
acceptance uses validation. Missing validation code fails the default check.
The seed changes image pixels, fresh-load hashes and simulated team selections;
module structure and curriculum text stay fixed.

The schema package owns `examples/` and `approvals/`. The generator reads those
inputs without changing them. JSON data and media receive their licence through
the repository's REUSE annotations; Python sources carry SPDX headers.

Boundary mutations use these operations in their listed order:

| Operation | Meaning |
| --- | --- |
| `item` | Update fields on the named item. |
| `lesson` | Update lesson flags. |
| `checkpoints` | Replace the checkpoint inventory. |
| `add_item` | Append an item to the named page. |
| `remove_page` | Remove a page and shift later page indices and checkpoint records. |
| `insert_checkpoint_page` | Insert a live checkpoint and shift later page indices and checkpoint records. |

Mutations preserve item identifiers so assertions can follow the original items.
They target boundary logic and need not constitute complete recaptures.
