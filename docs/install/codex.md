<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Codex CLI installation and sandbox configuration. -->

# Codex CLI

Install the marketplace and plugin:

```bash
codex plugin marketplace add dorsaltegmentum/lt-suite
codex plugin add lt-suite@lt-suite
```

Start a new session and ask:

```text
Set up lt-suite.
```

The plugin installation route targets Codex CLI. Plugins are unavailable in the Codex IDE extension for this release's documented workflow.

## Browser connection

Use the generated MCP example in `examples/mcp/codex-config.toml`.

The corresponding server settings are:

```toml
[mcp_servers.lt-chrome]
command = "npx"
args = [
  "-y",
  "chrome-devtools-mcp@1.12.0",
  "--autoConnect",
  "--no-usage-statistics",
  "--no-performance-crux",
  "--no-category-input",
  "--no-category-network",
  "--no-category-memory",
  "--no-category-performance",
  "--no-category-emulation",
  "--redact-network-headers",
]
startup_timeout_sec = 30
default_tools_approval_mode = "prompt"
enabled_tools = [
  "list_pages",
  "select_page",
  "navigate_page",
  "evaluate_script",
  "take_screenshot",
  "wait_for",
]
```

Chrome 144 or newer supports the documented auto-connect route. Open `chrome://inspect/#remote-debugging`, enable remote debugging and approve the connection.

The suite evaluates rendered local files through `evaluate_script` with `sourcePath`. Follow [browser adapters](../browser-adapters.md) for navigation checks and script forms.

## Sandbox setup

Setup probes configuration storage, data storage and loopback access.

When those probes fail, use a workspace configuration:

```toml
[paths]
data_dir = ".lt-suite/data"
downloads_mode = "copy"

[browser]
transport = "download"
```

Save it as `lt-suite.toml` in the working directory. Relative paths resolve against that file's directory.

A restricted sandbox may prevent dependency installation. Run the bootstrap command printed by setup in a normal terminal, then resume.

See [Python](python.md) and [safety](../safety.md).

Sources: [plugins](https://developers.openai.com/codex/plugins/build), [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
