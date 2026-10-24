<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Research versions and pending release verification. -->

# Platforms

This matrix records researched installation routes and pending release installation tests.

Research was checked on 2026-10-24. Mark a platform verified only after testing the built release and recording its actual client version.

| Surface | Designed installation | Designed scope | Release verified on |
|---|---|---|---|
| Claude Code | Marketplace plugin | Local capture, draft, fill, export and diagram | Pending |
| Codex CLI | Marketplace plugin | Local workflow through MCP | Pending |
| Gemini CLI | Extension | Local workflow through MCP | Pending |
| Antigravity | Plugin | Local workflow through MCP | Pending |
| Cursor | Skill installer and separate MCP setup | Local workflow | Pending |
| Copilot CLI and VS Code | Plugin | Local workflow through MCP | Pending |
| Claude hosted chat | Per-skill archive | Drafting, uploaded-capture export and supported diagrams | Pending |
| ChatGPT | Plugin archive | Drafting and supported diagrams | Pending |
| Gemini app and Enterprise | Per-skill uploads | Supplied-material drafting | Pending |
| Perplexity | UI or API archive layout | Supplied-material drafting | Pending |

Replace a pending cell only after recording installation and workflow evidence for a specific release archive or tag.

## Research versions

| Component | Supplied research version or requirement |
|---|---|
| Python runtime | 3.11+ |
| Claude Code research validator | 2.1.293 |
| Claude Code release CI validator | 2.1.306 |
| Claude Code one-command shell installation | 2.1.292+ |
| Claude Code one-command in-session installation | 2.1.275+ |
| Claude in Chrome extension | 1.0.36+ |
| Codex local research tests | 0.162.1 |
| Codex registry version reported by research | 0.164.0 |
| Gemini CLI | 0.65.0 |
| Antigravity CLI | 1.4.6 |
| Reference browser MCP | chrome-devtools-mcp 1.12.0 |
| Alternative browser MCP | Playwright MCP 0.0.83 |
| Chrome auto-connect | 144+ |
| Chrome URL-pattern filtering | 149+ |
| Skill installer | skills 1.7.4, Node 22.20+ |
| REUSE | 6.2.0 |
| Agent Skills validator | skills-ref 0.1.1 |

These are research observations and release pins where specified. They are not a claim that every listed client combination passed end-to-end tests.

## Access and host limits

The supplied Gemini research reports that individual free and subscription access ended on 2026-06-18. Paid API keys and supported enterprise licences retained access.

ChatGPT plan eligibility and upload acceptance require a workspace check.

Claude chat capture and fill remain outside this release's support scope. Codex IDE plugins and cloud browser access remain outside the documented local workflow.

Hosted execution environments can lack rendering binaries. Record each skipped render separately from a successful draft.

## Verification record

For each completed check, record:

- Release tag or archive hash.
- Agent and version.
- Operating system.
- Adapter and browser version.
- Installation result.
- Setup and doctor result.
- Synthetic workflow result.
- Date and remaining limitations.

Do not place live Lt content or identifying account details in the record.
