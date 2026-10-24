<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Institutional AI rules and human responsibility for submitted work. -->

# Academic integrity

Follow your institution's rules on AI assistance before using lt-suite.

A course can restrict drafting, answer entry, model-answer access or the entire tool. Graded and invigilated work can have stricter rules.

Disclose AI assistance wherever your institution requires disclosure. A technical capability does not establish permission to use it.

## Human submission

lt-suite prepares drafts and can enter approved answers for review. The suite never submits.

You review the answers, edit them and control any final submission. The suite stops before commit boundaries.

Drafts, citations, diagrams and model answers can contain errors. Check the content against the course and its permitted sources.

## Model answers

Reading Lt's model answer is enabled by default.

Set the following configuration to disable it:

```toml
[model_answer]
read = false
```

Disabling model-answer reading does not establish that another form of AI assistance is allowed.

## Content handling

Use only material you are authorised to access and process.

Keep captures and answers local. Do not publish course content, submitted work, grades or identifying details.

The software licence governs code. Course material and Lt Content retain their own restrictions.

See [safety](safety.md) and [provenance](provenance.md).

## RAT answers

RAT entry uses your own or your team's supplied answers. Check your course's rules before enabling it. Individual RAT pages are never drafted. Only you submit in Lt. See [RAT answers](rat-answers.md).


## Check Answer

Check Answer retries are off by default. Setup asks before enabling them. The loop can discover Lt's key through repeated trials on multi-attempt items. Lt may log each attempt; check your course's rules. The suite excludes single-attempt items, graded items, RAT pages and unknown candidate pages. The user reviews and submits.
