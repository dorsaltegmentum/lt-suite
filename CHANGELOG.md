<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Changelog

lt-suite records notable changes using [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

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
