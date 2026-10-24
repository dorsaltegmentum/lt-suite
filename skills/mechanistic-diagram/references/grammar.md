<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# The diagram grammar

A mechanism diagram makes claims. Every box and every connector says something, so the
grammar fixes what each mark means. The connector meanings follow SBGN Activity Flow,
because "X increases Y" and "Y cannot happen without X" are different mechanistic claims.
The glyphs are the engine's own.

## Box roles

Each box has one role. The role sets its colour pair (fill and stroke, built per palette)
and its glyph badge, so a role reads without colour too.

| Role | Meaning | Mark beside the colour |
|---|---|---|
| `risk` | risk factor or predisposition | dice glyph |
| `mech` | mechanism step | cog glyph |
| `anat` | normal anatomy or physiology the mechanism needs | human glyph, dashed border |
| `sign` | sign or symptom | stethoscope glyph |
| `inv` | investigation result | test-tube glyph |
| `dx` | diagnosis, confirmed or ruled out | clipboard glyph |
| `tx` | treatment | medical-bag glyph |

Bold text marks the key steps of the chain. A case may rename the roles in the key
(`ROLE_NAME` in `content.py`) and change the glyphs (`role_icons` in `case.json`).

## Connector kinds

| Kind | Claim | Glyph |
|---|---|---|
| `stim` | X causes, increases or leads to Y | solid line, arrowhead |
| `inh` | X (a treatment) blocks or reduces Y | solid line, T-bar |
| `excl` | evidence X argues against Y | dotted line, T-bar |
| `req` | Y depends on the normal anatomy or physiology X | dashed line, arrowhead |

Five rules follow. The build checks the ones a program can see.

**Every connector has a kind, and every kind has its own head.** The engine draws no
unstyled default. A plain line with a stock arrowhead would make a claim nobody wrote.

**Colour never carries a meaning alone.** Kinds differ by head (arrowhead or T-bar) and by
line style (solid, dashed, dotted). Roles differ by glyph and, for `anat`, by a dashed
border. The diagram survives greyscale printing and every common colour-vision deficiency;
`access.py` simulates protanopia, deuteranopia and tritanopia and checks the role strokes
stay apart.

**Never write the relation in words as well.** A label "inhibits" beside a T-bar says the
same thing twice, and the two copies drift apart at the first edit. A connector label
(`e(..., label=...)`) names what moves along the connector, never the relation.

**Up and down arrows live inside boxes, beside a named quantity.** "Substrate S 48 µmol/L
(normal 2–10)" with `lab=[dict(value=48, lo=2, hi=10, at="48")]` gets its ↑ and a range bar
from the engine. An arrow with no named quantity says nothing. Record where each range comes
from in `content.py` (`RANGE_SOURCES`).

**Heads stop clear of the box.** Arrowheads and T-bars end about 5 px short of the border
they point at (`core.end_gap`), so a T-bar never merges with the border it blocks.

## Flags

A box may carry flags: `urgent` (a time-critical red flag), `pitfall` (a common trap) and
`high_yield` (a key concept). Each flag needs a reason recorded in `content.py`
(`FLAG_REASONS`); a flag without a reason is decoration.

## Stages

Boxes sit in stage containers (`CLUSTERS` in `content.py`), read in story order. Optional
stage numbers, glyphs and a time-course ribbon come from `case.json` `"stages"`. Every finding
in the case needs a chain of connectors that ends in it.
