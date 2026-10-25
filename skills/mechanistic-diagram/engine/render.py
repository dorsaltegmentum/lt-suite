# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""geometry.json -> final-<flavour>.svg -> .pdf / .png (rsvg-convert), the same picture as the .drawio.
The title, case line, group and members come from case.json.

    python3 render.py <case_dir> --theme <palette id> --out final-<palette id>

Glyph layers (decor.py), each in its own group: <g id="timeline" class="timeline"> (one
<g class="tl-marker" data-stage> per stage on it); in each stage group a <g class="stage-badge"> (number and
glyph) and data-n; in each node group <g class="rangebar"> and <g class="glyphs"> (role glyph and flag badges)
after the box's text, and data-flags. A geometry.json from before the layers ("stages" absent) draws as it did.

Inset: <g id="inset" class="inset" data-inset> (the illustration panel: card, header, the recoloured image
as a PNG data URI under a rounded clipPath, id inset-image-clip-<flavour>, and <g class="labels"> with its leaders);
<g id="edge-labels" class="labels"> after the connectors, one <g class="edge-label" data-edge data-a data-b> per
labelled connector; <g id="credit" class="credit">
(the illustration credit: inside the inset panel at its bottom right, else the page's bottom right).
final-*.png get transparent rounded corners (PNG_CORNER canvas px); the PDF and the SVG pages stay rectangular.
"""
import argparse, json, os, pathlib, subprocess, sys

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)
from _legacy import read_legacy_text
import core as E
import decor, themes
from themes import THEMES

KEY_ROLES = ["risk", "mech", "anat", "sign", "inv", "dx", "tx"]
# each arrow sits beside the swatch it goes with (rows 1, 3, 5, 7 of the roles): risk factor / causes, anatomy /
# depends on, investigation / argues against, treatment / blocks. A case may override both lists in
# case.json ("key": {"roles": [...], "arrows": [[label, kind], ...]}).
KEY_ARROWS = [("Causal link", "stim"), ("Required structure or function", "req"),
              ("Evidence against diagnosis", "excl"), ("Blocked by treatment", "inh")]
SUB_FS, GROUP_FS, NAMES_FS, KEY_FS = 19, 21, 17, 14      # the title stays 30 px
TITLE_FS = 30                # title and group name use the configured mono (fonts.mono), Bold
PNG_CORNER = 16          # the PNG page's corner radius in canvas px (containers 12, boxes 7)


def rasterise(svg_path, stem, W, sans=None):
    """final-*.svg -> .png and .pdf beside it, through rsvg-convert with the static faces (fontset.py)."""
    import fontset
    out_dir = svg_path.parent
    env, src = dict(os.environ), svg_path
    conf, rename = fontset.static_set()
    env["FONTCONFIG_FILE"] = str(conf)
    svg = read_legacy_text(svg_path)
    if rename:
        for fam, sfam in rename.items():
            svg = svg.replace(f'font-family="{fam}"', f'font-family="{sfam}"')
        src = svg_path.with_suffix(".static.svg")
        src.write_text(svg, encoding="utf-8", newline="\n")
    try:
        subprocess.run(["rsvg-convert", "-f", "png", "-w", str(int(W * 1.5)), "-o", str(out_dir / f"{stem}.png"), str(src)],
                       check=True, env=env)
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(out_dir / f"{stem}.pdf"), str(src)], check=True, env=env)
    finally:
        if src != svg_path:
            src.unlink()
    round_png(out_dir / f"{stem}.png", PNG_CORNER * int(W * 1.5) / W)


def round_png(path, r):
    """Give a PNG page transparent rounded corners of radius r px (anti-aliased; the PDF and SVG stay square)."""
    from PIL import Image, ImageDraw
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    ss = 4
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    a = im.getchannel("A")
    from PIL import ImageChops
    im.putalpha(ImageChops.multiply(a, mask))
    im.save(path, "PNG", optimize=True)


def used_layers(g):
    """Which glyph layers a geometry draws (the key explains only those)."""
    nodes = g["nodes"].values()
    return dict(stages=any(s.get("n") is not None for s in (g.get("stages") or {}).values()),
                timeline=bool(g.get("timeline")),
                flags={f for nd in nodes for f in nd.get("flags", ())},
                marks=any(e.get("dir") for nd in nodes for e in nd.get("lab", ())),
                bars=any(nd.get("bars") for nd in nodes))


def key_block(x, y, w, h, T, case, used=None):
    """Legend panel at (x, y) with size (w, h). With the glyph layers (decor.py): boxes with their role glyph
    and the stage number on the left; arrows, flags, lab marks and range bars on the right."""
    out = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="12" ry="12" fill="{T["key_fill"]}" stroke="{T["key_stroke"]}" stroke-width="1.2"/>',
           f'<text x="{x + 16:.1f}" y="{y + 26:.1f}" font-family="{T["mono"]}" font-size="{E.CL_FS}" font-weight="700" fill="{T["cl_text"]}">KEY</text>']
    if used is not None:
        rows, _ = decor.key_layout(x, y, w, h, case, used)
        return out + decor.key_svg_rows(rows, T, case)
    wt = E.WGHT[0]
    roles, arrows, ROLE_NAME = case.key_roles(KEY_ROLES), case.key_arrows(KEY_ARROWS), case.role_name
    col1, top = x + 18, y + 48
    step = min(44, (h - 66) / len(roles))
    for i, r in enumerate(roles):
        fill, border, text = T["roles"][r]
        yy = top + i * step
        dash = E.anat_dash_attr() if r == "anat" else ""
        out.append(f'<rect x="{col1:.1f}" y="{yy:.1f}" width="34" height="20" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.5"{dash}/>')
        out.append(f'<text x="{col1 + 46:.1f}" y="{yy + 15:.1f}" font-family="{T["sans"]}" font-size="{KEY_FS}" font-weight="{wt}" fill="{T["ink"]}">{E.esc(ROLE_NAME[r])}</text>')
    col2 = col1 + 46 + max(E.text_w(ROLE_NAME[r]) for r in roles) + 40
    room = x + w - 16 - (col2 + 66)
    for i, (lab, kind) in enumerate(arrows):
        colour, dashed, head = T["edges"][kind]
        yy = top + 10 + i * (step * (len(roles) - 1) / max(1, len(arrows) - 1))
        out += E.edge_svg([(col2, yy), (col2 + 52, yy)], colour, dashed, head, gap=0)
        lines = [lab] if E.text_w(lab) <= room else E.balanced(lab, 2)[0]
        if max(E.text_w(l) for l in lines) > room:
            raise ValueError("key text overflows the panel: widen KEY_SPAN or shorten the arrow labels")
        for j, ln in enumerate(lines):
            out.append(f'<text x="{col2 + 66:.1f}" y="{yy + 5 + (j - (len(lines) - 1) / 2) * E.LH:.1f}" font-family="{T["sans"]}" font-size="{KEY_FS}" font-weight="{wt}" fill="{T["ink"]}">{E.esc(ln)}</text>')
    return out


def header(x_min, x_max, T, case):
    """Title and case line on the left; the group and its members (when case.json gives them) on the right.
    The title and the group name use the configured mono (fonts.mono), bold, like the stage headers.
    case.preflight() checks the member order when diagram.header.members_order is "surname"."""
    wt = E.WGHT[0]
    names = "  ·  ".join(case.members)
    out = [f'<text x="{x_min:.1f}" y="64" font-family="{T["mono"]}" font-size="{TITLE_FS}" font-weight="700" fill="{T["ink"]}">{E.esc(case.title)}</text>']
    if case.subtitle:
        out.append(f'<text x="{x_min:.1f}" y="100" font-family="{T["sans"]}" font-size="{SUB_FS}" font-weight="{wt}" fill="{T["muted"]}">{E.esc(case.subtitle)}</text>')
    if case.group:
        out.append(f'<text x="{x_max:.1f}" y="64" text-anchor="end" font-family="{T["mono"]}" font-size="{GROUP_FS}" font-weight="700" fill="{T["ink"]}">{E.esc(case.group)}</text>')
    if names:
        out.append(f'<text x="{x_max:.1f}" y="100" text-anchor="end" font-family="{T["sans"]}" font-size="{NAMES_FS}" font-weight="{wt}" fill="{T["muted"]}">{E.esc(names)}</text>')
    sub_w = E.text_w(case.subtitle, size=SUB_FS) if case.subtitle else 0
    names_w = E.text_w(names, size=NAMES_FS) if names else 0
    if names and not x_min + sub_w + 60 < x_max - names_w:
        raise ValueError("the subtitle runs into the member list: shorten case.json's subtitle")
    title_w = E.text_w(case.title, size=TITLE_FS)
    group_w = E.text_w(case.group, size=GROUP_FS) if case.group else 0
    if title_w + group_w + 60 > x_max - x_min:
        raise ValueError("the title runs into the group name: shorten case.json's title")
    return out


def render(case, theme=None, stem="final", geo_file="geometry.json"):
    """Write <case_dir>/<stem>.svg, .pdf and .png in a palette (default: the print palette). Returns (W, H)."""
    theme = theme or themes.print_palette()
    T = THEMES[theme]
    g = json.loads(read_legacy_text(case.dir / geo_file))
    E.fonts_for_geometry(g)
    if E.FONT != T["sans"]:
        raise ValueError(f"geometry was measured in {E.FONT}, theme sets text in {T['sans']}")
    nodes, rects, edges = g["nodes"], g["clusters"], g["edges"]
    allr = list(rects.values()) + ([g["inset"]["rect"]] if g.get("inset") else [])
    xs = [r[0] for r in allr] + [r[2] for r in allr]
    ys = [r[1] for r in allr] + [r[3] for r in allr]
    x_min, x_max, y_max = min(xs), max(xs), max(ys)
    W = x_max + x_min            # symmetric margins
    H = y_max + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
           f'role="img" aria-label="{E.esc(case.aria_label)}">',
           f'<rect width="{W:.0f}" height="{H:.0f}" fill="{T["canvas"]}"/>']
    # Every part sits in a <g> with a stable id and data attributes: the Artifact page finds stages,
    # arrows and boxes by them (make_artifact.py). Keep these when adding layers.
    out.append('<g id="header" class="header">')
    out += header(x_min, x_max, T, case)
    out.append('</g>')
    # the key sits on the stage grid (layout.py KEY_SPAN)
    kx0, ky0, kx1, ky1 = g["key"]
    layers = "stages" in g        # a geometry from before the glyph layers draws exactly as it did
    out.append('<g id="key" class="key">')
    out += key_block(kx0, ky0, kx1 - kx0, ky1 - ky0, T, case, used_layers(g) if layers else None)
    out.append('</g>')
    if g.get("timeline"):
        out.append('<g id="timeline" class="timeline">')
        out += decor.timeline_svg(g["timeline"], T)
        out.append('</g>')
    stages = g.get("stages") or {}
    for cid, r in rects.items():
        st = stages.get(cid, {})
        out.append(f'<g id="stage-{cid}" class="stage" data-stage="{cid}"' + (f' data-n="{st["n"]}"' if st.get("n") is not None else "") + '>')
        out += E.cluster_svg(cid, r, case.content.CLUSTERS[cid], T["cl_fill"], T["cl_stroke"], T["cl_text"], T["mono"],
                             title_x=decor.title_x(st))
        if st.get("n") is not None or st.get("icon"):
            out.append('<g class="stage-badge">')
            out += decor.stage_header_svg(r, st, T)
            out.append('</g>')
        out.append('</g>')
    if g.get("inset"):        # the illustration panel (decor.inset_svg)
        out.append('<g id="inset" class="inset" data-inset="inset">')
        out += decor.inset_svg(g["inset"], T, theme, case.dir)
        out.append('</g>')
    boxes = {k: E.Box(id=k, x=v["x"], y=v["y"], w=v["w"], h=v["h"], role=v["role"], lines=v["lines"], bold=v["bold"],
                      cluster=v["cluster"], foot=v.get("foot", 0)) for k, v in nodes.items()}
    marks = {themes.LAB_MARK[d]: decor.lab_colour(d, T) for d in ("high", "low")}
    for i, ed in enumerate(edges):
        colour, dashed, head = T["edges"][ed["kind"]]
        out.append(f'<g id="edge-{i}" class="edge" data-a="{ed["a"]}" data-b="{ed["b"]}" data-kind="{ed["kind"]}">')
        out += E.edge_svg(ed["pts"], colour, dashed, head, gap=E.end_gap(head, nodes[ed["b"]]["bold"]))
        out.append('</g>')
    labelled = [(i, ed) for i, ed in enumerate(edges) if ed.get("label_at")]
    if labelled:              # edge labels (decor.place_edge_labels), above every connector
        out.append('<g id="edge-labels" class="labels">')
        for i, ed in labelled:
            out.append(f'<g class="edge-label" data-edge="edge-{i}" data-a="{ed["a"]}" data-b="{ed["b"]}">')
            out += decor.edge_label_svg(ed["label_at"], T)
            out.append('</g>')
        out.append('</g>')
    for b in boxes.values():
        nd = nodes[b.id]
        extra = f' data-flags="{" ".join(nd["flags"])}"' if nd.get("flags") else ""
        out.append(f'<g id="node-{b.id}" class="node" data-node="{b.id}" data-role="{b.role}" data-stage="{b.cluster}"{extra}>')
        out += E.node_svg(b, T["roles"][b.role], marks if nd.get("lab") else None)
        if nd.get("bars"):
            out.append('<g class="rangebar">')
            for br in nd["bars"]:
                out += decor.bar_svg(br, T, b.role)
            out.append('</g>')
        if nd.get("badges"):
            out.append('<g class="glyphs">')
            for bd in nd["badges"]:
                out += decor.badge_svg(bd, T, b.role, T["cl_fill"])
            out.append('</g>')
        out.append('</g>')
    if g.get("credit"):       # the illustration credit: inside the inset at its bottom right, else the page's
        cr = (g.get("inset") or {}).get("credit")
        out.append('<g id="credit" class="credit">')
        out += (decor.credit_svg(cr["text"], cr["x"], cr["y"], T, cr.get("fs", decor.CREDIT_FS)) if cr else
                decor.credit_svg(g["credit"], x_max, H - 14, T))
        out.append('</g>')
    out.append("</svg>")
    svg = "\n".join(out)
    svg_path = case.dir / f"{stem}.svg"
    svg_path.write_text(svg, encoding="utf-8", newline="\n")
    rasterise(svg_path, stem, W, T["sans"])
    print(f"{stem}.svg {W:.0f}x{H:.0f}")
    return W, H


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    import style
    from case import Case, CaseError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("case_dir")
    ap.add_argument("--theme", help="palette id (default: style.print_palette)")
    ap.add_argument("--out", default="final")
    ap.add_argument("--geometry", default="geometry.json")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    try:
        style.configure(a.config)
        render(Case(a.case_dir, trust=a.trust_case), a.theme, a.out, a.geometry)
    except (CaseError, ValueError, style.StyleError) as ex:
        raise SystemExit(str(ex))


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
