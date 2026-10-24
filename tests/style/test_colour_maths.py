# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-tests of the colour maths in ltcore.palettes against published values:
WCAG 2.x contrast, the 34 CIEDE2000 pairs of Sharma, Wu and Dalal (2005), the
Machado, Oliveira and Fernandes (2009) severity 1.0 matrices, the CSS Color 4
Oklch examples and CIELAB of white and red.
"""
from __future__ import annotations

import math

import pytest

from ltcore import palettes as P

# Sharma, Wu, Dalal (2005), Color Res. Appl. 30(1):21-30, Table 1:
# (L1, a1, b1, L2, a2, b2, published dE00)
SHARMA_2005 = [
    (50.0000, 2.6772, -79.7751, 50.0000, 0.0000, -82.7485, 2.0425),
    (50.0000, 3.1571, -77.2803, 50.0000, 0.0000, -82.7485, 2.8615),
    (50.0000, 2.8361, -74.0200, 50.0000, 0.0000, -82.7485, 3.4412),
    (50.0000, -1.3802, -84.2814, 50.0000, 0.0000, -82.7485, 1.0000),
    (50.0000, -1.1848, -84.8006, 50.0000, 0.0000, -82.7485, 1.0000),
    (50.0000, -0.9009, -85.5211, 50.0000, 0.0000, -82.7485, 1.0000),
    (50.0000, 0.0000, 0.0000, 50.0000, -1.0000, 2.0000, 2.3669),
    (50.0000, -1.0000, 2.0000, 50.0000, 0.0000, 0.0000, 2.3669),
    (50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0009, 7.1792),
    (50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0010, 7.1792),
    (50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0011, 7.2195),
    (50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0012, 7.2195),
    (50.0000, -0.0010, 2.4900, 50.0000, 0.0009, -2.4900, 4.8045),
    (50.0000, -0.0010, 2.4900, 50.0000, 0.0010, -2.4900, 4.8045),
    (50.0000, -0.0010, 2.4900, 50.0000, 0.0011, -2.4900, 4.7461),
    (50.0000, 2.5000, 0.0000, 50.0000, 0.0000, -2.5000, 4.3065),
    (50.0000, 2.5000, 0.0000, 73.0000, 25.0000, -18.0000, 27.1492),
    (50.0000, 2.5000, 0.0000, 61.0000, -5.0000, 29.0000, 22.8977),
    (50.0000, 2.5000, 0.0000, 56.0000, -27.0000, -3.0000, 31.9030),
    (50.0000, 2.5000, 0.0000, 58.0000, 24.0000, 15.0000, 19.4535),
    (50.0000, 2.5000, 0.0000, 50.0000, 3.1736, 0.5854, 1.0000),
    (50.0000, 2.5000, 0.0000, 50.0000, 3.2972, 0.0000, 1.0000),
    (50.0000, 2.5000, 0.0000, 50.0000, 1.8634, 0.5757, 1.0000),
    (50.0000, 2.5000, 0.0000, 50.0000, 3.2592, 0.3350, 1.0000),
    (60.2574, -34.0099, 36.2677, 60.4626, -34.1751, 39.4387, 1.2644),
    (63.0109, -31.0961, -5.8663, 62.8187, -29.7946, -4.0864, 1.2630),
    (61.2901, 3.7196, -5.3901, 61.4292, 2.2480, -4.9620, 1.8731),
    (35.0831, -44.1164, 3.7933, 35.0232, -40.0716, 1.5901, 1.8645),
    (22.7233, 20.0904, -46.6940, 23.0331, 14.9730, -42.5619, 2.0373),
    (36.4612, 47.8580, 18.3852, 36.2715, 50.5065, 21.2231, 1.4146),
    (90.8027, -2.0831, 1.4410, 91.1528, -1.6435, 0.0447, 1.4441),
    (90.9257, -0.5406, -0.9208, 88.6381, -0.8985, -0.7239, 1.5381),
    (6.7747, -0.2908, -2.4247, 5.8714, -0.0985, -2.2286, 0.6377),
    (2.0776, 0.0795, -1.1350, 0.9033, -0.0636, -0.5514, 0.9082),
]


def test_wcag_black_on_white_is_21():
    assert P.contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=1e-12)


def test_wcag_aa_boundary_grey():
    # #767676 is the lightest grey that passes AA on white; #777777 fails
    assert round(P.contrast("#767676", "#ffffff"), 2) == 4.54
    assert round(P.contrast("#777777", "#ffffff"), 2) == 4.48


def test_wcag_contrast_is_symmetric():
    assert P.contrast("#123456", "#abcdef") == P.contrast("#abcdef", "#123456")


@pytest.mark.parametrize("row", SHARMA_2005, ids=[f"pair{i + 1}" for i in range(len(SHARMA_2005))])
def test_ciede2000_sharma_pairs(row):
    got = P.ciede2000(row[0:3], row[3:6])
    assert got == pytest.approx(row[6], abs=1e-4)
    assert P.ciede2000(row[3:6], row[0:3]) == pytest.approx(got, abs=1e-9)


def test_machado_deuteranopia_of_red_is_first_column():
    # linear red through the severity 1.0 matrix gives the matrix's first column
    m = P.MACHADO["deutan"]
    col = tuple(m[i][0] for i in range(3))
    assert col == (0.367322, 0.280085, -0.011820)
    sim = P.simulate_linear("#ff0000", "deutan")
    assert sim[0] == pytest.approx(0.367322) and sim[1] == pytest.approx(0.280085)
    assert sim[2] == 0.0  # negative component clipped


@pytest.mark.parametrize("kind", ["protan", "deutan", "tritan"])
def test_machado_rows_sum_to_one(kind):
    for row in P.MACHADO[kind]:
        assert sum(row) == pytest.approx(1.0, abs=2e-6)
    # so greys stay grey
    assert P.simulate("#808080", kind) == "#808080"


@pytest.mark.parametrize("hexs,ref", [("#0000ff", (0.452, 0.313, 264.1)), ("#ffff00", (0.968, 0.211, 109.8))])
def test_oklch_css_color_4_examples(hexs, ref):
    L, C, h = P.oklch(hexs)
    assert L == pytest.approx(ref[0], abs=5e-4)
    assert C == pytest.approx(ref[1], abs=5e-4)
    assert h == pytest.approx(ref[2], abs=0.05)


def test_oklch_round_trip_in_gamut():
    for h in ("#4c4f69", "#ffe6c8", "#007a86", "#6699cc", "#994455"):
        assert P.oklch_hex(*P.oklch(h)) == h


def test_oklch_out_of_gamut_keeps_lightness_and_hue():
    L, C, h = 0.7, 0.4, 150.0
    out = P.oklch_hex(L, C, h)
    L2, C2, h2 = P.oklch(out)
    assert L2 == pytest.approx(L, abs=0.01)
    assert abs(h2 - h) < 2.0
    assert C2 < C


def test_cielab_white_and_red():
    assert P.lab("#ffffff") == pytest.approx((100.0, 0.0, 0.0), abs=1e-6)
    assert P.lab("#ff0000") == pytest.approx((53.2408, 80.0925, 67.2032), abs=1e-3)


def test_greyscale_lightness_of_black_and_white():
    assert P.grey_l("#000000") == pytest.approx(0.0)
    assert P.grey_l("#ffffff") == pytest.approx(1.0)


def test_de_ok_zero_for_same_colour_every_view():
    for kind in ("none", "protan", "deutan", "tritan", "grey"):
        assert P.de_ok("#336699", "#336699", kind) == 0.0


def test_hex_parsing_rejects_garbage():
    with pytest.raises(P.PaletteError):
        P.hex_rgb("blue")
    assert P.norm_hex("#ABC") == "#aabbcc"
    assert math.isclose(P.luminance("#ffffff"), 1.0)
