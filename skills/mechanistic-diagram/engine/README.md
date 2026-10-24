<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Mechanistic diagram engine

The engine builds checked diagrams, editable masters and an interactive HTML page from a case folder.
`../SKILL.md` describes the diagram workflow.

`ENGINE` means the absolute path of the `engine/` folder beside the skill's `SKILL.md`.
Scripts that load configuration accept `--config PATH`.
Otherwise, lt-suite checks `LT_SUITE_CONFIG`, `./lt-suite.toml`, the platform config file, then built-in defaults.

## Build

```sh
python3 ENGINE/build.py <case_dir>
python3 ENGINE/build.py <case_dir> --final
```

The shipped example is `examples/toy-case/`, beside `engine/`.
“Fictional enzyme X deficiency” has 11 boxes, 4 stages, all 7 roles and all 4 arrow kinds.
It includes 1 self-drawn, themed SVG inset.
From `engine/`, build it with:

```sh
python3 ENGINE/build.py ../examples/toy-case
```

**Case folders run Python.**
The engine imports `content.py`, `layout.py`, `artifact_content.py`, `variants.py` and `layout_<variant>.py`.
It trusts only the lt-suite data folder and the skill's own `examples/` folder by location.
Other folders require `--trust-case`, which prints every Python file's sha256 and records approval in `<data>/state/trusted-cases.json`.
A changed, added or removed Python file requires fresh approval. The refusal names the files that would run.
Only build case folders you wrote or trust.

The build writes outputs into `<case_dir>`.
It also uses temporary files and the lt-suite caches.
When `TMPDIR` is set, it writes `$TMPDIR/verify.js`.

Every export uses each palette in `style.palettes`.
The print palette, `style.print_palette`, supplies the submission copy and filenames without a palette suffix.

| File | Contents |
|---|---|
| `geometry.json` | Boxes, containers, key and routes shared by both drawing writers |
| `final-<palette>.{svg,pdf,png}` | Each configured palette; one-page PDF and 1.5x PNG with rounded transparent corners |
| `<slug>.drawio` | Editable master in the print palette |
| `<slug>-<palette>.drawio` | Editable master for each other palette |
| `deliver/<slug>-<palette>.pdf` | PDF for every palette |
| `deliver/<slug>.pdf` | Copy of the print palette's PDF |
| `deliver/<slug>.png` | PNG in the print palette |
| `deliver/<slug>-<palette>.png` | PNG for each other palette |
| `deliver/*.drawio` | Copies of all editable masters |
| `<artifact_content.OUT_NAME>` | Standalone Artifact page, once `artifact_content.py` is complete |
| `checks/cvd/final-<palette>-{protan,deutan,tritan,grey}.png` | Colour-vision simulations and greyscale renders |
| `checks/diff-overlay.png` | Changes since `accepted/`, when a snapshot exists |
| `checks/final-report.json` | Cleaning report from `--final` |
| `consistency-report.md` | Source checks, when `case.json` has `sources` |

The build stops on preflight gaps, layout problems, crossings, unrouted edges, extra PDF pages, contrast failures or floor failures.
Without `--final`, all outputs remain drafts.
Run regression comparisons on drafts when checking output bytes.

## Gates

`build.py` prints the check results before stopping on a gate failure.
Warnings remain visible in a failing build.

| Check | Tool | Stops the build | Threshold |
|---|---|---|---|
| Layout | `layout_engine.py` | Any PROBLEM, crossing or unrouted edge | See “What the checks enforce” |
| Contrast | `access.py` | Any unrecorded or worsened WCAG shortfall | Text 4.5:1; large text 3:1 at 24 px, or 18.66 px bold; arrows 3:1 against the page and container fill |
| Floor | `floor.py` | Any FAIL row | Grammar minimums in `floor.json` and configured page limits |
| Widows | `textrules.py` | Warning only | Last line is a lone number, one word of 5 characters or fewer, or narrower than 40 % of the widest line |
| Treatment | `guideline.py` | Warning only | Every treatment box has a ledger entry matching its current text and a verdict |
| Facts | `consistency.py` | Warning only | Values, measurements, percentages, doses and drug names appear in `case.json` sources |
| Changes | `diffviz.py` | Summary only | Changes since `<case_dir>/accepted/`, when present |

`engine/floor.json` holds scale-free quality minimums from the diagram grammar and accessibility checks.
Its `source` is `"grammar"`.
`diagram.floor_path` can select another floor file.
Edit the floor by hand and keep `tests/diagram/test_floor.py` passing.

| Row | Limit | Purpose |
|---|---|---|
| Smallest text | At least 11 px; box text at least 12 px | Minimums in `floor.json` |
| Layout problems, crossings, unrouted edges | 0 each | Complete, unambiguous routes |
| Label overlaps | 0 | Clear text |
| Box gap | At least 10 px | Separate boxes |
| Edge clearance | At least 4 px from unrelated boxes | Separate routes from boxes |
| Gutters | 28 px, tolerance 0.5 px, including the key | Equal container gaps |
| PDF pages | 1 for every `final-*.pdf` and `deliver/*.pdf` | One-page exports |
| Box aspect | Width:height at least 1.9 for boxes with 2 or more lines, excluding bars | Minimum in `floor.json`; `core.ASPECT_FLOOR` keeps layout sizing above it |
| Box count | Warning above 80 | Crowding warning |
| Corner radii | Boxes 7 px; containers 12 px | Consistent rounding |
| Images | Rounded corners in SVGs, checked masters, delivery copies and the Artifact page | Clip paths, rounded image data or CSS must round the image itself |
| Contrast | `access.py` passes | WCAG text and graphics contrast |
| draw.io verification | Current `verify-result.json`: `fontOk`, `edgesBad = 0`, `labelsBad = 0` | Missing or stale results warn; failed checks fail |

Page limits come from `diagram.page.max_w` and `diagram.page.max_h`.
Their defaults are 4000 and 3000 px.

Image clipping must use a rounded rectangle inside the image.
Rounded source data, transparent bitmap corners, draw.io `clipPath=inset(… round …)` and CSS `border-radius` also qualify.
Transparent-corner glyphs no larger than 40 px in either dimension need no extra rounding.
Displayed picture radii outside 5 to 14 px warn.

The shipped floor has no `contrast_exceptions`, so every WCAG shortfall fails.
A configured exception must name the palette, foreground, background, minimum ratio and minimum text size.
`access.py --strict` also fails recorded exceptions.

## Final outputs (`--final`)

`--final` runs only after a build passes every gate.
`finalize.py` uses `ltcore.cleaners` to remove metadata from every PDF in `deliver/`.
It uses exiftool, then qpdf, with pikepdf as the fallback.
Cleaning removes XMP and `/Info` and sets a content-derived `/ID`.
The cleaned PDF therefore depends on its draft.

The engine then runs optional `[[text_cleaners]]` hooks with stage `"diagram"`.
No external cleaner is required.
It rebuilds the Artifact page with cleaned downloads, then cleans the page.

The build checks every cleaned PDF for 1 page and unchanged rendering at 30 dpi.
It also checks every PNG for unchanged pixels.
Results go into `checks/final-report.json`.
A later draft build replaces `deliver/` with drafts again.

## Reproducible builds

`build.py` sets `SOURCE_DATE_EPOCH` unless the environment already defines it.
It uses the newest source modification time from `case.json`, case Python files and files named inside `case.json`.
Referenced files count only when they lie inside the case folder.

Generated recolours, outputs and `checks/` do not affect the epoch.
Scratch `*.draft.py` files do not count.
Variant files count only for that variant's build.

Cairo uses the epoch for PDF CreationDate.
The masters and their delivery copies receive the same modification time.
The Artifact page's zipped masters therefore carry fixed timestamps.
Static font instances stay in the font cache.

With unchanged sources, configuration, fonts and tools, repeated builds produce byte-identical outputs.
Preserve source modification times when copying a case for comparison.
`--final` removes PDF timestamps.

## Starting a case

```sh
python3 ENGINE/new_case.py <case_dir>
```

`new_case.py` writes `case.json`, `content.py`, `layout.py` and `artifact_content.py`.
It marks unfinished values with `TODO` and refuses to overwrite existing case files.
Skeleton comments explain `POS`, `SPAN`, `KEY_SPAN`, `GROUPS` and `HINT`.

The slug comes from `diagram.naming.case_slug_pattern`.
The page eyebrow comes from `diagram.naming.eyebrow_template`.
`--n N` supplies the case number used by those templates.
Otherwise, the scaffold looks for a number in the folder name.

Header requirements come from `diagram.header.required_fields`, default `["title"]`.
A filename-safe `slug` is always required.
`group` and `members` are optional unless configured as required.
Surname ordering applies only when `diagram.header.members_order = "surname"`.

Use `examples/toy-case/` as the worked example:

1. Write `content.py`: stages in `CLUSTERS`, nodes through `n(id, role, text, stage, bold=False)` and edges through `e(a, b, kind)`. Use `PAIRS` for RULED OUT blocks. Add bind rules and `ITALIC = auto_italic(NODES)` where needed.
2. Place boxes in `layout.py`. Run `python3 ENGINE/layout_engine.py <case_dir>` until it reports `UNROUTED: []`, `PROBLEMS (0)` and `CROSSINGS (0)`.
3. Complete `case.json`, including source paths, then run `python3 ENGINE/build.py <case_dir>`.
4. Complete `artifact_content.py`. The build skips the page while required page fields contain `TODO`.
5. Check treatment boxes against the treatment guideline. Record results in the configured ledger, default `guideline-ledger.json`; `guideline.py` describes its format.
6. Optionally, run `verify.js` in an app.diagrams.net tab with any browser tool that can evaluate JavaScript. Check every connector and label line count. Save the returned object as `verify-result.json`, with `drawio_sha256` set to the checked master's SHA-256.
7. Save a comparison snapshot with `python3 ENGINE/diffviz.py <case_dir> --accept`. Later builds report changes and write `checks/diff-overlay.png`.

`opt.py` can suggest a starting grid for 1 stage through simulated annealing.
It runs separately from the build.
From a script that loads a trusted case:

```python
import sys
sys.path.insert(0, "ENGINE")
from case import Case
from opt import Problem

case = Case("ENGINE/../examples/toy-case")
C = case.content
members = [nid for nid, node in C.NODES.items() if node["cluster"] == "mech"]
edges = [
    (edge["a"], edge["b"], 3.0 if edge["kind"] == "inh" else 1.0)
    for edge in C.EDGES
    if edge["a"] in members and edge["b"] in members
]
problem = Problem(4, 4, members, edges)
score, positions = problem.anneal(iters=20000, seed=0)
print(problem.show(positions))
```

## Architecture: engine and case

**Engine** modules serve every case:

| Module | Purpose |
|---|---|
| `build.py` | Preflight, layout, render and masters for each palette, delivery copies, Artifact page, checks and optional cleaning |
| `case.py` | Trusted case loading, variants and preflight messages |
| `casekit.py` | `NBSP`, bind rules, `binder()`, `UNIT_RULE`, `PLUS_RULE`, `keep_together()` and the `Content` builder |
| `core.py` | PIL text measurement, wrapping, proportions, orthogonal routing, checks, SVG pieces and italic runs through `set_italic`, `runs` and `rich` |
| `layout_engine.py` | Grid maths, even spacing, `COL_EXTRA`/`ROW_EXTRA`, pair blocks, spans, gutters, padding, routing hints, checks and `geometry.json` |
| `render.py` | Headers, key and SVG rendering to PDF/PNG through rsvg-convert |
| `drawio.py` | Editable masters with containers, attached connectors and bend points |
| `style.py`, `themes.py` | Configuration, derived palettes, fonts, role names, edge styles and glyph colours |
| `fontset.py` | Static font instances, private fontconfig and the fonts embedded in the Artifact page |
| `mkverify.py` | `verify.js`, checked by draw.io's own layout engine |
| `make_artifact.py` | Standalone HTML, CSS, downloads, figure data and palette switch |
| `artifact_logic.js`, `artifact_ui.js` | Present, Trace and Quiz logic and DOM controls |
| `anki_cards.py` | Optional arrow-based study cards in `deliver/<slug>-cards.tsv` |
| `autoplace.py` | Draft full-case or variant layouts |
| `variant_build.py` | Separate outputs for a selected subset of the content |
| `decor.py`, `icons.py` | Stage numbers, glyphs, timeline, role badges, flags, range bars and inset panels |
| `recolour.py` | Illustration recolouring, cropping, rounded corners and optional vector tracing |
| `tools/extract_icons.py` (repository) | Icon paths from an installed Nerd Font (`--font` or `NERD_FONT`), using Material Design Icons names or other set prefixes |
| `floor.py`, `floor.json` | Grammar minimums and configured page limits |
| `access.py` | Contrast for every configured palette, rendered text, colour-vision simulations and arrow cues |
| `textrules.py` | `auto_italic()`, `unit_rules()`, `range_rules()` and widow checks |
| `diffviz.py` | Snapshots, change summaries and overlays |
| `consistency.py` | Source checks using synonyms from `institution.locale`: `en`, `en-AU`, `en-GB` or `en-US` |
| `guideline.py` | Treatment text against the configured guideline ledger |
| `finalize.py` | Metadata cleaning, deterministic PDFs and configured cleaner hooks |
| `opt.py` | Optional annealing placer |
| `new_case.py` | Case scaffold |
| `regress.py` | Build comparisons and reference manifests |
| `tests/diagram/` | Repository tests for text, floor, access, sources, ledgers, snapshots, pages and regression |

**Case folder** files supply content and executable Python:

| File | Contents |
|---|---|
| `case.json` | Required `slug` and configured header fields. Optional `subtitle`, `group`, `members`, `aria_label`, `diagram_name`, `verify_probe`, `key`, `sources`, `stages`, `timeline`, `role_icons`, `glyphs` and `inset` |
| `content.py` | Ordered `CLUSTERS`, nodes, edges, bind rules, `ITALIC`, `PAIRS` and optional `ROLE_NAME` |
| `layout.py` | `POS`, `SPAN`, `KEY_SPAN`, `GROUPS`, `GROUPS_ABOVE_ROW`, `HINT`, `W_MAX`; optional `INSET_SPAN`, `GUTTER_COLS`, `GUTTER_ROWS`, `MIN_H`, `NUDGE_X` and spacing tunables |
| `artifact_content.py` | `OUT_NAME`, `STORAGE_KEY`, `PAGE_TITLE`, `EYEBROW`, `HEADING`, `LEDE`, `FIGCAPTION`, `FINDINGS`, `SOURCES`; optional `STAGE_NOTES` |
| `variants.py`, `layout_<variant>.py` | Content subsets and their layouts |
| `assets/` | Source illustrations and cached `<stem>-<palette>.svg/.png` recolours with JSON sidecars |
| `guideline-ledger.json` | Default treatment ledger; override with `diagram.treatment_check.ledger_name` |
| `accepted/` | Snapshot geometry and PNGs from `diffviz.py --accept` |
| `verify-result.json` | Optional draw.io verification result |

`key` can override role entries and arrow labels through `{"roles": [...], "arrows": [[label, kind], ...]}`.
`sources` holds `[{"label": ..., "path": ...}]`.
Source paths may be absolute, home-relative or relative to the case folder.
`LEDE` and `FIGCAPTION` contain HTML.
`STAGE_NOTES` maps stage IDs to Present-mode notes.

Layout defaults include `GAP_X = 28`, `X0 = 60`, `Y0 = 176`, `ROW_GAP = 26`, `PAD = 18`, `GUTTER = 28` and `PAIR_GAP = 56`.
Absent gutter rows and columns derive from span boundaries.
Routing hints include `"trunk"`, `("gutter", a, b)` and `("mid", a, b)`.

Case modules can import `casekit` and `textrules` through the engine's `sys.path`.
Loading case modules leaves no `__pycache__`.
Shared drawing rules live in the engine.
Run regression checks after engine changes.

## Drawing conventions

- **Configured palettes.** `ltcore.palettes` derives role fills, strokes and text at run time in Oklch at each role's hue. It checks WCAG contrast, colour-vision separation and greyscale separation. Failed palettes name the failing pair. Some catalog palettes have hand-tuned overrides. `style.custom_palettes_file` supplies custom palettes.
- **Configured fonts.** Box text uses `fonts.sans`, measured at weights 500 and 700. Headers use bold `fonts.mono`; the title is 30 px. `fonts.text_px` sets box text size, default 14.
- **Pill ends.** Open lines and dashes have round caps. This covers connectors, timeline lines, leaders, range bars, T-bars and dashed anatomy borders. `core.round_dash` adjusts dash lengths for cap width. Masters set `linecap=round;linejoin=round`.
- **Rounded arrowheads.** `core.arrow_head_d` uses rounded triangle corners through `ARROW_ROUND`. `core.end_gap` stops arrow tips 6 px short and T-bars 7 px short, with 0.35 px extra for bold boxes. The painted result leaves about 5 px clear.
- **Master attachment.** `drawio.entry_back` moves fixed entry points through `entryDx`/`entryDy` and `entryPerimeter=0`. Verification allows 0.6 px route-end tolerance.
- **Four arrow kinds.** `stim` uses a solid arrow; `inh` uses a solid T-bar; `excl` uses a dotted T-bar; `req` uses a dashed arrow.
- **Secondary text.** Arrow labels and illustration credits use the theme's `label` colour at 4.5:1 contrast.
- **Box proportions.** Multiline boxes widen to `core.ASPECT_FLOOR` when range bars add height.
- **Inset spacing.** The title must clear the image. An overlap reports `inset title runs into the image` as a PROBLEM. Credits come from `case.json` or `diagram.illustration.credit`.

The toy case uses stages `cause` (CAUSE), `mech` (MECHANISM), `find` (FINDINGS AND DIAGNOSIS) and `care` (TREATMENT).
Its inset is `assets/clearance.svg`, titled CLEARANCE, with mode `"themed"`.
A laboratory node can use `"Substrate S 48 µmol/L (normal 2–10)"` with `lab=[dict(value=48, lo=2, hi=10, at="48")]`.
An italic term can be `"X. fictus"`.

`recolour.py` accepts `--palette ID`, alias `--flavour`, and `--config PATH`.
It crops logos unless `--keep-logo` is set; `--crop` supplies explicit bounds.
`--mode vector` uses potrace; `--anchor` preserves a small focal colour.
Raster recolouring needs optional `opencv-python-headless` and `scikit-learn` dependencies.
A themed SVG inset needs neither.

`--bg` accepts `canvas`, `cl_fill`, `cl_stroke`, `cl_text`, `key_fill`, `key_stroke`, `ink`, `muted` and `label`.
Aliases are `base = key_fill`, `mantle = cl_fill`, `text = ink` and `subtext = muted`.
It also accepts `crust`, `<role>.fill`, `<role>.stroke` and the 14 named accents in `themes.ACCENT_NAMES`.

## The Artifact page

`make_artifact.py` writes a complete standalone HTML document with doctype, charset, viewport and title.
Read mode shows the static figure.
Present steps through `case.json` stages, hides or fades later stages and offers Zoom to stage.
Trace follows upstream causes and downstream consequences with a depth limit and chain panel.
Quiz covers all boxes or 1 stage and reveals answers.

The palette switch lists the configured palettes.
Auto follows the page theme.
Downloads use plain `<a download>` links with data URIs.
They include 1 PDF per palette and a ZIP containing every master.

SVG hooks connect interactive layers to geometry.
`data-node` identifies boxes, `data-a`/`data-b` identify edge ends and `data-stage` identifies stages.

Repository tests use a cut-down toy page at `tests/diagram/fixtures/toy-case-page.html`.
`tests/diagram/make_page_fixture.py` creates it from a built page.
Run page checks from the lt-suite repository:

```sh
python3 tests/diagram/check_artifact.py <page.html>
node --test tests/diagram/artifact_logic.test.js tests/diagram/artifact_ui.test.js
```

**Study cards:** `python3 ENGINE/anki_cards.py <case_dir>` writes `deliver/<slug>-cards.tsv` on request.
Cards cover causes, diagnosis, treatment and ruled-out links.
`diagram.anki.tag_prefix` sets the tag prefix, default `"Diagram::"`.
The script imports no cards.

## Drafting a layout

```sh
python3 ENGINE/autoplace.py <case_dir>
cp <case_dir>/layout.draft.py <case_dir>/layout.py
python3 ENGINE/build.py <case_dir>
```

Review an existing `layout.py` before replacing it.
`autoplace.py` defaults to `layout.draft.py` and never overwrites an existing case layout.
It drafts stage containers, box cells, key and inset panels, diagnosis bars, RULED OUT blocks, groups, widths and routing hints.
The draft's docstring lists remaining problems.
Inspect the figure, move boxes and rerun `layout_engine.py`.

`--seed N` changes the search.
`--effort N` changes search effort.
`diagram.page.max_w` sets the page width limit.
`diagram.layout.workers = "auto"` uses forked workers on POSIX and serial search elsewhere.
`"serial"` forces serial search; a number sets the worker count.

## Variants

A variant keeps a subset of `content.py`.
For `core`, `variants.py` defines `CORE` or `CORE_DROP`.
`BRIDGES` reconnect chains through removed boxes; `META` overrides case metadata.
`layout_core.py` supplies its grid.

```sh
python3 ENGINE/variant_build.py <case_dir> --variant core --autoplace
```

`--autoplace` drafts a missing variant layout.
Outputs include `geometry-core.json`, `final-core-<palette>.{svg,pdf,png}` and masters and delivery files under `<slug>-core`.
`META` can override the variant slug.
The full case keeps its outputs.
Variants produce drafts and have no Artifact page.

## What the checks enforce

Preflight in `case.py` checks these rules before layout:

- `content.py` has nodes and stages. Every node names a known stage and role. Every edge joins known nodes with a known kind.
- `layout.py` gives every node exactly 1 `POS`, every stage a `SPAN`, and the key a `KEY_SPAN`. Each `HINT` names an existing edge.
- `case.json` contains required fields and no unfinished header placeholders. The slug is filename-safe. Configured surname ordering applies to members.

Layout failures stop the build:

- Boxes stay at least 10 px apart and inside their own containers. Containers stay at least 8 px apart.
- Boxes respect `PAD` and the title clearance `CL_TOP`. Neighbouring containers, including the key, stay exactly `GUTTER` apart.
- Routes remain orthogonal and clear boxes by 4 px. Routes cannot run within 8 px along container borders.
- Unrelated edges cannot overlap or form false continuations at corners. Crossings and unrouted edges must both equal 0.
- Neighbours in a stage row keep at least `GAP_X - 4` spacing. Text fits inside its box.
- Boxes and routes cannot intrude into the key or inset. Badges, edge labels, inset titles and credits must clear nearby content.
- Shared fan-out trunks and routes through stage title bands can warn.

Render and master checks enforce configured fonts, fitting key text and clear header text.
Every PDF has 1 page.
`xmllint` checks master XML.
The Artifact page requires complete page fields.
The remaining gates then run.

## The check tools

Tools run separately or through the build.
Case-loading tools execute trusted case Python.
Other writes are listed below.

`floor.py <case_dir>` reads build outputs and exits 1 on a breach.
It recomputes geometry-only layout checks without importing case Python.
`--floor PATH` overrides the configured floor.
A geometry node's `bar` field marks spanning bars; older geometry falls back to a 300 px width threshold.
Edit `floor.json` by hand and keep `tests/diagram/test_floor.py` passing.

`access.py <case_dir> [--no-sims] [--strict]` checks every configured palette and rendered text run.
It measures foreground contrast against rectangles underneath and writes `checks/cvd/`.
Simulations use severity 1.0 in linear sRGB for protan, deutan and tritan vision, plus greyscale.
The report calls pairs collapsed below deltaE_OK 0.05 and indistinguishable below 0.02.
Dash patterns and head shapes provide additional arrow cues.

`textrules.py <case_dir>` reports italic terms, unit rules, range rules and widows without editing content.

`diffviz.py <case_dir> [--accept [--from DIR]] [--no-image]` compares or saves `accepted/`.
It writes the snapshot or difference overlay when requested.

`consistency.py <case_dir>` writes `consistency-report.md`.
Facts receive FOUND, WEAK or MISSING status with the closest source match.
WEAK means the number appears without its nearby label or unit.
Synonym lists follow `institution.locale`: `en`, `en-AU`, `en-GB` or `en-US`.

`guideline.py <case_dir>` reads the ledger selected by `diagram.treatment_check.ledger_name`, default `guideline-ledger.json`.
Messages use `diagram.treatment_check.provider_label`, default `"treatment guideline"`.
It reports missing entries, stale text, absent or unknown verdicts and orphan entries.
Text hashes use the first 12 SHA-1 hex digits after replacing no-break spaces with plain spaces.

`finalize.py FILE_OR_DIR ...` cleans final outputs.
`--dry-run` reports without changing files.

`regress.py` compares builds or checks a manifest, as described below.

## Fonts

`ltcore.fonts` resolves bundled defaults, installed fonts and fonts fetched by the setup skill.
Text uses the configured sans, `fonts.sans`, at weights 500 and 700.
Headers use the configured mono, `fonts.mono`, in bold.
`fonts.text_px` defaults to 14.

Variable fonts become static instances under `<cache>/font-cache/`, renamed `<Family>Static`.
rsvg-convert reads these through a private `FONTCONFIG_FILE`.
This avoids cairo embedding the variable font's default weight for non-ASCII runs.
Static font files remain unchanged.

With `fonts.page_fonts = "local"`, the default, the Artifact page embeds the faces it shows as `@font-face` data and makes no network request; the masters name the font families, and draw.io uses the fonts installed on the machine.
Setting `"google"` makes the page and the masters link Google Fonts instead.

## Regression

The repository reference is `tests/diagram/reference/toy-case.manifest.json`.
`tests/diagram/test_regress.py` rebuilds the toy case in a temporary folder and checks that manifest.

```sh
python3 ENGINE/regress.py <ref_dir> <new_dir>
python3 ENGINE/regress.py <build_dir> --write-manifest FILE
python3 ENGINE/regress.py <build_dir> --manifest FILE
```

Two-folder comparison reads both builds.
It checks SVGs after setting aside inert `<g>` wrappers and compares geometry as bytes or JSON.
PNGs must match pixel for pixel.
PDFs must have 1 page and identical rendering at 40 dpi.
Allowed PDF byte differences cover CreationDate, embedded-font timestamps and checksums, and resulting stream or object offsets.
Masters must match apart from the `mxfile` agent string.
`--ref-html FILE --new-html FILE` also compares pages and their decoded download payloads.

`--write-manifest FILE` records SHA-256 hashes of `geometry.json`, every palette's SVG and each master.
SVG hashes set aside inert `<g>` wrappers.
Master hashes set aside the agent string and embedded font data.
The command saves a 480 px wide thumbnail of each `final-<palette>.png` beside the manifest.

`--manifest FILE` requires matching hashes and thumbnails within 0.5 % differing pixels.
A pixel differs when any channel moves by more than 24.
This tolerance allows rendering differences between librsvg versions.
Record a new manifest after reviewing a deliberate visual change.

A passing comparison ends with `REGRESSION PASSED`.