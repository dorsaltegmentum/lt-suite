<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Evaluation inputs, quarantine and result interpretation. -->

# Evaluations

Each skill owns its synthetic tasks and routing queries.

| File | Purpose |
|---|---|
| `<skill>/evals.json` | Task inputs and expected behaviour |
| `<skill>/triggers.json` | Positive and negative routing queries |

`evals.json` contains `skill_name` and an `evals` array. Each task uses `id`, `prompt` and `expected_output`; optional `expectations` enumerate checks and `files` names its inputs. A top-level `environment` table supplies environment overrides when present.

Trigger datasets use a `cases` array with string `id`, `query` and boolean `should_trigger`. A legacy bare-list file needs conversion to this form before scoring. Record observations separately as `id` and boolean `triggered`.

Use fixture inputs from `tests/fixtures/`. Do not add captured course material or submitted work.

Drafting contexts exclude `reference-*.json`. Graders may read those references after drafting and must preserve their hashes.

Record the agent version, release version, fixture hashes, invoked skill and unmet expectations.

Native invocation evidence establishes whether a trigger fired. Model self-report alone does not establish invocation.

The [Claude Code tools](../dev/claude-code/README.md) score recorded routing observations against `triggers.json`. Task grading uses the task's expectations and remains separate. Confusion scoring needs a dataset with `expected_skill` and observations with `actual_skill`; each label is a suite skill name or `null`.

Run deterministic routing-grader tests with:

```bash
uv run --python 3.11 --group ci python -m pytest dev/claude-code/test_runners.py -q
```

See [evaluation](../docs/evaluation.md) for manual checks and interpretation.
