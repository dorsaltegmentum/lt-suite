<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Synthetic evaluations and the limits of routing measurements. -->

# Evaluation

lt-suite separates deterministic tests, agent evaluations and manual platform checks.

| Check | Evidence |
|---|---|
| Python and JavaScript tests | Executable behaviour under synthetic inputs |
| Per-skill evaluation | Output quality against stated expectations |
| Trigger evaluation | Whether a host selects the intended skill |
| Confusion evaluation | Whether neighbouring skills receive the right tasks |
| Manual platform check | Installation and execution on a named client version |

A passing routing score does not establish correct answers or safe browser behaviour.

## Synthetic material

Use the generated fixtures under `tests/fixtures/`.

Do not copy live questions, answers, screenshots, grades or identifiers into an evaluation.

The diagram example uses an invented mechanism. A reference file supplies grader-only ground truth and must stay outside drafting context.

Check generated fixtures without rewriting them:

```bash
python3 tools/gen_fixtures.py --check
```

## Per-skill evaluations

`evals/<skill>/evals.json` supplies tasks and expectations. `triggers.json` supplies routing queries with unique case identifiers for the offline scorers.

Run a task in a fresh context with only its permitted inputs. Keep the reference out until grading.

Record the agent, version, selected skill, input fixture hashes, output and unmet expectations. Compare the same cases before and after a change.

Every `evals.json` has `skill_name` and an `evals` array. Each task has `id`, `prompt` and `expected_output`; `expectations` lists observable checks when present. Optional `files` and top-level `environment` identify required inputs and environment settings. These task files need a human or model grader; the routing scorers do not execute them.

## Manual trigger checks

For each agent:

1. Install the release and confirm that all six skills appear.
2. Run each positive query in a fresh session.
3. Record actual skill invocation from the host's event trace.
4. Run negative queries and neighbouring-skill queries.
5. Record missing, extra and incorrect invocations separately.
6. Keep the client version with the result.

An assistant saying it would use a skill is not an invocation receipt.

## Claude Code tooling

The tools under `dev/claude-code/` score recorded routing observations. They do not collect native tool events or run a model.

See the [Claude Code tooling README](../dev/claude-code/README.md) for their explicit input formats.

Do not rely on a read-tool restriction as a filesystem sandbox. A shell can read the same files.

Use separate scratch directories for independent runs. Confirm evaluation-harness concurrency before enabling multiple workers.

## Release evidence

Publish only synthetic prompts and sanitised results.

A skipped test, missing binary or unavailable login remains an unverified check. Record it in [platforms](platforms.md), then complete it before making the associated support claim.
