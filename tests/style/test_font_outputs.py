# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Output precedence and label compatibility, using synthetic faces only."""
from pathlib import Path

import pytest
from ltcore import config, fonts as F


class Cfg(dict):
    path = None


@pytest.mark.parametrize('output', [None, 'pdf', 'diagram', 'labels'])
@pytest.mark.parametrize('base', [{}, {'fonts.sans': 'Noto Sans'}, {'annotate.font_path': ''}])
def test_v01_configs_resolve_the_same_files(output, base):
    assert F.resolve('sans', Cfg(base), output=output) == F.resolve('sans', Cfg(base))


def test_output_table_overrides_base_per_role(font_file):
    path = font_file()
    cfg = Cfg({'fonts.diagram.sans': str(path)})
    assert F.resolve('sans', cfg, output='diagram') == path
    for output in (None, 'pdf', 'labels'):
        assert F.resolve('sans', cfg, output=output) == F.resolve('sans')
    assert F.resolve('mono', cfg, output='diagram') == F.resolve('mono')


def test_empty_output_keys_inherit_base(font_file):
    path = font_file()
    cfg = Cfg({'fonts.sans_path': str(path), **{f'fonts.{o}.sans': '' for o in F.OUTPUTS}})
    assert all(F.resolve('sans', cfg, output=o) == path for o in F.OUTPUTS)


def test_output_family_shadows_base_path(font_file):
    path = font_file()
    cfg = Cfg({'fonts.sans_path': str(path), 'fonts.diagram.sans': 'Noto Sans'})
    assert F.resolve('sans', cfg, output='diagram') != path
    assert F.resolve('sans', cfg, output='pdf') == path


def test_output_path_value_is_relative_to_the_config(tmp_path, font_file):
    path = font_file(filename='f/x.ttf')
    source = tmp_path / 'config.toml'
    source.write_text('[fonts.pdf]\nsans = "f/x.ttf"\n', encoding="utf-8", newline="\n")
    cfg = config.load(source)
    assert F.resolve('sans', cfg, output='pdf') == path


def test_output_mono_shadows_base_bold_path(font_file):
    path = font_file('Test Mono')
    cfg = Cfg({'fonts.mono_bold_path': str(path), 'fonts.pdf.mono': 'Noto Sans Mono'})
    assert F.resolve('mono', cfg, weight=700, output='pdf') != path
    assert F.resolve('mono', cfg, weight=700, output='diagram') == path


def test_unknown_output_is_refused():
    with pytest.raises(F.FontError, match='pdf, diagram, labels'):
        F.find(output='html')


@pytest.mark.parametrize('key,hint', [('colour', 'unknown key'), ('sans_path', "did you mean 'sans'"), ('mono', 'unknown key')])
def test_schema_rejects_unknown_output_keys(tmp_path, key, hint):
    source = tmp_path / 'config.toml'
    output = 'labels' if key == 'mono' else 'diagram'
    source.write_text(f'[fonts.{output}]\n{key} = "x"\n', encoding="utf-8", newline="\n")
    with pytest.raises(config.ConfigError, match=hint):
        config.load(source)


def test_missing_output_path_raises():
    cfg = Cfg({'fonts.pdf.sans': 'missing.ttf'})
    with pytest.raises(F.FontError, match='does not exist'):
        F.resolve('sans', cfg, output='pdf')
    assert F.resolve('sans', cfg, output='diagram').is_file()


def test_label_font_order(font_file, capsys):
    paths = [font_file(f'Test {n}') for n in range(5)]
    keys = ['annotate.font_path', 'fonts.labels.sans', 'fonts.pdf.sans', 'fonts.sans_path']
    cfg = Cfg(dict(zip(keys, map(str, paths[1:]))))
    font, label = F.label_font(cfg, 24, path=paths[0])
    assert Path(font.path) == paths[0] and label == 'config annotate.font_path'
    for key, path in zip(keys, paths[1:]):
        font, label = F.label_font(cfg, 24)
        assert Path(font.path) == path and key.removesuffix('_path') in capsys.readouterr().err
        cfg.pop(key)
    cfg['fonts.diagram.sans'] = str(paths[0])
    assert Path(F.label_font(cfg, 24)[0].path) == F.resolve('sans')


def test_labels_table_has_sans_only():
    with pytest.raises(F.FontError, match='sans role only'):
        F.find('mono', output='labels')


@pytest.mark.parametrize('override', ['config', 'argument'])
def test_missing_explicit_label_font_warns_after_fallback(tmp_path, capsys, override):
    missing = tmp_path / 'missing-label.ttf'
    cfg = Cfg({'fonts.system_fallback': False})
    kwargs = {}
    if override == 'config':
        cfg['annotate.font_path'] = str(missing)
    else:
        kwargs['path'] = missing
    font, label = F.label_font(cfg, 24, **kwargs)
    assert Path(font.path) == F.resolve('sans', cfg)
    assert label == 'fonts.sans'
    diagnostic = capsys.readouterr().err
    assert 'WARNING:' in diagnostic
    assert 'annotate.font_path' in diagnostic
    assert 'missing-label.ttf' in diagnostic and 'does not exist' in diagnostic
    assert 'pillow-default' not in diagnostic


def test_label_font_keeps_the_axis_rule(monkeypatch, font_file):
    from PIL import ImageFont
    path = font_file()
    class Face:
        def get_variation_axes(self):
            return [dict(name=b'Weight', minimum=100, default=400, maximum=900),
                    dict(name=b'Optical Size', minimum=14, default=14, maximum=32)]
        def set_variation_by_axes(self, values):
            self.values = values
    face = Face()
    monkeypatch.setattr(ImageFont, 'truetype', lambda *a, **k: face)
    assert F.label_font(Cfg({'fonts.labels.sans': str(path)}), 40)[0].values == [600, 14]


@pytest.mark.parametrize('mode',['empty','families','paths','annotate'])
def test_v010_face_files_are_preserved(mode,font_file):
    # These expected paths express the old precedence independently of output resolution.
    noto=F.FONTS_DIR/'noto-sans/NotoSans-Variable.ttf'
    mono=F.FONTS_DIR/'noto-sans-mono/NotoSansMono-Variable.ttf'
    cfg=Cfg()
    expected={'sans':noto,'mono':mono}
    label=noto
    if mode=='families':
        cfg.update({'fonts.sans':'Noto Sans Mono','fonts.mono':'Noto Sans'})
        expected={'sans':mono,'mono':noto}
        label=mono
    elif mode=='paths':
        a,b=font_file('Test Sans'),font_file('Test Mono')
        cfg.update({'fonts.sans_path':str(a),'fonts.mono_path':str(b)})
        expected={'sans':a,'mono':b}
        label=a
    elif mode=='annotate':
        label=font_file('Test Labels')
        cfg['annotate.font_path']=str(label)
    for role,weight,italic in [('sans',400,False),('sans',700,False),('sans',400,True),
            ('sans',700,True),('sans',600,False),('mono',400,False),('mono',700,False),('mono',400,True)]:
        assert F.find(role,cfg,weight=weight,italic=italic,output='pdf').path==expected[role]
    for role in ('sans','mono'):
        assert F.find(role,cfg,output='diagram').path==expected[role]
    assert Path(F.label_font(cfg,24)[0].path)==label
