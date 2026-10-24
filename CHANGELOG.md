<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Changelog

lt-suite records notable changes using [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

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

[0.1.0]: https://github.com/dorsaltegmentum/lt-suite/releases/tag/v0.1.0
