#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generic text rules a case's content.py can import: italics for organism names, bind rules that
keep a number with its unit and a range in one piece, and a widow finder for the laid-out boxes.

    from textrules import auto_italic, unit_rules, range_rules
    _BIND = [*range_rules(), *unit_rules(), PLUS_RULE, ...]       # casekit.binder() takes these as they are
    ...nodes...
    ITALIC = auto_italic(NODES)                                   # after the nodes, so it sees bound text

    python3 textrules.py <case_dir>        # what the rules find in a case (reads it; edits nothing)

auto_italic(texts)
    Binomial names and genus names from a curated list of medically common genera (bacteria, fungi,
    protozoa, helminths), and their one-letter abbreviations ("S. aureus"). A genus
    followed by a lower-case epithet is one term ("Staphylococcus aureus"); "spp." and "sp." stay roman
    ("Klebsiella spp." italicises "Klebsiella" only), and so does a capitalised serovar ("Salmonella
    Typhi"). An abbreviation counts only when the letter is the initial of a listed genus and the
    epithet is a listed epithet or looks Latin (-i, -ae, -is, -us, -um, -a, -ensis ...) and is not a
    common English word. Terms are returned exactly as they appear in the texts (a bound "S.\u00a0aureus"
    keeps its no-break space), longest first, which is what core.set_italic() expects.
unit_rules()
    A number keeps its unit: the space between them becomes a no-break space, for the clinical
    units in UNITS (µmol/L, mmol/L, g/L, U/L, IU/L, mmHg, bpm, /min, %, kg, kg/m², cm, mm, mg, µg,
    mcg, mL, L, °C, s, min, h, days, weeks, months, years and more) and the ×10⁹/L forms, which are
    also kept whole ("16.0 × 10⁹/L").
range_rules()
    A range stays whole: "1 – 2 h" (spaces round a dash between numbers), "(normal 0–18)" and
    "(normal < 5)" (the word that introduces a reference value keeps it), "< 5" (a comparator keeps
    its number). "3.5–5.2 mmol/L" is whole once unit_rules() has bound the unit; core.wrap() breaks
    lines only at plain spaces, so a dash without spaces never breaks.
widows(geometry)
    Boxes whose last line is a lone number, or one short word: five characters or fewer, or
    narrower than 40 % of the box's widest line (measured in the geometry's face when the fonts are
    installed, else by characters). No-break spaces count as word breaks here, because "(normal 6)"
    reads as two words even though it cannot break.

Tests: tests/diagram/test_textrules.py in the lt-suite repository.
"""
from _legacy import read_legacy_text
import json
import pathlib
import re
import sys

ENGINE = pathlib.Path(__file__).resolve().parent
NBSP, NNBSP = "\u00a0", "\u202f"
_SP = "[ \u00a0\u202f]"          # a space as the texts may hold it (plain, no-break, narrow no-break)

# ------------------------------------------------------------------------------------------ italics
GENERA = (
    # bacteria
    "Acinetobacter", "Actinomyces", "Aeromonas", "Aggregatibacter", "Anaplasma", "Bacillus", "Bacteroides",
    "Bartonella", "Bordetella", "Borrelia", "Brucella", "Burkholderia", "Campylobacter", "Cardiobacterium",
    "Chlamydia", "Chlamydophila", "Citrobacter", "Clostridioides", "Clostridium", "Corynebacterium", "Coxiella",
    "Cutibacterium", "Ehrlichia", "Eikenella", "Enterobacter", "Enterococcus", "Escherichia", "Francisella",
    "Fusobacterium", "Gardnerella", "Haemophilus", "Helicobacter", "Kingella", "Klebsiella", "Lactobacillus",
    "Legionella", "Leptospira", "Listeria", "Moraxella", "Morganella", "Mycobacterium", "Mycoplasma", "Neisseria",
    "Nocardia", "Pasteurella", "Peptostreptococcus", "Prevotella", "Propionibacterium", "Proteus", "Providencia",
    "Pseudomonas", "Rickettsia", "Salmonella", "Serratia", "Shigella", "Staphylococcus", "Stenotrophomonas",
    "Streptococcus", "Treponema", "Tropheryma", "Ureaplasma", "Veillonella", "Vibrio", "Yersinia",
    # fungi
    "Aspergillus", "Blastomyces", "Candida", "Coccidioides", "Cryptococcus", "Histoplasma", "Malassezia", "Mucor",
    "Pneumocystis", "Rhizopus", "Sporothrix", "Trichophyton",
    # protozoa
    "Acanthamoeba", "Babesia", "Blastocystis", "Cryptosporidium", "Cyclospora", "Entamoeba", "Giardia",
    "Leishmania", "Naegleria", "Plasmodium", "Toxoplasma", "Trichomonas", "Trypanosoma",
    # helminths and ectoparasites
    "Ancylostoma", "Ascaris", "Clonorchis", "Echinococcus", "Enterobius", "Fasciola", "Necator", "Onchocerca",
    "Opisthorchis", "Pediculus", "Sarcoptes", "Schistosoma", "Strongyloides", "Taenia", "Toxocara", "Trichinella",
    "Trichuris", "Wuchereria",
)
EPITHETS = frozenset("""
    abscessus acnes actinomycetemcomitans aerogenes aeruginosa agalactiae albicans americanus anginosus anthracis
    asteroides auris aureus avium bancrofti baumannii bovis brucei burgdorferi burnetii canis capitis capsulatum
    catarrhalis cayetanensis cepacia cereus chaffeensis cholerae cloacae coli constellatus corrodens cruzi
    dermatitidis difficile diphtheriae dispar donovani ducreyi duodenale duodenalis dysenteriae dysgalactiae
    enterica enterocolitica epidermidis faecalis faecium falciparum flavus flexneri fowleri fragilis freundii
    fumigatus furfur gallolyticus gattii genitalium glabrata gondii gonorrhoeae granulomatis granulosus
    haematobium haemolyticus henselae hepatica histolytica hominis humanus hydrophila immitis influenzae intermedius
    interrogans intestinalis intracellulare israelii japonicum jejuni jirovecii kansasii kingae knowlesi koseri
    krusei lamblia leprae lugdunensis lumbricoides malariae maltophilia mansoni marcescens marinum melitensis
    meningitidis microti mirabilis mitis monocytogenes morganii multilocularis multocida mutans necrophorum
    neoformans niger nucleatum oralis ovale oxytoca pallidum parahaemolyticus parainfluenzae parapsilosis parvum
    perfringens pertussis pestis phagocytophilum pneumophila pneumoniae prowazekii pseudomallei pseudotuberculosis
    psittaci pylori pyogenes quintana rickettsii rubrum saginata salivarius sanguinis saprophyticus scabiei
    schenckii sinensis solium sonnei spiralis stercoralis stutzeri tetani trachomatis trichiura tropicalis
    tuberculosis tularensis typhi typhimurium ulcerans urealyticum vaginalis vermicularis vivax volvulus
    vulgaris vulnificus warneri whipplei
""".split())
# lower-case words that follow a genus or an initial in ordinary clinical prose and are not epithets
NOT_EPITHET = frozenset("""
    a about after against also an and are as at be been before but by can cause causes caused colonises colonize
    colonizes complex count counts culture cultures does each enters for from gets grows grew group grows has have
    in infection infections invades is isolate isolated isolates it its may more most multiplies no not of on or
    other others pneumonia positive negative resistance resistant sepsis septicaemia species strain strains such
    than that the then these this those to type was were which while with within without bacteraemia bacteremia
    area data extra idea media plus status versus minus thus bus gas virus focus bonus census corpus fetus genus
    nexus onus radius sinus stimulus syllabus uterus villus anus agenda alpha beta gamma delta via vista
    era dogma schema sigma stoma trauma asthma oedema edema angina anaemia anemia hernia mania insomnia
""".split())
_LATIN = re.compile(r"(?:ii|i|ae|is|us|um|a|es|ensis|oides|icus|ica|icum|ens)$")
_INITIALS = "".join(sorted({g[0] for g in GENERA}))


def _epithet_ok(word):
    w = word.lower()
    if w in EPITHETS:
        return True
    return word.islower() and len(word) >= 4 and w not in NOT_EPITHET and bool(_LATIN.search(w))


def _texts(texts):
    """(key, text) pairs from a dict of texts or node dicts, or from an iterable of strings."""
    if isinstance(texts, dict):
        for k, v in texts.items():
            yield k, (v.get("text", "") if isinstance(v, dict) else str(v))
    else:
        for i, t in enumerate(texts):
            yield i, (t.get("text", "") if isinstance(t, dict) else str(t))


_GENUS_RE = re.compile(r"\b(" + "|".join(GENERA) + r")\b(?:(" + _SP + r")([a-z][a-z-]+)\b)?")
_ABBR_RE = re.compile(r"(?<![\w.])([" + _INITIALS + r"])\.(" + _SP + r")([a-z][a-z-]+)\b")


def find_italic(texts):
    """Every organism name found: [(key, term, reason)], reason 'genus', 'binomial' or 'abbreviation'."""
    out = []
    for key, t in _texts(texts):
        for m in _GENUS_RE.finditer(t):
            if m.group(3) and _epithet_ok(m.group(3)) and m.group(3) not in ("spp", "sp"):
                out.append((key, m.group(0), "binomial"))
            else:
                out.append((key, m.group(1), "genus"))
        for m in _ABBR_RE.finditer(t):
            if _epithet_ok(m.group(3)):
                out.append((key, m.group(0), "abbreviation"))
    return out


def auto_italic(texts):
    """The terms to set in italic, unique, longest first (the order core.runs() needs)."""
    terms = {term for _, term, _ in find_italic(texts)}
    return tuple(sorted(terms, key=lambda s: (-len(s), s)))


# ------------------------------------------------------------------------------------------ units
UNITS = (
    # concentrations and activities
    "µmol/L", "μmol/L", "umol/L", "nmol/L", "pmol/L", "mmol/L", "mmol/mol", "mol/L", "mEq/L", "mOsm/kg",
    "g/L", "g/dL", "mg/L", "mg/dL", "µg/L", "μg/L", "ng/mL", "ng/L", "pg/mL", "U/L", "IU/L", "mU/L", "mIU/L",
    "kU/L", "units/mL",
    # pressures and rates
    "mmHg", "cmH₂O", "cmH2O", "kPa", "bpm", "beats/min", "breaths/min", "mL/min", "mL/kg/h", "mL/kg", "mL/h",
    "L/min", "mg/kg/day", "mg/kg", "µg/kg/min", "mm/h", "/min", "/day", "/week",
    # amounts, sizes, volumes
    "kg/m²", "kg/m2", "kg", "mg", "µg", "μg", "mcg", "ng", "g", "mL", "L", "fL", "pg", "cm", "mm", "µm", "μm",
    "units", "IU", "%", "°C",
    # time
    "seconds", "second", "sec", "s", "minutes", "minute", "mins", "min", "hours", "hour", "hrs", "hr", "h",
    "days", "day", "weeks", "week", "wk", "months", "month", "years", "year", "yr",
)
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
_TIMES10 = r"[×x]" + _SP + r"?10(?:\^9|⁹|\^12|¹²|\^6|⁶)" + _SP + r"?/L"


def _nbsp(m):
    """Every space in the match becomes a no-break space; nothing else changes."""
    return re.sub(_SP, NBSP, m.group(0))


def unit_rules():
    """bind() rules: a number keeps its unit, and a ×10⁹/L form stays whole."""
    return [
        (r"\d" + _SP + r"?" + _TIMES10, _nbsp),
        (r"(\d)" + _SP + r"(?=(?:" + _UNIT_ALT + r")(?![\w/²⁹]))", r"\1" + NBSP),
    ]


def range_rules():
    """bind() rules: a range, a reference value and a comparator stay with their numbers."""
    return [
        (r"\d(?:" + _SP + r"[–—-]" + _SP + r"?|[–—-]" + _SP + r")(?=\d)", _nbsp),
        (r"\b(normal|ref\.?|reference|range|target|ULN|LLN|under|over|above|below)" + _SP + r"(?=[<>≤≥]?" + _SP + r"?\d)",
         r"\1" + NBSP),
        (r"([<>≤≥])" + _SP + r"(?=\d)", r"\1" + NBSP),
    ]


# ------------------------------------------------------------------------------------------ widows
_NUM_ONLY = re.compile(r"^[(\[]?[<>≤≥~]?\d[\d.,/–-]*[)\],;:.%]*$")
SHORT_CHARS = 5          # a one-word last line this short is a widow
SHORT_SHARE = 0.40       # ... and so is one narrower than this share of the box's widest line


def _measure(geo):
    """A width function for the geometry's face (core.text_w), or None when the fonts are missing."""
    try:
        if str(ENGINE) not in sys.path:
            sys.path.insert(0, str(ENGINE))
        import core as E
        E.fonts_for_geometry(geo)
        return E.text_w
    except Exception:
        return None


def widows(geometry):
    """[(node id, last line, why)] for boxes of two or more lines; geometry is a dict or a path."""
    geo = geometry if isinstance(geometry, dict) else json.loads(read_legacy_text(pathlib.Path(geometry)))
    width = _measure(geo)
    out = []
    for nid, b in geo["nodes"].items():
        lines = b["lines"]
        if len(lines) < 2:
            continue
        last = lines[-1]
        words = re.split(_SP, last.strip())
        if _NUM_ONLY.match(last.replace(NBSP, "").replace(NNBSP, "")):
            out.append((nid, last, "lone number"))
            continue
        if len(words) != 1:
            continue
        word = words[0].strip(".,;:()")
        if len(word) <= SHORT_CHARS:
            out.append((nid, last, f"one word of {len(word)} characters"))
            continue
        if width is not None:
            share = width(last, b.get("bold", False)) / max(width(l, b.get("bold", False)) for l in lines)
        else:
            share = len(last) / max(len(l) for l in lines)
        if share < SHORT_SHARE:
            out.append((nid, last, f"one word, {share:.0%} of the widest line"))
    return out


# ------------------------------------------------------------------------------------------ report
def report(case_dir, trust=False):
    """What the rules would do on a case: italics against content.ITALIC, extra no-break spaces,
    widows in geometry.json. Returns the lines printed."""
    sys.dont_write_bytecode = True
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from case import Case
    from casekit import binder
    case = Case(case_dir, trust=trust)
    nodes = case.content.NODES
    out = [f"textrules on {case.dir}"]
    found = find_italic(nodes)
    terms = auto_italic(nodes)
    have = tuple(getattr(case.content, "ITALIC", ()))
    out.append(f"italics: auto_italic finds {len(terms)} term(s): " + (", ".join(repr(t) for t in terms) or "none"))
    for key, term, why in found:
        out.append(f"  {key}: {term!r} ({why})")
    missing, extra = [t for t in terms if t not in have], [t for t in have if t not in terms]
    out.append("  matches content.ITALIC" if not missing and not extra else
               f"  content.ITALIC lacks {missing!r}; has terms the rules do not find: {extra!r}")
    bind = binder([*range_rules(), *unit_rules()])
    changes = []
    for nid, nd in nodes.items():
        new = bind(nd["text"])
        if new != nd["text"]:
            changes.append((nid, nd["text"], new))
    out.append(f"units and ranges: {len(changes)} node text(s) would gain a no-break space beyond the case's own rules")
    for nid, old, new in changes:
        spots = [new[max(0, i - 12):i + 12].replace(NBSP, "⍽") for i, (a, b) in enumerate(zip(old, new)) if a != b]
        out.append(f"  {nid}: " + "; ".join(spots))
    geo = case.dir / "geometry.json"
    if geo.exists():
        w = widows(geo)
        out.append(f"widows: {len(w)}")
        for nid, last, why in w:
            out.append(f"  {nid}: last line {last!r} ({why})")
    else:
        out.append("widows: no geometry.json yet (run the layout first)")
    return out


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    import argparse
    ap = argparse.ArgumentParser(description="Report what auto_italic, unit_rules, range_rules and widows find in a case.")
    ap.add_argument("case_dir")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    from case import CaseError
    try:
        style.configure(a.config)
        print("\n".join(report(a.case_dir, trust=a.trust_case)))
    except (CaseError, style.StyleError) as ex:
        raise SystemExit(str(ex))


if __name__ == "__main__":
    import _bootstrap
    _bootstrap.entry()
    main(sys.argv[1:])
