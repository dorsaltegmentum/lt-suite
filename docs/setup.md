<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Setup

Ask your agent to set up lt-suite with the `lt-suite-setup` skill.
Setup asks one question at a time and waits before using each answer.
The commands below use `SKILL_DIR`, the absolute path to the installed setup skill.

1. Run `python3 "$SKILL_DIR/scripts/setup.py" bootstrap` to prepare the Python environment.
   Bootstrap starts on Python 3.8 and finds Python 3.11 or newer using uv or PATH.
   It creates `<data>/venv` outside repositories and installs the installed skills' requirements.
   Repository data folders use the external platform data directory for the environment.
   Bootstrap refuses a platform environment path inside a git work tree, including symlinks.
   Bootstrap and runtime lookup select the same environment location.
   An offline shell prints 1 command to run in a normal terminal.
   `bootstrap --dry-run --json` prints its plan without installing packages.

2. Run `python3 "$SKILL_DIR/scripts/setup.py" locate` to inspect paths and probes.
   Config lookup checks `--config`, `LT_SUITE_CONFIG`, `./lt-suite.toml`, then the platform file.
   Data lookup checks `LT_SUITE_DATA`, `paths.data_dir`, then the platform default.
   Relative config paths resolve against the config file's directory.
   Failed write or loopback probes propose a workspace config with `.lt-suite/data`.
   Workspace transport uses browser downloads and copies files into the workspace.

3. Choose the Lt origin, institution labels, data folder, optional study folders, and handoffs.
   An origin contains only the scheme and host, such as `https://example.lt.app`.
   Source tiers start with 4 neutral labels; setup can replace them with your labels.
   Model answer reading starts enabled and keeps Lt's answer in a separate reference file.
   Set `model_answer.read = false` to disable that reading.

4. Preview palettes with `python3 "$SKILL_DIR/scripts/preview.py" palettes --top 6`.
   Pillow draws 7 role swatches, 4 edge styles, and a contrast table for each derived theme.
   The command needs neither rsvg-convert nor the diagram engine.
   Choose 1 to 3 export palettes and 1 light print palette.
   Everforest Dark Medium, Catppuccin Frappé, and Catppuccin Latte remain catalog options.
   Defaults come from measured derived-theme ranking; your choices override them.

5. Preview fonts with `python3 "$SKILL_DIR/scripts/preview.py" fonts --top 6`.
   The skill bundles the Noto Sans family and its companion, Noto Sans Mono.
   The catalog includes Atkinson Hyperlegible Next and Mono, Adwaita Sans, and Adwaita Mono.
   Each row lists its licence and bundled, fetched, installed, downloadable, or unavailable state.
   Available font files receive sample PNGs; missing fonts receive installation notes.
   Sample boxes show glyphs absent from that font; previews apply no replacement font.
   `fetch-font <id>` prints the pinned download plan and waits for separate approval.
   After approval, `fetch-font <id> --yes` checks SHA-256 before publishing downloaded files.

6. Write nested config answers from JSON or TOML:
   `python3 "$SKILL_DIR/scripts/setup.py" write --answers <answers.json> --config <target.toml>`.
   Existing files require `--force` or an agreed edit with `--merge`; replacements keep `.bak`.
   `--workspace` selects `./lt-suite.toml` with the workspace layout.
   `--stdout` prints TOML without writing config or data folders.
   Cloud chat users keep this TOML and upload it each session.
   Cloud chat supports drafting, export from uploaded captures, and diagrams.
   Browser capture and fill need the local installation's browser and filesystem.
   Run `python3 "$SKILL_DIR/scripts/setup.py" bootstrap --config <target.toml>` after writing the final config.
   This prepares dependencies for a custom data folder before doctor runs.

7. Run `python3 "$SKILL_DIR/scripts/setup.py" doctor --config <target.toml>`.
   PASS means the check succeeded; WARN identifies a feature dependency or unset optional setting.
   FAIL requires a fix before the affected feature can run.
   Exit 0 means no FAIL; exit 1 means FAIL; exit 2 means doctor crashed.
   `--require <part>` promotes that part's WARN lines to FAIL, and accepts repeated flags.
   Parts are python, packages, binaries, fonts, palettes, config, data, and browser.
   Diagram and export use rsvg-convert, pdfinfo, pdftoppm, pdftotext, and xmllint.
   qpdf, exiftool, potrace, and drawio provide optional features.
   `--require binaries` checks all binary warnings, including optional features.

8. Check the browser using the skill's guarded ping procedure.
   The `auto` adapter tries Claude in Chrome, lt-chrome, Playwright MCP, then manual paste.
   Lt-chrome uses `browser.adapter = "chrome-devtools-mcp"` and evaluates the rendered file through `sourcePath`.
   Doctor pre-fetches `chrome-devtools-mcp@1.12.0` for that adapter and Antigravity; `--prefetch-mcp` requests it explicitly.
   Codex MCP configuration should set `startup_timeout_sec = 30`.
   `ping render` returns a file receipt; execute that file through your evaluate-file tool.
   If the server refuses its directory, render once in the permitted directory reported by the server.
   `ping verify --reply-file <reply.json> --record-dir <directory>` records the successful directory as `browser.js_tmp_dir`.
   A failed reply leaves the directory setting unchanged.
   Ping reads client presence and origin; it verifies no answer saves or course permissions.

The [run review guide](../skills/lt-suite-setup/references/run-review.md) describes local diff classification and selector ledgers.
Proposals stay under `<data>/lexicon/proposals/`; a human reviews and applies changes separately.
The [configuration reference](configuration.md) lists every config key.

Setup asks for the browser path, browser name and auto-connect or a loopback DevTools address.
Claude in Chrome and manual paste skip the DevTools questions and serve zero lt-chrome tools.
Restart after writing the config. Run `setup.py lt-chrome plan --json` to inspect the redacted decision.
Doctor pre-fetches for explicit Chromium and Antigravity choices; quiet adapters skip it.
For Codex and Cursor, approve `lt-chrome install`, then merge `lt-chrome snippet --host <host>` output.
For an unlisted Chromium browser, choose other and set browser.chromium_user_data_dir.
