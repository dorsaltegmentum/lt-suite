# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers a case's content.py imports: non-breaking spaces, bind() rules, and the node/edge builder.

    from casekit import NBSP, UNIT_RULE, PLUS_RULE, Content
    _BIND = [UNIT_RULE, PLUS_RULE, (r"\\bX\\. fictus", "X." + NBSP + "fictus")]
    C = Content(bind_rules=_BIND)
    # Bind labels before their text is measured.
# This builder applies the configured text binding rules.
    NODES, EDGES = C.nodes, C.edges
    # Expose the data collections for the renderer.
# Export the accumulated data for rendering, then expose the declaration methods.
    n, e = C.n, C.e
    # Declare a mechanism and an investigation in the next two calls.
# Declare boxes with n and connect their identifiers with e.
    n("m_low", "mech", "Low enzyme X activity", "mech", bold=True)
    e("m_low", "i_level")
    n("i_level", "inv", "Substrate S 48 µmol/L (normal 2–10)", "find", lab=[dict(value=48, lo=2, hi=10, at="48")])
    # high_yield emphasizes the principal diagnosis.
    n("dx_main", "dx", "Fictional enzyme X deficiency", "care", bold=True, flags=("high_yield",))
    # urgent marks time-sensitive care; tag updates a box already declared.
    C.tag("x_diet", flags=("urgent",))

The glyph layers read two attrs (decor.py): flags (pitfall, urgent, high_yield) and lab (value, lo, hi,
unit, at, or flag="high"/"low" when the case states no range).

bind() runs every node text through the case's rules, in order, so a value keeps its label or unit
and a name stays in one piece when the engine wraps the text. The rules are the case's
own list: UNIT_RULE and PLUS_RULE are the generic ones (a number with its unit, "assay +" with
its sign) and a case adds its term lists. keep_together() builds exact-phrase rules for new cases.
"""
import re

NBSP = " "
# a number keeps its unit: "48 µmol/L", "9 mm", "37.5 °C", "5 months"
UNIT_RULE = (r"(\d) (?=(µmol/L|mmol/L|mm\b|°C|h\b|months|years|days|mL/min))", r"\1" + NBSP)
# a sign keeps its word: "assay +"
PLUS_RULE = (r"(\w) \+", r"\1" + NBSP + "+")


def keep_together(*phrases):
    """bind() rules that keep each phrase on one line (its spaces become NBSP), matched as whole words."""
    return [(r"\b" + re.escape(p) + r"\b", p.replace(" ", NBSP)) for p in phrases]


def binder(rules):
    """bind(text): apply the (pattern, replacement) rules in order."""
    rules = list(rules)

    def bind(text):
        for pat, rep in rules:
            text = re.sub(pat, rep, text)
        return text
    return bind


class Content:
    """Collects a case's nodes and edges in the order it declares them (that order is the drawing
    order of boxes and connectors, so keep it stable)."""

    def __init__(self, bind_rules=()):
        self.nodes = {}     # id -> dict(role, text, bold, cluster)
        self.edges = []     # dict(a, b, kind, label)
        self.bind = binder(bind_rules)

    def n(self, nid, role, text, cluster, bold=False, **attrs):
        """A box: id, role (risk mech anat sign inv dx tx), text, stage (a CLUSTERS key); bold for key steps.
        Optional attrs feed the engine's extra layers (glyph flags, lab ranges, the core/detail tier ...);
        they are stored under "attrs" and leave a box without any exactly as before."""
        if nid in self.nodes:
            raise ValueError(f"node id {nid!r} is declared twice")
        self.nodes[nid] = dict(role=role, text=self.bind(text), bold=bold, cluster=cluster)
        if attrs:
            self.nodes[nid]["attrs"] = attrs

    def tag(self, nid, **attrs):
        """Add attrs to a box declared earlier (e.g. a flag on a RULED OUT diagnosis made by pairs())."""
        if nid not in self.nodes:
            raise ValueError(f"tag: no node {nid!r}")
        self.nodes[nid].setdefault("attrs", {}).update(attrs)

    def e(self, a, b, kind="stim", label=None):
        """A connector a -> b: stim (causes), inh (treatment blocks), excl (evidence argues against),
        req (depends on normal anatomy or physiology)."""
        self.edges.append(dict(a=a, b=b, kind=kind, label=label))

    def pairs(self, pairs, cluster):
        """Create an exclusion pair from an evidence identifier, role and label, followed by a diagnosis
        identifier and label. The layout groups each observation beside its excluded diagnosis."""
        for ev, role, et, dx, dt in pairs:
            self.n(ev, role, et, cluster)
            self.n(dx, "dx", dt, cluster)
            self.e(ev, dx, "excl")
