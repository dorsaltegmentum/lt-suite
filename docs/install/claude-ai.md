<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Hosted Claude skill installation and workflow limits. -->

# Claude chat

Upload the required per-skill archive from the repository's [releases](https://github.com/dorsaltegmentum/lt-suite/releases).

Use the Skills uploader in Claude settings. Each archive contains one enclosing skill folder with its `SKILL.md`, scripts, references and licence files.

lt-suite keeps skill descriptions within 200 characters to accommodate the stricter documented upload limit. The documented individual file limit is 30 MB.

## Supported workflows

The 0.1.0 hosted-chat workflow supports answer drafting from supplied material, export from an uploaded capture and diagram preparation.

Capture and fill are unsupported on this surface in 0.1.0. The execution container cannot use the suite's local sink, local Downloads folder and lasting configuration together.

A separate browser connector does not establish compatibility with this release's complete capture and fill workflow.

## Configuration

Run the setup skill's configuration writer with `--stdout` to obtain TOML that you can save and upload again:

```bash
python3 "<skill-directory>/scripts/setup.py" write --answers answers.json --stdout
```

Replace `<skill-directory>` with the setup skill's actual directory. Keep the configuration locally between sessions. `--stdout` can include your configured local paths; inspect the TOML before uploading or sharing it. Check workspace sharing rules before uploading skills or configuration.

The hosted container may lack `rsvg-convert`, draw.io and `qpdf`. Record an unavailable render or check as a limitation. Preserve editable outputs for completion in a local installation.

See [academic integrity](../academic-integrity.md) before uploading study material.

Sources: [custom skills](https://support.claude.com/en/articles/12512198-creating-custom-skills), [platform support](https://claude.com/docs/plugins/platform-support).
