<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Review past runs

The review loop classifies exported-page diffs and proposes changes from corroborated evidence.
Every command uses the absolute `SKILL_DIR` defined in the setup skill.
Pass `--config <path>` before the subcommand when selecting a config explicitly.

1. Run `python3 "$SKILL_DIR/scripts/improve.py" classify --diff <page.md.diff>`.
   Export diffs compare the edited page on disk against the regenerated draft.
   The minus side contains the user's edit; the plus side contains the draft.
   The classifier proves tag-only provenance changes and punctuation-only style changes.
   Changed claims require the user's factual, scope, or diagram judgement.
   For an item-specific diff, pass `--item-id <id> --page <exported-page.md>`.

2. Record an agreed classification with the selected run:
   `python3 "$SKILL_DIR/scripts/improve.py" ingest --run <run.json> --skill lt-module-answer --class factual --diff <page.md.diff> --steered-run-wrong --item-id <id>`.
   Omit `--steered-run-wrong` unless the recorded instruction caused the failure.
   Use `--dry-run` to inspect the entry before writing it into the run record.

3. Run `python3 "$SKILL_DIR/scripts/improve.py" propose --dry-run` to inspect candidates.
   Proposals require 2 distinct runs by default, explicit ownership, and a benchmark cell.
   `improvement.benchmark_cells` overrides each skill's default eval pass-rate cell.
   Run `propose` without `--dry-run` to write `<data>/lexicon/proposals/*.md`.
   Each file carries `status: proposed` and an empty rule for the reviewer to compose.
   The script never loads proposal text as instructions and never edits a skill file.

4. The user reviews the evidence and applies any approved change outside this script.
   Re-run the named evals and record the before and after measurement.
   Record an applied change with `applied --skill <name> --class <class> --decision <approval-record> --measured <measurement-record>`.
   `applied --list` lists decisions; it applies no code or lexicon change.

Run `python3 "$SKILL_DIR/scripts/build_ledger.py" --dry-run` to audit selector observations.
Drop `--dry-run` to write `<data>/state/ledger.json`.
Use `--fill-report <fill-report.json>` to include the selected report's timestamps.
Repeated event arrays within one module count as 1 independent observation.
The ledger flags selectors addressed only by CSS, because they can match another panel.
The ledger never tests a selector against a live browser or changes a selector automatically.
