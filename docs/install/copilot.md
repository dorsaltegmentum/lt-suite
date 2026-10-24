<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Copilot CLI and VS Code installation. -->

# GitHub Copilot

Install the plugin in Copilot CLI:

```bash
copilot plugin install dorsaltegmentum/lt-suite
```

In VS Code, run **Chat: Install Plugin From Source** and enter:

```text
dorsaltegmentum/lt-suite
```

Ask the local agent to set up lt-suite.

## Browser connection

Use the generated MCP configuration for your client. VS Code examples live in `examples/mcp/vscode-mcp.json`; Copilot CLI examples live in `examples/mcp/copilot-mcp-config.json`.

Attach `lt-chrome` to an existing signed-in Chromium browser. Enable remote debugging and approve the connection.

Use the suite's reviewed page scripts and keep action approval enabled.

## Cloud limitation

The documented Copilot cloud-agent browser reaches localhost inside its cloud environment. It cannot attach to your local signed-in Lt tab.

Use a local installation for capture and fill. Keep cloud work limited to authorised supplied material and supported drafting or rendering tasks.

See [Python](python.md), [browser adapters](../browser-adapters.md) and [academic integrity](../academic-integrity.md).

Sources: [CLI plugins](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference), [VS Code plugins](https://code.visualstudio.com/docs/copilot/customization/agent-plugins), [cloud MCP](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/mcp-and-cloud-agent).

The plugin's Node launcher reads `browser.adapter` at startup when the host expands its plugin folder.
Choose Claude in Chrome for zero lt-chrome tools, or configure any Chromium browser through setup.
If doctor reports the 0.1.0 fallback, install the stable launcher and merge its host snippet.
Restart after changes; see [browser adapters](../browser-adapters.md) for folders and loopback limits.
