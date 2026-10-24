<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Claude Code only: offline scoring of recorded routing observations. -->

# Claude Code only

These development tools score recorded Claude Code routing observations.

They do not launch Claude Code, collect native events or prove that an observation accurately describes an invocation.

These are offline scoring tools. Collect native invocation evidence separately in the host client, then transcribe its observations into the documented format. They do not replace a live description sweep or select skills themselves.

## Input contract

Each input is a JSON object containing a nonempty `cases` list.

Case identifiers are unique nonempty strings. Expectations and observations must contain exactly the same identifiers.

Trigger expectations:

```json
{
  "cases": [
    {
      "id": "capture-request",
      "query": "Capture the named practice module.",
      "should_trigger": true
    },
    {
      "id": "unrelated-request",
      "query": "Translate a short sentence.",
      "should_trigger": false
    }
  ]
}
```

Trigger observations:

```json
{
  "cases": [
    {"id": "capture-request", "triggered": true},
    {"id": "unrelated-request", "triggered": false}
  ]
}
```

Record `triggered` from actual native invocation evidence.

Every shipped `evals/<skill>/triggers.json` uses this expectation contract, with a `cases` array and unique case identifiers. Pass one as `--expectations` and retain its exact case identifiers in the observations file. Task files named `evals.json` use a different format and cannot be scored by these commands.

Confusion expectations:

```json
{
  "cases": [
    {
      "id": "export-request",
      "query": "Export the supplied capture.",
      "expected_skill": "lt-module-export"
    },
    {
      "id": "unrelated-request",
      "query": "Translate a short sentence.",
      "expected_skill": null
    }
  ]
}
```

Confusion observations:

```json
{
  "cases": [
    {"id": "export-request", "actual_skill": "lt-module-export"},
    {"id": "unrelated-request", "actual_skill": null}
  ]
}
```

`null` means no suite skill. Confusion scoring accepts the six shipped skill names.

Multiple suite invocations require manual review before creating a single-label observation. Do not discard extra invocations to improve the score.

## Commands

```bash
python3 dev/claude-code/run_trigger_eval.py --expectations trigger-expectations.json --observations trigger-observations.json
python3 dev/claude-code/run_confusion_eval.py --expectations routing-expectations.json --observations routing-observations.json
```

Each command prints a JSON report to stdout.

| Exit | Meaning |
|---|---|
| 0 | Every case matches |
| 1 | At least one routing mismatch |
| 2 | Invalid input or unreadable file |

Missing observations, duplicate identifiers and empty datasets are errors.

The confusion matrix uses `none` for no suite skill. Rows represent expected labels; columns represent observed labels.

## Tests

```bash
python3 -m pytest dev/claude-code/test_runners.py -q
```

Tests create synthetic JSON inputs under `tmp_path` and invoke the actual command-line tools.

No runtime skill imports this directory. No test calls a live model or browser.
