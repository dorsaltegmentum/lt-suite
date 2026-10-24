<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Measured palette defaults, font resolution and output validation. -->

# Fonts and palettes

lt-suite draws diagrams, PDF pages and micrograph labels in colours from a palette catalog and in fonts it can always find. The defaults are Paul Tol's bright palette (light, also used for print), Petroff's 10-colour cycle on GitHub Primer Dark High Contrast neutrals as the dark companion, and Noto Sans with Noto Sans Mono. Each was chosen by measurement; the numbers are below and in `shared/palettes/ranking.json`.

## What ships

| Path | Contents |
|---|---|
| `shared/palettes/catalog.json` | 42 palettes in 15 families (41 upstream palettes and the composite dark default): colour roles, role sources, licence, rank |
| `shared/palettes/overrides/*.toml` | hand-tuned themes for Catppuccin Latte, Catppuccin Frappé and Everforest Dark Medium |
| `shared/palettes/ranking.json` | every score, component and weighting behind the defaults |
| `shared/palettes/ATTRIBUTION.md` | licence and credit per palette family |
| `shared/fonts/noto-sans/`, `shared/fonts/noto-sans-mono/` | the variable TrueType files, byte-identical to upstream, with `OFL.txt` |
| `shared/fonts/catalog.json` | 32 libre fonts: licence, packages, readability evidence, pinned downloads |
| `shared/style.defaults.toml` | the `[style]` and `[fonts]` defaults |

Code: `shared/ltcore/palettes.py` (catalog, derivation, checks, ranking), `fonts.py` (resolution, metrics, static instances, downloads), `raster.py` (SVG to PNG and PDF) and `cleaners.py` (output cleaners and the PDF metadata strip).

## From palette to diagram theme

A catalog entry lists roles: `background`, `surface`, `text`, `text_muted`, `border`, `accent_1` to `accent_N`, `positive`, `negative`. `ltcore.palettes.derive_theme()` turns them into the theme the diagram engine draws with: canvas, container fill and border, text colours, a fill, stroke and text colour for each of the 7 roles (risk, mech, anat, sign, inv, dx, tx), and a colour, dash and head for each of the 4 arrow kinds.

The rule, for light themes (dark themes mirror it):

1. Structure. Canvas is the background. The container fill is the surface, or the background 0.035 darker in Oklch lightness when the palette has none. Muted text starts at `text_muted` and steps toward the text colour until it reaches 4.5:1 on the canvas and on the container fill.
2. Accent choice. A palette with more than 6 accents gives the 6 whose strokes stay furthest apart: for each pair, the smallest of its OKLab distances under normal vision and under simulated protanopia, deuteranopia and tritanopia, and its greyscale lightness gap. A palette with 5 accents may add its distinct `positive` or `negative` colour.
3. Role match. The 6 accents go to the 6 coloured roles by the assignment with the smallest total hue distance from the role hues (sign 25°, risk 60°, dx 140°, mech 195°, inv 260°, tx 300°). `anat` is a neutral.
4. Strokes keep each accent's own Oklch lightness and chroma. A stroke steps darker in 0.01 steps only until it reaches 3:1 on the canvas, the container fill and its own final fill. So a palette designed as light/dark pairs keeps its pairs apart.
5. Fills move each accent toward the background by one shared factor t (lightness by t, chroma by 1.5 t), which keeps hue and the order of lightness. t starts at 0.40 and steps down by 0.01 until text reaches 7:1 (WCAG AAA) on every fill. Two cases settle for 4.5:1 (AA): the text reaches less than 7:1 on the background itself, or 7:1 would need fills so pale that two of them fall within one just-noticeable difference (OKLab 0.02). In the second case t steps back up to the palest value that keeps every pair apart. Under 4.5:1 at t 0.04 the palette fails.
   When two of these fills are closer than 0.03 in greyscale lightness, the 7 fills are re-spaced on a ladder 0.035 apart in greyscale lightness, starting beside the background and moving toward the text colour. Each fill keeps its hue and chroma, and the fills keep the lightness order of their accents. The ladder is kept only when text still reaches the target on every fill.
6. The neutral `anat` stroke takes the lightness furthest, in greyscale, from the other 6 strokes.
7. Arrows: "causes" and "argues against" use the muted colour (the second dotted), "blocks" the tx stroke, "depends on" the anat stroke (dashed).

The engine used to derive every fill at one lightness and every stroke at another. That erased palettes whose colours differ mainly in lightness: Tol medium-contrast's derived strokes fell to CIEDE2000 0.06 under deuteranopia. Under the rule above its worst stroke pair is 8.8 under the worse of protanopia and deuteranopia (`tests/style/test_palettes.py::test_derivation_keeps_designed_separation_of_tol_medium_contrast`).

## Checks

Hard gates, which always raise `PaletteError` naming the colour pair and the measured value:

- text on every role fill, text on the canvas, stage titles on the container fill, muted text on the canvas and on the container fill, labels on the canvas: 4.5:1 (WCAG 2.2 SC 1.4.3)
- role strokes on the canvas, the container fill and their own fill, arrows on the canvas and the container fill: 3:1 (SC 1.4.11)
- two role strokes, or two role fills, closer than OKLab 0.02 (one just-noticeable difference) in normal vision

Accessibility gates, which decide whether a palette can be a default:

- every pair of the 7 role strokes at least OKLab 0.05 apart under simulated protanopia, deuteranopia and tritanopia (Machado, Oliveira and Fernandes 2009, severity 1.0, in linear sRGB)
- every pair of the 7 role fills at least 0.03 apart in greyscale lightness

A palette that misses an accessibility gate is never a default. Catalog palettes have `cvd = "report"`: such a palette stays selectable, `checks.reported` names each failing pair with its distance. A palette with `cvd = "check"` raises `PaletteError` instead. Every role also has its own icon in the boxes and the key, and every arrow kind its own dash and head, so a diagram can be read without colour.

`python3 -m ltcore.palettes check --all` runs the checks on every palette. `show <id>` prints a theme with its contrast ratios and reports.

PDF pages take their colours from the same theme through `ltcore.palettes.pdf_theme(id)`: the canvas is the page, the container fill the banded rows, the body and muted text the text colours, and the accents named blue, mauve, peach, red and green the link, heading and code colours. Every text colour reaches 4.5:1 on the page and on the banded rows; an accent that does not is stepped in Oklch lightness with its hue and chroma kept.

## Ranking and defaults

Every catalog entry is derived and scored on the derived theme, together with 40 composite dark candidates (below). Licence fields are never read: `tests/style/test_catalog.py::test_ranking_is_independent_of_licence_form` replaces or removes every licence and gets the same order. Components, each credited from 0 to 1:

| Component | Measure | Full credit |
|---|---|---|
| text | body text on the canvas and on every fill (half credit at 4.5:1, log scale) | 7:1 |
| nontext | weakest stroke or arrow contrast (half credit at 3:1) | 4.5:1 |
| stroke_normal | worst stroke pair, CIEDE2000, normal vision | 20 |
| stroke_red_green | worst stroke pair, CIEDE2000, the worse of protanopia and deuteranopia | 20 |
| stroke_tritan | worst stroke pair, CIEDE2000, tritanopia | 20 |
| stroke_grey | worst stroke pair, greyscale lightness gap | 0.10 |
| fill_cvd | worst fill pair, CIEDE2000, worst of the 4 views | 10 |
| fill_grey | worst fill pair, greyscale lightness gap | 0.05 |

Protanopia and deuteranopia share one component because together they affect about 8% of men: a palette that collapses for one of them cannot buy the loss back with the other. Tritanopia is rare and has its own smaller weight.

Weightings (each sums to 100; "balanced" picks the defaults, the other three test how robust the choice is):

| Weighting | text | nontext | normal | red-green | tritan | stroke grey | fill CVD | fill grey |
|---|---|---|---|---|---|---|---|---|
| balanced | 20 | 10 | 10 | 22 | 8 | 15 | 8 | 7 |
| cvd-first | 15 | 5 | 5 | 35 | 15 | 10 | 10 | 5 |
| print-first | 20 | 10 | 10 | 10 | 5 | 30 | 5 | 10 |
| text-first | 35 | 15 | 10 | 15 | 5 | 10 | 5 | 5 |

Each weighted ranking contains only palettes that pass every gate. The first palette of each list is the winner. Failed palettes retain their measurements and failure reasons in `ranking.json`, without a rank position. Default-eligible: 4 of 19 derivable light palettes, 0 of 10 derivable catalog dark palettes considered for defaults (12 including hand-tuned options), 6 of 31 derivable dark composites.

Winners:

| Weighting | Light | Dark |
|---|---|---|
| balanced | tol-bright (57.42) | petroff-10-on-primer-dark-high-contrast (68.23) |
| cvd-first | tol-bright (54.20) | petroff-10-on-primer-dark-high-contrast (65.38) |
| print-first | tol-muted (53.06 against 51.40) | petroff-10-on-primer-dark-high-contrast (68.77) |
| text-first | tol-bright (67.56) | petroff-10-on-primer-dark-high-contrast (74.56) |

The light default wins 3 of 4 weightings. Tol Muted takes print-first on its larger stroke greyscale gap. The dark default wins all 4.

### Composite dark candidates

No catalog dark palette passes every gate: each fails the protan, deutan or tritan stroke gate, and the Modus Vivendi and Catppuccin palettes also fail fill greyscale separation. So `ltcore.palettes.dark_composites()` builds 40 candidates: the accents of 8 categorical palettes (Tol medium-contrast, bright and muted, Petroff 6, 8 and 10, Okabe-Ito, IBM) on the neutral colours (background, surface, text, muted text, border) of 5 dark palettes (Primer Dark High Contrast, Modus Vivendi, Catppuccin Mocha and Macchiato, Everforest Dark Medium). Each candidate is derived, gated and scored like a catalog entry. 31 derive; 9 cannot (IBM has 5 accents; 4 others have two fills within one just-noticeable difference). 6 pass every gate, all on Primer Dark High Contrast neutrals; on the other 4 neutral sets the fills fail the greyscale gate. The winner ships in `catalog.json` as `petroff-10-on-primer-dark-high-contrast` (licence `MIT AND CC0-1.0`). When no candidate passes, the ranking provides no dark default and retains the light default alone. Dark options retain their failing pairs in `checks.reported`.

Light palettes, eligible first, each group by the balanced score (scores for balanced, cvd-first, print-first, text-first; the worst text-on-fill contrast; the worst non-text contrast; the worst stroke CIEDE2000 in normal / red-green / tritan view; the worst stroke OKLab distance under protan / deutan / tritan simulation, gate 0.05; the fill greyscale gap, gate 0.03):

| Palette | bal. | cvd | print | text | text/fill | non-text | strokes ΔE00 N / RG / T | strokes OKLab P / D / T | fill grey | default? |
|---|---|---|---|---|---|---|---|---|---|---|
| tol-bright | 57.42 | 54.2 | 51.4 | 67.6 | 10.66 | 3.02 | 19.4 / 10.4 / 9.6 | 0.080 / 0.059 / 0.050 | 0.033 | yes, the default |
| tol-muted | 56.08 | 52.5 | 53.1 | 66.0 | 10.73 | 3.07 | 15.0 / 8.4 / 11.5 | 0.095 / 0.072 / 0.093 | 0.033 | yes |
| petroff-6 | 55.69 | 52.4 | 50.9 | 66.0 | 10.67 | 3.00 | 17.9 / 8.1 / 12.5 | 0.063 / 0.067 / 0.099 | 0.034 | yes |
| tol-medium-contrast | 50.05 | 47.8 | 45.8 | 60.9 | 10.72 | 3.02 | 8.9 / 8.8 / 8.8 | 0.077 / 0.079 / 0.058 | 0.033 | yes |
| govuk | 53.84 | 49.7 | 48.3 | 64.9 | 10.00 | 3.05 | 17.0 / 10.0 / 5.9 | 0.060 / 0.064 / 0.023 | 0.032 | no: tritan |
| viridis | 52.57 | 48.0 | 47.9 | 63.7 | 10.70 | 3.06 | 14.6 / 9.7 / 3.8 | 0.063 / 0.075 / 0.024 | 0.033 | no: sequential ramp |
| okabe-ito | 50.79 | 44.6 | 47.3 | 62.7 | 10.66 | 3.01 | 17.5 / 5.9 / 7.3 | 0.076 / 0.058 / 0.028 | 0.033 | no: tritan |
| cividis | 50.26 | 47.4 | 45.9 | 61.1 | 10.64 | 3.01 | 9.5 / 9.2 / 6.0 | 0.065 / 0.059 / 0.025 | 0.032 | no: sequential ramp |
| modus-operandi-tinted | 50.11 | 42.2 | 47.7 | 63.0 | 9.87 | 3.42 | 15.4 / 1.9 / 8.8 | 0.030 / 0.017 / 0.040 | 0.034 | no: protan, deutan, tritan |
| petroff-10 | 50.03 | 44.1 | 46.3 | 62.2 | 10.74 | 3.08 | 16.0 / 6.3 / 6.7 | 0.047 / 0.046 / 0.055 | 0.033 | no: protan, deutan |
| tol-light | 49.09 | 43.4 | 46.5 | 61.1 | 10.68 | 3.03 | 14.0 / 6.0 / 6.9 | 0.034 / 0.041 / 0.046 | 0.031 | no: protan, deutan, tritan |
| tol-vibrant | 48.95 | 43.1 | 45.7 | 61.3 | 10.67 | 3.03 | 15.2 / 7.4 / 5.4 | 0.060 / 0.042 / 0.042 | 0.034 | no: deutan, tritan |
| modus-operandi | 46.41 | 36.0 | 45.9 | 61.7 | 10.72 | 3.69 | 17.0 / 0.8 / 8.3 | 0.031 / 0.004 / 0.037 | 0.032 | no: protan, deutan, tritan |
| petroff-8 | 45.94 | 39.7 | 45.2 | 58.2 | 10.64 | 3.02 | 10.5 / 1.1 / 11.0 | 0.064 / 0.009 / 0.082 | 0.034 | no: deutan |
| uswds | 44.83 | 36.2 | 45.2 | 58.0 | 8.84 | 3.00 | 14.0 / 0.8 / 8.3 | 0.008 / 0.006 / 0.066 | 0.033 | no: protan, deutan |
| primer-light-high-contrast | 43.84 | 34.4 | 43.4 | 59.4 | 10.49 | 3.98 | 8.6 / 0.9 / 5.9 | 0.015 / 0.009 / 0.044 | 0.034 | no: protan, deutan, tritan |
| modus-operandi-deuteranopia | 42.47 | 34.6 | 41.7 | 56.9 | 10.70 | 3.38 | 8.6 / 1.7 / 6.0 | 0.032 / 0.014 / 0.038 | 0.033 | no: protan, deutan, tritan |
| modus-operandi-tritanopia | 41.01 | 30.5 | 41.3 | 57.1 | 10.72 | 3.69 | 10.1 / 0.7 / 3.2 | 0.015 / 0.007 / 0.019 | 0.031 | no: protan, deutan, tritan |
| catppuccin-latte | 35.63 | 27.6 | 33.6 | 50.5 | 6.41 | 3.20 | 14.6 / 2.0 / 4.8 | 0.032 / 0.024 / 0.044 | 0.000 | no: hand-tuned |

Dark palettes: the 6 eligible composites, then the 12 catalog dark palettes (the 25 composites that fail a gate are in `ranking.json`):

| Palette | bal. | cvd | print | text | text/fill | non-text | strokes ΔE00 N / RG / T | strokes OKLab P / D / T | fill grey | default? |
|---|---|---|---|---|---|---|---|---|---|---|
| petroff-10-on-primer-dark-high-contrast | 68.23 | 65.4 | 68.8 | 74.6 | 12.90 | 3.10 | 17.4 / 10.8 / 15.9 | 0.054 / 0.096 / 0.091 | 0.032 | yes, the default |
| okabe-ito-on-primer-dark-high-contrast | 65.11 | 61.4 | 61.5 | 73.8 | 12.84 | 3.34 | 22.3 / 12.2 / 10.5 | 0.096 / 0.076 / 0.086 | 0.032 | yes |
| tol-muted-on-primer-dark-high-contrast | 62.74 | 61.9 | 56.6 | 70.8 | 12.83 | 3.08 | 16.1 / 14.0 / 9.5 | 0.103 / 0.052 / 0.082 | 0.034 | yes |
| tol-medium-contrast-on-primer-dark-high-contrast | 60.43 | 59.1 | 53.7 | 69.4 | 12.77 | 3.02 | 18.4 / 11.9 / 11.9 | 0.086 / 0.106 / 0.086 | 0.033 | yes |
| petroff-6-on-primer-dark-high-contrast | 59.58 | 61.0 | 51.2 | 68.3 | 12.88 | 3.01 | 15.8 / 12.3 / 16.5 | 0.138 / 0.113 / 0.109 | 0.031 | yes |
| petroff-8-on-primer-dark-high-contrast | 58.11 | 54.9 | 52.8 | 68.1 | 12.85 | 3.12 | 18.4 / 9.2 / 11.7 | 0.075 / 0.092 / 0.090 | 0.033 | yes |
| modus-vivendi-tinted | 50.95 | 38.1 | 51.4 | 67.3 | 9.87 | 5.28 | 24.6 / 0.9 / 8.0 | 0.042 / 0.006 / 0.059 | 0.032 | no: protan, deutan |
| primer-dark-high-contrast | 47.10 | 35.9 | 46.4 | 63.8 | 12.82 | 6.97 | 13.5 / 2.9 / 3.8 | 0.043 / 0.035 / 0.019 | 0.031 | no: protan, deutan, tritan |
| modus-vivendi-tritanopia | 46.85 | 39.0 | 42.9 | 62.1 | 12.37 | 3.62 | 20.4 / 3.2 / 9.1 | 0.042 / 0.022 / 0.059 | 0.001 | no: protan, deutan, fill grey |
| modus-vivendi | 45.93 | 34.2 | 44.7 | 63.8 | 12.37 | 5.13 | 24.6 / 0.9 / 8.0 | 0.042 / 0.006 / 0.059 | 0.001 | no: protan, deutan, fill grey |
| primer-dark-colorblind | 42.67 | 33.5 | 42.1 | 59.2 | 8.83 | 4.38 | 5.6 / 2.0 / 4.6 | 0.033 / 0.015 / 0.046 | 0.032 | no: protan, deutan, tritan |
| catppuccin-macchiato | 42.62 | 34.7 | 43.7 | 53.3 | 5.93 | 3.08 | 17.1 / 4.4 / 4.9 | 0.030 / 0.065 / 0.035 | 0.005 | no: protan, tritan, fill grey |
| catppuccin-mocha | 41.40 | 32.0 | 42.0 | 54.8 | 6.26 | 3.43 | 16.8 / 2.1 / 5.5 | 0.019 / 0.070 / 0.039 | 0.002 | no: protan, tritan, fill grey |
| primer-dark-tritanopia | 41.09 | 32.9 | 40.5 | 56.6 | 8.77 | 3.78 | 5.6 / 2.2 / 4.6 | 0.024 / 0.025 / 0.046 | 0.032 | no: protan, deutan, tritan |
| a11y-syntax-dark | 36.44 | 26.5 | 35.9 | 52.9 | 7.15 | 3.08 | 15.3 / 0.4 / 3.5 | 0.002 / 0.049 / 0.020 | 0.001 | no: protan, deutan, tritan, fill grey |
| modus-vivendi-deuteranopia | 35.75 | 25.5 | 35.0 | 53.9 | 11.70 | 3.87 | 10.3 / 0.6 / 3.0 | 0.007 / 0.004 / 0.022 | 0.001 | no: protan, deutan, tritan, fill grey |
| catppuccin-frappe | 32.45 | 26.1 | 29.7 | 43.5 | 4.92 | 3.95 | 11.5 / 2.4 / 5.0 | 0.026 / 0.017 / 0.023 | 0.000 | no: hand-tuned |
| everforest-dark-medium | 29.90 | 22.6 | 28.1 | 41.3 | 4.88 | 3.84 | 11.3 / 1.7 / 3.2 | 0.027 / 0.007 / 0.013 | 0.001 | no: hand-tuned |

`ranking.json` holds every entry. Three kinds of entry retain measured scores without rank positions: palettes that miss an accessibility gate, sequential ramps (viridis, cividis), whose colours imply an order the roles do not have, and the three hand-tuned themes. Ten entries cannot give a diagram theme and name the reason: tol-high-contrast, colorbrewer-set2-n3, colorbrewer-dark2-n3, colorbrewer-paired-n4, ibm-colourblind, primer-light-colorblind and primer-light-tritanopia have fewer than 6 usable accents; solarized-light's body text is 4.13:1 on its background; solarized-dark's muted text cannot reach 4.5:1 on its container fill; everforest-light-medium's anat and inv fills are within one just-noticeable difference.

`python3 -m ltcore.palettes rank` prints the winners. `rank --write shared/palettes/ranking.json --update-catalog` regenerates the ranking file and the rank fields of `catalog.json`; a test fails when either is stale.

### Catalog corrections

Three corrections from a value-by-value check against upstream are applied:

- cividis takes the authors' table (pnnl/cmaputil `cividis.txt`); the earlier values came from a different copy.
- The four Modus deuteranopia and tritanopia variants take their accents from each variant's own `rainbow-N` mapping, the colours the variant uses to keep headings apart for that deficiency. The earlier entries carried the base theme's red and green.
- Petroff's colour cycles are CC0-1.0; the repository's MIT licence covers its code only.

## Hand-tuned themes

`overrides/catppuccin-latte.toml`, `catppuccin-frappe.toml` and `everforest-dark-medium.toml` hold complete themes built in Oklch at one fill lightness and one stroke lightness per palette. `derive_theme()` uses an override instead of deriving. They pass every contrast gate; their colour-vision and greyscale numbers are reported (constant lightness gives a fill greyscale gap of 0.000), so they are never defaults. `tests/style/test_overrides.py` checks them against the themes the engine drew before the catalog existed (`tests/style/golden-themes.json`). One value changed, listed in `tests/style/override-deltas.json`: Latte's muted text went from `#6c6f85` (4.37:1 on the canvas, 3.20:1 on the container fill) to `#56586d` (6.16 and 4.52), the same hue and chroma 0.08 darker.

## Your own palette

Point `style.custom_palettes_file` at a TOML file:

```toml
[[palette]]
id = "my-palette"          # lower case, digits and hyphens
name = "My palette"
mode = "light"             # light, dark or categorical-only
cvd = "report"             # or "check" to gate on colour-vision separation
[palette.roles]
background = "#ffffff"
text = "#1a1a1a"
text_muted = "#555555"     # optional
surface = "#f4f4f4"        # optional
accent_1 = "#004488"       # at least 6 accents
accent_2 = "#ddaa33"
accent_3 = "#bb5566"
accent_4 = "#117733"
accent_5 = "#44aa99"
accent_6 = "#882255"
```

Then list its id in `style.palettes`. A custom id replaces a catalog id of the same name, and the shipped override for that id (Catppuccin Latte, Frappé, Everforest Dark Medium) no longer applies: the theme is derived from your roles. Derived themes are cached in `<cache>/themes/<id>-<sha8>.json`; the hash covers the roles, the `cvd` policy and the `mode`, so editing any of them rebuilds the theme.

## Fonts

Defaults: Noto Sans and Noto Sans Mono, both SIL OFL 1.1 with no Reserved Font Name.

- Availability: Noto Sans is packaged for Debian and Ubuntu, Fedora, Arch and Homebrew (package score 4 of 4). It is the default sans on Fedora and the generic sans-serif on Ubuntu desktop; Noto Sans Mono is Fedora's default monospace and is installed on Ubuntu desktop.
- Readability: the largest independent study in the catalog (Wallace et al. 2022, 352 adults, 16 fonts) ranked Noto Sans first for preference. It found no font that raised reading speed for everyone; Noto Sans was mid-table on speed.
- Coverage: Noto Sans has all 49 basic Greek letters and the super- and subscripts labels need (IL-1β, Ca²⁺). Arrows and ≤ ≥ fall back to another font at render time.
- Weakness: capital I and lower-case l differ only in height (shape distance 0.275).

Windows and macOS preinstall neither, so lt-suite bundles both variable files (3.6 MB together, under the 4 MB per-skill budget) with the upstream `OFL.txt`. They are copied into the setup, fill, export and diagram skills.

Low-vision option: Atkinson Hyperlegible Next with Atkinson Hyperlegible Mono (I/l/1 shape distance 0.605 against Noto Sans's 0.275). Atkinson Hyperlegible Next has 4 Greek letters and no super- or subscripts, so those characters fall back to Noto Sans. The setup skill offers both; `python3 -m ltcore.fonts fetch atkinson-hyperlegible-next --yes` downloads it.

No dyslexia font is recommended. Controlled studies and a 2026 meta-analysis found no reading benefit from OpenDyslexic (citations in `shared/fonts/catalog.json`).

### How a font is found

`ltcore.fonts.resolve(role, weight=400, italic=False, cfg=cfg)` for `sans`, `mono` or `mono_bold` returns a path with `.family`, `.source` and `.fallback` attached. First hit wins:

1. an explicit file: `fonts.sans_path`, `fonts.mono_path`, `fonts.mono_bold_path` (mono at weight 600 or more), or a `fonts.sans` / `fonts.mono` value that is a path
2. the bundled copy in `shared/fonts/<id>/`
3. a downloaded copy in `<data>/fonts/<id>/`
4. an installed font: `fc-match`, accepted only when it returns the requested family, else a scan of the platform font folders
5. Noto from bundled, fetched or installed files, marked as a fallback (`doctor` reports it)
6. for page PDFs and labels, the OS font when `fonts.system_fallback = true`

Bundled and downloaded copies come before installed ones, so a diagram renders the same on every machine.

An italic request that no file serves (the bundled Noto files are upright only) returns the upright file with `.italic` False and a `.source` that names the missing face. Diagrams and raster renderers slant the upright outlines; page PDFs use upright text. `static_instance()` refuses to label an upright file italic.

### Fonts per output

`fonts.sans` and `fonts.mono` remain the base pair. Each nonempty output key replaces
both the base family and its explicit file keys for that role:

```toml
[fonts.pdf]
sans = "Noto Sans"
mono = "Noto Sans Mono"

[fonts.diagram]
sans = "Noto Sans"
mono = "Noto Sans Mono"

[fonts.labels]
sans = ""  # inherit PDF sans, then base sans
```

Each key accepts a family name or a font file path relative to the config file.
`annotate.font_path` still wins over label settings. Missing explicit files raise an
error; label tools report the reason and use Pillow's default. A missing family
falls through Noto before trying OS files.

The OS chain uses SF Pro and SF Mono on macOS, Segoe UI and Cascadia Mono on
Windows, and DejaVu Sans and DejaVu Sans Mono on Linux. These files stay on their
host: lt-suite never bundles, fetches, cuts or embeds them in HTML or draw.io.
Set `fonts.system_fallback = false` to disable that step and remove OS names from
CSS stacks. Diagrams require a font with a recognised libre licence, with Noto
as the fallback. HTML pages and copied SVGs name viewer-side CSS fallback families.
Developer-download Apple fonts outside `/System/Library/Fonts` are refused.

`python3 -m ltcore.fonts resolve sans --output pdf` reports the resolved file.
`python3 -m ltcore.fonts stack mono --output diagram` prints its CSS stack.
Doctor reports PDF sans and mono, diagram sans and mono, and label sans separately.
Google mode warns about diagram families Google Fonts does not host.

New output tables and `annotate.palette_copies` require the updated loader.
v0.1.0 rejects these keys. Three licence changes affect existing configs:

1. Page PDFs refuse fsType-restricted, preview-and-print, no-subsetting and bitmap-only fonts.
2. Diagrams keep Reserved Font Name variable fonts whole, so PDF weights may flatten.
3. Diagrams replace fonts with unreadable or restricted licences with Noto.

`pdf_embeddable` accepts installable fsType 0 or editable bit 0x0008, except
no-subsetting bit 0x0100 and bitmap-only bit 0x0200. All PDF body and symbol faces
pass this check before ReportLab registration. SVG figure glyph fallback still
uses system fontconfig; cairo's enforcement of fsType remains unverified.
`licence_class` separates modification (`modify`), whole-file embedding (`whole`)
and refused redistribution (`none`). Only `modify` fonts may be cut or subset.

### Micrograph palette copies

The Lt upload stays yellow on black with the configured upload colours.
`annotate.palette_copies = true` also writes `<stem>-<palette id>` images for every
PDF palette, including print. Each copy uses the PDF mantle for its margin and
the PDF text colour for its caption. Point, ring, bracket and inset marks use blue,
peach, green and teal accents. Named overrides win; other palettes choose chromatic
accents by hue, without reuse until candidates run out. Oklch lightness steps
reach 4.5:1 on margin and casing; gamut mapping may reduce chroma.

| Palette | Print | Margin | Casing | Point | Ring | Bracket | Inset |
|---|---|---|---|---|---|---|---|
| Tol Bright | yes | #f3f3f3 | #ffffff | #3e71a4 | #c43f54 | #137f2a | #007793 |
| Petroff dark | no | #151b23 | #000104 | #3f90da | #e76300 | #b9ac70 | #92dadd |
| Catppuccin Latte | yes | #eff1f5 | #ffffff | #1b63f1 | #bd4700 | #1f7d00 | #00787f |
| Catppuccin Frappé | no | #292c3c | #222534 | #8caaee | #ef9f76 | #a6d189 | #81c8be |
| Everforest Dark Medium | no | #232a2e | #1c2327 | #7fbbb3 | #e69875 | #a7c080 | #83c092 |

Colours can converge under colour-vision simulation. Mark shapes and a key repeat
the distinction. `python3 -m ltcore.palettes micrograph tol-bright --print` reports
contrast and all simulated pair distances. Copies alone carry the key for 2 or more
mark kinds. Their long side is capped at 1920 pixels; `--copy-max 0` removes the cap.
`--palette lt` or `annotate.palette_copies = false` writes only the upload.

Every render rewrites `<stem>.copies.json`, binding upload and copy sha256 values.
After a verified upload and media capture, file the copies beside captured media:

```sh
python3 "$SKILL_DIR/scripts/annotate_micrograph.py" rendered.jpg media/lt-demo-u5.jpg --file-copies
```

Filing renders nothing and leaves the captured upload untouched. Additional paths
are replaced only when the previous manifest owns their current bytes, or with
`--overwrite`. PDF and asset export accept a copy only while its own hash and the
linked upload hash match the manifest. Changed uploads, edited copies and disabled
copies invalidate older siblings. A warning names `--file-copies` when binding fails.

### Downloads

`python3 -m ltcore.fonts fetch <id> --yes` (the setup skill asks first) downloads a catalog font from the google/fonts repository at a pinned commit, checks every file and its licence against the pinned sha256, and writes them to `<data>/fonts/<id>/`. Each file streams in 64 KiB reads into a private temporary folder (mode 0700) and is hashed as it arrives; a file over 20 MiB stops the download. A mismatch or an oversized file writes nothing to the data folder. `fonts.allow_download = false` refuses every download. 19 catalog fonts have pinned downloads; the others name their packages.

### Static instances

`resolve()` returns the font file itself, so a variable font comes back variable, and `ltcore.fonts.pillow_font()` sets its weight per call. cairo's PDF surface, ReportLab and a plain `ImageFont.truetype()` cannot select a weight: cairo can embed the default instance for a run that goes through its CID subset (curly quotes, µ, °), so bold text comes out regular. For them `resolve(..., static=True)`, `face_file()` and `ltcore.raster` cut static instances with fontTools into `<cache>/font-cache/`, renamed `<Family>Static` (for example `NotoSansStatic-Bold`) so the copy never shadows the installed family. A font with a Reserved Font Name is never cut. Cut files are never committed.

## Rendering and cleaners

`python3 -m ltcore.raster in.svg out.pdf` runs `rsvg-convert` with a private fontconfig file that adds the resolved font folders, and substitutes the static family names in a temporary copy of the SVG. The copy is a hidden `.lt-raster-*.svg` file beside the source, deleted after the conversion, so linked images and stylesheets with relative paths load as they do from the source. The source SVG is never changed. In a read-only folder the source renders with its own family names and a warning says so.

`ltcore.cleaners` runs the `[[text_cleaners]]` programs you configure (stages `fill-text`, `pdf`, `diagram`). Each runs from its `argv` list with no shell; `{in}`, `{out}` and `{pdf}` are the only placeholders. `run_cleaner()` works on temporary copies and replaces a file only when the program exits 0 within `timeout_s` with a non-empty result. `run_one()` runs on the caller's own files and raises on a non-zero exit, a timeout or a missing `{out}` file. For PDFs, the built-in strip (`pdf.strip_metadata`, on by default) removes the document information, XMP and PieceInfo, cuts metadata segments from JPEGs whose pixels stay equal, runs `exiftool -all=` when installed, and rewrites the file with a content-derived ID, so the same draft always gives the same bytes. Without pikepdf the rewrite and the check use qpdf's JSON v2 format, which needs qpdf 11.0 or newer; an older qpdf makes the strip report "failed". Then pikepdf, or `qpdf --json` without pikepdf, reads every object again: "cleaned" is reported only when no document information dictionary, no XMP stream (document or page level) and no PieceInfo is left. Without pikepdf or qpdf that check cannot run, so the strip reports "failed" and keeps the original. When page text, image pixels, link targets or the page count (pikepdf, else `qpdf --show-npages`) would change, the original is kept.

## Licences

Colour values keep their source's terms: MIT, BSD-3-Clause, Apache-2.0 (with ColorBrewer's acknowledgement), GPL-3.0-or-later (Modus values only), CC0-1.0, CC-BY-4.0, and `LicenseRef-palette-attribution` for Okabe-Ito and cividis. `shared/palettes/ATTRIBUTION.md` lists them per family with the credit lines. The fonts are SIL OFL 1.1; each bundled font folder carries its `OFL.txt`.
