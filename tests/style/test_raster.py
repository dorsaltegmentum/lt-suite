# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.raster: font discovery in an SVG, static-instance substitution, the
rsvg-convert argv, and a real conversion when rsvg-convert is installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ltcore import raster as R

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="320" height="90" viewBox="0 0 320 90">
<rect width="320" height="90" fill="#ffffff"/>
<text x="10" y="30" font-family="Noto Sans" font-size="20" font-weight="700">Bold “µ°” IL-1β</text>
<text x="10" y="60" font-family="Noto Sans" font-size="16">Regular Ca²⁺</text>
<text x="10" y="84" style="font-family: 'Noto Sans Mono', monospace; font-size: 14px">mono 0O Il1</text>
</svg>
"""


def test_families_and_weights_are_found():
    assert R._families(SVG) == ["Noto Sans", "'Noto Sans Mono', monospace"]
    assert R._weights(SVG) == [400, 700]
    assert R._families('<text font-family="sans-serif">x</text>') == []


def test_prepare_substitutes_static_family(tmp_path):
    pytest.importorskip("fontTools")
    prep = R.prepare(SVG, cache_dir=tmp_path, use_system=False)
    assert prep.families == {"Noto Sans": "NotoSansStatic", "Noto Sans Mono": "NotoSansMonoStatic"}
    assert 'font-family="NotoSansStatic"' in prep.svg
    assert "font-family:NotoSansMonoStatic" in prep.svg
    conf = Path(prep.env["FONTCONFIG_FILE"])
    assert conf.is_file() and "NotoSans" not in conf.name
    cut = sorted(p.name for p in tmp_path.rglob("*.ttf"))
    assert "NotoSansStatic-Bold.ttf" in cut and "NotoSansStatic-Regular.ttf" in cut
    assert prep.warnings == []


def test_prepare_warns_for_an_unknown_family(tmp_path):
    prep = R.prepare('<text font-family="No Such Family">x</text>', cache_dir=tmp_path, use_system=False)
    assert "No Such Family" not in prep.svg
    assert "NotoSansStatic" in prep.svg
    assert prep.warnings and "not found" in prep.warnings[0]


def test_convert_builds_argv_and_keeps_source(tmp_path, monkeypatch):
    src = tmp_path / "in.svg"
    src.write_text(SVG, encoding="utf-8")
    monkeypatch.setattr(R.shutil, "which", lambda name: "/usr/bin/rsvg-convert")
    seen = {}

    def runner(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        out = Path(argv[argv.index("-o") + 1])
        out.write_bytes(b"\x89PNG fake")
        assert Path(argv[-1]).read_text(encoding="utf-8") == SVG   # no font preparation asked for
        return subprocess.CompletedProcess(argv, 0, "", "")

    out, warnings = R.convert(src, tmp_path / "out" / "x.png", width=600, zoom=None, cache_dir=tmp_path / "c",
                              prepare_fonts=False, runner=runner)
    assert out.read_bytes() == b"\x89PNG fake"
    assert seen["argv"][:5] == ["/usr/bin/rsvg-convert", "-f", "png", "-w", "600"]
    assert "-o" in seen["argv"] and not any(a.startswith("-z") for a in seen["argv"])
    assert src.read_text(encoding="utf-8") == SVG


def test_convert_renders_a_prepared_copy_and_keeps_source(tmp_path, monkeypatch):
    pytest.importorskip("fontTools")
    src = tmp_path / "in.svg"
    src.write_text(SVG, encoding="utf-8")
    monkeypatch.setattr(R.shutil, "which", lambda name: "/usr/bin/rsvg-convert")
    seen = {}

    def runner(argv, **kw):
        seen["svg"] = Path(argv[-1]).read_text(encoding="utf-8")
        seen["env"] = kw["env"]
        Path(argv[argv.index("-o") + 1]).write_bytes(b"%PDF-1.7 fake")
        return subprocess.CompletedProcess(argv, 0, "", "")

    out = R.svg_to_pdf(src, tmp_path / "x.pdf", cache_dir=tmp_path / "c", runner=runner)
    assert out.read_bytes().startswith(b"%PDF-")
    assert 'font-family="NotoSansStatic"' in seen["svg"]
    assert Path(seen["env"]["FONTCONFIG_FILE"]).is_file()
    assert src.read_text(encoding="utf-8") == SVG


def test_convert_failure_and_missing_binary(tmp_path, monkeypatch):
    src = tmp_path / "in.svg"
    src.write_text(SVG, encoding="utf-8")
    monkeypatch.setattr(R.shutil, "which", lambda name: None)
    with pytest.raises(R.RasterError, match="rsvg-convert not found"):
        R.convert(src, tmp_path / "x.png", prepare_fonts=False)
    monkeypatch.setattr(R.shutil, "which", lambda name: "/usr/bin/rsvg-convert")
    fail = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "bad svg")  # noqa: E731
    with pytest.raises(R.RasterError, match="bad svg"):
        R.convert(src, tmp_path / "x.png", prepare_fonts=False, runner=fail)
    with pytest.raises(R.RasterError, match="unknown output format"):
        R.convert(src, tmp_path / "x.gif", prepare_fonts=False)


@pytest.mark.requires_bin("rsvg-convert")
def test_real_conversion_png_and_pdf(tmp_path):
    pytest.importorskip("fontTools")
    src = tmp_path / "t.svg"
    src.write_text(SVG, encoding="utf-8")
    res = R.render(src, tmp_path / "out", formats=("png", "pdf"), png_zoom=2, cache_dir=tmp_path / "cache")
    assert res["png"].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert res["pdf"].read_bytes()[:5] == b"%PDF-"
    if shutil.which("pdffonts"):
        listing = subprocess.run(["pdffonts", str(res["pdf"])], capture_output=True, text=True).stdout
        assert "NotoSansStatic-Bold" in listing, listing
        assert "NotoSansStatic-Regular" in listing, listing


def test_prepare_tries_the_whole_stack_then_noto(tmp_path,monkeypatch,font_file):
    from ltcore import fonts as F
    source='<text font-family="\'Missing A\', \'Noto Sans\'">x</text>'
    result=R.prepare(source,cache_dir=tmp_path,use_system=False)
    assert 'NotoSansStatic' in result.svg and 'Missing A' not in result.svg
    result=R.prepare('<text font-family="Missing A, Missing B">x</text>',cache_dir=tmp_path,use_system=False)
    assert 'NotoSansStatic' in result.svg and any('drawn in Noto Sans' in w for w in result.warnings)
    path=font_file('Test Static')
    monkeypatch.setattr(F,'fc_match',lambda fam,*a,**k:path if fam=='Test Static' else None)
    result=R.prepare('<text font-family="Test Static">x</text>',cache_dir=tmp_path)
    assert result.svg=='<text font-family="Test Static">x</text>'


@pytest.mark.parametrize('variable_italic', [True, False])
def test_prepare_preserves_family_when_an_additional_face_has_rfn(
        tmp_path, monkeypatch, font_file, variable_italic):
    """A modifiable regular face must not permit cutting an RFN italic face."""
    from fontTools.ttLib import TTFont
    from ltcore import fonts as F
    family = 'Test Mixed Licence'
    regular = font_file(family, variable=True)
    italic = font_file(family, variable=variable_italic,
                       copyright='with Reserved Font Name "Test Mixed Licence"')
    with TTFont(italic) as face:
        face['OS/2'].fsSelection |= 1
        face['head'].macStyle |= 2
        face.save(italic)
    assert F.licence_class(regular) == 'modify'
    assert F.licence_class(italic) == 'whole'
    entry = {'id': 'test-mixed-licence', 'family': family, 'role': 'sans'}
    original_entry, original_files = F.find_entry, F.bundled_files
    monkeypatch.setattr(F, 'find_entry', lambda name: entry if name == family else original_entry(name))
    monkeypatch.setattr(F, 'bundled_files', lambda e: [regular, italic] if e['id'] == entry['id'] else original_files(e))
    original_bytes = [regular.read_bytes(), italic.read_bytes()]
    svg = f'<text font-family="{family}" font-style="italic">Test</text>'
    cache = tmp_path / 'cache'
    result = R.prepare(svg, cache_dir=cache, use_system=False)
    assert not list(cache.rglob('*.ttf')), 'no family member may be cut after another face forbids modification'
    assert result.svg == svg and not result.families
    assert any('weight' in warning and family in warning for warning in result.warnings)
    conf = Path(result.env['FONTCONFIG_FILE']).read_text()
    assert str(regular.parent.resolve()) in conf and str(italic.parent.resolve()) in conf
    assert [regular.read_bytes(), italic.read_bytes()] == original_bytes


@pytest.mark.parametrize('attribute', [
    'font-family=" Missing A, Noto Sans "',
    "font-family=' Missing A, Noto Sans '",
    'style="font-family: Missing A, Noto Sans ; font-size:20px"',
])
def test_prepare_substitutes_padded_font_stacks(tmp_path, attribute):
    """Stack discovery and replacement must apply the same whitespace normalization."""
    source = f'<text {attribute}>Test</text>'
    result = R.prepare(source, cache_dir=tmp_path / 'cache', use_system=False)
    assert 'NotoSansStatic' in result.svg and 'Missing A' not in result.svg
    assert 'NotoSansStatic' not in source
    assert not result.warnings


@pytest.mark.requires_bin('rsvg-convert', 'pdffonts')
def test_padded_font_stack_embeds_the_resolved_face(tmp_path):
    """A padded stack must embed the prepared bundled face in an actual PDF."""
    source = '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="60"><text x="10" y="30" font-family=" Missing A, Noto Sans ">Test</text></svg>'
    path = tmp_path / 'source.svg'
    path.write_text(source)
    output = R.svg_to_pdf(path, tmp_path / 'output.pdf', cache_dir=tmp_path / 'cache')
    result = subprocess.run(['pdffonts', str(output)], capture_output=True, text=True, check=True)
    assert 'NotoSansStatic-Regular' in result.stdout, result.stdout
    assert path.read_text() == source
