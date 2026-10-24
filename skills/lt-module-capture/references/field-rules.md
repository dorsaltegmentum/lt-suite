<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# How capture.js reads each field

Each rule below exists because a wrong read once produced a believable value and no error.
A census that looks right proves nothing. A second count made another way does, so
`stamp_capture.py` validates the file and the SKILL recounts from it.

## Pages and boundaries

- **Page kinds come from structure.** The report page owns a `reportInfoPanels` property. A
  Checkpoint page has `committed` and lacks `allPanels`. All other pages are content pages.
  Titles are never compared: "Report", "Review" and "Checkpoint" are words the author chose.
- **Two numbering schemes.** `page_index` counts from 1 through `lessonFlowPages()`.
  `page_no` is the number in `/run/page/<n>` and exists for content pages only; report and
  checkpoint pages get `page_no: null` and no `url`. The `index()` of a checkpoint is not part
  of any URL, so capture builds none for it.
- **Committing a checkpoint removes it from the flow.** After the student commits, Lt drops
  the checkpoint from `lessonFlowPages()` and reveals the pages after it; the page walk then
  sees no door. `root.checkpointPageContainer.pages` still lists it.
  `commit_boundaries.checkpoints` joins both readers and tags each entry with `source`
  (`lessonFlowPages`, `checkpointPageContainer` or `both`). `checkpoint_pages` holds only the
  doors still standing in the flow. The two lists stay separate: a fill halts before a
  standing door, and halting before an opened one would cut the run short for no reason.
  `checkpoints_container_present: false` says the container was unreadable, so an empty list
  then proves nothing.
- **The completion page is inferred and never opened.** `has_completion_page` is read from
  `root.hasCompletionPage`, and `run_level_commit` records the result.

## Panels

- **A panel is answerable when its `panelState` has an `input` key.** No list of
  content-only plugin keys is needed for this.
- **Two panel shapes.** `page.allPanels` yields view-model objects, where `pluginKey` and
  `panelKey` must be called as functions. Inside a subpage a panel is a plain Backbone model:
  the same names are attributes and `panelState` sits under `.attributes`. One accessor reads
  both shapes.
- **Subpage children live under `content.subpages[].rows[].cells[].panels[]`** (also
  `cells[].columns[].panels[]`). The parent page's `allPanels` gains them only after the
  subpage has been displayed. The walk recurses and keeps whichever sighting comes first, so
  each child is counted once and keeps its `parent_item_id`.
- **The allow-list.** Readers exist for `select`, `text-question`, `categories`,
  `table-question`, `kcImageLabelling`, `drag-and-drop` and `image-annotation` (in either
  input mode), plus subpage recursion. `kcImageLabelling` and `drag-and-drop` have one shape
  and one write path, so one reader serves both. Any other answerable plugin is captured as
  `unmapped` with `degrade.reason: unmapped-type` and a `capture_note`: the cost is one TODO
  item, and the rest of the module is still captured.
- **`notebook` is left out of the allow-list deliberately.** That panel is the student's
  private notebook, and no lt-suite skill writes there. It is captured as `unmapped`, its
  degrade is permanent (`notebook-never-written`), and its input stays unread.
- **Panels without input are content.** `kcTextArea` keeps its text in `content.theText`.
  `image`, author `table`, `divider`, `whitespace`, `button`, `video` and `audio` are
  recorded by kind. Author text that matches `capture.drop_text_patterns` is dropped. A
  `hotspot` panel keeps its popups (`labelTitle`, `labelDescription`, and `position` parsed
  from a JSON string) and its base image. For a plugin with no reader, only the NAMES of its
  content keys are kept, never their values.

## Answered tests

Each `answered` test asks whether some cell the student could fill is non-empty. It never
relies on truthiness and never counts a derived collection. A false "unanswered" would allow
a fill to overwrite the student's work, so every shape that has not been measured counts as
answered.

- `select`: `input.selectedAnswers` holds option positions starting at 0. The test counts
  those positions; resolved option text only serves display.
- `image-annotation`: the test depends on `inputMode`. With `allowAnnotation`, annotations
  are the answer. With `allowImageAndAnnotation`, an upload without annotations already
  counts: `answered = annotations.length > 0 || input.imageKey !== ''`.
- Labelling panels: a link in `input.linkedLabels` with both indices set counts, and 0 is a
  valid index.
- `categories`: placements inside `labelAnswerItems` are counted. When some or all entries of
  the answer collection lack that key, the shape is unmeasured and its length is used. The
  note records `shape=` and the key that held the text (`text_keys=`).
- `table-question`: see Tables.

## Tables

- Rows come in two shapes: with `cells`, or with `headerRow` and `columns`. A reader that
  only looks at `cells` gets an empty grid.
- Header rows and header columns were written by the author. They go to
  `constraints.column_headers` and `row_headers` and never count as answers.
- **The author's grid settles it.** `inp.rows`, the grid the student sees, also holds labels
  the author typed into normal body cells, which Lt does not mark as headers.
  `content.rows` is the author's version. Cells that match it exactly get an `a` in
  `answered_evidence.filled_map` and are not counted; their text is copied to
  `constraints.authored_cells` with full-grid `row` and `col`. Every other non-empty cell is
  `#`, also when the student edited a pre-filled cell. `author_grid.status` tells whether
  the comparison happened: `read`, or one of `absent`, `same-object`, `shape-mismatch`, in
  which case every non-empty body cell counts.
- With no author grid, two heuristics can add `answered_suspect` and the basis for it: a
  single filled label column, or authored text forming an L along the top row and left
  column. They only flag; `answered` keeps its value.

## Images

- `content.*` describes the author's image and `input.*` the student's upload. Upload
  panels (`allowImageAndAnnotation`) take `presented_ratio` and `image_key` from `input`,
  because their target is the learner's slot. Annotate-only panels (`allowAnnotation`) take
  both from `content`.
  `media[].role` names the image each entry refers to.
- `presented_ratio` = `naturalHeight / naturalWidth` of the image Lt stored. Screen size plays
  no part in it. An empty slot yields `null` (a 1 would be a guess) and the item becomes
  `degrade-todo` with `geometry-unknown-until-presented`. Lt shrinks each upload to 1920 px on
  its longest side, so the fill step reads the ratio back after saving an image.
- Labelling and categorisation panels list their label pool in **sorted** order. The source
  order might line up with Lt's answer key; sorting keeps the words and drops that order.

## Flags, never decisions

- `rat_candidate` marks the one page on which every item is multiple-choice. Team and
  individual readiness tests look identical in the view model, so the verdict belongs in
  `page-kinds.json` and never in the capture. The flag is a hint; the shared classifier also inspects unflagged candidates.
- `mirror_of` marks a panel on a later page that repeats an earlier panel's type, prompt and
  constraints. `basis` lists the evidence: equal answered evidence, plus `read_only` null on
  the copy and a boolean on the original. Team-based modules show earlier tables again in
  this way, and those copies are not writable. `read_only` keeps its measured value.
- `read_only` has three values. `true` and `false` were measured; `null` says the panel
  exposed no such flag, and every consumer treats `null` as unknown and does not write.
