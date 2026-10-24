<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Failure symptoms, safe recovery and required evidence. -->

# Troubleshooting

Stop navigation when a save, target or boundary remains uncertain.

| Symptom | Likely cause | Recovery |
|---|---|---|
| Python version error | Runtime is older than 3.11 | Run setup bootstrap using [Python installation](install/python.md) |
| Externally managed environment error | Installation targets system Python | Use the bootstrap virtual environment |
| Missing Python import | Dependencies are absent from the selected environment | Run bootstrap and doctor |
| Sink request times out | Browser cannot reach the receiver's network namespace | Start the sink in a normal terminal or choose download transport |
| Sink returns 403 | Origin is missing or outside the allow-list | Check `lt.origin` and the receiver's allowed origins |
| Sink returns 401 | Temporary sink token does not match | Restart the authorised receiver and render a fresh capture script |
| `unsafe js_tmp_dir <path>: mode 0755 ...` | Render folder is shared, a symlink or owned by another account | Use an owner-only folder; run `chmod 700` only on a folder you own, set `browser.js_tmp_dir`, then repeat setup's browser check |
| `check-url` reports `current-not-in-plan` | Current run page is absent from the plan's page mapping | Navigate the tab yourself to a planned page, or regenerate the plan with `flow_pages` |
| Download never appears | Browser prompt or download permission is pending | Inspect browser prompts and the configured Downloads directory |
| Rendered JavaScript path is refused | MCP and shell temporary directories differ | Repeat setup's browser check and record the working `browser.js_tmp_dir` |
| Script hash check fails | Copied or served bytes differ from the rendered file | Regenerate and transfer the complete file |
| Returned JSON is truncated | Browser tool limits large return strings | Use capture transport and short receipts |
| Save is `unconfirmed` | Expected acknowledgements did not arrive before the cap | Remain on the page and perform fresh-load verification |
| Save reports an error | A matching save failed | Stop, preserve the receipt and inspect persisted state |
| Batch returns to one page | Calibration or cross-page verification failed | Review the recorded reason and repeat safe calibration |
| Verification succeeds only before reload | Values may exist only in client memory | Treat persistence as unverified and inspect the fresh load |
| Target already contains work | Capture is stale or a human edited the field | Ask before replacement and preserve existing work |
| Panel selector matches several inputs | Target scope is ambiguous | Resolve the exact page and panel; leave a TODO if ambiguity remains |
| Unknown plugin appears | No supported handler exists | Record the shape and leave that item unwritten |
| Upload geometry changes | Lt stored a resized or rounded image | Read the stored geometry and verify the replacement image key |
| URL check refuses navigation | Plan, origin or boundary does not allow the route | Inspect the plan; keep the refusal in force |
| Model-answer rendering is refused | `model_answer.read` is false | Respect the configured policy |
| Export refuses an edited page | Re-export would overwrite human changes | Review the preserved diff before deciding how to merge |
| Diagram case is refused | Folder lies outside trusted roots | Inspect executable case files before considering `--trust-case` |
| Palette derivation fails | A rendered role pair fails accessibility checks | Choose another measured palette or inspect the named failure |
| Rendering binary is missing | Host lacks a required system tool | Run doctor and preserve editable outputs until rendering is available |

## Browser-call timeouts

A browser tool can time out while its page-side task continues.

Inspect the saved receipt and current state before retrying. Retrying an unknown operation can duplicate a write or media request.

Run media retrieval sequentially.

## Browser-tool filters

Some adapters filter long encoded strings or sensitive-looking property names.

Keep returned receipts small. Do not rename or alter captured content merely to evade a filter.

Use the transport and script form documented in [browser adapters](browser-adapters.md).

## Reports

A public report should contain versions, synthetic reproduction steps and sanitised errors.

Remove account details, authentication values, Lt identifiers, questions, answers, screenshots and grades.

Report security weaknesses through [private reporting](../SECURITY.md).

## lt-chrome startup

| Doctor or startup message | Fix |
|---|---|
| `lt-chrome did not start: browser.devtools_url` | Use HTTP on `127.0.0.1` or `[::1]` with port 1 to 65535; values stay hidden. |
| `browser.chromium_user_data_dir is required` | Choose `other` and supply the folder above the Profile Path from `chrome://version`. |
| `this agent did not pass the plugin folder` | The 0.1.0 fallback ignores settings; approve `lt-chrome install`, merge `lt-chrome snippet --host <host>`, restart. |
| `no lt-chrome launcher under the plugin folder` | Reinstall lt-suite or use the stable launcher snippet. |
| `no DevToolsActivePort` | Start the selected browser and enable remote debugging. |
| `unexpected second line` | Stop attaching and restart the browser with remote debugging; doctor refuses the malformed file. |
| `npx cache working folder cannot be written` | Allow the platform cache's `lt-chrome/run`; fallback npx otherwise uses the agent folder. |
| `Node too old` | Install Node 20.19+ or 22.12+; Node 21 gets a package-support warning. |
| `npm's npx-cli.js not found next to node.exe` | Reinstall Node with npm; settings-aware Windows startup requires the adjacent npm script. |
| `stable launcher ... run setup.py lt-chrome install` | Approve installing the current launcher again after upgrading the skill. |
| `last started with config ... but lt-suite reads` | Set `LT_SUITE_CONFIG` or use the platform config, then restart the agent. |

`setup.py lt-chrome plan --json` shows the redacted action and config source.
`doctor --probe-devtools` probes only the configured loopback HTTP listener and follows no redirects.
