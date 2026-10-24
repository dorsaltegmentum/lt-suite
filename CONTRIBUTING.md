<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Contributing

Contribute synthetic examples, reproducible fixes, and tests that protect the never-submit boundaries.

## Developer Certificate of Origin

Pull requests after the initial release certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
The DCO covers your right to contribute the work and its permanent public record.
Sign off each contribution commit with `git commit -s` using your contribution identity.
Git adds a `Signed-off-by` trailer; check that identity before publishing the commit.
Use GitHub's private commit address when you need to protect your address.
The initial release history contains no commit trailers. The DCO sign-off rule applies to later pull-request commits.
Retain upstream copyright notices and identify third-party licences in `THIRD_PARTY_NOTICES.md`.
Project code uses `AGPL-3.0-or-later`; third-party assets retain their documented licences.

## Privacy and safety

Use invented modules and synthetic fixtures in patches, tests, issues, and pull requests.
Include no course material, model answers, student work, personal data, credentials, or session exports.
Redact screenshots, logs, browser storage, and local paths before sharing any report.
Keep bulk harvesting prohibited and never bypass a Checkpoint, submission, or completion boundary.
Report guard bypasses privately through the [Security tab](https://github.com/dorsaltegmentum/lt-suite/security).
Run the privacy gate before pushing a branch, then inspect the proposed diff and commit metadata.
The public scan covers project patterns; maintainers also run their separate private-pattern checks.

## Tests and generated files

Install Python 3.11 or later, Node.js, and the development tools described in [setup](docs/setup.md).
Keep the development environment outside the checkout. Set `UV_PROJECT_ENVIRONMENT` to that environment before running `uv run`.
From the repository root, run the checks relevant to your change:

```sh
uv run --frozen --python 3.11 --group ci python packaging/ci.py area-tests
uv run --frozen --python 3.13 --group ci python packaging/ci.py area-tests
uv run --frozen --python 3.14 --group ci python packaging/ci.py area-tests
python3 tools/node_tests.py tests
python3 tools/check_skills.py
python3 tools/sync_shared.py --check --strict
python3 tools/privacy_gate.py --all
uvx reuse@6.2.0 lint
```

The `uv` commands can download dependencies; review the dependency groups in `pyproject.toml` first.
Run a focused test directory while developing, then the full checks before requesting review.
The area runner starts one Python process per test folder to isolate skill import paths.
Document commands, results, skipped checks, and required external binaries in the pull request.
Edit canonical files under `shared/`; regenerate skill copies with `python3 tools/sync_shared.py --strict`.
Run `python3 tools/gen_manifests.py --check` when packaging files or skill versions change.
Run `python3 tools/gen_manifests.py` first when those inputs require regenerated manifests.
Use `python3 tools/check_isolation.py` to verify standalone skill packages.
Dependency updates must regenerate `uv.lock` and the hashed skill requirements through `tools/sync_shared.py`.
The CI installer exports the frozen CI dependency group and verifies every downloaded package hash.
Keep the browser package versions in `packaging/package.json`, its lockfile, and `packaging/plugin.toml` consistent.
Record reviewed runtime files in `packaging/runtime-files.json` when adding distribution assets.
The builder rejects unlisted files and private runtime filenames before writing archives.
CI never expands the runtime allowlist.

## Skill authoring

Place each skill in `skills/<name>/SKILL.md` as an immediate child of `skills/`.
Keep each skill self-contained, with its scripts, references, licence, and notices.
Use these frontmatter rules:

- Match `name` to its folder; use lowercase letters, numbers, and single internal hyphens.
- Limit `name` to 64 characters, with no leading, trailing, or consecutive hyphens.
- Limit `description` to 200 characters; put detailed triggers in the first body section.
- Set `license: AGPL-3.0-or-later` and match `metadata.version` to the plugin version.
- Quote `compatibility` and limit its value to 500 characters when present.
- Omit `allowed-tools` and `metadata.phase`.

Keep the body within 500 lines; move supporting detail into `references/`.
Retain the inline safety block and use tool-neutral browser-adapter instructions.
Resolve scripts from the skill directory so each skill runs outside the repository.
Declare shared data in module `NEEDS`; regenerate copies instead of editing generated files.
Keep each skill below 10 MB and review file counts above 100.
Add meaningful regression coverage for changed save waits, guards, and browser behavior.
