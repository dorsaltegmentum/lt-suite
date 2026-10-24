<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Example config: University of Sydney Doctor of Medicine

`config.toml` in this folder fills the institution keys for the University of Sydney (USyd) Doctor of Medicine (MD). It holds public programme values only. It is an example of how one institution maps onto the config, and lt-suite works the same for any institution.

## What it sets

| Key | Value | Why |
|---|---|---|
| `institution.*` | name, unit code `MDMP5512` as a label, `en-AU`, `Australia/Sydney` | Display and run records only. No gate reads them. |
| `curriculum.week_number_pattern` | `N.M` weeks (block week and session) | Module titles carry the week as `3.2`. |
| `curriculum.patterns.*` | `### N.M Title` learning-outcome headings and a `*Kind · Discipline*` line | The shape of a learning outcomes file organised by week. |
| `rat.*` | iRAT skipped, tRAT answers from a folder | Team RAT answers come from a folder you keep yourself. |
| `tiers.tier` | course material, eTG Complete, NSW Health then other Australian and international guidance, peer-reviewed evidence | The source order for drafted answers. |
| `export.outputs.*` | notes graph, whiteboard, learning-outcome map and flashcard source on | Outputs for a Logseq notes graph. |
| `diagram.*` | TBL case naming, group and members in the header, Canvas delivery labels | Mechanism diagrams for team-based learning cases. |

Palettes and fonts are not set, so the defaults apply.

## Before use

1. Copy `config.toml` to the path `python3 -m ltcore.config path` prints, or let the `lt-suite-setup` skill write a config and copy over the keys you want.
2. Set `lt.origin` to your Lt address (`https://<tenant>.lt.app`). Capture and fill refuse to run while it is empty.
3. Replace the placeholder paths `~/Notes/Logseq`, `~/Notes/Curriculum` and `~/Notes/RATs` with your own folders, or set them to `""` to turn those features off.
4. Check it: `python3 -m ltcore.config validate --config <your file>` (with `shared/` or a skill's `scripts/_shared/` on `PYTHONPATH`).

eTG Complete needs your own licensed access. lt-suite never stores credentials; you sign in by hand in the browser the agent uses.

Set `rat.answers_dir` and `rat.answers_dirs` to your own answer-sheet folders. Choose `from_dir` per kind only when your course permits entry from those sheets. The example keeps `irat = "skip"` and `trat = "ask"`. See [RAT answers](../../docs/rat-answers.md).
