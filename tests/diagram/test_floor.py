# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""floor.py: its image, gutter and verify checks on small synthetic inputs, and the floor itself on the
worked example (examples/toy-case): the built example passes, and four mutated copies each fail with the
named metric (text shrunk below the minimum, two labels overlapped, a gap closed, a box pushed past the
page width limit). A floor check that passes everything fails here.
"""
import base64
import io
import json
import pathlib
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

import floor as F          # noqa: E402  (tests/diagram/conftest.py puts the engine on sys.path)

NS = 'xmlns="http://www.w3.org/2000/svg"'


def png_b64(rounded, w=200, h=100, r=24):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0) if rounded else (200, 100, 50, 255))
    if rounded:
        ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=(200, 100, 50, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


ROUNDED_SVG = (f'<svg {NS} viewBox="0 0 677 572" width="677" height="572"><defs><clipPath id="c">'
               '<rect width="677" height="572" rx="20.3"/></clipPath></defs><g clip-path="url(#c)">'
               '<rect width="677" height="572" fill="#e8e8e8"/></g></svg>')


def images(svg):
    return F.svg_images(ET.fromstring(svg))


class SvgImages(unittest.TestCase):
    def test_clip_path_on_image(self):
        got = images(f'<svg {NS} width="500" height="400"><defs><clipPath id="k"><rect x="10" y="20" width="200" '
                     f'height="100" rx="12"/></clipPath></defs><image x="10" y="20" width="200" height="100" '
                     f'href="x.png" clip-path="url(#k)"/></svg>')
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["ok"])
        self.assertEqual(got[0]["rx"], 12)
        self.assertEqual(got[0]["note"], "")

    def test_clip_larger_than_image_fails(self):
        got = images(f'<svg {NS}><defs><clipPath id="k"><rect x="0" y="0" width="300" height="300" rx="12"/></clipPath>'
                     f'</defs><image x="10" y="20" width="200" height="100" href="x.png" clip-path="url(#k)"/></svg>')
        self.assertFalse(got[0]["ok"])
        self.assertIn("larger than the image", got[0]["note"])

    def test_clip_on_scaled_group(self):
        got = images(f'<svg {NS}><defs><clipPath id="k"><rect width="400" height="200" rx="24"/></clipPath></defs>'
                     f'<g transform="translate(100,50) scale(0.5)" clip-path="url(#k)"><image width="400" height="200" '
                     f'href="x.png"/></g></svg>')
        self.assertTrue(got[0]["ok"])
        self.assertAlmostEqual(got[0]["rx"], 12.0)
        self.assertIn("at (100, 50) 200x100", got[0]["where"])

    def test_square_image_fails(self):
        got = images(f'<svg {NS}><image x="0" y="0" width="200" height="100" href="data:image/png;base64,{png_b64(False)}"/></svg>')
        self.assertFalse(got[0]["ok"])

    def test_bitmap_with_transparent_corners(self):
        got = images(f'<svg {NS}><image x="0" y="0" width="100" height="50" href="data:image/png;base64,{png_b64(True)}"/></svg>')
        self.assertTrue(got[0]["ok"])
        self.assertEqual(got[0]["how"], "transparent corners")
        self.assertTrue(9 <= got[0]["rx"] <= 15, got[0]["rx"])          # radius 24 of 200 px, shown at 100 px: 12

    def test_embedded_svg_with_rounded_clip(self):
        b64 = base64.b64encode(ROUNDED_SVG.encode()).decode()
        got = images(f'<svg {NS}><image x="0" y="0" width="338.5" height="286" href="data:image/svg+xml;base64,{b64}"/></svg>')
        self.assertTrue(got[0]["ok"])
        self.assertAlmostEqual(got[0]["rx"], 10.15, places=2)

    def test_nested_svg(self):
        inner = ROUNDED_SVG.replace(f'<svg {NS} ', '<svg x="40" y="60" ', 1)
        got = images(f'<svg {NS} width="900" height="700">{inner}</svg>')
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["ok"])
        self.assertAlmostEqual(got[0]["rx"], 20.3, places=1)
        self.assertIn("outside", got[0]["note"])                         # 20.3 px is rounder than a container

    def test_glyph_and_opaque_svg(self):
        disk = base64.b64encode(f'<svg {NS} width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7.5" '
                                f'fill="#884400"/></svg>'.encode()).decode()
        full = base64.b64encode(f'<svg {NS} width="300" height="200" viewBox="0 0 300 200"><rect width="300" '
                                f'height="200" fill="#e8e8e8"/></svg>'.encode()).decode()
        soft = base64.b64encode(f'<svg {NS} width="300" height="200" viewBox="0 0 300 200"><rect width="300" '
                                f'height="200" rx="18" fill="#e8e8e8"/></svg>'.encode()).decode()
        got = images(f'<svg {NS}><image x="0" y="0" width="16" height="16" href="data:image/svg+xml;base64,{disk}"/>'
                     f'<image x="0" y="40" width="150" height="100" href="data:image/svg+xml;base64,{full}"/>'
                     f'<image x="0" y="200" width="150" height="100" href="data:image/svg+xml;base64,{soft}"/></svg>')
        self.assertEqual([g["ok"] for g in got], [True, False, True])
        self.assertEqual(got[0]["how"], "glyph (no square corners)")
        self.assertIsNone(got[0]["rx"])
        self.assertEqual(got[2]["how"], "transparent corners")
        self.assertTrue(6 <= got[2]["rx"] <= 12, got[2]["rx"])          # rx 18 of 300, shown at 150: 9

    def test_no_images(self):
        self.assertEqual(images(f'<svg {NS}><rect width="10" height="10"/></svg>'), [])


class DrawioImages(unittest.TestCase):
    def write(self, styles):
        cells = "".join(f'<mxCell id="i{k}" style="{s}" vertex="1" parent="1"><mxGeometry x="0" y="0" width="200" '
                        f'height="100" as="geometry"/></mxCell>' for k, s in enumerate(styles))
        xml = (f'<mxfile><diagram id="d" name="p"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
               f'{cells}</root></mxGraphModel></diagram></mxfile>')
        d = pathlib.Path(tempfile.mkdtemp(dir=self.tmp))
        p = d / "t.drawio"
        p.write_text(xml, encoding="utf-8", newline="\n")
        return p

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_icon_cell_is_a_glyph(self):
        disk = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 17 17">'
                                b'<circle cx="8.5" cy="8.5" r="8" fill="#eeeeee"/></svg>').decode()
        cells = (f'<mxCell id="g" style="shape=image;html=1;image=data:image/svg+xml,{disk};" vertex="1" parent="1">'
                 f'<mxGeometry x="0" y="0" width="17" height="17" as="geometry"/></mxCell>')
        d = pathlib.Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "g.drawio").write_text(f'<mxfile><diagram id="d" name="p"><mxGraphModel><root><mxCell id="0"/>'
                                    f'<mxCell id="1" parent="0"/>{cells}</root></mxGraphModel></diagram></mxfile>', encoding="utf-8", newline="\n")
        got = F.drawio_images(d / "g.drawio")
        self.assertTrue(got[0]["ok"])
        self.assertEqual(got[0]["how"], "glyph (no square corners)")

    def test_kinds(self):
        svg64 = base64.b64encode(ROUNDED_SVG.encode()).decode()
        p = self.write([f"shape=image;image=data:image/png,{png_b64(False)};clipPath=inset(0% 0% 0% 0% round 7px);",
                        f"shape=image;image=data:image/svg+xml,{svg64};",
                        f"shape=image;image=data:image/png,{png_b64(False)};rounded=1;",
                        f"shape=image;image=data:image/png,{png_b64(False)};",
                        "rounded=1;whiteSpace=wrap;fillColor=#fff;"])                # not an image
        got = F.drawio_images(p)
        self.assertEqual(len(got), 4)
        self.assertEqual([g["ok"] for g in got], [True, True, True, False])
        self.assertEqual(got[0]["rx"], 7)
        self.assertEqual(got[1]["how"], "SVG with a rounded clip")
        self.assertIn("frame", got[2]["note"])


class HtmlImages(unittest.TestCase):
    def test_inline_svg_and_img(self):
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / "a.html"
        p.write_text('<style>.x img { border-radius: 12px; }</style><figure><svg viewBox="0 0 10 10">'
                     '<image width="10" height="10" href="q.png"/></svg></figure><img src="a.png">'
                     '<img src="b.png" style="border-radius: 7px">', encoding="utf-8", newline="\n")
        got = F.html_images(p)
        self.assertEqual(len(got), 3)
        self.assertFalse(got[0]["ok"])                 # the SVG image has no rounding
        self.assertTrue(got[1]["ok"] and got[1]["rx"] == 12)
        self.assertTrue(got[2]["ok"] and got[2]["rx"] == 7)


class Grid(unittest.TestCase):
    GEO = {"clusters": {"a": [0, 0, 100, 100], "b": [128, 0, 228, 100], "c": [0, 128, 228, 200]},
           "key": [256, 0, 356, 100], "nodes": {}, "edges": []}

    def test_gutters_equal(self):
        gaps = F.gutters(self.GEO)
        self.assertEqual(sorted(round(g) for *_, g in gaps), [28, 28, 28, 28])

    def test_gutter_off(self):
        geo = json.loads(json.dumps(self.GEO))
        geo["key"] = [260, 0, 360, 100]
        self.assertIn(32.0, [g for *_, g in F.gutters(geo)])


class Verify(unittest.TestCase):
    class B:
        def __init__(self, d):
            self.dir = d
            self.geo = {"edges": [{}] * 3, "nodes": {"a": {}, "b": {}}}
            self.drawio = None

    def run_with(self, obj):
        d = pathlib.Path(tempfile.mkdtemp())
        if obj is not None:
            (d / "verify-result.json").write_text(json.dumps(obj), encoding="utf-8", newline="\n")
        return F.verify_result(self.B(d))

    def test_states(self):
        self.assertEqual(self.run_with(None)[0], "WARN")
        self.assertEqual(self.run_with({"fontOk": True, "edgesOk": 3, "edgesBad": 0, "labelsOk": 2, "labelsBad": 0})[0], "ok")
        self.assertEqual(self.run_with({"fontOk": True, "edgesOk": 2, "edgesBad": 1, "labelsOk": 2, "labelsBad": 0})[0], "FAIL")
        st, detail = self.run_with({"fontOk": True, "edgesOk": 5, "edgesBad": 0, "labelsOk": 2, "labelsBad": 0})
        self.assertEqual(st, "WARN")
        self.assertIn("stale", detail)


class FloorFile(unittest.TestCase):
    """floor.json holds scale-free minimums set from the grammar, never numbers measured from a diagram."""

    def setUp(self):
        self.floor = json.loads(F.FLOOR_JSON.read_text(encoding="utf-8"))

    def test_source_is_the_grammar(self):
        self.assertEqual(self.floor["source"], "grammar")

    def test_no_size_limits_in_the_floor(self):
        # page size limits come from diagram.page.max_w / max_h, so a larger diagram breaches nothing here
        def keys(d):
            for k, v in d.items():
                yield k
                if isinstance(v, dict):
                    yield from keys(v)
        found = set(keys(self.floor))
        for key in ("height_max", "width_max", "PAGE_W_MAX", "canvas", "measured_from", "sha256", "text_px_rel"):
            self.assertNotIn(key, found)

    def test_aspect_floor_not_below_floor_json(self):
        import core
        # layout_engine widens a box to core.ASPECT_FLOOR; below the floor's minimum it would pass a box the floor fails
        self.assertGreaterEqual(core.ASPECT_FLOOR, self.floor["box_aspect_min"])

    def test_minimums_are_accessible(self):
        self.assertGreaterEqual(self.floor["contrast"]["text_min"], 4.5)          # WCAG 2.2 1.4.3
        self.assertGreaterEqual(self.floor["contrast"]["arrow_min"], 3.0)         # WCAG 2.2 1.4.11
        self.assertGreaterEqual(self.floor["text_px_min"], 11)
        self.assertEqual(self.floor["layout"], {"problems": 0, "crossings": 0, "unrouted": 0})
        self.assertEqual(self.floor["label_overlaps"], 0)


# ---------------------------------------------------------------- the floor on the worked example
def status(rows, name):
    """The status of the first row whose check name starts with name."""
    hits = [r[3] for r in rows if r[0].startswith(name)]
    assert hits, f"no floor row {name!r} in {[r[0] for r in rows]}"
    return hits[0]


def check(case_dir):
    rows, ok = F.check(case_dir, quiet=True)
    return rows, ok


def print_svg(case_dir):
    """The print palette's final SVG: the first in the floor's palette order."""
    b = F.Build(case_dir)
    return b.svg(b.palettes()[0])


def test_toy_case_holds_the_floor(toy_copy):
    rows, ok = check(toy_copy)
    fails = [r for r in rows if r[3] == "FAIL"]
    assert ok and not fails, fails


def test_text_shrunk_below_the_minimum_fails(toy_copy):
    svg = print_svg(toy_copy)
    text = svg.read_text(encoding="utf-8")
    floor = json.loads(F.FLOOR_JSON.read_text(encoding="utf-8"))
    small = floor["text_px_min"] - 2
    new, n = re.subn(r'(<text [^>]*font-size=")14(")', rf"\g<1>{small}\g<2>", text, count=1)
    assert n == 1
    svg.write_text(new, encoding="utf-8", newline="\n")
    rows, ok = check(toy_copy)
    assert not ok
    assert status(rows, "smallest text") == "FAIL"


def test_two_labels_overlapped_fail(toy_copy):
    svg = print_svg(toy_copy)
    text = svg.read_text(encoding="utf-8")
    runs = re.findall(r'<text x="([\d.]+)" y="([\d.]+)"', text)
    assert len(runs) > 2
    (x0, y0), (x1, y1) = runs[0], runs[1]
    # move the second text run onto the first one
    new = text.replace(f'<text x="{x1}" y="{y1}"', f'<text x="{x0}" y="{y0}"', 1)
    assert new != text
    svg.write_text(new, encoding="utf-8", newline="\n")
    rows, ok = check(toy_copy)
    assert not ok
    assert status(rows, "label overlaps") == "FAIL"


def test_a_closed_gap_fails(toy_copy):
    geo_path = toy_copy / "geometry.json"
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    a, b = geo["nodes"]["m_low"], geo["nodes"]["m_build"]
    # slide m_build left until only 2 px separate it from m_low
    b["x"] = a["x"] + a["w"] / 2 + 2 + b["w"] / 2
    geo_path.write_text(json.dumps(geo), encoding="utf-8", newline="\n")
    rows, ok = check(toy_copy)
    assert not ok
    assert status(rows, "box gap") == "FAIL"


def test_a_box_past_the_page_width_limit_fails(toy_copy):
    geo_path = toy_copy / "geometry.json"
    geo = json.loads(geo_path.read_text(encoding="utf-8"))
    max_w, _ = F.page_limits()
    geo["nodes"]["s_tired"]["x"] = max_w + 100
    geo_path.write_text(json.dumps(geo), encoding="utf-8", newline="\n")
    rows, ok = check(toy_copy)
    assert not ok
    assert status(rows, "page size") == "FAIL"


def test_page_limits_come_from_the_config(tmp_path, monkeypatch):
    import style
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text("[diagram.page]\nmax_w = 1200\nmax_h = 900\n", encoding="utf-8", newline="\n")
    try:
        style.configure(str(cfg))
        assert F.page_limits() == (1200, 900)
    finally:
        style.configure(None)
