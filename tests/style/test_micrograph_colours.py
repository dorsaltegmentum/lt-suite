# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact palette measurements and contrast, independent of installed fonts."""
import pytest
from ltcore import palettes as P


@pytest.mark.parametrize('pid,print_copy,expected', [
 ('tol-bright',True,['#f3f3f3','#ffffff','#000000','#3e71a4','#c43f54','#137f2a','#007793']),
 ('petroff-10-on-primer-dark-high-contrast',False,['#151b23','#000104','#ffffff','#3f90da','#e76300','#b9ac70','#92dadd']),
 ('catppuccin-latte',True,['#eff1f5','#ffffff','#4c4f69','#1b63f1','#bd4700','#1f7d00','#00787f']),
 ('catppuccin-frappe',False,['#292c3c','#222534','#c6d0f5','#8caaee','#ef9f76','#a6d189','#81c8be']),
 ('everforest-dark-medium',False,['#232a2e','#1c2327','#d3c6aa','#7fbbb3','#e69875','#a7c080','#83c092'])])
def test_owner_and_default_palettes(pid, print_copy, expected):
    assert list(P.micrograph_colours(pid, print_copy=print_copy).values()) == expected


@pytest.mark.parametrize('entry', list(P.catalog().values()), ids=lambda e:e['id'])
def test_every_catalog_palette_reaches_4_5(entry):
    try:
        theme = P.derive_theme(entry, use_cache=False)
    except P.PaletteError as exc:
        pytest.skip(f'palette does not derive: {exc}')
    for printed in (False, True):
        colours = P.micrograph_colours(entry, print_copy=printed)
        for kind in ('caption', *P.MICROGRAPH_KINDS):
            assert min(P.contrast(colours[kind], colours[g]) for g in ('margin','casing')) >= 4.5
        if not theme['dark']:
            assert colours['casing'] == '#ffffff'
        else:
            assert P.luminance(colours['casing']) <= min(P.luminance(theme[g]) for g in ('canvas','cl_fill'))


def test_hue_pick_rules():
    entry = P.get('catppuccin-latte')
    named = P.micrograph_colours(entry)
    assert named['ring'] != named['bracket']
    # Override-only palettes are supported without roles or accent metadata.
    custom = dict(id='test-custom', name='Test', mode='light', override=P.load_override('catppuccin-latte'))
    custom['override'] = dict(custom['override'])
    custom['override'].pop('accents', None)
    colours = P.micrograph_colours(custom)
    assert all(colours[k] == colours['caption'] for k in P.MICROGRAPH_KINDS)


def test_hue_pick_excludes_greys(monkeypatch):
    entry = P.get('tol-bright')
    # Keep a valid derived theme, vary only the source hue candidates.
    theme = P.derive_theme(entry)
    monkeypatch.setattr(P, 'derive_theme', lambda *a, **k: theme)
    for accents, count in [(['#777777']*6, 1), (['#0077bb','#ee7733']+['#777777']*4, 2)]:
        custom = dict(entry, roles={f'accent_{i}': c for i,c in enumerate(accents)}, origin='custom')
        colours = P.micrograph_colours(custom)
        assert len(set(colours[k] for k in P.MICROGRAPH_KINDS)) == count
    assert P.micrograph_audit(P.micrograph_colours('tol-bright'))['close_pairs']
