# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for executable trust, safe exports and delivery gates."""
import base64
import hashlib
import html
import io
import json
import re
import xml.etree.ElementTree as ET

import pytest

import case as C
import decor
import drawio
import floor
import fontset
import make_artifact
import style
from diagram_testkit import ENGINE, TOY, copy_case, run_engine


def test_cwd_code_requires_trust_and_records_all_hashes(tmp_path, monkeypatch):
    case = copy_case(tmp_path / "case")
    marker = case / "ran"
    code = case / "content.py"
    # A shared folder beneath cwd must not execute before consent.
    code.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + code.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (case / "helper.py").write_text('"""A case helper."""\n', encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LT_SUITE_DATA", str(tmp_path / "data"))
    style.configure(None)
    with pytest.raises(C.CaseError, match="--trust-case"):
        C.Case(case)
    assert not marker.exists()
    C.Case(case, trust=True)
    assert marker.exists()
    record = json.loads((tmp_path / "data/state/trusted-cases.json").read_text(encoding="utf-8"))
    expected = {p.relative_to(case).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in case.rglob("*.py")}
    assert record[str(case.resolve())] == expected
    C.Case(case)  # Identical code retains its explicit approval.
    (case / "helper.py").write_text('"""Changed helper."""\n', encoding="utf-8", newline="\n")
    with pytest.raises(C.CaseError, match="--trust-case"):
        C.Case(case)


def test_build_cwd_refusal_has_exit_three(tmp_path, engine_env):
    case = copy_case(tmp_path / "case")
    r = run_engine("build.py", case, cwd=tmp_path, env=engine_env)
    assert r.returncode == 3


def test_external_trusted_build_reuses_loaded_case(tmp_path, engine_env):
    case = copy_case(tmp_path / "case")
    r = run_engine("build.py", case, "--trust-case", cwd=ENGINE, env=engine_env)
    assert r.returncode == 0, r.stdout[-1800:] + r.stderr[-1800:]
    # Consistency must use the loaded object, even if trust is subsequently revoked.
    import consistency
    loaded = C.Case(case, trust=True)
    original = C.Case
    try:
        C.Case = lambda *a, **kw: pytest.fail("consistency reloaded executable case code")
        result = consistency.run(loaded, quiet=True)
    finally:
        C.Case = original
    assert result["counts"]
    assert result["report"].parent == case


def test_inset_active_content_removed_from_every_export(tmp_path, engine_env):
    case = copy_case(tmp_path / "case")
    src = case / "assets/clearance.svg"
    attack = ('<script>globalThis.inset_attack=1</script>'
              '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">unsafe</div></foreignObject>'
              '<image href="https://example.invalid/track.png"/>'
              '<a href="java&#x73;cript:alert(1)"><rect onload="alert(1)" width="2" height="2"/></a>'
              '<path style="fill:url(https://example.invalid/a.svg#p)"/>'
              '<style>@import "https://example.invalid/a.css";</style>')
    src.write_text(src.read_text(encoding="utf-8").replace('</svg>', attack + '</svg>'), encoding="utf-8", newline="\n")
    r = run_engine("build.py", case, "--trust-case", cwd=ENGINE, env=engine_env)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    for path in list(case.glob('final-*.svg')) + list(case.glob('*.drawio')) + list(case.glob('*.html')):
        text = path.read_text(encoding="utf-8")
        # Embedded SVGs in masters must be inspected after decoding too.
        for data in re.findall(r'data:image/svg\+xml,(?:base64,)?([A-Za-z0-9+/=]+)', html.unescape(text)):
            text += base64.b64decode(data).decode()
        for forbidden in ('inset_attack', 'foreignObject', 'onload=', 'javascript:', 'example.invalid'):
            assert forbidden not in text, (path.name, forbidden)
    for path in case.glob('final-*.svg'):
        assert '<script' not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize('escape', ['../outside.svg', 'symlink'])
def test_inset_path_must_stay_in_case(tmp_path, escape):
    case = copy_case(tmp_path / 'case')
    outside = tmp_path / 'outside.svg'
    outside.write_bytes((case / 'assets/clearance.svg').read_bytes())
    if escape == 'symlink':
        (case / 'assets/escape.svg').symlink_to(outside)
        escape = 'assets/escape.svg'
    loaded = C.Case(case, trust=True)
    loaded.meta['inset']['image'] = escape
    with pytest.raises(ValueError, match='outside the case folder'):
        decor.inset_prepare(loaded)


def test_json_script_cannot_close_script_element():
    payload = {'text': '</script><script>globalThis.json_attack=1</script><img onerror="x">'}
    encoded = make_artifact.json_script(payload)
    assert '<' not in encoded
    assert json.loads(encoded) == payload


@pytest.mark.parametrize('mutation', ['all-missing', 'one-missing', 'wrong-label'])
def test_floor_requires_labels_for_geometry_nodes(toy_copy, mutation):
    path = floor.Build(toy_copy).svg(floor.Build(toy_copy).palettes()[0])
    root = ET.parse(path).getroot()
    if mutation == 'all-missing':
        for parent in root.iter():
            for child in list(parent):
                if child.tag == floor.SVG_NS + 'text':
                    parent.remove(child)
    else:
        node = next(e for e in root.iter() if e.get('data-node'))
        for parent in node.iter():
            for child in list(parent):
                if child.tag == floor.SVG_NS + 'text':
                    if mutation == 'one-missing':
                        parent.remove(child)
                    else:
                        child.clear()
                        child.text = 'Unrelated label'
    ET.ElementTree(root).write(path, encoding='unicode')
    rows, ok = floor.check(toy_copy, quiet=True)
    assert not ok
    assert any(r[0].startswith('rendered labels') and r[3] == 'FAIL' for r in rows), rows


def test_floor_checks_dark_palette_overlaps(toy_copy):
    b = floor.Build(toy_copy)
    path = b.svg(b.palettes()[-1])
    text = path.read_text(encoding="utf-8")
    runs = re.findall(r'<text x="([\d.]+)" y="([\d.]+)"', text)
    (x0, y0), (x1, y1) = runs[:2]
    path.write_text(text.replace(f'<text x="{x1}" y="{y1}"', f'<text x="{x0}" y="{y0}"', 1), encoding="utf-8", newline="\n")
    rows, ok = floor.check(toy_copy, quiet=True)
    assert not ok
    assert any(r[0].startswith('label overlaps') and r[3] == 'FAIL' and b.palettes()[-1] in r[0] for r in rows)


def variant_case(tmp_path):
    case = copy_case(tmp_path / 'case')
    (case / 'variants.py').write_text('CORE = set(N for N in ' + repr(list(C.Case(TOY).content.NODES)) + ')\n', encoding="utf-8", newline="\n")
    (case / 'layout_core.py').write_bytes((case / 'layout.py').read_bytes())
    return case


def test_variant_must_pass_page_floor_before_delivery(tmp_path, engine_env):
    case = variant_case(tmp_path)
    cfg = tmp_path / 'small.toml'
    cfg.write_text('[diagram.page]\nmax_w=1000\n', encoding="utf-8", newline="\n")
    r = run_engine('variant_build.py', case, '--variant', 'core', '--trust-case', '--config', cfg,
                   cwd=ENGINE, env=engine_env)
    assert r.returncode != 0
    assert 'page size' in r.stdout + r.stderr
    assert not (case / 'deliver').exists()
    assert not (case / 'geometry-core.json').exists()


def test_variant_must_pass_accessibility_before_delivery(tmp_path, engine_env):
    case = variant_case(tmp_path)
    # A real render with white node labels on pale fills violates WCAG.
    (case / 'content.py').write_text((case / 'content.py').read_text(encoding="utf-8") +
        '\nimport themes\nfor palette in themes.final_palettes():\n'
        '    theme = themes.THEMES[palette]\n'
        '    theme["roles"] = {k: (f, s, "#ffffff") for k, (f,s,t) in theme["roles"].items()}\n', encoding="utf-8", newline="\n")
    r = run_engine('variant_build.py', case, '--variant', 'core', '--trust-case', cwd=ENGINE, env=engine_env)
    assert r.returncode != 0
    assert 'contrast' in r.stdout + r.stderr
    assert not (case / 'deliver').exists()


def test_fontconfig_escapes_paths(tmp_path, monkeypatch):
    cache = tmp_path / 'cache & <test>'
    monkeypatch.setenv('LT_SUITE_CACHE', str(cache))
    style.configure(None)
    conf, _ = fontset.static_set()
    root = ET.parse(conf).getroot()
    assert str(conf.parent) in [d.text for d in root.findall('dir')]
    assert root.findtext('cachedir') == str(conf.parent / 'cache')


@pytest.mark.parametrize('field', ['id', 'parent', 'source', 'target'])
def test_drawio_rejects_malformed_identifiers(field):
    d = drawio.Doc()
    kwargs = {'cid': 'safe', 'parent': '1', 'src': 'safe', 'tgt': 'safe'}
    kwargs[{'id':'cid', 'parent':'parent', 'source':'src', 'target':'tgt'}[field]] = 'safe" injected="YES'
    with pytest.raises(ValueError, match='identifier'):
        d.edge(style='', **kwargs)
    if field in ('id', 'parent'):
        with pytest.raises(ValueError, match='identifier'):
            d.vertex(kwargs['cid'], '', '', 0, 0, 1, 1, parent=kwargs['parent'])


def test_drawio_xml_attributes_preserve_literal_values():
    d = drawio.Doc()
    value, style_value = '<b>A & B</b>', 'fontFamily=A "quoted" & B;'
    d.vertex('safe', value, style_value, 0, 0, 10, 10)
    d.edge('edge', style_value, value=value)
    root = ET.fromstring('<root>' + ''.join(d.cells) + '</root>')
    for cell in list(root)[2:]:
        assert cell.get('style') == style_value
        assert cell.get('value') == value


def test_drawio_metadata_is_plain_text(toy_copy):
    loaded = C.Case(toy_copy, trust=True)
    payload = '<img src=x onerror="alert(1)"> & text'
    loaded.meta.update(subtitle=payload, group=payload, members=[payload])
    loaded.content.CLUSTERS['cause'] = payload
    path = drawio.write(loaded)
    cells = {e.get('id'): e for e in ET.parse(path).iter('mxCell')}
    for cid in ('subtitle', 'group', 'members', 'cause'):
        assert cells[cid].get('value') == html.escape(payload, quote=False)


def test_drawio_carries_resolved_fonts_without_system_copies(toy_copy, tmp_path, monkeypatch):
    # Empty fontconfig excludes installed copies. Every used face needs a loadable embedded file.
    conf = tmp_path / 'empty-fonts.conf'
    conf.write_text('<fontconfig></fontconfig>', encoding="utf-8", newline="\n")
    monkeypatch.setenv('FONTCONFIG_FILE', str(conf))
    loaded = C.Case(toy_copy, trust=True)
    path = drawio.write(loaded)
    root = ET.parse(path).getroot()
    embedded = root.find('.//mxGraphModel').get('extFonts', '')
    assert embedded, 'No bundled font data in master'
    fonts = {}
    from fontTools.ttLib import TTFont
    for entry in embedded.split('|'):
        family, uri = entry.split('^', 1)
        assert uri.startswith('data:font/ttf;base64,')
        data = base64.b64decode(uri.split(',', 1)[1])
        font = TTFont(io.BytesIO(data))
        assert 'X' in ''.join(chr(cp) for cp in font.getBestCmap())
        fonts[family] = font
    families = set(re.findall(r'fontFamily=([^;]+);', path.read_text(encoding="utf-8")))
    assert families <= set(fonts)
    assert len(families) >= 3  # sans medium, sans bold and mono bold


def test_scaffold_refuses_any_nonempty_folder(tmp_path, engine_env):
    dest = tmp_path / 'case'
    dest.mkdir()
    (dest / 'keep.txt').write_text('keep', encoding="utf-8", newline="\n")
    r = run_engine('new_case.py', dest, cwd=tmp_path, env=engine_env)
    assert r.returncode != 0
    assert sorted(p.name for p in dest.iterdir()) == ['keep.txt']


def test_drawio_static_faces_do_not_receive_synthetic_bold_or_italic():
    d = drawio.Doc({('Example', True, True): 'EmbeddedBoldItalic'})
    d.vertex('label', 'X', 'fontFamily=Example;fontStyle=3;', 0, 0, 10, 10)
    cell = ET.fromstring(d.cells[-1])
    assert 'fontFamily=EmbeddedBoldItalic;' in cell.get('style')
    assert 'fontStyle=0;' in cell.get('style')


def test_embedded_drawio_faces_render_without_installed_fonts(toy_copy, tmp_path):
    """An isolated browser loads every embedded face and measures its real X glyph advances."""
    import os
    import shutil
    import subprocess
    from fontTools.ttLib import TTFont
    browser = os.environ.get('DIAGRAM_ELECTRON') or shutil.which('electron')
    if not browser:
        pytest.skip('set DIAGRAM_ELECTRON to an Electron executable for browser font verification')
    loaded = C.Case(toy_copy, trust=True)
    root = ET.parse(drawio.write(loaded)).getroot()
    embedded = root.find('.//mxGraphModel').get('extFonts', '')
    assert embedded
    faces = []
    for item in embedded.split('|'):
        family, uri = item.split('^', 1)
        font = TTFont(io.BytesIO(base64.b64decode(uri.split(',', 1)[1])))
        glyph = font.getBestCmap()[ord('X')]
        expected = font['hmtx'].metrics[glyph][0] * 5 * 14 / font['head'].unitsPerEm
        faces.append({'family': family, 'uri': uri, 'expected': expected})
    data = tmp_path / 'faces.json'
    data.write_text(json.dumps(faces), encoding="utf-8", newline="\n")
    runner = tmp_path / 'fonts.cjs'
    runner.write_text('''// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
// Measure embedded static faces in a browser without system fonts.
const {app, BrowserWindow} = require('electron');
const fs = require('node:fs');
app.whenReady().then(async () => {
  const win = new BrowserWindow({show:false, webPreferences:{sandbox:true}});
  await win.loadURL('data:text/html,<html><body></body></html>');
  const faces = JSON.parse(fs.readFileSync(process.argv[process.argv.length - 1], 'utf8'));
  const result = await win.webContents.executeJavaScript(`(async () => {
    const faces = ${JSON.stringify(faces)};
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const result = [];
    for (const f of faces) {
      const face = new FontFace(f.family, 'url(' + f.uri + ')');
      document.fonts.add(face);
      await face.load();
      ctx.font = '14px "' + f.family + '"';
      result.push({family:f.family, loaded:face.status, width:ctx.measureText('XXXXX').width});
    }
    return result;
  })()`);
  process.stdout.write('FONT_RESULT=' + JSON.stringify(result) + '\\n');
  app.quit();
}).catch(e => { console.error(e); app.exit(1); });
''', encoding="utf-8", newline="\n")
    conf = tmp_path / 'empty-fonts.conf'
    conf.write_text('<fontconfig><match target="font">'
                    '<edit name="hinting" mode="assign"><bool>false</bool></edit>'
                    '<edit name="hintstyle" mode="assign"><const>hintnone</const></edit>'
                    '<edit name="antialias" mode="assign"><bool>true</bool></edit>'
                    '</match></fontconfig>', encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(conf))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    cmd = [browser, '--no-sandbox', '--disable-gpu', '--ozone-platform=x11', str(runner), str(data)]
    if shutil.which('xvfb-run'):
        cmd = ['xvfb-run', '-a', *cmd]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=90, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-1500:]
    match = re.search(r'FONT_RESULT=(.*)', r.stdout)
    assert match, r.stdout[-1500:] + r.stderr[-1500:]
    rendered = json.loads(match[1])
    assert len(rendered) == len(faces)
    for got, face in zip(rendered, faces):
        assert got['loaded'] == 'loaded'
        assert got['family'] == face['family']
        assert abs(got['width'] - face['expected']) < 0.02


def test_embedded_face_aliases_are_stable_across_export_times(monkeypatch):
    face = fontset.faces()[0]
    monkeypatch.setattr(fontset, "faces", lambda: [face])
    fontset._drawio_face.cache_clear()
    monkeypatch.setenv('SOURCE_DATE_EPOCH', '946684800')
    first = fontset.drawio_fonts('XXXXX')
    fontset._drawio_face.cache_clear()
    monkeypatch.setenv('SOURCE_DATE_EPOCH', '946684810')
    second = fontset.drawio_fonts('XXXXX')
    assert first == second


def test_recolour_help_works_without_optional_packages(tmp_path, engine_env):
    import subprocess
    import sys
    code = '''import builtins, pathlib, runpy, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'cv2', 'sklearn'}:
        raise ImportError('optional package deliberately unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.argv = [sys.argv[1], *sys.argv[2:]]
sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
'''
    r = subprocess.run([sys.executable, '-c', code, str(ENGINE / 'recolour.py'), '--help'],
                       env=engine_env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert '--palette' in r.stdout
    r = subprocess.run([sys.executable, '-c', code, str(ENGINE / 'recolour.py'), 'missing.png', '--palette', 'tol-bright'],
                       env=engine_env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode != 0
    assert 'optional dependency group' in r.stderr


def test_sanitised_svg_preserves_safe_fragment_references():
    source = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
              'viewBox="0 0 10 10"><defs><rect id="shape" width="3" height="3"/></defs>'
              '<use xlink:href="#shape"/></svg>')
    svg, _ = decor.themed_svg(source, {'key_fill': '#ffffff', 'flavour': 'example'})
    root = ET.fromstring(svg)
    uses = list(root.iter('{http://www.w3.org/2000/svg}use'))
    assert len(uses) == 1
    assert uses[0].get('href') == '#shape'
