# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression coverage for metadata, linked SVG assets, themes and font faces."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess

import pytest

from ltcore import cleaners as C, fonts as F, palettes as P, raster as R

SANS = F.bundled_files(F.catalog()['noto-sans'])[0]


def test_pdf_strip_requires_complete_metadata_validation(tmp_path, monkeypatch):
    from tests.style.test_cleaners import _minimal_pdf
    path = tmp_path / 'doc.pdf'
    original = _minimal_pdf()
    path.write_bytes(original)
    monkeypatch.setattr(C, 'metadata_left', lambda data: None)
    result = C.strip_pdf_metadata(path)
    assert result.status == 'failed', result.detail
    assert 'validation' in result.detail
    assert path.read_bytes() == original


@pytest.mark.requires_bin('qpdf')
@pytest.mark.parametrize('location', ['page-xmp', 'custom-info'])
def test_qpdf_validation_sees_page_xmp_and_custom_info(tmp_path, monkeypatch, location):
    from tests.style.test_cleaners import _minimal_pdf
    path = tmp_path / 'doc.pdf'
    original = _minimal_pdf()
    if location == 'page-xmp':
        # Hand-built page-level metadata with exact xref offsets.
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Metadata 4 0 R >>',
                   b'<< /Type /Metadata /Subtype /XML /Length 5 >>\nstream\n<x/>\nendstream']
        data = bytearray(b'%PDF-1.4\n')
        offsets = []
        for i, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data.extend(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
        start = len(data)
        data.extend(b'xref\n0 5\n0000000000 65535 f \n')
        for off in offsets:
            data.extend(f'{off:010d} 00000 n \n'.encode())
        data.extend(f'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
        original = bytes(data)
    else:
        original = original.replace(b'/Author', b'/Secret')  # equal length, valid xref
    path.write_bytes(original)
    real = C._optional
    monkeypatch.setattr(C, '_optional', lambda name: None if name == 'pikepdf' else real(name))
    left = C.metadata_left(original)
    assert left is not None and left, 'qpdf must inspect every object and every Info key'
    if location == 'page-xmp':
        assert any('XMP' in item for item in left)
        result = C.strip_pdf_metadata(path)
        assert result.status == 'failed', result.detail
        assert 'metadata left' in result.detail and 'XMP' in result.detail
        assert path.read_bytes() == original
    else:
        assert any('Info' in item for item in left)


@pytest.mark.requires_bin('rsvg-convert')
@pytest.mark.parametrize('href', ['href', 'xlink:href'])
def test_prepared_svg_preserves_relative_linked_png(tmp_path, href):
    Image = pytest.importorskip('PIL.Image')
    pytest.importorskip('fontTools')
    Image.new('RGB', (20, 20), 'red').save(tmp_path / 'tile.png')
    src = tmp_path / 'figure.svg'
    text = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="20" height="20"><image {href}="tile.png" width="20" height="20"/>'
            '<text x="0" y="19" font-family="Noto Sans">.</text></svg>')
    src.write_text(text, encoding="utf-8", newline="\n")
    out, _ = R.convert(src, tmp_path / 'out.png', cache_dir=tmp_path / 'cache')
    with Image.open(out) as im:
        assert im.convert('RGB').getpixel((5, 5)) == (255, 0, 0)
    assert src.read_text(encoding="utf-8") == text


@pytest.mark.requires_bin('rsvg-convert')
def test_relative_svg_source_resolves_before_changing_directory(tmp_path, monkeypatch):
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'figure.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>', encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path)
    path, _ = R.convert('nested/figure.svg', tmp_path / 'out.png', prepare_fonts=False)
    assert path.read_bytes().startswith(b'\x89PNG')


@pytest.mark.requires_bin('rsvg-convert')
def test_raster_height_changes_actual_output(tmp_path):
    Image = pytest.importorskip('PIL.Image')
    src = tmp_path / 'figure.svg'
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"/>', encoding="utf-8", newline="\n")
    path, _ = R.convert(src, tmp_path / 'out.png', height=60, prepare_fonts=False)
    with Image.open(path) as im:
        assert im.height == 60


def test_cache_separates_effective_cvd_policy(tmp_path):
    entry = dict(P.catalog()['modus-operandi'])
    P.derive_theme(entry, cache_dir=tmp_path)
    entry['cvd'] = 'check'
    with pytest.raises(P.PaletteError, match='simulation'):
        P.derive_theme(entry, cache_dir=tmp_path)


def test_cache_separates_mode(tmp_path):
    entry = dict(P.catalog()['tol-medium-contrast'])
    a = P.derive_theme(entry, cache_dir=tmp_path)
    entry['mode'] = 'auto'
    b = P.derive_theme(entry, cache_dir=tmp_path)
    assert a['digest'] != b['digest']


def test_custom_catalog_id_bypasses_shipped_override(tmp_path):
    file = tmp_path / 'custom.toml'
    file.write_text('[[palette]]\nid="catppuccin-latte"\nmode="light"\n[palette.roles]\n' +
                    '\n'.join(f'{k}="{v}"' for k, v in P.catalog()['tol-medium-contrast']['roles'].items()), encoding="utf-8", newline="\n")
    theme = P.derive_theme('catppuccin-latte', {'style.custom_palettes_file': str(file)}, use_cache=False)
    assert theme['canvas'] == '#ffffff'
    assert theme['source'] == 'derived'


def test_custom_explicit_override_wins():
    ov = P.load_override('catppuccin-latte')
    ov['theme'] = dict(ov['theme'], canvas='#ffffff')
    entry = {'id': 'catppuccin-latte', 'override': ov}
    assert P.derive_theme(entry, use_cache=False)['canvas'] == '#ffffff'


def test_explicit_upright_font_reports_missing_italic():
    font = F.find('sans', {'fonts.sans_path': str(SANS)}, italic=True, use_system=False)
    assert font.italic is False
    assert font.fallback and 'italic' in font.source.lower()


def test_italic_filter_rejects_upright_file():
    assert F._pick([SANS], 400, True) is None
    assert F._pick([SANS], 400, False)[0] == SANS


def test_ubuntu_catalog_resolves_real_family():
    entry = F.catalog()['ubuntu']
    assert entry['family'] == 'Ubuntu'
    assert 'classic' in entry['name'].lower()


def test_font_download_has_bounded_reads_and_private_staging(tmp_path, monkeypatch):
    data = b'x' * (20 * 1024 * 1024 + 1)
    entry = {'id': 'test-font', 'family': 'Test', 'fetch': {'files': [
        {'url': 'https://example.org/font.ttf', 'sha256': hashlib.sha256(data).hexdigest(), 'file': 'font.ttf'}]}}
    monkeypatch.setattr(F, 'catalog', lambda: {'test-font': entry})
    sizes, staged = [], []
    mkstemp = F.tempfile.mkstemp
    def private_file(*args, **kwargs):
        fd, name = mkstemp(*args, **kwargs)
        path = Path(name)
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        staged.append(path)
        return fd, name
    monkeypatch.setattr(F.tempfile, 'mkstemp', private_file)
    class Response(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            assert 0 < size <= 65536, 'font downloads must use bounded reads'
            return super().read(size)
    with pytest.raises(F.FontError, match='20 MiB'):
        F.fetch('test-font', dest_root=tmp_path, opener=lambda *a, **k: Response(data))
    assert len(sizes) > 1
    assert staged and all(not path.exists() for path in staged)
    assert not list(tmp_path.iterdir())


def test_pdf_cleaner_rejects_changed_page_count(tmp_path):
    pikepdf = pytest.importorskip('pikepdf')
    path = tmp_path / 'doc.pdf'
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(path)
    original = path.read_bytes()
    def runner(argv, **kw):
        with pikepdf.new() as pdf:
            pdf.add_blank_page()
            pdf.add_blank_page()
            pdf.save(argv[1])
        return subprocess.CompletedProcess(argv, 0, '', '')
    entry = {'name': 'pdf', 'stage': 'pdf', 'argv': ['tool', '{pdf}']}
    result = C.run_cleaner(entry, path, runner=runner)
    assert result.status == 'failed' and 'page count' in result.detail
    assert path.read_bytes() == original


def test_custom_palette_rejects_invalid_mode(tmp_path):
    file = tmp_path / 'bad.toml'
    file.write_text('[[palette]]\nid="bad"\nmode="invalid"\n[palette.roles]\nbackground="#fff"\ntext="#000"', encoding="utf-8", newline="\n")
    with pytest.raises(P.PaletteError, match='mode'):
        P.load_custom(file)


def test_defaults_require_cvd_strokes_and_fill_greyscale():
    result = P.rank()
    rows = {r['id']: r for r in result['entries']}
    assert not rows['modus-vivendi-tritanopia']['default_eligible']
    for pid in result['defaults']['palettes']:
        row = rows[pid]
        assert row['default_eligible']
        assert min(row['stroke_de_ok'][s] for s in ('protan', 'deutan', 'tritan')) >= .05
        assert row['fill_grey_dl'] >= .03


def test_light_default_passes_restored_gates():
    result = P.rank()
    assert result['defaults']['palettes'], 'a usable light default must pass every gate'
    default = result['defaults']['palettes'][0]
    theme = P.derive_theme(default, use_cache=False)
    assert theme['mode'] == 'light'
    assert P.check_theme(theme, cvd='check')['failures'] == []


def test_fill_greyscale_is_a_strict_gate():
    theme = P.derive_theme('tol-medium-contrast', use_cache=False)
    check = P.check_theme(theme, cvd='check')
    assert check['fills']['grey']['dl'] >= .03
    assert not check['failures']


def test_dark_default_uses_measured_composites():
    result = P.rank()
    composites = [r for r in result['entries'] if r.get('composite_sources')]
    assert len(composites) == 40
    assert len({tuple(r['composite_sources']) for r in composites}) == 40
    assert result['defaults']['palettes'][-1] == 'petroff-10-on-primer-dark-high-contrast'
    theme = P.derive_theme(result['defaults']['palettes'][-1], use_cache=False)
    assert theme['mode'] == 'dark'
    assert P.check_theme(theme, cvd='check')['failures'] == []
    assert result['catalog_dark_eligible'] == 0


def test_static_instance_cannot_invent_italic_outlines(tmp_path):
    pytest.importorskip('fontTools')
    with pytest.raises(F.FontError, match='italic face'):
        F.static_instance(SANS, weight=400, italic=True, cache_dir=tmp_path)


def test_weighted_orders_exclude_failed_gates():
    result = P.rank()
    rows = {r['id']: r for r in result['entries']}
    for weighting, modes in result['order'].items():
        for mode, ids in modes.items():
            assert all(rows[pid]['default_eligible'] for pid in ids), (weighting, mode)
            scores = [rows[pid]['scores'][weighting] for pid in ids]
            assert scores == sorted(scores, reverse=True)
            assert result['winners'][weighting][mode] == (ids[0] if ids else None)


def test_no_dark_default_when_no_dark_theme_passes():
    cat = P.catalog()
    entries = {k: cat[k] for k in ('tol-medium-contrast', 'catppuccin-mocha')}
    result = P.rank(entries)
    rows = {r['id']: r for r in result['entries']}
    assert not any(r['default_eligible'] for r in rows.values() if r.get('mode') == 'dark')
    assert result['winners']['balanced']['dark'] is None
    assert result['defaults'] == {'palettes': ['tol-medium-contrast'],
                                  'print_palette': 'tol-medium-contrast'}


@pytest.mark.requires_bin('qpdf')
@pytest.mark.parametrize('pages', [1, 2])
def test_pdf_page_count_check_works_with_qpdf_alone(tmp_path, monkeypatch, pages):
    from tests.style.test_cleaners import _minimal_pdf
    real = C._optional
    monkeypatch.setattr(C, '_optional', lambda name: None if name == 'pikepdf' else real(name))
    path = tmp_path / 'doc.pdf'
    original = _minimal_pdf()
    path.write_bytes(original)

    def runner(argv, **kw):
        target = Path(argv[1])
        rebuilt = target.with_name('rebuilt.pdf')
        subprocess.run(['qpdf', '--empty', '--pages', *[str(target)] * pages, '--', str(rebuilt)],
                       check=True, capture_output=True)
        rebuilt.replace(target)
        return subprocess.CompletedProcess(argv, 0, '', '')
    entry = {'name': 'pdf', 'stage': 'pdf', 'argv': ['tool', '{pdf}']}
    result = C.run_cleaner(entry, path, runner=runner)
    if pages == 1:
        assert result.status == 'cleaned', result.detail
        assert 'not checked' not in result.detail
    else:
        assert result.status == 'failed' and 'page count' in result.detail
        assert path.read_bytes() == original


def _hand_built_pdf(objects, trailer):
    """PDF 1.4 bytes: objects numbered from 1, exact xref offsets, /Size added to trailer."""
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(data)
    data.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for off in offsets:
        data.extend(f'{off:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects) + 1} {trailer} >>\n'
                f'startxref\n{start}\n%%EOF\n'.encode())
    return bytes(data)


@pytest.mark.requires_bin('qpdf')
def test_qpdf_rewrite_works_before_qpdf_11_10(tmp_path, monkeypatch):
    # Ubuntu 24.04 ships qpdf 11.9; --remove-info and --remove-metadata arrived in 11.10.
    objects = [b'<< /Type /Catalog /Pages 2 0 R /Metadata 5 0 R /LTReal 0.00001 >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 4 0 R >>',
               b'<< /Length 0 >>\nstream\n\nendstream',
               b'<< /Type /Metadata /Subtype /XML /Length 4 >>\nstream\n<x/>\nendstream',
               b'<< /Author (Somebody) /ModDate (D:20260101000000Z) >>']
    old_id = 'ab' * 16
    path = tmp_path / 'doc.pdf'
    path.write_bytes(_hand_built_pdf(objects, f'/Root 1 0 R /Info 6 0 R /ID [<{old_id}> <{old_id}>]'))
    real_run, real_which, real_optional = subprocess.run, C.shutil.which, C._optional

    def qpdf_11_9(argv, *args, **kwargs):
        if {'--remove-info', '--remove-metadata'} & set(argv):
            return subprocess.CompletedProcess(argv, 2, '', 'qpdf: unrecognized argument')
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(C.subprocess, 'run', qpdf_11_9)
    # The qpdf rewrite alone has to remove the document information, ModDate included.
    monkeypatch.setattr(C.shutil, 'which', lambda name: None if name == 'exiftool' else real_which(name))
    monkeypatch.setattr(C, '_optional', lambda name: None if name == 'pikepdf' else real_optional(name))
    first = C.strip_pdf_metadata(path)
    assert first.status == 'cleaned', first.detail
    cleaned = path.read_bytes()
    for leftover in (b'Somebody', b'ModDate', b'/Metadata', old_id.encode()):
        assert leftover not in cleaned
    assert b'/LTReal 0.00001' in cleaned
    assert C.metadata_left(cleaned) == []
    second = C.strip_pdf_metadata(path)
    assert second.status == 'unchanged', second.detail
    assert path.read_bytes() == cleaned


@pytest.mark.requires_bin('qpdf')
def test_qpdf_rewrite_keeps_catalog_strings_under_a_legacy_code_page(tmp_path, monkeypatch):
    # The UTF-16 name tree key fits PDFDocEncoding, so a catalog written back from JSON
    # would store it as (A) and the link's <feff0041> would no longer match it. The
    # L with stroke is C5 81 in qpdf's UTF-8 JSON; cp1252, the Windows default for
    # text mode, has no character for 0x81.
    objects = [b'<< /Type /Catalog /Pages 2 0 R /Metadata 5 0 R /Lang <feff0065006e>'
               b' /Names << /Dests << /Names [<feff0041> [3 0 R /Fit]] >> >> >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources << >>'
               b' /Contents 4 0 R /Annots [7 0 R] >>',
               b'<< /Length 0 >>\nstream\n\nendstream',
               b'<< /Type /Metadata /Subtype /XML /Length 4 >>\nstream\n<x/>\nendstream',
               b'<< /Author (Somebody) >>',
               b'<< /Type /Annot /Subtype /Link /Rect [0 0 5 5] /Dest <feff0041>'
               b' /Contents <feff014100f3> >>']
    path = tmp_path / 'doc.pdf'
    path.write_bytes(_hand_built_pdf(objects, '/Root 1 0 R /Info 6 0 R'))
    real_run, real_which, real_optional = subprocess.run, C.shutil.which, C._optional

    def legacy_code_page(argv, *args, **kwargs):
        if kwargs.get('text') and not kwargs.get('encoding'):
            kwargs['encoding'] = 'cp1252'
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(C.subprocess, 'run', legacy_code_page)
    monkeypatch.setattr(C.shutil, 'which', lambda name: None if name == 'exiftool' else real_which(name))
    monkeypatch.setattr(C, '_optional', lambda name: None if name == 'pikepdf' else real_optional(name))
    result = C.strip_pdf_metadata(path)
    assert result.status == 'cleaned', result.detail
    cleaned = path.read_bytes()
    for kept in (b'/Dests << /Names [ <feff0041>', b'/Dest <feff0041>', b'/Lang <feff0065006e>',
                 b'<feff014100f3>'):
        assert kept in cleaned
    for leftover in (b'Somebody', b'/Metadata'):
        assert leftover not in cleaned
    assert C.metadata_left(cleaned) == []


@pytest.mark.requires_bin('qpdf')
def test_qpdf_without_json_v2_fails_closed(tmp_path, monkeypatch):
    # qpdf 10 has no JSON v2, so it rejects --json-key=qpdf.
    from tests.style.test_cleaners import _minimal_pdf
    path = tmp_path / 'doc.pdf'
    original = _minimal_pdf()
    path.write_bytes(original)
    real_run, real_which, real_optional = subprocess.run, C.shutil.which, C._optional

    def qpdf_10(argv, *args, **kwargs):
        if '--json-key=qpdf' in argv:
            return subprocess.CompletedProcess(argv, 2, '', 'qpdf: unrecognized argument')
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(C.subprocess, 'run', qpdf_10)
    monkeypatch.setattr(C.shutil, 'which', lambda name: None if name == 'exiftool' else real_which(name))
    monkeypatch.setattr(C, '_optional', lambda name: None if name == 'pikepdf' else real_optional(name))
    assert C.metadata_left(original) is None
    result = C.strip_pdf_metadata(path)
    assert result.status == 'failed' and 'qpdf --json exit 2' in result.detail, result.detail
    assert path.read_bytes() == original


@pytest.mark.requires_bin('rsvg-convert')
def test_read_only_svg_folder_renders_with_a_warning(tmp_path):
    pytest.importorskip('fontTools')
    folder = tmp_path / 'ro'
    folder.mkdir()
    src = folder / 'figure.svg'
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
                   '<text x="0" y="19" font-family="Noto Sans">a</text></svg>', encoding="utf-8", newline="\n")
    folder.chmod(0o555)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip('this user can write to a 0555 folder')
        out, warnings = R.convert(src, tmp_path / 'out.png', cache_dir=tmp_path / 'cache')
    finally:
        folder.chmod(0o755)
    assert out.read_bytes().startswith(b'\x89PNG')
    assert any('cannot write the font-prepared copy' in w for w in warnings), warnings
    assert sorted(x.name for x in folder.iterdir()) == ['figure.svg']


def test_bundled_italic_request_names_the_missing_face():
    font = F.find('sans', None, italic=True, use_system=False)
    assert font.italic is False
    assert font.source.startswith('bundled') and 'italic face missing' in font.source


def test_pdf_strip_fails_without_metadata_validation_tools(tmp_path, monkeypatch):
    from tests.style.test_cleaners import _minimal_pdf
    path = tmp_path / 'document.pdf'
    original = _minimal_pdf()
    path.write_bytes(original)
    monkeypatch.setattr(C, '_optional', lambda name: None)
    monkeypatch.setattr(C.shutil, 'which', lambda name: None)
    result = C.strip_pdf_metadata(path)
    assert result.status == 'failed', result.detail
    assert 'validation' in result.detail
    assert path.read_bytes() == original


def test_fill_greyscale_gate_rejects_distinct_chromatic_fills():
    # These fills stay distinct in normal vision but merge in greyscale.
    theme = P.derive_theme('tol-bright', use_cache=False)
    for role, fill in [('risk', '#eeeeff'), ('sign', '#ffebea')]:
        theme['roles'][role] = (fill, *theme['roles'][role][1:])
    report = P.check_theme(theme, cvd='report')
    assert not report['failures'], report['failures']
    assert report['fills']['none']['de_ok'] >= .02
    assert report['fills']['grey']['dl'] < .03
    assert any('greyscale' in item for item in report['eligibility_failures'])
    strict = P.check_theme(theme, cvd='check')
    assert any('greyscale' in item for item in strict['failures'])
