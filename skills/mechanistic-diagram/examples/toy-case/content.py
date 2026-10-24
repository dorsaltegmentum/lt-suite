# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Toy case "Fictional enzyme X deficiency": what the diagram says (the engine draws it).

An invented mechanism for the engine's worked example. Nothing here describes a real disease, drug
or patient. It uses every part of the grammar once: 11 boxes in 4 stages, all 7 roles, all 4 arrow
kinds, two flags, one lab value with its range and a ruled-out diagnosis.

Roles (box colours): risk (risk factor), mech (mechanism), anat (normal anatomy or physiology; dashed),
sign (sign or symptom), inv (investigation), dx (diagnosis), tx (treatment).
Connector kinds: stim (causes or leads to), inh (treatment blocks it), excl (evidence argues against
it), req (the normal anatomy or physiology it depends on; dashed).
Declaration order is drawing order: keep it stable once the layout is settled.
"""
from casekit import NBSP, UNIT_RULE, PLUS_RULE, Content, keep_together   # the engine puts casekit on sys.path

CLUSTERS = {   # Map each stage identifier to its display title
    "cause": "CAUSE  ·  from birth",
    "mech": "MECHANISM  ·  over years",
    "find": "FINDINGS AND DIAGNOSIS  ·  at the visit",
    "care": "TREATMENT  ·  after the assay",
}

# Apply _BIND sequentially before wrapping labels; retain the association between measurements and units.
_BIND = [UNIT_RULE, PLUS_RULE, (r"(\d) %", r"\1" + NBSP + "%"),
         *keep_together("Enzyme X", "enzyme X", "substrate S", "gene XA")]
ITALIC = ()   # terms set in italic (an organism's binomial name, for example), as bind() leaves them

C = Content(bind_rules=_BIND)
# This builder applies the configured text binding rules.
NODES, EDGES = C.nodes, C.edges
# Export the accumulated data for rendering, then expose the declaration methods.
n, e = C.n, C.e
# Declare boxes with n and connect their identifiers with e.

c = "cause"
n("rf_gene", "risk", "Two faulty copies of gene XA, inherited from both parents", c)
n("an_clear", "anat", "Enzyme X normally clears substrate S from the blood", c)

c = "mech"
n("m_low", "mech", "Enzyme X activity falls below 10 % of normal", c, bold=True)
n("m_build", "mech", "Substrate S builds up in skin and muscle over years", c, bold=True)

c = "find"
n("s_tired", "sign", "Tiredness after light exercise and pale skin patches", c)
n("i_level", "inv", "Blood substrate S 48 µmol/L (normal 2–10)", c,
  lab=[dict(value=48, lo=2, hi=10, unit="µmol/L", at="48")])
n("i_assay", "inv", "Enzyme X assay in blood cells: 6 % of normal activity", c)

n("dx_main", "dx", "Fictional enzyme X deficiency, confirmed by the assay", c, bold=True, flags=("high_yield",))
n("x_diet", "dx", "Too much substrate S in the diet (ruled out)", c, flags=("pitfall",))

c = "care"
n("tx_enz", "tx", "Weekly enzyme X replacement given by infusion", c)
n("tx_diet", "tx", "Low substrate S diet to slow the build-up", c)

e("rf_gene", "m_low")
e("an_clear", "m_low", "req")
e("m_low", "m_build")
e("m_build", "s_tired")
e("m_build", "i_level")
e("m_low", "i_assay")
e("i_level", "dx_main")
e("i_assay", "dx_main")
e("i_assay", "x_diet", "excl")
e("tx_enz", "m_build", "inh")
e("tx_diet", "m_build", "inh")

# why each flag and range is there (the engine draws them; a reviewer checks them against the sources)
FLAG_REASONS = {
    "dx_main": "high_yield: the assay result, not the blood level, makes the diagnosis",
    "x_diet": "pitfall: a high blood level alone looks like a dietary excess until the enzyme is measured",
}
RANGE_SOURCES = {"i_level": "case notes, laboratory report (normal 2–10 µmol/L)"}
