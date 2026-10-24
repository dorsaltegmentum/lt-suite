<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Install tests

These scripts check lt-suite installation and discovery of its 6 skills.
They verify archive entry points separately from host upload acceptance. Each test copies Git-visible release inputs into a temp folder first.
Ignored render outputs and virtual environments stay outside the snapshot. Agent CLIs run with a temp `HOME` and temp config folders. Credential variables are removed. Your own config, plugin cache and logins stay untouched.

No test sends a prompt to a model. The exception is `--live` on the Claude Code test, which sends one short prompt.

Python 3.11+ and the standard library only.

```bash
python3 tools/install_tests/run_all.py                    # every platform
python3 tools/install_tests/run_all.py --only zips,codex  # a subset
python3 tools/install_tests/run_all.py --present-only     # partial tree: SKIP skills not written yet
```

| Script | Platform | How skills are seen without a model call |
|---|---|---|
| `install_claude_code.py` | Claude Code | `claude plugin validate --strict`; `claude --plugin-dir <copy> plugin details lt-suite`; the `system/init` message of `claude -p --output-format stream-json` (no login in the temp config, process stopped after that message); a plugin `.zip` through `--plugin-dir`; `claude plugin marketplace add <copy>` then `claude plugin install lt-suite@lt-suite` |
| `install_codex.py` | Codex CLI | `codex debug prompt-input` lists the skills Codex would offer. Three routes: `codex plugin marketplace add <copy>` with `codex plugin add lt-suite@lt-suite`, `<project>/.agents/skills/`, `$CODEX_HOME/skills/` |
| `install_npx_skills.py` | `npx skills add` (skills 1.7.4, Node 22.20+) | `--list` output; installed folders compared file by file with the source; Codex and Claude Code then read the installed project. Needs the npm registry; without network it reports SKIP |
| `install_zips.py` | claude.ai, Gemini app, Perplexity uploads | Zips from `tools/build_dist.py`; `--builder dist` refuses a failed build, each extracted alone; layout, licence files, paths named in SKILL.md, `MANIFEST.json` hashes, every entry script's `--help` from the skill folder and from another folder under an audit hook that fails any read inside the source repo |
| `install_gemini_agy.py` | Gemini CLI, Antigravity | Static checks of the files each reads. CLI steps run when `gemini` or `agy` is on PATH, else they print as steps marked `MANUAL` |

Result lines start with `PASS`, `FAIL`, `WARN`, `SKIP` or `MANUAL`. Exit 0 means no `FAIL`. Exit 1 means at least one `FAIL`. Exit 2 means a usage error or a crashed test.

Useful options: `--claude "npx -y @anthropic-ai/claude-code@2.1.306"` and `--gemini "npx -y @google/gemini-cli@0.65.0"` run a pinned CLI through npx. `--codex PATH` names the Codex binary. `--python PATH` picks an interpreter with the dependencies for the zip `--help` runs. `--strict-deps` makes a missing third-party module a `FAIL`. `--keep` keeps the temp folders for inspection.

## Manual steps (release checklist)

Run these in a shell whose `HOME` points at an empty temp folder:

```bash
gemini extensions validate <repo>
gemini extensions link <repo>
gemini extensions list            # lt-suite 0.2.0
gemini skills list --all          # 6 lt-suite skills
agy plugin validate <repo>
agy plugin install <repo>
agy plugin list --json            # lt-suite enabled
```

In an `agy` session, the `/plugin` Installed tab should show 6 skills and the always-on safety rule.

## Pytest wrappers

```sh
python3 -m pytest tests/install -q
LT_SUITE_INSTALL_NETWORK=1 python3 -m pytest tests/install -q
```

Missing Claude Code and Codex CLIs produce pytest skips.
The npm test requires the opt-in variable and skips registry connection failures.
Gemini and Antigravity missing binaries produce 9 manual steps after static checks.
Archive checks require the CI Python dependencies; missing packages fail with `--strict-deps`.
Result JSON records every subprocess command and exit code.
Session probes stop after `system/init`; their termination exit is recorded separately.

## Proof assets

```sh
python3 tools/install_tests/make_proof_assets.py build --repo .
python3 tools/install_tests/make_proof_assets.py verify --repo .
```

The builder uses the synthetic schema fixtures and invented toy diagram.
The fill-review image uses a 2-item excerpt with recomputed source hashes and question digest.
It shows one disputed draft and one manual placement task.
All PDF samples pass the exporter's QC before publication.
The asset manifest records commands, hashes, sizes, alt text and contrast measurements.
This check does not contact Lt or type into a browser.
