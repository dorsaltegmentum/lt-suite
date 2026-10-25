#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fact check of a diagram's words against the case's own sources: every value, measurement,
percentage, dose and drug name on the boxes (and in the stage titles) should appear in the case
material the diagram rests on.

    python3 consistency.py <case_dir> [--out FILE]      # writes <case_dir>/consistency-report.md

The sources are case.json "sources", read-only:

    "sources": [{"label": "case notes", "path": "sources/case-notes.md"},
                {"label": "drafted answers", "path": "answers.json"}]

(a path may be absolute, start with ~, or be relative to the case folder; a JSON source is read as
all of its string values). Without "sources" the check reports that and stops.

Extract facts from node labels and stage headings:
  value        a known clinical label and its number: "K⁺ 4.1 mmol/L", "BMI about 24",
               "BP 120/80", "(normal 2–10)"; found when the number appears in a source within
               50 characters after the label (or a synonym: potassium for K⁺)
  measurement  a number with a unit and no known label: "9 mm", "1–2 h", "48 h", "5 months"; found
               when the number appears with the same unit (h, hr and hours count as one)
  percentage   "30 %"; found with its % sign
  drug         a name from the drug lexicon or with a drug stem (-pril, -olol, -sartan, -cillin, -mycin ...),
               found by name or by a synonym the locale uses (institution.locale: en-AU frusemide and
               furosemide, en-US acetaminophen and paracetamol; en takes every spelling)
Unlabelled numbers without units are counted separately. FOUND requires a value together with
its neighbouring label or unit. WEAK records a bare value match, while MISSING means no match
was found. The report supplies nearby source text for these two warning states.
"""
import argparse
import difflib
import hashlib
import json
import pathlib
import re
import sys

import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent
REPORT = "consistency-report.md"

LABELS = {   # label -> synonyms that may stand for it in a source (all lower case, normalised)
    "bp": ["bp", "blood pressure"], "hr": ["hr", "heart rate", "pulse"], "rr": ["rr", "respiratory rate", "resp"],
    "t": ["t", "temp", "temperature", "°c"], "spo2": ["spo2", "sats", "saturation"], "hb": ["hb", "haemoglobin", "hemoglobin"],
    "wcc": ["wcc", "wbc", "white cell", "white blood cell", "leucocyte"], "platelets": ["platelets", "plt", "platelet"],
    "inr": ["inr"], "aptt": ["aptt"], "pt": ["pt", "prothrombin"], "alt": ["alt"], "ast": ["ast"], "alp": ["alp", "alkaline"],
    "ggt": ["ggt", "gamma"], "bilirubin": ["bilirubin", "bili"], "albumin": ["albumin"], "lipase": ["lipase"],
    "amylase": ["amylase"], "crp": ["crp"], "esr": ["esr"], "na+": ["na+", "na ", "sodium"], "k+": ["k+", "k ", "potassium"],
    "cl-": ["cl-", "chloride"], "urea": ["urea"], "creatinine": ["creatinine", "cr "], "egfr": ["egfr"],
    "crcl": ["crcl", "creatinine clearance"], "glucose": ["glucose", "bgl"], "hba1c": ["hba1c"], "lactate": ["lactate"],
    "troponin": ["troponin", "trop"], "bnp": ["bnp"], "tsh": ["tsh"], "ferritin": ["ferritin"], "bmi": ["bmi"],
    "cbd": ["cbd", "common bile duct", "bile duct", "duct"], "age": ["age", "aged", "year-old", "year old", "yo"],
    "lvef": ["lvef", "ejection"], "mcv": ["mcv"], "ck": ["ck"], "ph": ["ph"], "pco2": ["pco2"], "po2": ["po2"],
    "hco3-": ["hco3", "bicarbonate"], "ratio": ["ratio"], "normal": ["normal", "range", "ref", "reference", "uln"],
}
FILLER = {"about", "of", "was", "is", "at", "approximately", "approx", "around", "~", "=", "only", "still"}
UNIT_SYN = [("µmol/l", "umol/l"), ("mmol/l",), ("g/l",), ("u/l", "iu/l"), ("mmhg",), ("bpm", "/min", "beats/min"),
            ("kg/m2", "kg/m²"), ("kg",), ("mm",), ("cm",), ("mg",), ("µg", "mcg", "ug"), ("ml",), ("l",), ("°c", "c"),
            ("%",), ("h", "hr", "hrs", "hour", "hours"), ("min", "mins", "minute", "minutes"), ("s", "sec", "seconds"),
            ("days", "day", "d"), ("weeks", "week", "wk", "wks"), ("months", "month", "mo"),
            ("years", "year", "yr", "yrs", "y", "-year-old", "year-old"), ("x10^9/l", "x 10^9/l", "x109/l"),
            ("ml/min",), ("mg/l",), ("ng/l",), ("units",)]
# Drug names on a box may be any of these; each group lists names that stand for the same drug. Names
# shared by every locale come first; LOCALE_SYNONYMS adds the spellings one locale uses
# (institution.locale: en takes them all).
DRUG_GROUPS = [
    ["metoprolol"], ["enalapril"], ["gentamicin", "gentamycin"], ["ampicillin"], ["benzylpenicillin", "penicillin g"],
    ["piperacillin-tazobactam", "piperacillin+tazobactam", "piperacillin/tazobactam", "piperacillin with tazobactam",
     "piperacillin and tazobactam", "piperacillin tazobactam", "pip-taz"],
    ["amoxicillin-clavulanate", "amoxicillin+clavulanate", "co-amoxiclav"],
    ["flucloxacillin"], ["ceftriaxone"], ["cefazolin"], ["metronidazole"], ["vancomycin"], ["meropenem"],
    ["ciprofloxacin"], ["doxycycline"], ["azithromycin"], ["trimethoprim"], ["nitrofurantoin"], ["clindamycin"],
    ["vitamin k", "phytomenadione", "phytonadione"], ["ibuprofen"], ["morphine"], ["oxycodone"], ["fentanyl"],
    ["tramadol"], ["ondansetron"], ["metoclopramide"], ["pantoprazole"], ["esomeprazole"], ["omeprazole"],
    ["ursodeoxycholic acid", "ursodiol"], ["warfarin"], ["heparin"], ["enoxaparin"], ["apixaban"], ["rivaroxaban"],
    ["dabigatran"], ["aspirin"], ["clopidogrel"], ["atorvastatin"], ["rosuvastatin"], ["amlodipine"], ["perindopril"],
    ["ramipril"], ["candesartan"], ["irbesartan"], ["spironolactone"], ["hydrochlorothiazide"], ["bisoprolol"],
    ["carvedilol"], ["digoxin"], ["amiodarone"], ["sotalol"], ["insulin"], ["metformin"], ["gliclazide"],
    ["prednisolone"], ["hydrocortisone"], ["dexamethasone"],
    ["potassium chloride", "kcl", "potassium tablets"], ["magnesium"], ["lactulose"], ["loperamide"], ["naloxone"],
]
LOCALE_SYNONYMS = {   # locale -> groups whose names are one drug in that locale's usage
    "en-AU": [["frusemide", "furosemide"], ["amoxicillin", "amoxycillin"], ["cephalexin", "cefalexin"],
              ["paracetamol"], ["adrenaline"], ["noradrenaline"], ["salbutamol"], ["glyceryl trinitrate", "gtn"],
              ["cholestyramine", "colestyramine"]],
    "en-GB": [["furosemide", "frusemide"], ["amoxicillin"], ["cefalexin", "cephalexin"], ["paracetamol"],
              ["adrenaline", "epinephrine"], ["noradrenaline", "norepinephrine"], ["salbutamol"],
              ["glyceryl trinitrate", "gtn"], ["colestyramine", "cholestyramine"]],
    "en-US": [["furosemide"], ["amoxicillin"], ["cephalexin"], ["acetaminophen", "paracetamol"],
              ["epinephrine", "adrenaline"], ["norepinephrine", "noradrenaline"], ["albuterol", "salbutamol"],
              ["nitroglycerin", "glyceryl trinitrate"], ["cholestyramine"]],
}


def drug_lexicon(locale=None):
    """name -> synonyms (lower case) for the locale; "en" (or an unknown locale) merges every locale's groups."""
    if locale is None:
        try:
            import style
            locale = style.get("institution.locale", "en")
        except Exception:  # noqa: BLE001  (no config: every spelling)
            locale = "en"
    groups = [list(g) for g in DRUG_GROUPS]
    if locale in LOCALE_SYNONYMS:
        groups += LOCALE_SYNONYMS[locale]
    else:
        merged = {}
        for loc_groups in LOCALE_SYNONYMS.values():
            for g in loc_groups:
                key = next((k for k in merged if set(merged[k]) & set(g)), None)
                if key is None:
                    merged[g[0]] = list(g)
                else:
                    merged[key] += [n for n in g if n not in merged[key]]
        groups += list(merged.values())
    out = {}
    for g in groups:
        for name in g:
            out.setdefault(name, [])
            out[name] += [n for n in g if n != name and n not in out[name]]
    return out


DRUGS = drug_lexicon("en")    # every spelling; run() switches to the configured locale's lexicon
DRUG_STEM = re.compile(r"\b[a-z]{3,}(?:pril|sartan|olol|dipine|semide|thiazide|statin|parin|xaban|gatran|prazole|tidine|"
                       r"cillin|mycin|micin|floxacin|cycline|penem|conazole|nidazole|vir|mab|setron|lukast|gliptin|"
                       r"gliflozin|formin|dronate|oxetine|azepam|azolam|profen|coxib|fenac)\b")
NOT_DRUG = {"cholesterol"}


def norm(s, lower=True):
    """Plain spaces and hyphens, straight quotes, ASCII-ish units and superscripts; lower case unless lower=False."""
    table = {"\u00a0": " ", "\u202f": " ", "\u2009": " ", "–": "-", "—": "-", "−": "-", "μ": "µ", "×": "x",
             "’": "'", "‘": "'", "“": '"', "”": '"', "⁺": "+", "⁻": "-", "²": "2", "³": "3", "⁹": "9", "¹": "1", "⁰": "0"}
    s = "".join(table.get(c, c) for c in s)
    s = s.lower() if lower else s
    s = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", s)
    s = re.sub(r"[xX]\s*10\s*\^?\s*9\s*/\s*[lL]", "x10^9/l" if lower else "x10^9/L", s)
    return re.sub(r"\s+", " ", s)


def _units():
    from textrules import UNITS
    us = {norm(u) for u in UNITS} | {u for grp in UNIT_SYN for u in grp}
    return sorted(us, key=len, reverse=True)


NUM = re.compile(r"(?<![\w.+\-/^])(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?(?:/\d+(?:\.\d+)?)?)(?![\w.]*\d)(?!\w)")


def facts(text, where, units):
    """The checkable facts in one text: [dict(where, kind, shown, num, label, unit)]."""
    shown_t = norm(text, lower=False)
    t = shown_t.lower()
    if len(t) != len(shown_t):
        shown_t = t
    out, skipped, parent = [], 0, None
    unit_re = re.compile(r"\s?(" + "|".join(re.escape(u) for u in units) + r")(?![\w/])")
    for m in NUM.finditer(t):
        num = m.group(1)
        um = unit_re.match(t, m.end())
        unit = um.group(1) if um else None
        before = re.split(r"[,;:(]", t[max(0, m.start() - 40):m.start()])[-1].split()
        words = [w for w in before if w not in FILLER][-3:]
        label = None
        for k in range(len(words)):
            cand = " ".join(words[k:])
            if cand in LABELS:
                label = cand
                break
        if label is None and words and words[-1] in LABELS:
            label = words[-1]
        if "(normal" in t[max(0, m.start() - 9):m.start()]:
            label = "normal"
        end = um.end() if um else m.end()
        a = m.start()
        if label:
            k = t.rfind(label.split()[0], max(0, m.start() - 40), m.start())
            a = k if k >= 0 else a
        shown = shown_t[a:end].strip()
        if label:
            out.append(dict(where=where, kind="value", shown=shown, num=num, label=label, unit=unit,
                            parent=parent if label == "normal" else None))
            if label != "normal":
                parent = label
        elif unit == "%":
            out.append(dict(where=where, kind="percentage", shown=shown, num=num, label=None, unit="%"))
        elif unit:
            out.append(dict(where=where, kind="measurement", shown=shown, num=num, label=None, unit=unit))
        else:
            skipped += 1
    seen = set()
    for name in sorted(DRUGS, key=len, reverse=True):
        for m in re.finditer(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", t):
            if not any(a <= m.start() < b for a, b in seen):
                seen.add((m.start(), m.end()))
                out.append(dict(where=where, kind="drug", shown=name, num=None, label=None, unit=None))
    for m in DRUG_STEM.finditer(t):
        if m.group(0) not in NOT_DRUG and not any(a <= m.start() < b for a, b in seen):
            seen.add((m.start(), m.end()))
            out.append(dict(where=where, kind="drug", shown=m.group(0), num=None, label=None, unit=None))
    return out, skipped


def _unit_group(u):
    for grp in UNIT_SYN:
        if u in grp:
            return grp
    return (u,)


class Source:
    """A source's text normalised twice: .low (lower case, searched) and .disp (case kept, quoted)."""

    def __init__(self, label, text):
        self.label, self.disp = label, norm(text, lower=False)
        self.low = self.disp.lower()
        if len(self.low) != len(self.disp):
            self.disp = self.low

    def quote(self, i, n, pad=45):
        a, b = max(0, i - pad), min(len(self.disp), i + n + pad)
        q = ("…" if a else "") + self.disp[a:b].replace("\n", " ").replace("|", "/").strip() + ("…" if b < len(self.disp) else "")
        return f'{self.label}: "{q}"'


def find(f, sources):
    """(status, where it was found, or the closest match)."""
    if f["kind"] == "drug":
        for n in [f["shown"].lower()] + DRUGS.get(f["shown"].lower(), []):
            for src in sources:
                m = re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", src.low)
                if m:
                    return "FOUND", src.quote(m.start(), len(n))
        return "MISSING", _closest(f["shown"].lower(), sources)
    pat = re.compile(r"(?<![\d.])" + re.escape(f["num"]) + r"(?![\d]|\.\d)")
    hits = [(src, m) for src in sources for m in pat.finditer(src.low)]
    if not hits:
        return "MISSING", _closest(f["shown"].lower(), sources, label=f.get("parent") or f["label"],
                                   reference=bool(f.get("parent")))
    if f["kind"] == "value":
        syn = [re.compile(r"(?<![a-z])" + re.escape(x.strip()) + r"(?![a-z])") for x in LABELS.get(f["label"], [f["label"]])]
        for src, m in hits:
            # the label within 50 characters before the number (or 15 after), with no other number between them
            pre, post = src.low[max(0, m.start() - 50):m.start()], src.low[m.end():m.end() + 15]
            near = any(not re.search(r"\d", pre[x.end():]) for r in syn for x in r.finditer(pre)) or \
                any(not re.search(r"\d", post[:x.start()]) for r in syn for x in r.finditer(post))
            if near:
                return "FOUND", src.quote(m.start(), m.end() - m.start())
    else:
        grp = _unit_group(f["unit"])
        for src, m in hits:
            after = src.low[m.end():m.end() + 12]
            if any(re.match(r"\s?" + re.escape(u) + r"(?![a-z])", after) for u in grp):
                return "FOUND", src.quote(m.start(), m.end() - m.start())
    src, m = hits[0]
    return "WEAK", src.quote(m.start(), m.end() - m.start()) + \
        f" (the number, not beside its {'label' if f['kind'] == 'value' else 'unit'})"


def _closest(shown, sources, label=None, reference=False):
    if label and reference:      # a reference range: where the source gives the test's own limits, if anywhere
        for src in sources:
            for x in LABELS.get(label, [label]):
                m = re.search(r"(?<![a-z])" + re.escape(x.strip()) + r"(?![a-z])[^.;]{0,80}?(normal|upper limit|range|uln)",
                              src.low)
                if m:
                    return src.quote(m.start(), m.end() - m.start()) + f" ({label} and its limits)"
    if label:
        for src in sources:
            for x in LABELS.get(label, [label]):
                m = re.search(r"(?<![a-z])" + re.escape(x.strip()) + r"(?![a-z])", src.low)
                if m:
                    return src.quote(m.start(), m.end() - m.start()) + " (its label)"
    n = max(1, len(shown.split()))
    best = (0.0, None)
    for src in sources:
        toks = src.disp.split()
        cands = {" ".join(toks[i:i + n]) for i in range(0, max(1, len(toks) - n + 1))}
        for c in difflib.get_close_matches(shown, cands, n=1, cutoff=0.4):
            r = difflib.SequenceMatcher(None, shown, c.lower()).ratio()
            if r > best[0]:
                best = (r, f'{src.label}: "{c}" ({r:.0%} alike)')
    return best[1] or "nothing close"


def read_source(path):
    p = pathlib.Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        out = []

        def walk(x):
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(json.loads(raw))
        return "\n".join(out), raw
    return raw, raw


def sources_of(case_dir, meta):
    src = meta.get("sources")
    if not src:
        return None
    items = src.items() if isinstance(src, dict) else [(s.get("label") or s["path"], s["path"]) for s in src]
    out = []
    for label, path in items:
        p = pathlib.Path(path).expanduser()
        if not p.is_absolute():
            p = (pathlib.Path(case_dir) / p).resolve()
        out.append((label, p))
    return out


def run(case_dir, out=None, quiet=False, trust=False):
    """Returns dict(lines, counts, report path or None)."""
    sys.dont_write_bytecode = True
    from case import Case
    global DRUGS
    case = Case(case_dir, trust=trust) if isinstance(case_dir, (str, pathlib.Path)) else case_dir
    DRUGS = drug_lexicon()
    lines = []
    srcs = sources_of(case.dir, case.meta)
    if not srcs:
        lines.append('consistency: case.json has no "sources"; nothing checked')
        if not quiet:
            print("\n".join(lines))
        return dict(lines=lines, counts={}, report=None)
    loaded, meta = [], []
    for label, p in srcs:
        if not p.exists():
            lines.append(f"consistency: source {label!r} not found at {p}")
            continue
        text, raw = read_source(p)
        loaded.append(Source(label, text))
        meta.append((label, p, len(raw), hashlib.sha256(raw.encode()).hexdigest()[:12]))
    if not loaded:
        lines.append("consistency: none of the sources could be read; nothing checked")
        if not quiet:
            print("\n".join(lines))
        return dict(lines=lines, counts={}, report=None)
    units = _units()
    allf, skipped = [], 0
    for nid, nd in case.content.NODES.items():
        fs, sk = facts(nd["text"], nid, units)
        allf += fs
        skipped += sk
    for cid, title in case.content.CLUSTERS.items():
        fs, sk = facts(title, f"stage {cid}", units)
        allf += fs
        skipped += sk
    res = [(f, *find(f, loaded)) for f in allf]
    counts = {s: sum(1 for _, st, _ in res if st == s) for s in ("FOUND", "WEAK", "MISSING")}
    warn = [(f, st, m) for f, st, m in res if st != "FOUND"]
    lines.append(f"consistency: {len(allf)} facts from {len(case.content.NODES)} boxes and {len(case.content.CLUSTERS)} "
                 f"stage titles against {', '.join(l for l, *_ in meta)}: {counts['FOUND']} found, {counts['WEAK']} weak, "
                 f"{counts['MISSING']} missing ({skipped} numbers without label or unit not checked)")
    for f, st, m in warn:
        lines.append(f"  {st:7s} {f['where']}: {f['shown']!r} ({f['kind']}); closest: {m}")
    rp = pathlib.Path(out) if out else case.dir / REPORT
    md = [f"# Consistency check: {case.meta.get('title', case.dir.name)}", "",
          f"Every value, measurement, percentage, dose and drug name on the diagram, looked up in the case's sources "
          f"by `consistency.py`. Warnings only: a fact the sources do not show may still be right, but it needs a "
          f"source.", "", "## Sources", "", "| Label | File | Size | sha256 |", "|---|---|---|---|"]
    md += [f"| {l} | `{p}` | {n} bytes | `{h}` |" for l, p, n, h in meta]
    md += ["", "## Summary", "",
           f"{len(allf)} facts: {counts['FOUND']} found, {counts['WEAK']} weak (the number is there, not beside its label "
           f"or unit), {counts['MISSING']} missing. {skipped} numbers with neither a label nor a unit were not checked.", ""]
    md += ["## Warnings", ""]
    if warn:
        md += ["| Box or stage | Fact | Kind | Status | Closest match in the sources |", "|---|---|---|---|---|"]
        md += [f"| `{f['where']}` | {f['shown']} | {f['kind']} | {st} | {m} |" for f, st, m in warn]
    else:
        md.append("None.")
    md += ["", "## Found", "", "| Box or stage | Fact | Kind | Where |", "|---|---|---|---|"]
    md += [f"| `{f['where']}` | {f['shown']} | {f['kind']} | {m} |" for f, st, m in res if st == "FOUND"]
    rp.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    lines.append(f"consistency: report written to {rp}")
    if not quiet:
        print("\n".join(lines))
    return dict(lines=lines, counts=counts, report=rp)


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Check a diagram's facts against the case's sources (warnings only).")
    ap.add_argument("case_dir")
    ap.add_argument("--out", help=f"report path (default: <case_dir>/{REPORT})")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    from case import CaseError
    try:
        style.configure(a.config)
        run(a.case_dir, a.out, trust=a.trust_case)
    except (CaseError, style.StyleError) as ex:
        raise SystemExit(str(ex))
    return 0


if __name__ == "__main__":
    _bootstrap.entry()
    sys.exit(main(sys.argv[1:]))
