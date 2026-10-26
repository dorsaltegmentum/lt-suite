# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check rendered citation wrapping while retaining strict checks for omitted content."""
from __future__ import annotations

import json

import pytest


URL = ("https://www.example.gov.au/news/safety-updates/potential-risk-"
       "neurodevelopmental-disorders-children-born-men-taking-sodium-valproate")
BODY = (f"See the safety notice xxx ({URL}) and the second citation (https://example.invalid/source). "
        "A low-risk review needs complete evidence-based wording.")
PALETTES = ["catppuccin-latte", "catppuccin-frappe", "everforest-dark-medium", "tol-bright"]


@pytest.fixture
def citation_page(tmp_path, make_config, need_fonts):
    from ltcore import config

    page = tmp_path / "page.md"
    page.write_text(
        "title:: Lt/Citation wrapping\ntype:: **Lt Module Output**\nlt-module:: c1-r101\n\n"
        "- # Safety notice\n\t- ## Review the citations\n\t  " + BODY + "\n",
        encoding="utf-8", newline="\n")
    cfg = config.load(make_config(style={"palettes": PALETTES, "print_palette": PALETTES[0]}))
    return page, cfg


def test_pdf_qc_accepts_citation_url_wrapped_at_hyphen(citation_page, tmp_path):
    import module_pdf
    import module_pdf_qc
    import pypdfium2 as pdfium

    page, cfg = citation_page
    out = tmp_path / "pdf"
    assert module_pdf.build_pdfs(page, out, cfg, final=False) == 0
    report = json.loads((out / ".qc.json").read_text(encoding="utf-8"))
    assert set(report) == set(PALETTES)
    assert all(not row["missing"] and not row["bad"] for row in report.values())
    for path in out.glob("*.pdf"):
        with pdfium.PdfDocument(path) as pdf:
            raw = pdf[0].get_textpage().get_text_bounded()
            assert ("neurodevelopmental" + chr(2) + "disorders" in raw
                    or "neurodevelopmental-\r\ndisorders" in raw)
            assert URL in module_pdf_qc.squash(module_pdf_qc.page_texts(pdf)[0][0])


@pytest.mark.parametrize("wanted,replacement", [
    pytest.param("low-risk ", "", id="hyphenated-word"),
    pytest.param("complete ", "", id="missing-word"),
    pytest.param("evidence-based", "evidencebased", id="inline-hyphen"),
    pytest.param("neurodevelopmental-disorders", "neurodevelopmentaldisorders", id="wrapped-hyphen"),
])
def test_pdf_qc_rejects_renderer_omissions(
        citation_page, tmp_path, monkeypatch, wanted, replacement):
    import module_pdf

    page, cfg = citation_page
    render = module_pdf.render_page

    def omit_content(source, out, cfg):
        altered = tmp_path / "altered.md"
        altered.write_text(source.read_text(encoding="utf-8").replace(wanted, replacement),
                           encoding="utf-8", newline="\n")
        return render(altered, out, cfg)

    monkeypatch.setattr(module_pdf, "render_page", omit_content)
    out = tmp_path / "pdf"
    assert module_pdf.build_pdfs(page, out, cfg, final=False) == 1
    report = json.loads((out / ".qc.json").read_text(encoding="utf-8"))
    assert set(report) == set(PALETTES)
    assert all(row["missing"] == [["body", BODY[:90]]] for row in report.values())
