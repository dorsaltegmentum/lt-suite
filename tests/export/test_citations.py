# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check citation precedence and recording page links across exported notes and PDFs."""
from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize("enabled,entry,expected", [
    (False, {"kind": "transcript", "path": "/recordings/Recording.vtt"}, "(**SOURCE**)"),
    (True, {"kind": "transcript", "path": "/recordings/Recording.vtt"}, "([[Recording]])"),
    (True, {"kind": "transcript", "path": r"C:\recordings\Recording.vtt"}, "([[Recording]])"),
    (True, {"kind": "transcript", "path": "/recordings/a.b.vtt"}, "([[a.b]])"),
    (True, {"kind": "transcript", "path": "Recording"}, "([[Recording]])"),
    (True, {"kind": "transcript"}, "(**SOURCE**)"),
    (True, {"kind": "transcript", "path": None}, "(**SOURCE**)"),
    (True, {"kind": "transcript", "path": ""}, "(**SOURCE**)"),
    (True, {"kind": "transcript", "path": "/recordings/"}, "(**SOURCE**)"),
    (True, {"kind": "transcript", "path": "/recordings/   .vtt"}, "(**SOURCE**)"),
    (True, {"kind": "transcript", "path": "/recordings/.vtt"}, "(**SOURCE**)"),
    (True, {"kind": "slides", "path": "/recordings/Recording.vtt"}, "(**SOURCE**)"),
])
def test_transcript_filename_citations(export_module, make_config, enabled, entry, expected):
    from ltcore import config
    cfg = config.load(make_config(export__citations={"transcript_page_from_filename": enabled}))
    assert export_module.render_citation({"source_id": "SOURCE"}, {"SOURCE": entry}, set(), cfg) == expected


def test_transcript_filename_citations_default_off(export_module, make_config):
    from ltcore import config
    cfg = config.load(make_config())
    assert cfg.get("export.citations.transcript_page_from_filename") is False
    entry = {"kind": "transcript", "path": "/recordings/Recording.vtt"}
    assert export_module.render_citation({"source_id": "SOURCE"}, {"SOURCE": entry}, set(), cfg) == "(**SOURCE**)"


@pytest.mark.parametrize("aliases,self_ids,source_id,expected", [
    ({"page_aliases": {"SOURCE": "Alias"}, "kind_pages": {"transcript": "Kind"}}, set(), "SOURCE", ("ref", "Alias")),
    ({"kind_pages": {"transcript": "Kind"}}, set(), "SOURCE", ("ref", "Kind")),
    ({"url_aliases": {"SOURCE": "https://example.invalid/source"}}, set(), "SOURCE", ("link", "https://example.invalid/source")),
    ({}, {"SOURCE"}, "SOURCE", ("plain", "this module")),
    ({}, set(), "PMID:12345", ("link", "https://pubmed.ncbi.nlm.nih.gov/12345/")),
    ({}, set(), "10.1234/demo", ("link", "https://doi.org/10.1234/demo")),
])
def test_transcript_filename_citation_precedence(
        export_module, make_config, aliases, self_ids, source_id, expected):
    from ltcore import config
    cfg = config.load(make_config(export__citations={"transcript_page_from_filename": True, **aliases}))
    entry = {"kind": "transcript", "path": "/recordings/Recording.vtt"}
    assert export_module.source_display(source_id, {source_id: entry}, self_ids, cfg) == expected


@pytest.mark.parametrize("fmt", ["logseq", "obsidian", "markdown"])
def test_transcript_filename_export_notes_and_pdf_qc(
        run, fixtures, make_config, tmp_path, need_fonts, fmt):
    import pypdfium2 as pdfium

    graph = tmp_path / "graph"
    cfg = make_config(
        notes={"graph_path": str(graph), "format": fmt},
        export__outputs={"pdf": True, "notes_graph": True},
        export__citations={"transcript_page_from_filename": True})
    rc, out, err = run(
        "--config", cfg, "--module", fixtures / "modules/module-c1-r101.json",
        "--answers", fixtures / "answers/answers-c1-r101.json",
        "--media-dir", fixtures / "media", "--out-dir", tmp_path / "out", "--install", "--json")
    assert rc == 0, err
    report = json.loads(out)
    assert report["pdf"]["passed"]
    page = (tmp_path / "out/module-page.md").read_text(encoding="utf-8")
    assert "([[transcript]], 00:05:40)" in page
    installed = next((graph / "pages").rglob("*.md")).read_text(encoding="utf-8")
    expected = "(transcript, 00:05:40)" if fmt == "markdown" else "([[transcript]], 00:05:40)"
    assert expected in installed
    qc = json.loads((tmp_path / "out/pdf/.qc.json").read_text(encoding="utf-8"))
    assert qc
    for row in qc.values():
        assert not row["bad"] and not row["missing"]
    for path in (tmp_path / "out/pdf").glob("*.pdf"):
        with pdfium.PdfDocument(path) as pdf:
            text = "".join(page.get_textpage().get_text_range() for page in pdf)
        assert "transcript" in text and "[[" not in text


def test_table_export_cites_segments_once(run, fixtures, make_config, tmp_path, need_fonts):
    import pypdfium2 as pdfium

    cfg = make_config(export__outputs={"pdf": True, "flashcard_source": True})
    rc, out, err = run(
        "--config", cfg, "--module", fixtures / "modules/module-c1-r106.json",
        "--answers", fixtures / "answers/answers-c1-r106.json",
        "--out-dir", tmp_path / "out", "--json")
    assert rc == 0, err
    assert json.loads(out)["pdf"]["passed"]
    answers = json.loads((fixtures / "answers/answers-c1-r106.json").read_text(encoding="utf-8"))
    expected = sum(len(segment.get("provenance_refs", []))
                   for answer in answers["items"] for segment in answer.get("segments", []))
    for name in ("module-page.md", "flashcard-source.md"):
        text = (tmp_path / "out" / name).read_text(encoding="utf-8")
        assert text.count("](https://doi.org/10.5555/fixture.0001)") == expected
    for path in (tmp_path / "out/pdf").glob("*.pdf"):
        with pdfium.PdfDocument(path) as pdf:
            text = "".join(page.get_textpage().get_text_range() for page in pdf)
        assert text.count("10.5555/fixture.0001") == expected


@pytest.mark.parametrize("locator,expected", [
    ("/course root/Clinical/Management.pdf;  PDF p. 7; figure 2 ",
     "Management.pdf;  PDF p. 7; figure 2 "),
    (r"C:\course\Clinical\Management.pdf; PDF p. 7", "Management.pdf; PDF p. 7"),
    ("C:/course/Clinical/Management.pdf; PDF p. 7", "Management.pdf; PDF p. 7"),
    ("https://example.invalid/Clinical/Management.pdf; PDF p. 7",
     "https://example.invalid/Clinical/Management.pdf; PDF p. 7"),
    ("Clinical/Management.pdf; PDF p. 7", "Clinical/Management.pdf; PDF p. 7"),
    (r"C:Clinical\Management.pdf; PDF p. 7", r"C:Clinical\Management.pdf; PDF p. 7"),
    ("/course root/Clinical/Management.pdf", "Management.pdf"),
    (r"C:\course\Clinical\Management.pdf", "Management.pdf"),
], ids=["posix", "windows", "windows-forward", "url", "relative", "drive-relative",
        "posix-without-place", "windows-without-place"])
def test_citation_displays_local_filename(export_module, make_config, locator, expected):
    from ltcore import config
    cfg = config.load(make_config())
    provenance = {"source_id": "SOURCE", "locator": locator}
    assert export_module.render_citation(provenance, {}, set(), cfg) == f"(**SOURCE**, {expected})"
    assert provenance["locator"] == locator


def test_export_outputs_omit_absolute_locator_paths(
        run, fixtures, make_config, tmp_path, need_fonts):
    import pypdfium2 as pdfium

    answers = json.loads((fixtures / "answers/answers-c1-r101.json").read_text(encoding="utf-8"))
    answers["source_module"]["path"] = str(fixtures / "modules/module-c1-r101.json")
    paths = ["/course root/Clinical/Management.pdf", r"C:\course\Clinical\Treatment.pdf"]
    for index, answer in enumerate(answers["items"]):
        for provenance in answer.get("provenance", []):
            provenance["locator"] = paths[index % len(paths)] + "; PDF p. 7"
    path = tmp_path / "answers.json"
    original = json.dumps(answers).encode("utf-8")
    path.write_bytes(original)
    cfg = make_config(export__outputs={"pdf": True, "flashcard_source": True, "ilo_map": True})
    rc, out, err = run(
        "--config", cfg, "--module", fixtures / "modules/module-c1-r101.json",
        "--answers", path, "--media-dir", fixtures / "media", "--out-dir", tmp_path / "out", "--json")
    assert rc == 0, err
    assert json.loads(out)["pdf"]["passed"]
    assert path.read_bytes() == original
    exported = list((tmp_path / "out").rglob("*"))
    assert (tmp_path / "out/ilo-map.md").is_file()
    pdfs = [p for p in exported if p.suffix == ".pdf"]
    assert len(pdfs) == 2
    for output in exported:
        if not output.is_file():
            continue
        for absolute in paths:
            assert absolute.encode() not in output.read_bytes(), output
            assert json.dumps(absolute)[1:-1].encode() not in output.read_bytes(), output
    for output in [tmp_path / "out/module-page.md", tmp_path / "out/flashcard-source.md", *pdfs]:
        if output.suffix == ".pdf":
            with pdfium.PdfDocument(output) as pdf:
                text = "".join(page.get_textpage().get_text_range() for page in pdf)
        else:
            text = output.read_text(encoding="utf-8")
        assert "Management.pdf; PDF p. 7" in text
        assert "Treatment.pdf; PDF p. 7" in text
        assert all(absolute not in text for absolute in paths), output
