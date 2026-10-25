#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build one variant of a case (a smaller "core" page, or any cut that variants.py names).

    python3 variant_build.py <case_dir> --variant core
    python3 variant_build.py <case_dir> --variant core --autoplace    # no layout_core.py yet: draft one first

The variant's boxes come from the case's own content.py, cut by variants.py (see case.py), and are laid out
by layout_<variant>.py. The steps are build.py's: preflight -> layout (any PROBLEM, crossing or unrouted
edge stops) -> render every palette in style.palettes (one page each)
-> one .drawio per palette (xmllint) -> verify.js -> deliver/.
They run in a scratch copy of the case's inputs (content, case.json, assets ...), so the full case's
geometry.json, final-*.{svg,pdf,png} and .drawio files are never touched; the variant's files come back under
their own names (<f> identifies each palette):

    <case_dir>/geometry-<v>.json
    <case_dir>/final-<v>-<f>.{svg,pdf,png}
    <case_dir>/<slug>-<v>.drawio (print palette) and <slug>-<v>-<f>.drawio (each other palette)
    <case_dir>/deliver/<slug>-<v>-<f>.pdf, every .drawio, <slug>-<v>.png (print palette),
                       <slug>-<v>-<f>.png (the others), <slug>-<v>.pdf (the print palette's PDF again)

(<slug>-<v> is the variant's slug; variants.py META may rename it.) No Artifact page is made for a
variant. Outputs are drafts until the optional cleaners configured in [[text_cleaners]] run, as for build.py.
"""
import argparse, os, pathlib, re, shutil, subprocess, sys, tempfile

sys.dont_write_bytecode = True
import _bootstrap  # noqa: E402,F401  (engine and shared core on sys.path)

ENGINE = pathlib.Path(__file__).resolve().parent

from build import fail                          # build.py's stop message
import themes


def outputs(case_dir, slug, variant):
    """Where each staged file goes: {staged name: path in the case folder}."""
    from drawio import out_name
    d = pathlib.Path(case_dir)
    out = {"geometry.json": d / f"geometry-{variant}.json"}
    for flavour in themes.final_palettes():
        out[out_name(slug, flavour)] = d / out_name(slug, flavour)
        for ext in ("svg", "pdf", "png"):
            out[f"final-{flavour}.{ext}"] = d / f"final-{variant}-{flavour}.{ext}"
    return out


def build_variant(case_dir, variant, autoplace=False, quiet=False, trust=False):
    from case import Case, CaseError
    import layout_engine, render, drawio, mkverify
    case_dir = pathlib.Path(case_dir).resolve()
    if variant in (None, "", "full"):
        fail("give a variant (the full case is build.py's)")
    try:
        from case import check_trust
        if not trust:
            check_trust(case_dir)               # a case folder is code: refuse an untrusted one before anything runs
    except CaseError as ex:
        fail(str(ex))
    lay = case_dir / f"layout_{variant}.py"
    if not lay.exists():
        if not autoplace:
            fail(f"no layout_{variant}.py in {case_dir}: draft one with\n"
                 f"  python3 {ENGINE / 'autoplace.py'} {case_dir} --variant {variant} --out layout_{variant}.py\n"
                 f"or run this with --autoplace")
        import autoplace
        res = autoplace.autoplace(case_dir, variant=variant, out=lay, quiet=quiet, trust=trust)
        if not res["ok"]:
            print(f"autoplace: the draft in {lay.name} still has problems; the build below will say which")
    try:
        case = Case(case_dir, variant=variant, trust=trust)
    except CaseError as ex:
        fail(str(ex))
    pre = case.preflight()
    if pre:
        fail("\n  - ".join([f"variant {variant!r} of {case_dir} is not ready:"] + pre))
    FLAVOURS = themes.final_palettes()
    if "SOURCE_DATE_EPOCH" not in os.environ:     # as build.py: unchanged sources give byte-identical outputs
        from build import source_epoch
        os.environ["SOURCE_DATE_EPOCH"] = str(source_epoch(case_dir, variant))
    for note in case.variant_notes:
        print(f"variant {variant}: {note}")
    if case.bridges:
        print(f"variant {variant}: bridges " + ", ".join(f"{a}->{b}" for a, b, _ in case.bridges))
    slug = case.slug
    from case import scratch_copy
    scratch = pathlib.Path(tempfile.mkdtemp(prefix=f"variant-{variant}-", dir=os.environ.get("TMPDIR")))
    stage = scratch_copy(case_dir, scratch / "case")    # the case's inputs (assets too: an inset's image)
    try:
        case.dir = stage                      # every step below reads and writes the scratch folder only
        try:
            res = layout_engine.run(case, quiet=quiet)
        except layout_engine.LayoutError as ex:
            fail(f"layout_{variant}.py: {ex}")
        if res["problems"] or res["crossings"] or res["unrouted"]:
            if quiet:
                for p in res["problems"]:
                    print("  PROBLEM", p)
                for c in res["crossings"]:
                    print("  CROSSING", c[0], c[1])
                if res["unrouted"]:
                    print("  UNROUTED", res["unrouted"])
            fail(f"layout_{variant}.py has problems, crossings or unrouted edges; not building outputs")
        for flavour in FLAVOURS:
            try:
                render.render(case, flavour, f"final-{flavour}")
            except ValueError as ex:
                fail(f"render {flavour}: {ex}")
            info = subprocess.run(["pdfinfo", str(stage / f"final-{flavour}.pdf")], capture_output=True, text=True, encoding="utf-8").stdout
            if re.search(r"^Pages:\s+1$", info, re.M) is None:
                fail(f"final-{variant}-{flavour}.pdf is not a single page")
        for flavour in FLAVOURS:              # one master per palette, as build.py writes them
            master = drawio.write(case, flavour)
            subprocess.run(["xmllint", "--noout", str(master)], check=True)
        if os.environ.get("TMPDIR"):
            mkverify.write(case, pathlib.Path(os.environ["TMPDIR"]) / f"verify-{variant}.js")
        from build import checks
        checks(case, res)  # Gate staged outputs before copying or creating deliver/.
        moved = {}
        for name, dest in outputs(case_dir, slug, variant).items():
            shutil.copy2(stage / name, dest)
            moved[name] = dest
    finally:
        case.dir = case_dir
        shutil.rmtree(scratch, ignore_errors=True)
    deliver = case_dir / "deliver"
    deliver.mkdir(exist_ok=True)
    from drawio import out_name
    for flavour in FLAVOURS:
        shutil.copy2(moved[out_name(slug, flavour)], deliver / out_name(slug, flavour))
        shutil.copy2(moved[f"final-{flavour}.pdf"], deliver / f"{slug}-{flavour}.pdf")
        shutil.copy2(moved[f"final-{flavour}.png"], deliver / themes.out_name(slug, flavour, "png"))
    shutil.copy2(moved[f"final-{themes.print_palette()}.pdf"], deliver / f"{slug}.pdf")
    print(f"variant {variant}: " + ", ".join(sorted(p.name for p in moved.values())))
    print("deliver/:", ", ".join(sorted(p.name for p in deliver.iterdir() if p.name.startswith(slug + "-") or p.name.startswith(slug + "."))))
    return case


def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Build one variant of a case (variants.py + layout_<variant>.py).")
    ap.add_argument("case_dir")
    ap.add_argument("--variant", required=True, help="a variant named in variants.py, e.g. core")
    ap.add_argument("--autoplace", action="store_true",
                    help="when layout_<variant>.py is missing, draft it with autoplace.py first (never overwrites one)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    ap.add_argument("--trust-case", action="store_true", help="run a case folder's Python from any location")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    build_variant(a.case_dir, a.variant, autoplace=a.autoplace, quiet=a.quiet, trust=a.trust_case)


if __name__ == "__main__":
    _bootstrap.entry()
    main(sys.argv[1:])
