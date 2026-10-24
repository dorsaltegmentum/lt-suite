# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check generated PDFs for content survival, layout defects, and format validity.

Content checks compare source lines independently of the renderer's parsed tree.
Layout checks flag stranded headings, empty bullets, markdown, and misplaced continuation labels.
Layout checks also flag missing labels, paragraph breaks, and thin intermediate pages.
Thin pages contain under 700 characters and images below 15% frame coverage.
Format checks verify PDF parsing, image counts, footers, and matching palette text.
Draft PDFs carry page properties in Subject, which metadata checks verify.
Finalised PDFs carry none, so QC runs before the final pass.

CLI: module_pdf_qc.py PAGE OUT [--render DIR] [--config FILE]

Exit codes: 0 means passed, 1 means failed, and 3 means unavailable prerequisites.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
SELF = Path(__file__).resolve().parent
sys.path.insert(0, str(SELF / "_shared"))
sys.path.insert(0, str(SELF))
import ltcore; _MISSING = ltcore.bootstrap()

import module_pdf as B

import argparse
import hashlib
import json
import re

FOOT = B.PW.MARGIN + 10 - 2


def squash(t):
    return re.sub(r"\s+", "", t)


def md_plain(t):
    return B.plain(t)


def wants(page, namespace="Lt"):
    """Collect expected content directly from source lines to detect renderer omissions."""
    lines = Path(page).read_text(encoding="utf-8").split("\n")
    props, i = {}, 0
    while i < len(lines) and not lines[i].startswith("-"):
        m = B.PROP.match(lines[i])
        if m:
            props[m[1]] = m[2]
        i += 1
    # Check other page properties through metadata because they appear outside the body.
    W = [("title", md_plain(re.sub(rf"^{re.escape(namespace)}/", "", props["title"]))),
         ("type", md_plain(props["type"]))]
    head_line = in_props = False
    tdepth = None
    for raw in lines[i:]:
        m = B.NODE.match(raw)
        if m:
            depth, t = len(m[1]), (m[2] or "").strip()
            head_line, in_props = False, True
            if t in ("", "---"):
                continue
            if depth == 0 and t.startswith("# "):
                W.append(("block", md_plain(t[2:])))
                continue
            h = B.HEAD.match(t)
            if h:
                head_line = True
                full = (h[2] or "").strip()
                if full:
                    W.append(("heading", md_plain(B.display_heading(full))))
                    # Check full heading text because shortened headings repeat their source sentence.
                    W.append(("heading-full", md_plain(full.rstrip("…"))))
                continue
            mi = B.ILO.match(t)
            if depth == 0 and mi:
                W.append(("ilo", md_plain(mi[2])))
                continue
            mg = B.IMG.match(t)
            if mg:
                note = mg[3].strip()
                if note.startswith("(") and note.endswith(")"):
                    note = note[1:-1]
                W.append(("image-alt", md_plain(mg[1])))
                if note:
                    W.append(("image-note", md_plain(note)))
                continue
            if tdepth is not None and depth != tdepth:
                tdepth = None
            mc = (B.CELL_ANY if tdepth is not None else B.CELL).match(t)
            if mc:
                W.append(("cell", md_plain(mc[3])))
                if not re.fullmatch(r"column \d+", mc[2]):
                    W.append(("column", md_plain(mc[2])))
                if not re.fullmatch(r"(?:row )?\d+\.?", mc[1]):
                    W.append(("row", md_plain(mc[1])))
                continue
            tdepth = depth if t.startswith("**Table as drafted**") else None
            if t.startswith(("⚠", "Fill:", "**Table as drafted**")) or B.TIER.match(t):
                W.append(("line", md_plain(t)))
                continue
            claim, provs = B.peel(t)
            if claim:
                W.append(("claim", md_plain(claim)))
            for p in provs:
                W.append(("prov", md_plain(p)))
            continue
        s = raw.lstrip("\t")
        s = (s[2:] if s.startswith("  ") else s.strip()).rstrip()
        if not s.strip():
            in_props = False
            continue
        pm = B.PROP.match(s)
        if pm and in_props:
            W.append(("prop", md_plain(pm[2])))
            continue
        in_props = False
        mt = B.ITALIC.match(s.strip())
        if head_line and mt:
            W.append(("meta", md_plain(mt[1])))
            continue
        if s.strip().startswith("|"):
            if B.PIPE_SEP.match(s.strip()):
                continue
            for c in s.strip().strip("|").split("|"):
                if c.strip():
                    W.append(("pipe-cell", md_plain(c.strip())))
            continue
        W.append(("body", md_plain(s.strip())))
    return props, W


def heading_lines(page):
    """Every line the renderer prints as a heading or an item's meta line."""
    _, _, blocks = B.parse(page)
    out, todo = [], []
    for blk in blocks:
        out.append(md_plain(blk["title"]))
        todo += blk["kids"]
    while todo:
        n = todo.pop()
        todo += n.kids
        if B.HEAD.match(n.text):
            _, head, meta, _, _ = B.item_parts(n)
            out.append(md_plain(head))
            if meta:
                out.append(meta)
    return out


def page_texts(pdf):
    """Separate body text and footers, excluding continuation headings from source comparisons."""
    out = []
    for i in range(len(pdf)):
        pg = pdf[i]
        w, h = pg.get_size()
        tp = pg.get_textpage()
        body = tp.get_text_bounded(left=0, bottom=FOOT, right=w, top=h)
        foot = tp.get_text_bounded(left=0, bottom=0, right=w, top=FOOT)
        lines = body.replace("\r", "").split("\n")
        conts = [j for j, x in enumerate(y for y in lines if y.strip())
                 if x.strip().endswith("— continued")]
        lines = [x for x in lines if not x.strip().endswith("— continued")]
        out.append(("\n".join(lines), foot, conts))
    return out


def images_per_page(pdf):
    """Count image objects and top-level forms once to avoid counting nested figures twice."""
    import pypdfium2.raw as pdfium_c

    res = []
    kinds = [pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_FORM]
    for i in range(len(pdf)):
        objs = list(pdf[i].get_objects(filter=kinds, max_depth=1))
        area = 0.0
        for o in objs:
            left, bottom, right, top = o.get_bounds()
            area += (right - left) * (top - bottom)
        res.append((len(objs), area))
    return res


def audit(path, props, W, heads, n_images, title, stamp_property="lt-module", glyph_subs=None):
    """Check draft PDF content, layout, footers, and Subject properties before finalisation.

    Finalised PDFs carry none of these properties, so QC precedes the final pass.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    pt = page_texts(pdf)
    hay = squash("".join(t for t, _, _ in pt))
    subs = glyph_subs or {}      # stand-ins the build used for characters no font had
    missing = [(k, v[:90]) for k, v in W if squash(B.PW.apply_subs(v, subs)) not in hay]
    bad = []
    for leak in ("**", "[[", "`", "\\*"):
        if leak in hay:
            bad.append(("literal markdown", leak))
    # Require a link target because adjacent provenance tags can produce matching punctuation.
    if re.search(r"\]\((?:https?:|\.\./|\./)", hay):
        bad.append(("literal markdown", "](url"))
    imgs = images_per_page(pdf)
    total_imgs = sum(n for n, _ in imgs)
    if total_imgs != n_images:
        bad.append(("image count", f"{total_imgs} in PDF vs {n_images} in page"))
    chars, thin = [], []
    frame = B.AVAIL_W * B.AVAIL_H
    for i, (t, foot, conts) in enumerate(pt):
        if conts and conts != [0]:
            bad.append(("continued label not first on page", i + 1, conts))
        body = [x.strip() for x in t.split("\n") if x.strip()]
        chars.append(len("".join(body)))
        # Match heading suffixes because wrapped headings end on their final printed line.
        if i != len(pt) - 1 and body and len(body[-1]) >= 8 and any(
                h.endswith(body[-1]) for h in heads):
            bad.append(("stranded heading", i + 1, body[-1][:60]))
        for x in body:
            if re.fullmatch(r"[–○•⚠]\s*|\d+\.", x):
                bad.append(("empty bullet", i + 1, x))
        if i != len(pt) - 1 and chars[-1] < 700 and imgs[i][1] < 0.15 * frame:
            thin.append((i + 1, chars[-1]))
        if squash(B.PW.apply_subs(title, subs)) not in squash(foot) or str(i + 1) not in foot:
            bad.append(("footer", i + 1, foot.strip()[:60]))
    meta = pdf.get_metadata_dict()
    for k in (stamp_property, "lt-run", "lt-captured", "lt-exported", "lt-exported-by"):
        if props.get(k) and props[k] not in meta.get("Subject", ""):
            bad.append(("metadata", k))
    n_pages = len(pdf)
    pdf.close()
    return dict(pages=n_pages, chars=chars, images=imgs, missing=missing, bad=bad,
                thin=thin, text_sha=hashlib.sha256(hay.encode()).hexdigest()[:16])


def check(page, out, report, render=None, namespace="Lt", stamp_property="lt-module") -> bool:
    """Audit each palette PDF, print results, and write .qc.json for later inspection."""
    page, out = Path(page), Path(out)
    props, W = wants(page, namespace=namespace)
    n_images = sum(1 for x in page.read_text(encoding="utf-8").split("\n")
                   if (m := B.NODE.match(x)) and B.IMG.match(m[2] or ""))
    heads = heading_lines(page)
    title = md_plain(re.sub(rf"^{re.escape(namespace)}/", "", props["title"]))
    print(f"QC  {len(W)} content strings, {n_images} images, {len(report)} flavours")
    results = {}
    for fl, b in report.items():
        path = b["path"]
        r = audit(path, props, W, heads, n_images, title, stamp_property=stamp_property,
                  glyph_subs=b.get("glyph_subs"))
        r["breaks"] = [f"{k}@p{p}" for k, p in b["splits"]]
        r["para_breaks"] = sum(1 for k, _ in b["splits"] if k == "paragraph")
        if r["para_breaks"]:
            r["bad"].append(("paragraph broken across a page", r["breaks"]))
        for kind in ("missing", "stray"):
            for lab in b["labels"][kind]:
                r["bad"].append((f"continued label {kind}", lab[1], lab[0][:60]))
        results[fl] = r
        print(f"    {fl:14} {r['pages']:3} pages  missing {len(r['missing'])}  "
              f"defects {len(r['bad'])}  thin {r['thin'] or 'none'}  text {r['text_sha']}")
        for x in r["missing"]:
            print("      MISSING", x)
        for x in r["bad"]:
            print("      DEFECT ", x)
        if render:
            import pypdfium2 as pdfium

            rd = Path(render)
            rd.mkdir(parents=True, exist_ok=True)
            pdf = pdfium.PdfDocument(path)
            for i in range(len(pdf)):
                pdf[i].render(scale=1.4).to_pil().save(rd / f"{fl}-p{i + 1:02d}.png")
            pdf.close()
    shas = {r["text_sha"] for r in results.values()}
    ok = (all(not r["missing"] and not r["bad"] and not r["thin"] for r in results.values())
          and len(shas) == 1)
    if len(shas) != 1:
        print(f"    the flavours carry {len(shas)} different texts; they must carry one")
    (out / ".qc.json").write_text(json.dumps(
        {fl: {x: r[x] for x in ("pages", "text_sha", "missing", "bad", "thin", "breaks")}
         for fl, r in results.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("QC  PASS" if ok else "QC  FAIL")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--render", type=Path)
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()

    if {"pypdfium2", "reportlab"} & set(_MISSING):
        print(ltcore.missing_hint(sorted(_MISSING)))
        return 3

    from ltcore import config as config_mod

    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 3

    namespace = cfg.get("notes.page_namespace")
    stamp_property = cfg.get("notes.stamp_property")
    build_path = args.out / ".build.json"
    try:
        report = json.loads(build_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FATAL: {build_path} not found; build the PDFs first", file=sys.stderr)
        return 3

    return 0 if check(
        args.page, args.out, report, args.render,
        namespace=namespace, stamp_property=stamp_property,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
