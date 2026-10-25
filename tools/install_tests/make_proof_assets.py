#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and verify the README proof assets in docs/assets/ from synthetic data.

    python3 make_proof_assets.py build  [--repo DIR] [options]   build, check, then copy into docs/assets/
    python3 make_proof_assets.py verify [--repo DIR]             check committed assets and README alt text
    python3 make_proof_assets.py plan   [--repo DIR]             print the commands build runs

Inputs are synthetic only: the schema-example module, answers and fill report in
tests/fixtures/examples/ and the toy diagram case. Build calls the release's own tools
(diagram build, module export, setup palette preview) in a throwaway work folder, with
a scratch config and data folder, so no real config, capture or answer is read. Nothing
in the repo changes except docs/assets/. If any step or check fails, docs/assets/ keeps
its previous content.

Assets (12 files plus MANIFEST.json): a toy diagram in a light and a dark palette with
its one-page PDF, a colour-vision sheet, a module export page and PDF, a fill review
page and PDF, two palette previews, and two text transcripts.

Build needs Python 3.11 or newer for this script, an interpreter with the release's CI
dependencies for the tools it launches (the current interpreter, else
`uv run --python 3.11 --group ci python`), and these programs on PATH: rsvg-convert,
xmllint, pdfinfo, pdftoppm, pdftotext, pdffonts. Verify needs only the standard library.

Exit codes: 0 done; 1 a step or check failed; 2 bad usage; 3 a tool, file or
interpreter is missing (the message names the work package that owns it).
Tested on Linux only.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("make_proof_assets.py needs Python 3.11 or newer.")

EPOCH = "946684800"                      # 2000-01-01T00:00:00Z, the date the fixtures carry
GROUPS = ("diagram", "cvd", "export", "fill", "palette")
TOTAL_MAX = 4_500_000                    # bytes for everything in docs/assets/
TOY_CASE = "skills/mechanistic-diagram/examples/toy-case"
EXAMPLES = "tests/fixtures/examples"
SUBSETS = {"png": "PNG image", "pdf": "PDF", "txt": "text"}

# file, kind, group, max bytes, exact width in px (None: any width from 600 to 2400)
ASSETS = [
    ("toy-diagram-light.png", "png", "diagram", 600_000, 1600),
    ("toy-diagram-dark.png", "png", "diagram", 600_000, 1600),
    ("toy-diagram-print.pdf", "pdf", "diagram", 400_000, None),
    ("diagram-build.txt", "txt", "diagram", 20_000, None),
    ("toy-diagram-cvd.png", "png", "cvd", 700_000, None),
    ("export-page.png", "png", "export", 400_000, 1000),
    ("sample-module-export.pdf", "pdf", "export", 400_000, None),
    ("fill-review-page.png", "png", "fill", 400_000, 1000),
    ("sample-fill-review.pdf", "pdf", "fill", 400_000, None),
    ("export-run.txt", "txt", "fill", 20_000, None),
    ("palette-preview-light.png", "png", "palette", 400_000, None),
    ("palette-preview-dark.png", "png", "palette", 400_000, None),
]
BY_NAME = {a[0]: a for a in ASSETS}

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_KEEP = {b"IHDR", b"PLTE", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"pHYs", b"bKGD",
            b"IDAT", b"IEND"}
PDF_META_KEYS = ("Title", "Subject", "Keywords", "Author", "Creator", "Producer",
                 "CreationDate", "ModDate")
PDF_RAW_META = re.compile(rb"/(Title|Author|Creator|Producer|Subject|Keywords|CreationDate|ModDate)\s*[(<]")
LEAK = re.compile(r"(/home/|/Users/|/tmp/|/var/folders/|[A-Za-z]:\\Users\\|/mnt/[a-z]/)")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SYMBOL_FONTS = ("dejavusans", "notosanssymbols", "notosansmath", "nimbussans")     # libre fallbacks, may appear
COLOUR_WORDS = re.compile(r"\b(red|green|blue|yellow|orange|purple|pink|brown|violet|teal|cyan|"
                          r"magenta|beige|gold|golden)\b", re.I)
BAD_ALT_START = re.compile(r"^(an? )?(image|picture|photo|graphic|screenshot|figure)\b", re.I)
ALT_SOFT, ALT_HARD = 125, 150

# Visible captions sit under each image in the README. Each says the data is synthetic.
CAPTION = {
    "toy-diagram-light.png": (
        "Synthetic example. A mechanism diagram built from the toy case in "
        "`skills/mechanistic-diagram/examples/toy-case`, an invented enzyme deficiency. "
        "Palette: {light_name} (dark mode shows {dark_name}). "
        "[One-page PDF](docs/assets/toy-diagram-print.pdf), "
        "[build checks](docs/assets/diagram-build.txt)."),
    "toy-diagram-cvd.png": (
        "Synthetic example. The toy diagram as seen with three kinds of colour-vision "
        "deficiency and in greyscale. Every box keeps its text label and every arrow keeps "
        "its dash or head."),
    "export-page.png": (
        "Synthetic example. A module export built from the schema example in "
        "`tests/fixtures/examples`. Every claim carries a source tag. "
        "[Full PDF](docs/assets/sample-module-export.pdf)."),
    "fill-review-page.png": (
        "Synthetic example. The same module after a fill run that stopped before a one-way "
        "door. The suite never clicks Submit: the human reads this page, then submits. "
        "[Full PDF](docs/assets/sample-fill-review.pdf), "
        "[export log](docs/assets/export-run.txt)."),
    "palette-preview-light.png": (
        "Synthetic example. The setup skill's preview of the default light palette, "
        "{light_name}, with measured contrast."),
}


class Fail(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def say(msg: str = "") -> None:
    print(msg, flush=True)


def tail(text: str, n: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- command builders
# One place per external command. build runs them, plan prints them.
def cmd_diagram_build(py, repo, case):
    return [*py, f"{repo}/skills/mechanistic-diagram/engine/build.py", case, "--final", "--trust-case"]


def cmd_diagram_access(py, repo, case):
    return [*py, f"{repo}/skills/mechanistic-diagram/engine/access.py", case, "--quiet"]


def cmd_export(py, repo, cfg, data, module, answers, fill, out):
    argv = [*py, f"{repo}/skills/lt-module-export/scripts/export_module.py",
            "--config", cfg, "--data-dir", data, "--module", module, "--answers", answers]
    if fill:
        argv += ["--fill-report", fill]
    return argv + ["--week", "1", "--out-dir", out, "--pdf"]


def cmd_preview(py, repo, out):
    return [*py, f"{repo}/skills/lt-suite-setup/scripts/preview.py", "palettes", "--top", "99", "--out", out]


def cmd_raster(pdf, prefix, page, width):
    return ["pdftoppm", "-png", "-singlefile", "-f", page, "-l", page,
            "-scale-to-x", width, "-scale-to-y", "-1", pdf, prefix]


def display(argv, repo, work, py):
    """A command as a reader would type it: repo-relative, work paths as $WORK."""
    s = " ".join(shlex.quote(str(a)) if " " in str(a) else str(a) for a in argv)
    py_s = " ".join(py)
    if py_s and s.startswith(py_s):
        s = "python3" + s[len(py_s):]
    return s.replace(f"{work}", "$WORK").replace(f"{repo}/", "")


# --------------------------------------------------------------------------- PNG and PDF helpers
def png_chunks(data: bytes):
    if data[:8] != PNG_SIG:
        raise Fail(1, "file is not a PNG")
    pos, out = 8, []
    while pos < len(data):
        if pos + 12 > len(data):
            raise Fail(1, "PNG is cut off")
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        out.append((data[pos + 4:pos + 8], pos, n + 12))
        pos += n + 12
    return out


def png_size(data: bytes):
    return struct.unpack(">II", data[16:24])


def strip_png(path: Path) -> list[str]:
    """Rewrite the file with only the structural chunks. Returns the dropped chunk types."""
    data = path.read_bytes()
    keep, dropped = [PNG_SIG], []
    for typ, pos, n in png_chunks(data):
        if typ in PNG_KEEP:
            keep.append(data[pos:pos + n])
        else:
            dropped.append(typ.decode("latin-1"))
    if dropped:
        path.write_bytes(b"".join(keep))
    return dropped


def pdf_raw_problems(data: bytes) -> list[str]:
    out = sorted({m.group(1).decode() for m in PDF_RAW_META.finditer(data)})
    out = [f"/Info {k}" for k in out]
    if b"<x:xmpmeta" in data or re.search(rb"/Type\s*/Metadata", data):
        out.append("XMP packet")
    return out


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u00a0", " ")).strip().lower()


# --------------------------------------------------------------------------- embedded helper
# Runs in the release interpreter (it imports ltcore, Pillow and the export base module).
HELPER = r'''
import json, sys
from pathlib import Path

req = json.load(open(sys.argv[1], encoding="utf-8"))
mode = req["mode"]
repo = Path(req["repo"])
sys.path.insert(0, str(repo / "shared"))


def done(obj):
    print(json.dumps(obj))
    sys.exit(0)


def die(msg):
    sys.exit("helper %s: %s" % (mode, msg))


def want(mod, name):
    fn = getattr(mod, name, None)
    if fn is None:
        die("%s has no %s (interface missing)" % (mod.__name__, name))
    return fn


if mode == "font-usage":
    import ctypes
    import pypdfium2 as pdfium
    result = {}
    for filename in req["files"]:
        used = set()
        with pdfium.PdfDocument(filename) as document:
            for page in document:
                text = page.get_textpage()
                for index in range(text.count_chars()):
                    flags = ctypes.c_int()
                    length = pdfium.raw.FPDFText_GetFontInfo(text, index, None, 0, ctypes.byref(flags))
                    if length:
                        buffer = ctypes.create_string_buffer(length)
                        pdfium.raw.FPDFText_GetFontInfo(text, index, buffer, length, ctypes.byref(flags))
                        used.add(buffer.value.decode("utf-8", "replace"))
                text.close()
                page.close()
        result[filename] = sorted(used)
    done(result)

if mode == "optimize":
    from PIL import Image
    for p in req["files"]:
        with Image.open(p) as im:
            im.load()
            im.save(p, format="PNG", optimize=True)
    done({"ok": True})

from ltcore import config, palettes
cfg = config.load(req["config"])

if mode == "discover":
    meta = want(palettes, "catalog_meta")()
    avail = want(palettes, "available")(cfg)
    light = req.get("light") or want(palettes, "default_print_palette")()
    dark = req.get("dark")
    if not dark:
        for pid in meta.get("defaults", {}).get("palettes", []):
            entry = avail.get(pid)
            if entry is not None and pid != light and not palettes.is_light(entry):
                dark = pid
                break
    if not dark:
        die("no dark palette among the catalog defaults; pass --dark")
    for pid in (light, dark):
        if pid not in avail:
            die("palette %r is not in the catalog" % pid)
    done({"light": light, "dark": dark})

if mode == "checks":
    derive = want(palettes, "derive_theme")
    check = want(palettes, "check_theme")
    contrast = want(palettes, "contrast")
    avail = want(palettes, "available")(cfg)
    problems, warnings = [], []
    res = {"palettes": {}, "roles": len(want(palettes, "ROLES")),
           "edge_kinds": len(want(palettes, "EDGE_KINDS"))}
    for label in ("light", "dark"):
        pid = req[label]
        try:
            theme = derive(pid, cfg)
        except Exception as ex:
            problems.append("%s palette %s: %s" % (label, pid, ex))
            continue
        rep = check(theme, cvd=req["cvd"])
        problems += ["%s palette %s: %s" % (label, pid, f) for f in rep["failures"]]
        warnings += ["%s palette %s: %s" % (label, pid, f) for f in rep["reported"]]
        groups = {}
        for kind, spec in theme["edges"].items():
            cue = tuple(spec[1:]) if isinstance(spec, (list, tuple)) else ()
            groups.setdefault(cue, []).append(kind)
        for cue, kinds in groups.items():
            if len(kinds) > 1:
                warnings.append("%s palette %s: arrow kinds %s share dash and head, so colour alone "
                                "separates them" % (label, pid, "/".join(kinds)))
        res["palettes"][label] = {
            "id": pid, "name": avail[pid].get("name", pid), "dark": not palettes.is_light(theme),
            "text_min": rep["text_min"], "nontext_min": rep["nontext_min"],
            "text_on_fill_min": rep["text_on_fill_min"],
            "stroke_distance": {s: rep["strokes"][s]["de_ok"] for s in ("none", "protan", "deutan", "tritan")},
            "stroke_grey_gap": rep["strokes"]["grey"]["dl"]}
    sys.path.insert(0, str(repo / "skills" / "lt-module-export" / "scripts"))
    try:
        import pdf_base
        themes, notes = pdf_base.pdf_themes(cfg)
    except Exception as ex:
        problems.append("PDF themes: %s" % ex)
        themes, notes = [], []
    floor = float(getattr(pdf_base, "MIN_TEXT_CONTRAST", 4.5)) if themes else 4.5
    keys = tuple(getattr(pdf_base, "TEXT_KEYS", ("text", "sub", "over", "blue", "mauve", "peach"))) if themes else ()
    for note in notes:
        if "unavailable" in note:
            problems.append("PDF themes fell back to the built-in ones: " + note)
    res["pdf"] = []
    for pid, use, th in themes:
        worst = min(contrast(th[k], th[g]) for k in keys for g in ("base", "mantle"))
        if worst < floor - 1e-9:
            problems.append("PDF theme %s (%s): text contrast %.2f is below %.1f" % (pid, use, worst, floor))
        res["pdf"].append({"id": pid, "use": use, "text_min": round(worst, 2)})
    fonts_cfg = {role: cfg.get("fonts.pdf." + role) or cfg.get("fonts." + role, "") for role in ("sans", "mono")}
    try:
        from ltcore import fonts
        sans = Path(str(want(fonts, "resolve")("sans", weight=400, cfg=cfg, output="pdf", os_fallback=True)))
        fonts_cfg["resolved"] = [fonts.family(role, cfg, output=output, os_fallback=output == "pdf")
                                 for output in ("pdf", "diagram") for role in ("sans", "mono")]
        fonts_cfg["sans_file"] = sans.name
        fonts_cfg["bundled"] = any(str(sans).startswith(str(repo / d)) for d in ("shared/fonts", "skills"))
        if not fonts_cfg["bundled"]:
            warnings.append("sans font %s comes from outside the repo, so another machine may render "
                            "the assets differently" % sans.name)
    except SystemExit:
        raise
    except Exception as ex:
        problems.append("fonts: %s" % ex)
    res["fonts"] = fonts_cfg
    res["problems"], res["warnings"] = problems, warnings
    done(res)

if mode == "sheet":
    from PIL import Image, ImageDraw, ImageFont
    from ltcore import fonts
    path = want(fonts, "resolve")("sans", weight=700, cfg=cfg)
    font = ImageFont.truetype(str(path), 22)
    W, PAD, BAR = 800, 24, 44
    cells = []
    for label, p in req["images"]:
        with Image.open(p) as im:
            im = im.convert("RGB")
            cells.append((label, im.resize((W, max(1, round(im.height * W / im.width))), Image.LANCZOS)))
    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    height = PAD + sum(BAR + max(c[1].height for c in r) + PAD for r in rows)
    sheet = Image.new("RGB", (2 * W + 3 * PAD, height), "#ffffff")
    draw = ImageDraw.Draw(sheet)
    y = PAD
    for r in rows:
        for i, (label, im) in enumerate(r):
            x = PAD + i * (W + PAD)
            draw.text((x, y + 8), label, fill="#000000", font=font)
            sheet.paste(im, (x, y + BAR))
        y += BAR + max(c[1].height for c in r) + PAD
    sheet.save(req["out"], format="PNG", optimize=True)
    done({"ok": True, "size": sheet.size})

if mode == "strip-pdf":
    from ltcore import cleaners
    fn = want(cleaners, "strip_pdf_metadata")
    done({"results": [fn(p) for p in req["files"]]})

die("unknown mode")
'''


# --------------------------------------------------------------------------- context
class Ctx:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.repo = find_repo(a.repo)
        self.out = Path(a.out).resolve() if a.out else self.repo / "docs" / "assets"
        self.groups = pick_groups(a.only, a.skip)
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.facts: dict = {}
        self.checks: dict = {}
        self.page_text: dict[str, str] = {}
        self.entries: dict[str, dict] = {}
        self.versions: dict[str, str] = {}
        self.light = a.light
        self.dark = a.dark
        self.py: list[str] = []
        self.python_work: Path | None = None
        self.work = Path()

    # -- preflight ---------------------------------------------------------------------------
    def preflight(self) -> None:
        missing: list[str] = []
        need_bins = ["pdfinfo", "pdftoppm", "pdftotext", "pdffonts"]
        if {"diagram", "cvd"} & self.groups:
            need_bins += ["rsvg-convert", "xmllint"]
        for b in need_bins:
            if shutil.which(b) is None:
                missing.append(f"program not on PATH: {b} (poppler-utils, librsvg2-bin and libxml2-utils on Debian)")
        files = [("tools/privacy_gate.py", "WP01")] if not self.a.no_gate else []
        files += [(f"{EXAMPLES}/module.example.json", "WP02"), (f"{EXAMPLES}/answers.example.json", "WP02"),
                  (f"{EXAMPLES}/fill-report.example.json", "WP02"),
                  ("shared/ltcore/palettes.py", "WP09"), ("shared/palettes/catalog.json", "WP09"),
                  ("shared/ltcore/fonts.py", "WP09"), ("shared/ltcore/cleaners.py", "WP09")]
        if {"diagram", "cvd"} & self.groups:
            files += [("skills/mechanistic-diagram/engine/build.py", "WP10"),
                      ("skills/mechanistic-diagram/engine/access.py", "WP10"),
                      (f"{TOY_CASE}/case.json", "WP10")]
        if {"export", "fill"} & self.groups:
            files += [("skills/lt-module-export/scripts/export_module.py", "WP08"),
                      ("skills/lt-module-export/scripts/module_pdf.py", "WP08"),
                      ("skills/lt-module-export/scripts/pdf_base.py", "WP08")]
        if "palette" in self.groups:
            files += [("skills/lt-suite-setup/scripts/preview.py", "WP11")]
        for rel, wp in files:
            if not (self.repo / rel).is_file():
                missing.append(f"file missing: {rel} (owner {wp}; not built yet?)")
        if missing:
            raise Fail(3, "cannot start, " + str(len(missing)) + " item(s) missing:\n  " + "\n  ".join(missing))

    def find_python(self) -> None:
        probe = "import sys; assert sys.version_info >= (3, 11); import PIL, reportlab, pypdfium2, fontTools, numpy, jsonschema"
        cands = []
        if self.a.python:
            cands.append(shlex.split(self.a.python))
        if os.environ.get("LT_SUITE_PROOF_PYTHON"):
            cands.append(shlex.split(os.environ["LT_SUITE_PROOF_PYTHON"]))
        cands.append([sys.executable])
        if shutil.which("uv"):
            cands.append(["uv", "run", "--python", "3.11", "--group", "ci", "python"])
        tried = []
        self.python_work = Path(tempfile.mkdtemp(prefix="lt-suite-proof-python-"))
        probe_env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(self.python_work / "venv"))
        for cand in cands:
            try:
                p = subprocess.run([*cand, "-c", probe], encoding='utf-8', cwd=self.repo, env=probe_env,
                                   capture_output=True, text=True, timeout=600)
            except (OSError, subprocess.SubprocessError) as ex:
                tried.append(f"{' '.join(cand)}: {ex}")
                continue
            if p.returncode == 0:
                self.py = cand
                return
            tried.append(f"{' '.join(cand)}: {tail(p.stderr, 2)}")
        shutil.rmtree(self.python_work, ignore_errors=True)
        raise Fail(3, "no interpreter has Python 3.11+ with Pillow, reportlab, pypdfium2, fontTools, numpy and "
                      "jsonschema. Tried:\n  " + "\n  ".join(tried) +
                   "\nPass --python, or set LT_SUITE_PROOF_PYTHON.")

    # -- work folder -------------------------------------------------------------------------
    def prepare(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="lt-suite-proof-")).resolve()
        for d in ("data", "cache", "tmp", "logs", "stage", "preview"):
            (self.work / d).mkdir()
        self.logs, self.stage, self.data = self.work / "logs", self.work / "stage", self.work / "data"
        self.cfgfile = self.work / "proof-config.toml"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LT_SUITE_")}
        env.update({"LT_SUITE_CONFIG": str(self.cfgfile), "LT_SUITE_DATA": str(self.data),
                    "LT_SUITE_CACHE": str(self.work / "cache"), "LT_SUITE_NO_INSTALL": "1",
                    "SOURCE_DATE_EPOCH": EPOCH, "TZ": "UTC", "TMPDIR": str(self.work / "tmp"),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"})
        if self.python_work is not None:
            env["UV_PROJECT_ENVIRONMENT"] = str(self.python_work / "venv")
        self.env = env
        (self.work / "tmp" / "helper.py").write_text(HELPER, encoding="utf-8")

    def write_config(self) -> None:
        lines = ["# Scratch config for the proof assets. Synthetic values only.", "schema_version = 1", "",
                 "[lt]", 'origin = "https://example.lt.app"', "", "[notes]", 'graph_path = ""', "",
                 "[export.outputs]", "module_page = true", "pdf = true", "notes_graph = false", "",
                 "[pdf]", 'author = ""', "strip_metadata = true"]
        if self.light and self.dark:
            lines += ["", "[style]", f'palettes = ["{self.light}", "{self.dark}"]', f'print_palette = "{self.light}"']
        self.cfgfile.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- running things ----------------------------------------------------------------------
    def run(self, argv, name, *, timeout=1200, ok=(0,), cwd=None):
        argv = [str(x) for x in argv]
        try:
            p = subprocess.run(argv, encoding='utf-8', cwd=str(cwd or self.repo), env=self.env, capture_output=True,
                               text=True, errors="replace", timeout=timeout)
        except FileNotFoundError:
            raise Fail(3, f"{name}: program not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            raise Fail(1, f"{name}: no result after {timeout} s: {' '.join(argv)}")
        log = self.logs / f"{name}.log"
        log.write_text(f"$ {' '.join(argv)}\n{p.stdout}\n--- stderr ---\n{p.stderr}", encoding="utf-8")
        if p.returncode not in ok:
            raise Fail(1, f"{name}: exit {p.returncode}: {' '.join(argv)}\n{tail(p.stderr or p.stdout)}\nlog: {log}")
        return p

    def helper(self, mode, **kw):
        req = {"mode": mode, "repo": str(self.repo), "config": str(self.cfgfile), **kw}
        f = self.work / "tmp" / f"helper-{mode}.json"
        f.write_text(json.dumps(req), encoding="utf-8")
        p = self.run([*self.py, self.work / "tmp" / "helper.py", f], f"helper-{mode}")
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        if not lines:
            raise Fail(1, f"helper {mode} printed no result:\n{tail(p.stdout + p.stderr)}")
        return json.loads(lines[-1])

    def pdfinfo(self, pdf: Path) -> dict:
        p = self.run(["pdfinfo", pdf], "pdfinfo")
        info = {}
        for ln in p.stdout.splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                info[k.strip()] = v.strip()
        return info

    def pdf_pages(self, pdf: Path) -> int:
        """Read the real page count through Poppler."""
        return int(self.pdfinfo(pdf).get("Pages", "0"))

    def pdf_pages_text(self, pdf: Path) -> list[str]:
        p = self.run(["pdftotext", "-layout", "-enc", "UTF-8", pdf, "-"], "pdftotext")
        pages = p.stdout.split("\f")
        return pages[:-1] if pages and not pages[-1].strip() else pages

    def pdf_fonts(self, pdf: Path) -> list[tuple[str, str]]:
        p = self.run(["pdffonts", pdf], "pdffonts")
        rows = []
        for ln in p.stdout.splitlines()[2:]:
            m = re.match(r"^(\S+)\s+.+?\s+\S+\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$", ln)
            if m:
                rows.append((m.group(1), m.group(2)))
        return rows

    # -- staging -----------------------------------------------------------------------------
    def raster(self, pdf: Path, name: str, width: int, page: int = 1) -> None:
        prefix = self.stage / name[:-4]
        self.run(cmd_raster(pdf, prefix, page, width), "raster-" + name[:-4])
        if not (self.stage / name).is_file():
            raise Fail(1, f"pdftoppm wrote no {name} for {pdf.name}")

    def stage_copy(self, src: Path, name: str) -> None:
        shutil.copyfile(src, self.stage / name)

    def note(self, name: str, command: str, check: str) -> None:
        e = self.entries.setdefault(name, {"command": [], "checks": []})
        if command and command not in e["command"]:
            e["command"].append(command)
        e["checks"].append(check)

    def transcript(self, name: str, text: str, command: str) -> None:
        text = ANSI.sub("", text).replace("\r\n", "\n")
        for raw in {str(self.work), str(self.work.resolve()), str(self.repo)}:
            text = text.replace(raw, "$WORK" if raw.startswith(str(self.work)) else "$REPO")
        text = "\n".join(ln.rstrip() for ln in text.splitlines())
        bad = LEAK.search(text)
        if bad:
            raise Fail(1, f"{name}: the transcript still holds an absolute path near {text[max(0, bad.start() - 30):bad.end() + 30]!r}")
        head = ("SPDX-FileCopyrightText: 2026 dorsaltegmentum\n"
                "SPDX-" "License-Identifier: AGPL-3.0-or-later\n\nSynthetic data. Output of the command below, with paths shortened to $WORK and $REPO.\n\n"
                f"$ {command}\n\n")
        (self.stage / name).write_text(head + text.strip() + "\n", encoding="utf-8")


def find_repo(arg) -> Path:
    cands = [Path(arg)] if arg else [Path.cwd(), Path(__file__).resolve().parent.parent]
    for p in cands:
        p = p.resolve()
        if (p / "skills" / "mechanistic-diagram").is_dir() and (p / "shared" / "ltcore").is_dir():
            return p
    raise Fail(2, "cannot find the release repo (it holds skills/mechanistic-diagram and shared/ltcore); pass --repo")


def pick_groups(only, skip) -> set[str]:
    names = set(GROUPS)
    for label, val in (("--only", only), ("--skip", skip)):
        bad = [g for g in (val or "").split(",") if g and g not in GROUPS]
        if bad:
            raise Fail(2, f"{label}: unknown group {', '.join(bad)}; groups are {', '.join(GROUPS)}")
    if only:
        names = {g for g in only.split(",") if g}
    for g in (skip or "").split(","):
        names.discard(g)
    if "cvd" in names and "diagram" not in names:
        raise Fail(2, "group cvd reads the diagram build, so it needs group diagram")
    if not names:
        raise Fail(2, "no group selected")
    return names


def pick_by_id(files: list[Path], pid: str, what: str) -> Path:
    """The file for a palette id: names containing the id as a whole token, shortest first."""
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(pid.lower()) + r"(?![a-z0-9])")
    hits = [f for f in files if pat.search(f.stem.lower())]
    if not hits:
        raise Fail(1, f"{what}: no file for palette {pid} among: {', '.join(sorted(f.name for f in files)) or 'nothing'}")
    hits.sort(key=lambda f: (len(f.name), f.name))
    if len(hits) > 1 and len(hits[0].name) == len(hits[1].name):
        raise Fail(1, f"{what}: more than one file for palette {pid}: {', '.join(f.name for f in hits)}")
    return hits[0]


# --------------------------------------------------------------------------- steps
def step_diagram(c: Ctx) -> None:
    say("diagram: build the toy case and check it")
    case = c.work / "toy-case"
    shutil.copytree(c.repo / TOY_CASE, case,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "deliver", "checks", "geometry.json", "final-*"))
    build = cmd_diagram_build(c.py, c.repo, case)
    p = c.run(build, "diagram-build")
    c.run(cmd_diagram_access(c.py, c.repo, case), "diagram-access")
    deliver = case / "deliver"
    pdfs = sorted(deliver.glob("*.pdf"))
    light_pdf, dark_pdf = pick_by_id(pdfs, c.light, "deliver/"), pick_by_id(pdfs, c.dark, "deliver/")
    need = norm(c.a.diagram_sentinel)
    for pdf in (light_pdf, dark_pdf):
        if c.pdf_pages(pdf) != 1:
            raise Fail(1, f"{pdf.name}: the diagram must be one page")
        if need not in norm(" ".join(c.pdf_pages_text(pdf))):
            raise Fail(1, f"{pdf.name}: pdftotext finds no text {c.a.diagram_sentinel!r}, so the PDF text is not selectable "
                          f"or the toy case changed (--diagram-sentinel)")
    cmd = display(build, c.repo, c.work, c.py)
    c.raster(light_pdf, "toy-diagram-light.png", 1600)
    c.raster(dark_pdf, "toy-diagram-dark.png", 1600)
    c.stage_copy(light_pdf, "toy-diagram-print.pdf")
    for n in ("toy-diagram-light.png", "toy-diagram-dark.png", "toy-diagram-print.pdf"):
        c.note(n, cmd, "build.py and access.py exit 0 (WCAG gate); text selectable")
    out = p.stdout
    i = out.find("checks:")
    c.transcript("diagram-build.txt", out[i:] if i >= 0 else out, cmd)
    c.note("diagram-build.txt", cmd, "paths shortened, no absolute path left")
    c.facts["diagram"] = geometry_facts(case / "geometry.json")


def geometry_facts(path: Path) -> dict:
    try:
        geo = json.loads(path.read_text(encoding="utf-8"))
        nodes = geo["nodes"]
        vals = list(nodes.values()) if isinstance(nodes, dict) else list(nodes)
        stages = {n.get("cluster") for n in vals if isinstance(n, dict) and n.get("cluster")}
        kinds = {e.get("kind") for e in geo.get("edges", []) if isinstance(e, dict) and e.get("kind")}
        return {"boxes": len(vals), "stages": len(stages) or None, "edge_kinds": len(kinds) or None}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def step_cvd(c: Ctx) -> None:
    say("cvd: build the colour-vision sheet")
    d = c.work / "toy-case" / "checks" / "cvd"
    cells = []
    for sim, label in (("protan", "Protanopia"), ("deutan", "Deuteranopia"), ("tritan", "Tritanopia"), ("grey", "Greyscale")):
        files = sorted(d.glob(f"*-{sim}.png"))
        if not files:
            raise Fail(1, f"access.py wrote no *-{sim}.png in {d.name}/ ({d})")
        cells.append([label, str(pick_by_id(files, c.light, "checks/cvd") if len(files) > 1 else files[0])])
    c.helper("sheet", out=str(c.stage / "toy-diagram-cvd.png"), images=cells)
    c.note("toy-diagram-cvd.png", "access.py simulations, joined by the helper (Pillow)",
           "4 simulations present; labels are text, black on white")


def export_pdfs(d: Path) -> list[tuple[Path, str, str]]:
    pat = re.compile(r"\((?P<id>[^(),]+), (?P<use>print|screen)\)\.pdf$")
    out = []
    for f in sorted(d.glob("*.pdf")):
        m = pat.search(f.name)
        if m:
            out.append((f, m.group("id"), m.group("use")))
    return out


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise Fail(2, f"cannot read {path}: {ex}")


def answer_sentinel(answers: Path) -> str:
    for item in load_json(answers).get("items", []):
        if item.get("status") == "drafted":
            for seg in item.get("segments", []):
                words = str(seg.get("text", "")).split()
                if len(words) >= 3:
                    return " ".join(words[:4])
    raise Fail(2, f"{answers.name} has no drafted answer text to look for in the PDF")


def run_export(c: Ctx, tag: str, fill: bool) -> tuple[Path, str, subprocess.CompletedProcess, str]:
    out = c.work / f"export-{tag}"
    argv = cmd_export(c.py, c.repo, c.cfgfile, c.data, c.module, c.answers, c.fill_report if fill else None, out)
    p = c.run(argv, f"export-{tag}")
    if "palette themes unavailable" in p.stdout + p.stderr:
        raise Fail(1, "export used the built-in themes, not the configured palettes (WP09 palette API missing)")
    pdfs = export_pdfs(out / "pdf")
    prints = [x for x in pdfs if x[2] == "print"]
    if len(prints) != 1:
        raise Fail(1, f"export wrote {len(prints)} print PDFs in pdf/ (want 1): {[x[0].name for x in pdfs]}")
    final = out / "pdf" / ".final.json"
    if final.is_file():
        bad = re.findall(r'"status":\s*"(error|verify|kept-original)"', final.read_text(encoding="utf-8"))
        if bad:
            raise Fail(1, f"the PDF final pass reported {sorted(set(bad))} in {final.name}")
    else:
        c.warnings.append("export left no pdf/.final.json, so the final pass is unproven")
    return prints[0][0], prints[0][1], p, display(argv, c.repo, c.work, c.py)


def step_export(c: Ctx) -> None:
    say("export: module export without a fill report")
    pdf, pid, _p, cmd = run_export(c, "answers", fill=False)
    pages = c.pdf_pages_text(pdf)
    need = norm(answer_sentinel(c.answers))
    if need not in norm(" ".join(pages)):
        raise Fail(1, f"{pdf.name}: pdftotext finds no drafted answer text {need!r}; the PDF may hold text as images")
    c.stage_copy(pdf, "sample-module-export.pdf")
    c.raster(pdf, "export-page.png", 1000, page=1)
    tag = re.search(r"\[T\d:[^\]\s]+\]", pages[0])
    c.facts["export_tag"] = tag.group(0) if tag else None
    if not tag:
        c.warnings.append("page 1 of the export shows no source tag, so its alt text stays generic")
    for n in ("sample-module-export.pdf", "export-page.png"):
        c.note(n, cmd, f"palette {pid}; answer text selectable; fonts embedded")


def step_fill(c: Ctx) -> None:
    """Render a compact synthetic excerpt with a conflict and placement TODO.

    The full fixture includes unrelated policies and questions. The README sample
    keeps 2 items and recomputes their source bindings through the public digest API.
    It remains a synthetic illustration, with no browser writes or real answers.
    """
    say("fill: module export with a 2-item synthetic review excerpt")
    from collections import Counter
    sys.path.insert(0, str(c.repo / "shared"))
    from ltcore.digest import question_digest
    excerpt = c.work / "fill-example"
    excerpt.mkdir()
    module, answers, report = map(load_json, (c.module, c.answers, c.fill_report))
    keep = {"i-text-1", "i-unmapped-1"}
    for page in module["pages"]:
        page["items"] = [i for i in page.get("items", []) if i["item_id"] in keep]
        page["content"] = []
        page.pop("rat_candidate", None)
        page.pop("policy_skip", None)
    module["commit_boundaries"]["single_attempt_items"] = []
    module["commit_boundaries"]["unmapped"] = []
    items = [i for page in module["pages"] for i in page["items"]]
    module["census"].update(captured_total=len(items), answerable_total=len(items),
                           by_type=dict(Counter(i["type"] for i in items)))
    answers["items"] = [i for i in answers["items"] if i["item_id"] in keep]
    report["items"] = [i for i in report["items"] if i["item_id"] in keep]
    report["selector_events"] = []
    report["connection_events"] = []
    report["boundary_reached"] = {"stopped": True, "class": "run-level-commit",
                                 "page_index": 5, "detail": "Synthetic run stops before completion."}
    old = c.module, c.answers, c.fill_report
    c.module, c.answers, c.fill_report = (excerpt / "module.example.json",
                                        excerpt / "answers.example.json",
                                        excerpt / "fill-report.example.json")
    c.module.write_text(json.dumps(module, indent=1) + "\n", encoding="utf-8")
    answers["source_module"].update(path=c.module.name, sha256=sha256(c.module),
                                    question_digest=question_digest(module))
    c.answers.write_text(json.dumps(answers, indent=1) + "\n", encoding="utf-8")
    report["source_answers"].update(path=c.answers.name, sha256=sha256(c.answers))
    c.fill_report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    pdf, pid, p, cmd = run_export(c, "fill", fill=True)
    report = load_json(c.fill_report)
    items = report.get("items", [])
    needles = ["Fill:"]
    if any(i.get("lt_conflict") for i in items):
        needles.append("Lt marks this wrong")
    if any(i.get("todo") for i in items):
        needles.append("Placement:")
    pages = [norm(t) for t in c.pdf_pages_text(pdf)]
    whole = " ".join(pages)
    absent = [n for n in needles if norm(n) not in whole]
    if absent:
        raise Fail(1, f"{pdf.name}: the fill review lacks {absent}; the export did not render fill results as text")
    scores = [sum(norm(n) in t for n in needles) for t in pages]
    page = scores.index(max(scores)) + 1
    c.facts["fill_page"] = page
    c.stage_copy(pdf, "sample-fill-review.pdf")
    c.raster(pdf, "fill-review-page.png", 1000, page=page)
    c.transcript("export-run.txt", p.stdout, cmd)
    for n in ("sample-fill-review.pdf", "fill-review-page.png"):
        c.note(n, cmd, f"palette {pid}; page {page} holds {len(needles)} of {len(needles)} fill markers as text")
    c.note("export-run.txt", cmd, "paths shortened, no absolute path left")
    c.module, c.answers, c.fill_report = old


def step_palette(c: Ctx) -> None:
    say("palette: setup preview")
    out = c.work / "preview"
    argv = cmd_preview(c.py, c.repo, out)
    c.run(argv, "preview")
    pngs = sorted(out.rglob("*.png"))
    if not pngs:
        raise Fail(1, "preview.py wrote no PNG")
    cmd = display(argv, c.repo, c.work, c.py)
    for role, pid in (("light", c.light), ("dark", c.dark)):
        name = f"palette-preview-{role}.png"
        c.stage_copy(pick_by_id(pngs, pid, "preview.py output"), name)
        c.note(name, cmd, f"palette {pid}; swatches and contrast table are drawn by preview.py")


# --------------------------------------------------------------------------- checks and hygiene
def a11y_checks(c: Ctx) -> None:
    say("checks: palette contrast, colour vision, PDF themes, fonts")
    res = c.helper("checks", light=c.light, dark=c.dark, cvd=c.a.cvd)
    c.checks = res
    c.warnings += res.get("warnings", [])
    if res.get("problems"):
        raise Fail(1, "accessibility checks failed:\n  " + "\n  ".join(res["problems"]))
    allowed = [re.sub(r"[^a-z0-9]", "", s.lower()) for s in (res["fonts"].get("sans"), res["fonts"].get("mono")) if s]
    allowed += [re.sub(r"[^a-z0-9]", "", s.lower()) for s in res["fonts"].get("resolved", [])]
    allowed += list(SYMBOL_FONTS)
    font_usage = c.helper("font-usage", files=[str(p) for p in c.stage.glob("*.pdf")])
    for name, (_n, kind, *_r) in BY_NAME.items():
        if kind != "pdf" or not (c.stage / name).is_file():
            continue
        used = {re.sub(r"[^a-z0-9]", "", f.split("+", 1)[-1].lower())
                for f in font_usage[str(c.stage / name)]}
        for font, emb in c.pdf_fonts(c.stage / name):
            base = re.sub(r"[^a-z0-9]", "", font.split("+", 1)[-1].lower())
            if base not in used:
                c.note(name, "", f"unused font resource {font} does not render text")
                continue
            if "NimbusSans" in font:
                c.warnings.append(f"{name}: embedded Nimbus Sans supplies fallback symbols")
            base = re.sub(r"[^a-z0-9]", "", font.split("+", 1)[-1].lower())
            if emb != "yes":
                raise Fail(1, f"{name}: font {font} is not embedded, so viewers would substitute it")
            if not any(base.startswith(a) for a in allowed):
                raise Fail(1, f"{name}: embeds font {font}, which is not the configured sans or mono or a libre symbol font")
    for name, e in c.entries.items():
        if BY_NAME[name][1] == "pdf":
            e["checks"].append("all fonts used by text are embedded and on the allowlist")
    for label in ("light", "dark"):
        p = res["palettes"].get(label, {})
        for name in (f"toy-diagram-{label}.png", f"palette-preview-{label}.png"):
            if name in c.entries:
                c.entries[name]["checks"].append(
                    f"{p.get('name')}: text {p.get('text_min')}:1 min, non-text {p.get('nontext_min')}:1 min, "
                    f"text on fill {p.get('text_on_fill_min')}:1 min")


def hygiene(c: Ctx, stage_dir: Path) -> None:
    for name, kind, _g, limit, width in ASSETS:
        f = stage_dir / name
        if not f.is_file():
            continue
        size = f.stat().st_size
        if size > limit:
            raise Fail(1, f"{name}: {size} bytes is over its budget of {limit}")
        if kind == "png":
            dropped = strip_png(f)
            if dropped:
                say(f"  {name}: dropped PNG chunks {','.join(dropped)}")
            w, h = png_size(f.read_bytes())
            if (width is not None and w != width) or (width is None and not 600 <= w <= 2400):
                raise Fail(1, f"{name}: width {w}px is outside the plan ({width or '600 to 2400'})")
        elif kind == "pdf":
            info = c.pdfinfo(f)
            bad = [k for k in PDF_META_KEYS if info.get(k)]
            bad += [m for m in pdf_raw_problems(f.read_bytes()) if m not in bad]
            if info.get("Metadata Stream", "no") != "no" and "XMP packet" not in bad:
                bad.append("XMP packet")
            if bad:
                say(f"  {name}: strip metadata ({', '.join(bad)})")
                c.helper("strip-pdf", files=[str(f)])
                info = c.pdfinfo(f)
                bad = [k for k in PDF_META_KEYS if info.get(k)] + pdf_raw_problems(f.read_bytes())
                if bad:
                    raise Fail(1, f"{name}: metadata remains after the strip: {', '.join(bad)}")
        else:
            text = f.read_text(encoding="utf-8")
            if LEAK.search(text) or ANSI.search(text):
                raise Fail(1, f"{name}: holds an absolute path or a terminal escape code")


def post_clean(c: Ctx) -> None:
    if not c.a.post_clean:
        return
    say("post-clean hook over every staged file")
    for f in sorted(c.stage.iterdir()):
        argv = [x.replace("{path}", str(f)) for x in shlex.split(c.a.post_clean)]
        if not any("{path}" in x for x in shlex.split(c.a.post_clean)):
            argv.append(str(f))
        c.run(argv, f"post-clean-{f.name}")


def total_budget(files: list[Path]) -> None:
    total = sum(f.stat().st_size for f in files)
    if total > TOTAL_MAX:
        raise Fail(1, f"docs/assets would hold {total} bytes, over the {TOTAL_MAX} budget")


# --------------------------------------------------------------------------- copy, alt text, manifest
def make_copy(c: Ctx) -> dict[str, dict]:
    d = c.facts.get("diagram") or {}
    names = {k: v.get("name", v.get("id")) for k, v in c.checks.get("palettes", {}).items()}
    if d.get("boxes") and d.get("stages") and d.get("edge_kinds"):
        shape = f"{d['boxes']} boxes in {d['stages']} stages, joined by {d['edge_kinds']} arrow styles"
    else:
        shape = "boxes grouped in stages and joined by arrows"
    tag = c.facts.get("export_tag")
    roles, kinds = c.checks.get("roles", 7), c.checks.get("edge_kinds", 4)
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}
    alt = {
        "toy-diagram-light.png": f"Mechanism diagram of an invented enzyme deficiency: {shape}. Light palette.",
        "toy-diagram-dark.png": f"Mechanism diagram of an invented enzyme deficiency: {shape}. Dark palette.",
        "toy-diagram-print.pdf": "One-page PDF of the synthetic mechanism diagram, light palette.",
        "toy-diagram-cvd.png": "The toy diagram under protanopia, deuteranopia, tritanopia and greyscale. Boxes keep their labels, arrows their styles.",
        "export-page.png": ("First page of a synthetic module export: questions, drafted answers and source tags such as "
                            + tag + ".") if tag else "First page of a synthetic module export: questions, drafted answers and source tags.",
        "sample-module-export.pdf": "Synthetic module export as a PDF, light palette.",
        "fill-review-page.png": "Synthetic fill review page: a fill result per answer, to-dos saying where to type, a warning on a disputed answer.",
        "sample-fill-review.pdf": "Synthetic fill review as a PDF, light palette.",
        "palette-preview-light.png": f"Palette preview, light default: {words.get(roles, roles)} role swatches with sample text, {words.get(kinds, kinds)} arrow styles, contrast table.",
        "palette-preview-dark.png": f"Palette preview, dark default: {words.get(roles, roles)} role swatches with sample text, {words.get(kinds, kinds)} arrow styles, contrast table.",
        "diagram-build.txt": "Text log of the diagram build checks on the synthetic toy case.",
        "export-run.txt": "Text log of the synthetic module export run.",
    }
    cap = {k: v.format(light_name=names.get("light", c.light), dark_name=names.get("dark", c.dark)) for k, v in CAPTION.items()}
    return {n: {"alt": alt[n], "caption": cap.get(n, "")} for n in alt}


def lint_alt(name: str, alt: str, problems: list[str], warnings: list[str]) -> None:
    if not alt.strip():
        problems.append(f"{name}: empty alt text")
        return
    if len(alt) > ALT_HARD:
        problems.append(f"{name}: alt text is {len(alt)} characters, over {ALT_HARD}")
    elif len(alt) > ALT_SOFT:
        warnings.append(f"{name}: alt text is {len(alt)} characters, over {ALT_SOFT}")
    if BAD_ALT_START.search(alt):
        warnings.append(f"{name}: alt text starts with a noise word; screen readers already say 'image'")
    m = COLOUR_WORDS.search(alt)
    if m:
        warnings.append(f"{name}: alt text names a colour ({m.group(0)}); say what the colour marks instead")
    if alt != alt.strip() or "\n" in alt or "\u2014" in alt or "\u2013" in alt:
        problems.append(f"{name}: alt text has stray whitespace or a dash character")


def tool_versions(c: Ctx) -> dict:
    out = {}
    for label, argv in (("pdftoppm", ["pdftoppm", "-v"]), ("rsvg-convert", ["rsvg-convert", "--version"])):
        try:
            p = subprocess.run(argv, encoding='utf-8', capture_output=True, text=True, timeout=30)
            first = (p.stdout + p.stderr).strip().splitlines()
            out[label] = re.sub(r"\s+", " ", first[0]) if first else ""
        except (OSError, subprocess.SubprocessError):
            out[label] = ""
    try:
        p = subprocess.run([*c.py, "-c", "import sys, PIL, reportlab; print(sys.version.split()[0], PIL.__version__, reportlab.Version)"],
                           encoding='utf-8', cwd=c.repo, capture_output=True, text=True, timeout=120)
        v = p.stdout.split()
        out.update(zip(("python", "pillow", "reportlab"), v))
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def build_manifest(c: Ctx, files: list[Path]) -> dict:
    copy = make_copy(c)
    old = {}
    mpath = c.out / "MANIFEST.json"
    if mpath.is_file():
        try:
            old = {e["file"]: e for e in json.loads(mpath.read_text(encoding="utf-8")).get("assets", [])}
        except (ValueError, KeyError):
            old = {}
    new = {}
    for f in files:
        name, kind = f.name, BY_NAME[f.name][1]
        e = {"file": name, "kind": kind, "bytes": f.stat().st_size, "sha256": sha256(f),
             "alt": copy[name]["alt"], "caption": copy[name]["caption"]}
        if kind == "png":
            e["width"], e["height"] = png_size(f.read_bytes())
        if kind == "pdf":
            e["pages"] = int(c.pdfinfo(f).get("Pages", "0"))
        e["command"] = c.entries.get(name, {}).get("command", [])
        e["checks"] = c.entries.get(name, {}).get("checks", [])
        new[name] = e
    merged = {n: new.get(n) or old.get(n) for n in (a[0] for a in ASSETS)}
    present = {n for n in merged if merged[n] and (c.out / n).is_file() or n in new}
    for n in list(merged):
        if n not in present:
            merged.pop(n)
    pal = {k: {"id": v["id"], "name": v["name"], "text_min": v["text_min"], "nontext_min": v["nontext_min"],
               "stroke_distance": v["stroke_distance"]} for k, v in c.checks.get("palettes", {}).items()}
    return {"schema": 1, "data": "synthetic", "generator": Path(__file__).name, "palettes": pal,
            "tool_versions": tool_versions(c), "assets": [merged[n] for n in (a[0] for a in ASSETS) if n in merged]}


def snippet(manifest: dict) -> str:
    """README markup with the exact alt text. Paste it into the README sections."""
    by = {e["file"]: e for e in manifest["assets"]}
    esc = lambda s: html.escape(s, quote=True)
    out = []
    if "toy-diagram-light.png" in by:
        e, d = by["toy-diagram-light.png"], by.get("toy-diagram-dark.png")
        out += ["<!-- hero: directly under the one-line purpose and the unofficial notice -->", "<p align=\"center\">", "<picture>"]
        if d:
            out.append(f'  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/{d["file"]}">')
        out += [f'  <img src="docs/assets/{e["file"]}" alt="{esc(e["alt"])}" width="880">', "</picture>", "</p>", "",
                f"<sub>{e['caption']}</sub>", ""]
    for name in ("fill-review-page.png", "export-page.png", "palette-preview-light.png", "toy-diagram-cvd.png"):
        if name in by:
            e = by[name]
            section = {"fill-review-page.png": "README: What it does", "export-page.png": "README: What it does",
                       "palette-preview-light.png": "docs/setup.md and README First run, step 2",
                       "toy-diagram-cvd.png": "README: Accessibility, or docs/diagrams.md"}[name]
            out += [f"<!-- {section} -->", f'<img src="docs/assets/{e["file"]}" alt="{esc(e["alt"])}" width="{440 if "page" in name else 640}">',
                    "", f"<sub>{e['caption']}</sub>", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- git, gate, publish
def git_status(repo: Path) -> set[str] | None:
    if shutil.which("git") is None or not (repo / ".git").exists():
        return None
    p = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], encoding='utf-8', capture_output=True, text=True)
    return set(p.stdout.splitlines()) if p.returncode == 0 else None


def run_gate(c: Ctx) -> None:
    if c.a.no_gate:
        c.warnings.append("privacy gate skipped (--no-gate); run it before any commit")
        return
    say("privacy gate over docs/assets")
    rel = os.path.relpath(c.out, c.repo)
    p = subprocess.run([sys.executable, "tools/privacy_gate.py", "--paths", rel, *shlex.split(c.a.gate_args or "")],
                       encoding='utf-8', cwd=c.repo, capture_output=True, text=True)
    if p.returncode != 0:
        raise Fail(1, f"privacy gate exit {p.returncode}:\n{tail(p.stdout + p.stderr, 30)}")


def publish(c: Ctx, manifest: dict) -> None:
    c.out.mkdir(parents=True, exist_ok=True)
    known = {a[0] for a in ASSETS} | {"MANIFEST.json"}
    stray = sorted(f.name for f in c.out.iterdir() if f.name not in known)
    if stray and not c.a.clean:
        raise Fail(1, f"{c.out} holds files the plan does not know: {', '.join(stray)}. Remove them or pass --clean.")
    backup = c.work / "backup"
    shutil.copytree(c.out, backup)
    try:
        for f in stray:
            (c.out / f).unlink()
        for f in sorted(c.stage.iterdir()):
            shutil.copyfile(f, c.out / f.name)
        (c.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        run_gate(c)
    except BaseException:
        shutil.rmtree(c.out)
        shutil.copytree(backup, c.out)
        raise


# --------------------------------------------------------------------------- README lint
IMG_MD = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML = re.compile(r"<img\b[^>]*>", re.I)
ATTR = re.compile(r"(\w+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def lint_docs(repo: Path, out: Path, manifest: dict) -> tuple[list[str], list[str]]:
    problems, warnings = [], []
    alts = {e["file"]: e["alt"] for e in manifest["assets"]}
    used: set[str] = set()
    docs = [repo / "README.md"] + sorted((repo / "docs").glob("**/*.md")) if (repo / "docs").is_dir() else [repo / "README.md"]
    for md in docs:
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8")
        lines = text.splitlines()
        refs = [(m.start(), m.group("src"), html.unescape(m.group("alt")), "markdown") for m in IMG_MD.finditer(text)]
        for m in IMG_HTML.finditer(text):
            attrs = {k.lower(): (a or b) for k, a, b in ATTR.findall(m.group(0))}
            refs.append((m.start(), attrs.get("src", ""), html.unescape(attrs.get("alt", "")) if "alt" in attrs else None, "html"))
        for m in re.finditer(r"<source\b[^>]*>", text, re.I):
            attrs = {k.lower(): (a or b) for k, a, b in ATTR.findall(m.group(0))}
            src = attrs.get("srcset", "").split()[0] if attrs.get("srcset") else ""
            if src:
                refs.append((m.start(), src, "", "source"))
        for pos, src, alt, kind in refs:
            target = (md.parent / src).resolve()
            try:
                rel = target.relative_to(out.resolve()).as_posix()
            except ValueError:
                continue
            where = f"{md.relative_to(repo)}:{text.count(chr(10), 0, pos) + 1}"
            used.add(rel)
            if rel not in alts:
                problems.append(f"{where}: {rel} is not in MANIFEST.json")
                continue
            if kind == "source":
                continue
            if alt is None or not alt.strip():
                problems.append(f"{where}: image {rel} has no alt text")
            elif alt != alts[rel] and kind != "source":
                problems.append(f"{where}: alt text of {rel} differs from MANIFEST.json (regenerate the snippet)")
            if alt:
                lint_alt(f"{where} {rel}", alt, problems, warnings)
            line = text.count("\n", 0, pos)
            window = " ".join(lines[max(0, line - 2): line + 8]).lower()
            if "synthetic" not in window and "invented" not in window:
                warnings.append(f"{where}: no caption near {rel} says the data is synthetic")
    for e in manifest["assets"]:
        if e["file"] not in used and e["kind"] in ("png",):
            warnings.append(f"{e['file']} is not referenced from README.md or docs/")
    return problems, warnings


def verify(a: argparse.Namespace) -> int:
    repo = find_repo(a.repo)
    out = Path(a.out).resolve() if a.out else repo / "docs" / "assets"
    mpath = out / "MANIFEST.json"
    if not mpath.is_file():
        raise Fail(1, f"{mpath} is missing; run build first")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    problems, warnings = [], []
    listed = {e["file"] for e in manifest["assets"]}
    for f in sorted(out.iterdir()):
        if f.name != "MANIFEST.json" and f.name not in listed:
            problems.append(f"{f.name}: in docs/assets but not in MANIFEST.json")
    total = 0
    for e in manifest["assets"]:
        f = out / e["file"]
        if not f.is_file():
            problems.append(f"{e['file']}: listed in MANIFEST.json but missing")
            continue
        total += f.stat().st_size
        spec = BY_NAME.get(e["file"])
        if spec is None:
            problems.append(f"{e['file']}: not an asset in the plan")
            continue
        if sha256(f) != e["sha256"]:
            problems.append(f"{e['file']}: sha256 differs from MANIFEST.json (edited by hand or rebuilt without the manifest)")
        if f.stat().st_size > spec[3]:
            problems.append(f"{e['file']}: {f.stat().st_size} bytes is over its budget of {spec[3]}")
        data = f.read_bytes()
        if spec[1] == "png":
            bad = [t.decode("latin-1") for t, _p, _n in png_chunks(data) if t not in PNG_KEEP]
            if bad:
                problems.append(f"{e['file']}: PNG chunks {','.join(bad)} can carry text or metadata")
        elif spec[1] == "pdf":
            bad = pdf_raw_problems(data)
            if bad:
                problems.append(f"{e['file']}: PDF metadata present ({', '.join(bad)})")
        else:
            txt = data.decode("utf-8", "replace")
            if LEAK.search(txt):
                problems.append(f"{e['file']}: holds an absolute path")
        lint_alt(e["file"], e["alt"], problems, warnings)
    if total > TOTAL_MAX:
        problems.append(f"docs/assets holds {total} bytes, over the {TOTAL_MAX} budget")
    p2, w2 = lint_docs(repo, out, manifest)
    problems += p2
    warnings += w2
    for w in warnings:
        say(f"WARN {w}")
    for p in problems:
        say(f"FAIL {p}")
    say(f"verify: {len(manifest['assets'])} asset(s), {total} bytes, {len(problems)} failure(s), {len(warnings)} warning(s)")
    return 1 if problems else 0


# --------------------------------------------------------------------------- build and plan
def build(a: argparse.Namespace) -> int:
    c = Ctx(a)
    c.module = (Path(a.module) if a.module else c.repo / EXAMPLES / "module.example.json").resolve()
    c.answers = (Path(a.answers) if a.answers else c.repo / EXAMPLES / "answers.example.json").resolve()
    c.fill_report = (Path(a.fill_report) if a.fill_report else c.repo / EXAMPLES / "fill-report.example.json").resolve()
    c.preflight()
    c.find_python()
    c.prepare()
    keep = a.keep_work
    try:
        before = git_status(c.repo)
        c.write_config()
        if not (c.light and c.dark) or not (a.light and a.dark):
            found = c.helper("discover", light=a.light or "", dark=a.dark or "")
            c.light, c.dark = found["light"], found["dark"]
        c.write_config()
        say(f"palettes: light {c.light}, dark {c.dark}; work folder {c.work}")
        for name, fn in (("diagram", step_diagram), ("cvd", step_cvd), ("export", step_export),
                         ("fill", step_fill), ("palette", step_palette)):
            if name in c.groups:
                fn(c)
        a11y_checks(c)
        pngs = sorted(c.stage.glob("*.png"))
        if pngs:
            c.helper("optimize", files=[str(p) for p in pngs])
        hygiene(c, c.stage)
        post_clean(c)
        hygiene(c, c.stage)
        staged = sorted(c.stage.iterdir())
        for f in staged:
            if f.name not in BY_NAME:
                raise Fail(1, f"internal: staged file {f.name} is not in the plan")
        total_budget(staged + [f for f in c.out.glob("*") if f.name in BY_NAME and f.name not in {s.name for s in staged}])
        manifest = build_manifest(c, staged)
        for e in manifest["assets"]:
            lint_alt(e["file"], e["alt"], c.problems, c.warnings)
        if c.problems:
            raise Fail(1, "alt text problems:\n  " + "\n  ".join(c.problems))
        publish(c, manifest)
        after = git_status(c.repo)
        if before is not None and after is not None:
            rel = os.path.relpath(c.out, c.repo)
            moved = sorted(x for x in after - before if rel not in x)
            if moved:
                c.warnings.append("the repo changed outside docs/assets during the run: " + "; ".join(moved[:8]))
        pw, ww = lint_docs(c.repo, c.out, manifest)
        c.warnings += ww
        if pw:
            c.warnings += [f"README/docs: {x}" for x in pw]
        (c.work / "README-proof-snippet.md").write_text(snippet(manifest) + "\n", encoding="utf-8")
        for w in c.warnings:
            say(f"WARN {w}")
        say("")
        say(f"built {len(staged)} file(s) into {c.out}")
        for e in manifest["assets"]:
            say(f"  {e['file']:<30} {e['bytes']:>8} bytes  {e['sha256'][:12]}")
        say("")
        say("README snippet (also in the work folder as README-proof-snippet.md):")
        say(snippet(manifest))
        say("Next: run the final-output cleaner over docs/assets, run the gate with the private layer, "
            "then `verify`. Then look at every image (checklist in proof-assets.md).")
        if a.keep_work:
            say(f"work folder kept: {c.work}")
        return 0
    except BaseException:
        keep = True
        say(f"work folder kept for inspection: {c.work}")
        raise
    finally:
        if not keep:
            shutil.rmtree(c.work, ignore_errors=True)
        if c.python_work is not None:
            shutil.rmtree(c.python_work, ignore_errors=True)


def plan(a: argparse.Namespace) -> int:
    repo = find_repo(a.repo) if (a.repo or Path.cwd()) else Path("$REPO")
    py = shlex.split(a.python) if a.python else ["uv", "run", "--python", "3.11", "--group", "ci", "python"]
    r, w = "$REPO", "$WORK"
    groups = pick_groups(a.only, a.skip)
    say("Common: cd $REPO; every command below runs with")
    say(f"  LT_SUITE_CONFIG=$WORK/proof-config.toml LT_SUITE_DATA=$WORK/data LT_SUITE_CACHE=$WORK/cache "
        f"LT_SUITE_NO_INSTALL=1 SOURCE_DATE_EPOCH={EPOCH} TZ=UTC PYTHONDONTWRITEBYTECODE=1")
    say("  proof-config.toml: [lt] origin = https://example.lt.app; [style] palettes = [<light>, <dark>], print_palette = <light>; [pdf] strip_metadata = true")
    say("  <light>, <dark>: default_print_palette() and the first dark palette in the catalog defaults (override: --light, --dark)")
    say("")
    show = lambda argv: say("  " + display(argv, r, w, py))
    if "diagram" in groups:
        say("diagram (toy-diagram-light.png, toy-diagram-dark.png, toy-diagram-print.pdf, diagram-build.txt)")
        say(f"  cp -R {TOY_CASE} $WORK/toy-case")
        show(cmd_diagram_build(py, r, f"{w}/toy-case"))
        show(cmd_diagram_access(py, r, f"{w}/toy-case"))
        show(cmd_raster(f"{w}/toy-case/deliver/<case>-<light>.pdf", f"{w}/stage/toy-diagram-light", 1, 1600))
        show(cmd_raster(f"{w}/toy-case/deliver/<case>-<dark>.pdf", f"{w}/stage/toy-diagram-dark", 1, 1600))
        say(f"  cp $WORK/toy-case/deliver/<case>-<light>.pdf $WORK/stage/toy-diagram-print.pdf")
    if "cvd" in groups:
        say("cvd (toy-diagram-cvd.png)")
        say("  Pillow joins $WORK/toy-case/checks/cvd/*-{protan,deutan,tritan,grey}.png into a 2 by 2 sheet, 800 px per cell, black labels on white")
    if "export" in groups:
        say("export (sample-module-export.pdf, export-page.png)")
        show(cmd_export(py, r, f"{w}/proof-config.toml", f"{w}/data", f"{EXAMPLES}/module.example.json",
                        f"{EXAMPLES}/answers.example.json", None, f"{w}/export-answers"))
        show(cmd_raster(f"{w}/export-answers/pdf/<title> (<light>, print).pdf", f"{w}/stage/export-page", 1, 1000))
    if "fill" in groups:
        say("fill review (sample-fill-review.pdf, fill-review-page.png, export-run.txt)")
        show(cmd_export(py, r, f"{w}/proof-config.toml", f"{w}/data", f"{EXAMPLES}/module.example.json",
                        f"{EXAMPLES}/answers.example.json", f"{EXAMPLES}/fill-report.example.json", f"{w}/export-fill"))
        show(cmd_raster(f"{w}/export-fill/pdf/<title> (<light>, print).pdf", f"{w}/stage/fill-review-page", "<page with the fill markers>", 1000))
    if "palette" in groups:
        say("palette preview (palette-preview-light.png, palette-preview-dark.png)")
        show(cmd_preview(py, r, f"{w}/preview"))
    say("")
    say("then: optimise PNGs (Pillow, lossless), drop non-structural PNG chunks, strip PDF metadata (ltcore.cleaners), "
        "optional --post-clean hook, write MANIFEST.json, copy to docs/assets, run tools/privacy_gate.py --paths docs/assets")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build and verify the README proof assets (synthetic data only).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 done; 1 step or check failed; 2 bad usage; 3 tool, file or interpreter missing.")
    ap.add_argument("command", nargs="?", default="build", choices=("build", "verify", "plan"))
    ap.add_argument("--repo", help="release repo root (default: current folder, else this script's parent folder)")
    ap.add_argument("--out", help="asset folder (default: <repo>/docs/assets)")
    ap.add_argument("--python", help="interpreter command for the release tools, for example 'uv run --python 3.11 --group ci python'")
    ap.add_argument("--only", help="comma list of groups to build: " + ", ".join(GROUPS))
    ap.add_argument("--skip", help="comma list of groups to leave out")
    ap.add_argument("--light", help="light (print) palette id; default: the catalog's print default")
    ap.add_argument("--dark", help="dark palette id; default: the first dark palette in the catalog defaults")
    ap.add_argument("--cvd", choices=("check", "report"), default="check",
                    help="check: a colour-vision collapse in a default palette fails the build (default); report: it is a warning")
    ap.add_argument("--module", help="module JSON (default: the schema example)")
    ap.add_argument("--answers", help="answers JSON (default: the schema example)")
    ap.add_argument("--fill-report", help="fill-report JSON (default: the schema example)")
    ap.add_argument("--diagram-sentinel", default="Fictional enzyme X deficiency",
                    help="text the toy diagram PDF must contain as selectable text")
    ap.add_argument("--post-clean", help="command run on every staged file, {path} is replaced (appended when absent)")
    ap.add_argument("--gate-args", help="extra arguments for tools/privacy_gate.py, for example '--require-private --shingles INDEX'")
    ap.add_argument("--no-gate", action="store_true", help="skip tools/privacy_gate.py (the build says so)")
    ap.add_argument("--clean", action="store_true", help="delete files in the asset folder that the plan does not know")
    ap.add_argument("--keep-work", action="store_true", help="keep the work folder after a good build")
    a = ap.parse_args(argv)
    try:
        return {"build": build, "verify": verify, "plan": plan}[a.command](a)
    except Fail as ex:
        print(f"make_proof_assets: FAIL ({ex.code}): {ex}", file=sys.stderr)
        return ex.code
    except KeyboardInterrupt:
        print("make_proof_assets: interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
