<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Browser adapters

Use lt-suite's 5 browser verbs in the user's existing, signed-in Chromium session.

`SKILL_DIR` names the absolute directory containing the active skill's `SKILL.md` file.
Run every lt-suite script through its absolute path beneath `SKILL_DIR`.

## The 5 verbs

| Verb | Input and output | Rule |
|---|---|---|
| `tab` | No input; returns a tab identifier | Select the user's existing Lt tab. Never open a URL or create a profile. |
| `navigate` | URL; no result | Run `check-url --plan --from` first. After navigation, make the guard script the next call in that tab and require `ok: true` before filling. |
| `eval_file` | Rendered JavaScript file; small JSON receipt | Run page code only through files produced by `ltjs.py render`. |
| `upload` | Panel key and checked image; receipt | Execute the rendered upload script through `eval_file`. Never use an agent-side upload tool. |
| `screenshot` | Output path; image file | Use an optional screenshot to inspect the page visually. |

Lt agents never click, type or press keys in the Lt tab, and never submit, commit or finish.
Every write runs inside a rendered page script.
After each navigation, the guard waits for the view model within `fill.save_wait.render_wait_ms`.
A guard receipt with `ok: false` or `fatal: "guard-degraded"` stops subsequent fill calls.
At login pages or CAPTCHAs, ask the user and wait for the answer.

## Render and run

The `ltjs.py render` command writes a file and prints its metadata.
Its result includes `path`, `sha256`, `form`, and the rendered file's `bytes`.

Replace `<name>` with the bundled script name required by the active skill.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form expression --cache --arg key=value
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form function --no-stub --arg key=value
```

The `--arg` option places values inside the deep-frozen JSON object `__LT_ARGS__`.
Never construct page code by concatenating user values into JavaScript strings.
The renderer adds `lt_guard.js`; fill-related scripts also receive `lt_net.js`.

| Browser tool | Form and flags | Execution |
|---|---|---|
| Claude in Chrome `javascript_tool` | `--form expression --cache` | Follow the inline protocol below with rendered expression text. |
| chrome-devtools-mcp `evaluate_script` | `--form function --no-stub` | Pass the local rendered path as `sourcePath`, with `format="function"`. |
| Playwright MCP `browser_evaluate` | `--form function --cache` | Follow the inline protocol below with rendered function text. |
| Antigravity, Gemini CLI, Codex CLI, Copilot, Cursor through `lt-chrome` | `--form function --no-stub` | Use the chrome-devtools-mcp execution rule. |
| Manual paste | `--form expression --cache` | The user executes each rendered expression and returns its JSON receipt. |

The `expression` form ends with top-level `await`, which preserves asynchronous results.
The `--stub` form decompresses gzip bytes, verifies SHA-256, then executes through `new Function`.
The `claude-in-chrome` and `auto` adapters default to compressed stubs without another configuration switch.

The SHA-256 gate assumes the page's globals and JavaScript prototypes remain unmodified.
This assumption also applied to the previous uncompressed stub.
Modified `TextDecoder`, `Function`, `crypto.subtle`, `Array.prototype`, `JSON.parse` or `Object.freeze` can bypass checks or retain inputs.
A hostile page can already read anything passed to its execution context.
The renderer uses the page's existing execution realm throughout these operations.

### Inline protocol

1. Render the target with `--cache`, the adapter's form, and every current argument and plan.
   Execute the file's text inline and inspect the awaited JSON receipt.
2. On `{ok:false, need:'payload', want:<12 hex>}`, render the same target with `--stub` instead of `--cache`.
   Keep the same form, arguments, plan and network flags so `body_sha256` matches.
3. For large payloads, add `--chunk-bytes 20000` to that stub render command.
   Its JSON lists `chunks` with numbered snippet `path`, `payload_path`, `len` and `sha16` values.
   Execute each snippet in order and compare its `{i,len,sha16}` receipt with the printed metadata.
   Expressions await at top level; function tools receive the rendered function form.
   Stop on any mismatch and render a fresh set before retrying.
4. Execute the stub file named by the top-level `path` and inspect its normal target receipt.
   The stub consumes assembled chunks and clears that body's temporary global payload.
5. Start later calls with step 1, including after navigation within the same Lt origin.
   Blocked storage can require another stub on every call; a cache miss executes nothing.

With intact page globals, only verified compressed code enters `localStorage` under `lt-suite:js:<full SHA-256>`.
The `lt-suite:js:index` key tracks at most 6 entries and prunes the oldest stored bodies.
Each body excludes arguments and policy; every call supplies deeply frozen current inputs.
The wrapper clears input bindings after completion so published closures cannot reuse a completed run's token.
Only allowlisted shipped script names inside bundled browser directories qualify; `cacheable` reports that decision in render metadata.
Generated filename prefixes remain excluded even when their files appear inside a bundled browser directory.
Generated fill files keep their original contents for `--no-stub` and served adapters.
Inline fill emission renders the shipped `fill_runner` with each call's data as separate JSON arguments.
The runner combines `fill_lib.js`, `probe_panels.js`, `check_answer.js` and a fixed method dispatcher.
Fill, probe and check functions stay inside each call's lexical scope throughout execution.
Served loaders verify both library sources and create private instances for each call.
Generated answer files remain ineligible for caching, including when placed inside bundled browser directories.
With intact page globals, cache writes contain code and exclude tokens, Lt content and receipts.
Unreadable, corrupt or mismatched entries return `need:'payload'`; cleanup preserves replacements written after the failed read.
Successful insertion precedes pruning; failed writes preserve existing bodies and leave later calls to repair the index.
Index repair reserves the current body among 6 survivors and records entries before pruning.
Browsers without `DecompressionStream` return `fatal:'stub-no-decompression'` and execute no body.
Use `--stub --no-compress` for legacy browsers; that form bypasses the page cache.
Never remove the SHA-256 check or replace refused payloads with handwritten page code.

Rendered files go to `browser.js_tmp_dir`, else `<OS temp>/lt-suite-js-<uid>`.
`render` exits 2 with `unsafe js_tmp_dir` when that folder is a symlink, belongs to another account, or has group or other permissions.
Then stop and tell the user; never loosen the folder's permissions to get past it.

### Fill call protocol

`emit_fill.py scripts` lists each loader in `scripts.json` under `calls[].rendered`.
Each call also lists `runner_payload.path` and its numbered `runner_payload.chunks` metadata.
`check` and `check-verify` print render metadata with the same fallback under `payload`.
Inline `probe_panels --cache` returns a loader for the same runner.
Use the generic inline protocol to render `--stub` and execute its top-level `path`.

1. Execute the loader with that call's data, current plan and adapter form.
2. On `need:'payload'`, execute that call's runner chunks in order and compare every transfer receipt.
3. Execute the listed fallback stub once; its receipt belongs to the fill, verify, check or probe call.
4. Start every later call with its own loader, including after a fresh page load.

Each cache hit verifies the decompressed runner against WANT before installing this call's guard and executing.
Arguments include jobs, expected hashes, calibration tickets, save waits and check plans.
Only shipped code enters storage or chunk assembly; the wrapper clears argument bindings after completion.
A blocked cache repeats the chunk fallback, while a miss executes no page code.

A synthetic 5-page fixture uses 1 text, table, MCQ, category and labeling item per page.
Fill loaders measure 3,047 to 3,153 bytes, replacing 45,595 to 45,631 rendered bytes per call.
Per-page verify loaders measure 2,905 to 3,050 bytes, replacing 45,401 to 45,469 rendered bytes per call.
Check loaders measure 4,258 to 4,379 bytes, replacing 59,464 to 59,536 rendered bytes per eligible call.
The aggregate 5-page verify loader measures 3,871 bytes, replacing 45,766 bytes.
The runner carries 63,672 base64 bytes in 4 chunks at `--chunk-bytes 20000`.
The first text call's chunk snippets and final stub total 71,394 bytes, including its 3,810-byte stub.
Call data and plan size determine loader size; the runner body stays identical across these calls.

## Navigation and upload

Run `check-url` before every navigation, including navigation performed by the user.
Pass the tab's current URL as `--from`.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" check-url --plan plan.json --from "<current tab URL>" "https://example.lt.app/i/inst0001/student/courses/1/runs/101/run/page/1"
```

| Exit | Meaning | Action |
|---|---|---|
| `0` | The URL passes the plan, origin and crossing checks. | Navigate, then install the guard immediately. |
| `1` | The URL is forbidden. | Stop and tell the user. |
| `2` | The plan, the `--from` URL or the command contains an error. | Refuse navigation and tell the user. |

The `check-url` command permits only matching Lt page routes and `/run/background`.
The plan blocks report pages, checkpoint pages, excluded routes, and flow indexes reaching `stop_before_page`.
URL page numbers reach a flow position only through the plan's page map; a plan without one forbids every numbered route.
With `--from`, a move that would pass a checkpoint page or the stop, forward or back, is forbidden (`crosses-checkpoint`, `crosses-stop`).
Completion, checkpoint, preview, and foreign-origin routes always fail.

With chrome-devtools-mcp, call `navigate_page` with `type: "url"` and `handleBeforeUnload: "dismiss"`.
If Lt's leave-page prompt keeps the tab where it was, a save is pending: stop and run the fresh-load verify.
Never pass `initScript` to `navigate_page`.

After every navigation, render `guard` using your adapter's form and flags, with the plan when there is one.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form expression --cache --plan plan.json
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form function --plan plan.json
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form expression --plan plan.json
```

Execute the selected guard file as the next browser call after navigation.
The guard persists until reload and refuses completion, checkpoint, preview, and report pages.
It cancels form submission, clicks and Enter or Space on commit-labelled controls, and answers No to `confirm()` dialogs.
A step that needs a `confirm()` dialog stops there; hand it to the user.

Run `check-upload` before uploading an image into its intended Lt panel.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" check-upload "<data>/figure.png"
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render upload --form function --arg panel=<key> --file "<data>/figure.png"
```

Use your adapter's form and the inline protocol when rendering `upload`.
The `check-upload` command requires a regular file inside the configured data directory.
It refuses symlinks and files over 8 MB, and checks PNG or JPEG magic bytes.
The `upload` script requires exactly 1 file input inside the named panel.
It assigns embedded bytes through `DataTransfer`, dispatches `change`, and returns their SHA-256.
The `upload` script refuses missing or multiple inputs and never dispatches clicks.

## Mapping per agent

| Agent | `tab` | `navigate` | `eval_file` | `upload` | `screenshot` |
|---|---|---|---|---|---|
| Claude in Chrome, Claude Code or Desktop | `tabs_context_mcp` | `navigate` | `javascript_tool`, loader then compressed stub through the inline protocol | Page JS through `eval_file` | `computer`, action `screenshot` |
| chrome-devtools-mcp 1.12.0, server `lt-chrome` | `list_pages`, `select_page` | `navigate_page`, type `url`, `handleBeforeUnload: "dismiss"` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot(filePath)` |
| Playwright MCP 0.0.83, `--extension` | `browser_tabs`, list/select | `browser_navigate` | `browser_evaluate`, function text | Page JS through `eval_file` | `browser_take_screenshot` |
| Antigravity, `mcp_config.json`, `disabledTools` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Gemini CLI, `gemini-extension.json`, `excludeTools` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Codex CLI, `lt-chrome`, `enabled_tools` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Copilot CLI or VS Code, `lt-chrome` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Cursor, `lt-chrome` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Manual paste | User opens the tab | User navigates after validation | User pastes expression text and returns JSON | User uploads the checked image | User captures the image |

The `auto` adapter prefers Claude in Chrome, then `lt-chrome`, then Playwright MCP, then manual.
The `lt-chrome` server runs with `--no-category-input`, so it offers no click, type, key, upload or dialog tools.
Its configured arguments also include `--no-category-network`, `--no-category-memory`, `--no-category-performance`, `--no-category-emulation` and `--redact-network-headers`.
The server exposes at most 15 tools: `close_page`, `evaluate_script`, `get_console_message`, `get_css_styles`, `lighthouse_audit`, `list_console_messages`, `list_pages`, `navigate_page`, `new_page`, `screencast_start`, `screencast_stop`, `select_page`, `take_screenshot`, `take_snapshot` and `wait_for`.
Gemini and Antigravity also deny `click_at` alongside the other input tools. Never add `--experimentalVision`.
With Playwright MCP use only `browser_tabs`, `browser_navigate`, `browser_evaluate`, `browser_take_screenshot` and `browser_wait_for`.
With Claude in Chrome, never use `computer` clicks or typing, `form_input` or `find` in the Lt tab.
Unsupported for Lt: the Codex IDE extension (plugins do not run there), the ChatGPT browser extension and Cursor's built-in browser (no local script-file evaluation in your signed-in profile), and claude.ai chat (a cloud container cannot reach your tab).


### Configurable lt-chrome startup

`browser.adapter` controls lt-chrome when the host passes its plugin folder.
`auto`, `chrome-devtools-mcp` and `antigravity` start the pinned 1.12.0 server.
`claude-in-chrome`, `playwright-mcp` and `manual-paste` serve zero tools without npx or network access.
Restart the agent after changing the config.
A workspace `lt-suite.toml` cannot start lt-chrome when the platform config chose a quiet adapter.
An explicit `LT_SUITE_CONFIG` takes precedence over both files.

Choose `browser.chromium` from this table. A nonempty `browser.chromium_user_data_dir` overrides every folder and channel.
Use the folder above the Profile Path displayed at `chrome://version`.
Linux uses absolute `$XDG_CONFIG_HOME`, falling back to `~/.config`.
The folder names for browsers other than Chrome remain unverified until doctor finds the file.

| Browser value | Linux folder under config home | macOS folder under ~/Library/Application Support | Windows folder under %LOCALAPPDATA% |
|---|---|---|---|
| `chrome`, `chrome-beta`, `chrome-dev`, `chrome-canary` | Chrome resolves its channel folder | Chrome resolves its channel folder | Chrome resolves its channel folder |
| `chromium` | `chromium` | `Chromium` | `Chromium/User Data` |
| `edge` | `microsoft-edge` | `Microsoft Edge` | `Microsoft/Edge/User Data` |
| `brave` | `BraveSoftware/Brave-Browser` | `BraveSoftware/Brave-Browser` | `BraveSoftware/Brave-Browser/User Data` |
| `vivaldi` | `vivaldi` | `Vivaldi` | `Vivaldi/User Data` |
| `other` | Supply `browser.chromium_user_data_dir` | Supply the folder | Supply the folder |

Auto-connect uses remote debugging at `chrome://inspect/#remote-debugging`, available in Chrome 144+.
Doctor reads only DevToolsActivePort's port and path line; it never reads browser cookies or page data.

`browser.devtools_url` selects HTTP discovery instead of auto-connect.
Only `http://127.0.0.1:<port>` or `http://[::1]:<port>`, optionally ending in `/`, is accepted.
Ports range from 1 to 65535; credentials, other hosts and other schemes are refused.
Start that browser yourself with `--remote-debugging-port` and a separate `--user-data-dir`, then sign in to Lt.
lt-suite creates no browser or profile. Turn remote debugging off after the session.

The loopback rule constrains the configured first hop.
The pinned server follows the listener's WebSocket address and redirects; auto-connect trusts DevToolsActivePort.
A local process controlling that listener or profile can redirect the connection.
Doctor checks the path line and, only with `--probe-devtools`, checks HTTP discovery's WebSocket host.
`lt.allowed_url_patterns` remains unwired.

The launcher disables the server's own cd4a config discovery and removes its inherited feature variables.
It runs `npx --no-workspaces` under the platform cache's `lt-chrome/run` folder with a `package.json` stop file.
An unwritable cache restores 0.1.0's agent working folder and prints a warning.
npm still searches ancestor `node_modules/.bin` for `node` and `sh`; the environment and the user's folders remain trusted inputs.

Hosts that expose no usable plugin folder run the 0.1.0 auto-connect fallback, ignoring browser settings.
One stderr line explains the fallback; doctor reports its separate last-fallback record.
Plugin-folder expansion in Codex, Antigravity, Copilot and VS Code remains unverified.
For paste-in hosts, approve `setup.py lt-chrome install`, then merge `setup.py lt-chrome snippet --host <host>` output.
Supported hosts: `codex`, `cursor`, `vscode`, `copilot`, `gemini-settings`, `antigravity`.
Static `examples/mcp/` entries remain a hardened 0.1.0 fallback and ignore browser settings.
`setup.py lt-chrome plan --json` prints the redacted startup decision.
Run `lt-chrome install` again after upgrading the skill; doctor warns when the stable copy is stale.

## Limits and refusals

| Tool | Limit that changes agent behavior |
|---|---|
| Claude in Chrome `javascript_tool` | Inline text only; returned strings truncate near 950 characters, and objects truncate beyond 4 levels. Keep receipts small and flat. |
| Claude in Chrome `javascript_tool` | A non-awaited promise returns `{}`. Use the rendered expression's top-level `await`. |
| chrome-devtools-mcp `evaluate_script` | `sourcePath` requires 1.11.0 or later; lt-suite pins 1.12.0. Use local files within server roots or its own `os.tmpdir()`. |
| chrome-devtools-mcp `navigate_page` | Accepts leave-page prompts by default. Pass `handleBeforeUnload: "dismiss"`. |
| Playwright MCP `browser_evaluate` | Takes function text. Read the rendered file and pass its text; its `filename` parameter names an output file. |
| Antigravity | Each tool call has a 180 s timeout. Keep work within that call limit. |
| Manual paste | The user executes each rendered expression and returns JSON before the agent continues. |

| Receipt | Meaning |
|---|---|
| `fatal: "forbidden-route"` | The guard refused the current route before running the script body. |
| `fatal: "guard-install-failed"` | A guard wrapper could not be installed; the script body never ran. |
| `fatal: "stub-hash-mismatch"` | The stub detected changed bytes and executed no script body. |
| `fatal: "stub-no-decompression"` | The browser lacks gzip decompression; use the legacy uncompressed stub. |
| `fatal: "stub-chunk-mismatch"` | A chunk failed verification or arrived out of order; execute no stub. |
| `need: "payload"` | The loader executed nothing; continue with the inline protocol's stub step. |
| `fatal: "script-threw"` | The script threw; the receipt includes its error and `guard_trips`. |
| `guard_trips > 0` | A guard blocked an action; the cumulative count survives within this document. |

For any fatal refusal or positive `guard_trips`, stop and tell the user.
Ask the user and wait for the answer.

The page guard contains accidents in lt-suite's own scripts; it cannot stop JavaScript an agent writes itself.
Run only rendered files, and never work around a refusal.
