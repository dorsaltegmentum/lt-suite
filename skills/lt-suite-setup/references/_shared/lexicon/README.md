<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# The lexicon

Durable knowledge the skills read: Lt's own vocabulary, the house style a drafted answer is held to, and the corrections that keep recurring across runs.

The shipped entries live in `shared/lexicon/` and are copied into each skill's `references/_shared/lexicon/`. Entries a user's run review proposes live in the data folder (`<data>/lexicon/`), so they survive a reinstall and stay out of the code. Markdown with YAML frontmatter, because the lexicon is read into an agent's context as text.

## The rule that keeps it useful

The lexicon is a reference, not a dumping ground. Every entry is loaded into context and competes for attention with the module, the course material and the task. So:

- One topic per file. An entry that needs a heading for a second subject is two entries.
- Every entry cites its evidence: a section of `docs/design-notes.md` (`docs/design-notes.md#<slug>`) or a test (`tests/<path>::<test>`). A remembered claim with no evidence reads exactly like a measured one, which is what this rule prevents.
- `review_by` in the frontmatter is a real date. The checker reports overdue entries; an overdue entry is re-verified or pruned, never renewed on sight.
- An entry that has never changed a run's behaviour is a candidate for deletion, however true it is.

## Frontmatter

```yaml
---
name: <file name without .md>
description: <one line>
review_by: <ISO date>
evidence: [docs/design-notes.md#<slug>, tests/<path>::<test>]
status: active | empty-by-construction | deprecated
---
```

## Checking it

```bash
PYTHONPATH=shared python3 -m ltcore.lexicon                     # inventory and context budget
PYTHONPATH=shared python3 -m ltcore.lexicon --check             # frontmatter, evidence, review dates
PYTHONPATH=shared python3 -m ltcore.lexicon --check --expired warn --anchors warn
```

`--check` exits 1 when an entry is malformed, carries no evidence, cites an anchor that does not exist (`--anchors fail`), is past its review date (`--expired fail`), or when the lexicon outgrows its word budget.

## What is here

| Entry | What it is for |
|---|---|
| `lt-client-vocabulary.md` | Lt's client-side view model: the names, and the trap attached to each |
| `answer-provenance-style.md` | the provenance discipline as it applies to a drafted answer |
| `recurring-corrections.md` | corrections aggregated across runs by run review; empty until evidence exists |
