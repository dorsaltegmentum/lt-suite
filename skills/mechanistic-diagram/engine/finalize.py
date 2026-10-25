#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clean final outputs: metadata off, deterministic PDFs, then any configured cleaners.

    python3 finalize.py FILE_OR_DIR [...] [--report report.json] [--dry-run] [--config FILE]
    (or: python3 build.py <case_dir> --final, which runs this over deliver/ and the Artifact page)

Built in, no external cleaner needed (the shared core, ltcore.cleaners, does the PDF work):
  PDF   ltcore.cleaners.strip_pdf_metadata: /Info, XMP and PieceInfo off every object, Exif, XMP, IPTC
        and comment segments cut from each JPEG whose decoded pixels stay the same, exiftool -all= when
        exiftool is installed, then a rewrite with pikepdf (else qpdf) without /Info, XMP or the old /ID
        and with a /ID computed from the content, so a cleaned PDF is a function of its draft and a
        rebuild gives the same bytes. A PDF whose page text, image pixels or link targets would change
        is kept as it was and reported (the comparison needs pikepdf and PyMuPDF).
  PNG   text, time and Exif chunks (tEXt, iTXt, zTXt, tIME, eXIf) are dropped; the pixel data is
        copied unchanged.
Then every [[text_cleaners]] entry of the config with stage = "diagram" runs on each file, in config
order: argv list only, no shell, {in} the file and {out} where the cleaner writes. A cleaner's output
is kept only when it changes nothing the file shows: a text file (.html, .drawio, .svg, .md) may lose
invisible format characters only (no-break spaces stay), a PDF must keep its content, a PNG its pixels.
Afterwards exiftool (when present) lists any metadata left in each PDF and PNG; anything beyond the
file's structure (ltcore.cleaners.STRUCTURAL, plus ICC colour profiles) is reported as residual.
"""
import argparse
import io
import json
import sys
import tempfile
import unicodedata
from pathlib import Path

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)
from ltcore import cleaners as _cl

TEXTUAL = {".html", ".htm", ".drawio", ".svg", ".md", ".txt", ".json", ".py", ".tsv", ".csv"}
KEEP = {"\u00a0", "\u202f"}
PNG_DROP = {b"tEXt", b"iTXt", b"zTXt", b"tIME", b"eXIf"}
STRUCTURAL = _cl.STRUCTURAL
qpdf_settle = _cl.qpdf_settle
pdf_content = _cl.pdf_content
residual_metadata = _cl.residual_metadata


def only_invisibles_removed(before: bytes, after: bytes) -> bool:
    """True if `after` is `before` with some format/invisible characters deleted and nothing else."""
    try:
        a, b = before.decode("utf-8"), after.decode("utf-8")
    except UnicodeDecodeError:
        return False
    i = 0
    for ch in a:
        if i < len(b) and b[i] == ch:
            i += 1
        elif unicodedata.category(ch) in ("Cf", "Mn", "Zs", "Co") and ch not in KEEP:
            continue
        else:
            return False
    return i == len(b)


def png_without_text(data: bytes):
    """A PNG with its text, time and Exif chunks dropped (pixel data copied as it is); None if nothing to drop."""
    import struct
    sig = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig):
        return None
    out, pos, dropped = bytearray(sig), 8, False
    while pos + 8 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + n
        if kind in PNG_DROP:
            dropped = True
        else:
            out += data[pos:end]
        pos = end
        if kind == b"IEND":
            break
    return bytes(out) if dropped else None


def same_pixels(a: bytes, b: bytes) -> bool:
    import numpy as np
    from PIL import Image
    pa = np.asarray(Image.open(io.BytesIO(a)).convert("RGBA"))
    pb = np.asarray(Image.open(io.BytesIO(b)).convert("RGBA"))
    return pa.shape == pb.shape and not np.any(pa != pb)


def builtin(path: Path, raw: bytes):
    """(cleaned bytes, notes) from the built-in passes; raises RuntimeError when a PDF cannot be cleaned."""
    ext = path.suffix.lower()
    notes, out = [], raw
    if ext == ".pdf":
        with tempfile.TemporaryDirectory(prefix="finalize-") as td:
            tmp = Path(td) / path.name
            tmp.write_bytes(raw)
            r = _cl.strip_pdf_metadata(tmp)
            if r.status == "skipped":
                raise RuntimeError(f"PDF metadata not stripped: {r.detail}")
            if r.status not in ("cleaned", "unchanged"):
                raise RuntimeError(f"PDF metadata strip {r.status}: {r.detail}")
            out = tmp.read_bytes()
        notes.append("metadata stripped and rewritten with a deterministic /ID (ltcore.cleaners)"
                     + (f": {r.detail}" if r.detail else ""))
    elif ext == ".png":
        new = png_without_text(raw)
        if new is not None:
            out = new
            notes.append("text, time and Exif chunks dropped")
    return out, notes


def run_hooks(path: Path, data: bytes, dry):
    """Every [[text_cleaners]] entry with stage "diagram", in order. Returns (bytes, notes, problems)."""
    import style
    notes, problems = [], []
    hooks = style.cleaners("diagram")
    if not hooks:
        return data, notes, problems
    ext = path.suffix.lower()
    for entry in hooks:
        name = entry.get("name", "?")
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / ("in" + path.suffix), Path(td) / ("out" + path.suffix)
            src.write_bytes(data)
            try:
                style.run_cleaner(entry, {"in": src, "out": dst, "pdf": src})
            except Exception as exc:  # noqa: BLE001  (a failing hook is reported; the file keeps its last good bytes)
                problems.append(f"cleaner {name}: {exc}")
                continue
            got = dst.read_bytes() if dst.exists() else (src.read_bytes() if ext == ".pdf" else None)
        if got is None:
            problems.append(f"cleaner {name} wrote no output")
            continue
        if got == data:
            continue
        if ext in TEXTUAL:
            ok = only_invisibles_removed(data, got)
        elif ext == ".png":
            ok = same_pixels(data, got)
        elif ext == ".pdf":
            before = pdf_content(data)
            ok = before is None or pdf_content(got) == before
            if ok:
                got = qpdf_settle(got)
        else:
            ok = False
        if not ok:
            problems.append(f"cleaner {name} changed what {path.name} shows; its output was not kept")
            continue
        data = got
        notes.append(f"cleaner {name}")
    return data, notes, problems


def clean_one(path: Path, dry=False):
    raw = path.read_bytes()
    ext = path.suffix.lower()
    try:
        out, extra = builtin(path, raw)
    except (RuntimeError, OSError) as ex:
        if "content changed" in str(ex):
            return {"file": str(path), "status": "kept-original",
                    "detail": "cleaning changed a page's text, an image's pixels or a link target; original kept"}
        return {"file": str(path), "status": "error", "detail": str(ex)}
    out, hook_notes, problems = run_hooks(path, out, dry)
    extra += hook_notes
    changed = out != raw
    if changed and not dry:
        path.write_bytes(out)
    rec = {"file": str(path), "status": "cleaned" if changed else "already-clean",
           "bytes": [len(raw), len(out)], "actions": [], "extra": extra}
    if problems:
        rec["status"] = "error"
        rec["detail"] = "; ".join(problems)
    if ext in (".pdf", ".png", ".jpg", ".jpeg", ".webp") and not dry:
        left = residual_metadata(path)
        if left:
            rec["residual"] = left
    return rec


def clean(paths, dry=False):
    """Clean every file under the given files and folders; returns one result dict per file."""
    files = []
    for p in map(Path, paths):
        files += sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p]
    return [clean_one(f, dry) for f in files]


def describe(r):
    """One line per file: what changed."""
    line = f"{r['status']:14s} {r['file']}"
    if r["status"] in ("error", "kept-original") and not r.get("bytes"):
        return line + f"  ({r['detail']})"
    if r.get("bytes"):
        a, b = r["bytes"]
        line += f"  {a} -> {b} bytes"
    if r.get("extra"):
        line += "; " + "; ".join(r["extra"])
    if r.get("detail"):
        line += f"; {r['detail']}"
    if r.get("residual"):
        line += f"; RESIDUAL metadata: {', '.join(r['residual'])}"
    return line


def main(argv=None):
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", help="lt-suite config file (default: the usual lookup)")
    a = ap.parse_args(argv)
    import style
    try:
        style.configure(a.config)
    except style.StyleError as ex:
        raise SystemExit(str(ex))
    results = clean(a.paths, a.dry_run)
    for r in results:
        print(describe(r))
    if a.report:
        a.report.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8", newline="\n")
    return 1 if any(r["status"] in ("error", "kept-original") or r.get("residual") for r in results) else 0


if __name__ == "__main__":
    _bootstrap.entry()
    sys.exit(main())
