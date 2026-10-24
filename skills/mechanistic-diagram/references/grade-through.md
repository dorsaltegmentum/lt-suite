<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Grade-through: five lenses, then verification

A clean build proves the layout rules hold. It says nothing about whether the diagram is
right, complete or readable. The grade-through checks those with five independent readers
(lenses), then one sceptic per lens that tries to refute each finding. Only findings that
survive get fixed.

## Prepare

```bash
python3 "$SKILL_DIR/engine/grade_prep.py" <case_dir> <prep_dir>
```

`<prep_dir>` then holds `diagram-dump.md` (every box with its rendered line breaks, every
connector with its kind), `full.png` (the print palette at full size), `overview-half.png`,
`overview-<palette>-half.png` for the other palettes, and twelve overlapping full-resolution
tiles `tile-r{0..2}c{0..3}.png`.

## Run

Run each lens as an independent reader with a fresh context: a subagent where the agent
has them, otherwise one lens after another, each starting from this file and the materials
only. A lens never sees another lens's findings. Then run one verifier per lens with that
lens's findings.

Every reader gets the shared context below, with the placeholders filled in, followed by its
lens prompt. Readers that cannot view images get `diagram-dump.md` only and say so in their
summary; the visual lens then needs a person to look at the tiles.

### Shared context

```text
You are reviewing a finished mechanism diagram for case <CASE NAME> (<one line: patient and
diagnosis>). It will be graded against the rubric at <RUBRIC PATH> (default
references/rubric-template.md). Every criterion must reach the rubric's passing level; the
top level is the aim.

Materials (local files only; read what your lens needs):
- The rubric: <RUBRIC PATH>
- The diagram grammar: references/grammar.md
- The case material the diagram rests on: <CASE SOURCES> (case.json "sources"). The case
  data is the source of truth for every number and finding.
- Every box and connector: <PREP>/diagram-dump.md
- The diagram's source text: <CASE DIR>/content.py
- Renders: <PREP>/overview-half.png, <PREP>/full.png, the tiles <PREP>/tile-r{0..2}c{0..3}.png
  and the other palettes' overviews. Crop further into your own scratch folder
  <PREP>/work-<lens>/ when you need to zoom.

Rules:
- Read-only: write nothing outside <PREP>/work-<lens>/.
- Local files only: no web search, no upload, no connector. Send no case content anywhere.
- Choices the author has settled, not to re-open: <SETTLED>. A real defect inside one of
  them (a clipped glyph, a misaligned connector) may still be reported.
- Known risk: <DENSITY NOTE>. Cutting or merging boxes is a fair suggestion only when no
  finding, learning outcome or causal step is lost.
- Every fix must be concrete: the exact new box text (keep it about as long as its
  neighbours), the exact connector to add or remove with its kind, or a precise layout move.
  Use the spelling of <LOCALE> and the case's own terms and numbers.
```

### Lens 1: accuracy

```text
Check the medical accuracy of every step and every connector (rubric criterion 1).
Go through every box in diagram-dump.md and check each claim against the case data and
standard medical science. Then go through every connector: stim must be a real cause or
leads-to relation in that direction with no hidden leap; inh must be a treatment that
really blocks its target; excl must be evidence that really argues against the diagnosis
it points at; req must be normal anatomy or physiology the target really depends on. Check
numbers, units, reference ranges and drug names, doses and schedules against the case
exactly. Report only real errors, misleading simplifications, wrong kinds or directions,
and causal leaps.
```

### Lens 2: coverage

```text
Check content coverage (rubric criterion 2). First list every positive finding and every
pertinent negative in the case (history, examination, investigations, management) and every
learning outcome the case material names. Map each to the boxes that carry it. Report:
findings or outcomes missing or misrepresented; boxes the case does not support; boxes that
add nothing (candidates to cut or merge); chains that stop short (a finding with no mechanism
leading to it, a mechanism with no finding, a diagnosis without its evidence, a treatment
without its target). Give the mapping only as a count in your summary ("41 of 43 findings
mapped").
```

### Lens 3: visual organisation

```text
Check organisation and clarity (rubric criterion 3) by looking. Open overview-half.png
first, then every tile at full resolution, then the other palettes' overviews. Judge as a
grader opening the PDF on a laptop and zooming: reading order, whether each stage reads as
a chain, whether every connector can be followed without ambiguity (junctions, shared
trunks, lines running close beside unrelated boxes or container edges, heads that are hard
to see, lines behind text), alignment, even spacing, empty or cramped regions, whether the
key steps stand out, and whether each added layer (stage numbers, time course, glyphs, range
bars, connector labels, the illustration) helps or clutters. Name each defect by box ids and
approximate pixel position in full.png. Fixes must be precise (move X to column C row R,
route a->b through ..., shorten Z to "...").
```

### Lens 4: copy-edit

```text
Proofread every string on the page: boxes, stage titles, key, header. Check spelling for
<LOCALE>, grammar, typos, sentence case in boxes, consistent punctuation, parallel phrasing
across sibling boxes, abbreviations (standard, or spelled out somewhere on the page), units
and number formatting (µmol/L, °C, en dashes in ranges), rendered line breaks (a lone short
word on the last line, a number split from its unit, a word split across lines), and one
term used one way across the page. Use diagram-dump.md for text and line breaks and the
tiles for the render. Report only real issues.
```

### Lens 5: strict grader

```text
You are a strict grader whose job is to find reasons not to award the top level. Read the
rubric and the case, then the diagram (overview, tiles, dump). For each criterion, state the
level you would award and exactly what stops the top level. Consider: does it read as
mechanism (chains from cause to finding) rather than a list; is the underlying science joined
to the clinical picture; is the time course clear; are the alternative diagnoses handled with
evidence; is each treatment linked to the step it acts on; is anything wrong, overstated or
unsupported; is density a readability problem, and if so which boxes or connectors you would
cut; is anything a grader would expect for this case missing. Give the smallest set of changes
that makes the top level unambiguous.
```

### Output of each lens

```json
{
  "grades": {"accuracy": "<level and one-line reason>", "content": "<level and reason>",
             "overall": "<level and reason>"},
  "findings": [{"id": "<lens>-<n>", "severity": "must | should | polish", "where": "<box ids, a->b, or region>",
                "problem": "...", "evidence": "<case quote, reasoning, or what the image shows>", "fix": "<the exact change>"}],
  "summary": "<three sentences at most>"
}
```

`must`: a grader would mark it down, or it is factually wrong. `should`: clearly better.
`polish`: a small finish.

### Verifier

```text
You are an adversarial verifier. Another reader (lens: <LENS>) produced the findings below.
For each finding, try hard to refute it: check the case text, standard medical knowledge, the
actual render (look at the tiles or crops, not only the dump) and the rubric. A finding
survives only if the problem is real and its fix is correct, consistent with the case,
applicable, and makes nothing worse (more density, a broken chain, a new contradiction, a
longer box that crowds its neighbours, a settled choice re-opened). If the problem is real but
the fix is flawed or too long, return "modified" with a corrected fix. If you cannot
substantiate the problem, return "refuted". One verdict per finding id:
{"verdicts": [{"id": "...", "verdict": "confirmed | modified | refuted",
               "reason": "<what you checked and found>", "final_fix": "<the fix to apply; empty if refuted>"}]}

FINDINGS (JSON): <the lens's findings>
```

## After the run

Collect every finding whose verdict is `confirmed` or `modified`, with its final fix. Apply
the `must` fixes, rebuild, and look again. Report one clause per criterion on what would lift
it to the top level.
