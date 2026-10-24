# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""textrules.py: automatic italics for organism names, values bound to their units, ranges kept whole,
and widow detection."""
import unittest

import textrules as T
from casekit import binder

NB = "\u00a0"


def bind(text):
    return binder([*T.range_rules(), *T.unit_rules()])(text)


class Italic(unittest.TestCase):
    def test_abbreviation_plain_and_bound(self):
        self.assertEqual(T.auto_italic(["Stool E. coli count"]), ("E. coli",))
        self.assertEqual(T.auto_italic(["E." + NB + "coli in a culture"]), ("E." + NB + "coli",))

    def test_binomial_is_one_term(self):
        self.assertEqual(T.auto_italic(["Escherichia coli and Staphylococcus aureus"]),
                         ("Staphylococcus aureus", "Escherichia coli"))

    def test_genus_alone_and_spp(self):
        self.assertEqual(T.auto_italic(["Klebsiella spp. and Candida infection"]), ("Klebsiella", "Candida"))

    def test_serovar_stays_roman(self):
        self.assertEqual(T.auto_italic(["Salmonella Typhi"]), ("Salmonella",))

    def test_unknown_latin_epithet(self):
        # not in EPITHETS, but Latin-looking after a listed genus initial
        self.assertIn("S. capitis", T.auto_italic(["S. capitis grew"]))
        self.assertIn("P. novellii", T.auto_italic(["P. novellii"]))

    def test_no_false_positives(self):
        for t in ("Vitamin K. then", "Group A. the", "Hb 140", "IL-6 and TNF-α", "E. this was", "Dr. smith",
                  "S. is", "Plan A. data"):
            self.assertEqual(T.auto_italic([t]), (), t)

    def test_dict_of_nodes(self):
        nodes = {"a": {"text": "H. pylori gastritis"}, "b": {"text": "no organism"}}
        self.assertEqual(T.auto_italic(nodes), ("H. pylori",))
        self.assertEqual(T.find_italic(nodes), [("a", "H. pylori", "abbreviation")])

    def test_longest_first(self):
        terms = T.auto_italic(["Mycobacterium tuberculosis; M. tuberculosis; Mycobacterium"])
        self.assertEqual(terms, ("Mycobacterium tuberculosis", "M. tuberculosis", "Mycobacterium"))


class Units(unittest.TestCase):
    def test_common_units(self):
        for text in ("Substrate S 48 µmol/L", "K 4.1 mmol/L", "albumin 40 g/L", "ALT 60 U/L", "CK 200 IU/L",
                     "BP 120/80 mmHg", "HR 72 bpm", "RR 16 /min", "SpO2 98 %", "weight 70 kg",
                     "BMI 24 kg/m2", "BMI 24 kg/m²", "lesion 6 mm", "mass 2 cm", "drug X 5 mg/kg",
                     "dose 250 mg", "B12 40 µg", "vitamin K 10 mcg", "infuse 250 mL", "give 1 L",
                     "T 36.8 °C", "for 20 s", "for 4 min", "within 24 h", "for 2 days", "3 weeks",
                     "4 months ago", "12 years", "CrCl 90 mL/min"):
            out = bind(text)
            self.assertIn(NB, out, text)
            self.assertEqual(out.replace(NB, " "), text, text)

    def test_times_ten_forms(self):
        for text, want in (("WCC 7.5 ×10⁹/L", "WCC 7.5" + NB + "×10⁹/L"),
                           ("WCC 7.5 × 10⁹/L", "WCC 7.5" + NB + "×" + NB + "10⁹/L"),
                           ("platelets 250 x10^9/L", "platelets 250" + NB + "x10^9/L"),
                           ("WCC 7.5×10⁹/L", "WCC 7.5×10⁹/L")):
            self.assertEqual(bind(text), want, text)

    def test_no_unit_no_bind(self):
        for text in ("Age 34 and well", "IL-6 and TNF", "3 siblings", "2 hours later", "type 2 diabetes"):
            out = bind(text)
            if text == "2 hours later":
                self.assertEqual(out, "2" + NB + "hours later")
            else:
                self.assertNotIn(NB, out, text)

    def test_idempotent(self):
        once = bind("Substrate S 48 µmol/L (normal 2 – 10)")
        self.assertEqual(bind(once), once)


class Ranges(unittest.TestCase):
    def test_ranges_stay_whole(self):
        self.assertEqual(bind("tiredness 1–2 h after exercise"), "tiredness 1–2" + NB + "h after exercise")
        self.assertEqual(bind("(normal 2–10)"), "(normal" + NB + "2–10)")
        self.assertEqual(bind("K 3.5–5.2 mmol/L"), "K 3.5–5.2" + NB + "mmol/L")
        self.assertEqual(bind("1 – 2 h"), "1" + NB + "–" + NB + "2" + NB + "h")
        self.assertEqual(bind("1 -2 h"), "1" + NB + "-2" + NB + "h")

    def test_reference_and_comparator(self):
        self.assertEqual(bind("amylase 40 (normal < 100)"), "amylase 40 (normal" + NB + "<" + NB + "100)")
        self.assertEqual(bind("ratio 0.5, under 2"), "ratio 0.5, under" + NB + "2")
        self.assertEqual(bind("eGFR > 60"), "eGFR >" + NB + "60")

    def test_words_are_not_ranges(self):
        self.assertEqual(bind("Lewis - type"), "Lewis - type")
        self.assertEqual(bind("normal levels"), "normal levels")


class Widows(unittest.TestCase):
    @staticmethod
    def geo(**nodes):
        return {"font": "none", "nodes": {k: {"lines": v, "bold": False} for k, v in nodes.items()}}

    def test_flags(self):
        g = self.geo(a=["Substrate S builds up in", "the tissue"],       # ends with two words, fine
                     b=["Substrate S builds up in the", "skin"],         # one short word
                     c=["BMI about", "24"],                              # lone number
                     d=["Fictional", "deficiency"],                      # balanced two-liner
                     e=["One line only"],
                     f=["Scan: lesion" + NB + "6" + NB + "mm", "(normal" + NB + "4)"],   # two words, bound
                     g=["Signals travel slowly along the sensory", "nerves"])       # 6 chars, about 15 % wide
        got = {k: why for k, _, why in T.widows(g)}
        self.assertEqual(set(got), {"b", "c", "g"})
        self.assertEqual(got["c"], "lone number")
        self.assertTrue(got["b"].startswith("one word of"))
        self.assertIn("of the widest line", got["g"])

