# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""geometry.json -> <slug>.drawio, the editable master with the same geometry as final-<palette>.svg.

* Each cluster is a draw.io container; its nodes are children (drag a stage and its boxes follow).
* Every edge is attached to its source and target (fixed exit/entry ports), uses draw.io's
  orthogonal edge style, and carries its bend points, so it opens exactly as routed here and
  re-routes orthogonally if a box is moved.
* Labels are HTML with explicit line breaks, so draw.io never re-wraps a line. Text uses
  the configured sans (fonts.sans). Stage headers, the title and the group name use
  the configured mono (fonts.mono). With fonts.page_fonts = "google" each cell also carries a Google
  Fonts source for its face; with "local" (the default) every used face travels inside the master as a data URI and a distinct family alias.
  These fonts also render on machines without installed copies. Colours are the palette's, as in its PDF.

Glyph layers (decor.py): stage numbers (ellipse cells) and glyphs (SVG image cells) are
children of their stage container; a box's badges (one SVG image each: disk and icon) and range bars
(native rect/ellipse cells) are children of the box, so they move with it; abnormal-value marks are
<font color> runs in the label, and a box with bars keeps its text above them with spacingBottom. The
ribbon is cells on the page; the key draws every layer the diagram uses.

The anatomy inset is a container like the key (fill: the recoloured image's card colour) holding
the image as an image cell with a pre-rounded alpha PNG (so draw.io shows the rounding), its label text cells,
leader lines (edges with a card-coloured halo under them) and anchor dots (an open ring for a bold label). A labelled
connector carries its label as the edge's value, placed along the route where the PDF has it (mxGeometry x), on a
canvas-coloured background. The illustration credit is a text cell in the inset at its bottom right, or at
the page's bottom right when the geometry has no inset credit.

Write one .drawio per configured palette (write(case, theme)): <slug>.drawio for the print palette and
<slug>-<palette>.drawio for the others. Lines have round caps and joins (draw.io's linecap/linejoin style keys, which its canvas
applies to the line and to the arrowhead drawn after it, so the block arrowhead's corners round too). Arrowheads
and T-bars stop core.end_gap short of their box (0.35 px more on a bold border), as in the PDF: the entry point is
moved back along the last segment by entry_back (entryDx/entryDy with entryPerimeter=0; draw.io ignores
targetPerimeterSpacing on a fixed entry point). draw.io's block arrowhead already stops DRAWIO_BLOCK_SHORT short of
its end point; its T-bar sits on it.

    python3 drawio.py <case_dir> [--theme <palette id>]       (after layout_engine.py)
"""
import base64, html, json, pathlib, re, sys, urllib.parse

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)
import core as E
import decor, themes
from render import KEY_ROLES, KEY_ARROWS, TITLE_FS, SUB_FS, GROUP_FS, NAMES_FS, KEY_FS, used_layers
from themes import THEMES

T = ROLE = EDGE = None                        # set by use_theme()
FONT = MONO = ""                              # the faces' style keys, set by use_theme()
HOST = "lt-suite"                             # the mxfile host attribute
ROUND = "linecap=round;linejoin=round;"       # pill-ended lines and dashes, rounded arrowhead corners


def font_styles(T):
    """(text face, mono face) as draw.io style keys: fontFamily, and a Google Fonts fontSource only with
    fonts.page_fonts = "google"."""
    import style
    sans, mono = T["sans"], T["mono"]
    if style.page_fonts() != "google":
        return f"fontFamily={sans};", f"fontFamily={mono};"
    w0, w1 = E.WGHT
    src = urllib.parse.quote(f"https://fonts.googleapis.com/css2?family={sans.replace(' ', '+')}:ital,wght@0,{w0};0,{w1};"
                             f"1,{w0};1,{w1}&display=swap", safe="")
    msrc = urllib.parse.quote(f"https://fonts.googleapis.com/css2?family={mono.replace(' ', '+')}:wght@700&display=swap", safe="")
    return f"fontFamily={sans};fontSource={src};", f"fontFamily={mono};fontSource={msrc};"


def use_theme(theme):
    """Draw with this palette (module globals: every helper below reads T, ROLE, EDGE, FONT and MONO)."""
    global T, ROLE, EDGE, FONT, MONO
    T = THEMES[theme]
    ROLE, EDGE = T["roles"], T["edges"]
    FONT, MONO = font_styles(T)


def out_name(slug, theme):
    """The .drawio's file name: the print palette's master keeps the plain name."""
    return themes.out_name(slug, theme, "drawio")


def anat_dash(width=1.5):
    return f"dashed=1;dashPattern={E.round_dash(E.ANAT_DASH, width)};fixDash=1;" + ROUND
AGENT = f"lt-suite mechanistic-diagram engine {E.ENGINE_VERSION}"



def n(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def f6(v):
    """Port fractions need more precision than coordinates: 0.01 of a 1300 px bar is 13 px."""
    return f"{v:.6f}".rstrip("0").rstrip(".")


def attr(s):
    return html.escape(str(s), quote=True)


def identifier(value):
    """Reject identifiers that can alter XML structure or output paths."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*", value):
        raise ValueError(f"invalid diagram identifier: {value!r}")
    return attr(value)


class Doc:
    def __init__(self, font_aliases=None):
        self.font_aliases = font_aliases or {}
        self.cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    def font_style(self, style):
        """Select a bundled static face matching draw.io's bold and italic flags."""
        flags = re.findall(r"(?:^|;)fontStyle=(\d+);", style)
        flags = int(flags[-1]) if flags else 0
        resolved = []
        def face(match):
            family = match.group(1)
            alias = self.font_aliases.get((family, bool(flags & 1), bool(flags & 2)))
            if alias:
                resolved.append(alias)
            return f"fontFamily={alias or family};"
        style = re.sub(r"fontFamily=([^;]+);", face, style)
        if resolved:
            # The embedded face already has the selected outlines. Disable browser synthesis.
            style = re.sub(r"fontStyle=\d+;", "", style) + "fontStyle=0;"
        return style

    def vertex(self, cid, value, style, x, y, w, h, parent="1"):
        cid, parent = identifier(cid), identifier(parent)
        style = attr(self.font_style(style))
        self.cells.append(f'<mxCell id="{cid}" value="{attr(value)}" style="{style}" vertex="1" parent="{parent}">'
                          f'<mxGeometry x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" as="geometry"/></mxCell>')

    def edge(self, cid, style, src=None, tgt=None, pts=(), parent="1", sp=None, tp=None, value="", label_x=None):
        cid, parent = identifier(cid), identifier(parent)
        src = identifier(src) if src is not None else None
        tgt = identifier(tgt) if tgt is not None else None
        style = attr(self.font_style(style))
        st = f' source="{src}"' if src else ""
        tt = f' target="{tgt}"' if tgt else ""
        lx = f' x="{f6(label_x)}"' if label_x is not None else ""
        geo = [f'<mxGeometry{lx} relative="1" as="geometry">']
        if sp:
            geo.append(f'<mxPoint x="{n(sp[0])}" y="{n(sp[1])}" as="sourcePoint"/>')
        if tp:
            geo.append(f'<mxPoint x="{n(tp[0])}" y="{n(tp[1])}" as="targetPoint"/>')
        if pts:
            geo.append('<Array as="points">' + "".join(f'<mxPoint x="{n(x)}" y="{n(y)}"/>' for x, y in pts) + '</Array>')
        geo.append('</mxGeometry>')
        self.cells.append(f'<mxCell id="{cid}" value="{attr(value)}" style="{style}" edge="1" parent="{parent}"{st}{tt}>'
                          + "".join(geo) + '</mxCell>')


def node_style(role, bold):
    fill, border, text = ROLE[role]
    s = (f"rounded=1;absoluteArcSize=1;arcSize=14;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={border};"
         f"fontColor={text};{FONT}fontSize={E.FS};spacing=4;strokeWidth={'2.2' if bold else '1.5'};")
    if bold:
        s += "fontStyle=1;"
    if role == "anat":
        s += anat_dash(2.2 if bold else 1.5)
    return s


DRAWIO_BLOCK_SHORT = 0.93     # draw.io's block arrowhead (endSize 7, stroke 1.5, linejoin=round) shows its tip this far
                              # before its end point: the unstroked tip sits 1.118 x stroke back and the round join
                              # reaches stroke / 2 past it


def entry_back(head, bold=False):
    """How far an edge's entry point sits back from the middle of the target's border (entryDx/entryDy):
    core.end_gap, less the block arrowhead's own shortfall (a T-bar sits on its end point)."""
    return E.end_gap(head, bold) - (DRAWIO_BLOCK_SHORT if head == "arrow" else 0)


def edge_style(kind, exit_=None, entry=None, end_dir=None, bold=False):
    """end_dir: the last segment's unit direction; the entry point moves back along it by the head's gap
    (core.end_gap; bold: the target box has the bold border)."""
    colour, dashed, head = EDGE[kind]
    s = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;arcSize=12;html=1;jumpStyle=none;strokeColor={colour};"
         f"strokeWidth={E.EDGE_W};startArrow=none;" + ROUND)
    width = E.EDGE_W
    if head == "arrow":
        s += "endArrow=block;endFill=1;endSize=7;"
        back = entry_back(head, bold)
    else:
        width = E.EDGE_W + 0.15
        s += f"endArrow=baseDash;endFill=0;endSize={n(E.TEE_LEN)};strokeWidth={n(width)};"
        back = entry_back(head, bold)
    if dashed:
        s += f"dashed=1;dashPattern={E.round_dash(E.DEP_DASH, width) if dashed is True else dashed};fixDash=1;"
    if exit_ is not None:
        dx, dy = (-end_dir[0] * back, -end_dir[1] * back) if end_dir else (0, 0)
        s += (f"exitX={f6(exit_[0])};exitY={f6(exit_[1])};exitDx=0;exitDy=0;exitPerimeter=1;"
              f"entryX={f6(entry[0])};entryY={f6(entry[1])};entryDx={n(dx)};entryDy={n(dy)};"
              f"entryPerimeter={0 if end_dir else 1};")
    return s


# ------------------------------------------------------------------ glyph layers (decor.py)
DECO = "connectable=0;movable=0;resizable=0;rotatable=0;editable=0;"   # badges and bars ride on their box


def image_style(svg):
    """An image cell holding an SVG document (draw.io's own form: base64 after "data:image/svg+xml,")."""
    b64 = base64.b64encode(svg.encode()).decode()
    return f"shape=image;html=1;imageAspect=0;aspect=fixed;image=data:image/svg+xml,{b64};" + DECO


def badge_doc(bd, role, T, ground):
    """The SVG of a badge on a box, as render.py draws it (decor.badge_svg)."""
    size = 2 * bd["r"] + 2
    if bd["kind"] == "role":
        fill, border, _ = T["roles"][role]
        return decor.icon_doc(bd["icon"], border, size=size, disk=fill, ring=border, ring_w=1.2,
                              icon_px=decor.ROLE_ICON_PX), size
    return decor.icon_doc(bd["icon"], decor.flag_icon_colour(bd["kind"], T), size=size, disk=decor.flag_colour(bd["kind"], T),
                          ring=ground, ring_w=1.5, icon_px=decor.FLAG_ICON_PX), size


def add_badge(d, cid, bd, role, ox, oy, parent, ground):
    doc, size = badge_doc(bd, role, T, ground)
    d.vertex(cid, "", image_style(doc), bd["cx"] - size / 2 - ox, bd["cy"] - size / 2 - oy, size, size, parent=parent)


def add_number(d, cid, n_, cx, cy, r, ox, oy, parent, size=decor.BADGE_FS):
    """A stage number (decor.number_badge_svg): a disc, or a pill for two digits."""
    size = decor.badge_fs(n_, size)
    w = decor.badge_w(n_, r, size)
    shape = "ellipse;" if w <= 2 * r + 0.01 else f"rounded=1;absoluteArcSize=1;arcSize={n(2 * r)};"
    d.vertex(cid, str(n_), (f"{shape}html=1;whiteSpace=nowrap;fillColor={T['cl_text']};strokeColor=none;fontColor={T['canvas']};"
                            f"{MONO}fontStyle=1;fontSize={n(size)};spacing=0;align=center;verticalAlign=middle;" + DECO),
             cx - w / 2 - ox, cy - r - oy, w, 2 * r, parent=parent)


def add_icon(d, cid, name, colour, cx, cy, size, ox, oy, parent):
    d.vertex(cid, "", image_style(decor.icon_doc(name, colour, size=size)), cx - size / 2 - ox, cy - size / 2 - oy,
             size, size, parent=parent)


def add_bar(d, cid, br, role, ox, oy, parent):
    """A range bar (decor.bar_svg) as draw.io shapes: track, normal band, value dot, chevron when off the scale."""
    fill, border, _ = T["roles"][role]
    track, band = decor.bar_colours(T, role)
    col = decor.lab_colour(br["dir"], T) if br["dir"] else border
    y = br["y"]
    d.vertex(f"{cid}_track", "", f"rounded=1;absoluteArcSize=1;arcSize=1.2;html=1;fillColor={track};strokeColor=none;" + DECO,
             br["x0"] - ox, y - 0.6 - oy, br["x1"] - br["x0"], 1.2, parent=parent)
    d.vertex(f"{cid}_band", "", f"rounded=1;absoluteArcSize=1;arcSize=6;html=1;fillColor={band};strokeColor=none;" + DECO,
             br["band"][0] - ox, y - 3 - oy, max(1.0, br["band"][1] - br["band"][0]), 6, parent=parent)
    if br["clamp"]:
        sgn = 1 if br["clamp"] == "high" else -1
        cx = br["vx"] + sgn * 7
        doc = (f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
               + decor.chevron_svg(5, 5, sgn, col) + "</svg>")
        d.vertex(f"{cid}_more", "", image_style(doc), cx - 5 - ox, y - 5 - oy, 10, 10, parent=parent)
    d.vertex(f"{cid}_dot", "", f"ellipse;html=1;fillColor={col};strokeColor={fill};strokeWidth=1.2;" + DECO,
             br["vx"] - 3.6 - ox, y - 3.6 - oy, 7.2, 7.2, parent=parent)


def label_html(lines, marks=None):
    """A box's lines as draw.io HTML: italic terms in <i>, abnormal-value marks in their colour."""
    out = []
    for l in lines:
        t = "".join(f"<i>{html.escape(p, quote=False)}</i>" if it else html.escape(p, quote=False) for p, it in E.runs(l))
        for ch, col in (marks or {}).items():
            t = t.replace(ch, f'<font color="{col}">{ch}</font>')
        out.append(t)
    return "<br>".join(out)


def key_rows(d, rows, kx0, ky0):
    """The key's rows (decor.key_layout) inside the KEY container."""
    for k, kid, lab, rx, ry, lx, room in rows:
        x, y, cy = rx - kx0, ry - ky0, ry + 10 - ky0
        tag = f"{k}_{kid}" if kid else k
        if k == "role":
            fill, border, _ = ROLE[kid]
            d.vertex(f"key_sw_{kid}", "", "rounded=1;absoluteArcSize=1;arcSize=12;html=1;" + f"fillColor={fill};strokeColor={border};strokeWidth=1.5;"
                     + (anat_dash() if kid == "anat" else ""), x, y, 34, 20, parent="key")
            ic = decor.role_icon(CASE, kid)
            if ic and decor.roles_on(CASE):
                add_badge(d, f"key_gl_{kid}", dict(kind="role", icon=ic, cx=rx + 3, cy=ry + 1, r=decor.ROLE_R), kid, kx0, ky0,
                          "key", T["key_fill"])
        elif k == "stage":
            add_number(d, "key_stage", 1, rx + 11, ry + 10, decor.STAGE_BADGE_R, kx0, ky0, "key")
        elif k == "arrow":
            st = edge_style(kid).replace("edgeStyle=orthogonalEdgeStyle;", "edgeStyle=none;")
            d.edge(f"key_ar_{kid}", st, parent="key", sp=(x, cy), tp=(x + 52, cy))
        elif k == "flag":
            add_badge(d, f"key_fl_{kid}", dict(kind=kid, icon=themes.FLAGS[kid][0], cx=rx + 26, cy=ry + 10, r=decor.FLAG_R),
                      None, kx0, ky0, "key", T["key_fill"])
        elif k == "mark":
            hi, lo = decor.lab_colour("high", T), decor.lab_colour("low", T)
            d.vertex("key_mark", f'<font color="{hi}">{themes.LAB_MARK["high"]}</font>&nbsp;&nbsp;<font color="{lo}">{themes.LAB_MARK["low"]}</font>',
                     f"text;html=1;align=center;verticalAlign=middle;spacing=0;{FONT}fontSize={KEY_FS};fontColor={T['ink']};",
                     x, y - 2, 52, 24, parent="key")
        elif k == "bar":
            fill, border, _ = ROLE["inv"]
            d.vertex("key_barbox", "", f"rounded=1;absoluteArcSize=1;arcSize=12;html=1;fillColor={fill};strokeColor={border};strokeWidth=1.5;",
                     x - 2, y, 56, 20, parent="key")
            add_bar(d, "key_bar", dict(x0=rx + 6, x1=rx + 38, y=ry + 10, band=[rx + 12, rx + 24], vx=rx + 38, clamp="high",
                                       dir="high"), "inv", kx0, ky0, "key")
        d.vertex(f"key_lb_{tag}", html.escape(lab, quote=False), f"text;html=1;align=left;verticalAlign=middle;spacing=0;{FONT}"
                 f"fontSize={KEY_FS};fontColor={T['ink']};", lx - kx0, y - 2, room + 4, 24, parent="key")


CASE = None


def label_pos(pts, lb):
    """draw.io's relative label position (-1 at the source end .. 1 at the target end, by length along the route)
    of the point lb (x, y) on the polyline pts."""
    segl = [abs(q[0] - p[0]) + abs(q[1] - p[1]) for p, q in zip(pts, pts[1:])]
    total, run = sum(segl), 0.0
    for (p, q), L in zip(zip(pts, pts[1:]), segl):
        if decor._seg_hits_rect(p, q, (lb["x"], lb["y"], lb["x"], lb["y"]), 0.6):
            run += abs(lb["x"] - p[0]) + abs(lb["y"] - p[1])
            break
        run += L
    return 2 * run / total - 1 if total else 0.0


def add_inset(d, ins, case):
    """The anatomy inset (decor.inset_geom) as a container: header, image cell, leaders, dots and labels."""
    x0, y0, x1, y1 = ins["rect"]
    card = (ins["images"].get(T["flavour"]) or next(iter(ins["images"].values())))["card"]
    d.vertex("inset", html.escape(ins["title"], quote=False),
             ("rounded=1;absoluteArcSize=1;arcSize=24;whiteSpace=wrap;html=1;"
              f"fillColor={card};strokeColor={T['cl_stroke']};strokeWidth=1.2;verticalAlign=top;align=left;spacing=0;"
              f"spacingLeft={n(ins['title_x'])};spacingTop=10;fontStyle=1;fontSize={E.CL_FS};fontColor={T['cl_text']};{MONO}"
              "container=1;collapsible=0;recursiveResize=0;"), x0, y0, x1 - x0, y1 - y0)
    if ins.get("icon"):
        col = themes.glyph_colour(ins["accent"], T["flavour"]) if ins.get("accent") else T["cl_text"]
        add_icon(d, "inset_icon", ins["icon"], col, x0 + decor.TITLE_X + decor.STAGE_ICON / 2, y0 + decor.HEADER_CY,
                 decor.STAGE_ICON, x0, y0, "inset")
    im = ins["img"]
    if im.get("vector"):       # the trace as an SVG image cell: vector in draw.io too, corners clipped in the SVG
        d.vertex("inset_image", "", image_style(decor.inset_vector_svg(ins, case.dir, T["flavour"], standalone=True)),
                 im["x"] - x0, im["y"] - y0, im["w"], im["h"], parent="inset")
    else:
        png = decor.inset_image_png(ins, case.dir, T["flavour"])
        d.vertex("inset_image", "", "shape=image;html=1;imageAspect=0;aspect=fixed;image=data:image/png,"
                 + base64.b64encode(png).decode() + ";" + DECO, im["x"] - x0, im["y"] - y0, im["w"], im["h"], parent="inset")
    for j, l in enumerate(ins["labels"]):
        (lx, ly), (ax, ay) = l["lead"]
        for tag, colour, width in (("halo", card, decor.LEADER_HALO), ("lead", T["ink"], decor.LEADER_W)):
            d.edge(f"inset_{tag}{j}", f"edgeStyle=none;html=1;endArrow=none;startArrow=none;strokeColor={colour};"
                   f"strokeWidth={width};" + ROUND + DECO, parent="inset", sp=(lx - x0, ly - y0), tp=(ax - x0, ay - y0))
        if l.get("ring"):        # a bold label's open ring round its anchor (decor.inset_svg), on a card-coloured halo
            r = l["ring"]
            for tag, colour, width in (("ringhalo", card, decor.INSET_RING_W + 2.4), ("ring", decor.ring_colour(T), decor.INSET_RING_W)):
                d.vertex(f"inset_{tag}{j}", "", f"ellipse;html=1;fillColor=none;strokeColor={colour};strokeWidth={n(width)};" + DECO,
                         l["ax"] - r - x0, l["ay"] - r - y0, 2 * r, 2 * r, parent="inset")
        else:
            r = decor.ANCHOR_R
            d.vertex(f"inset_dot{j}", "", f"ellipse;html=1;fillColor={T['ink']};strokeColor={card};strokeWidth=1.4;" + DECO,
                     ax - r - x0, ay - r - y0, 2 * r, 2 * r, parent="inset")
        w = l["w"] + 8
        left = l["side"] == "left"
        d.vertex(f"inset_label{j}", html.escape(l["text"], quote=False),
                 f"text;html=1;align={'right' if left else 'left'};verticalAlign=middle;spacing=0;whiteSpace=nowrap;{FONT}"
                 f"fontSize={decor.INSET_LABEL_FS};fontColor={T['ink']};" + ("fontStyle=1;" if l["bold"] else ""),
                 (l["x"] - w if left else l["x"]) - x0, l["y"] - 10 - y0, w, 20, parent="inset")
    cr = ins.get("credit")
    if cr:                       # the illustration credit, inside the panel at its bottom right (as in the PDF)
        fs = cr.get("fs", decor.CREDIT_FS)
        d.vertex("credit", html.escape(cr["text"], quote=False),
                 f"text;html=1;align=right;verticalAlign=bottom;spacing=0;whiteSpace=nowrap;{FONT}fontSize={n(fs)};"
                 f"fontColor={T['label']};", cr["x"] - cr["w"] - 12 - x0, cr["y"] + 4 - 18 - y0, cr["w"] + 12, 18, parent="inset")


def rel(b, p):
    return ((p[0] - (b["x"] - b["w"] / 2)) / b["w"], (p[1] - (b["y"] - b["h"] / 2)) / b["h"])


def write(case, theme=None):
    """Write <case_dir>/<slug>.drawio (or <slug>-<theme>.drawio) from geometry.json in a palette. Returns its path."""
    theme = theme or themes.print_palette()
    use_theme(theme)
    identifier(case.slug)
    out_path = case.dir / out_name(case.slug, theme)
    g = json.loads((case.dir / "geometry.json").read_text())
    E.fonts_for_geometry(g)
    roles, arrows, ROLE_NAME = case.key_roles(KEY_ROLES), case.key_arrows(KEY_ARROWS), case.role_name
    CLUSTERS = case.content.CLUSTERS
    nodes, rects, edges = g["nodes"], g["clusters"], g["edges"]
    allr = list(rects.values()) + ([g["inset"]["rect"]] if g.get("inset") else [])
    xs = [r[0] for r in allr] + [r[2] for r in allr]
    x_min, x_max = min(xs), max(xs)
    y_max = max(r[3] for r in allr)
    W, H = x_max + x_min, y_max + 40
    import fontset, style
    font_aliases, ext_fonts = ({}, "") if style.page_fonts() == "google" else fontset.drawio_fonts(
        " ".join([json.dumps(g, ensure_ascii=False), json.dumps(case.meta, ensure_ascii=False),
                  json.dumps(CLUSTERS, ensure_ascii=False), json.dumps(ROLE_NAME, ensure_ascii=False)]))
    d = Doc(font_aliases)
    global CASE
    CASE = case
    layers = "stages" in g          # glyph layers (decor.py); an older geometry draws as before
    stages = g.get("stages") or {}
    tstyle = f"text;html=1;verticalAlign=top;spacing=0;whiteSpace=nowrap;{FONT}"
    mstyle = f"text;html=1;verticalAlign=top;spacing=0;whiteSpace=nowrap;{MONO}fontStyle=1;"
    d.vertex("title", html.escape(case.title, quote=False),
             mstyle + f"align=left;fontSize={TITLE_FS};fontColor={T['ink']};", x_min, 28, 1000, 42)
    d.vertex("subtitle", html.escape(case.subtitle, quote=False), tstyle + f"align=left;fontSize={SUB_FS};fontColor={T['muted']};", x_min, 78, 1300, 28)
    d.vertex("group", html.escape(case.group, quote=False), mstyle + f"align=right;fontSize={GROUP_FS};fontColor={T['ink']};",
             x_max - 400, 38, 400, 30)
    d.vertex("members", html.escape("  ·  ".join(case.members), quote=False), tstyle + f"align=right;fontSize={NAMES_FS};fontColor={T['muted']};",
             x_max - 900, 80, 900, 26)

    # KEY: a container on the stage grid (same geometry as render.key_block)
    kx0, ky0, kx1, ky1 = g["key"]
    kw, kh = kx1 - kx0, ky1 - ky0
    d.vertex("key", "KEY", ("rounded=1;absoluteArcSize=1;arcSize=24;whiteSpace=wrap;html=1;"
                            f"fillColor={T['key_fill']};strokeColor={T['key_stroke']};strokeWidth=1.2;verticalAlign=top;"
                            f"align=left;spacing=0;spacingLeft=16;spacingTop=10;fontStyle=1;fontSize={E.CL_FS};"
                            f"fontColor={T['cl_text']};{MONO}container=1;collapsible=0;recursiveResize=0;"), kx0, ky0, kw, kh)
    if layers:
        rows, _ = decor.key_layout(kx0, ky0, kw, kh, case, used_layers(g))
        key_rows(d, rows, kx0, ky0)
    step = min(44, (kh - 66) / len(roles))
    for i, r in enumerate(roles if not layers else ()):
        fill, border, text = ROLE[r]
        yy = 48 + i * step
        sw = ("rounded=1;absoluteArcSize=1;arcSize=12;html=1;" + f"fillColor={fill};strokeColor={border};strokeWidth=1.5;"
              + (anat_dash() if r == "anat" else ""))
        d.vertex(f"key_sw_{r}", "", sw, 18, yy, 34, 20, parent="key")
        d.vertex(f"key_lb_{r}", html.escape(ROLE_NAME[r], quote=False), f"text;html=1;align=left;verticalAlign=middle;spacing=0;{FONT}"
                 f"fontSize={KEY_FS};fontColor={T['ink']};", 64, yy - 2, 240, 24, parent="key")
    col2 = 18 + 46 + max(E.text_w(ROLE_NAME[r]) for r in roles) + 40
    room = kw - 16 - (col2 + 66)
    for i, (lab, kind) in enumerate(arrows if not layers else ()):
        yy = 48 + 10 + i * (step * (len(roles) - 1) / max(1, len(arrows) - 1))
        st = edge_style(kind).replace("edgeStyle=orthogonalEdgeStyle;", "edgeStyle=none;")
        d.edge(f"key_ar_{kind}", st, parent="key", sp=(col2, yy), tp=(col2 + 52, yy))
        lines = [lab] if E.text_w(lab) <= room else E.balanced(lab, 2)[0]
        hh = len(lines) * E.LH + 4
        d.vertex(f"key_al_{kind}", "<br>".join(html.escape(l, quote=False) for l in lines),
                 f"text;html=1;align=left;verticalAlign=middle;spacing=0;{FONT}fontSize={KEY_FS};fontColor={T['ink']};",
                 col2 + 66, yy - hh / 2, room, hh, parent="key")

    # the time-course ribbon (decor.timeline_geom)
    tl = g.get("timeline")
    if tl:
        y = tl["y"]
        d.vertex("tl_label", html.escape(tl["label"], quote=False), mstyle + f"align=left;verticalAlign=middle;fontSize={E.CL_FS};"
                 f"fontColor={T['cl_text']};", tl["x0"], y - 12, decor.mono_w(tl["label"], E.CL_FS) + 8, 24)
        line = T["muted"]
        segs = decor.timeline_segments(tl)
        for i, (a, b_) in enumerate(segs):
            last = i == len(segs) - 1
            d.edge(f"tl_line{i}", f"edgeStyle=none;html=1;strokeColor={line};strokeWidth=1.2;startArrow=none;" + ROUND
                   + ("endArrow=block;endFill=1;endSize=7;" if last else "endArrow=none;"), sp=(a, y), tp=(b_, y))
        for m in tl["markers"]:
            sid = m["stage"]
            add_number(d, f"tl_n_{sid}", m["n"], m["x"], y, decor.TL_BADGE_R, 0, 0, "1", size=decor.TL_BADGE_FS)
            tx = m["x"] + m.get("bw", 2 * decor.TL_BADGE_R) / 2 + 6
            if m["icon"]:
                add_icon(d, f"tl_ic_{sid}", m["icon"], decor.stage_colour(m, T), tx + decor.STAGE_ICON / 2, y,
                         decor.STAGE_ICON, 0, 0, "1")
                tx += decor.STAGE_ICON + 5
            d.vertex(f"tl_when_{sid}", html.escape(m["when"], quote=False), f"text;html=1;align=left;verticalAlign=middle;spacing=0;"
                     f"whiteSpace=nowrap;{FONT}fontSize={E.FS};fontColor={T['ink']};", tx, y - 12, E.text_w(m["when"]) + 8, 24)

    if g.get("inset"):         # the anatomy inset
        add_inset(d, g["inset"], case)

    # clusters (containers) and their nodes
    for cid, (x0, y0, x1, y1) in rects.items():
        st = stages.get(cid, {})
        d.vertex(cid, html.escape(CLUSTERS[cid], quote=False), ("rounded=1;absoluteArcSize=1;arcSize=24;whiteSpace=wrap;html=1;"
                                      f"fillColor={T['cl_fill']};strokeColor={T['cl_stroke']};strokeWidth=1.2;verticalAlign=top;"
                                      f"align=left;spacing=0;spacingLeft={n(decor.title_x(st))};spacingTop=10;fontStyle=1;fontSize={E.CL_FS};"
                                      f"fontColor={T['cl_text']};{MONO}container=1;collapsible=0;recursiveResize=0;"),
                 x0, y0, x1 - x0, y1 - y0)
        cy, hx = y0 + decor.HEADER_CY, x0 + decor.TITLE_X
        if st.get("n") is not None:
            bw = decor.badge_w(st["n"], decor.STAGE_BADGE_R)
            add_number(d, f"{cid}_n", st["n"], hx + bw / 2, cy, decor.STAGE_BADGE_R, x0, y0, cid)
            hx += bw + 6
        if st.get("icon"):
            add_icon(d, f"{cid}_icon", st["icon"], decor.stage_colour(st, T), hx + decor.STAGE_ICON / 2, cy, decor.STAGE_ICON,
                     x0, y0, cid)
    marks = {themes.LAB_MARK[k]: decor.lab_colour(k, T) for k in ("high", "low")}
    for nid, b in nodes.items():
        x0, y0 = rects[b["cluster"]][0], rects[b["cluster"]][1]
        label = label_html(b["lines"], marks if b.get("lab") else None)
        style = node_style(b["role"], b["bold"]) + (f"spacingBottom={n(b['foot'])};" if b.get("foot") else "")
        d.vertex(nid, label, style, b["x"] - b["w"] / 2 - x0, b["y"] - b["h"] / 2 - y0, b["w"], b["h"], parent=b["cluster"])
        bx0, by0 = b["x"] - b["w"] / 2, b["y"] - b["h"] / 2
        for j, br in enumerate(b.get("bars", ())):
            add_bar(d, f"{nid}_bar{j}", br, b["role"], bx0, by0, nid)
        for bd in b.get("badges", ()):
            add_badge(d, f"{nid}_{bd['kind']}", bd, b["role"], bx0, by0, nid, T["cl_fill"])

    # edges: fixed ports + bend points (draw.io's segment connector reproduces the route from them)
    for i, ed in enumerate(edges):
        pts = [tuple(p) for p in ed["pts"]]
        ex = rel(nodes[ed["a"]], pts[0])
        en = rel(nodes[ed["b"]], pts[-1])
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 or 1.0
        u = ((x2 - x1) / L, (y2 - y1) / L)
        lb = ed.get("label_at")
        if lb:      # the connector's label: the edge's value, where the PDF has it, on the canvas colour
            st = (edge_style(ed["kind"], ex, en, u, nodes[ed["b"]]["bold"]) + f"fontSize={decor.EDGE_LABEL_FS};{FONT}fontStyle=2;"
                  f"fontColor={T['label']};labelBackgroundColor={T['canvas']};")
            d.edge(f"e{i:03d}_{ed['a']}__{ed['b']}", st, ed["a"], ed["b"], pts[1:-1], value=html.escape(lb["text"], quote=False),
                   label_x=label_pos(pts, lb))
        else:
            d.edge(f"e{i:03d}_{ed['a']}__{ed['b']}", edge_style(ed["kind"], ex, en, u, nodes[ed["b"]]["bold"]), ed["a"], ed["b"],
                   pts[1:-1])
    if g.get("credit") and not (g.get("inset") or {}).get("credit"):   # the credit at the page's bottom right
        d.vertex("credit", html.escape(g["credit"], quote=False),
                 f"text;html=1;align=right;verticalAlign=bottom;spacing=0;whiteSpace=nowrap;{FONT}fontSize={decor.CREDIT_FS};"
                 f"fontColor={T['label']};", x_max - 400, H - 14 - 14, 400, 18)

    model = (f'<mxGraphModel dx="{n(W)}" dy="{n(H)}" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
             f'arrows="1" fold="1" page="1" pageScale="1" pageWidth="{n(W)}" pageHeight="{n(H)}" background="{attr(T["canvas"])}" extFonts="{attr(ext_fonts)}" '
             f'math="0" shadow="0"><root>' + "".join(d.cells) + '</root></mxGraphModel>')
    xml = (f'<mxfile host="{attr(HOST)}" agent="{attr(AGENT)}" version="24.7.17">'
           f'<diagram id="{identifier(case.slug)}" name="{attr(case.diagram_name)}">{model}</diagram></mxfile>\n')
    out_path.write_text(xml)
    print(f"wrote {out_path.name}: {len(d.cells)} cells, {len(xml)} bytes")
    return out_path


def main(argv):
    import argparse
    import style
    from case import Case, CaseError
    ap = argparse.ArgumentParser(description="geometry.json -> the editable .drawio master in one palette.")
    ap.add_argument("case_dir")
    ap.add_argument("--theme", help="palette id (default: style.print_palette)")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    try:
        style.configure(a.config)
        write(Case(a.case_dir, trust=a.trust_case), a.theme)
    except (CaseError, ValueError, style.StyleError) as ex:
        raise SystemExit(str(ex))


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
