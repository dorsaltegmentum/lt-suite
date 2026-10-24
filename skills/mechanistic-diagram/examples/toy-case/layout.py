# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Toy case "Fictional enzyme X deficiency": where every box and stage sits. Data only; the engine does the work.

Drafted by autoplace.py (seed 2), then set by hand: the two cause boxes moved to columns 2 and 3 so the
inset panel below them is wide enough for its labels, and the two routes into m_low run down the gutter
between the cause and treatment stages. layout_engine.py reports 0 problems, 0 crossings, 0 unrouted.
"""

# Stage containers on one grid: (first column, last column, first row, last row).
# Column groups [(0, 3), (4, 5), (6, 9)]; main bands of rows [2, 2, 1].
SPAN = {
    'cause': (0, 3, 0, 1),   # CAUSE  ·  from birth
    'mech': (4, 5, 2, 3),   # MECHANISM  ·  over years
    'find': (4, 9, 4, 4),   # FINDINGS AND DIAGNOSIS  ·  at the visit
    'care': (4, 5, 0, 1),   # TREATMENT  ·  after the assay
}
KEY_SPAN = (6, 9, 0, 3)
INSET_SPAN = (0, 3, 2, 4)   # the illustration panel (case.json "inset")
GROUPS = [(0, 3)]
GROUPS_ABOVE_ROW = None

POS = {
    # CAUSE  ·  from birth
    'rf_gene': (2, 0), 'an_clear': (3, 1),
    # MECHANISM  ·  over years
    'm_low': (4, 2), 'm_build': (5, 2),
    # FINDINGS AND DIAGNOSIS  ·  at the visit
    'x_diet': (5, 4), 'i_assay': (6, 4), 'dx_main': (7, 4), 'i_level': (8, 4), 's_tired': (9, 4),
    # TREATMENT  ·  after the assay
    'tx_enz': (5, 0), 'tx_diet': (4, 1),
}
W_MAX = {}

HINT = {
    ('rf_gene', 'm_low'): dict(shape='ZH', sx=0.5, tx=0.3, ch=('gutter', 'cause', 'care')),
    ('an_clear', 'm_low'): dict(shape='ZH', sx=0.5, tx=0.7, ch=('mid', 'an_clear', 'tx_diet')),
    ('m_build', 's_tired'): dict(shape='ZV', sx=0.85, tx=0.3, ch='trunk'),
    ('m_build', 'i_level'): dict(shape='ZV', sx=0.7, tx=0.3),
    ('tx_enz', 'm_build'): dict(shape='V', x='sx0.7'),
}
