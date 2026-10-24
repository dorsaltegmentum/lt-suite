<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Claude Code installation and browser connection. -->

# Claude Code

Install the marketplace and plugin:

```bash
claude plugin marketplace add dorsaltegmentum/lt-suite
claude plugin install lt-suite@lt-suite
```

Start a session and ask:

```text
Set up lt-suite.
```

Setup configures Python, storage, source tiers, fonts, palettes and the browser connection.

## Browser connection

Claude in Chrome requires its browser extension, a direct supported Claude plan and interactive account sign-in. Start Claude Code with `claude --chrome`, or enable the connection with `/chrome`.

Claude in Chrome is unavailable in WSL and under API-key authentication. Use the generated `lt-chrome` MCP connection where the host can attach to your signed-in Chromium browser.

The reference MCP backend is `chrome-devtools-mcp@1.12.0`. Enable remote debugging through `chrome://inspect/#remote-debugging` and approve the browser's connection request.

Use the suite's rendered page scripts. Keep browser action approval enabled.

## Versions

The two-command installation above avoids relying on the newer one-command installation form.

The one-command shell form requires Claude Code 2.1.292 or newer. Its in-session counterpart requires 2.1.275 or newer. See the [platform matrix](../platforms.md) for research versions and verification status.

## Files and dependencies

Python dependencies require the [bootstrap step](python.md). Run scripts through absolute paths under the installed skill folder.

The binding safety rules appear inside every `SKILL.md`. A plugin's root `AGENTS.md` does not supply those rules to every installation.

Read [safety](../safety.md) before capture or fill.

Sources: [plugin installation](https://code.claude.com/docs/en/plugins/install.md), [Chrome connection](https://code.claude.com/docs/en/chrome).

The plugin's Node launcher reads `browser.adapter` at startup when the host expands its plugin folder.
Choose Claude in Chrome for zero lt-chrome tools, or configure any Chromium browser through setup.
If doctor reports the 0.1.0 fallback, install the stable launcher and merge its host snippet.
Restart after changes; see [browser adapters](../browser-adapters.md) for folders and loopback limits.
