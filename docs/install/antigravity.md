<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Antigravity plugin installation and browser selection. -->

# Antigravity

Install the plugin:

```bash
agy plugin install https://github.com/dorsaltegmentum/lt-suite
```

Ask the agent to set up lt-suite, then run the [Python bootstrap](python.md).

The plugin provides `skills/`, `mcp_config.json` and generated safety rules.

## Browser connection

Use the configured `lt-chrome` server to attach to your existing signed-in Chromium browser.

The IDE's built-in browser uses a separate profile. The built-in `/browser` connection has no verified profile choice in the supplied research. Use the documented MCP connection for Lt.

Keep action review enabled. Run the suite's navigation checks and rendered scripts through the [browser adapter](../browser-adapters.md).

## Skill locations

The documented workspace location is `.agents/skills/`.

The documented global locations differ: the App and IDE use `~/.gemini/config/skills/`, while the CLI uses `~/.gemini/antigravity-cli/skills/`.

Plugin installation manages its own skill directory.

## Validation

From a checkout, run:

```bash
agy plugin validate .
```

The supplied research classifies the root `AGENTS.md` diagnostic as a warning.

Validation of this release on an installed Antigravity client remains pending. Record the command's result and client version before claiming a tested installation; see [platforms](../platforms.md).

Sources: [plugins](https://antigravity.google/docs/plugins?tab=cli), [skills](https://antigravity.google/docs/skills), [separate browser profile](https://antigravity.google/docs/ide/separate-chrome-profile/).

The plugin's Node launcher reads `browser.adapter` at startup when the host expands its plugin folder.
Choose Claude in Chrome for zero lt-chrome tools, or configure any Chromium browser through setup.
If doctor reports the 0.1.0 fallback, install the stable launcher and merge its host snippet.
Restart after changes; see [browser adapters](../browser-adapters.md) for folders and loopback limits.
