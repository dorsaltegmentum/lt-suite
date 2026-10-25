# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real web-font encoding and static-set gates with synthetic font metadata."""
from pathlib import Path

import pytest
import fontset
import style
from ltcore import fonts as F


@pytest.mark.parametrize('licence',['unknown','Microsoft supplied font. SIL Open Font License 1.1'])
def test_unreadable_licence_fonts_never_embedded_in_html_or_drawio(font_file,monkeypatch,licence):
    path=font_file('Test Restricted',licence=licence)
    monkeypatch.setattr(fontset,'faces',lambda:[('Test Restricted',400,False,path)])
    monkeypatch.setattr(style,'font_family',lambda role:'Test Restricted')
    with pytest.raises(F.FontError,match='licence refuses'):
        fontset.page_css('abc')
    with pytest.raises(F.FontError,match='licence refuses'):
        fontset.drawio_fonts('abc')


def test_engine_refuses_rfn_variable_fonts(font_file,monkeypatch,tmp_path):
    path=font_file('Test RFN',variable=True,copyright='with Reserved Font Name "Test"')
    monkeypatch.setattr(fontset,'faces',lambda:[('Test RFN',500,False,path)])
    def cache(*parts):
        result=tmp_path/'cache'
        result.mkdir(exist_ok=True)
        return result
    monkeypatch.setattr(style,'cache_dir',cache)
    monkeypatch.setattr(fontset,'_cut',lambda *a:pytest.fail('RFN font was cut'))
    with pytest.warns(UserWarning,match='lose its weight'):
        conf,rename=fontset.static_set()
    assert not rename and conf.is_file()
    assert not list((tmp_path/'cache').rglob('*.ttf'))
    assert fontset._subset(path,'abc',500,False)[0] == path.read_bytes()


def test_page_carries_the_chain(toy_build):
    from ltcore import fonts
    page=(toy_build/'toy-case.html').read_text(encoding="utf-8")
    assert '--sans: '+fonts.css_stack() in page
    assert '--mono: '+fonts.css_stack('mono') in page
    assert 'font-family="'+fonts.css_stack()+'"' in page
