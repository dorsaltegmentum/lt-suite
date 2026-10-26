<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Browser adapters

The lt-suite browser layer runs checked page scripts in your existing, signed-in Chromium session.

The browser contract has 5 verbs, each with a defined input and result.
The contract omits raw clicks because Lt controls can commit answers or cross boundaries.
The `upload` verb assigns image bytes directly to the intended panel's file input.
The user reviews saved answers and performs any submission through their own browser.

| Verb | Input | Result | Rule |
|---|---|---|---|
| `tab` | None | Tab identifier | Select your existing Lt tab; ask you to open it when absent. |
| `navigate` | Checked URL | None | Validate the URL first; execute the guard immediately after navigation. |
| `eval_file` | Rendered JavaScript file | Small JSON receipt | Execute page code only through the renderer's output. |
| `upload` | Panel key and checked image | Upload receipt | Run page JavaScript through `eval_file`; never use an agent-side upload tool. |
| `screenshot` | Output path | Image file | Capture an optional visual check of the current page. |

## Adapter selection and agent mapping

The `browser.adapter = "auto"` setting chooses the first available adapter in this order.

| Priority | Adapter |
|---|---|
| `1` | Claude in Chrome |
| `2` | `lt-chrome`, using chrome-devtools-mcp |
| `3` | Playwright MCP |
| `4` | Manual paste |

| Agent or backend | `tab` | `navigate` | `eval_file` | `upload` | `screenshot` |
|---|---|---|---|---|---|
| Claude in Chrome, Claude Code or Desktop | `tabs_context_mcp` | `navigate` | `javascript_tool`, loader and compressed payload protocol | Page JS through `eval_file` | `computer`, action `screenshot` |
| chrome-devtools-mcp 1.12.0, server `lt-chrome` | `list_pages`, `select_page` | `navigate_page`, type `url`, `handleBeforeUnload: "dismiss"` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot(filePath)` |
| Playwright MCP 0.0.83, `--extension` | `browser_tabs`, list/select | `browser_navigate` | `browser_evaluate`, rendered function text | Page JS through `eval_file` | `browser_take_screenshot` |
| Antigravity, `lt-chrome` in `mcp_config.json` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Gemini CLI, `lt-chrome` in `gemini-extension.json` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Codex CLI, `lt-chrome` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Copilot CLI or VS Code, `lt-chrome` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Cursor, `lt-chrome` | `list_pages`, `select_page` | `navigate_page` | `evaluate_script`, function file through `sourcePath` | Page JS through `eval_file` | `take_screenshot` |
| Manual paste, any agent | User opens the Lt tab | User navigates after `check-url` passes | User pastes the expression into DevTools and returns JSON | User uploads the checked image | User captures the image |


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

### `lt-chrome` (chrome-devtools-mcp 1.12.0)

The `lt-chrome` server pins chrome-devtools-mcp 1.12.0.
Its arguments remove whole tool categories inside the server, so every client sees the same reduced set.
Claude Code's `.mcp.json` cannot filter tools; these arguments are what removes the input tools there.

| Argument | Removes |
|---|---|
| `--no-category-input` | `click`, `click_at`, `hover`, `fill`, `type_text`, `drag`, `fill_form`, `upload_file`, `press_key`, `handle_dialog` |
| `--no-category-network` | `list_network_requests`, `get_network_request` (request headers, cookies included) |
| `--no-category-memory` | `take_heapsnapshot` (strings from page memory) |
| `--no-category-performance` | `performance_start_trace`, `performance_stop_trace`, `performance_analyze_insight` |
| `--no-category-emulation` | `emulate`, `resize_page` |
| `--redact-network-headers` | Sensitive headers in any network output that remains |

A `tools/list` call against the pinned 1.12.0 package returned 32 tools with the default arguments and 15 with the arguments above.
The exposed set is `close_page`, `evaluate_script`, `get_console_message`, `get_css_styles`, `lighthouse_audit`, `list_console_messages`, `list_pages`, `navigate_page`, `new_page`, `screencast_start`, `screencast_stop`, `select_page`, `take_screenshot`, `take_snapshot` and `wait_for`.
The 15 keep every tool the 5 verbs use: `list_pages`, `select_page`, `navigate_page`, `evaluate_script`, `take_screenshot`, `wait_for`.
`click_at` appears only with `--experimentalVision`; never pass that flag.

The per-client lists stay as a second layer.
Antigravity uses `disabledTools` and Gemini CLI uses `excludeTools`; both deny `new_page`, `click`, `click_at`, `drag`, `fill`, `fill_form`, `hover`, `press_key`, `type_text`, `upload_file` and `handle_dialog`.
Codex uses `enabled_tools`, which allows only the verbs' tools.
Keep Gemini's user MCP configuration at `trust: false` so tool confirmations remain enabled.
Gemini extension server declarations do not support the user configuration's `trust` field.

Two optional `--blockedUrlPattern` arguments stop the server from navigating to Lt's completion and checkpoint routes:
`--blockedUrlPattern=*://*/i/*/student/courses/*/runs/*/run/complete*` and `--blockedUrlPattern=*://*/i/*/student/courses/*/runs/*/run/checkpoint*`.
The server refuses `--blockedUrlPattern` together with `--allowedUrlPattern`, so a configuration uses one or the other.
Whether Lt's own requests match these patterns is not yet measured on a live run.

Call `navigate_page` with `handleBeforeUnload: "dismiss"`.
The server's default is `"accept"`, which leaves a page even when Lt warns that a save is still pending.
When a dismissed prompt keeps the tab on its page, stop and run the fresh-load verify of that page.
Never pass `initScript` to `navigate_page`; it runs unrendered JavaScript before Lt loads.

### Playwright MCP 0.0.83

Playwright MCP connects to your browser through its `--extension` option.
Allow only these 5 tools: `browser_tabs`, `browser_navigate`, `browser_evaluate`, `browser_take_screenshot`, `browser_wait_for`.
Codex takes that list as `enabled_tools`.
A client that takes only a deny list denies the other 20 core tools of 0.0.83:
`browser_click`, `browser_close`, `browser_console_messages`, `browser_drag`, `browser_drop`, `browser_emulate_media`, `browser_file_upload`, `browser_fill_form`, `browser_find`, `browser_handle_dialog`, `browser_hover`, `browser_navigate_back`, `browser_network_request`, `browser_network_requests`, `browser_press_key`, `browser_resize`, `browser_run_code_unsafe`, `browser_select_option`, `browser_snapshot`, `browser_type`.
Never add `--caps`; its `vision`, `network`, `storage` and `devtools` sets add coordinate clicks, routing, cookie access and recording.
The tool names come from the README bundled in the 0.0.83 package.

| Unsupported surface | Reason |
|---|---|
| Codex IDE extension | Codex plugins are unavailable in this surface. |
| ChatGPT browser extension | No documented tool evaluates a local script file in your tab. |
| Cursor built-in browser | Its isolated workspace browser lacks your existing Chrome session and documented JavaScript-file evaluation. |
| claude.ai chat | Its cloud environment cannot reuse your existing local Lt tab. |
| Antigravity IDE browser | Its separate Chrome profile shares no cookies with your normal profile. |
| Antigravity `/browser` | Reuse of your signed-in profile is not verified; select the configured `lt-chrome` server. |
| Copilot cloud agent | Its browser reaches only `localhost` and `127.0.0.1` inside the cloud environment. |

Claude in Chrome's `computer`, `form_input` and `find` tools cannot be removed.
Keep the extension's ask-before-acting setting on for the Lt site.

## Save acknowledgements

A Backbone `sync` event confirms a save only when its request identifies an observed write transport.
The transport must finish with a 2xx status other than 202 and match the calibrated method and endpoint.
Captured or unidentified transports require the fallback wait and fresh-load verification.
Each batch key needs its own explicit successful outcome; a key's error overrides success.
A confirmation set must include every outstanding expectation, including writes added during the wait.
Model calibration receipts include `method` and `path_template`; records missing either use the fallback wait.

## Rendered JavaScript

The active skill sets `SKILL_DIR` to the absolute directory containing its `SKILL.md`.
The renderer resolves bundled script names beneath that skill, independently of the working directory.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form expression --cache --arg key=value
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form function --no-stub --arg key=value
```

| Form | Output | Consumer |
|---|---|---|
| `expression` | An asynchronous expression ending with top-level `await` | Claude in Chrome or manual DevTools paste |
| `function` | An `async () => { ... }` function | chrome-devtools-mcp or Playwright MCP |
| `--stub` | Gzip base64 script plus a SHA-256 integrity check | Inline tools receiving their first payload |
| `--cache` | Loader for verified compressed code in origin storage | Claude in Chrome, Playwright text and manual paste |
| `--chunk-bytes 20000` | Numbered payload files and verified snippets | Inline tools with large first payloads |
| `--no-compress` | Uncompressed stub without caching | Legacy browsers without gzip decompression |

The `--stub` wrapper decompresses gzip bytes and calculates SHA-256 using `crypto.subtle.digest`.
The SHA-256 gate assumes the page's globals and JavaScript prototypes remain unmodified.
This assumption also applied to the previous uncompressed stub.
Modified `TextDecoder`, `Function`, `crypto.subtle`, `Array.prototype`, `JSON.parse` or `Object.freeze` can bypass checks or retain inputs.
A hostile page can already read anything passed to its execution context.
The renderer uses the page's existing execution realm throughout these operations.

Only a matching SHA-256 permits execution of the decoded script through `new Function`.
With intact page globals, a mismatch returns `fatal: "stub-hash-mismatch"` and executes none of the embedded script.
The agent protocol appears once in [the shared browser adapter reference](../shared/references/browser-adapters.md#inline-protocol).
Every inline tool follows that protocol; file tools keep direct `sourcePath` scripts with `--no-stub`.
The `auto` and `claude-in-chrome` adapters default to compressed stubs for ordinary page targets.
Inline fill, verify, check and probe calls start with a cached runner loader containing current call data.
On `need:'payload'`, send the listed runner chunks, verify their receipts, then execute the fallback stub.
Later calls on that origin need only their own loader, including verification after a fresh load.
`emit_fill.py scripts` records fallback paths in `scripts.json`; check commands print render metadata.

A synthetic 5-page fixture measures fill loaders at 3,047 to 3,153 bytes, previously 45,595 to 45,631 bytes.
Per-page verify loaders measure 2,905 to 3,050 bytes, previously 45,401 to 45,469 bytes.
Eligible check loaders measure 4,258 to 4,379 bytes, previously 59,464 to 59,536 bytes.
The aggregate verify loader measures 3,871 bytes for 5 pages, previously 45,766 bytes.
The shared runner uses 63,672 base64 bytes and 4 chunks at 20,000 bytes each.
The first text call transfers 71,394 bytes across chunk snippets and its 3,810-byte final stub.
The fixture contains 1 text, table, MCQ, category and labeling item on separate pages.

Only fixed shipped code enters `localStorage`; jobs, expected hashes, tickets and policies remain call arguments.
The wrapper deep-freezes current arguments and policy, then installs the guard before dispatching any write.
The runner reads its lexical arguments and publishes no callable runner accepting page globals.
Generated answer files remain ineligible for caching; served mode and `--no-stub` retain their existing scripts.

The renderer places arguments in the deep-frozen JSON object named `__LT_ARGS__`.
The wrapper uses `JSON.parse`, preserving a `__proto__` key as an own property.
The renderer avoids concatenating user values into executable JavaScript source strings.

Page scripts contain asynchronous function bodies and return small JSON receipt objects.
The renderer adds `lt_guard.js`, plus `lt_net.js` for fill-related scripts.
The `render` result reports `path`, `sha256`, `form`, and the rendered file's `bytes`.

### Where rendered files go

| Order | Folder |
|---|---|
| `1` | `--out-dir` |
| `2` | `browser.js_tmp_dir` in the config |
| `3` | `<OS temp>/lt-suite-js-<uid>`, or `<OS temp>/lt-suite-js` on systems without user ids |

The folder must be a real directory owned by you with mode `0700`; the renderer creates it that way.
A symlink, a folder owned by another account, or a folder with group or other permission bits makes `render` exit 2 with `unsafe js_tmp_dir`.
Each render creates a new file `<name>-<sha8>-<random8>.js` with mode `0600` and never follows or reuses an existing path.
On a shared machine this stops another account from reading drafts or swapping a script between `render` and `evaluate_script`.
The folder must also be readable by the MCP server: chrome-devtools-mcp reads `sourcePath` only within its roots or its own `os.tmpdir()`.
The setup skill renders `ping`, evaluates it, and records the folder that worked as `browser.js_tmp_dir`.

## URL validation and navigation

Every automated navigation first runs `check-url` with the current fill plan and the tab's current URL.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" check-url --plan plan.json --from "<current tab URL>" "https://example.lt.app/i/inst0001/student/courses/1/runs/101/run/page/1"
```

The `check-url` command accepts matching numbered page routes and the same run's background route.
The numbered route has this shape: `/i/<inst>/student/courses/<c>/runs/<r>/run/page/<n>`.
The background route replaces `/run/page/<n>` with `/run/background` beneath the same run prefix.

| Exit code | Meaning | Agent action |
|---|---|---|
| `0` | URL and plan checks pass. | Navigate, then execute the guard immediately. |
| `1` | The URL is forbidden. | Refuse navigation and tell the user. |
| `2` | The plan, the `--from` URL or the command contains an error. | Refuse navigation and tell the user. |

The `check-url` command rejects foreign origins, preview URLs, completion routes, and checkpoint routes.
It rejects report pages, checkpoint pages, excluded routes, and flow indexes at or beyond `stop_before_page`.
Numbered page routes fail without `--plan`; automated navigation always supplies the current plan.

URL page numbers count content pages only: checkpoint pages use `/run/checkpoint/<n>` routes and the report page uses `/run/complete`.
A URL number is therefore never taken as a flow position.
`check-url` maps it through the plan's own map, and a plan with no map fails every numbered route with `plan-has-no-page-map`.

With `--from`, `check-url` also checks every flow position strictly between the current page and the destination, in either direction.
A checkpoint page or a position at or past `stop_before_page` on the way forbids the move.
Report pages and excluded pages on the way do not, because a direct navigation never visits them.

| Result `reason` | Meaning |
|---|---|
| `crosses-checkpoint` | A checkpoint page lies between the current page and the destination. |
| `crosses-stop` | A position at or past `stop_before_page` lies on the way. |
| `current-not-in-plan` | The tab is on a page of this run that the plan cannot place. |
| `bad-from-url` | The `--from` value is not a URL (exit 2). |

A `--from` URL outside the plan's run skips the crossing check and reports `crossing: "outside-run"`.
Without `--from`, the result reports `crossing: "not-checked"`.

| Plan field | How `check-url` and the guard use it |
|---|---|
| `origin` | Uses this origin, or config `lt.origin` when absent. Conflicting configured and planned origins forbid navigation. |
| `instance`, `course_id`, `run_id` | Requires each supplied identifier to match the target URL. |
| `stop_before_page` | Forbids flow indexes greater than or equal to this integer; `null` adds no limit. |
| `never_navigated` | Excludes route strings, integer flow indexes, `{"route": ...}`, or `{"page_index": n}` entries. |
| `boundary_map.never_navigated` | Supplies exclusions only when top-level `never_navigated` is absent. |
| `report_pages` | Identifies report pages by their flow indexes. |
| `checkpoint_pages` | Identifies checkpoint pages by their flow indexes. |
| `boundary_map.doors[*]`, `first_boundary` | A door of class `checkpoint-page` marks its `page_index` as a checkpoint page. |
| `pages[*]`, `skipped_pages[*]`, `flow_pages[*]` | `page_index` with `page_no` or `url` maps URLs onto flow indexes, exact URL paths first. `is_report_page`, `is_checkpoint`, and `reason` values `report-page` or `checkpoint-page` mark pages. |
| `batch_pages`, `title_hash` | Adds no URL permission; `check-url` ignores these fields. |

`flow_pages` lists every captured page, so the crossing check can place a tab on a page with nothing to fill.
When any plan page supplies `url` or `page_no`, unmapped numbered URLs fail with `page-not-in-plan`.
The `/run/background` route uses its listed URL mapping, otherwise flow index `1`.

After every navigation, render `guard` using the selected adapter's form and flags.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form expression --cache --plan plan.json
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form function --plan plan.json
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form expression --plan plan.json
```

Execute the matching guard file as the next browser call after navigation.

## Guard behavior and refusals

The `LtGuard` wrapper installs once per document and remains installed after scripts finish.
Only a document reload removes its wrappers; script completion never restores them.

| Guard target | Behavior |
|---|---|
| `HTMLFormElement.prototype.submit` | Throws the `lt-suite never-submit` error. |
| `HTMLFormElement.prototype.requestSubmit` | Throws the `lt-suite never-submit` error. |
| Native `submit` events | A capture-phase listener cancels them (Enter in a field, a submit button). |
| `$root.commitClicked` | Becomes a locked accessor that returns a throwing function; increments `guard_trips`. |
| `$root.setCheckpointCommitState` | Same. |
| `$root.removeCommittedCheckpoints` | Same. |
| `$root.removePagesAfterUncommittedCheckpoints` | Same. |
| Pointer, mouse, touch and click events, Enter and Space keys | Cancelled before any page handler when the control reads like a commit or its Knockout binding names a commit mutator. |
| `window.confirm` | Answers No (`false`) and increments `guard_trips`. |

A locked mutator ignores Lt's later assignments and counts them as `rebinds_blocked`.
Where a mutator cannot be locked, the guard assigns a throwing function and re-checks it every second and on every script run.

The event guard stops handlers Lt bound before the guard ran, which the mutator wrappers cannot reach.
A control counts as a commit control when its text, `aria-label`, `value`, `title` or `alt` matches submit, commit, finish, finalise, hand in, complete, checkpoint or final answer.
It also counts when an element on the way up names one of the 4 mutators in its `data-bind`.
Other buttons, such as Save or OK in a dialog, work as usual.

A step that needs a native `confirm()` dialog stops, because the guard answers No; the user finishes that step.

The Navigation API `navigate` listener cancels completion, checkpoint, report-page, boundary, and excluded navigation.
It also cancels a move that would pass a checkpoint page or the stop, using the flow page Lt painted as the current position.
Capture scripts lack a plan, so their guard blocks every page change.

The guard refuses scripts on completion routes, `/run/checkpoint/*`, preview URLs, report pages and checkpoint pages.
The report-page check detects `reportInfoPanels` on the current flow page.

Every script receipt includes the document's cumulative `guard_trips` count for inspection.
A positive `guard_trips` receipt also supplies `guard_last`, identifying the last blocked action.
A receipt with `guard_degraded` names a guard part that could not install, such as `no-navigation-api`.

| Receipt | Meaning |
|---|---|
| `fatal: "forbidden-route"` | The script body never started because the current route is forbidden. |
| `fatal: "guard-install-failed"` | A form or mutator wrapper could not be installed; the body never started. |
| `fatal: "stub-hash-mismatch"` | The integrity check failed before the embedded script ran. |
| `fatal: "script-threw"` | The script threw; `error` contains its first 200 characters. |
| `guard_trips > 0` | The guard blocked an action in the current document. |

For any listed refusal or positive `guard_trips`, stop and tell the user.
Ask the user and wait for the answer.

The fresh-load verification workflow reloads the tab before the user's own review.
The user's submission happens in a document with no installed lt-suite guard.

### What the guard cannot do

The page-side guards contain accidents in lt-suite's own scripts and in raw tool calls made in the same document.
They cannot stop code that kept a reference to a mutator and calls it directly, code in another frame, a direct HTTP request to Lt, or JavaScript an agent writes itself.
A browser-initiated navigation (`navigate_page`, the address bar) cannot be cancelled from the page; the guard only counts it as `uncancelable`.
The hard controls sit outside the page: the agent runs only files that `ltjs.py render` wrote, and the browser server exposes no input tools.
`check-url` before every navigation is the control for agent-initiated navigation.

## Upload checks

The `check-upload` command validates image bytes before the `upload` script reaches Lt.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" check-upload "<data>/figure.png"
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render upload --form function --arg panel=<key> --file "<data>/figure.png"
```

Use the adapter's render form and the shared inline protocol for inline tools.

| Check | Required result |
|---|---|
| Path | A regular file inside the configured data directory; a symlink is refused. |
| File size | At most 8 MB. |
| File content | PNG or JPEG magic bytes. |
| Panel lookup | Exactly 1 `input[type=file]` inside the named panel's own root. |

The renderer embeds the checked image's bytes, MIME type, filename, and SHA-256.
The page script constructs a `File` through `DataTransfer` and sets `input.files`.
The `upload` script dispatches `change`, returns the assigned bytes' SHA-256, and never clicks.
The `upload` receipt identifies assigned bytes; fresh-load verification checks the saved result.

## Tool limits and connection requirements

| Tool or connection | Limit | Required response |
|---|---|---|
| Claude in Chrome `javascript_tool` | Inline JavaScript text only | Read the rendered file and pass its text. |
| Claude in Chrome `javascript_tool` | Returned strings truncate near 950 characters; objects truncate beyond 4 levels | Keep receipts small and flat. |
| Claude in Chrome `javascript_tool` | A non-awaited promise returns `{}` | Preserve the rendered expression's top-level `await`. |
| Claude in Chrome native upload | 10 MB per call | lt-suite uses page uploads and retains its own 8 MB check. |
| Claude in Chrome dialogs | JavaScript alert or confirm dialogs block execution | Hand control to the user. |
| chrome-devtools-mcp `evaluate_script` | Local `sourcePath` requires version 1.11.0 or later | Use lt-suite's exact 1.12.0 pin. |
| chrome-devtools-mcp file access | Files must reside within server roots or its own `os.tmpdir()` | Render locally into an accepted directory; pass local paths only. |
| chrome-devtools-mcp `navigate_page` | Accepts `beforeunload` prompts by default | Pass `handleBeforeUnload: "dismiss"`; never pass `initScript`. |
| Playwright MCP `browser_evaluate` | Takes function text; `filename` names an output file | Read the rendered file and pass its text. |
| Playwright MCP file access | Files use workspace roots or the working directory by default | Keep file operations within those roots. |
| Antigravity MCP | 180 s per call and 300 s to connect | Keep each browser call within its configured timeout. |
| Antigravity browser | IDE browsing uses a separate profile; `/browser` profile reuse is not verified | Use the configured `lt-chrome` server. |
| Codex MCP startup | Default startup timeout is 10 s | Set `startup_timeout_sec = 30` for `lt-chrome`. |
| Chrome `--allowedUrlPattern` | Requires Chrome 149 or later; also filters subresources; excludes `--blockedUrlPattern` | Include the required Lt page, asset, and API hosts. |
| Chrome `--autoConnect` | Requires Chrome 144 or later; attaches to every tab of the browser | Prefer a separate Chrome profile for Lt. |
| Chrome debug switches | Chrome 136 or later ignores them on the default profile | Use the existing-browser connection; never copy a signed-in profile. |
| Manual paste | The agent cannot execute browser calls directly | The user pastes expressions and returns each JSON receipt. |

Doctor pre-fetches chrome-devtools-mcp 1.12.0 for explicit Chromium and Antigravity adapters.
Quiet adapters skip npx and pre-fetch; Automatic can opt in with `--prefetch-mcp`.
Live Lt capture, fill, upload, and boundary compatibility across these adapters remains unverified.

## Codex configuration example

Approve `setup.py lt-chrome install`, then merge `setup.py lt-chrome snippet --host codex` into your config.
The snippet keeps the 6-tool allowlist, a 30-second startup timeout and approval prompts.
It forwards the config and platform-directory environment variables.
Use `examples/mcp/codex-config.toml` only for the static fallback, which ignores browser settings.

On Chrome 149 or later, you may add `--allowedUrlPattern` with your Lt host, for example `https://example.lt.app`, in place of the `--blockedUrlPattern` pair.
That allowlist also blocks subresources, so list the Lt asset and API hosts too.
Codex reads project configuration only when the project has been trusted.
The generated client configuration snippets live in the repository's `examples/mcp/` directory.

## Credentials

The user signs into Lt manually before the agent selects the existing tab.
The lt-suite credentials rule forbids saving passwords, cookies, tokens, or browser storage state.
Never pass `--storage-state` or use a copied profile for an lt-suite session.
At login pages or CAPTCHAs, ask the user and wait for the answer.
