#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Study cards from a case's diagram, as a tab-separated file Anki can import (optional; run only on request).

    python3 anki_cards.py <case_dir> [--out PATH] [--verbose]

Writes <case_dir>/deliver/<slug>-cards.tsv. Importing cards is a separate, optional step.
The build never runs this. Every card uses the diagram's own boxes and arrows.
Cards are drafts; run the optional cleaners configured in [[text_cleaners]] before delivery.

Cards, one per question (several answers share one card rather than making cards with the same front):
  cause       'stim' arrows: front  "<cause box>  leads to …?"           back  the box(es) it leads to
  diagnosis   'stim' arrows into a diagnosis box: front "<diagnosis>  What in this case supports it?"
              back  the findings that point to it (a finding supports a diagnosis rather than causing it)
  treatment   'inh' arrows: front  "<box>  Which treatment blocks it?"   back  the treatment(s)
  ruled-out   'excl' arrows (RULED OUT): front "<diagnosis>  What in this case argues against it?"
              back  the evidence
Skipped as trivial: a finding leading to a finding (a sign confirmed by a test),
and an arrow between two boxes that say nearly the same thing (most content words shared).
Cards are de-duplicated on their front; identical answers are merged.

File: Anki's plain-text import with a header (#separator:tab, #html:true, #columns, #tags column), then
Front, Back, Tags. Text is HTML-escaped, the case's ITALIC terms (binomial names) are in <i>, several answers
are a <ul>. Tags identify the case and card type: <prefix><slug> and <prefix><slug>::<type>, with
the prefix from diagram.anki.tag_prefix (default "Diagram::"). Order: story order of the stages (case.json
"stages", else CLUSTERS), then the order content.py declares the boxes.
"""
import argparse, html, pathlib, re, sys

import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent
sys.dont_write_bytecode = True
from casekit import NBSP

FINDINGS = {"sign", "inv"}
TYPES = ("cause", "diagnosis", "treatment", "ruled-out")
QUESTION = {"cause": "leads to …?", "diagnosis": "What in this case supports it?",
            "treatment": "Which treatment blocks it?", "ruled-out": "What in this case argues against it?"}
WORD = re.compile(r"[a-z0-9⁺]+")
STOP = {"with", "from", "into", "that", "this", "than", "more", "less", "they", "them", "their", "after",
        "before", "about", "over", "under", "some", "still", "only", "also", "case"}


def words(text):
    return {w for w in WORD.findall(text.lower()) if len(w) >= 4 and w not in STOP}


def similar(a, b, cut=0.6):
    """Two box texts that share most of their content words (Jaccard >= cut)."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= cut


def cell(text, terms):
    """One field: escaped, single line, the italic terms in <i>."""
    out = html.escape(re.sub(r"\s+", " ", text.replace(NBSP, " ")).strip(), quote=False)
    for term in terms:
        plain = html.escape(term.replace(NBSP, " "), quote=False)
        out = out.replace(plain, f"<i>{plain}</i>")
    return out


def answers(items):
    return items[0] if len(items) == 1 else "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def cards(case, verbose=False):
    """[(type, front, back)] for the case, in story order, de-duplicated; trivial arrows skipped."""
    from make_artifact import story_stages
    C = case.content
    nodes, terms = C.NODES, getattr(C, "ITALIC", ())
    rank = {sid: i for i, (sid, _n) in enumerate(story_stages(case))}
    decl = {nid: i for i, nid in enumerate(nodes)}
    order = lambda nid: (rank.get(nodes[nid]["cluster"], len(rank)), decl[nid])
    text = lambda nid: nodes[nid]["text"].replace(NBSP, " ")
    groups = {}          # (type, anchor box) -> [answer boxes]
    skipped = []
    for e in C.EDGES:
        a, b, kind = e["a"], e["b"], e["kind"]
        ra, rb = nodes[a]["role"], nodes[b]["role"]
        if kind == "stim" and rb == "dx":
            key, ans = ("diagnosis", b), a
        elif kind == "stim":
            if ra in FINDINGS and rb in FINDINGS:
                skipped.append((a, b, "a finding leading to a finding"))
                continue
            key, ans = ("cause", a), b
        elif kind == "inh":
            key, ans = ("treatment", b), a
        elif kind == "excl":
            key, ans = ("ruled-out", b), a
        else:                # 'req': normal anatomy a mechanism depends on; no card
            continue
        if similar(text(a), text(b)):
            skipped.append((a, b, "the two boxes say nearly the same"))
            continue
        lst = groups.setdefault(key, [])
        if ans not in lst:
            lst.append(ans)
    out, fronts = [], {}
    for (typ, anchor) in sorted(groups, key=lambda k: (TYPES.index(k[0]), order(k[1]))):
        ans = sorted(groups[(typ, anchor)], key=order)
        q = QUESTION[typ] + (f" ({len(ans)})" if len(ans) > 1 and typ == "cause" else "")
        front = f"{cell(text(anchor), terms)}<br><br>{q}"
        backs = [cell(text(x), terms) for x in ans]
        norm = re.sub(r"\s+", " ", front.lower())
        if norm in fronts:                       # two boxes with the same words: one card, answers merged
            i = fronts[norm]
            merged = out[i][3] + [b for b in backs if b not in out[i][3]]
            out[i] = (out[i][0], out[i][1], out[i][2], merged)
            continue
        fronts[norm] = len(out)
        out.append((typ, anchor, front, backs))
    if verbose:
        for a, b, why in skipped:
            print(f"skipped {a} -> {b}: {why}")
    return [(typ, front, answers(backs)) for typ, _anchor, front, backs in out], skipped


def tag_prefix():
    """The cards' tag prefix: diagram.anki.tag_prefix (default "Diagram::")."""
    try:
        import style
        return style.get("diagram.anki.tag_prefix", "Diagram::")
    except Exception:  # noqa: BLE001
        return "Diagram::"


def write(case, out=None, verbose=False):
    rows, skipped = cards(case, verbose)
    slug = case.slug
    prefix = tag_prefix()
    path = pathlib.Path(out) if out else case.dir / "deliver" / f"{slug}-cards.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#separator:tab", "#html:true", "#columns:Front\tBack\tTags", "#tags column:3"]
    for typ, front, back in rows:
        for field in (front, back):
            if "\t" in field or "\n" in field:
                raise ValueError("a card field holds a tab or a line break")
        lines.append(f"{front}\t{back}\t{prefix}{slug} {prefix}{slug}::{typ}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    counts = {t: sum(1 for r in rows if r[0] == t) for t in TYPES}
    print(f"wrote {path}: {len(rows)} cards ({', '.join(f'{n} {t}' for t, n in counts.items())}); "
          f"{len(skipped)} trivial arrows skipped")
    return path


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    from case import Case, CaseError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("case_dir")
    ap.add_argument("--out", help="where to write the .tsv (default <case_dir>/deliver/<slug>-cards.tsv)")
    ap.add_argument("--verbose", action="store_true", help="list the arrows skipped as trivial")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
        write(Case(a.case_dir, trust=a.trust_case), a.out, a.verbose)
    except (CaseError, ValueError, style.StyleError) as ex:
        raise SystemExit(str(ex))


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
