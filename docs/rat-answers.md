<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# RAT answer sheets

lt-suite enters your individual or team answers from folders you allow.
It never drafts an individual RAT. Check your course's assessment rules first.

Set `rat.answers_dir` for one folder and `rat.answers_dirs` for additional folders.
Only immediate `.md`, `.markdown` and `.txt` files qualify. Hidden names, links,
reparse points and subfolders are skipped. Each file must be UTF-8, at most 256 KiB;
each root may contain at most 500 candidate files. List subfolders as separate roots.
Roots may sit inside the curriculum tree, where course lookup prunes them. Roots
must be directories and cannot contain the curriculum root, touch the notes graph,
or touch the data folder. Missing roots warn. Real-path duplicates count once.

`rat.irat` defaults to `skip`; `rat.trat` defaults to `ask`. Both accept `skip`,
`ask` and `from_dir`. Reading requires an existing root and `from_dir`, or a
current sheet choice under `ask`. The root command lists metadata without reads.

## Compact sheets

Compact sheets bind the Lt module and page explicitly:

```markdown
---
lt-rat: 1
module: c1-r109
page: 3
kind: trat
questions: 5
week: "1.2"
---
1. B
2. D | Desert oasis
3. A, C
4. ?
5. -
```

Numbers must run from 1 without gaps or repeats. `module` and `page` are required.
`kind`, `questions` and `week` are optional. `?` and `-` leave questions unanswered.
Multiple letters require a multiselect item. Text after `|` cross-checks the chosen
option. Comments after `#` and unknown front-matter keys never reach output.

## Marked sheets

Marked sheets use consecutive option letters and marks:

```markdown
Choose the fictional component.
A. Amber spindle
B. Cobalt rotor ✓
C. Silver helix
D. Bronze vessel
```

`✓`, `✔`, `✅` and a leading `[x]` task box choose an option. Negative crosses
choose nothing. `A.` and `A)` work, including list bullets and bold prefixes.
Blank lines may separate options. One-option runs are ignored. Duplicate or missing
letters make a question malformed. Multiple marks on a single-select question,
unanswered questions and failed text checks become TODOs. Front matter is optional.
A declared sheet kind never classifies the destination page.

## Matching and classification

The classifier lists all pages with at least 2 multiple-choice items, capture flags,
or content markers. Content markers classify iRAT and tRAT; titles never classify.
A full sheet match supplies RAT evidence. Skip reasons can identify an individual
or team RAT. Drafted answers supply no classification.

Text comparison uses NFKC, stripped tags and marks, folded case and whitespace.
Capture prefixes disappear only when their letter equals their option position.
Marked questions require a unique option bijection in position order and mean
similarity of at least 0.80. Each chosen option separately needs 0.80. Distinct
questions beat every other compatible item by 0.10. Full matches align every
question. Partial matches align at least `max(2, n // 2)` questions with some item;
filling always uses positional matches only. Compact sheets bind their page directly.

Partial, indistinct, ambiguous, kind-different, week-different or draft-conflicting
matches ask for confirmation. The question lists the aligned question numbers.
Identical sheets pick the first by root order and file name. Differing full sheets
prefer one declaring the page kind when exactly one does. Unknown candidates hold.

## Commands and records

Run the answer or fill skill's `scripts/page_kinds.py`:

```sh
python3 page_kinds.py roots --config config.toml --json
python3 page_kinds.py classify --module module.json --answers answers.json --json
python3 page_kinds.py mark --module module.json --answers answers.json --mark 5=not-rat --answer "Practice quiz"
python3 page_kinds.py choose --module module.json --answers answers.json --page 3 --fill sheet --sheet rat.answers_dir:1.2-team.md --answer "Use this sheet"
```

All commands accept `--config`, `--data-dir` and `--json`. Choices are `sheet`,
`draft` (team only) and `leave`. A sheet choice without a name grants reading
under `ask`; a named choice confirms a current full or partial match. Replies are
stored verbatim. Classification exits 0 when decided, 1 with questions, 2 without
candidates, and 3 for invalid input. Roots exits 0 or 3. `--include-rat` is deprecated.

The shared store is `<data>/runs/<module_id>/page-kinds.json`. It stores marks,
choices, root and policy hashes, page pins and safe match counts. It contains no
selections, stems, option text or absolute paths. Plain sheet names use 1 to 80
ASCII letters, digits, spaces or `._()-`; other names display as `sheet-<hash>`.
Safe match `todo_questions` records question numbers and fixed status codes only.
Changes to policy, answers, roots, listings or page inputs require reclassification.
Marks survive answered-state changes. Choices also bind the policy and roots.
The store is validated and replaced atomically.

An informed `not-rat` mark can permit ordinary filling after conflicting sources
were shown. Marker or full-match evidence still forbids Check Answer. The shared
read-only `page_status` API exposes this distinction without reading sheets.

## Quarantine and hand-back

Only `ltcore.rat.read_sheet` opens sheets. Drafting agents never open, quote,
summarise or cite roots, payloads, rendered fill scripts, logs or fill reports.
The `rat-root-quarantine` rail rejects those provenance sources. An individual
or unknown-kind RAT draft fails `rat-irat-drafted`, without a waiver.

The payload holds selections and option text locally. Rendered inline browser
calls expose them to the calling agent. lt-chrome's `sourcePath` avoids reading
script text into that context. Receipt hashes also permit guessing small selections.
Finish ordinary filling, checks and reconciliation first. Fill RAT pages last,
one page per call, with occupancy and single-attempt approvals preserved.
Do no further drafting in that session. Rebuild the final report after verification;
reconcile rebinding belongs to the Check Answer feature.

Export opens no sheet. It shows the sheet name, filled count and remaining count.
Sheet questions and answers never enter notes, PDFs or flashcard sources. The
hand-back names each decision and sheet and tells you to start a new session for
further drafting. Only you submit in Lt.


Sheet receipts pin the executed payload by path and SHA256. Fill and fresh-load
verification echo that payload hash. The payload also pins the classification hash.
Keep the payload and `page-kinds.json` unchanged until `make_fill_report.py` succeeds.
The report refuses missing bindings, changed payloads, changed classifications and
receipt hashes that differ from the executed jobs. Reclassification requires a new
fill and fresh-load verification before reporting those sheet entries.
