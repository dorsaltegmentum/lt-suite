<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Portable skill installation and hosted archive layouts. -->

# Other agents

Install the portable skills with:

```bash
npx --yes skills@1.7.4 add dorsaltegmentum/lt-suite
```

The installer selects an agent-specific skill location. It does not configure a browser MCP server or install Python dependencies.

The supplied research gives Node 22.20 or newer as the requirement for the evaluated skill installer.

## Standalone folders

Each skill folder includes its runtime dependency closure, references and licence files.

Use real copies when an installer or archive format does not preserve links. Run scripts through absolute paths under the installed skill folder.

Kiro documents `.kiro/skills/`. Cline documents `.cline/skills/`, `.clinerules/skills/` and `.claude/skills/`.

Other clients commonly use `.agents/skills/`. Confirm discovery with your client's skill listing before beginning a task.

## Hosted uploads

| Surface | Generated archive layout | Intended workflow |
|---|---|---|
| Gemini app | Per-skill upload | Drafting from supplied material |
| Gemini Enterprise | One `SKILL.md` at the root or within one wrapper folder | Drafting; execution depends on the host |
| Perplexity UI | `SKILL.md` at the archive root | Drafting; rendering depends on the host |
| Perplexity API | One wrapper folder containing `SKILL.md` | Drafting; rendering depends on the host |

Gemini app and Enterprise archives retain only `name`, `description`, `license` and `metadata` in frontmatter.

The builder names each archive `<skill>-<target>-<version>.zip` under its target directory. Targets here are `gemini-app`, `gemini-enterprise`, `perplexity-ui` and `perplexity-api`. Layout checks cannot establish hosted upload acceptance or execution capability; those platform checks remain pending.

## Browser requirements

Capture and fill require the five operations documented in [browser adapters](../browser-adapters.md).

A client with no suitable browser tool can use the manual-paste workflow. The human runs the reviewed scripts and returns their small receipts.

See [Python](python.md) for dependency setup.

Sources: [skill installer](https://github.com/vercel-labs/skills), [Kiro skills](https://kiro.dev/docs/skills/), [Cline skills](https://docs.cline.bot/customization/skills), [Gemini Enterprise](https://docs.cloud.google.com/gemini/enterprise/docs/skills), [Perplexity API](https://docs.perplexity.ai/docs/agent-api/skills).
