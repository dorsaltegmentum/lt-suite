#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wrap every final-<palette>.svg in the Artifact page: one standalone HTML document.

By default (Palette: Auto) the page theme selects a light or a dark palette from the configured ones
(style.palettes); the Palette switch shows any of them, and the browser remembers the choice. The page's
words (title, lede, figcaption, findings table, sources, optional stage notes) are case content in
<case_dir>/artifact_content.py. The group and members come from case.json.

The figure is interactive (artifact_logic.js, the pure logic, and artifact_ui.js, the DOM wiring, both
inlined here). It opens in Read mode with the static figure and offers three more modes:

  Present  step through the stages in story order (case.json "stages", else CLUSTERS order): the current
           step at full strength, earlier stages kept but subdued, later ones hidden or faded; Back/Next,
           arrow keys, swipe, a step counter, and Fit width / Zoom to stage / Actual size.
  Trace    click or tap a box (or pick it from a list): every upstream cause and downstream consequence
           stays, the rest dims, and a side panel lists the chain in order with its arrow kinds.
  Quiz     cover the box texts (all, or one stage) with blank cards in the box's own colours; click to
           reveal, Reveal next, a shown/total count, Reset.

The page finds the figure's parts by the SVG contract render.py writes: <g class="stage" data-stage>,
<g id="edge-I" class="edge" data-a data-b data-kind>, <g class="node" data-node data-role data-stage>;
any other element carrying data-node, data-a/data-b or data-stage (a later layer: glyphs, labels ...)
dims with what it names. Box rectangles come from the first <rect> of each node group. The data the
scripts need is embedded as JSON (#fig-data).

Downloads are plain <a download> links with data URIs: one PDF per palette and a zip of the draw.io
masters. With SOURCE_DATE_EPOCH set, the zip entries carry that time, so a rebuild gives the same bytes;
otherwise the files' mtimes. Fonts: with fonts.page_fonts = "local" (the default) the page carries the
diagram's faces as @font-face data, cut down to the characters it shows, and makes no network request;
with "google" it links Google Fonts.

    python3 make_artifact.py <case_dir> [out.html]      (after the build has filled deliver/)
"""
import base64, html, io, json, numbers, os, pathlib, re, sys, time, zipfile
import xml.etree.ElementTree as ET

import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)
from _legacy import read_legacy_text

ENGINE = pathlib.Path(__file__).resolve().parent
from casekit import NBSP
import themes
from ltcore import fonts
import style

FIELDS = ("PAGE_TITLE", "EYEBROW", "HEADING", "LEDE", "FIGCAPTION", "FINDINGS", "SOURCES")
SVG_NS = "{http://www.w3.org/2000/svg}"


def esc(s):
    return html.escape(s)


def submission_note():
    """The print palette's file card note, from diagram.delivery.submission_label and .lms_name."""
    try:
        import style
        label, lms = style.get("diagram.delivery.submission_label"), style.get("diagram.delivery.lms_name")
    except Exception:  # noqa: BLE001  (no config: the plain note)
        label = lms = None
    note = " This is the submission copy" + (f" ({esc(str(label))})" if label else "")
    return note + (f"; hand it in through {esc(str(lms))}." if lms else ".")


def _suffix_ids(svg, suffix):
    """Give every id in the SVG a suffix, with its url(#id) and href="#id" references, so the two inline
    copies of the figure never share an id (a later layer's clipPath or gradient then resolves in its own copy)."""
    ids = set(re.findall(r'\sid="([^"]+)"', svg))
    if not ids:
        return svg
    svg = re.sub(r'(\sid=")([^"]+)(")', lambda m: m.group(1) + m.group(2) + suffix + m.group(3), svg)
    svg = re.sub(r'url\(#([^)\s]+)\)', lambda m: f"url(#{m.group(1)}{suffix})" if m.group(1) in ids else m.group(0), svg)
    return re.sub(r'((?:xlink:)?href=")#([^"]+)(")',
                  lambda m: m.group(1) + "#" + m.group(2) + (suffix if m.group(2) in ids else "") + m.group(3), svg)


def figure(case_dir, flavour, index=0):
    """The inline figure: final-<flavour>.svg with a class instead of its pixel size, font fallbacks, and (after
    the first copy) every id suffixed so the page has no duplicate ids. Returns (svg, width, height)."""
    svg = read_legacy_text(case_dir / f"final-{flavour}.svg")
    head = re.search(r'<svg xmlns="http://www.w3.org/2000/svg"[^>]*>', svg).group(0)
    w = int(re.search(r' width="(\d+)"', head).group(1))
    h = int(re.search(r' height="(\d+)"', head).group(1))
    svg = re.sub(r'(<svg xmlns="http://www.w3.org/2000/svg"[^>]*?) width="\d+" height="\d+"',
                 rf'\1 class="fig fig-{flavour}" data-pal="{flavour}"', svg, count=1)
    T = themes.THEMES[flavour]
    svg = fonts.svg_with_stacks(svg, style.cfg())
    if index:
        svg = _suffix_ids(svg, f"--p{index}")
    return svg, w, h


def _classes(el):
    return (el.get("class") or "").split()


def _rect(g):
    """The first <rect> of a group (its box or container), as (x, y, w, h)."""
    r = g.find(f"{SVG_NS}rect")
    if r is None:
        r = next(g.iter(f"{SVG_NS}rect"), None)
    if r is None:
        return None
    return tuple(round(float(r.get(k)), 1) for k in ("x", "y", "width", "height"))


def svg_parts(svg_text):
    """Read the figure's parts off the SVG contract (render.py): nodes, edges in order, stage containers,
    and the viewBox. Raises ValueError when a part breaks the contract."""
    root = ET.fromstring(svg_text)
    vb = [float(v) for v in root.get("viewBox").split()]
    nodes, edges, stages = {}, [], {}
    for g in root.iter(f"{SVG_NS}g"):
        cls = _classes(g)
        if "node" in cls:
            nid = g.get("data-node")
            if not nid or nid in nodes:
                raise ValueError(f"SVG: node group {g.get('id')!r} has no data-node or repeats one")
            rect = _rect(g)
            if rect is None:
                raise ValueError(f"SVG: node {nid} has no <rect>")
            nodes[nid] = dict(role=g.get("data-role"), stage=g.get("data-stage"), rect=rect)
        elif "edge" in cls:
            m = re.fullmatch(r"edge-(\d+)", g.get("id") or "")
            if not m or int(m.group(1)) != len(edges):
                raise ValueError(f"SVG: edge groups must be edge-0, edge-1 ... in order; found {g.get('id')!r}")
            edges.append(dict(a=g.get("data-a"), b=g.get("data-b"), k=g.get("data-kind")))
        elif "stage" in cls:
            sid = g.get("data-stage")
            rect = _rect(g)
            if not sid or rect is None:
                raise ValueError(f"SVG: stage group {g.get('id')!r} needs data-stage and a container <rect>")
            stages[sid] = rect
    return dict(viewbox=vb, nodes=nodes, edges=edges, stages=stages)


def story_stages(case):
    """Stage ids in story order with their step number n: case.json "stages" when present, else CLUSTERS order.

    Accepted shapes: ["cause", "mech", ...]; [{"id": "cause", "n": 1}, ...] ("stage" or "cluster" may name
    the id); {"cause": {"n": 1}, ...} or {"cause": 1, ...}. Entries are ordered by n where given (stages
    sharing an n are presented together), keeping the listed order otherwise; stages the list leaves out
    follow in CLUSTERS order. Returns [(id, n or None)]."""
    clusters = list(case.content.CLUSTERS)
    spec = case.meta.get("stages")
    if not spec:
        return [(c, None) for c in clusters]
    entries = []
    items = spec.items() if isinstance(spec, dict) else enumerate(spec) if isinstance(spec, list) else None
    if items is None:
        raise ValueError('case.json "stages" must be a list or an object')
    for key, v in items:
        if isinstance(spec, dict):
            sid, n = key, (v.get("n") if isinstance(v, dict) else v)
        elif isinstance(v, str):
            sid, n = v, None
        elif isinstance(v, dict):
            sid, n = v.get("id") or v.get("stage") or v.get("cluster"), v.get("n")
        else:
            raise ValueError(f'case.json "stages": cannot read entry {v!r}')
        if sid not in case.content.CLUSTERS:
            raise ValueError(f'case.json "stages": {sid!r} is not a stage in content.py CLUSTERS')
        if n is not None and (isinstance(n, bool) or not isinstance(n, numbers.Real)):
            raise ValueError(f'case.json "stages": n for {sid} must be a number')
        if sid in [e[0] for e in entries]:
            raise ValueError(f'case.json "stages": {sid} is listed twice')
        entries.append((sid, n))
    order = sorted(range(len(entries)), key=lambda i: (entries[i][1] is None, entries[i][1] or 0, i))
    out = [entries[i] for i in order]
    return out + [(c, None) for c in clusters if c not in dict(out)]


def fig_data(case, parts, storage_key):
    """The JSON the page's scripts read: boxes, arrows, stages in story order, role names, italic terms."""
    C = case.content
    svg_nodes, svg_edges = parts["nodes"], parts["edges"]
    missing, extra = sorted(set(C.NODES) - set(svg_nodes)), sorted(set(svg_nodes) - set(C.NODES))
    if missing or extra:
        raise ValueError(f"SVG and content.py disagree on boxes: missing {missing}, unknown {extra}")
    for i, e in enumerate(svg_edges):
        if e["a"] not in svg_nodes or e["b"] not in svg_nodes:
            raise ValueError(f"SVG edge-{i}: {e['a']} -> {e['b']} names a box the SVG does not have")
        if e["k"] not in themes.EDGE_KINDS:
            raise ValueError(f"SVG edge-{i}: unknown kind {e['k']!r}")
    notes = getattr(case.artifact, "STAGE_NOTES", None) or {}
    unknown = [k for k in notes if k not in C.CLUSTERS]
    if unknown:
        raise ValueError(f"artifact_content.py STAGE_NOTES: unknown stages {unknown}")
    nodes = {}
    for nid in C.NODES:                       # declaration order
        sv = svg_nodes[nid]
        if sv["stage"] not in C.CLUSTERS or sv["role"] not in themes.ROLES:
            raise ValueError(f"SVG node {nid}: stage {sv['stage']!r} or role {sv['role']!r} is unknown")
        x, y, w, h = sv["rect"]
        nodes[nid] = dict(t=C.NODES[nid]["text"].replace(NBSP, " "), r=sv["role"], s=sv["stage"],
                          b=bool(C.NODES[nid]["bold"]), x=x, y=y, w=w, h=h)
    stages = []
    for sid, n in story_stages(case):
        r = parts["stages"].get(sid)
        stages.append(dict(id=sid, title=re.sub(r"\s+", " ", C.CLUSTERS[sid]), n=n,
                           rect=[r[0], r[1], round(r[0] + r[2], 1), round(r[1] + r[3], 1)] if r else None,
                           note=notes.get(sid, "").replace(NBSP, " ")))
    vb = parts["viewbox"]
    return dict(W=vb[2], H=vb[3], vb=vb, nodes=nodes, order=list(C.NODES), edges=svg_edges, stages=stages,
                roles=dict(case.role_name), italic=[t.replace(NBSP, " ") for t in getattr(C, "ITALIC", ())],
                kinds=kind_dashes(), storageKey=storage_key)


def kind_dashes():
    """Each arrow kind's dash for the trace panel's small arrow glyphs, as the diagram draws it (themes.py:
    False solid, True the "depends on" dash, or a pattern string drawn with round caps)."""
    out = {}
    for k, (_c, dashed, _h) in themes.THEMES[themes.print_palette()]["edges"].items():
        if dashed is True:
            out[k] = dict(dash="1.4 3.6", round=True)       # the "depends on" dash at glyph scale, pill-ended
        elif dashed:
            out[k] = dict(dash=str(dashed), round=True)
    return out


def json_script(obj):
    """JSON safe inside <script type="application/json">: no "<" survives, so no "</script>"."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def theme_tokens(theme, indent):
    """CSS custom properties for the figure overlays and the trace panel, from the engine's palette."""
    T = themes.THEMES[theme]
    out = [f"--ring: {T['ink']}; --q-mark: {T['muted']};"]
    out += [f"--r-{r}-fill: {f}; --r-{r}-line: {s};" for r, (f, s, _t) in T["roles"].items()]
    out.append(" ".join(f"--e-{k}: {c};" for k, (c, _d, _h) in T["edges"].items()))
    return ("\n" + " " * indent).join(out)


def role_css():
    """Per-role and per-kind classes that read the tokens (quiz cards, panel swatches, arrow glyphs)."""
    out = []
    for r in themes.ROLES:
        out.append(f".r-{r} {{ --sw-fill: var(--r-{r}-fill); --sw-line: var(--r-{r}-line); }}")
    for k in themes.EDGE_KINDS:
        out.append(f".kg .ks-{k} {{ stroke: var(--e-{k}); }} .kg .kf-{k} {{ fill: var(--e-{k}); }}")
    return "\n".join(out)


def b64(path):
    return base64.b64encode(path.read_bytes()).decode()


def zipped(path, arcname):
    """The file in a one-entry zip, base64. The entry date is SOURCE_DATE_EPOCH (UTC) when that is set, so a
    rebuild gives the same bytes; otherwise the file's mtime, as before."""
    buf = io.BytesIO()
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if sde is None or not sde.strip():
            z.write(path, arcname)
        else:
            try:
                t = time.gmtime(max(int(sde), 315532800))        # clamp to the zip format's earliest timestamp
            except ValueError:
                raise ValueError(f"SOURCE_DATE_EPOCH must be whole seconds, not {sde!r}")
            info = zipfile.ZipInfo(arcname, date_time=t[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, path.read_bytes())
    return base64.b64encode(buf.getvalue()).decode()


def zipped_many(paths):
    """Several files in one zip, base64, dated as zipped() dates its one entry."""
    buf = io.BytesIO()
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            if sde is None or not sde.strip():
                z.write(path, path.name)
                continue
            info = zipfile.ZipInfo(path.name, date_time=time.gmtime(max(int(sde), 315532800))[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, path.read_bytes())
    return base64.b64encode(buf.getvalue()).decode()


def auto_pair(pals):
    """(light palette, dark palette) for Palette: Auto: the first light and the first dark of the configured
    palettes, the print palette first; one of them stands in for the other when the config has only one kind."""
    light = next((p for p in pals if not themes.is_dark(themes.THEMES[p])), pals[0])
    dark = next((p for p in pals if themes.is_dark(themes.THEMES[p])), light)
    return light, dark


def palette_css(pals):
    """Which figure shows: --fig-<i> is block for the one shown. Auto takes the page theme (auto_pair); a
    palette class on the shell (.shell.pal-<id>) shows that one outright, with its paper and overlay tokens."""
    out = [".paper svg.fig { display: none; }"]
    out += [f".paper svg.fig-{fl} {{ display: var(--fig-{i}, none); }}" for i, fl in enumerate(pals)]
    for i, fl in enumerate(pals):
        show = " ".join(f"--fig-{j}: {'block' if j == i else 'none'};" for j in range(len(pals)))
        out.append(f".shell.pal-{fl} {{ --paper: {themes.THEMES[fl]['canvas']}; {show}\n  {theme_tokens(fl, 2)} }}")
    return "\n".join(out)


def show_vars(pals, pid):
    return " ".join(f"--fig-{j}: {'block' if fl == pid else 'none'};" for j, fl in enumerate(pals))


def font_head(svgs):
    """The page's font link or inline @font-face rules (fonts.page_fonts)."""
    import fontset
    import style
    if style.page_fonts() == "google":
        return f'<link rel="stylesheet" href="{html.escape(fontset.google_css_url())}">'
    text = " ".join(re.findall(r">([^<]+)<", "".join(svgs)))
    return "<style>\n" + fontset.page_css(html.unescape(text)) + "\n</style>"


def ital(t, terms):
    """Escaped text with the case's ITALIC terms (binomial names) in italic, as on the diagram."""
    out = html.escape(t)
    for term in terms:
        plain = html.escape(term.replace(NBSP, " "))
        out = out.replace(plain, f"<i>{plain}</i>")
    return out


def unfilled(A):
    """The artifact_content.py fields still missing or holding new_case.py's TODO placeholders."""
    bad = [f for f in FIELDS if not getattr(A, f, None)]
    bad += [f for f in FIELDS if f not in bad and "TODO" in repr(getattr(A, f))]
    if "TODO" in repr(getattr(A, "STAGE_NOTES", None) or {}):
        bad.append("STAGE_NOTES")
    return bad


def make(case, out=None):
    """Write the Artifact page for the case; returns its path (None when the case has no artifact_content.py)."""
    A = case.artifact
    if A is None:
        return None
    todo = unfilled(A)
    if todo:
        raise ValueError(f"artifact_content.py still needs: {', '.join(todo)}")
    slug, here = case.slug, case.dir
    storage_key = getattr(A, "STORAGE_KEY", None) or f"{slug}-fit"     # remembers the viewer's fit / actual size choice
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", storage_key):
        raise ValueError("artifact_content.py: STORAGE_KEY must be letters, digits, '.', '-' or '_'")
    out = pathlib.Path(out) if out else here / getattr(A, "OUT_NAME", f"{slug}.html")
    terms = getattr(case.content, "ITALIC", ())
    pp = themes.print_palette()
    pals = [pp] + [fl for fl in themes.final_palettes() if fl != pp]
    parts = svg_parts(read_legacy_text(here / f"final-{pp}.svg"))
    for fl in pals[1:]:
        if svg_parts(read_legacy_text(here / f"final-{fl}.svg")) != parts:
            raise ValueError(f"final-{pp}.svg and final-{fl}.svg differ in their boxes, arrows or stages")
    figs = [figure(here, fl, i) for i, fl in enumerate(pals)]
    FIG_W = figs[0][1]
    svgs = "\n".join(f[0] for f in figs)
    data = fig_data(case, parts, storage_key)
    data["palettes"] = pals
    deliver = here / "deliver"
    pdfs = [(fl, f"{slug}-{fl}.pdf", b64(deliver / f"{slug}-{fl}.pdf")) for fl in pals]
    masters = [deliver / themes.out_name(slug, fl, "drawio") for fl in pals]
    zip_b64 = zipped_many(masters)
    rows = "\n".join(f'<tr><td class="k">{html.escape(k)}</td><td>{ital(f, terms)}</td><td>{ital(x, terms)}</td></tr>'
                     for k, f, x in A.FINDINGS)
    srcs = "\n".join(f"<li>{html.escape(s)}</li>" for s in A.SOURCES)
    logic_js = (ENGINE / "artifact_logic.js").read_text(encoding='utf-8')
    ui_js = (ENGINE / "artifact_ui.js").read_text(encoding='utf-8')
    for name, js in (("artifact_logic.js", logic_js), ("artifact_ui.js", ui_js)):
        if "</script" in js.lower():
            raise ValueError(f"{name} must not contain '</script'")
    lp, dp = auto_pair(pals)
    light, dark, dark_mq = theme_tokens(lp, 2), theme_tokens(dp, 2), theme_tokens(dp, 4)
    T0 = themes.THEMES[pp]
    sans_css = fonts.css_stack("sans", style.cfg(), output="diagram", first=T0["sans"])
    mono_css = fonts.css_stack("mono", style.cfg(), output="diagram", first=T0["mono"])
    pal_buttons = "\n".join(f'          <button type="button" id="pal-{fl}" data-pal="{fl}" aria-pressed="false">'
                            f'{esc(themes.label(fl))}</button>' for fl in pals)
    file_cards = "\n".join(
        f'''      <div class="file">
        <h3>PDF, {esc(themes.label(fl))}</h3>
        <p>The diagram as one vector page, <code>{name}</code>.{submission_note() if fl == pp else ""}</p>
        <a class="dl" id="save-pdf-{fl}" href="data:application/pdf;base64,{data64}" download="{name}">Save PDF</a>
      </div>''' for fl, name, data64 in pdfs)
    drawio_names = ", ".join(f"<code>{m.name}</code>" for m in masters)
    team = ""
    if case.group or case.members:
        team = (f'<p class="team">' + (f"<b>{html.escape(case.group)}</b>" if case.group else "")
                + (" · " if case.group and case.members else "") + html.escape(", ".join(case.members)) + "</p>")

    page = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(A.PAGE_TITLE)}</title>
{font_head([f[0] for f in figs])}
<style>
/* A paper page round one wide figure: header, the figure with its view controls, files, findings, sources.
   Page tokens first; the figure tokens (rings, quiz cards, panel swatches, arrow glyphs) are the diagram's
   own colours from the configured palettes (style.palettes). */
:root {{
  --bg: #EEF0F3; --surface: #FFFFFF; --ink: #1F2430; --muted: #5A6070; --rule: #D5D9E1;
  --accent: #3A8791; --accent-ink: #FFFFFF; --chip: #E4F1F3;
  --paper: {themes.THEMES[lp]['canvas']}; {show_vars(pals, lp)}
  --sans: {sans_css}; --mono: {mono_css};
  {light}
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #151A21; --surface: #1D232C; --ink: #E7EAF0; --muted: #A2AABA; --rule: #333B48;
    --accent: #6FC0C9; --accent-ink: #0E2226; --chip: #22343A; color-scheme: dark;
    --paper: {themes.THEMES[dp]['canvas']}; {show_vars(pals, dp)}
    {dark_mq}
  }}
}}
:root[data-theme="dark"] {{
  --bg: #151A21; --surface: #1D232C; --ink: #E7EAF0; --muted: #A2AABA; --rule: #333B48;
  --accent: #6FC0C9; --accent-ink: #0E2226; --chip: #22343A; color-scheme: dark;
  --paper: {themes.THEMES[dp]['canvas']}; {show_vars(pals, dp)}
  {dark}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 var(--sans); padding: 0 16px; }}
.wrap {{ max-width: 1240px; margin: 0 auto; padding-block: 28px 48px; display: grid; gap: 22px; }}
header {{ display: grid; gap: 6px; }}
.eyebrow {{ font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); font-weight: 600; }}
h1 {{ margin: 0; font: 600 clamp(28px, 4.2vw, 42px)/1.12 var(--sans); text-wrap: balance; }}
.lede {{ margin: 0; max-width: 68ch; color: var(--muted); }}
.team {{ margin: 0; font-size: 13.5px; color: var(--muted); }}
.team b {{ color: var(--ink); font-weight: 600; }}
.bar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }}
.hint {{ color: var(--muted); font-size: 13px; }}
.seg {{ display: inline-flex; flex-wrap: wrap; border: 1px solid var(--rule); border-radius: 999px; overflow: hidden; background: var(--surface); }}
.seg button {{ font: 600 13px var(--sans); border: 0; background: transparent; color: var(--ink);
  padding: 7px 14px; cursor: pointer; }}
.seg button[aria-pressed="true"] {{ background: var(--accent); color: var(--accent-ink); }}
.seg button:focus-visible, .files .dl:focus-visible, .btn:focus-visible, .fig-ctl select:focus-visible,
.ci:focus-visible, .paper:focus-visible, .tp-sel:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.seg button:focus-visible {{ outline-offset: -3px; }}
.paper {{ background: var(--paper); border: 1px solid var(--rule); border-radius: 14px; overflow: auto; max-height: 82vh;
  box-shadow: 0 1px 2px rgba(0,0,0,.05); }}
.paper svg.fig {{ display: block; width: {FIG_W}px; max-width: none; height: auto; }}
{palette_css(pals)}
.paper.fit svg.fig {{ width: 100%; }}
.paper.zoom {{ overflow: hidden; }}
.paper.zoom svg.fig {{ width: 100%; height: auto; aspect-ratio: 16 / 9; max-height: 76vh; }}
figure {{ margin: 0; display: grid; gap: 10px; }}
figcaption {{ color: var(--muted); font-size: 13.5px; max-width: 90ch; }}
section {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 14px; padding: 18px 20px; }}
h2 {{ margin: 0 0 10px; font: 600 19px/1.3 var(--sans); }}
.files {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
.file {{ display: grid; gap: 6px; align-content: start; border: 1px solid var(--rule); border-radius: 12px; padding: 14px 16px; }}
.file h3 {{ margin: 0; font: 600 15px/1.3 var(--sans); }}
.file p {{ margin: 0; color: var(--muted); font-size: 13.5px; }}
.file code {{ font-size: 12.5px; background: var(--chip); padding: 1px 5px; border-radius: 5px; }}
.files .dl {{ justify-self: start; font: 600 13.5px var(--sans); border: 1px solid var(--accent); text-decoration: none;
  background: var(--accent); color: var(--accent-ink); border-radius: 999px; padding: 7px 16px; cursor: pointer; }}
.tablewrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ text-align: left; vertical-align: top; padding: 8px 10px; border-bottom: 1px solid var(--rule); }}
th {{ font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }}
td.k {{ white-space: nowrap; color: var(--muted); font-size: 13px; }}
ol {{ margin: 0; padding-left: 20px; display: grid; gap: 6px; font-size: 14px; }}

/* the interactive figure: mode bars, trace panel, overlays */
.sr-only {{ position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap; border: 0; }}
.shell {{ display: grid; gap: 10px; min-width: 0; }}
.fig-ctl {{ display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; min-height: 36px; min-width: 0; }}
.fig-ctl[hidden], .tp[hidden] {{ display: none; }}
.grp {{ display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }}
.btn {{ font: 600 13px var(--sans); border: 1px solid var(--rule); background: var(--surface);
  color: var(--ink); border-radius: 999px; padding: 7px 14px; cursor: pointer; }}
.btn:hover:not(:disabled) {{ border-color: var(--accent); }}
.btn:disabled {{ color: var(--muted); cursor: default; opacity: .6; }}
.btn.primary {{ background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }}
.fig-ctl select {{ font: 500 13.5px var(--sans); color: var(--ink); background: var(--surface);
  border: 1px solid var(--rule); border-radius: 8px; padding: 6px 8px; max-width: min(100%, 420px); min-width: 0; }}
.step {{ display: grid; gap: 2px; min-width: 0; }}
.step-count {{ font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; letter-spacing: .04em; }}
.step-title {{ font: 700 13.5px/1.3 var(--mono); color: var(--ink); }}
.step-note {{ margin: 0; flex-basis: 100%; color: var(--muted); font-size: 14px; max-width: 90ch; }}
.count {{ font-variant-numeric: tabular-nums; color: var(--ink); font-weight: 600; }}
.area {{ display: grid; gap: 14px; grid-template-columns: minmax(0, 1fr); min-width: 0; }}
@media (min-width: 1100px) {{ .area.with-panel {{ grid-template-columns: minmax(0, 1fr) 300px; }} #tr-status {{ display: none; }} }}
.tp {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 14px; padding: 14px 16px;
  max-height: 82vh; overflow: auto; min-width: 0; align-self: start; }}
.tp h3 {{ margin: 0 0 8px; font: 600 15px/1.3 var(--sans); }}
.tp-empty {{ margin: 0; color: var(--muted); font-size: 13.5px; }}
.tp-sel {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px; align-items: start; font-weight: 600;
  font-size: 14px; border-radius: 8px; }}
.tp-meta {{ margin: 4px 0 0; color: var(--muted); font-size: 12.5px; }}
.tp h4 {{ margin: 16px 0 2px; font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }}
.tp-dist {{ margin: 8px 0 2px; font-size: 12px; color: var(--muted); }}
.tp ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }}
.ci {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px 8px; width: 100%; text-align: left;
  font: 500 13.5px/1.4 var(--sans); color: var(--ink); background: transparent;
  border: 0; border-radius: 8px; padding: 6px 6px; cursor: pointer; }}
.ci:hover {{ background: var(--chip); }}
.cv {{ grid-column: 2; color: var(--muted); font-size: 12.5px; }}
.sw {{ display: inline-block; width: 20px; height: 13px; margin-top: 3px; border-radius: 4px;
  background: var(--sw-fill); border: 1.5px solid var(--sw-line); }}
.sw.r-anat {{ border-style: dashed; }}
.kg {{ display: inline-block; vertical-align: -1px; margin-right: 5px; overflow: visible; }}
.kg line, .kg polygon {{ stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }}
{role_css()}
.paper svg.fig .node, .paper svg.fig .edge, .paper svg.fig .stage, .paper svg.fig [data-v] {{
  transition: opacity .3s ease, visibility .3s; }}
.paper svg.fig [data-v="past"] {{ opacity: .5; }}
.paper svg.fig [data-v="fade"] {{ opacity: .12; }}
.paper svg.fig [data-v="gone"] {{ opacity: 0; visibility: hidden; }}
.paper svg.fig [data-v="dim"] {{ opacity: .16; }}
.paper[data-mode="trace"] svg.fig .node {{ cursor: pointer; }}
.paper svg.fig .node:focus, .paper svg.fig .qcard:focus {{ outline: none; }}
.paper svg.fig .ov {{ pointer-events: none; }}
.paper svg.fig .qcard {{ display: none; }}
.paper[data-mode="quiz"] svg.fig .qcard {{ display: inline; }}
.paper[data-mode="quiz"] svg.fig .qcard.out {{ display: none; }}
.paper svg.fig .ov rect {{ fill: none; vector-effect: non-scaling-stroke; }}
.paper svg.fig .ov .ring {{ stroke: var(--ring); stroke-width: 3px; }}
.paper svg.fig .ov .ring-next {{ stroke: var(--ring); stroke-width: 2px; stroke-dasharray: 4 6; stroke-linecap: round; }}
.paper svg.fig .ov .ring-focus {{ stroke: var(--accent); stroke-width: 3px; }}
.paper svg.fig .qcard {{ cursor: pointer; transition: opacity .25s ease; }}
.paper svg.fig .qcard.shown {{ opacity: 0; pointer-events: none; }}
.paper svg.fig .qcard rect {{ fill: var(--sw-fill); stroke: var(--sw-line); stroke-width: 1.5; }}
.paper svg.fig .qcard.bold rect {{ stroke-width: 2.2; }}
.paper svg.fig .qcard.r-anat rect {{ stroke-dasharray: 4.5 5; stroke-linecap: round; }}
.paper svg.fig .qcard text {{ fill: var(--q-mark); font: 700 17px var(--sans); }}
.paper[data-mode="present"].fit, .paper[data-mode="present"].zoom {{ touch-action: pan-y pinch-zoom; }}
.shell:fullscreen {{ background: var(--bg); padding: 16px; display: flex; flex-direction: column; gap: 10px; overflow: auto; }}
.shell:fullscreen .area {{ flex: 1 1 auto; min-height: 0; grid-template-rows: minmax(0, 1fr); }}
.shell:fullscreen .paper {{ max-height: none; height: 100%; }}
.shell:fullscreen .paper.fit svg.fig, .shell:fullscreen .paper.zoom svg.fig {{ width: 100%; height: 100%;
  aspect-ratio: auto; max-height: none; }}
@media (pointer: coarse) {{ .seg button, .btn {{ min-height: 40px; }} }}
@media (prefers-reduced-motion: reduce) {{
  * {{ scroll-behavior: auto; }}
  .paper svg.fig .node, .paper svg.fig .edge, .paper svg.fig .stage, .paper svg.fig [data-v], .paper svg.fig .qcard {{ transition: none; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow">{esc(A.EYEBROW)}</div>
    <h1>{esc(A.HEADING)}</h1>
    <p class="lede">{A.LEDE}</p>
    {team}
  </header>
  <figure>
    <div class="shell" id="fig-shell" data-mode="read">
      <div class="bar">
        <div class="seg" role="group" aria-label="View">
          <button type="button" id="mode-read" aria-pressed="true">Read</button>
          <button type="button" id="mode-present" aria-pressed="false">Present</button>
          <button type="button" id="mode-trace" aria-pressed="false">Trace</button>
          <button type="button" id="mode-quiz" aria-pressed="false">Quiz</button>
        </div>
        <div class="seg" role="group" aria-label="Palette">
          <button type="button" id="pal-auto" data-pal="" aria-pressed="true" title="Select a light or dark palette to match the page">Auto</button>
{pal_buttons}
        </div>
        <div class="seg" role="group" aria-label="Figure size">
          <button type="button" id="size-actual" aria-pressed="true">Actual size</button>
          <button type="button" id="size-fit" aria-pressed="false">Fit width</button>
          <button type="button" id="size-zoom" aria-pressed="false" hidden>Zoom to stage</button>
        </div>
      </div>
      <div class="fig-ctl" id="bar-read">
        <span class="hint">Scroll to pan at full size, or fit the whole figure to the width. Present steps through the stages; Trace follows one box’s causes and consequences; Quiz covers the boxes so you can test yourself.</span>
      </div>
      <div class="fig-ctl" id="bar-present" hidden>
        <div class="grp">
          <button type="button" class="btn" id="pv-prev" aria-keyshortcuts="ArrowLeft">‹ Back</button>
          <button type="button" class="btn primary" id="pv-next" aria-keyshortcuts="ArrowRight">Next ›</button>
        </div>
        <div class="step"><span class="step-count" id="pv-count"></span><span class="step-title" id="pv-title"></span></div>
        <div class="grp"><span id="pv-later-l">Later stages</span>
          <div class="seg" role="group" aria-labelledby="pv-later-l">
            <button type="button" id="pv-gone" aria-pressed="true">Hidden</button>
            <button type="button" id="pv-fade" aria-pressed="false">Faded</button>
          </div>
        </div>
        <button type="button" class="btn" id="pv-full" aria-pressed="false">Full screen</button>
        <p class="step-note" id="pv-note" hidden></p>
      </div>
      <div class="fig-ctl" id="bar-trace" hidden>
        <span class="hint">Tap or click a box to see what leads to it and what follows from it.</span>
        <label class="grp" for="tr-pick">Box <select id="tr-pick"><option value="">Choose a box…</option></select></label>
        <div class="grp"><span id="tr-depth-l">Steps</span>
          <div class="seg" role="group" aria-labelledby="tr-depth-l">
            <button type="button" id="tr-d1" data-depth="1" aria-pressed="false">Direct only</button>
            <button type="button" id="tr-d2" data-depth="2" aria-pressed="false">2</button>
            <button type="button" id="tr-d3" data-depth="3" aria-pressed="false">3</button>
            <button type="button" id="tr-d0" data-depth="0" aria-pressed="true">All</button>
          </div>
        </div>
        <button type="button" class="btn" id="tr-clear" aria-keyshortcuts="Escape" disabled>Clear</button>
        <p class="step-note" id="tr-status" hidden></p>
      </div>
      <div class="fig-ctl" id="bar-quiz" hidden>
        <label class="grp" for="qz-scope">Cover <select id="qz-scope"><option value="">All stages</option></select></label>
        <span class="grp"><span class="count" id="qz-count"></span></span>
        <div class="grp">
          <button type="button" class="btn primary" id="qz-next">Reveal next</button>
          <button type="button" class="btn" id="qz-all">Reveal all</button>
          <button type="button" class="btn" id="qz-reset">Reset</button>
        </div>
      </div>
      <div class="area" id="fig-area">
        <div class="paper" id="paper" data-mode="read" tabindex="0" role="region" aria-label="Diagram">
{svgs}
        </div>
        <aside class="tp" id="trace-panel" aria-labelledby="tp-h" hidden>
          <h3 id="tp-h">Chain</h3>
          <div id="tp-body"></div>
        </aside>
      </div>
      <p class="sr-only" id="fig-live" aria-live="polite"></p>
    </div>
    <figcaption>{A.FIGCAPTION}</figcaption>
  </figure>
  <section aria-labelledby="files-h">
    <h2 id="files-h">Files</h2>
    <div class="files">
{file_cards}
      <div class="file">
        <h3>draw.io masters</h3>
        <p>{drawio_names}: the same diagram as the PDFs, ready to edit in draw.io, in one zip.</p>
        <a class="dl" id="save-drawio" href="data:application/zip;base64,{zip_b64}" download="{slug}-drawio.zip">Save .drawio files (zip)</a>
      </div>
    </div>
  </section>
  <section>
    <h2>Findings checklist</h2>
    <div class="tablewrap"><table>
      <thead><tr><th>From</th><th>Finding</th><th>Explained by</th></tr></thead>
      <tbody>
{rows}
      </tbody>
    </table></div>
  </section>
  <section>
    <h2>Sources</h2>
    <ol>
{srcs}
    </ol>
  </section>
</div>
<script type="application/json" id="fig-data">{json_script(data)}</script>
<script>
{logic_js}
{ui_js}
</script>
<script>
(function () {{
  /* the Palette switch: a class on the shell (empty: Auto, the page theme decides); remembered per browser */
  var PALS = {json_script(pals)};
  var shell = document.getElementById('fig-shell'), PK = '{storage_key}-palette';
  var palBtns = Array.prototype.slice.call(document.querySelectorAll('button[data-pal]'));
  function setPal(p) {{
    PALS.forEach(function (k) {{ shell.classList.toggle('pal-' + k, k === p); }});
    palBtns.forEach(function (b) {{ b.setAttribute('aria-pressed', String(b.getAttribute('data-pal') === p)); }});
  }}
  var savedPal = null;
  try {{ savedPal = localStorage.getItem(PK); }} catch (e) {{ /* storage blocked */ }}
  setPal(PALS.indexOf(savedPal) >= 0 ? savedPal : '');
  palBtns.forEach(function (b) {{
    b.addEventListener('click', function () {{
      var p = b.getAttribute('data-pal');
      setPal(p);
      try {{ localStorage.setItem(PK, p); }} catch (e) {{ /* storage blocked */ }}
    }});
  }});
}})();
</script>
</body>
</html>
'''
    out.write_text(page, encoding='utf-8', newline="\n")
    print("wrote", out, len(page), "bytes")
    return out


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    import argparse
    import style
    from case import Case, CaseError
    ap = argparse.ArgumentParser(description="Write a case's Artifact page: one standalone HTML file.")
    ap.add_argument("case_dir")
    ap.add_argument("out", nargs="?", help="the HTML file (default: artifact_content.OUT_NAME in the case folder)")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    try:
        style.configure(a.config)
        path = make(Case(a.case_dir, trust=a.trust_case), a.out)
    except (CaseError, ValueError, style.StyleError) as ex:
        raise SystemExit(str(ex))
    if path is None:
        raise SystemExit("this case has no artifact_content.py")


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
