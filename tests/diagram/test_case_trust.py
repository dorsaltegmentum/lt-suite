# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The case trust rule: a case folder is Python the engine imports, so the engine runs it only from the
lt-suite data folder or the skill's examples/, or after explicit hash approval.
"""
import os

import pytest

import case as C
from diagram_testkit import ENGINE, TOY, copy_case, run_engine


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
    (case / "content.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n" + (case / "content.py").read_text())
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
