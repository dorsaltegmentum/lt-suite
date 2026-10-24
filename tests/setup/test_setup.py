# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise setup commands, offline bootstrap, configuration and browser receipts."""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/lt-suite-setup/scripts"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("wp11_setup", SCRIPTS / "setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LT_SUITE_DATA", str(tmp_path / "data"))
    return mod


def test_help_starts_with_stdlib_only(run_py, tmp_path):
    # A dependency imported before bootstrap would break a first install.
    p = run_py("-S", str(SCRIPTS / "setup.py"), "--help", cwd=tmp_path,
               shared_on_path=False)
    assert p.returncode == 0, p.stderr
    assert "bootstrap" in p.stdout
    ast.parse((SCRIPTS / "setup.py").read_text(), feature_version=(3, 8))


@pytest.mark.skipif(os.name == "nt", reason="POSIX exec boundary; Windows uses subprocess.call")
@pytest.mark.parametrize("data_source", ["env", "flag"])
def test_doctor_reexecutes_prepared_environment_when_packages_are_missing(run_py, tmp_path, data_source):
    # -S removes site packages. Intercept only execve, retaining real bootstrap lookup.
    data = tmp_path / "data"
    py = data / "venv/bin/python"
    py.parent.mkdir(parents=True)
    py.symlink_to(sys.executable)
    snippet = '''import json, os, runpy, sys
def reexec(path, argv, env):
    print(json.dumps({"reexec": path, "argv": argv,
                      "guard": env.get("LT_SUITE_BOOTSTRAPPED")}))
    raise SystemExit(0)
os.execve = reexec
sys.argv = [sys.argv[1], "doctor"] + json.loads(sys.argv[2])
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    extra = ["--data-dir", str(data)] if data_source == "flag" else []
    env = {"LT_SUITE_DATA": str(data)} if data_source == "env" else {}
    p = run_py("-S", "-c", snippet, str(SCRIPTS / "setup.py"), json.dumps(extra),
               env=env, shared_on_path=False, cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    receipt = json.loads(p.stdout)
    assert receipt["reexec"] == str(py)
    assert receipt["guard"] == "1"
    assert receipt["argv"][1:] == [str(SCRIPTS / "setup.py"), "doctor"] + extra


def test_old_python_reaches_actionable_gate(setup, monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "version_info", (3, 9, 0))
    with pytest.raises(SystemExit) as e:
        setup.main(["write", "--answers", "missing.json"])
    assert e.value.code == 2
    assert "Python 3.11" in capsys.readouterr().err


def test_old_python_can_plan_bootstrap(setup, monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "version_info", (3, 9, 0))
    monkeypatch.setattr(setup, "find_python", lambda flag: ("python3.11", (3, 11, 9), "PATH"))
    assert setup.main(["bootstrap", "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["python_version"] == "3.11.9"
    assert not Path(plan["venv"]).exists()


@pytest.mark.parametrize("before", [True, False])
def test_global_config_flag_survives_subcommand(setup, tmp_path, capsys, before):
    cfg = tmp_path / "chosen.toml"
    cfg.write_text('schema_version = 1\n')
    args = ["--config", str(cfg), "locate", "--json"] if before else [
        "locate", "--config", str(cfg), "--json"]
    assert setup.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["config"]["path"] == str(cfg)


@pytest.mark.parametrize("failed", ["config_dir", "data_dir", "loopback"])
def test_locate_workspace_on_failed_probe(setup, monkeypatch, capsys, failed):
    from ltcore import config
    monkeypatch.setattr(config, "probe_dir", lambda p: (
        (False, "denied") if (("config" in str(p)) == (failed == "config_dir")
                             and failed != "loopback") else (True, "writable")))
    monkeypatch.setattr(config, "probe_loopback", lambda: (failed != "loopback", "probe"))
    assert setup.main(["locate", "--json"]) == 0
    proposal = json.loads(capsys.readouterr().out)["proposal"]
    assert proposal["workspace"] is True
    assert proposal["values"] == {
        "paths": {"data_dir": ".lt-suite/data", "downloads_mode": "copy"},
        "browser": {"transport": "download"}}


def test_write_stdout_is_valid_and_writes_nothing(setup, tmp_path, capsys):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"lt": {"origin": "https://example.lt.app"}}))
    target = tmp_path / "result.toml"
    assert setup.main(["write", "--answers", str(answers), "--config", str(target),
                       "--stdout"]) == 0
    doc = tomllib.loads(capsys.readouterr().out)
    assert doc["lt"]["origin"] == "https://example.lt.app"
    assert not target.exists()
    assert not (tmp_path / "data").exists()


def test_write_refuses_overwrite_and_force_keeps_backup(setup, tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text('{"institution": {"name": "Example University"}}')
    target = tmp_path / "config.toml"
    args = ["write", "--answers", str(answers), "--config", str(target)]
    assert setup.main(args) == 0
    original = target.read_bytes()
    answers.write_text('{"institution": {"name": "Example College"}}')
    assert setup.main(args) == 1
    assert target.read_bytes() == original
    assert setup.main(args + ["--force"]) == 0
    assert target.with_suffix(".toml.bak").read_bytes() == original
    assert tomllib.loads(target.read_text())["institution"]["name"] == "Example College"


@pytest.mark.parametrize("doc", [{"fonts": {"text_px": "large"}},
                                  {"lt": {"origin": "https://name:password" + chr(64) + "example.lt.app"}},
                                  {"api_token": "example"}])
def test_write_rejects_invalid_or_credential_answers(setup, tmp_path, doc):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(doc))
    target = tmp_path / "config.toml"
    assert setup.main(["write", "--answers", str(answers), "--config", str(target)]) == 1
    assert not target.exists()


def test_write_workspace_layout(setup, tmp_path, monkeypatch):
    monkeypatch.delenv("LT_SUITE_DATA")
    answers = tmp_path / "answers.json"
    answers.write_text("{}")
    assert setup.main(["write", "--answers", str(answers), "--workspace"]) == 0
    doc = tomllib.loads((tmp_path / "lt-suite.toml").read_text())
    assert doc["paths"]["data_dir"] == ".lt-suite/data"
    assert doc["paths"]["downloads_mode"] == "copy"
    assert doc["browser"]["transport"] == "download"


def test_bootstrap_stub_uv_builds_and_installs_union(setup, tmp_path, monkeypatch):
    # The subprocess boundary is simulated; no package or interpreter is downloaded.
    root = tmp_path / "skills"
    own = root / "lt-suite-setup"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("example")
    (own / "requirements.txt").write_text("jsonschema>=4.23\n")
    monkeypatch.setattr(setup, "SKILL_DIR", own)
    for name, req in [("lt-module-answer", "jsonschema>=4.23\n"),
                      ("lt-module-export", "Pillow>=11.0\nreportlab>=4.2\n")]:
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("example")
        (d / "requirements.txt").write_text(req)
    monkeypatch.setattr(setup.shutil, "which", lambda n: "stub-uv" if n == "uv" else None)
    real_py = sys.executable
    monkeypatch.setattr(setup, "python_info", lambda cmd: ((3, 11, 9), real_py))
    installed = []

    def fake_run(argv, **kwargs):
        if argv[1:3] == ["python", "find"]:
            return 0, real_py + "\n", ""
        if argv[1] == "venv":
            py = setup.venv_python_path(tmp_path / "data")
            py.parent.mkdir(parents=True)
            py.touch()
            return 0, "", ""
        if argv[1:3] == ["pip", "install"]:
            installed.extend(Path(argv[-1]).read_text().splitlines())
            assert "--require-hashes" in argv
            assert "--only-binary" in argv and ":all:" in argv
            assert argv[3] == "--python"
            assert argv[4] == str(setup.venv_python_path(tmp_path / "data"))
            return 0, "", ""
        raise AssertionError(argv)

    monkeypatch.setattr(setup, "run", fake_run)
    monkeypatch.setattr(setup, "_modules_missing", lambda py, names: [] if installed else names)
    assert setup.main(["bootstrap", "--skills-dir", str(root), "--network", "yes"]) == 0
    assert "reportlab>=4.2" in installed
    assert installed.count("Pillow>=11.0") == 1


def test_bootstrap_offline_prints_one_terminal_command(setup, monkeypatch, tmp_path, capsys):
    py = setup.venv_python_path(tmp_path / "data")
    py.parent.mkdir(parents=True)
    py.touch()
    monkeypatch.setattr(setup, "find_python", lambda flag: (sys.executable, (3, 11, 9), "PATH"))
    monkeypatch.setattr(setup, "python_info", lambda cmd: ((3, 11, 9), str(py)))
    monkeypatch.setattr(setup, "_modules_missing", lambda *args: ["PIL"])
    assert setup.main(["bootstrap", "--network", "no"]) == 3
    err = capsys.readouterr().err
    assert "normal terminal" in err
    assert "bootstrap --data-dir" in err


def test_doctor_real_fonts_and_palettes(setup, capsys):
    assert setup.main(["doctor", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["FAIL"] == 0
    assert sum(r["status"] == "PASS" and r["part"] == "fonts"
               for r in result["results"]) == 5


def test_doctor_require_escalates_warning(setup, monkeypatch, capsys):
    monkeypatch.setattr(setup.shutil, "which", lambda n: None)
    assert setup.main(["doctor", "--require", "binaries", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert any(r["part"] == "binaries" and r["status"] == "FAIL" for r in result["results"])


def test_doctor_crash_has_exit_two(setup, monkeypatch):
    def broken(ctx):
        raise RuntimeError("example failure")
    monkeypatch.setattr(setup, "check_data", broken)
    assert setup.main(["doctor"]) == 2


def test_doctor_early_crash_has_exit_two(setup, monkeypatch):
    def broken(*args):
        raise OSError("unreadable environment")
    monkeypatch.setattr(setup, "resolve_data_dir", broken)
    assert setup.main(["doctor"]) == 2


def test_ping_renders_guard_and_records_only_verified_directory(setup, tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('schema_version = 1\n[lt]\norigin = "https://example.lt.app"\n')
    out = tmp_path / "js"
    assert setup.main(["ping", "render", "--config", str(cfg), "--out-dir", str(out)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["guard"]
    assert Path(receipt["path"]).is_file()
    args = ["ping", "verify", "--config", str(cfg), "--record-dir", str(out), "--reply"]
    bad = json.dumps({"ok": False, "fatal": "forbidden-route"})
    assert setup.main(args + [bad]) == 1
    assert "js_tmp_dir" not in tomllib.loads(cfg.read_text()).get("browser", {})
    good = json.dumps({"ok": True, "origin": "https://example.lt.app", "lt_client": True})
    assert setup.main(args + [good]) == 0
    assert tomllib.loads(cfg.read_text())["browser"]["js_tmp_dir"] == str(out)


@pytest.mark.parametrize("origin", [None, "https://other.example.org"])
def test_ping_refuses_missing_or_wrong_origin(setup, tmp_path, origin):
    cfg = tmp_path / "config.toml"
    cfg.write_text('schema_version = 1\n[lt]\norigin = "https://example.lt.app"\n')
    original = cfg.read_bytes()
    reply = {"ok": True, "lt_client": True}
    if origin:
        reply["origin"] = origin
    assert setup.main(["ping", "verify", "--config", str(cfg), "--record-dir", str(tmp_path),
                       "--reply", json.dumps(reply)]) == 1
    assert cfg.read_bytes() == original


def test_fetch_font_requires_confirmation_and_rejects_tampering(setup, monkeypatch, tmp_path):
    import _catalog
    from ltcore import fonts
    monkeypatch.setattr(_catalog, "fonts_module", lambda: fonts)
    entry = {"id": "example-font", "family": "Example Font", "role": "sans",
             "licence": {"spdx": "OFL-1.1"},
             "fetch": {"files": [{"url": "https://example.org/font.ttf", "file": "font.ttf",
                                   "sha256": "0" * 64}]}}
    monkeypatch.setattr(fonts, "catalog", lambda: {"example-font": entry})
    args = setup.build_parser().parse_args(["fetch-font", "example-font", "--config", "",
                                           "--data-dir", str(tmp_path / "data")])
    args.argv = []
    calls = []

    def opener(*a, **kw):
        calls.append(a[0])
        return io.BytesIO(b"tampered")

    assert setup.cmd_fetch_font(args, opener=opener) == 3
    assert calls == []
    args.yes = True
    assert setup.cmd_fetch_font(args, opener=opener) == 1
    assert calls == ["https://example.org/font.ttf"]
    assert not (tmp_path / "data/fonts/example-font").exists()


def test_set_changes_one_key_and_keeps_a_backup(setup, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('schema_version = 1\n[lt]\norigin = "https://example.lt.app"\n'
                   '[fonts]\nsans = "Noto Sans"\n')
    original = cfg.read_bytes()
    assert setup.main(["set", "fonts.mono", "Noto Sans Mono", "--config", str(cfg)]) == 0
    assert setup.main(["set", "style.palettes", '["tol-medium-contrast"]', "--config", str(cfg)]) == 0
    doc = tomllib.loads(cfg.read_text())
    assert doc["fonts"] == {"sans": "Noto Sans", "mono": "Noto Sans Mono"}
    assert doc["style"]["palettes"] == ["tol-medium-contrast"]
    assert doc["lt"]["origin"] == "https://example.lt.app"
    assert cfg.with_suffix(".toml.bak").is_file()
    assert original != cfg.read_bytes()


def test_set_refuses_missing_file_credentials_and_bad_keys(setup, tmp_path):
    missing = tmp_path / "none.toml"
    assert setup.main(["set", "fonts.sans", "Noto Sans", "--config", str(missing)]) == 1
    assert not missing.exists()
    cfg = tmp_path / "config.toml"
    cfg.write_text("schema_version = 1\n")
    before = cfg.read_bytes()
    assert setup.main(["set", "browser.api_token", "example", "--config", str(cfg)]) == 1
    assert setup.main(["set", "not a key", "x", "--config", str(cfg)]) == 2
    assert setup.main(["set", "fonts.text_px", '"large"', "--config", str(cfg)]) == 1
    assert cfg.read_bytes() == before


@pytest.mark.parametrize("state", ["missing", "unreadable", "empty", "comments-only"])
@pytest.mark.parametrize("skill", ["lt-suite-setup", "lt-module-answer"])
def test_doctor_fails_closed_for_invalid_requirements_manifest(
    setup, monkeypatch, tmp_path, capsys, state, skill
):
    # Missing dependency evidence must never become a successful package check.
    root = tmp_path / "installed"
    for name in ("lt-suite-setup", "lt-module-answer"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("example")
        (folder / "requirements.txt").write_text("jsonschema>=4.23\n")
    monkeypatch.setattr(setup, "SKILL_DIR", root / "lt-suite-setup")
    manifest = root / skill / "requirements.txt"
    if state == "missing":
        manifest.unlink()
    elif state == "empty":
        manifest.write_text("")
    elif state == "comments-only":
        manifest.write_text("# dependencies\n")
    else:
        read = Path.read_text

        def denied(path, *args, **kwargs):
            if path == manifest:
                raise PermissionError("example permission denied")
            return read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    assert setup.main(["doctor", "--skills-dir", str(root), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(r["part"] == "packages" and r["status"] == "FAIL"
               and str(manifest) in r["detail"] for r in report["results"])


@pytest.mark.parametrize("unsafe", ["permissions", "symlink", "owner"])
@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX directory ownership")
def test_doctor_uses_renderer_directory_safety_checks(setup, tmp_path, monkeypatch, unsafe):
    # A folder refused by rendering must also fail doctor without --require.
    from ltcore import ltjs
    folder = tmp_path / "js"
    folder.mkdir(mode=0o700)
    if unsafe == "permissions":
        folder.chmod(0o755)
    elif unsafe == "symlink":
        link = tmp_path / "linked-js"
        link.symlink_to(folder, target_is_directory=True)
        folder = link
    else:
        lstat = os.lstat

        def other_owner(path, *args, **kwargs):
            st = lstat(path, *args, **kwargs)
            if Path(path) == folder:
                fields = list(st)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return st

        monkeypatch.setattr(ltjs.os, "lstat", other_owner)
    cfg = {"browser.adapter": "manual-paste", "browser.js_tmp_dir": str(folder)}
    with pytest.raises(ltjs.RenderRefused):
        ltjs.private_dir(folder)
    results = setup.check_browser({"cfg": cfg})
    assert any(r.status == "FAIL" and "unsafe js_tmp_dir" in r.text for r in results)


def test_doctor_checks_default_js_directory(setup, tmp_path, monkeypatch):
    # An unsafe default folder fails as surely as an unsafe configured folder.
    from ltcore import ltjs
    folder = tmp_path / "default-js"
    folder.mkdir(mode=0o755)
    monkeypatch.setattr(ltjs, "default_js_dir", lambda: folder)
    results = setup.check_browser({"cfg": {"browser.adapter": "manual-paste"}})
    assert any(r.status == "FAIL" for r in results)


@pytest.mark.parametrize("client", [False, None, "true", 1])
def test_ping_rejects_absent_client_without_recording_directory(setup, tmp_path, client):
    # A valid origin alone cannot authorize recording a browser directory.
    cfg = tmp_path / "config.toml"
    cfg.write_text('schema_version = 1\n[lt]\norigin = "https://example.lt.app"\n')
    before = cfg.read_bytes()
    reply = {"ok": True, "origin": "https://example.lt.app"}
    if client is not None:
        reply["lt_client"] = client
    assert setup.main(["ping", "verify", "--config", str(cfg), "--reply", json.dumps(reply),
                       "--record-dir", str(tmp_path / "js")]) == 1
    assert cfg.read_bytes() == before
    assert not cfg.with_suffix(".toml.bak").exists()


def test_bootstrap_replaces_existing_python_below_minimum(setup, tmp_path, monkeypatch):
    # A found environment using Python 3.9 must be rebuilt before reuse.
    data = tmp_path / "data"
    py = setup.venv_python_path(data)
    py.parent.mkdir(parents=True)
    py.touch()
    venv = py.parent.parent
    (venv / "pyvenv.cfg").write_text("version = 3.9.0\n")
    monkeypatch.setattr(setup, "find_python", lambda flag: (sys.executable, (3, 11, 9), "PATH"))
    monkeypatch.setattr(setup, "python_info", lambda cmd: ((3, 9, 0), str(py)))
    monkeypatch.setattr(setup, "_modules_missing", lambda *args: [])
    monkeypatch.setattr(setup.shutil, "which", lambda name: "stub-uv" if name == "uv" else None)
    calls = []

    def create(argv, **kwargs):
        calls.append(argv)
        assert argv[1] == "venv"
        assert not py.exists()
        py.parent.mkdir(parents=True)
        py.touch()
        return 0, "", ""

    monkeypatch.setattr(setup, "run", create)
    assert setup.main(["bootstrap", "--network", "no"]) == 0
    assert len(calls) == 1
    assert calls[0][-1] == str(venv)


@pytest.mark.parametrize("marker", ["directory", "worktree", "symlink-data", "symlink-venv"])
def test_bootstrap_plans_environment_outside_repositories(setup, tmp_path, monkeypatch, capsys, marker):
    # Configured workspace data and symlinks must never place a venv in a work tree.
    repo = tmp_path / "project"
    repo.mkdir()
    if marker == "worktree":
        (repo / ".git").write_text("gitdir: ../repository.git/worktrees/example\n")
    else:
        (repo / ".git").mkdir()
    data = repo / "data"
    if marker == "symlink-data":
        data = tmp_path / "linked-data"
        data.symlink_to(repo / "data", target_is_directory=True)
    elif marker == "symlink-venv":
        data = tmp_path / "external-data"
        data.mkdir()
        (data / "venv").symlink_to(repo / "environment", target_is_directory=True)
    monkeypatch.setattr(setup, "find_python", lambda flag: (sys.executable, (3, 11, 9), "PATH"))
    assert setup.main(["bootstrap", "--data-dir", str(data), "--dry-run", "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    expected = Path(os.environ["XDG_DATA_HOME"]) / "lt-suite" / "venv"
    assert Path(plan["venv"]) == expected
    assert not expected.exists()
    assert not (repo / "data").exists()


def test_runtime_finds_repository_external_bootstrap_environment(setup, tmp_path):
    # Shared runtime and setup must choose one environment for workspace data.
    import ltcore
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()
    data = repo / ".lt-suite" / "data"
    expected = Path(os.environ["XDG_DATA_HOME"]) / "lt-suite" / "venv"
    executable = Path("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert setup.venv_python_path(data) == expected / executable
    assert Path(ltcore.venv_python(str(data))) == expected / executable


def test_skill_bootstraps_written_custom_data_configuration(setup, tmp_path, monkeypatch, capsys):
    # Follow the skill's setup/write instructions with a custom final data folder.
    import re
    import shlex
    monkeypatch.delenv("LT_SUITE_DATA")
    target = tmp_path / "chosen.toml"
    data = tmp_path / "chosen-data"
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"paths": {"data_dir": str(data)}}))
    monkeypatch.setattr(setup, "find_python", lambda flag: (sys.executable, (3, 11, 9), "PATH"))
    monkeypatch.setattr(setup.shutil, "which", lambda name: "stub-uv" if name == "uv" else None)
    monkeypatch.setattr(setup, "_modules_missing", lambda *args: [])

    def create(argv, **kwargs):
        assert argv[1] == "venv"
        py = Path(argv[-1]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        py.parent.mkdir(parents=True)
        py.touch()
        return 0, "", ""

    monkeypatch.setattr(setup, "run", create)
    instructions = (SCRIPTS.parent / "SKILL.md").read_text()
    commands = re.findall(r'`python3 "\$SKILL_DIR/scripts/setup.py" ((?:bootstrap|write)[^`]*)`', instructions)
    for command in commands:
        args = shlex.split(command.replace("<answers.json>", str(answers)).replace("<path>", str(target)))
        if args[0] == "write" and "--config" not in args:
            args.extend(["--config", str(target)])
        assert setup.main(args) == 0
    expected = data / "venv" / "lt-suite-requirements.txt"
    assert expected.is_file(), "The written custom config needs its own bootstrap before doctor"
    assert "pillow" in expected.read_text().lower()
    capsys.readouterr()
    assert setup.main(["doctor", "--config", str(target), "--require", "packages", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["FAIL"] == 0
    assert any(r["part"] == "python" and "setup environment built" in r["detail"]
               for r in report["results"])



def test_bootstrap_refuses_repository_platform_fallback(setup, tmp_path, monkeypatch, capsys):
    # A platform data override inside the same repository must not bypass isolation.
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(repo / "platform-data"))
    data = repo / "data"
    assert setup.main(["bootstrap", "--data-dir", str(data), "--dry-run", "--json"]) == 1
    assert "outside a git work tree" in capsys.readouterr().err
    assert not (repo / "platform-data").exists()
    assert not data.exists()


def test_bootstrap_creates_environment_at_external_location(setup, tmp_path, monkeypatch):
    # Actual creation must use the external plan, leaving workspace data free of venvs.
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".git").mkdir()
    data = repo / "data"
    expected = Path(os.environ["XDG_DATA_HOME"]) / "lt-suite" / "venv"
    monkeypatch.setattr(setup, "find_python", lambda flag: (sys.executable, (3, 11, 9), "PATH"))
    monkeypatch.setattr(setup.shutil, "which", lambda name: "stub-uv" if name == "uv" else None)
    monkeypatch.setattr(setup, "_modules_missing", lambda *args: [])

    def create(argv, **kwargs):
        assert Path(argv[-1]) == expected
        executable = expected / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable.parent.mkdir(parents=True)
        executable.touch()
        return 0, "", ""

    monkeypatch.setattr(setup, "run", create)
    assert setup.main(["bootstrap", "--data-dir", str(data), "--network", "no"]) == 0
    assert (expected / "lt-suite-requirements.txt").is_file()
    assert not (data / "venv").exists()


def test_bootstrap_preserves_marker_variants_and_hashes(setup, tmp_path, monkeypatch):
    root = tmp_path / "skills"
    own = root / "lt-suite-setup"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("example")
    rows = [
        "numpy==2.4.0 ; python_full_version < '3.12' --hash=sha256:" + "a" * 64,
        "numpy==2.5.0 ; python_full_version >= '3.12' --hash=sha256:" + "b" * 64,
    ]
    (own / "requirements.txt").write_text("\n".join(rows) + "\n")
    monkeypatch.setattr(setup, "SKILL_DIR", own)
    required, optional, skills, conflicts = setup.collect_requirements(root)
    assert required == rows
    assert conflicts == []
    assert optional == [] and skills == ["lt-suite-setup"]


def test_doctor_uses_actual_distribution_import_names(setup):
    assert setup.import_name("rpds-py") == "rpds"


def test_doctor_lists_each_output(setup,capsys,monkeypatch):
    from ltcore import fonts
    monkeypatch.setattr(fonts,'fc_match',lambda *a,**k:None)
    monkeypatch.setattr(fonts,'scan_family',lambda *a,**k:[])
    from ltcore import config
    import _catalog
    rows=_catalog.check_fonts(config.load())
    assert len(rows)==5 and all(status=='PASS' for status,_ in rows)
    assert all(any(name in text for _,text in rows) for name in ['pdf sans','pdf mono','diagram sans','diagram mono','labels sans'])
    rows=_catalog.check_fonts({'fonts.diagram.sans':'Missing Synthetic Family'})
    assert any(status=='WARN' and 'diagram sans' in text and 'Noto Sans' in text for status,text in rows)


def test_doctor_lists_labels(setup,font_file,monkeypatch):
    from ltcore import fonts
    monkeypatch.setattr(fonts,'fc_match',lambda *a,**k:None)
    monkeypatch.setattr(fonts,'scan_family',lambda *a,**k:[])
    import _catalog
    path=font_file('Test Labels')
    rows=_catalog.check_fonts({'annotate.font_path':str(path)})
    assert any('annotate.font_path wins over fonts.labels.sans' in text for _,text in rows)
    assert any(status=='PASS' and 'labels sans' in text and 'Test Labels' in text for status,text in rows)
    rows=_catalog.check_fonts({'fonts.labels.sans':'Missing Synthetic Family'})
    assert any(status=='WARN' and 'labels sans' in text and 'Noto Sans' in text for status,text in rows)


def test_doctor_warns_google_mode_for_unhosted_family(setup,font_file):
    import _catalog
    path=font_file('Test Local')
    rows=_catalog.check_fonts({'fonts.diagram.sans':str(path),'fonts.page_fonts':'google'})
    assert any(status=='WARN' and 'Test Local' in text and 'Google-hosted' in text for status,text in rows)
    rows=_catalog.check_fonts({'fonts.page_fonts':'google'})
    assert not any('Google-hosted' in text for _,text in rows)


def test_answers_with_diagram_fonts_validate(setup,tmp_path,capsys):
    from ltcore import config
    answer=tmp_path/'answers.json'
    answer.write_text(json.dumps({'fonts':{'diagram':{'sans':'Noto Sans','mono':'Noto Sans Mono'},'labels':{'sans':'Noto Sans'}}}))
    target=tmp_path/'new.toml'
    assert setup.main(['write','--answers',str(answer),'--config',str(target)]) == 0
    assert config.load(target).get('fonts.diagram.sans')=='Noto Sans'
    assert setup.main(['set','fonts.diagram.sans','Noto Sans','--config',str(target)]) == 0
    assert setup.main(['set','fonts.labels.sans','Noto Sans','--config',str(target)]) == 0


def test_doctor_missing_annotate_path_follows_label_chain(setup):
    import _catalog
    rows=_catalog.check_fonts({'annotate.font_path':'missing.ttf'})
    assert not any(status=='FAIL' for status,_ in rows)
    assert any(status=='WARN' and 'annotate.font_path' in text and 'does not exist' in text for status,text in rows)
    assert any('labels sans: Noto Sans' in text for _,text in rows)


def test_doctor_warns_for_os_fallback(setup,monkeypatch,font_file):
    import _catalog
    from ltcore import fonts as F
    path=font_file('Test OS')
    original=F.find
    def lookup(role,cfg=None,**kw):
        if kw.get('output')=='diagram':
            return original(role,None,output='diagram')
        return F.Font(role,'Test OS',path,'system fallback (Test OS)',fallback=True,os_fallback=True)
    monkeypatch.setattr(F,'find',lookup)
    rows=_catalog.check_fonts({})
    assert sum(status=='WARN' for status,_ in rows)==3
    assert sum(status=='PASS' for status,_ in rows)==2
    assert all('system fallback' in text for status,text in rows if status=='WARN')

def test_T36_rat_doctor_safe_counts_without_sheet_reads(setup, tmp_path, monkeypatch):
    from ltcore import rat
    root = tmp_path / 'sheets'
    root.mkdir()
    (root / 'team.md').write_text('Synthetic sheet never opened by doctor')
    cfg = tmp_path / 'config.toml'
    cfg.write_text('[rat]\nanswers_dir = ' + json.dumps(str(root)) + '\nanswers_dirs = []\nirat = "from_dir"\ntrat = "from_dir"\n')
    def forbidden(*args):
        pytest.fail('doctor opened a sheet')
    monkeypatch.setattr(rat, 'read_sheet', forbidden)
    rows = setup.check_rat({'config': str(cfg)})
    text = '\n'.join(r.text for r in rows)
    assert 'rat.answers_dir' in text and 'files=1' in text and 'irat=True trat=True' in text
    assert str(root) not in text and 'Synthetic sheet' not in text


def test_T36_write_rat_keys(setup, tmp_path, capsys):
    answers = tmp_path / 'answers.json'
    answers.write_text(json.dumps({'rat': {'answers_dirs': ['sheets'], 'irat': 'from_dir', 'trat': 'ask',
                                          'kind_markers': {'trat': '(?i)team test', 'irat': '(?i)individual test'}}}))
    assert setup.main(['write', '--answers', str(answers), '--config', str(tmp_path/'config.toml'), '--stdout']) == 0
    doc = tomllib.loads(capsys.readouterr().out)
    assert doc['rat']['answers_dirs'] == ['sheets']
    assert doc['rat']['irat'] == 'from_dir'


def test_combined_feature_config_roundtrip_and_doctor(setup, tmp_path, capsys):
    """One setup config must retain all four features and expose their diagnostics."""
    from ltcore import config
    root = tmp_path / 'sheets'
    root.mkdir()
    answers = tmp_path / 'answers.json'
    values = {
        'fonts': {'system_fallback': True,
                  'pdf': {'sans': 'Noto Sans', 'mono': 'Noto Sans Mono'},
                  'diagram': {'sans': 'Noto Sans', 'mono': 'Noto Sans Mono'},
                  'labels': {'sans': 'Noto Sans'}},
        'annotate': {'palette_copies': True},
        'browser': {'adapter': 'manual-paste', 'chromium': 'brave',
                    'chromium_user_data_dir': 'synthetic-profile', 'devtools_url': ''},
        'rat': {'answers_dir': str(root), 'answers_dirs': [], 'irat': 'from_dir',
                'trat': 'from_dir', 'kind_markers': {'irat': '(?i)individual examination',
                                                   'trat': '(?i)team examination'}},
        'fill': {'check_answer': {'enabled': True}},
    }
    answers.write_text(json.dumps(values))
    target = tmp_path / 'config.toml'
    assert setup.main(['write', '--answers', str(answers), '--config', str(target)]) == 0
    cfg = config.load(target)
    assert cfg.get('fonts.pdf.sans') == 'Noto Sans'
    assert cfg.get('fonts.diagram.mono') == 'Noto Sans Mono'
    assert cfg.get('fonts.labels.sans') == 'Noto Sans'
    assert cfg.get('annotate.palette_copies') is True
    assert cfg.get('browser.chromium') == 'brave'
    assert cfg.get('browser.chromium_user_data_dir') == str(tmp_path / 'synthetic-profile')
    assert cfg.get('rat.irat') == 'from_dir'
    assert cfg.get('rat.kind_markers.trat') == '(?i)team examination'
    assert cfg.get('fill.check_answer.enabled') is True
    ctx = {'config': str(target)}
    rows = setup.check_config(ctx) + setup.check_fonts(ctx) + setup.check_browser(ctx) + setup.check_rat(ctx)
    text = '\n'.join(row.text for row in rows)
    assert not [row.text for row in rows if row.status == 'FAIL']
    for expected in ('fill.check_answer.enabled = true', 'pdf sans', 'diagram mono',
                     'labels sans', 'lt-chrome plan: serve-none', 'irat=True trat=True'):
        assert expected in text
    capsys.readouterr()


def test_first_setup_feature_question_order():
    """RAT questions precede the opt-in, then the browser route and font choices."""
    skill = (SCRIPTS.parent / 'SKILL.md').read_text()
    prompts = [
        'Do you keep RAT answer sheets',
        'Team RAT: From my sheets',
        'Individual RAT: Leave',
        'Then ask the Check Answer question alone',
        'Browser path Q1',
        'After Chromium or Automatic, ask Q2',
        'After another browser, ask Q3',
        'After Chromium or Automatic, ask Q4',
        'Only after the address option, ask Q5',
        'Ask which sans page PDFs',
        'Ask which mono page PDFs',
        'Ask whether diagrams and labels',
    ]
    assert all(skill.count(prompt) == 1 for prompt in prompts)
    positions = [skill.index(prompt) for prompt in prompts]
    assert positions == sorted(positions)


def test_check_answer_setup_question_and_default():
    from ltcore import config
    assert config.load().get('fill.check_answer.enabled') is False
    text=(Path(__file__).resolve().parents[2]/'skills/lt-suite-setup/SKILL.md').read_text()
    assert 'no press limit' in text and 'individual' in text and 'tests' in text


def test_check_answer_doctor_prints_opt_in(setup,tmp_path):
    p=tmp_path/'lt-suite.toml';p.write_text('[fill.check_answer]\nenabled=true\n')
    rows=setup.check_config({'config':p})
    assert any('fill.check_answer.enabled = true' in row.text for row in rows)
