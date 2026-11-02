<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Changelog

lt-suite records notable changes using [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Update fonttools to 4.68.0 in `uv.lock` and the skill requirements.
- Update pytest to 9.2.0 in `uv.lock`.

### Fixed

- Accept Dependabot's sign-off in the pull-request DCO check, only on pull requests that Dependabot opened.

## [0.2.4] - 2026-11-02

### Added

- Report blocked mutator rebinds in script receipts as `guard_rebinds_blocked`.
- Name the commit mutators that got the start-up placeholder in `LtGuard.status().late_define_blocked` and in script receipts as `guard_late_define_blocked`.
- Count replies read only in part (past 32 levels or 50,000 values) as `resp_truncated` in `net_diag`.
- Add `binding: "unbound-legacy"` to a fill report item's receipt when the receipt carries no `source_payload`.
- Add `fill-notes.schema.json`, a template that `emit_fill.py payload` writes, and `make_fill_report.py --check-notes`, which lists every problem at once and writes nothing.
- Add `emit_fill.py repin`, which moves a payload's `answers.json` pin after a change that leaves every written draft identical, checks the question digest, and never writes `answers.json`.
- Add `run_notes` to fill notes and reports, and `source_check_plan` to reports.
- Print `MANUAL MODE` pages, `held for approval` counts and each single-attempt item's captured state ("answered, not submitted") in `plan_fill.py`.
- Optional loopback body server (`browser.body_server`, off by default): on a page-cache miss the loader fetches the body by SHA-256 from the host and checks it before running it.
- `tools/install_body_server.py` copies the server into a host-only folder after checking `packaging/host-files.sha256`, and writes a systemd user unit it never enables.
- `ltjs.py render cache_status` reports which page scripts the origin's cache holds, without running them.
- Add `browser.devtools_socket` so lt-chrome reaches a DevTools port through a Unix socket from a sandbox with its own network.
- Report the socket's state and the chosen route in doctor.

### Changed

- Hold single-attempt drafts without an approval in the plan; the report lists them skipped, "yours to check and submit", with no note needed.
- Refuse a stale `page-kinds.json` at payload time with or without a RAT candidate page; the report applies the same rule.
- Default `started_at` to the earliest bound payload's `generated_at` and `ended_at` to the last receipt; a notes time that differs needs `times_reason`.
- Replace `fill-report.json` only after the validator passes the new report; a rejected report goes to `fill-report.rejected.json`.
- Rename the capture summary field `table_author_grids` to `table_given_grids`; the capture skill reads either name in stored receipts.
- Return 16-hex `source_payload_sha16` and upload `sha16` values in browser receipts in place of full sha256 values.
- Refuse `record --supersede` on a stored row without `verify_receipt_sha256`, and archive the fields that changed.
- Every `need:'payload'` reply names its `reason`.
- The page cache keeps entries in least-recently-used order within 524,288 characters; a hit refreshes its entry.
- Cached bodies no longer carry the lt-suite version, so a release keeps unchanged scripts cached.
- With a body server, loaders name a plan policy longer than 256 characters by SHA-256 instead of carrying it.

### Fixed

- Record each page's real Lt route in captures. A Background page gets `/run/background` and flow page N after it gets `/run/page/N-1`. Capture takes each number from the page's `dataKey`, checks it against the tab's URL and painted page, and leaves `url` null when they disagree. New captures carry `route_source`.
- Refuse page routes from every capture without `route_source`, whatever its titles or boundaries. Nothing else in it reliably marks a Background page, after which its URLs were one page late and could pass a live Checkpoint. `check-url` and the guard report `recapture-needed`, and media, fill and check plans drop those URLs. Export and answer drafting still read such a capture.
- Refuse a plan without the `route_source` key, which was built before measured routes and holds no titles or source URL to check. Media, fill and check plans always write the key, `null` allowed.
- Map `/run/background` only through a captured url, never by default to flow page 1.
- Reject plans in which two pages claim one route or a URL number exceeds the pages before it.
- Accept Background pages, unconfirmed routes and `route_source` in the module schema and validator, and check that URLs match `page_no`.
- Let Lt's start-up call `removeCommittedCheckpoints` and `removePagesAfterUncommittedCheckpoints` before it paints a page. The guard locks both once a page paints or `render_wait_ms` runs out, and runs no script body before then.
- Give `commitClicked` or `setCheckpointCommitState`, when Lt has not defined it yet, a non-configurable placeholder accessor on `$root` from the moment the guard installs. A raw call throws at once. Lt's plain assignment lands without throwing and never becomes callable. Lt's `Object.defineProperty` on the name throws inside Lt, so a late definition can no longer replace the accessor and run before the next check. The two page-list mutators Lt has not defined lock on `$root` when start-up ends.
- Check the route again after the guard's wait, so the page Lt painted meanwhile decides.
- Wait up to `render_wait_ms` for a painted page before the fill location rail and `fetch_media` read `current()`. Refuse with `lt-not-painted` when none paints; the fill receipt carries `guard_last`.
- Identify Lt's run-data `PUT` by typed `panelKey`, `dataKey` or `uuid` fields, so 3- and 4-digit panel keys can be acknowledged.
- Never acknowledge a run-data save on a bare 2xx: require a per-key success or a top-level `"success": true` reply.
- Fail keys whose single-key reply is HTTP 200 with `{"success": false}`.
- Leave every run-data key pending when a success reply carries a failure marker at any depth, such as `{"success": true, "data": {"success": false}}` or a nested `status: "failed"`.
- Fail a reply item (an object with an identity field) when a failure marker sits anywhere inside it, whatever its own `success` says, so `{"results": [{"panelKey": 209, "success": true, "result": {"success": false}}]}` fails panel 209. Sibling items keep their own outcomes.
- Confirm no key of a request whose reply carries a failure marker outside every item, lists a failed value that names no key of the request, or runs past 32 levels or 50,000 values. Inspection used to stop at 8 levels and accept the rest.
- Leave a generic single-key save unconfirmed when its reply carries any failure marker, such as `{"success": true, "data": {"success": false}}`.
- Sign the Lt client build by the URLs, query strings included, of the core bundles loaded before `DOMContentLoaded`, so page chunks loaded later no longer drop calibration and `/core.js?v=2` no longer keeps a calibration made on `/core.js?v=1`.
- Drop save records bound to the old `c1-` script signature once, with the reason in the calibration history.
- Keep the save record when a fill page shows no build marker.
- Record `keys_weak`, `net_matched` and `save_key_source` in fill receipts, and `keys_weak` and `net_matched` in saved waits.
- Compute `save_diag` `model_sync` per request row, and add run-data identity counts to `save_diag`.
- Name the hash each browser receipt bound by, and the local payload file check, in report items.
- Name every `--payload` path that `record` cannot use, and a payload path outside the run in report binding refusals.
- Print each stored receipt row that `record` keeps because only its binding differs.
- Treat any `[BLOCKED:` value in a receipt's full payload hash as redacted.
- Reject a fill report in a run folder whose `fill-js/check-plan.json` it predates.
- Rebuild every job of a payload from the current `answers.json` before `make_fill_report.py` trusts an `answers-repin.json` record, and refuse on any difference. `repin` records the `--anchors` and `--approvals` it used.
- Compare rebuilt and written jobs in `repin` and in the report by sha256 of their content (page, panel, text, each table cell's row and column, selections and placements). The 32-bit receipt hash used before let a text with a colliding hash, or a table cell moved to another row, re-pin and verify.
- Report an ordinary item `filled-unverified`, never `filled`, when its latest receipt carries no `source_payload`: such a receipt is tied to no executed payload and not to today's draft. Only a receipt bound to an executed payload whose jobs match today's draft verifies.
- Start the default run window at the earliest bound payload's `generated_at`. When no receipt binds a payload it starts at the earliest payload in the run folder, and at the first receipt only when the run folder holds none. Refuse a receipt observed before its own payload was built.
- Name a body server miss only from a fixed set of reasons, so reply text never reaches a receipt.
- Accept `/run/background` in the fill location rail and the panel probe for the plan's Background page, placed by `current()`.
- List `fill-notes.schema.json` and `references/fill-notes.md` in the release allowlist, so release archives build.
- Read a `Signed-off-by` written below a `---` line, as Dependabot writes it, in the public history check.
- Warn in doctor when the stable lt-chrome launcher is missing and the config serves lt-chrome tools.

## [0.2.3] - 2026-10-26

### Changed

- Inline fill calls reuse one cached runner with current frozen arguments and guard policies.
- Download handoffs use paced groups of 8 and report missing media keys by page.
- Media calls use a configurable 20 second budget and return pending keys for resumed downloads.


### Fixed

- Show local citation paths by file name in exported notes and PDFs.
- Name each failed receipt binding check and reserve the RAT prefix for RAT receipts.
- Preserve hyphens at wrapped line ends when checking PDF content.
- Accept bounded save request diagnostics in fill reports and export validation.
- Use a neutral receipt binding message for ordinary fill receipts.
- Keep payload discovery inside each run and resolve manifest paths from their containing folders.
- Bind fill records to the executed payload, including payloads generated in sibling folders.
- Refuse conflicting receipt hashes and archive corrected rows with `record --supersede`.
- Refuse draft status text in answer segments and payload HTML through configurable patterns.
- Check the answer file capture path and hash binding before generating fill payloads.
- Wait for the Lt view model and refuse calls while commit guards remain unlocked.
- Include compact request diagnostics in fallback save receipts and saved wait records.
- Measure table occupancy over target cells while reporting other filled cells separately.
- Name stale page classifications and the command that refreshes them before checking receipt bindings.
- Add short payload hashes for browser tools that redact full hashes and bind unique matches.

## [0.2.2] - 2026-10-26

### Changed

- Inline browser tools receive gzip compressed page scripts with SHA-256 verification.
- Page cache loaders reuse verified code with current frozen arguments and guard policies.
- Chunked payload snippets verify numbered transfers before the stub assembles its body.

## [0.2.1] - 2026-10-26

### Added

- Opt-in `export.citations.transcript_page_from_filename` links recording citations to notes pages named after recording files.

### Fixed

- Table cells no longer repeat the answer's citations when cell provenance references are absent.

## [0.2.0] - 2026-10-25

### Added

- Separate fonts for page PDFs, diagrams and micrograph labels, with Noto and system fallback chains.
- Palette-matched micrograph copies bound to their upload and captured media for PDF and notes exports.
- Configurable Chromium selection, profile folders and loopback DevTools attachment through lt-chrome.
- Setup questions and doctor checks for output fonts, browser attachment, RAT sheets and Check Answer.
- Quarantined RAT answer-sheet folders, matching and shared page classification for permitted sheet entry.
- Opt-in Check Answer retries for eligible multi-attempt items, with attempt records and reconciled exports.

### Changed

- Exported SVGs and interactive diagram pages carry font fallback stacks for other machines.
- RAT filling and Check Answer share current page classifications; Check Answer refuses RAT pages.

### Fixed

- Windows text encoding, line endings, interpreter pinning and path handling.
- Check Answer refuses changed live classification inputs and requires recapture and reclassification.
- lt-chrome rejects unknown browser keys and nested paths before selecting startup defaults.
- Font discovery handles system filenames and checks every rendered face before font modification.
- Micrograph exports reject malformed, stale or edited palette-copy manifests.
- Fill receipts retain the executed sheet identity and reject changed classification bindings.
- Unread Check Answer verdicts require a fresh document before further checks.

### Security

- Font embedding and modification respect licence metadata and embedding restrictions.
- lt-chrome validates loopback addresses and isolates npm startup from workspace configuration.
- RAT sheets and Check Answer feedback stay outside cited research and curriculum reads.

## [0.1.0] - 2026-10-24

### Added

- Six skills: `lt-suite-setup`, `lt-module-capture`, `lt-module-answer`, `lt-module-fill`, `lt-module-export`, and `mechanistic-diagram`.
- Accessible defaults with Noto Sans and Noto Sans Mono, measured palette selection, and configurable alternatives.
- Event-driven save waits that observe acknowledgements and retain a bounded fallback.
- Automatic per-origin calibration after a verified first page, followed by a verified two-page trial.
- Batches of up to five pages after calibration; missing saves restore single-page operation.
- Multi-agent packaging for supported plugin clients and standalone skill archives.
- Synthetic examples, privacy checks, licence notices, and a Developer Certificate of Origin policy for pull requests after release.

### Changed

- Browser navigation waits for save evidence and verifies writes after a fresh reload.
- Institution settings, output paths, palettes, and fonts use configuration.

### Removed

- The permission gate and its configuration, scripts, and call sites.

[0.2.0]: https://github.com/dorsaltegmentum/lt-suite/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dorsaltegmentum/lt-suite/releases/tag/v0.1.0
