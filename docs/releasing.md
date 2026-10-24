<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Release ordering, evidence and publication boundaries. -->

# Releasing

Release from a fully checked tree with synthetic fixtures and complete asset notices.

## Prepare sources

1. Review changes to safety rules, schemas and configuration.
2. Reconcile this documentation with the built implementation.
3. Verify documentation claims against code and identify unsupported platform workflows in ordinary text.
4. Confirm the final palette ranking and font inventory.
5. Complete third-party notices and REUSE annotations.
6. Fetch verbatim SPDX licence texts only for identifiers actually used.

A palette attribution record documents its source. It must not invent a permission grant for upstream material.

## Finalise and generate

Apply the release's marks-and-metadata pass to source text and committed binary metadata. Preserve wording and behaviour.

Then run:

```bash
python3 tools/sync_shared.py --strict
python3 tools/gen_manifests.py
python3 tools/gen_fixtures.py --check
python3 tools/gen_config_docs.py --check
python3 tools/sync_shared.py --check --strict
python3 tools/gen_manifests.py --check
```

Inspect changed generated files. A later source edit requires regeneration and affected checks again.

## Test

```bash
uv run --python 3.11 --group ci python -m pytest -q
uv run --python 3.13 --group ci python -m pytest -q
uv run --python 3.14 --group ci python -m pytest -q
python3 tools/node_tests.py tests --expect-files 12 --expect-min 300
python3 tools/check_isolation.py
uvx reuse@6.2.0 lint
PYTHONPATH=shared uv run --python 3.11 --group ci python -m ltcore.lexicon --check --expired fail --anchors fail
```

A skipped check is not a passing check. Resolve required missing binaries before release.

## Privacy

Keep private patterns, salts and shingle indexes outside the repository.

Set `LT_PRIVACY_DIR` to that private directory, then run:

```bash
python3 tools/privacy_gate.py --all --history --require-private --shingles "$LT_PRIVACY_DIR/shingles.bin"
LT_REQUIRE_PRIVATE=1 uv run --python 3.11 --group ci python -m pytest tests/privacy/test_inventory_hits.py -q
```

An unavailable private layer blocks release validation.

Obtain an independent privacy re-scan. Its inspected file count must equal the tracked-file count.

## Platform checks

Run the manifest and skill validators:

```bash
npx -y @anthropic-ai/claude-code@2.1.306 plugin validate . --strict
npx -y @google/gemini-cli@0.65.0 extensions validate .
agy plugin validate .
```

Validate every skill with `uvx --from skills-ref==0.1.1 agentskills validate`.

Validators requiring login belong in the human release checklist. Record their results before claiming support.

Complete the manual Cursor repository-import test before publishing that installation route.

Run manual trigger checks after description changes. Record versions in [platforms](platforms.md).

## Repository settings

Enable private vulnerability reporting before publication. Require passing CI on protected main and disallow force-pushes.
Require maintainer two-factor authentication and sign release tags. Verify archive attestations against the published repository.

## History and publication

Public release commits carry the approved repository identity and UTC offsets.

The initial release history carries no trailers, including `Signed-off-by` and co-author or session trailers.

Pull requests after the initial release require DCO `Signed-off-by` trailers on their contribution commits. See [contributing](../CONTRIBUTING.md). Apply any DCO check only to those pull-request commits.

Before each release commit, complete finalisation, regeneration, tests and the private privacy gate.

After committing, repeat the history scan and run:

```bash
python3 tools/check_release.py
python3 tools/build_dist.py --out dist --check
```

Archives must contain licence texts, notices and required asset attributions. Inspect the final archives for layout, size and privacy.

Push and publication require the repository owner's explicit approval. Prepare the checked commit and release artifacts before requesting that approval.
