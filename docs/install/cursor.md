<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Cursor skill installation and MCP connection. -->

# Cursor

Install the skills:

```bash
npx --yes skills@1.7.4 add dorsaltegmentum/lt-suite -a cursor
```

Add the generated MCP configuration from `examples/mcp/cursor-mcp.json`, then ask Cursor to set up lt-suite.

The skill installer does not install MCP servers. Configure the browser connection separately.

## Browser connection

Use `lt-chrome` with your existing signed-in Chromium browser.

Cursor's built-in browser uses a separate browser context. The reference Lt workflow uses `chrome-devtools-mcp@1.12.0` and rendered local page scripts.

Enable Chrome remote debugging and approve the connection. Keep tool approval enabled.

See [browser adapters](../browser-adapters.md) and [safety](../safety.md).

## Discovery and local development

Cursor documents project skills under `.agents/skills/` and `.cursor/skills/`. Its user locations include `~/.agents/skills/` and `~/.cursor/skills/`.

The local-plugin and team-marketplace repository-import routes remain untested for this release. The [release checklist](../releasing.md) requires a manual repository-import test before publishing instructions for that route.

Sources: [skills](https://cursor.com/docs/context/skills), [plugins](https://cursor.com/docs/plugins).
