# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The case trust rule: a case folder is Python the engine imports, so the engine runs it only from the
lt-suite data folder or the skill's examples/, or after explicit hash approval.
"""
import os
import json
import locale

import pytest

import case as C
from diagram_testkit import ENGINE, TOY, copy_case, run_engine


def test_legacy_cp1252_case_metadata_migrates(tmp_path, monkeypatch, capsys):
    folder = copy_case(tmp_path / "legacy")
    meta = folder / "case.json"
    meta.write_bytes(b'{"slug":"legacy","title":"caf\xe9"}\r\n')
    monkeypatch.setattr(locale, 'getencoding', lambda: 'cp1252')
    loaded = C.Case(folder, trust=True)
    assert loaded.meta['title'] == 'caf\u00e9'
    assert meta.read_bytes() == '{"slug":"legacy","title":"caf\u00e9"}\n'.encode('utf-8')
    assert 'UTF-8' in capsys.readouterr().err
    assert json.loads(meta.read_text(encoding='utf-8')) == loaded.meta


def test_legacy_cp1252_scaffold_migrates_after_trust(tmp_path, monkeypatch, capsys):
    folder = copy_case(tmp_path / 'legacy-source')
    source = folder / 'content.py'
    text = '# caf\u00e9\n' + source.read_text(encoding='utf-8')
    # The toy source contains characters outside cp1252; replace only those.
    legacy = text.encode('cp1252', errors='replace')
    source.write_bytes(legacy.replace(b'\n', b'\r\n'))
    monkeypatch.setattr(locale, 'getencoding', lambda: 'cp1252')
    with pytest.raises(C.CaseTrustError):
        C.Case(folder)
    assert source.read_bytes().startswith(b'# caf\xe9\r\n')
    assert C.Case(folder, trust=True).slug == 'toy-case'
    assert source.read_bytes().startswith('# caf\u00e9\n'.encode('utf-8'))
    # Migrated bytes retain the previously granted trust on the next load.
    assert C.Case(folder).slug == 'toy-case'
    assert 'UTF-8' in capsys.readouterr().err


def test_legacy_cp1252_geometry_migrates(tmp_path, monkeypatch, capsys):
    import floor
    folder = tmp_path / 'legacy-build'
    folder.mkdir()
    geometry = folder / 'geometry.json'
    geometry.write_bytes(b'{"label":"caf\xe9"}\r\n')
    monkeypatch.setattr(locale, 'getencoding', lambda: 'cp1252')
    assert floor.Build(folder).geo['label'] == 'caf\u00e9'
    assert geometry.read_bytes() == '{"label":"caf\u00e9"}\n'.encode('utf-8')
    assert 'UTF-8' in capsys.readouterr().err


@pytest.mark.parametrize('kind', ['svg', 'drawio'])
def test_legacy_cp1252_xml_migrates(tmp_path, monkeypatch, capsys, kind):
    import floor
    path = tmp_path / ('legacy.' + kind)
    raw = (b'<svg xmlns="http://www.w3.org/2000/svg"><text>caf\xe9</text></svg>\r\n'
           if kind == 'svg' else b'<mxfile title="caf\xe9"><diagram><mxGraphModel/></diagram></mxfile>\r\n')
    path.write_bytes(raw)
    monkeypatch.setattr(locale, 'getencoding', lambda: 'cp1252')
    if kind == 'svg':
        assert floor.svg_texts(path)[0]['text'] == 'caf\u00e9'
    else:
        assert floor.drawio_images(path) == []
    assert 'caf\u00e9' in path.read_bytes().decode('utf-8')
    assert b'\r\n' not in path.read_bytes()
    assert 'UTF-8' in capsys.readouterr().err


def test_external_source_is_never_rewritten_by_legacy_migration(tmp_path, monkeypatch):
    import consistency
    path = tmp_path / 'external-source.md'
    raw = b'caf\xe9\r\n'
    path.write_bytes(raw)
    monkeypatch.setattr(locale, 'getencoding', lambda: 'cp1252')
    with pytest.raises(UnicodeDecodeError):
        consistency.read_source(path)
    assert path.read_bytes() == raw


def test_examples_folder_is_trusted():
    c = C.Case(TOY)                          # examples/ is a trusted root
    assert c.slug == "toy-case"


def test_folder_outside_the_roots_is_refused(tmp_path, monkeypatch):
    case = copy_case(tmp_path / "elsewhere" / "toy-case")
    monkeypatch.chdir(ENGINE)                # the case is not under the current folder
    with pytest.raises(C.CaseError) as ex:
        C.Case(case)
    msg = str(ex.value)
    assert "--trust-case" in msg
    assert "content.py" in msg and "layout.py" in msg     # it says which files would run


def test_trust_runs_the_case_and_names_its_files(tmp_path, monkeypatch, capsys):
    case = copy_case(tmp_path / "elsewhere" / "toy-case")
    monkeypatch.chdir(ENGINE)
    c = C.Case(case, trust=True)
    assert c.slug == "toy-case"
    err = capsys.readouterr().err
    assert "--trust-case: running case code" in err
    for f in ("content.py", "layout.py", "artifact_content.py"):
        assert f in err


def test_current_folder_requires_trust(tmp_path, monkeypatch):
    case = copy_case(tmp_path / "work" / "toy-case")
    monkeypatch.chdir(tmp_path / "work")
    with pytest.raises(C.CaseError, match="--trust-case"):
        C.Case(case)


def test_data_folder_is_trusted(tmp_path, monkeypatch):
    import style
    data = tmp_path / "data"
    monkeypatch.setenv("LT_SUITE_DATA", str(data))
    monkeypatch.chdir(ENGINE)
    style.configure(None)
    try:
        case = copy_case(data / "diagrams" / "toy-case")
        assert C.Case(case).slug == "toy-case"
    finally:
        style.configure(None)


def test_untrusted_code_never_runs(tmp_path, monkeypatch):
    case = copy_case(tmp_path / "elsewhere" / "toy-case")
    marker = tmp_path / "ran"
    (case / "content.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n" + (case / "content.py").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    monkeypatch.chdir(ENGINE)
    with pytest.raises(C.CaseError):
        C.Case(case)
    assert not marker.exists()


@pytest.mark.parametrize("script", ["build.py", "layout_engine.py", "autoplace.py", "make_artifact.py",
                                    "variant_build.py", "anki_cards.py"])
def test_scripts_refuse_without_the_flag(script, tmp_path, engine_env):
    case = copy_case(tmp_path / "elsewhere" / "toy-case")
    extra = ["--variant", "core"] if script == "variant_build.py" else []
    r = run_engine(script, case, *extra, cwd=ENGINE, env=engine_env, timeout=120)
    assert r.returncode != 0
    assert "--trust-case" in (r.stdout + r.stderr)
    assert not (case / "geometry.json").exists()


def test_build_accepts_the_flag(tmp_path, engine_env):
    case = copy_case(tmp_path / "elsewhere" / "toy-case")
    r = run_engine("layout_engine.py", case, "--trust-case", cwd=ENGINE, env=engine_env, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "--trust-case: running case code" in r.stderr
    assert (case / "geometry.json").exists()
    assert os.path.getsize(case / "geometry.json") > 0
