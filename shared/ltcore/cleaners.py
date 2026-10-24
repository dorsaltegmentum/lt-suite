# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Output cleaners: the ``[[text_cleaners]]`` hooks and the built-in PDF metadata strip.

A cleaner is any external program the user configures (config ``text_cleaners``):
``name``, ``stage`` (fill-text, pdf or diagram), ``argv`` with the placeholders
{in} and {out} (fill-text, diagram) or {pdf} (pdf), and ``timeout_s`` (60). Each
runs on a temporary copy; the original is replaced atomically only when the
program exits 0 with a non-empty result (and, for PDFs, a file that still starts
with %PDF- and keeps its page count). A failure or timeout leaves it untouched.

The built-in strip (``pdf.strip_metadata``, on by default) removes the document
info dictionary, XMP and PieceInfo from every object and the Exif, XMP, IPTC and
comment segments of each JPEG whose decoded pixels stay equal (pikepdf and
Pillow), runs ``exiftool -all=`` when present, then rewrites the file with a
content-derived /ID (pikepdf, else the qpdf command), so the same draft always
gives the same bytes. It then reads every object again (pikepdf, else ``qpdf
--json``) and reports "cleaned" only when no Info dictionary, XMP stream (at
document or page level) or PieceInfo is left. When neither tool can do that
check, or page text, image pixels, link targets or the page count differ
afterwards, the result is "failed" and the original is kept.

API: ``cleaners_for(stage, cfg)`` (entries in config order); ``run_cleaner(entry,
path)`` (temporary copies, atomic replace, never raises: a Result with status
cleaned, unchanged, failed or timeout); ``run_one(entry, {"in", "out", "pdf"},
timeout_s)`` (runs on the caller's files, raises CleanerError); ``run_stage``;
``finalize(paths, stage=)`` (cleaners, then the strip for stage pdf);
``strip_pdf_metadata(path)`` -> Result; ``strip_pdf(path)`` -> dict with status
cleaned, already-clean or error.

    python3 -m ltcore.cleaners run --stage pdf [--config F] [--no-strip] FILE...
    python3 -m ltcore.cleaners strip FILE.pdf...
    python3 -m ltcore.cleaners list [--config F]

Exit codes: 0 when no result failed or timed out, 1 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from ltcore import config

NEEDS: list[str] = []
STAGES = ("fill-text", "pdf", "diagram")
STRUCTURAL = {
    # Tool version identifies the metadata reader.
    "ExifTool:ExifToolVersion",
    # PDF tags describe structure, accessibility, forms and initial viewing settings.
    "PDF:PDFVersion", "PDF:Linearized", "PDF:PageCount", "PDF:PageMode",
    "PDF:Language", "PDF:TaggedPDF", "PDF:PageLayout", "PDF:HasXFA",
    # PNG tags describe image encoding, colour rendering and physical dimensions.
    "PNG:ImageWidth", "PNG:ImageHeight", "PNG:BitDepth", "PNG:ColorType",
    "PNG:Compression", "PNG:Filter", "PNG:Interlace", "PNG:SRGBRendering",
    "PNG:Gamma", "PNG:BackgroundColor", "PNG:PixelsPerUnitX",
    "PNG:PixelsPerUnitY", "PNG:PixelUnits",
    # Composite tags describe dimensions derived from structural values.
    "Composite:ImageSize", "Composite:Megapixels",
}


class CleanerError(Exception):
    """A cleaner definition, execution or PDF validation failed."""


@dataclass
class Result:
    """One cleaner run on one file. Fields read as attributes or as keys
    (``r.status`` or ``r["status"]``); ``as_dict()`` gives a plain dict."""
    name: str
    stage: str
    path: str
    status: str  # "cleaned" | "unchanged" | "failed" | "timeout" | "skipped" | "ran"
    detail: str = ""
    argv: list[str] = field(default_factory=list)
    returncode: int | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "stage": self.stage, "path": self.path, "file": self.path,
                "status": self.status, "detail": self.detail, "argv": list(self.argv),
                "returncode": self.returncode}

    def __getitem__(self, key: str):
        return self.as_dict()[key]

    def get(self, key: str, default=None):
        return self.as_dict().get(key, default)

    def keys(self):
        return self.as_dict().keys()

    @property
    def ok(self) -> bool:
        return self.status in ("cleaned", "unchanged", "ran")


def _optional(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def cleaners_for(stage: str, cfg=None, entries: list[dict] | None = None) -> list[dict]:
    """Return independent entry dictionaries in configuration order."""
    if stage not in STAGES:
        raise CleanerError(f"unknown cleaner stage: {stage}")
    if entries is None:
        cfg = config.load() if cfg is None else cfg
        entries = cfg.get("text_cleaners", [])
    selected = []
    for entry in entries:
        if entry.get("stage") == stage:
            item = dict(entry)
            item["argv"] = list(entry["argv"])
            item.setdefault("timeout_s", 60)
            selected.append(item)
    return selected


def build_argv(entry: dict, *, src: str | None = None, dst: str | None = None,
               pdf: str | None = None) -> list[str]:
    """Expand only the three supported literal placeholders."""
    stage = entry.get("stage")
    if stage not in STAGES:
        raise CleanerError(f"unknown cleaner stage: {stage}")
    values = {"in": src, "out": dst, "pdf": pdf}
    required = ("pdf",) if stage == "pdf" else ("in", "out")
    for name in required:
        if values[name] is None:
            raise CleanerError(f"{stage} cleaner needs a value for {{{name}}}")
    argv = entry.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) for a in argv):
        raise CleanerError("cleaner argv must be a non-empty list of strings")

    def expand(match):
        name = match.group(1)
        if values[name] is None:
            raise CleanerError(f"missing value for {{{name}}}")
        return str(values[name])

    return [re.sub(r"\{(in|out|pdf)\}", expand, arg) for arg in argv]


def _replace(path: Path, data: bytes):
    """Replace through a closed temporary file on the destination filesystem."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".lt-clean-", delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _page_count(data: bytes) -> int | None:
    """Pages in a PDF, read with pikepdf, else ``qpdf --show-npages``. None when
    neither is installed. Raises CleanerError when the tool cannot read the file."""
    pikepdf = _optional("pikepdf")
    if pikepdf is not None:
        try:
            with pikepdf.open(io.BytesIO(data)) as pdf:
                return len(pdf.pages)
        except Exception as exc:
            raise CleanerError(f"could not validate PDF page counts: {exc}") from exc
    executable = shutil.which("qpdf")
    if executable is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="lt-pages-") as directory:
            path = Path(directory) / "document.pdf"
            path.write_bytes(data)
            completed = subprocess.run([executable, "--show-npages", str(path)],
                                       capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanerError(f"could not validate PDF page counts: {exc}") from exc
    count = completed.stdout.strip()
    # qpdf exits 3 when it repaired the file with warnings; the count still holds.
    if completed.returncode not in (0, 3) or not count.isdigit():
        raise CleanerError(f"could not validate PDF page counts: qpdf exit "
                           f"{completed.returncode}: {completed.stderr.strip()}")
    return int(count)


def _check_pdf(before: bytes, after: bytes) -> str:
    if not after.startswith(b"%PDF-"):
        raise CleanerError("cleaner output does not start with %PDF-")
    pages = _page_count(before)
    if pages is None:
        return "page count not checked (needs pikepdf or qpdf)"
    if _page_count(after) != pages:
        raise CleanerError("PDF page count changed")
    return ""


def run_cleaner(entry: dict, path: str | os.PathLike, *, timeout_s: int | None = None,
                runner=subprocess.run) -> Result:
    """Run against temporary copies and install successful output atomically."""
    path = Path(path)
    stage = entry.get("stage", "")
    result = Result(entry.get("name", "unnamed"), stage, str(path), "failed")
    try:
        timeout = entry.get("timeout_s", 60) if timeout_s is None else timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise CleanerError("timeout_s must be a positive integer")
        original = path.read_bytes()
        with tempfile.TemporaryDirectory(prefix="lt-clean-") as directory:
            src = Path(directory) / f"in{path.suffix}"
            dst = Path(directory) / f"out{path.suffix}"
            src.write_bytes(original)
            result.argv = build_argv(entry, src=str(src), dst=str(dst), pdf=str(src))
            try:
                completed = runner(result.argv, capture_output=True, text=True, timeout=timeout)
            except FileNotFoundError:
                result.detail = f"executable not found: {result.argv[0]}"
                return result
            result.returncode = completed.returncode
            if completed.returncode != 0:
                message = (getattr(completed, "stderr", "") or
                           getattr(completed, "stdout", "") or "").strip()
                result.detail = f"exit {completed.returncode}" + (f": {message}" if message else "")
                return result
            output = src if stage == "pdf" else dst
            if not output.is_file() or output.stat().st_size == 0:
                result.detail = "cleaner produced no non-empty output file"
                return result
            cleaned = output.read_bytes()
            if stage == "pdf":
                result.detail = _check_pdf(original, cleaned)
            if cleaned == original:
                result.status = "unchanged"
            else:
                _replace(path, cleaned)
                result.status = "cleaned"
    except subprocess.TimeoutExpired:
        result.status, result.detail = "timeout", f"exceeded {timeout} seconds"
    except (OSError, CleanerError) as exc:
        result.detail = str(exc)
    return result


def run_one(entry: dict, placeholders: dict, timeout_s: int | None = None, *,
            runner=subprocess.run) -> Result:
    """Run one cleaner on the caller's own files: ``placeholders`` maps "in", "out"
    and "pdf" to paths; the argv list runs with no shell. Raises CleanerError on a bad
    entry, a missing program, a non-zero exit, a timeout, an ``{out}`` file that is
    missing or empty, or a ``{pdf}`` file that no longer starts with %PDF-. The
    caller keeps its own copy for a rollback; ``run_cleaner`` does that itself."""
    name = entry.get("name", "unnamed")
    timeout = entry.get("timeout_s", 60) if timeout_s is None else timeout_s
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise CleanerError(f"cleaner {name!r}: timeout_s must be a positive integer")
    values = {k: (None if placeholders.get(k) is None else str(placeholders[k])) for k in ("in", "out", "pdf")}
    argv = build_argv(entry, src=values["in"], dst=values["out"], pdf=values["pdf"])
    used = {m for a in entry["argv"] for m in re.findall(r"\{(in|out|pdf)\}", a)}
    result = Result(name, entry.get("stage", ""), values["pdf"] or values["in"] or "", "ran", argv=argv)
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise CleanerError(f"cleaner {name!r}: executable not found: {argv[0]}") from None
    except subprocess.TimeoutExpired:
        raise CleanerError(f"cleaner {name!r} did not finish within {timeout} s") from None
    result.returncode = completed.returncode
    if completed.returncode != 0:
        message = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "").strip()
        raise CleanerError(f"cleaner {name!r} exited {completed.returncode}"
                           + (f": {message[-300:]}" if message else ""))
    if "out" in used:
        out = Path(values["out"])
        if not out.is_file() or out.stat().st_size == 0:
            raise CleanerError(f"cleaner {name!r} wrote no non-empty {{out}} file ({out})")
    if "pdf" in used:
        with open(values["pdf"], "rb") as fh:
            if not fh.read(5).startswith(b"%PDF-"):
                raise CleanerError(f"cleaner {name!r} left {values['pdf']} not starting with %PDF-")
    return result


def run_stage(stage: str, paths, *, cfg=None, entries=None, runner=subprocess.run) -> list[Result]:
    """Run each configured cleaner over all paths, preserving both orders."""
    selected = cleaners_for(stage, cfg=cfg, entries=entries)
    files = list(paths)
    return [run_cleaner(entry, path, runner=runner) for entry in selected for path in files]


def _jpeg_without_metadata(data: bytes):
    """Cut metadata segments while preserving JPEG scan data and colour transforms."""
    if data[:2] != b"\xff\xd8":
        return None
    out, pos, cut = bytearray(data[:2]), 2, False
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:
            out.append(0xFF)
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:
            return None
        if marker == 0xDA:
            return bytes(out + data[pos:]) if cut else None
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        end = pos + 2 + length
        if length < 2 or end > len(data):
            return None
        body = data[pos + 4:end]
        keep = (not (0xE0 <= marker <= 0xEF or marker == 0xFE) or marker == 0xE0
                or (marker == 0xE2 and body.startswith(b"ICC_PROFILE\0"))
                or (marker == 0xEE and body.startswith(b"Adobe")))
        if keep:
            out.extend(data[pos:end])
        else:
            cut = True
        pos = end
    return None


def strip_pdf_bytes(data: bytes) -> tuple[bytes, int]:
    """(bytes, JPEG images cut): the PDF without /Info, XMP (/Metadata) and /PieceInfo on
    any object, and without Exif, XMP, IPTC and comment segments in JPEGs whose
    decoded pixels stay equal. Needs pikepdf and Pillow."""
    pikepdf, image = _optional("pikepdf"), _optional("PIL.Image")
    if pikepdf is None or image is None:
        raise CleanerError("strip_pdf_bytes needs pikepdf and Pillow")

    def decoded(raw):
        with image.open(io.BytesIO(raw)) as im:
            return im.mode, im.size, im.tobytes()

    cut = dropped = 0
    with pikepdf.open(io.BytesIO(data)) as pdf:
        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]
            dropped += 1
        for obj in pdf.objects:
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            for key in ("/Info", "/Metadata", "/PieceInfo"):
                if key in obj:
                    del obj[key]
                    dropped += 1
            if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != "/Image":
                continue
            filt = obj.get("/Filter")
            if (list(filt) if isinstance(filt, pikepdf.Array) else [filt]) != [pikepdf.Name.DCTDecode]:
                continue
            raw = obj.read_raw_bytes()
            new = _jpeg_without_metadata(raw)
            if new is not None:
                try:
                    same = decoded(raw) == decoded(new)
                except OSError:
                    same = False
                if same:
                    obj.write(new, filter=filt, decode_parms=obj.get("/DecodeParms"))
                    cut += 1
        if not (cut or dropped):
            return data, 0
        output = io.BytesIO()
        pdf.save(output)
        return output.getvalue(), cut


def qpdf_settle(data: bytes) -> bytes:
    """Rewrite metadata edits and use a content-derived document identifier."""
    pikepdf = _optional("pikepdf")
    if pikepdf is not None:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for key in ("/ID", "/Info"):
                if key in pdf.trailer:
                    del pdf.trailer[key]
            if "/Metadata" in pdf.Root:
                del pdf.Root["/Metadata"]
            output = io.BytesIO()
            pdf.save(output, linearize=True, deterministic_id=True)
            return output.getvalue()
    executable = shutil.which("qpdf")
    if executable is None:
        raise CleanerError("PDF rewrite needs pikepdf or qpdf")
    with tempfile.TemporaryDirectory(prefix="lt-pdf-") as directory:
        src, dst = Path(directory) / "in.pdf", Path(directory) / "out.pdf"
        src.write_bytes(data)
        completed = subprocess.run(
            [executable, "--remove-info", "--remove-metadata", "--linearize",
             "--deterministic-id", str(src), str(dst)],
            capture_output=True, text=True, timeout=60)
        if completed.returncode not in (0, 3):
            raise CleanerError(f"qpdf failed: {completed.stderr.strip()}")
        return dst.read_bytes()


def pdf_content(data: bytes):
    """Capture available page text, image pixels and link destinations."""
    pikepdf = _optional("pikepdf")
    mupdf = _optional("pymupdf") or _optional("fitz")
    image = _optional("PIL.Image")
    content = {}
    if mupdf is not None:
        with mupdf.open(stream=data, filetype="pdf") as document:
            content["page text"] = [page.get_text("text") for page in document]
            content["page count"] = len(document)
    if pikepdf is not None:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            content["page count"] = len(pdf.pages)
            page_ids = {page.obj.objgen: index for index, page in enumerate(pdf.pages)}

            def value(item):
                reference = getattr(item, "objgen", (0, 0))
                if reference in page_ids:
                    return ("page", page_ids[reference])
                if isinstance(item, pikepdf.Array):
                    return [value(part) for part in item]
                if isinstance(item, pikepdf.Dictionary):
                    return [(str(key), value(item[key])) for key in sorted(item.keys())]
                return None if item is None else str(item)

            links, images = [], []
            for page in pdf.pages:
                links.append([
                    (value(annotation.get("/Dest")), value(annotation.get("/A")))
                    for annotation in (page.obj.get("/Annots") or [])
                    if isinstance(annotation, pikepdf.Dictionary)
                    and annotation.get("/Subtype") == "/Link"
                ])
                if image is not None:
                    found = page.get_images() if hasattr(page, "get_images") else page.images
                    pixels = []
                    for name, obj in sorted(found.items(), key=lambda pair: str(pair[0])):
                        with pikepdf.PdfImage(obj).as_pil_image() as im:
                            pixels.append((str(name), im.mode, im.size,
                                           hashlib.sha256(im.tobytes()).hexdigest()))
                    images.append(pixels)
            content["link targets"] = links
            if image is not None:
                content["image pixels"] = images
    return content or None


def residual_metadata(path) -> list[str] | None:
    """List nonstructural metadata tags; preserve ICC colour profile tags."""
    executable = shutil.which("exiftool")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "-j", "-a", "-G1", "-n", str(path)],
            capture_output=True, text=True, timeout=60)
        tags = json.loads(completed.stdout)[0]
        if completed.returncode != 0 or not isinstance(tags, dict):
            return None
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, KeyError, TypeError):
        return None
    return sorted(key for key in tags if ":" in key and key not in STRUCTURAL
                  and not key.startswith(("System:", "File:", "ICC")))


def metadata_left(data: bytes) -> list[str] | None:
    """Inspect every PDF object for Info, XMP and PieceInfo metadata.
    Return None when neither pikepdf nor a complete qpdf JSON inspection works."""
    pikepdf = _optional("pikepdf")
    if pikepdf is None:
        executable = shutil.which("qpdf")
        if executable is None:
            return None
        try:
            with tempfile.TemporaryDirectory(prefix="lt-meta-check-") as directory:
                path = Path(directory) / "document.pdf"
                path.write_bytes(data)
                completed = subprocess.run(
                    [executable, "--json", "--json-key=qpdf", str(path)],
                    capture_output=True, text=True, timeout=60)
            if completed.returncode != 0:
                return None
            doc = json.loads(completed.stdout)
            objects = doc["qpdf"]
            if doc["version"] != 2 or len(objects) != 2 or not isinstance(objects[1], dict):
                return None
            if "trailer" not in objects[1]:
                return None
            left = []

            def inspect(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "/Info":
                            left.append("Info")
                        elif key == "/Metadata" or (key == "/Type" and child == "/Metadata"):
                            left.append("XMP")
                        elif key == "/PieceInfo":
                            left.append("PieceInfo")
                        inspect(child)
                elif isinstance(value, list):
                    for child in value:
                        inspect(child)

            inspect(objects[1])
            return sorted(set(left))
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            return None
    left = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        if "/Info" in pdf.trailer:
            left.append("Info")
        n = sum(1 for obj in pdf.objects
                if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) and "/Metadata" in obj)
        if n:
            left.append(f"XMP on {n} objects")
        if any(isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) and
               "/PieceInfo" in obj for obj in pdf.objects):
            left.append("PieceInfo")
    return left


def strip_pdf_metadata(path) -> Result:
    """Strip a PDF's metadata in place: no /Info, no XMP packet, a content-derived /ID.
    Steps: pikepdf object and JPEG strip, ``exiftool -all=``, then a rewrite with
    pikepdf (else qpdf), so exiftool's incremental update leaves no old revision.
    The file is replaced atomically, and only when page text, image pixels and link
    targets are unchanged. Returns a Result with status cleaned, unchanged, failed,
    or timeout. Missing metadata validation tools produce a failed result."""
    path = Path(path)
    result = Result("builtin-metadata", "pdf", str(path), "failed")
    steps = []
    pikepdf, image = _optional("pikepdf"), _optional("PIL.Image")
    exiftool, qpdf = shutil.which("exiftool"), shutil.which("qpdf")
    if pikepdf is None and qpdf is None:
        result.detail = "complete PDF metadata validation unavailable (needs pikepdf or qpdf); original kept"
        return result
    try:
        original = path.read_bytes()
        before = pdf_content(original)
        cleaned = original
        if pikepdf is not None and image is not None:
            cleaned, count = strip_pdf_bytes(cleaned)
            steps.append(f"object metadata stripped; {count} JPEG images stripped")
        else:
            steps.append("object/JPEG metadata strip skipped (needs pikepdf and Pillow)")
        if exiftool is not None:
            with tempfile.TemporaryDirectory(prefix="lt-meta-") as directory:
                temporary = Path(directory) / "document.pdf"
                temporary.write_bytes(cleaned)
                completed = subprocess.run(
                    [exiftool, "-all=", "-overwrite_original", str(temporary)],
                    capture_output=True, text=True, timeout=60)
                if completed.returncode != 0:
                    raise CleanerError(f"exiftool failed: {completed.stderr.strip()}")
                cleaned = temporary.read_bytes()
            steps.append("exiftool metadata stripped")
        else:
            steps.append("exiftool skipped (needs exiftool)")
        cleaned = qpdf_settle(cleaned)
        steps.append(f"deterministic rewrite with {'pikepdf' if pikepdf is not None else 'qpdf'}")
        note = _check_pdf(original, cleaned)
        if note:
            steps.append(note)
        left = metadata_left(cleaned)
        if left is None:
            raise CleanerError("complete PDF metadata validation unavailable; original kept")
        if left:
            raise CleanerError(f"metadata left after the rewrite: {', '.join(left)}; original kept")
        after = pdf_content(cleaned)
        if before != after:
            keys = set(before or {}) | set(after or {})
            changed = sorted(key for key in keys if (before or {}).get(key) != (after or {}).get(key))
            raise CleanerError(f"PDF content changed: {', '.join(changed)}; original kept")
        for key, dependency in (("page text", "PyMuPDF"),
                                ("image pixels", "pikepdf and Pillow"),
                                ("link targets", "pikepdf")):
            if key not in (before or {}):
                steps.append(f"{key} comparison skipped (needs {dependency})")
        if cleaned != original:
            _replace(path, cleaned)
            result.status = "cleaned"
        else:
            result.status = "unchanged"
    except subprocess.TimeoutExpired as exc:
        result.status = "timeout"
        steps.append(f"PDF metadata step exceeded {exc.timeout} seconds")
    except Exception as exc:
        steps.append(f"{type(exc).__name__}: {exc}")
    result.detail = "; ".join(steps)
    return result


STRIP_STATUS = {"cleaned": "cleaned", "unchanged": "already-clean"}


def strip_pdf(path) -> dict:
    """``strip_pdf_metadata`` as a dict: {"file", "status", "detail"} with status
    "cleaned", "already-clean" or "error" (failed, timeout or no tool to do it)."""
    r = strip_pdf_metadata(path)
    return {"file": r.path, "status": STRIP_STATUS.get(r.status, "error"), "detail": r.detail,
            "result": r.status}


def finalize(paths, *, stage: str, cfg=None, entries=None, runner=subprocess.run,
             strip_metadata: bool | None = None) -> list[Result]:
    """Run configured cleaners, then optional built-in PDF metadata stripping."""
    files = list(paths)
    results = run_stage(stage, files, cfg=cfg, entries=entries, runner=runner)
    if stage == "pdf":
        if strip_metadata is None:
            cfg = config.load() if cfg is None else cfg
            strip_metadata = cfg.get("pdf.strip_metadata", True)
        if strip_metadata:
            results.extend(strip_pdf_metadata(path) for path in files
                           if Path(path).suffix.lower() == ".pdf")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--stage", required=True, choices=STAGES)
    run.add_argument("--config")
    run.add_argument("--no-strip", action="store_true")
    run.add_argument("paths", nargs="+")
    listing = commands.add_parser("list")
    listing.add_argument("--config")
    strip = commands.add_parser("strip")
    strip.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "strip":
            results = [strip_pdf_metadata(path) for path in args.paths]
        else:
            cfg = config.load(args.config)
            if args.command == "list":
                for entry in cfg.get("text_cleaners", []):
                    print(json.dumps(entry))
                return 0
            results = finalize(args.paths, stage=args.stage, cfg=cfg,
                               strip_metadata=False if args.no_strip else None)
        for result in results:
            detail = " ".join(result.detail.split())
            print(f"{result.status} {result.name} {result.path}  {detail}")
        return int(any(result.status in ("failed", "timeout") for result in results))
    except (config.ConfigError, CleanerError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
