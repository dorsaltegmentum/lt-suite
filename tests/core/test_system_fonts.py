# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Report real OS font metadata on its CI runner; Linux discovery is covered by fakes."""
import json
import sys

import pytest
from ltcore import fonts as F


GLYPHS = ''.join(chr(c) for c in range(0x391,0x3aa) if c != 0x3a2) + ''.join(chr(c) for c in range(0x3b1,0x3ca)) + '←↑→↓↔⇄⇌⇒−±×≤≥≈∞∆⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉°µ–—•′″‰'


def test_os_fallback_fonts_on_this_runner():
    if sys.platform not in ('darwin', 'win32'):
        pytest.skip('OS metadata report runs on macOS and Windows CI only')
    from fontTools.ttLib import TTFont
    for role in ('sans','mono'):
        for weight in ((400,600) if role == 'sans' and sys.platform == 'win32' else (400,)):
            found = list(F._os_candidates(role,weight,False,F.subprocess.run))
            if not found and role == 'mono':
                print(f'{role}: optional SF Mono / Cascadia Mono absent on runner')
                continue
            assert found, f'{sys.platform} must provide sans at weight {weight}'
            font = found[0]
            info = F.read_info(font.path)
            with TTFont(font.path, fontNumber=0) as face:
                assert 'glyf' in face and face.getBestCmap()
                cmap = face.getBestCmap()
            print(json.dumps(dict(file=str(font.path),family=info.family,weight=info.weight,
                fsType=info.fs_type,pdf_embeddable=F.pdf_embeddable(font.path),axes=info.axes,
                glyphs=len(cmap),missing=''.join(c for c in GLYPHS if ord(c) not in cmap),licence_terms='unverified')))
