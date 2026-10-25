# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""anki_cards.py (no Anki involved): a synthetic case checks the card rules (grouping, diagnosis support,
treatment, ruled out, trivial skips, de-duplication, escaping, italics, story order, tag prefix); the
worked example gives well-formed rows. Writes only to temp folders."""
import pathlib
import tempfile
import types

import anki_cards
from casekit import NBSP, Content
from diagram_testkit import TOY

PREFIX = "Diagram::"          # diagram.anki.tag_prefix default


def fake_case(tmp):
    C = Content(bind_rules=[(r"\bX\. fictus", "X." + NBSP + "fictus")])
    C.n("r", "risk", "Inherited gene fault", "s1")
    C.n("m", "mech", "Enzyme <slow>", "s1")
    C.n("p", "mech", "Substrate rises", "s1")
    C.n("v", "sign", "Tiredness after exercise", "s1")
    C.n("i", "inv", "Tiredness after exercise on testing", "s2")
    C.n("e", "mech", "X. fictus in tissue", "s2")
    C.n("d", "dx", "Fictional deficiency", "s2")
    C.n("t", "tx", "Enzyme replacement", "s2")
    C.n("a", "anat", "Normal clearance", "s1")
    C.n("m2", "mech", "Substrate rises", "s2")      # same words as p: its card merges with p's
    C.n("w", "mech", "Tissue swells", "s2")
    C.e("r", "m"); C.e("m", "p"); C.e("m", "v"); C.e("v", "i"); C.e("e", "d"); C.e("v", "d")
    C.e("t", "e", "inh"); C.e("a", "m", "req"); C.e("p", "w"); C.e("m2", "w")
    C.pairs([("ev", "sign", "Normal diet", "x", "Dietary excess")], "s2")
    content = types.SimpleNamespace(NODES=C.nodes, EDGES=C.edges, CLUSTERS={"s1": "ONE", "s2": "TWO"},
                                    ITALIC=("X." + NBSP + "fictus",))
    return types.SimpleNamespace(content=content, meta={"stages": ["s2", "s1"]}, slug="case-0-test", dir=tmp)


def check_rows(path, slug):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:4] == ["#separator:tab", "#html:true", "#columns:Front\tBack\tTags", "#tags column:3"], lines[:4]
    fronts = set()
    for ln in lines[4:]:
        f, b, tags = ln.split("\t")
        assert f and b, ln
        assert tags.startswith(f"{PREFIX}{slug} {PREFIX}{slug}::"), tags
        assert f not in fronts, f"duplicate front {f}"
        fronts.add(f)
    return lines[4:]


def test_rules(tmp_path):
    case = fake_case(tmp_path)
    rows, skipped = anki_cards.cards(case)
    by = {}
    for typ, front, back in rows:
        by.setdefault(typ, []).append((front, back))
    assert [s[:2] for s in skipped] == [("v", "i")], skipped                  # finding -> finding
    causes = dict(by["cause"])
    assert causes["Enzyme &lt;slow&gt;<br><br>leads to …? (2)"] == \
        "<ul><li>Substrate rises</li><li>Tiredness after exercise</li></ul>"
    assert causes["Substrate rises<br><br>leads to …?"] == "Tissue swells"   # p and m2 merged into one card
    assert sum(1 for f in causes if f.startswith("Substrate rises")) == 1
    assert by["diagnosis"] == [("Fictional deficiency<br><br>What in this case supports it?",
                                "<ul><li><i>X. fictus</i> in tissue</li><li>Tiredness after exercise</li></ul>")], by["diagnosis"]
    assert by["treatment"] == [("<i>X. fictus</i> in tissue<br><br>Which treatment blocks it?", "Enzyme replacement")]
    assert by["ruled-out"] == [("Dietary excess<br><br>What in this case argues against it?", "Normal diet")]
    assert not any("Normal clearance" in f for f in causes), "req arrows make no card"
    # story order: case.json lists s2 first, so s2's cause cards come before s1's
    order = [f for t, f, _ in rows if t == "cause"]
    assert order[0].startswith("Substrate rises"), order      # m2 (s2) anchors the merged card, first in story order
    path = anki_cards.write(case)
    assert path == tmp_path / "deliver" / "case-0-test-cards.tsv"
    check_rows(path, case.slug)


def test_worked_example_cards():
    from case import Case
    case = Case(TOY)
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "cards.tsv"
        anki_cards.write(case, out)
        rows = check_rows(out, case.slug)
    assert rows
    types_seen = {r.split("\t")[2].split("::")[-1] for r in rows}
    assert types_seen <= set(anki_cards.TYPES), types_seen
    assert {"cause", "treatment", "ruled-out"} <= types_seen


def test_tag_prefix_from_the_config(tmp_path):
    import style
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text('[diagram.anki]\ntag_prefix = "Mine::"\n', encoding="utf-8", newline="\n")
    try:
        style.configure(str(cfg))
        case = fake_case(tmp_path)
        path = anki_cards.write(case, tmp_path / "c.tsv")
        assert all(line.split("\t")[2].startswith("Mine::case-0-test ") for line in path.read_text(encoding="utf-8").splitlines()[4:])
    finally:
        style.configure(None)
