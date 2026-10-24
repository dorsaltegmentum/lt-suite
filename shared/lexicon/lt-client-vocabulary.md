---
name: lt-client-vocabulary
description: The names in Lt's client-side view model, and the trap attached to each one
review_by: 2027-10-24
evidence: [docs/design-notes.md#view-model-capture, docs/design-notes.md#two-numbering-schemes, docs/design-notes.md#model-answer-policy, docs/design-notes.md#fresh-load-verify]
status: active
---

# Lt's client-side view model

Every read and write path in lt-suite goes through the page's own view model rather than through the DOM, so these names are the working vocabulary of the capture and fill skills. Each entry was measured against a live Lt client, and each carries the mistake that measuring it corrected. The mistake is the useful half, because all of these fail silently.

## Addressing

`lessonFlowPages()` is a plain ordered list. There is no `page.pageIndex` field: a `.find(p => p.pageIndex === n)` never matches, and code that appears to work is working through a positional fallback. Address a page by its position in the flat list and nothing else.

`panelKey` identifies a panel within a page and is not unique across the module. A target is therefore always the pair `(page_index, panel_key)`. Addressing by `panel_key` alone is ambiguous, and addressing by CSS class is worse: a class selector can match the wrong panel on the same page.

`content.subpages[].rows[].cells[].panels[]` is where subpage panels nest. Guessing `subpages[].panels` finds nothing and reports success, so a page with subpages reads as having no unanswered image keys while it has several.

## Item identity

`pluginKey` is the item type. An unmapped `pluginKey` is a per-item skip and never truncates a run: it is a gap in this suite, whose failure mode is a wrong write, and not writing is a complete defence. That is a different thing from a one-way door in Lt, which is crossed by navigating and can only be defended against by not arriving.

`attempts` and `assessment_type` are per item and both are read before writing. Most items are multi-attempt with `instantFeedback` or `noAssessment`, but `graded` items exist, so "this module has no graded item" is checked per item and never assumed.

## Writing

`currentInputModel().addAnswer(...)` is the structured write path for `image-labeling`, `drag-and-drop-labeling` and `categorization`. None of them needs a synthetic drag; simulating one is the route that risks touching submitted work.

`input.attributes.*` is the student's; `content.*` is the author's. Reading the wrong one makes a panel that holds the student's own submitted work read as empty, and empty is what licenses a write. The capture names which is which in `media[].role`.

`labelAnswerItems` has two shapes in one module: nested under `labelAnswerItems`, or an empty collection. "Empty, so unknowable" and "populated but exposing no `labelAnswerItems`, so unmeasured" are different states, and the second is the dangerous one, because counting it returns a false zero.

## Geometry

`presentedRatio` is an aspect ratio, not a scale. It is `naturalHeight / naturalWidth` of the image Lt has stored, and Lt downsamples an upload to a 1920-pixel longest side before computing it. It is predictable from a source file only to within that rounding, so it is read back after a write rather than assumed. Reading it as a scale letterboxes every upload-and-annotate image.

## Answer keys

`modelAnswer` and `correctAnswer` are shipped to the client by the labelling and categories plugins. `module.json` never carries them: the capture filters them out by name, and the `module-has-no-answer-key` rail rejects a capture that contains one. Reading Lt's model answer is a separate, explicit step governed by `model_answer.read` (on by default); its output goes to the run folder as `model-answer.json`, never into `module.json`.
