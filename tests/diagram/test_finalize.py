# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""finalize.py: its own guards (invisible characters only, PNG text chunks), the [[text_cleaners]]
hooks with stage "diagram" (an output that changes what a file shows is refused), and --final on the
worked example. The PDF metadata strip itself is ltcore.cleaners' and is tested with it (tests/style)."""
import io
import json
import shutil
import sys

import pytest

import finalize as F
from diagram_testkit import run_engine

ZWSP, NBSP = chr(0x200B), chr(0x00A0)          # chr(), so this file holds no invisible characters


def png(text_chunk=True):
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    im = Image.new("RGBA", (8, 6), (40, 90, 200, 255))
    info = PngInfo()
    if text_chunk:
        info.add_text("Software", "an image editor")
    buf = io.BytesIO()
    im.save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


class TestGuards:
    def test_only_invisibles(self):
        assert F.only_invisibles_removed(f"a{ZWSP}b".encode(), b"ab")
        assert F.only_invisibles_removed(f"5{NBSP}mg".encode(), f"5{NBSP}mg".encode())
        assert not F.only_invisibles_removed(f"5{NBSP}mg".encode(), b"5 mg")       # no-break space is kept
        assert not F.only_invisibles_removed(b"<!-- note -->\nx", b"x")
        assert not F.only_invisibles_removed(b"\xff\xfe\x00", b"\x00")             # not UTF-8

    def test_png_text_chunks_dropped_pixels_kept(self):
        raw = png()
        assert b"tEXt" in raw
        new = F.png_without_text(raw)
        assert new is not None and b"tEXt" not in new
        assert F.same_pixels(raw, new)
        assert F.png_without_text(png(text_chunk=False)) is None              # nothing to drop

    def test_structural_tags_come_from_the_shared_core(self):
        from ltcore import cleaners
        assert F.STRUCTURAL is cleaners.STRUCTURAL
        assert F.qpdf_settle is cleaners.qpdf_settle


@pytest.fixture
def hook_config(tmp_path):
    """A config with two diagram cleaners: one removes zero-width spaces, one rewrites visible text."""
    ok = tmp_path / "strip_zwsp.py"
    ok.write_text("import sys\nd = open(sys.argv[1], encoding='utf-8').read()\n"
                  "open(sys.argv[2], 'w', encoding='utf-8').write(d.replace(chr(0x200B), ''))\n", encoding="utf-8", newline="\n")
    bad = tmp_path / "rewrite.py"
    bad.write_text("import sys\nd = open(sys.argv[1], encoding='utf-8').read()\n"
                   "open(sys.argv[2], 'w', encoding='utf-8').write(d.replace('enzyme', 'ENZYME'))\n", encoding="utf-8", newline="\n")
    cfg = tmp_path / "lt-suite.toml"
    py = sys.executable.replace("\\", "/")
    cfg.write_text(
        "[[text_cleaners]]\nname = \"zwsp\"\nstage = \"diagram\"\n"
        f"argv = [\"{py}\", \"{ok.as_posix()}\", \"{{in}}\", \"{{out}}\"]\n\n"
        "[[text_cleaners]]\nname = \"rewrite\"\nstage = \"diagram\"\n"
        f"argv = [\"{py}\", \"{bad.as_posix()}\", \"{{in}}\", \"{{out}}\"]\n\n"
        "[[text_cleaners]]\nname = \"other-stage\"\nstage = \"pdf\"\nargv = [\"false\", \"{pdf}\"]\n", encoding="utf-8", newline="\n")
    return cfg


def test_hooks_keep_only_invisible_changes(tmp_path, hook_config):
    import style
    style.configure(str(hook_config))
    try:
        f = tmp_path / "page.html"
        f.write_text(f"<p>enzyme{ZWSP} X</p>", encoding="utf-8", newline="\n")
        data, notes, problems = F.run_hooks(f, f.read_bytes(), dry=False)
    finally:
        style.configure(None)
    assert data == "<p>enzyme X</p>".encode()
    assert notes == ["cleaner zwsp"]
    assert len(problems) == 1 and "rewrite" in problems[0] and "was not kept" in problems[0]


def test_final_build_of_the_worked_example(toy_copy, engine_env):
    if not (shutil.which("qpdf") or shutil.which("exiftool")):
        pytest.importorskip("pikepdf", reason="the PDF metadata pass needs pikepdf, qpdf or exiftool")
    r = run_engine("build.py", toy_copy, "--trust-case", "--final", cwd=toy_copy.parent, env=engine_env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    report = json.loads((toy_copy / "checks" / "final-report.json").read_text(encoding="utf-8"))
    assert report
    text = json.dumps(report)
    assert "error" not in [x.get("status") for x in report if isinstance(x, dict)], text[:2000]
    for pdf in (toy_copy / "deliver").glob("*.pdf"):
        data = pdf.read_bytes()
        assert b"/Producer" not in data and b"<x:xmpmeta" not in data, pdf.name
    # a second pass finds nothing left to clean: a cleaned PDF is a function of its draft
    again = run_engine("finalize.py", toy_copy / "deliver", cwd=toy_copy.parent, env=engine_env)
    assert again.returncode == 0, again.stdout[-2000:]
    assert "cleaned " not in again.stdout.replace("already-clean", "")
