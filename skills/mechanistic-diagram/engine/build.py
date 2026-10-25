#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mechanistic diagram engine: one command builds every output of a case folder, then checks it.

    python3 build.py <case_dir> [--final] [--config FILE] [--trust-case]

Steps:
  1. load the case (case.json, content.py, layout.py, artifact_content.py) and run the preflight checks;
     set SOURCE_DATE_EPOCH to the newest modification time of the case's sources (case.json, its *.py
     files and the files case.json names; unless it is already set), so a rebuild of unchanged sources
     gives byte-identical SVGs, PDFs, PNGs, .drawio and Artifact page. A case folder is code: one outside
     the lt-suite data folder and the skill's examples/ needs --trust-case on first use and after code changes.
  2. layout_engine: grid, containers, routes -> <case_dir>/geometry.json; any PROBLEM, crossing or
     unrouted edge stops the build
  3. render: final-<palette> as .svg, .pdf (one page, checked) and .png (1.5x) for every palette in the
     config's style.palettes
  4. drawio: one editable master per palette, <slug>.drawio for the print palette (style.print_palette)
     and <slug>-<palette>.drawio for the others (each checked with xmllint); their modification time is
     set to SOURCE_DATE_EPOCH, so the zipped copies in the Artifact page carry a fixed date
  5. mkverify: $TMPDIR/verify.js, draw.io's own engine re-lays the master in a browser (when TMPDIR is set)
  6. deliver/: <slug>-<palette>.pdf for every palette, every .drawio, <slug>.png (print palette) and
     <slug>-<palette>.png for the others, and <slug>.pdf, a copy of the print palette's PDF (the
     submission copy)
  7. make_artifact: the Artifact page (<case_dir>/<artifact_content.OUT_NAME>), a standalone HTML file,
     when artifact_content.py is filled in. Nothing is published or installed.
  8. checks (see README.md, "Gates"):
       access.py        WCAG contrast of every palette and of the rendered SVGs; colour-vision report;
                        checks/cvd/ images                                    stops the build on a failure
       floor.py         the quality minimums in floor.json and the page limits (diagram.page)
                                                                              stops the build on a breach
       textrules.py     widows (a last line of one short word or a lone number)          warning
       guideline.py     treatment boxes against the case's guideline ledger              warning
       consistency.py   the diagram's facts against case.json "sources"                  warning
       diffviz.py       what changed since <case_dir>/accepted/ (when it exists)         summary
  9. --final, after a build that passed every gate: finalize.py over deliver/ (metadata off, deterministic
     PDFs, then any [[text_cleaners]] with stage "diagram"), then the Artifact page is made again so it
     embeds the cleaned files, and finalize.py cleans it too. Every cleaned PDF must still be one page and
     render as its draft did, every PNG keep its pixels; what changed is reported and saved to
     checks/final-report.json.

Without --final everything the build writes is a draft. See README.md for the architecture, the checks
and the regression.
"""
import argparse, io, json, os, pathlib, re, shutil, subprocess, sys

sys.dont_write_bytecode = True       # keep case folders free of __pycache__
import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)
from _legacy import read_legacy_text

ENGINE = pathlib.Path(__file__).resolve().parent


def fail(msg):
    raise SystemExit(f"build stopped: {msg}")


def source_files(case_dir, variant=None):
    """The case's sources: case.json, its *.py files, and every file case.json names by a path inside
    the case folder (an inset's picture, for example). Files the build derives from them (a recoloured
    copy in assets/, the outputs, checks/) are not sources, so writing them cannot move the date.
    A variant's own files (variants.py, layout_<variant>.py) count only for that variant's build
    (variant_build.py), and scratch drafts (*.draft.py) never, so editing a variant leaves the full
    page's date alone."""
    d = pathlib.Path(case_dir).resolve()

    def counts(p):
        if p.name.endswith(".draft.py"):
            return False
        if p.name == "variants.py":
            return variant is not None
        if p.name.startswith("layout_"):
            return variant is not None and p.name == f"layout_{variant}.py"
        return True
    files = [d / "case.json"] + [p for p in sorted(d.glob("*.py")) if counts(p)]

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and 0 < len(x) < 300 and "\n" not in x:
            p = (d / x).resolve()
            if p.is_file() and d in p.parents:
                files.append(p)
    try:
        walk(json.loads(read_legacy_text(d / "case.json")))
    except (OSError, ValueError):
        pass
    return [p for p in files if p.exists()]


def source_epoch(case_dir, variant=None):
    """SOURCE_DATE_EPOCH for a build: the newest modification time (whole seconds) of source_files()."""
    return max(int(p.stat().st_mtime) for p in source_files(case_dir, variant))


def build(case_dir, final=False, trust=False):
    from case import Case, CaseError, CaseTrustError
    import layout_engine, render, drawio, mkverify, make_artifact, style, themes
    try:
        case = Case(case_dir, trust=trust)
        FLAVOURS = themes.final_palettes()
        for fl in FLAVOURS:
            themes.THEMES[fl]                     # derive every palette first: a palette that cannot pass stops here
    except CaseTrustError as ex:
        print(str(ex), file=sys.stderr)
        raise SystemExit(3)
    except (CaseError, style.StyleError) as ex:
        fail(str(ex))
    pre = case.preflight()
    if pre:
        fail("\n  - ".join([f"case {case.dir} is not ready:"] + pre))
    if "SOURCE_DATE_EPOCH" not in os.environ:
        os.environ["SOURCE_DATE_EPOCH"] = str(source_epoch(case.dir))
    epoch = int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        res = layout_engine.run(case)
    except layout_engine.LayoutError as ex:
        fail(f"layout: {ex}")
    if res["problems"] or res["crossings"] or res["unrouted"]:
        fail("layout has problems, crossings or unrouted edges; not building outputs")
    for flavour in FLAVOURS:
        try:
            render.render(case, flavour, f"final-{flavour}")
        except (ValueError, style.StyleError) as ex:
            fail(f"render {flavour}: {ex}")
        info = subprocess.run(["pdfinfo", str(case.dir / f"final-{flavour}.pdf")], capture_output=True, text=True, encoding="utf-8").stdout
        if re.search(r"^Pages:\s+1$", info, re.M) is None:
            fail(f"final-{flavour}.pdf is not a single page")
    masters = []
    for flavour in FLAVOURS:
        m = drawio.write(case, flavour)
        subprocess.run(["xmllint", "--noout", str(m)], check=True)
        os.utime(m, (epoch, epoch))            # the Artifact page's zip entries take their date from here
        masters.append(m)
    if os.environ.get("TMPDIR"):
        mkverify.write(case)
    slug = case.slug
    deliver = case.dir / "deliver"
    deliver.mkdir(exist_ok=True)
    for m in masters:
        shutil.copy2(m, deliver / m.name)
    for flavour in FLAVOURS:
        shutil.copy2(case.dir / f"final-{flavour}.pdf", deliver / f"{slug}-{flavour}.pdf")
        shutil.copy2(case.dir / f"final-{flavour}.png", deliver / themes.out_name(slug, flavour, "png"))
    shutil.copy2(case.dir / f"final-{themes.print_palette()}.pdf", deliver / f"{slug}.pdf")
    print("deliver/:", ", ".join(sorted(p.name for p in deliver.iterdir())))
    import style
    lms, label = style.get("diagram.delivery.lms_name"), style.get("diagram.delivery.submission_label")
    print(f"submission copy: deliver/{case.slug}.pdf" + (f" ({label})" if label else "")
          + (f", handed in through {lms}" if lms else ""))
    page = make_page(case, make_artifact)
    checks(case, res)
    if final:
        finalise(case, page, make_artifact)
    return case


def make_page(case, make_artifact):
    """The Artifact page, when artifact_content.py is filled in; returns its path or None."""
    if case.artifact is None:
        print("artifact: skipped (no artifact_content.py)")
        return None
    todo = make_artifact.unfilled(case.artifact)
    if todo:
        print(f"artifact: skipped (artifact_content.py still needs {', '.join(todo)})")
        return None
    try:
        return make_artifact.make(case)
    except ValueError as ex:
        fail(f"artifact: {ex}")


# ------------------------------------------------------------------------------------------ checks
def checks(case, res):
    """Run every check, print what each found, then stop the build on a contrast failure or a breach
    of the floor (the warnings are printed first, so a failing build still shows them)."""
    import access, floor, textrules, guideline, consistency, diffviz
    print("\nchecks:")
    acc = access.run(case.dir, sims=True, quiet=True)
    print("  " + acc["lines"][-1])
    for k in acc["known"]:
        print(f"    known: {k}")
    for f in acc["fails"]:
        print(f"    FAIL: {f}")
    print(f"    full report: python3 {ENGINE / 'access.py'} {case.dir}  (images in checks/cvd/)")
    F = json.loads(floor.floor_path().read_text(encoding="utf-8"))
    rows, floor_ok = floor.check(case.dir, layout=res, access_result=acc, floor=F, quiet=True)
    print("  " + floor.table(rows, F, floor.Build(case.dir)).replace("\n", "\n  "))
    w = textrules.widows(case.dir / "geometry.json")
    print(f"  widows: {len(w)}" + ("" if not w else " (warning)"))
    for nid, last, why in w:
        print(f"    {nid}: last line {last!r} ({why})")
    lines, _ = guideline.check(case.dir, case.content.NODES)
    print("  " + "\n  ".join(l for l in lines if not l.lstrip().startswith("notes:")))
    if case.meta.get("sources"):
        cons = consistency.run(case, quiet=True)
        print("  " + "\n  ".join(cons["lines"]))
    else:
        print('  consistency: skipped (case.json has no "sources")')
    diff = diffviz.run(case.dir, quiet=True)
    if diff:
        print("  " + "\n  ".join(diff))
    problems = []
    if not acc["ok"]:
        problems.append("contrast below WCAG: " + "; ".join(acc["fails"][:4]) + (" ..." if len(acc["fails"]) > 4 else ""))
    if not floor_ok:
        problems.append("below the floor in floor.json: " + ", ".join(f"{r[0]} ({r[2]})" for r in rows if r[3] == "FAIL"))
    if problems:
        fail("the outputs were written but must not be handed over:\n  - " + "\n  - ".join(problems))
    print("checks passed" + (" (with warnings above)" if any(r[3] == "WARN" for r in rows) or w else ""))


# ------------------------------------------------------------------------------------------ --final
def _pdf_pages_and_render(data, tag, tmp):
    p = pathlib.Path(tmp) / f"{tag}.pdf"
    p.write_bytes(data)
    info = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, encoding="utf-8").stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    subprocess.run(["pdftoppm", "-r", "30", "-png", "-singlefile", str(p), str(pathlib.Path(tmp) / tag)], check=True)
    return (int(m.group(1)) if m else 0), (pathlib.Path(tmp) / f"{tag}.png").read_bytes()


def _same_pixels(a, b):
    import numpy as np
    from PIL import Image
    pa = np.asarray(Image.open(io.BytesIO(a)).convert("RGBA"))
    pb = np.asarray(Image.open(io.BytesIO(b)).convert("RGBA"))
    return pa.shape == pb.shape and not np.any(pa != pb)


def finalise(case, page, make_artifact):
    """finalize.py over deliver/, the Artifact page made again from the cleaned files, then cleaned too;
    every PDF still one page and render-identical, every PNG pixel-identical."""
    import tempfile
    import finalize
    deliver = case.dir / "deliver"
    print("\nfinal: cleaning deliver/ (metadata, deterministic PDFs, configured cleaners)")
    drafts = {p: p.read_bytes() for p in sorted(deliver.iterdir()) if p.is_file()}
    results = finalize.clean([deliver])
    if page is not None:
        epoch = int(os.environ["SOURCE_DATE_EPOCH"])
        for m in deliver.glob("*.drawio"):
            os.utime(m, (epoch, epoch))            # cleaning may have rewritten them
        page = make_artifact.make(case)          # embed the cleaned PDFs and .drawio, not the drafts
        print(f"final: cleaning {page.name}")
        results += finalize.clean([page])
    for r in results:
        print("  " + finalize.describe(r))
    bad = [r for r in results if r["status"] in ("error", "kept-original") or r.get("residual")]
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        for p, before in drafts.items():
            after = p.read_bytes()
            if p.suffix == ".pdf":
                n0, r0 = _pdf_pages_and_render(before, "draft", tmp)
                n1, r1 = _pdf_pages_and_render(after, "final", tmp)
                ok = n1 == 1 and _same_pixels(r0, r1)
                print(f"  check {p.name}: {n1} page, " + ("renders as the draft did" if ok else "DIFFERS from the draft"))
            elif p.suffix == ".png":
                ok = _same_pixels(before, after)
                print(f"  check {p.name}: " + ("pixels unchanged" if ok else "PIXELS CHANGED"))
            else:
                continue
            if not ok:
                bad.append({"file": str(p), "status": "verify", "detail": "cleaning changed what the file shows"})
    (case.dir / "checks").mkdir(exist_ok=True)
    (case.dir / "checks" / "final-report.json").write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    if bad:
        fail("--final: " + "; ".join(f"{pathlib.Path(r['file']).name} {r['status']}" for r in bad)
             + " (deliver/ may now mix cleaned and draft files: rebuild before handing anything over)")
    print(f"final: {sum(r['status'] == 'cleaned' for r in results)} cleaned, "
          f"{sum(r['status'] == 'already-clean' for r in results)} already clean; deliver/ and "
          f"{page.name if page else 'no Artifact page'} are final (report: checks/final-report.json)")


def main(argv=None):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Build every output of a case folder, then check it.",
                                 epilog=f"start a new case with: python3 {ENGINE / 'new_case.py'} <case_dir>")
    ap.add_argument("case_dir")
    ap.add_argument("--final", action="store_true",
                    help="after a build that passes every gate, clean deliver/ and the Artifact page "
                         "(metadata off, deterministic PDFs, then any [[text_cleaners]] with stage \"diagram\")")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true",
                    help="run the case folder's Python even when it is outside the current folder, the lt-suite "
                         "data folder and the skill's examples/ (read content.py and layout.py first)")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
    except style.StyleError as ex:
        fail(str(ex))
    build(a.case_dir, final=a.final, trust=a.trust_case)


if __name__ == "__main__":
    _bootstrap.entry()
    main()
