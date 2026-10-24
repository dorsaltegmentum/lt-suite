<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- ChatGPT upload workflow and execution limits. -->

# ChatGPT

`tools/build_dist.py` creates `lt-suite-plugin-<version>.zip` for plugin distribution. It contains a skills-only plugin inside an `lt-suite/` wrapper.

The repository has no verified end-user ChatGPT ZIP installation workflow. OpenAI's [submission guide](https://developers.openai.com/plugins/guides/submit-claude-plugin.md) describes publisher submission; it does not establish a workspace upload control.

## Eligibility and limits

ChatGPT plan eligibility, administrator controls and upload limits remain unverified for this release. Check the workspace's actual capabilities before choosing this surface. See [platforms](../platforms.md).

## Supported workflow

Where the host accepts the skills and supplied files, use an authorised capture to draft answers or prepare diagrams. Review the resulting files yourself.

This release does not support local Lt capture or fill through ChatGPT chat. A plugin upload does not provide access to your existing signed-in browser.

Python execution and rendering binaries depend on the host. When a required tool is unavailable, keep the draft or editable diagram and report the unfinished render.

See [academic integrity](../academic-integrity.md) and [platforms](../platforms.md).

Sources: [skills](https://learn.chatgpt.com/docs/build-skills), [plugin upload documentation](https://developers.openai.com/plugins/guides/submit-claude-plugin.md).
