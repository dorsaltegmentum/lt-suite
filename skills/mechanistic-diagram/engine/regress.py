#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression check: does a build reproduce a reference build? Read-only on both folders.

    python3 regress.py <ref_dir> <new_dir> [--slug S] [--ref-slug S] [--ref-html F --new-html F]
    python3 regress.py <build_dir> --write-manifest FILE      # record a build: hashes and reference thumbnails
    python3 regress.py <build_dir> --manifest FILE            # check a build against a recorded manifest

Compares, file by file, for every palette the new build rendered (deliver/ copies included):
  final-<palette>.svg                 byte-identical, or identical once inert <g> wrappers are set aside
                                      (a <g> with only id, class, role, aria-* and data-* attributes: the
                                      hooks the Artifact finds stages, arrows and boxes by; they draw nothing)
  geometry.json                       byte-identical, or equal as JSON
  PNGs                                pixel-identical (PIL + numpy)
  PDFs                                one page each; pdftoppm -r 40 renders pixel-identical; every byte
                                      difference explained object by object (allowed: CreationDate,
                                      the embedded fonts' head.modified / checkSumAdjustment, stream
                                      lengths and object-stream offsets that follow from them; with
                                      SOURCE_DATE_EPOCH set, cairo writes the date without its "D:")
  <slug>.drawio, <slug>-<palette>.drawio   identical apart from the mxfile agent string (reported)
  Artifact HTML (--ref-html/--new-html)  identical once the base64 payloads and inert <g> wrappers are set aside; the payloads
                                      are decoded and compared as above (PDFs; the zipped .drawio)
Prints one line per check and exits 1 if anything is not identical within those allowances.

A manifest (--write-manifest) records one build without keeping its files: the sha256 of geometry.json,
of every final-<palette>.svg (inert <g> wrappers set aside) and of every .drawio (agent string and embedded
font data set aside, so a newer engine version or font subsetter does not count as a change), plus a
thumbnail of every final-<palette>.png (THUMB_W px wide) beside the manifest. --manifest checks a build
against it: hashes equal, and each PNG's thumbnail within PNG_TOLERANCE of the reference (the share of
pixels whose channels differ by more than PNG_DELTA), because librsvg versions anti-alias differently.
The test suite keeps the worked example's manifest in tests/diagram/reference/.
"""
import argparse, base64, io, json, os, pathlib, re, subprocess, sys, tempfile, zipfile, zlib

import _bootstrap  # noqa: E402,F401  (the Python version gate; engine on sys.path)
from _legacy import read_legacy_bytes, read_legacy_text

FAILS = []
THUMB_W = 480           # reference thumbnail width, px
PNG_TOLERANCE = 0.005   # share of thumbnail pixels allowed to differ
PNG_DELTA = 24          # a pixel differs when any channel moves by more than this (0-255)


def report(ok, what, detail=""):
    print(f"{'OK  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(what)


# ------------------------------------------------------------------ images
def pixels(path_or_bytes):
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes)
    return np.asarray(im.convert("RGBA"))


def same_pixels(a, b):
    import numpy as np
    pa, pb = pixels(a), pixels(b)
    if pa.shape != pb.shape:
        return False, f"size {pa.shape[1]}x{pa.shape[0]} vs {pb.shape[1]}x{pb.shape[0]}"
    n = int(np.any(pa != pb, axis=2).sum())
    return n == 0, f"{pa.shape[1]}x{pa.shape[0]}, {n} pixels differ"


# ------------------------------------------------------------------ SVG
INERT_G = re.compile(r'^\s*<g(?:\s+(?:id|class|role|aria-[\w-]+|data-[\w-]+)="[^"]*")*\s*>\s*$|^\s*</g>\s*$')


def without_inert_groups(text):
    """The text with every line that only opens an inert <g> or closes a </g> removed, and that count.
    Only whole lines go: render.py writes each <g> wrapper on its own line."""
    keep, n = [], 0
    for line in text.split("\n"):
        if INERT_G.match(line):
            n += 1
        else:
            keep.append(line)
    return "\n".join(keep), n


def compare_svg(a, b, what):
    if a == b:
        report(True, what, "byte-identical")
        return
    (ta, na), (tb, nb) = without_inert_groups(a.decode("utf-8")), without_inert_groups(b.decode("utf-8"))
    report(ta == tb, what, f"identical apart from inert <g> wrappers ({na} vs {nb} lines)" if ta == tb
           else f"{len(a)} vs {len(b)} bytes, differs beyond inert <g> wrappers")


# ------------------------------------------------------------------ PDFs
def pdf_render(data, tmp, name):
    p = pathlib.Path(tmp) / f"{name}.pdf"
    p.write_bytes(data)
    pages = re.search(r"^Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, encoding="utf-8").stdout, re.M)
    subprocess.run(["pdftoppm", "-r", "40", "-png", str(p), str(pathlib.Path(tmp) / name)], check=True)
    pngs = sorted(pathlib.Path(tmp).glob(f"{name}-*.png"))
    return int(pages.group(1)) if pages else 0, [x.read_bytes() for x in pngs]


def pdf_objects(data):
    """{object number: (dictionary bytes, stream bytes decompressed or None)}"""
    objs = {}
    for m in re.finditer(rb"(?m)^(\d+) 0 obj\b(.*?)\bendobj\b", data, re.S):
        body = m.group(2)
        sm = re.search(rb"\bstream\r?\n(.*)\r?\nendstream", body, re.S)
        if sm:
            head, raw = body[:sm.start()], sm.group(1)
            if b"/FlateDecode" in head:
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    pass
            objs[int(m.group(1))] = (head, raw)
        else:
            objs[int(m.group(1))] = (body, None)
    return objs


def font_same_but_timestamp(a, b):
    """Two font programs equal in every table, apart from head.modified and head.checkSumAdjustment?"""
    try:
        from fontTools.ttLib import TTFont
        fa, fb = TTFont(io.BytesIO(a)), TTFont(io.BytesIO(b))
    except Exception:
        return False
    if sorted(fa.reader.keys()) != sorted(fb.reader.keys()):
        return False
    for tag in fa.reader.keys():
        if tag == "head":
            ha, hb = bytearray(fa.reader[tag]), bytearray(fb.reader[tag])
            for lo, hi in ((8, 12), (28, 36)):      # checkSumAdjustment; modified (LONGDATETIME)
                ha[lo:hi] = hb[lo:hi] = b"\0" * (hi - lo)
            if ha != hb:
                return False
        elif fa.reader[tag] != fb.reader[tag]:
            return False
    return True


def objstm_same_but_dates(head, sa, sb, date):
    """Two object streams equal once their dates are set aside? The offset table before /First may
    differ (a date of another length moves the objects after it), but not its object numbers."""
    m = re.search(rb"/First (\d+)", head)
    if not m:
        return re.sub(date, b"", sa) == re.sub(date, b"", sb)
    first = int(m.group(1))
    na, nb = sa[:first].split(), sb[:first].split()
    if na[0::2] != nb[0::2]:
        return False
    # re-split both bodies at their own offsets and compare object by object
    def objs(st, nums):
        offs = [int(x) for x in nums[1::2]] + [len(st) - first]
        return [st[first + offs[i]:first + offs[i + 1]] for i in range(len(offs) - 1)]
    return [re.sub(date, b"", x).strip() for x in objs(sa, na)] == [re.sub(date, b"", x).strip() for x in objs(sb, nb)]


def explain_pdf_bytes(a, b):
    """Why do two PDFs differ byte-wise? Returns (explained, [reasons])."""
    oa, ob = pdf_objects(a), pdf_objects(b)
    if set(oa) != set(ob):
        return False, ["different object numbers"]
    length_refs = {int(x) for x in re.findall(rb"/Length (\d+) 0 R", a)}
    reasons, ok = set(), True
    for k in sorted(oa):
        if oa[k] == ob[k]:
            continue
        (ha, sa), (hb, sb) = oa[k], ob[k]
        date = rb"\((?:D:)?\d{14}[^)]*\)"
        nolen = lambda h: re.sub(rb"/Length \d+\b", b"", h)
        if sa is None and re.sub(date, b"", ha) == re.sub(date, b"", hb) and re.search(date, ha):
            reasons.add("CreationDate")
        elif sa is not None and b"/Type /ObjStm" in ha and nolen(ha) == nolen(hb) and re.search(date, sa) \
                and objstm_same_but_dates(ha, sa, sb, date):
            reasons.add("CreationDate")             # the Info dictionary, inside an object stream
        elif sa is not None and b"/Type /XRef" in ha and nolen(ha) == nolen(hb):
            reasons.add("cross-reference offsets")  # byte offsets move with the stream lengths
        elif sa is None and k in length_refs and re.fullmatch(rb"\s*\d+\s*", ha) and re.fullmatch(rb"\s*\d+\s*", hb):
            reasons.add("stream lengths")
        elif sa is not None and sa != sb and font_same_but_timestamp(sa, sb):
            reasons.add("embedded font head.modified/checkSumAdjustment")
            if re.sub(rb"/Length \d+\b", b"", ha) != re.sub(rb"/Length \d+\b", b"", hb):
                ok = False
                reasons.add(f"object {k} dictionary")
        elif sa is not None and sa == sb and re.sub(rb"/Length \d+\b", b"", ha) == re.sub(rb"/Length \d+\b", b"", hb):
            reasons.add("stream lengths")
        else:
            ok = False
            reasons.add(f"object {k}")
    return ok, sorted(reasons)


def compare_pdf(a, b, what, tmp):
    if a == b:
        report(True, what, "byte-identical")
        return
    pa, ra = pdf_render(a, tmp, "ref")
    pb, rb_ = pdf_render(b, tmp, "new")
    for f in pathlib.Path(tmp).glob("*"):
        f.unlink()
    if pa != 1 or pb != 1:
        report(False, what, f"pages {pa} vs {pb}")
        return
    same, detail = same_pixels(ra[0], rb_[0])
    ok_bytes, reasons = explain_pdf_bytes(a, b)
    report(same and ok_bytes, what, f"render-identical at 40 dpi ({detail}); bytes differ only in {', '.join(reasons)}"
           if same and ok_bytes else f"render: {detail}; byte differences: {', '.join(reasons)}")


# ------------------------------------------------------------------ draw.io
AGENT = re.compile(r'(<mxfile [^>]*?\bagent=")([^"]*)(")')


def compare_drawio(a, b, what):
    if a == b:
        report(True, what, "byte-identical")
        return
    ta, tb = a.decode("utf-8"), b.decode("utf-8")
    ma, mb = AGENT.search(ta), AGENT.search(tb)
    na, nb = AGENT.sub(r"\1\3", ta, 1), AGENT.sub(r"\1\3", tb, 1)
    report(na == nb, what, f'identical apart from agent "{ma and ma.group(2)}" -> "{mb and mb.group(2)}"' if na == nb
           else "differs beyond the agent string")


# ------------------------------------------------------------------ Artifact page
PAYLOAD = re.compile(r"(data:[a-z/+.-]+;base64,)([A-Za-z0-9+/=]+)")


def compare_html(ref, new, tmp):
    """The pages with their base64 payloads (download links, embedded fonts) set aside, then each payload
    decoded and compared: PDFs as compare_pdf, the zipped .drawio files as compare_drawio."""
    ta, tb = read_legacy_text(ref), read_legacy_text(new)
    pa, pb = [m[1] for m in PAYLOAD.findall(ta)], [m[1] for m in PAYLOAD.findall(tb)]
    sa, sb = PAYLOAD.sub(r"\1<payload>", ta), PAYLOAD.sub(r"\1<payload>", tb)
    (sa, ga), (sb, gb) = without_inert_groups(sa), without_inert_groups(sb)
    if sa == sb:
        report(True, "artifact HTML", f"identical apart from {len(pa)} base64 payloads"
               + (" (themselves identical)" if pa == pb else "")
               + (f" and inert <g> wrappers ({ga} vs {gb} lines)" if ga != gb else ""))
    else:
        import difflib
        diff = [l for l in difflib.unified_diff(sa.splitlines(), sb.splitlines(), "ref", "new", n=0, lineterm="")]
        report(False, "artifact HTML", f"{len(diff)} diff lines")
        print("\n".join(l[:300] for l in diff[:40]))
    if len(pa) != len(pb):
        report(False, "artifact payloads", f"{len(pa)} vs {len(pb)}")
        return
    for i, (x, y) in enumerate(zip(pa, pb)):
        da, db = base64.b64decode(x), base64.b64decode(y)
        if da[:5] == b"%PDF-":
            compare_pdf(da, db, f"artifact payload {i + 1} (PDF)", tmp)
        elif da[:2] == b"PK":
            za, zb = zipfile.ZipFile(io.BytesIO(da)), zipfile.ZipFile(io.BytesIO(db))
            if za.namelist() != zb.namelist():
                report(False, f"artifact payload {i + 1} (zip)", f"members {za.namelist()} vs {zb.namelist()}")
                continue
            for name in za.namelist():
                ia, ib = za.getinfo(name), zb.getinfo(name)
                note = "" if ia.date_time == ib.date_time else f"; zip entry dates {ia.date_time} vs {ib.date_time}"
                ca, cb = za.read(name), zb.read(name)
                if name.endswith(".drawio"):
                    compare_drawio(ca, cb, f"artifact payload {i + 1} (zip) {name}" + note)
                else:
                    report(ca == cb, f"artifact payload {i + 1} (zip) {name}" + note)
        else:
            report(da == db, f"artifact payload {i + 1}")


# ------------------------------------------------------------------ manifests
FONT_DATA = re.compile(r"data:(?:font/[\w.+-]+|application/(?:x-)?font-[\w.+-]+|application/octet-stream);base64,[A-Za-z0-9+/=%]+")


def drawio_digest(data):
    """sha256 of a .drawio with the mxfile agent and embedded font data set aside."""
    text = AGENT.sub(r"\1\3", data.decode("utf-8"), 1)
    return sha(FONT_DATA.sub("data:font;base64,<font>", text).encode("utf-8"))


def svg_digest(data):
    return sha(without_inert_groups(data.decode("utf-8"))[0].encode("utf-8"))


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def thumbnail(png):
    from PIL import Image
    im = Image.open(png).convert("RGBA")
    h = max(1, round(im.height * THUMB_W / im.width))
    return im.resize((THUMB_W, h), Image.Resampling.BOX)


def build_files(d, slug):
    """The files a manifest records: (name, kind) for geometry.json, every final-<palette>.svg and .png, and
    every <slug>*.drawio of the build."""
    pals = sorted(q.stem[len("final-"):] for q in d.glob("final-*.svg") if ".static" not in q.name)
    out = [("geometry.json", "json")]
    out += [(f"final-{fl}.svg", "svg") for fl in pals]
    out += [(q.name, "drawio") for q in sorted(d.glob(f"{slug}*.drawio"))]
    out += [(f"final-{fl}.png", "png") for fl in pals if (d / f"final-{fl}.png").exists()]
    return pals, out


def write_manifest(d, out, slug):
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pals, files = build_files(d, slug)
    man = {"slug": slug, "palettes": pals, "thumb_w": THUMB_W, "png_tolerance": PNG_TOLERANCE,
           "png_delta": PNG_DELTA, "files": {}}
    stem = out.name[:-len(".manifest.json")] if out.name.endswith(".manifest.json") else out.stem
    for name, kind in files:
        data = (d / name).read_bytes() if kind == "png" else read_legacy_bytes(d / name)
        if kind == "png":
            ref = f"{stem}-{name}"
            thumbnail(d / name).save(out.parent / ref, optimize=True)
            man["files"][name] = {"thumbnail": ref}
        elif kind == "svg":
            man["files"][name] = {"sha256": svg_digest(data)}
        elif kind == "drawio":
            man["files"][name] = {"sha256": drawio_digest(data)}
        else:
            man["files"][name] = {"sha256": sha(data)}
    out.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {len(man['files'])} files, palettes {', '.join(pals)}")
    return 0


def check_manifest(d, manifest):
    import numpy as np
    manifest = pathlib.Path(manifest)
    man = json.loads(read_legacy_text(manifest))
    slug = man["slug"]
    pals, files = build_files(d, slug)
    report(pals == man["palettes"], "palettes", f"{', '.join(pals)} vs manifest {', '.join(man['palettes'])}")
    have = {n for n, _ in files}
    for name in sorted(set(man["files"]) - have):
        report(False, name, "missing from the build")
    for name in sorted(have - set(man["files"])):
        report(False, name, "not in the manifest")
    for name, kind in files:
        rec = man["files"].get(name)
        if rec is None:
            continue
        data = (d / name).read_bytes() if kind == "png" else read_legacy_bytes(d / name)
        if kind == "png":
            ref = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(manifest.parent / rec["thumbnail"]).convert("RGBA"))
            new = np.asarray(thumbnail(d / name))
            if ref.shape != new.shape:
                report(False, name, f"thumbnail {new.shape[1]}x{new.shape[0]} vs {ref.shape[1]}x{ref.shape[0]}")
                continue
            share = float((np.abs(ref.astype(int) - new.astype(int)).max(axis=2) > man.get("png_delta", PNG_DELTA)).mean())
            tol = man.get("png_tolerance", PNG_TOLERANCE)
            report(share <= tol, name, f"{share:.3%} of thumbnail pixels differ (tolerance {tol:.1%})")
            continue
        got = svg_digest(data) if kind == "svg" else drawio_digest(data) if kind == "drawio" else sha(data)
        report(got == rec["sha256"], name, "sha256 matches" if got == rec["sha256"] else f"sha256 {got[:12]} vs {rec['sha256'][:12]}")
    print("REGRESSION", "PASSED" if not FAILS else f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
    return 1 if FAILS else 0


# ------------------------------------------------------------------ main
def main(argv):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description="Compare a build with a reference build or a manifest (read-only "
                                             "on the build folders).")
    ap.add_argument("dirs", nargs="+", metavar="DIR", help="<ref_dir> <new_dir>, or one build folder with "
                                                           "--manifest / --write-manifest")
    ap.add_argument("--slug", help="deliverable slug of the new build (default: from its case.json)")
    ap.add_argument("--ref-slug", help="slug of the reference build (default: the same)")
    ap.add_argument("--ref-html")
    ap.add_argument("--new-html")
    ap.add_argument("--manifest", help="check the build folder against this manifest")
    ap.add_argument("--write-manifest", help="write a manifest of the build folder (and its PNG thumbnails beside it)")
    a = ap.parse_args(argv)
    if a.manifest or a.write_manifest:
        if len(a.dirs) != 1 or (a.manifest and a.write_manifest):
            ap.error("--manifest and --write-manifest take one build folder, and only one of them")
        d = pathlib.Path(a.dirs[0])
        if a.write_manifest:
            return write_manifest(d, a.write_manifest, a.slug or json.loads(read_legacy_text(d / "case.json"))["slug"])
        return check_manifest(d, a.manifest)
    if len(a.dirs) != 2:
        ap.error("give <ref_dir> <new_dir>")
    ref, new = pathlib.Path(a.dirs[0]), pathlib.Path(a.dirs[1])
    slug = a.slug or json.loads(read_legacy_text(new / "case.json"))["slug"]
    rslug = a.ref_slug or slug
    tmp = tempfile.mkdtemp(prefix="regress-", dir=os.environ.get("TMPDIR"))
    pals = sorted(q.stem[len("final-"):] for q in new.glob("final-*.svg") if ".static" not in q.name
                  and (ref / q.name).exists())
    if not pals:
        report(False, "palettes", "no final-<palette>.svg in both folders")
    drawios = sorted(q.name for q in new.glob(f"{slug}*.drawio"))
    for f in (f"final-{fl}.svg" for fl in pals):
        compare_svg(read_legacy_bytes(ref / f), read_legacy_bytes(new / f), f)
    x, y = read_legacy_bytes(ref / "geometry.json"), read_legacy_bytes(new / "geometry.json")
    if x == y:
        report(True, "geometry.json", "byte-identical")
    else:
        report(json.loads(x) == json.loads(y), "geometry.json", "equal as JSON" if json.loads(x) == json.loads(y) else "differs")
    pngs = [(f"final-{fl}.png",) * 2 for fl in pals]
    pngs += [(f"deliver/{rslug}{q.name[len(slug):]}", f"deliver/{q.name}") for q in sorted((new / "deliver").glob(f"{slug}*.png"))]
    for fr, fn in pngs:
        x, y = (ref / fr).read_bytes(), (new / fn).read_bytes()
        if x == y:
            report(True, fn, "byte-identical")
        else:
            same, detail = same_pixels(x, y)
            report(same, fn, detail)
    pdfs = [(f"final-{fl}.pdf",) * 2 for fl in pals]
    pdfs += [(f"deliver/{rslug}{q.name[len(slug):]}", f"deliver/{q.name}") for q in sorted((new / "deliver").glob(f"{slug}*.pdf"))]
    for fr, fn in pdfs:
        compare_pdf((ref / fr).read_bytes(), (new / fn).read_bytes(), fn, tmp)
    pairs = [(f"{rslug}{n[len(slug):]}", n) for n in drawios] + [(f"deliver/{rslug}{n[len(slug):]}", f"deliver/{n}") for n in drawios]
    for fr, fn in pairs:
        compare_drawio(read_legacy_bytes(ref / fr), read_legacy_bytes(new / fn), fn)
    if a.ref_html and a.new_html:
        compare_html(pathlib.Path(a.ref_html), pathlib.Path(a.new_html), tmp)
    for f in pathlib.Path(tmp).glob("*"):
        f.unlink()
    pathlib.Path(tmp).rmdir()
    print("REGRESSION", "PASSED" if not FAILS else f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
