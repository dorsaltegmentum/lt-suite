# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Toy case: the words on the interactive page; make_artifact.py places them.

LEDE and FIGCAPTION hold HTML, the other fields plain text. Everything here is invented.
"""

OUT_NAME = "toy-case.html"
PAGE_TITLE = "Fictional enzyme X deficiency: mechanism"
EYEBROW = "Worked example · mechanistic flow chart"
HEADING = "Fictional enzyme X deficiency"
LEDE = ("Alex Example, 34, has had tiredness after light exercise for two years and pale patches on the skin. "
        "The figure follows one invented chain from an inherited gene fault to every finding, the test that makes "
        "the diagnosis and the two treatments. Read it left to right and top to bottom, one stage at a time.")
FIGCAPTION = ("Two faulty copies of gene XA leave enzyme X below 10&nbsp;% of normal, so substrate S builds up "
              "over years. The build-up explains the tiredness, the skin patches and the high blood level; the low "
              "enzyme assay explains why a dietary excess is ruled out. Both treatments act on the build-up.")
FINDINGS = [   # (History | Examination | Investigation, finding, explained by)
    ("History", "Tiredness after light exercise for two years", "Substrate S builds up in muscle"),
    ("Examination", "Pale patches on the skin", "Substrate S builds up in skin"),
    ("Investigation", "Blood substrate S 48 µmol/L (normal 2–10)", "Enzyme X no longer clears substrate S"),
    ("Investigation", "Enzyme X assay 6 % of normal activity", "Two faulty copies of gene XA"),
]
SOURCES = [
    "Case notes (invented for this example): sources/case-notes.md",
    "Treatment check: guideline-ledger.json (an invented guideline)",
]
STAGE_NOTES = {
    "cause": "Both copies of the gene must be faulty for the enzyme to fail.",
    "mech": "Every finding traces back to the build-up of substrate S.",
    "find": "The blood level points to the build-up; the assay points to the cause and rules out a dietary excess.",
    "care": "One treatment replaces the enzyme; the other lowers the intake of substrate S.",
}
