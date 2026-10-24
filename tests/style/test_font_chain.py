# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Platform and licence gates with invented font files and mocked discovery."""
from pathlib import Path

import pytest
from ltcore import fonts as F


@pytest.fixture
def no_fonts(monkeypatch, tmp_path):
    monkeypatch.setattr(F, 'bundled_files', lambda entry: [])
    monkeypatch.setattr(F, '_data_fonts_dir', lambda *a: tmp_path / 'empty')
    monkeypatch.setattr(F, 'fc_match', lambda *a, **k: None)
    monkeypatch.setattr(F, 'scan_family', lambda *a, **k: [])


def test_noto_installed_copy_before_system(no_fonts, monkeypatch, font_file):
    path = font_file('Noto Sans')
    monkeypatch.setattr(F, 'fc_match', lambda fam, *a, **k: path if fam == 'Noto Sans' else None)
    font = F.find(family='Missing', os_fallback=True)
    assert font.path == path and not font.os_fallback


def test_macos_chain_uses_system_folder_only(no_fonts, monkeypatch, tmp_path, font_file):
    root = tmp_path / 'system'
    path = font_file('.SF NS', filename='system/SFNS.ttf')
    monkeypatch.setattr(F, 'SYSTEM_FONT_ROOT', root)
    monkeypatch.setattr(F.sys, 'platform', 'darwin')
    assert F.find(os_fallback=True).path == path
    outside = font_file('SF Pro', filename='user/SF-Pro.ttf')
    for cfg in ({'fonts.sans': 'SF Pro'}, {'fonts.sans_path': str(outside)}):
        monkeypatch.setattr(F, 'fc_match', lambda *a, **k: outside)
        font = F.find(cfg=cfg, os_fallback=True)
        assert font.path == path and font.os_fallback
    with pytest.raises(F.FontError, match="Apple's font licence"):
        F.find(cfg={'fonts.sans_path': str(outside)}, os_fallback=False)


def test_windows_chain_picks_static_segoe_faces(no_fonts, monkeypatch, tmp_path, font_file):
    paths = [font_file('Segoe UI', weight=w, filename='system/'+n) for w,n in
             [(400,'segoeui.ttf'), (600,'seguisb.ttf'), (700,'segoeuib.ttf')]]
    font_file('Segoe UI', variable=True, filename='system/SegUIVar.ttf')
    monkeypatch.setattr(F.sys, 'platform', 'win32')
    monkeypatch.setattr(F, 'platform_font_dirs', lambda: [tmp_path / 'system'])
    assert F.find(weight=600, os_fallback=True).path == paths[1]


def test_linux_chain_is_dejavu(no_fonts, monkeypatch, font_file):
    path = font_file('DejaVu Sans')
    monkeypatch.setattr(F.sys, 'platform', 'linux')
    monkeypatch.setattr(F, 'fc_match', lambda fam, *a, **k: path if fam == 'DejaVu Sans' else None)
    assert F.find(os_fallback=True).os_fallback
    assert F.resolve(static=True, os_fallback=True) == path


@pytest.mark.parametrize('cfg,flag', [({}, False), ({'fonts.system_fallback': False}, True)])
def test_os_step_needs_caller_and_config(no_fonts, cfg, flag):
    with pytest.raises(F.FontError, match='tried.*fc-match.*platform font folder'):
        F.find(cfg=cfg, os_fallback=flag)


def test_os_fonts_are_never_cut(no_fonts, monkeypatch, font_file, tmp_path):
    path = font_file('DejaVu Sans', variable=True)
    monkeypatch.setattr(F.sys, 'platform', 'linux')
    monkeypatch.setattr(F, 'fc_match', lambda fam, *a, **k: path if fam == 'DejaVu Sans' else None)
    assert F.resolve(static=True, os_fallback=True, cache_dir=tmp_path/'cache') == path
    assert F.face_file(os_fallback=True, cache_dir=tmp_path/'cache')[0] == path
    assert not (tmp_path/'cache').exists()


@pytest.mark.parametrize('licence,copyright,expected', [
 ('SIL Open Font License 1.1','Test','modify'),
 ('SIL Open Font License 1.1. "Reserved Font Name" refers to any names specified.','Test','modify'),
 ((Path(__file__).resolve().parents[2]/'shared/fonts/noto-sans/OFL.txt').read_text(),'Test','modify'),
 ('SIL Open Font License 1.1','with Reserved Font Name "Test"','whole'),
 ('SIL Open Font License 1.1; Reserved Font Names: Test','Test','whole'),
 ('Microsoft supplied font. SIL Open Font License 1.1','Test','none'),
 ('unknown','Test','none'), ('Bitstream Vera','Test','whole'), ('Ubuntu Font Licence','Test','whole'),
 ('Apache License 2.0','Test','modify'), ('Apache License, Version 2.0','Test','modify')])
def test_licence_class_rules(font_file, licence, copyright, expected):
    path = font_file(licence=licence, copyright=copyright)
    assert F.licence_class(path) == expected


@pytest.mark.parametrize('value,allowed', [(0,True),(2,False),(4,False),(6,False),(8,True),(12,True),(256,False),(264,False),(512,False)])
def test_fstype_rules(font_file, value, allowed):
    path = font_file(fs_type=value)
    ok, reason = F.pdf_embeddable(path)
    assert ok == allowed and f'0x{value:04x}' in reason


def test_css_stacks():
    assert F.css_stack() == "'Noto Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'DejaVu Sans', sans-serif"
    assert F.css_stack('mono') == "'Noto Sans Mono', ui-monospace, 'SF Mono', 'Cascadia Mono', 'DejaVu Sans Mono', monospace"
    assert F.css_stack(cfg={'fonts.system_fallback':False}) == "'Noto Sans', sans-serif"
    assert F.css_stack(first="noto sans").lower().count("noto sans") == 1
    with pytest.raises(F.FontError, match='unsafe CSS'):
        F.css_stack(first="Test'font")


def test_svg_with_stacks_keeps_the_source():
    source = '<text font-family="Test Sans">a</text><text font-family="Test Mono">b</text>'
    output = F.svg_with_stacks(source)
    assert 'Test Sans' in source and 'Segoe UI' not in source
    assert F.css_stack(first='Test Sans') in output
    assert F.css_stack('mono', first='Test Mono') in output
    assert F.svg_with_stacks(output) == output


def test_none_diagram_font_falls_back(font_file):
    path = font_file(licence='unknown')
    font = F.find(cfg={'fonts.diagram.sans':str(path)}, output='diagram')
    assert font.family == 'Noto Sans' and font.fallback and 'embedding refused' in font.source


@pytest.mark.parametrize('platform,family,filename,source_label',[
 ('linux','DejaVu Sans','DejaVuSans.ttf','system fallback (Linux DejaVu Sans)'),
 ('darwin','.SF NS','system/SFNS.ttf','system fallback (macOS SF Pro)')])
def test_labels_use_the_os_font_when_noto_is_missing(no_fonts,monkeypatch,font_file,tmp_path,capsys,platform,family,filename,source_label):
    import importlib.util
    path=font_file(family,filename=filename)
    monkeypatch.setattr(F.sys,'platform',platform)
    if platform=='darwin':
        monkeypatch.setattr(F,'SYSTEM_FONT_ROOT',tmp_path/'system')
    else:
        monkeypatch.setattr(F,'fc_match',lambda fam,*a,**k:path if fam==family else None)
    repo=Path(__file__).resolve().parents[2]
    for script in ['annotate_micrograph.py','gridref.py']:
        spec=importlib.util.spec_from_file_location('label_tool',repo/'skills/lt-module-fill/scripts'/script)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        font,label=module.resolve_font({},24,600)
        assert Path(font.path)==path and label==source_label
        output=capsys.readouterr().err
        assert 'font: '+source_label in output and 'pillow-default' not in output


def test_catalog_and_os_licence_classes(font_file):
    path=font_file('Source Sans 3',variable=True)
    assert F.licence_class(path)=='whole'
    path=font_file('Test Sans')
    font=F.Font('sans','Test Sans',path,'OS',os_fallback=True)
    assert F.licence_class(font)=='none'
    path=font_file('SF Pro')
    assert F.licence_class(path)=='none'


@pytest.mark.parametrize('family,cfg', [
    ('Iosevka', {}),
    ('iosevka', {}),
    ('Test Fixed', {'fonts.diagram.mono': 'Test Fixed', 'fonts.mono': 'Test Base Mono'}),
    ('Test Base Fixed', {'fonts.mono': 'Test Base Fixed'}),
    ('Test Code Sans', {'fonts.diagram.sans': 'Test Code Sans'}),
])
def test_svg_stacks_use_catalog_and_configured_roles(family, cfg):
    """Names without mono and sans names containing code must retain their known role."""
    source = f'<text font-family="{family}">Test</text>'
    output = F.svg_with_stacks(source, cfg)
    if family == 'Test Code Sans':
        assert "'Noto Sans'," in output and "'Noto Sans Mono'" not in output
    else:
        assert "'Noto Sans Mono', ui-monospace" in output and "'Noto Sans'," not in output


def test_svg_stacks_use_catalog_and_configured_roles_for_files(font_file):
    """A diagram mono selected by file must retain its role in copied SVGs."""
    path = font_file('Test File Fixed')
    cfg = {'fonts.diagram.mono': str(path)}
    output = F.svg_with_stacks('<text font-family="Test File Fixed">Test</text>', cfg)
    assert "'Noto Sans Mono', ui-monospace" in output and "'Noto Sans'," not in output
