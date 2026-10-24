<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Diagram grammar, rendering checks and executable-case trust. -->

# Diagrams

The diagram skill produces editable draw.io files and SVG, PDF, PNG and HTML outputs.

## Case folders

A case folder supplies `case.json` and Python files such as `content.py`, `layout.py` and `artifact_content.py`.

Those Python files execute code during a build. Inspect a case before running it.

The trust policy restricts case folders to the current directory, configured data directory and shipped examples unless `--trust-case` is supplied.

`--trust-case` grants execution trust. It does not validate the scientific content.

See [security](../SECURITY.md).

## Grammar

The visual grammar uses seven roles: risk, mechanism, anatomy, sign, investigation, diagnosis and treatment.

Four edge relations represent stimulation, inhibition, exclusion and requirement.

Use the relation's line style and endpoint glyph. A T-bar represents inhibition without an additional “inhibits” label.

Name the quantity beside an increase or decrease symbol. Keep every node connected to the mechanism it helps explain.

Containers group related content. Labels, icons and line styles preserve meaning when colours become difficult to distinguish.

The detailed grammar lives in the skill's `references/grammar.md`.

## Defaults

`shared/style.defaults.toml` selects `tol-bright` for light output and print. The dark companion is `petroff-10-on-primer-dark-high-contrast`.

Ranking applies to the derived theme, including fills, strokes and text. Raw accent ranking alone cannot establish the accessibility of rendered roles.

`shared/palettes/ranking.json` records scores, eligibility gates and four weightings. The balanced weighting selects the defaults; a score describes these checks rather than universal accessibility.

Noto Sans and Noto Sans Mono are the default fonts. Atkinson Hyperlegible Next is the low-vision option.

See [fonts and palettes](fonts-and-palettes.md) for measurements and alternatives.

## Why draw.io

draw.io provides an editable text-based format, a free editor and the edge geometry needed by the grammar.

Editable geometry supports later corrections without redrawing the mechanism. Standalone HTML supplies an additional reading and presentation surface.

See [why draw.io](design-notes.md#why-drawio).

## Rendering traps

| Trap | Required check |
|---|---|
| draw.io emits stderr despite exit zero | Check exit status and inspect the output |
| A renderer treats Oklch colours as black | Resolve colours into supported output values |
| Strong accents become large fills | Check text and stroke contrast on derived fills |
| Orthogonal routing ignores supplied waypoints | Inspect the emitted route |
| SVG export crops or offsets the drawing | Compare bounds and placement |
| Rounded-corner units differ | Use `absoluteArcSize=1` where the emitter requires absolute units |
| Several elbows crowd one another | Inspect the route against radius and clearance checks |

## Quality floor

The floor contains scale-free requirements such as readable text, minimum spacing, contrast and zero label overlaps.

Page-size limits come from `diagram.page.max_w` and `diagram.page.max_h`.

Their defaults are 4000 and 3000 pixels. `engine/floor.json` records grammar-derived minimums; `access.py` checks each configured palette.

The shipped toy case exercises the grammar. It does not define a maximum size or scientific benchmark for real cases.

The [worked example](../skills/mechanistic-diagram/examples/toy-case/) contains 11 boxes, four stages, seven roles and four connector kinds. Its SVG inset is self-drawn. Regression references live under `tests/diagram/reference/`.

A successful build still requires visual inspection of the rendered PNG and review of the mechanism.

## Final outputs

Local font embedding is the default for HTML and draw.io output. Google-hosted font links require `fonts.page_fonts = "google"`.

Finalisation uses built-in metadata handling and configured optional cleaner hooks. A cleaner must preserve wording, relationships and geometry.

Run `build.py <case-folder> --final` for final delivery. Without `--final`, build outputs remain drafts. Finalisation uses `ltcore.cleaners` and `[[text_cleaners]]` entries whose stage is `diagram`; the release requires no external cleaner service.

The standalone HTML uses download links for its embedded files. The skill's `references/grammar.md`, `rubric-template.md` and `grade-through.md` document the grammar and review prompts. Set `diagram.rubric_path` to use your own rubric.

Publishing the standalone HTML to an external service requires a separate human decision. The suite never submits the diagram to Lt.
