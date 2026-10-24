<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Security policy

Report lt-suite vulnerabilities privately through the repository's [Security tab](https://github.com/dorsaltegmentum/lt-suite/security).
Choose **Report a vulnerability** to open a private advisory with maintainers.
Keep exploit details private until maintainers coordinate a fix and disclosure.
If private reporting is unavailable, wait for that channel before sharing exploit details.

## Scope

The security scope includes all released skills, shared libraries, and packaging code.
Report weaknesses in these components through private vulnerability reporting:

- Never-submit guards, commit boundaries, Checkpoint detection, and completion-page protection.
- The loopback sink, including origin checks, authentication, path validation, and data handling.
- Browser adapters, injected scripts, navigation guards, and unsafe browser-tool access.
- Capture quarantine, answer provenance, file uploads, and credential or personal-data exposure.
- `browser.js_tmp_dir` output paths, script permissions, and temporary JavaScript exposure.
- Working-directory `lt-suite.toml` configuration and untrusted configuration discovery.
- Case-folder Python code execution and executable cleaner hooks.
- The loopback serve endpoint, including origins, library integrity, and request handling.
- Dependency or generated-package defects that compromise these protections.

## Report contents

Include the affected version, component, impact, and a minimal synthetic reproduction.
Describe expected behavior, observed behavior, and the browser adapter when relevant.
Include no course material, student work, personal data, credentials, or live session identifiers.
Test only with accounts, modules, and systems you control and may assess.
Use [public issues](https://github.com/dorsaltegmentum/lt-suite/issues) for ordinary bugs without sensitive details.

## Maintenance

Security fixes target the current release and the development branch.
Maintainers coordinate advisory details and release instructions through GitHub's private reporting channel.
