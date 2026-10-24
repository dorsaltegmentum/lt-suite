# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""access.py, consistency.py, guideline.py and diffviz.py on small synthetic inputs."""
import hashlib
import json
import pathlib
import tempfile
import unittest

import access as A
import consistency as C
import diffviz as D
import guideline as G
from diagram_testkit import TOY

NS = 'xmlns="http://www.w3.org/2000/svg"'


class Colour(unittest.TestCase):
    def test_contrast_known_values(self):
        self.assertAlmostEqual(A.contrast(A.hex_rgb("#000"), A.hex_rgb("#fff")), 21.0, places=2)
        self.assertAlmostEqual(A.contrast(A.hex_rgb("#767676"), A.hex_rgb("#ffffff")), 4.54, places=2)
        self.assertAlmostEqual(A.contrast(A.hex_rgb("#595959"), A.hex_rgb("#ffffff")), 7.0, places=2)

    def test_oklab_white_and_de(self):
        L, a, b = A.oklab((1, 1, 1))
        self.assertAlmostEqual(L, 1.0, places=3)
        self.assertAlmostEqual(a, 0.0, places=3)
        self.assertAlmostEqual(b, 0.0, places=3)
        self.assertEqual(A.de_ok((0.2, 0.4, 0.6), (0.2, 0.4, 0.6)), 0.0)

    def test_simulation(self):
        for kind in ("protan", "deutan", "tritan", "grey"):
            g = A.simulate((0.5, 0.5, 0.5), kind)
            self.assertTrue(all(abs(c - 0.5) < 0.01 for c in g), (kind, g))     # neutrals stay neutral
        red, green = A.hex_rgb("#d62728"), A.hex_rgb("#2ca02c")
        self.assertLess(A.de_ok(A.simulate(red, "deutan"), A.simulate(green, "deutan")), A.de_ok(red, green) / 2)


class Scan(unittest.TestCase):
    def svg(self, body):
        p = pathlib.Path(tempfile.mkdtemp()) / "t.svg"
        p.write_text(f'<svg {NS} width="400" height="200"><rect width="400" height="200" fill="#ffffff"/>{body}</svg>')
        return p

    def test_text_on_box(self):
        texts, _ = A.scan_svg(self.svg('<g><rect x="10" y="10" width="200" height="60" fill="#000000"/>'
                                        '<text x="110" y="45" text-anchor="middle" font-size="14" fill="#777777">dim words</text></g>'
                                        '<text x="20" y="150" font-size="14" fill="#222222">dark on white</text>'))
        by = {t["text"]: t for t in texts}
        self.assertEqual(by["dim words"]["bg"], "#000000")
        self.assertEqual(by["dim words"]["status"], "ok")             # #777 on black is 4.69
        self.assertEqual(by["dark on white"]["status"], "ok")

    def test_failure_and_large_text(self):
        texts, _ = A.scan_svg(self.svg('<text x="20" y="50" font-size="14" fill="#aaaaaa">pale</text>'
                                        '<text x="20" y="120" font-size="24" fill="#949494">pale but large</text>'))
        by = {t["text"]: t for t in texts}
        self.assertEqual(by["pale"]["status"], "FAIL")
        self.assertEqual(by["pale but large"]["need"], 3.0)
        self.assertEqual(by["pale but large"]["status"], "ok")

    def test_over_image(self):
        texts, _ = A.scan_svg(self.svg('<image x="0" y="0" width="300" height="100" href="x.png"/>'
                                        '<text x="20" y="50" font-size="14" fill="#000000">caption</text>'))
        self.assertTrue(texts[0]["status"].startswith("not measured"))

    def test_exceptions(self):
        exc = [{"theme": "light-a", "fg": "#707070", "bg": "#f0f0f0", "ratio": 4.3695}]
        self.assertTrue(A._known(exc, "light-a", "#707070", "#F0F0F0", 4.37))
        self.assertFalse(A._known(exc, "light-a", "#707070", "#f0f0f0", 4.20))      # worse than recorded
        self.assertFalse(A._known(exc, "dark-b", "#707070", "#f0f0f0", 4.37))
        exc[0]["min_px"] = 17
        self.assertTrue(A._known(exc, "light-a", "#707070", "#f0f0f0", 4.37, px=19))
        self.assertFalse(A._known(exc, "light-a", "#707070", "#f0f0f0", 4.37, px=12))    # a smaller use is new


class Facts(unittest.TestCase):
    UNITS = C._units()

    def test_extraction(self):
        fs, skipped = C.facts("Albumin 30\u00a0g/L (normal\u00a035–50), mostly low; IL-6 and XA1; "
                              "tiredness 1–2\u00a0h after exercise; BMI about 24; give frusemide", "n", self.UNITS)
        got = [(f["kind"], f["shown"]) for f in fs]
        self.assertIn(("value", "Albumin 30 g/L"), got)
        self.assertIn(("value", "normal 35-50"), got)
        self.assertIn(("measurement", "1-2 h"), got)
        self.assertIn(("value", "BMI about 24"), got)
        self.assertIn(("drug", "frusemide"), got)
        self.assertFalse(any("6" == f["num"] or "1" == f["num"] for f in fs if f["num"]))   # IL-6, XA1 are names

    def test_matching(self):
        src = [C.Source("page", "Her albumin is 30 and CRP (12); weight of 70 then row 6 normal levels; on furosemide")]
        find = lambda text: C.find(C.facts(text, "n", self.UNITS)[0][0], src)[0]
        self.assertEqual(find("Albumin 30"), "FOUND")
        self.assertEqual(find("CRP 12"), "FOUND")
        self.assertEqual(find("Lesion 6 mm"), "WEAK")           # the number appears without the matching unit
        self.assertEqual(find("ALT 110"), "MISSING")
        self.assertEqual(find("frusemide"), "FOUND")            # by its synonym

    def test_locale_lexicon(self):
        us, au = C.drug_lexicon("en-US"), C.drug_lexicon("en-AU")
        self.assertIn("paracetamol", us["acetaminophen"])
        self.assertIn("furosemide", au["frusemide"])
        self.assertNotIn("frusemide", us)                      # en-US does not use that spelling
        every = C.drug_lexicon("en")
        self.assertIn("frusemide", every)
        self.assertIn("acetaminophen", every)


class Ledger(unittest.TestCase):
    def test_hash(self):
        plain = "Infusion within 48 h: enzyme X replacement"
        self.assertEqual(G.text_sha1("Infusion within 48\u00a0h: enzyme X replacement"),
                         hashlib.sha1(plain.encode()).hexdigest()[:12])
        self.assertEqual(G.text_sha1("Infusion within 48\u00a0h: enzyme X replacement"), "2ad0a680fa37")
        self.assertEqual(G.text_sha1("Give enzyme X before the diet"), "fc43450e8618")

    def test_states(self):
        d = pathlib.Path(tempfile.mkdtemp())
        nodes = {"tx_a": {"role": "tx", "text": "Give X"}, "tx_b": {"role": "tx", "text": "Give Y"},
                 "m": {"role": "mech", "text": "z"}}
        (d / G.LEDGER).write_text(json.dumps({"verdicts": {"consistent": "", "partly": ""}, "entries": [
            {"node": "tx_a", "text": "Give X", "text_sha1": G.text_sha1("Give X"), "verdict": "consistent"},
            {"node": "tx_b", "text": "Give W", "text_sha1": G.text_sha1("Give W"), "verdict": "partly", "notes": "n"},
            {"node": "tx_c", "text": "", "text_sha1": "0", "verdict": "consistent"}]}))
        lines, counts = G.check(d, nodes)
        self.assertEqual(counts["ok"], 1)
        self.assertEqual(counts["stale"], 1)
        self.assertEqual(counts["orphan"], 1)
        self.assertEqual(counts["open"], 1)

    def test_worked_example_ledger_is_current(self):
        lines, counts = G.check(TOY)
        self.assertEqual(counts["ok"], 2, lines)
        self.assertEqual(counts["missing"] + counts["stale"] + counts["open"], 0, lines)


class Diff(unittest.TestCase):
    def geo(self, **over):
        g = {"nodes": {"a": {"x": 100, "y": 100, "w": 100, "h": 40, "role": "mech", "lines": ["Substrate", "rises"],
                             "bold": False, "cluster": "s"},
                       "b": {"x": 300, "y": 100, "w": 100, "h": 40, "role": "sign", "lines": ["Tiredness"], "bold": False,
                             "cluster": "s"}},
             "edges": [{"a": "a", "b": "b", "kind": "stim", "pts": [[150, 100], [250, 100]]}],
             "clusters": {"s": [20, 40, 400, 200]}, "key": [420, 40, 600, 200]}
        g = json.loads(json.dumps(g))
        for path, v in over.items():
            node, field = path.split("__")
            g["nodes"][node][field] = v
        return g

    def test_no_change(self):
        ch = D.compare(self.geo(), self.geo())
        self.assertIn("no changes in the geometry", "\n".join(D.summary(ch)))

    def test_changes(self):
        ch = D.compare(self.geo(), self.geo(b__x=320, a__lines=["Substrate", "falls"]))
        self.assertEqual([k for k, *_ in ch["moved"]], ["b"])
        self.assertEqual(ch["text"], [("a", "Substrate [-rises-] {+falls+}")])
