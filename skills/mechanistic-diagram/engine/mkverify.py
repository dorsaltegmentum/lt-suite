# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The in-browser check: draw.io's own Graph lays out <slug>.drawio; compare its routes and labels
with geometry.json.

    python3 mkverify.py <case_dir>      -> $TMPDIR/verify.js (or the case folder without TMPDIR)

Run verify.js in an app.diagrams.net tab with any browser tool that can evaluate JavaScript (or the
devtools console): it loads the label fonts (from Google Fonts with fonts.page_fonts = "google";
otherwise the faces must be installed on the machine), re-lays the file with draw.io's engine and
returns {edgesOk, edgesBad, labelsOk, labelsBad, ...}. Every connector must match its geometry.json
route and every label its line count. case.json's verify_probe names the box whose computed face,
weight and size are reported (default: the first box that is not bold).
"""
import json, zlib, base64, urllib.parse, pathlib, os, sys

import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)
from _legacy import read_legacy_text

ENGINE = pathlib.Path(__file__).resolve().parent


def write(case, dest=None):
    """Write verify.js; returns its path."""
    xml = read_legacy_text(case.dir / f"{case.slug}.drawio")
    enc = urllib.parse.quote(xml, safe="!*'()")
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    b64 = base64.b64encode(co.compress(enc.encode("ascii")) + co.flush()).decode()
    g = json.loads(read_legacy_text(case.dir / "geometry.json"))
    exp = {}
    import math, drawio, style, themes
    import core as E
    E.fonts_for_geometry(g)
    drawio.use_theme(themes.print_palette())
    T = themes.THEMES[themes.print_palette()]
    sans, mono, w0, w1, fs = T["sans"], T["mono"], E.WGHT[0], E.WGHT[1], E.FS
    href = ""
    if style.page_fonts() == "google":
        import fontset
        href = fontset.google_css_url()
    faces = [f"{w0} {fs}px '{sans}'", f"{w1} {fs}px '{sans}'", f"italic {w0} {fs}px '{sans}'",
             f"italic {w1} {fs}px '{sans}'", f"700 {fs}px '{mono}'"]
    for i, ed in enumerate(g["edges"]):     # the last point where the .drawio puts it: moved back by drawio.entry_back
        pts = [tuple(p) for p in ed["pts"]]
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        L = math.hypot(x2 - x1, y2 - y1) or 1.0
        back = drawio.entry_back(drawio.EDGE[ed["kind"]][2], g["nodes"][ed["b"]]["bold"])
        pts[-1] = (x2 - (x2 - x1) / L * back, y2 - (y2 - y1) / L * back)
        exp[f"e{i:03d}_{ed['a']}__{ed['b']}"] = [[round(x, 1), round(y, 1)] for x, y in pts]
    lines = {k: len(v["lines"]) for k, v in g["nodes"].items()}
    LH = g.get("lh", 13.2)
    js = """(async () => {
const B64 = %s; const EXP = %s; const LINES = %s; const LH = %s; const PROBE = %s; const HREF = %s; const FACES = %s;
// load the label faces first, as draw.io would from each style's fontSource, so line counts are measured in them
if (HREF) {
  const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = HREF;
  document.head.appendChild(link);
  await new Promise(r => { link.onload = r; link.onerror = r; setTimeout(r, 8000); });
}
await Promise.all(FACES.map(f => document.fonts.load(f)));
const fontOk = document.fonts.check(FACES[0]) && document.fonts.check(FACES[1]);
const xml = Graph.decompress(B64);
const doc = mxUtils.parseXml(xml);
const mnode = doc.documentElement.getElementsByTagName('mxGraphModel')[0];
let div = document.getElementById('__verify'); if (div) div.remove();
div = document.createElement('div'); div.id = '__verify';
div.style.cssText = 'position:absolute;left:0;top:0;width:3200px;height:2400px;overflow:hidden;opacity:0.01;z-index:-1';
document.body.appendChild(div);
const g = new Graph(div); g.view.scale = 1;
new mxCodec(mnode.ownerDocument).decode(mnode, g.getModel()); g.refresh();
const model = g.getModel(); const bad = []; let okE = 0, okV = 0; const wrap = [];
for (const id in EXP) {
  const st = g.view.getState(model.getCell(id));
  if (!st) { bad.push(id + ' missing'); continue; }
  const got = (st.absolutePoints || []).map(p => [Math.round(p.x*10)/10, Math.round(p.y*10)/10]);
  const want = EXP[id];
  let same = got.length === want.length;
  // EXP's last point is where the .drawio puts it: the border point moved back by drawio.entry_back
  for (let i = 0; same && i < want.length; i++) {
    const tol = 0.6;
    if (Math.abs(got[i][0] - want[i][0]) > tol || Math.abs(got[i][1] - want[i][1]) > tol) same = false;
  }
  if (same) okE++; else bad.push(id + ' got ' + JSON.stringify(got) + ' want ' + JSON.stringify(want));
}
for (const id in LINES) {
  const st = g.view.getState(model.getCell(id));
  const inner = st && st.text && st.text.node && st.text.node.querySelector('div div div');
  if (!inner) { wrap.push(id + ' no label'); continue; }
  const r = inner.getBoundingClientRect();
  const n = Math.round(r.height / LH);
  if (n !== LINES[id] || r.width > st.width - 6) wrap.push(id + ' lines ' + n + '/' + LINES[id] + ' w ' + Math.round(r.width) + '/' + Math.round(st.width));
  else okV++;
}
const probe = g.view.getState(model.getCell(PROBE)).text.node.querySelector('div div div');
const cs = probe ? getComputedStyle(probe) : {};
const tprobe = g.view.getState(model.getCell('title')).text.node.querySelector('div div div');
const ts = tprobe ? getComputedStyle(tprobe) : {};
return {fontOk, family: cs.fontFamily, weight: cs.fontWeight, size: cs.fontSize,
        title: [tprobe && tprobe.textContent, ts.fontFamily, ts.fontWeight, ts.fontSize], edgesOk: okE, edgesBad: bad.length,
        labelsOk: okV, labelsBad: wrap.length, bad: bad.slice(0, 12), wrap: wrap.slice(0, 12)};
})()""" % (json.dumps(b64), json.dumps(exp, separators=(",", ":")), json.dumps(lines, separators=(",", ":")), json.dumps(LH),
           json.dumps(case.verify_probe), json.dumps(href), json.dumps(faces))
    dest = pathlib.Path(dest) if dest else pathlib.Path(os.environ.get("TMPDIR") or case.dir) / "verify.js"
    dest.write_text(js, encoding="utf-8", newline="\n")
    print(f"{dest}: {len(js)} chars")
    return dest


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    import argparse
    import style
    from case import Case, CaseError
    ap = argparse.ArgumentParser(description="Write $TMPDIR/verify.js, the in-browser check of a case's .drawio master.")
    ap.add_argument("case_dir")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    try:
        style.configure(a.config)
        write(Case(a.case_dir, trust=a.trust_case))
    except (CaseError, style.StyleError) as ex:
        raise SystemExit(str(ex))


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
