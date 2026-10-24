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
| `navigate` | URL; no result | Run `check-url --plan --from` first. After navigation, make the guard script the next browser call. |
| `eval_file` | Rendered JavaScript file; small JSON receipt | Run page code only through files produced by `ltjs.py render`. |
| `upload` | Panel key and checked image; receipt | Execute the rendered upload script through `eval_file`. Never use an agent-side upload tool. |
| `screenshot` | Output path; image file | Use an optional screenshot to inspect the page visually. |

Lt agents never click, type or press keys in the Lt tab, and never submit, commit or finish.
Every write runs inside a rendered page script.
At login pages or CAPTCHAs, ask the user and wait for the answer.

## Render and run

The `ltjs.py render` command writes a file and prints its metadata.
Its result includes `path`, `sha256`, `form`, and the rendered file's `bytes`.

Replace `<name>` with the bundled script name required by the active skill.

```text
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form expression --stub --arg key=value
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form function --arg key=value
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render <name> --form expression --arg key=value
```

The `--arg` option places values inside the deep-frozen JSON object `__LT_ARGS__`.
Never construct page code by concatenating user values into JavaScript strings.
The renderer adds `lt_guard.js`; fill-related scripts also receive `lt_net.js`.

| Browser tool | Form and flags | Execution |
|---|---|---|
| Claude in Chrome `javascript_tool` | `--form expression --stub` | Read the rendered file and pass its text inline. |
| chrome-devtools-mcp `evaluate_script` | `--form function` | Pass the local rendered path as `sourcePath`, with `format="function"`. |
| Playwright MCP `browser_evaluate` | `--form function` | Read the rendered file and pass its function text. |
| Antigravity, Gemini CLI, Codex CLI, Copilot, Cursor through `lt-chrome` | `--form function` | Use the chrome-devtools-mcp execution rule. |
| Manual paste | `--form expression` | The user pastes the rendered text into DevTools and returns its JSON. |

The `expression` form ends with top-level `await`, which preserves asynchronous results.
The `--stub` form checks SHA-256 before executing decoded code through `new Function`.

Rendered files go to `browser.js_tmp_dir`, else `<OS temp>/lt-suite-js-<uid>`.
`render` exits 2 with `unsafe js_tmp_dir` when that folder is a symlink, belongs to another account, or has group or other permissions.
Then stop and tell the user; never loosen the folder's permissions to get past it.

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
python3 "$SKILL_DIR/scripts/_shared/ltcore/ltjs.py" render guard --form expression --stub --plan plan.json
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

Use your adapter's `--form` and `--stub` settings when rendering `upload`.
The `check-upload` command requires a regular file inside the configured data directory.
It refuses symlinks and files over 8 MB, and checks PNG or JPEG magic bytes.
The `upload` script requires exactly 1 file input inside the named panel.
It assigns embedded bytes through `DataTransfer`, dispatches `change`, and returns their SHA-256.
The `upload` script refuses missing or multiple inputs and never dispatches clicks.

## Mapping per agent

| Agent | `tab` | `navigate` | `eval_file` | `upload` | `screenshot` |
|---|---|---|---|---|---|
| Claude in Chrome, Claude Code or Desktop | `tabs_context_mcp` | `navigate` | `javascript_tool`, expression text with `--stub` | Page JS through `eval_file` | `computer`, action `screenshot` |
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
| `fatal: "script-threw"` | The script threw; the receipt includes its error and `guard_trips`. |
| `guard_trips > 0` | A guard blocked an action; the cumulative count survives within this document. |

For any listed refusal or positive `guard_trips`, stop and tell the user.
Ask the user and wait for the answer.

The page guard contains accidents in lt-suite's own scripts; it cannot stop JavaScript an agent writes itself.
Run only rendered files, and never work around a refusal.
