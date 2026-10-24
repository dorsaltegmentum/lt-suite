#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start a case folder for the mechanistic-diagram engine.

    python3 new_case.py <case_dir> [--n N] [--config FILE]

Writes case.json (placeholders marked TODO), content.py, layout.py and artifact_content.py skeletons
with comments, pointing at the worked example (examples/toy-case, or diagram.example_case). Never
overwrites an existing case. build.py refuses to build until the placeholders are filled in and the
case has content.

The slug comes from diagram.naming.case_slug_pattern (default "case-{n}") and the page's eyebrow from
diagram.naming.eyebrow_template; {n} is --n, else the first number in the folder name. case.json asks
for the header fields in diagram.header.required_fields (default: title); group and members are
optional unless that list names them.
"""
import argparse, json, pathlib, re, sys

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent

CONTENT = '''"""{name}: diagram content, what the diagram says (the engine draws it).

Worked example: {example}/content.py.

Roles (box colours): risk (risk factor), mech (mechanism), anat (normal anatomy or physiology; dashed),
sign (sign or symptom), inv (investigation), dx (diagnosis), tx (treatment).
Connector kinds: stim (causes or leads to), inh (treatment blocks it), excl (evidence argues against
it), req (the normal anatomy or physiology it depends on; dashed).
Declaration order is drawing order: keep it stable once the layout is settled.
"""
from casekit import NBSP, UNIT_RULE, PLUS_RULE, Content, keep_together   # the engine puts casekit on sys.path

CLUSTERS = {{   # Stage titles may include a time interval
}}

# Apply _BIND sequentially before wrapping node labels. Each rule pairs a regex with its replacement.
# keep_together creates such rules for phrases that must share a line.
_BIND = [UNIT_RULE, PLUS_RULE]
ITALIC = ()   # Exact terms needing italics, using the text after _BIND substitutions

C = Content(bind_rules=_BIND)
# This builder applies the configured text binding rules.
NODES, EDGES = C.nodes, C.edges
# Export the accumulated data for rendering, then expose the declaration methods.
n, e = C.n, C.e
# Declare boxes with n and connect their identifiers with e.

# Starting example: declare the mechanism, its supporting evidence and a blocking treatment.
# c = "mech"
# n("rf_gene", "risk", "Two faulty copies of gene XA", "cause")
# n("m_build", "mech", "Substrate S builds up in tissue", c, bold=True)       # Emphasize a principal mechanism step
# n("i_level", "inv", "Substrate S 48 µmol/L (normal 2–10)", "find", lab=[dict(value=48, lo=2, hi=10, at="48")])
# e("rf_gene", "m_build")                                                     # kind="stim" unless given
# e("tx_enz", "m_build", "inh")

# ---------------------------------------------------------------- optional exclusion pairs
# Each tuple supplies the evidence identifier, its role and label, then a diagnosis identifier and label.
# PAIRS = [
#     ("e_diet", "inv", "Ordinary intake of substrate S", "x_diet", "Dietary excess of substrate S"),
# ]
# C.pairs(PAIRS, "care")
'''

LAYOUT = '''"""{name}: layout data, where every box and stage sits. Data only; the engine does the work.

Worked example: {example}/layout.py.

Iterate:  python3 {engine}/layout_engine.py <this folder>     (PROBLEMS / WARNINGS / CROSSINGS)
Build:    python3 {engine}/build.py <this folder>
"""

# POS: every node's grid cell (column, row), counted from 0 at the top left.
#   A column is as wide as its widest box; each box keeps its own proportions.
#   A fractional column or row (e.g. 0.5) sits halfway between two centres.
#   ((c0, c1), row) is a bar across columns c0..c1 whose text wraps to that width (a diagnosis bar, say).
# or draft every value of this file: python3 {engine}/autoplace.py <case_dir>   (writes layout.draft.py)
POS = {{
}}

# SPAN: every stage in content.CLUSTERS -> (first col, last col, first row, last row). A container is
#   drawn round its columns and rows plus padding, not round its boxes, so stages sharing columns line
#   up. Neighbouring containers must be exactly one GUTTER apart (the build checks it); the engine adds
#   the space before the first column / row of every span (GUTTER_COLS / GUTTER_ROWS override that).
SPAN = {{
}}
KEY_SPAN = None   # the key panel's (first col, last col, first row, last row): a free corner of the grid

# GROUPS lists inclusive column spans with equal internal gaps.
# GROUPS_ABOVE_ROW restricts these spans to earlier rows; None applies them throughout the layout.
GROUPS = []
GROUPS_ABOVE_ROW = None

# HINT: routing for one edge, keyed (source id, target id); edges left out take the shortest clean
#   route the router finds (straight, then one bend, then two).
#   shape  V / H straight; LV leaves vertically, enters horizontally; LH the reverse; ZV / ZH two bends
#   sx/tx  where the line leaves / enters along that side of the box, 0..1
#   x/y    the straight line's position: 'sxF' = source x0 + F*w, 'syF' = source y0 + F*h, 'tF' = target y0 + F*h
#   ch     a Z's channel: a number, "trunk" (one trunk for a fan-out into one row),
#          ("gutter", stage, stage) the middle of the gutter between two stages,
#          ("mid", box, box) the middle of the gap between two boxes
#   e.g.   ("m_low", "m_build"): dict(shape="ZH", sx=0.8, ch=("gutter", "cause", "mech"))
HINT = {{
}}

W_MAX = {{}}   # a wider limit for one box than the default 220 px, e.g. one between two column centres

# Tunables, with the engine's defaults: GAP_X = 28; X0, Y0 = 60, 176; ROW_GAP = 26; PAD, GUTTER = 18, 28;
# PAIR_GAP = 56; MIN_H = {{}}; NUDGE_X = {{}}
'''

ARTIFACT = '''"""{name}: the Artifact page's words (the engine's make_artifact.py lays the page out).

Worked example: {example}/artifact_content.py. LEDE and FIGCAPTION are HTML; the rest is plain text.
The build skips the page while any field below still says TODO.
"""

OUT_NAME = "{slug}.html"
PAGE_TITLE = "TODO: page title"
EYEBROW = "{eyebrow}"
HEADING = "TODO: the case title"
LEDE = "TODO: how the patient got here, and how to read the figure (HTML)"
FIGCAPTION = "TODO: one paragraph saying what the figure shows (HTML)"
FINDINGS = [   # (History | Examination | Investigation, finding, explained by)
    ("History", "TODO: finding", "TODO: the mechanism that explains it"),
]
SOURCES = [
    "TODO: the course material, guidelines and papers the content rests on",
]
STAGE_NOTES = {{}}   # optional: stage id -> one plain-text line shown under the step title in Present mode
'''


def settings():
    """(slug pattern, eyebrow template, required header fields, example folder) from the config."""
    import style
    from case import example_case, header_rules
    pattern = style.get("diagram.naming.case_slug_pattern", "case-{n}")
    eyebrow = style.get("diagram.naming.eyebrow_template", "")
    required, _order = header_rules()
    return pattern, eyebrow, required, example_case()


def main(argv):
    ap = argparse.ArgumentParser(description="Start a new case: writes content.py, layout.py, case.json and "
                                             "artifact_content.py with TODO markers.")
    ap.add_argument("case_dir")
    ap.add_argument("--n", help="the case number the slug and eyebrow templates use ({n}); default: the first "
                                "number in the folder name")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
        pattern, eyebrow_t, required, example = settings()
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    d = pathlib.Path(a.case_dir).resolve()
    taken = sorted(p.name for p in d.iterdir()) if d.is_dir() else []
    if taken:
        raise SystemExit(f"{d} already has {', '.join(taken)}; target must be empty; not overwriting any files")
    m = re.search(r"\d+(?:[.-]\d+)?", d.name if a.n is None else "")
    num = a.n if a.n is not None else (m.group(0) if m else None)
    slug = re.sub(r"[^a-z0-9.-]+", "-", pattern.replace("{n}", num.replace(".", "-") if num else "n").lower()).strip("-")
    if not num or not slug:
        slug = "TODO-" + (slug or "case")          # the build refuses a TODO slug until it is set
    eyebrow = eyebrow_t.replace("{n}", num) if (eyebrow_t and num) else (eyebrow_t or "TODO: the page's eyebrow")
    meta = {"slug": slug, "title": "TODO: case title",
            "subtitle": "TODO: patient, age · presentation (optional)",
            "aria_label": "TODO: Mechanism of ...: the steps, and the findings each step explains",
            "diagram_name": "TODO: the draw.io page name"}
    if "group" in required:
        meta["group"] = "TODO: the group to credit (ask)"
    if "members" in required:
        meta["members"] = ["TODO: each member to credit (ask; in the order the config sets)"]
    fill = dict(name=slug, example=example, engine=ENGINE, slug=slug, eyebrow=eyebrow.replace('"', "'"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    (d / "content.py").write_text(CONTENT.format(**fill))
    (d / "layout.py").write_text(LAYOUT.format(**fill))
    (d / "artifact_content.py").write_text(ARTIFACT.format(**fill))
    print(f"new case in {d}: case.json, content.py, layout.py, artifact_content.py")
    print(f"worked example: {example}")
    if {"group", "members"} & set(required):
        print("REMINDER: ask who to credit as group and members before filling in case.json; never copy them from")
        print("          an earlier case.")
    print(f"then:  python3 {ENGINE / 'layout_engine.py'} {d}   while placing boxes")
    print(f"       python3 {ENGINE / 'build.py'} {d}           for every output")


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
