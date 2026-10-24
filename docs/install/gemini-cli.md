<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Gemini CLI extension installation and browser configuration. -->

# Gemini CLI

Install the extension:

```bash
gemini extensions install https://github.com/dorsaltegmentum/lt-suite
```

Start a session and ask:

```text
Set up lt-suite.
```

The documented access route requires a paid API key or a supported enterprise licence. See [platforms](../platforms.md) for the research date and tested-release status.

## Browser connection

Use the extension's `lt-chrome` MCP server with your existing signed-in Chromium browser.

Enable remote debugging through `chrome://inspect/#remote-debugging` and approve the connection.

Keep MCP confirmation enabled with `trust: false`. Retain the generated input-tool exclusions.

The Gemini CLI browser agent is off by default. The suite's reference route uses its generated MCP configuration and reviewed page scripts.

See [browser adapters](../browser-adapters.md) for the five browser operations.

## Dependencies

Run the [Python bootstrap](python.md), then setup's `doctor` command.

Extension installation copies the source. Use Gemini CLI's extension update mechanism when changing versions.

Sources: [extensions](https://geminicli.com/docs/extensions/reference/), [MCP servers](https://geminicli.com/docs/tools/mcp-server/), [access transition](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli).

The plugin's Node launcher reads `browser.adapter` at startup when the host expands its plugin folder.
Choose Claude in Chrome for zero lt-chrome tools, or configure any Chromium browser through setup.
If doctor reports the 0.1.0 fallback, install the stable launcher and merge its host snippet.
Restart after changes; see [browser adapters](../browser-adapters.md) for folders and loopback limits.
