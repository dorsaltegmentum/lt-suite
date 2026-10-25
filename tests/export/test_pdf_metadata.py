# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise PDF rendering, configurable stamps, metadata stripping and visual checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def pdf_modules(need_fonts):
    import module_pdf
    import pdf_base
    return module_pdf, pdf_base


def metadata(path):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(path) as doc:
        return doc.get_metadata_dict()


def test_template_author_defaults_to_empty(pdf_modules, make_config, tmp_path):
    from ltcore import config
    from reportlab.platypus import Paragraph
    _, base = pdf_modules
    cfg = config.load(make_config())
    base.register_fonts(cfg=cfg)
    path = tmp_path / "template.pdf"
    base.Doc(str(path), base.LIGHT, "Template").build([
        Paragraph("One fictional mechanism.", base.styles(base.LIGHT)["item"])])
    assert metadata(path)["Author"] == ""


def test_every_pdf_text_colour_reaches_45_contrast(pdf_modules, make_config):
    from ltcore import config
    _, base = pdf_modules
    cfg = config.load(make_config(style={"palettes": ["tol-medium-contrast", "catppuccin-frappe"],
                                         "print_palette": "tol-medium-contrast"}))
    for _, _, theme in base.pdf_themes(cfg)[0]:
        for colour in ("text", "sub", "over", "blue", "mauve", "peach", "green", "yellow", "red"):
            for ground in ("base", "mantle"):
                assert base.contrast(theme[colour], theme[ground]) >= 4.5, (colour, ground, theme)


def test_fixture_page_smoke_cleans_metadata_and_preserves_render(
        pdf_modules, page_dir, make_config, tmp_path):
    from ltcore import config
    render, _ = pdf_modules
    cfg = config.load(make_config())
    out = tmp_path / "pdf"
    assert render.build_pdfs(page_dir / "page.md", out, cfg) == 0
    built = json.loads((out / ".build.json").read_text(encoding="utf-8"))
    assert built
    qc = json.loads((out / ".qc.json").read_text(encoding="utf-8"))
    for name, row in built.items():
        path = Path(row["path"])
        assert row["pages"] == qc[name]["pages"] == 1
        assert not qc[name]["bad"] and not qc[name]["missing"]
        assert metadata(path).get("Author", "") == ""
        assert metadata(path).get("Subject", "") == ""
        assert b"/Metadata" not in path.read_bytes()
    assert all(row["status"] in {"cleaned", "unchanged"}
               for row in json.loads((out / ".final.json").read_text(encoding="utf-8")))


def test_custom_namespace_and_stamp_reach_qc(pdf_modules, page_dir, make_config, tmp_path):
    from ltcore import config
    render, _ = pdf_modules
    page = page_dir / "page.md"
    page.write_text(page.read_text(encoding="utf-8").replace("Lt/", "Study/").replace("lt-module::", "worked-module::"), encoding="utf-8", newline="\n")
    cfg = config.load(make_config(notes={"page_namespace": "Study", "stamp_property": "worked-module"},
                                  pdf={"author": "Synthetic author"}))
    out = tmp_path / "draft"
    assert render.build_pdfs(page, out, cfg, final=False) == 0
    for path in out.glob("*.pdf"):
        info = metadata(path)
        assert info["Author"] == "Synthetic author"
        assert info["Title"].startswith("1.1 Demo")
        assert "c1-r101" in info["Subject"]


def test_final_pass_removes_seeded_info_and_xmp(pdf_modules, make_config, tmp_path):
    from ltcore import config
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.pdfbase.pdfdoc import PDFDictionary, PDFName, PDFStream
    render, _ = pdf_modules
    path = tmp_path / "seeded.pdf"
    canvas = Canvas(str(path))
    canvas.setAuthor("Synthetic author")
    packet = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><synthetic>private marker</synthetic></x:xmpmeta>'
    stream = PDFStream(PDFDictionary({"Type": PDFName("Metadata"), "Subtype": PDFName("XML")}), packet)
    canvas._doc.Catalog.Metadata = canvas._doc.Reference(stream)
    canvas.drawString(72, 720, "Fictional pathway")
    canvas.showPage()
    canvas.save()
    assert b"/Metadata" in path.read_bytes()
    before = render._pages_and_renders(path.read_bytes())
    cfg = config.load(make_config())
    assert render.finalise({"demo": {"path": str(path)}}, tmp_path, cfg)
    assert metadata(path).get("Author", "") == ""
    assert b"/Metadata" not in path.read_bytes()
    assert b"private marker" not in path.read_bytes()
    assert render._same_render(before, render._pages_and_renders(path.read_bytes()))


@pytest.mark.parametrize("change", ["pixels", "pages"])
def test_final_pass_rejects_changed_pdf(pdf_modules, make_config, tmp_path, monkeypatch, change):
    from ltcore import cleaners, config
    from reportlab.pdfgen.canvas import Canvas
    render, _ = pdf_modules
    path = tmp_path / "changed.pdf"

    def write(text, extra=False):
        canvas = Canvas(str(path))
        canvas.drawString(72, 720, text)
        canvas.showPage()
        if extra:
            canvas.drawString(72, 720, "Extra page")
            canvas.showPage()
        canvas.save()

    write("Original content")

    def changed(*args, **kwargs):
        write("Changed content" if change == "pixels" else "Original content", change == "pages")
        return [cleaners.Result("synthetic", "pdf", str(path), "cleaned")]

    monkeypatch.setattr(cleaners, "finalize", changed)
    cfg = config.load(make_config())
    assert not render.finalise({"demo": {"path": str(path)}}, tmp_path, cfg)
    assert any(row["step"] == "verify" for row in json.loads((tmp_path / ".final.json").read_text(encoding="utf-8")))


def test_pdf_output_cannot_write_into_course_or_graph(pdf_modules, page_dir, make_config, tmp_path):
    from ltcore import config
    render, _ = pdf_modules
    for key in ("curriculum", "notes"):
        root = tmp_path / key
        cfg = config.load(make_config(**{key: {"root" if key == "curriculum" else "graph_path": str(root)}}))
        assert render.build_pdfs(page_dir / "page.md", root / "pdf", cfg) == 1
        assert not root.exists()


def test_tables_provenance_and_todos_survive_pdf_qc(pdf_modules, page_dir, make_config, tmp_path):
    from ltcore import config
    render, _ = pdf_modules
    page = page_dir / "page.md"
    page.write_text((Path(__file__).parent / "data" / "page.md").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    cfg = config.load(make_config())
    assert render.build_pdfs(page, tmp_path / "tables", cfg, final=False) == 0


def test_pdf_hooks_run_in_order_and_other_stages_are_ignored(pdf_modules, make_config, tmp_path):
    from ltcore import config
    from reportlab.pdfgen.canvas import Canvas
    render, _ = pdf_modules
    path = tmp_path / "hooks.pdf"
    canvas = Canvas(str(path))
    canvas.drawString(72, 720, "Stable content")
    canvas.showPage()
    canvas.save()
    marker = tmp_path / "order.txt"
    cfg_path = make_config()
    with cfg_path.open("a", encoding="utf-8", newline="\n") as stream:
        for name, stage in (("first", "pdf"), ("ignored", "diagram"), ("last", "pdf")):
            code = ("from pathlib import Path; import sys; "
                    "p=Path(sys.argv[2]); p.write_text((p.read_text(encoding='utf-8') if p.exists() else '')+sys.argv[3], encoding='utf-8', newline='\\n')")
            argv = [sys.executable, "-c", code, "{pdf}" if stage == "pdf" else "{in}", str(marker), name]
            if stage == "diagram":
                argv.append("{out}")
            stream.write("\n[[text_cleaners]]\nname = " + json.dumps(name)
                         + "\nstage = " + json.dumps(stage)
                         + "\nargv = " + json.dumps(argv) + "\ntimeout_s = 20\n")
    cfg = config.load(cfg_path)
    assert render.finalise({"demo": {"path": str(path)}}, tmp_path, cfg)
    assert marker.read_text(encoding="utf-8") == "firstlast"
    rows = json.loads((tmp_path / ".final.json").read_text(encoding="utf-8"))
    assert [row["step"] for row in rows][:2] == ["first", "last"]


def test_full_schema_fill_review_passes_continuation_qc(run, make_config, tmp_path, need_fonts):
    examples = Path(__file__).resolve().parents[1] / "fixtures" / "examples"
    cfg = make_config(export__outputs={"pdf": True})
    rc, out, err = run("--config", cfg, "--week", "1.1", "--module", examples / "module.example.json",
                       "--answers", examples / "answers.example.json",
                       "--fill-report", examples / "fill-report.example.json",
                       "--out", tmp_path / "full-review", "--json")
    assert rc == 0, err
    result = json.loads(out)
    assert result["pdf"]["passed"]
