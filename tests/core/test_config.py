# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Config lookup order, locate probes, merging, validation, path resolution and writing."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from ltcore import config, paths

ORIGIN = "https://lt.example.invalid"


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def platform_file() -> Path:
    return Path(str(paths.config_file()))


# ----------------------------------------------------------------- lookup order

def test_lookup_order_flag_env_cwd_platform_none(tmp_path, monkeypatch):
    cwd = tmp_path / "work"
    cwd.mkdir()
    flag = write(tmp_path / "flag.toml", 'schema_version = 1\n[institution]\nname = "flag"\n')
    env = write(tmp_path / "env.toml", 'schema_version = 1\n[institution]\nname = "env"\n')
    ws = write(cwd / "lt-suite.toml", 'schema_version = 1\n[institution]\nname = "cwd"\n')
    plat = write(platform_file(), 'schema_version = 1\n[institution]\nname = "platform"\n')
    monkeypatch.setenv("LT_SUITE_CONFIG", str(env))

    assert config.load(flag, cwd=cwd).get("institution.name") == "flag"
    assert config.locate(flag, cwd=cwd).source == "flag --config"
    assert config.load(cwd=cwd).get("institution.name") == "env"
    assert config.locate(cwd=cwd).source == "env LT_SUITE_CONFIG"
    monkeypatch.delenv("LT_SUITE_CONFIG")
    assert config.load(cwd=cwd).get("institution.name") == "cwd"
    assert config.locate(cwd=cwd).source == "cwd ./lt-suite.toml"
    ws.unlink()
    assert config.load(cwd=cwd).get("institution.name") == "platform"
    assert config.locate(cwd=cwd).source == "platform"
    plat.unlink()
    cfg = config.load(cwd=cwd)
    assert cfg.path is None and cfg.source == "none"
    assert cfg.get("institution.name") == ""
    assert config.locate(cwd=cwd).target == plat


def test_missing_flag_or_env_file_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")
    monkeypatch.setenv("LT_SUITE_CONFIG", str(tmp_path / "also-nope.toml"))
    with pytest.raises(config.ConfigError, match="env LT_SUITE_CONFIG"):
        config.load()


def test_relative_flag_resolves_against_cwd(tmp_path):
    write(tmp_path / "sub" / "c.toml", "schema_version = 1\n")
    loc = config.locate("sub/c.toml", cwd=tmp_path)
    assert loc.path == tmp_path / "sub" / "c.toml" and loc.exists


# ----------------------------------------------------------------- merge and origins

def test_defaults_merge_and_origins(tmp_path):
    f = write(tmp_path / "c.toml", f'[lt]\norigin = "{ORIGIN}"\n[fill.save_wait]\nmax_ms = 9000\n')
    cfg = config.load(f)
    assert cfg.get("lt.origin") == ORIGIN
    assert cfg.get("fill.save_wait.max_ms") == 9000
    assert cfg.get("fill.save_wait.quiet_ms") == 300          # sibling default kept
    assert cfg.get("fill.max_pages_per_call") == 5
    assert cfg.where("lt.origin") == f"config {f}"
    assert cfg.where("fill.save_wait.quiet_ms") == "default"
    assert cfg.where("fill.save_wait") == "mixed"
    assert "lt.origin" in cfg and "lt.nope" not in cfg
    with pytest.raises(KeyError):
        cfg.get("lt.nope")


def test_arrays_replace_and_item_defaults_fill(tmp_path):
    f = write(tmp_path / "c.toml", '[[tiers.tier]]\nid = 1\nlabel = "Only"\ntag_template = "[T1:{source}]"\n')
    cfg = config.load(f)
    tiers = cfg.get("tiers.tier")
    assert len(tiers) == 1
    assert tiers[0]["requires_accessed"] is False and tiers[0]["issuer_order"] == []


def test_empty_default_table_gains_user_keys(tmp_path):
    f = write(tmp_path / "c.toml", '[export.citations.page_aliases]\nSRC-1 = "Notes/Page"\n')
    cfg = config.load(f)
    assert cfg.get("export.citations.page_aliases") == {"SRC-1": "Notes/Page"}
    assert cfg.where("export.citations.page_aliases.SRC-1").startswith("config ")


def test_style_defaults_layer(tmp_path, monkeypatch):
    style = write(tmp_path / "style.defaults.toml",
                  '[style]\npalettes = ["pal-a"]\nprint_palette = "pal-a"\ncustom_palettes_file = ""\n')
    monkeypatch.setattr(config, "STYLE_DEFAULTS_FILE", style)
    cfg = config.load(write(tmp_path / "c.toml", "schema_version = 1\n"))
    assert cfg.get("style.palettes") == ["pal-a"]
    assert cfg.where("style.palettes") == "style default"
    monkeypatch.setattr(config, "STYLE_DEFAULTS_FILE", tmp_path / "absent.toml")
    assert "style" not in config.load(write(tmp_path / "d.toml", "")).values


# ----------------------------------------------------------------- relative paths

def test_relative_paths_resolve_against_the_config_file(tmp_path, monkeypatch):
    # "~" reads HOME on Linux and macOS and USERPROFILE on Windows.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    d = tmp_path / "cfgdir"
    f = write(d / "c.toml", '[paths]\ndata_dir = "data"\ncache_dir = "~/cache"\n'
              'captures_dir = "caps"\n[curriculum]\nroot = "../course"\n'
              f'learning_outcomes_file = "outcomes.md"\n[notes]\ngraph_path = "{(tmp_path / "abs-graph").as_posix()}"\n')
    cfg = config.load(f, cwd=tmp_path / "elsewhere")
    assert cfg.get("paths.data_dir") == str(d / "data")
    assert cfg.get("paths.cache_dir") == str(tmp_path / "home" / "cache")
    assert cfg.get("paths.captures_dir") == "caps"        # data-relative: resolved by Store
    assert cfg.get("curriculum.root") == str(d / ".." / "course")
    assert cfg.get("curriculum.learning_outcomes_file") == str(d / ".." / "course" / "outcomes.md")
    assert Path(cfg.get("notes.graph_path")) == tmp_path / "abs-graph"
    assert cfg.get("rat.answers_dir") == ""                # empty stays empty


# ----------------------------------------------------------------- validation

@pytest.mark.parametrize("text, needle", [
    ('[lt]\norigni = "x"\n', "lt.origni: unknown key (did you mean 'origin'?)"),
    ("[fill]\nmax_pages_per_call = 0\n", "below the minimum"),
    ('[fill]\nmax_pages_per_call = "5"\n', "expected integer"),
    ('[notes]\nformat = "word"\n', "is not one of"),
    ('[lt]\norigin = "http://plain.example.invalid"\n', "does not match"),
    ('[lt]\norigin = "https://lt.example.invalid/i/x"\n', "does not match"),
    ('[curriculum]\nweek_number_pattern = "(unclosed"\n', "not a valid regular expression"),
    ('[curriculum]\nweek_number_pattern = "week"\n', "capture group"),
    ('[curriculum]\nweek_dir_pattern = "^week"\n', "{num}"),
    ('[[text_cleaners]]\nname = "c"\nstage = "pdf"\nargv = ["x"]\n', "needs {pdf}"),
    ('[[text_cleaners]]\nname = "c"\nstage = "fill-text"\nargv = ["x", "{in}"]\n', "{in} and {out}"),
    ('[[text_cleaners]]\nstage = "pdf"\nargv = ["x", "{pdf}"]\n', "name: required key is missing"),
    ('[[tiers.tier]]\nid = 1\nlabel = "a"\ntag_template = "t"\n[[tiers.tier]]\nid = 1\nlabel = "b"\ntag_template = "u"\n',
     "id must be unique"),
    ('[annotate]\nink = "yellow"\n', "does not match"),
    ("schema_version = 2\n", "newer than this lt-suite reads"),
    ('[style]\npalettes = []\n', "at least 1 item"),
    ('[model_answer]\nread = "yes"\n', "expected boolean"),
    ('[export.citations.url_aliases]\nX = "ftp://x"\n', "does not match"),
    ('notes = "flat"\n', "expected table"),
])
def test_invalid_configs_fail_with_named_key(tmp_path, text, needle):
    f = write(tmp_path / "bad.toml", text)
    with pytest.raises(config.ConfigError) as ei:
        config.load(f)
    assert needle in str(ei.value)
    assert str(f) in str(ei.value)


def test_bad_toml_is_a_config_error(tmp_path):
    f = write(tmp_path / "bad.toml", "[lt\norigin=")
    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.load(f)


def test_warnings_do_not_fail(tmp_path):
    f = write(tmp_path / "w.toml", "[export.outputs]\nwhiteboard = true\nnotes_graph = true\n"
              '[rat]\ntrat = "from_dir"\n')
    cfg = config.load(f)
    joined = " ".join(cfg.warnings)
    assert "notes.graph_path is empty" in joined and "rat.answers_dir is empty" in joined
    assert config.load(write(tmp_path / "w2.toml", "[export.outputs]\nwhiteboard = true\n")).warnings


def test_validate_false_keeps_errors(tmp_path):
    f = write(tmp_path / "bad.toml", "[lt]\nbogus = 1\n")
    cfg = config.load(f, validate=False)
    assert any("unknown key" in e for e in cfg.errors)


@pytest.mark.parametrize("name", sorted(p.relative_to(Path(__file__).resolve().parents[2])
                                                for p in [(Path(__file__).resolve().parents[2] / "examples/configs/minimal.toml"),
                                                          *(Path(__file__).resolve().parents[2] / "examples").glob("*/config.toml")]))
def test_shipped_examples_validate(repo_root, name):
    cfg = config.load(repo_root / name)
    assert cfg.errors == [] and cfg.warnings == []


def test_require_exits_3_naming_the_file(tmp_path, capsys):
    f = write(tmp_path / "c.toml", "schema_version = 1\n")
    cfg = config.load(f)
    with pytest.raises(SystemExit) as ei:
        cfg.require("lt.origin")
    assert ei.value.code == 3
    err = capsys.readouterr().err
    assert f"lt.origin is not set. Run the lt-suite-setup skill or set it in {f}." in err
    nofile = config.load(cwd=tmp_path)
    with pytest.raises(SystemExit):
        nofile.require("lt.origin")
    assert str(platform_file()) in capsys.readouterr().err
    assert config.load(write(tmp_path / "o.toml", f'[lt]\norigin = "{ORIGIN}"\n')).require("lt.origin") == ORIGIN


def test_digest_tracks_effective_values(tmp_path):
    a = config.load(write(tmp_path / "a.toml", "schema_version = 1\n"))
    b = config.load(write(tmp_path / "b.toml", "[fill]\nmax_pages_per_call = 5\n"))
    c = config.load(write(tmp_path / "c.toml", "[fill]\nmax_pages_per_call = 2\n"))
    assert a.digest() == b.digest() != c.digest()
    assert len(a.digest()) == 64


# ----------------------------------------------------------------- env vars

def test_no_install_env(monkeypatch):
    assert config.no_install() is False
    monkeypatch.setenv("LT_SUITE_NO_INSTALL", "1")
    assert config.no_install() is True
    assert config.no_install({"LT_SUITE_NO_INSTALL": "0"}) is False


# ----------------------------------------------------------------- locate probes

def test_locate_report_all_probes_pass(tmp_path):
    rep = config.locate_report(cwd=tmp_path)
    assert set(rep["probes"]) == {"config_dir", "data_dir", "loopback"}
    assert rep["proposal"]["workspace"] is False
    assert rep["proposal"]["path"] == str(platform_file())
    assert rep["config"]["exists"] is False


def test_locate_report_failed_probe_proposes_workspace(tmp_path, monkeypatch):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    # Put the platform config folder under a file on every OS: Linux reads XDG_CONFIG_HOME,
    # Windows reads APPDATA, macOS builds ~/Library/Application Support from HOME.
    for name in ("XDG_CONFIG_HOME", "APPDATA", "HOME"):
        monkeypatch.setenv(name, str(blocker / "sub"))
    rep = config.locate_report(cwd=tmp_path)
    assert rep["probes"]["config_dir"]["ok"] is False
    prop = rep["proposal"]
    assert prop["workspace"] is True
    assert prop["path"] == str(tmp_path / "lt-suite.toml")
    assert prop["values"] == {"paths": {"data_dir": ".lt-suite/data", "downloads_mode": "copy"},
                              "browser": {"transport": "download"}}
    # the proposal is a valid config
    config.build(prop["values"], config.locate(cwd=tmp_path))


def test_locate_report_loopback_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "probe_loopback", lambda: (False, "cannot bind 127.0.0.1: denied"))
    rep = config.locate_report(cwd=tmp_path)
    assert rep["probes"]["loopback"]["ok"] is False and rep["proposal"]["workspace"] is True


def test_locate_report_explicit_config_is_kept(tmp_path):
    f = write(tmp_path / "mine.toml", "schema_version = 1\n")
    rep = config.locate_report(f, cwd=tmp_path,
                               probes={"config_dir": {"ok": False}, "data_dir": {"ok": True},
                                       "loopback": {"ok": True}})
    assert rep["proposal"]["workspace"] is False and rep["proposal"]["path"] == str(f)


def test_probe_dir(tmp_path):
    ok, _ = config.probe_dir(tmp_path / "a" / "b")
    assert ok and not (tmp_path / "a").exists()          # probing creates nothing
    assert not list(tmp_path.iterdir())
    f = tmp_path / "f"
    f.write_text("x")
    assert config.probe_dir(f / "sub")[0] is False


# ----------------------------------------------------------------- write

def test_write_refuses_then_force_keeps_bak(tmp_path):
    target = tmp_path / "cfg" / "config.toml"
    doc = {"schema_version": 1, "lt": {"origin": ORIGIN}}
    assert config.write(doc, target) == target
    assert tomllib.loads(target.read_text(encoding="utf-8")) == doc
    with pytest.raises(config.ConfigError, match="--force"):
        config.write({"schema_version": 1}, target)
    config.write({"schema_version": 1, "institution": {"name": "New"}}, target, force=True)
    assert tomllib.loads(target.read_text(encoding="utf-8"))["institution"]["name"] == "New"
    assert tomllib.loads((tmp_path / "cfg" / "config.toml.bak").read_text(encoding="utf-8")) == doc
    assert sorted(p.name for p in target.parent.iterdir()) == ["config.toml", "config.toml.bak"]


def test_write_validates_first(tmp_path):
    target = tmp_path / "c.toml"
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.write({"lt": {"bogus": 1}}, target)
    assert not target.exists()


def test_write_default_target_is_platform_file(tmp_path):
    out = config.write({"schema_version": 1}, cwd=tmp_path)
    assert out == platform_file() and out.is_file()


def test_written_file_has_header_and_reads_back(tmp_path):
    target = config.write({"schema_version": 1, "curriculum": {"week_dir_pattern": r"^{num}\b"}},
                          tmp_path / "c.toml")
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# lt-suite config")
    assert config.load(target).get("curriculum.week_dir_pattern") == r"^{num}\b"


# ----------------------------------------------------------------- CLI

def test_cli_validate_examples(run_py, repo_root):
    for name in [repo_root / "examples/configs/minimal.toml",
                 *sorted((repo_root / "examples").glob("*/config.toml"))]:
        r = run_py("-m", "ltcore.config", "validate", "--config", name)
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout.startswith("OK ")


def test_cli_validate_invalid_and_missing(run_py, tmp_path):
    bad = write(tmp_path / "bad.toml", "[lt]\nbogus = 1\n")
    r = run_py("-m", "ltcore.config", "validate", "--config", str(bad))
    assert r.returncode == 1 and "INVALID" in r.stdout and "lt.bogus" in r.stdout
    r = run_py("-m", "ltcore.config", "validate", "--config", str(tmp_path / "none.toml"))
    assert r.returncode == 3 and "not found" in r.stderr


def test_cli_get_show_path_and_direct_run(run_py, tmp_path, repo_root):
    f = write(tmp_path / "c.toml", f'[lt]\norigin = "{ORIGIN}"\n')
    r = run_py("-m", "ltcore.config", "get", "lt.origin", "--config", str(f))
    assert r.returncode == 0 and r.stdout.strip() == ORIGIN
    r = run_py("-m", "ltcore.config", "--config", str(f), "get", "fill.max_pages_per_call")
    assert r.returncode == 0 and json.loads(r.stdout) == 5
    r = run_py("-m", "ltcore.config", "get", "lt.nope", "--config", str(f))
    assert r.returncode == 1 and "unknown key lt.nope" in r.stderr
    r = run_py("-m", "ltcore.config", "show", "--config", str(f))
    assert tomllib.loads(r.stdout)["lt"]["origin"] == ORIGIN
    r = run_py("-m", "ltcore.config", "show", "--origins", "--config", str(f))
    assert f'lt.origin = "{ORIGIN}"  # config {f}' in r.stdout
    r = run_py("-m", "ltcore.config", "path", "--config", str(f))
    assert r.returncode == 0 and str(f) in r.stdout and "flag --config" in r.stdout
    # run as a file path from another folder: the module adds its grandparent to sys.path
    r = run_py(str(repo_root / "shared" / "ltcore" / "config.py"), "get", "fill.max_pages_per_call",
               "--config", str(f), cwd=tmp_path, shared_on_path=False)
    assert r.returncode == 0 and r.stdout.strip() == "5"


@pytest.mark.parametrize("key", ["institution.name", "tiers.reference_set"])
def test_cli_get_unset_key_exits_3(run_py, tmp_path, key):
    """An empty string or empty list is unset: nothing on stdout, exit 3, hint names the file."""
    f = write(tmp_path / "c.toml", f'[lt]\norigin = "{ORIGIN}"\n')
    r = run_py("-m", "ltcore.config", "get", key, "--config", str(f))
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout == ""
    assert f"{key} is not set. Run the lt-suite-setup skill or set it in {f}." in r.stderr


def test_cli_get_unset_key_without_config_file(run_py, tmp_path):
    """With no config file anywhere, an unset key exits 3 and names the file setup would write."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    r = run_py("-m", "ltcore.config", "get", "lt.origin", cwd=cwd)
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout == ""
    assert "lt.origin is not set. Run the lt-suite-setup skill or set it in " in r.stderr
    r = run_py("-m", "ltcore.config", "get", "fill.max_pages_per_call", cwd=cwd)
    assert r.returncode == 0 and r.stdout.strip() == "5"


def test_cli_write_stdout_and_force(run_py, tmp_path):
    answers = write(tmp_path / "answers.json", json.dumps({"lt": {"origin": ORIGIN}}))
    r = run_py("-m", "ltcore.config", "write", "--from", str(answers), "--stdout")
    assert r.returncode == 0 and tomllib.loads(r.stdout) == {"lt": {"origin": ORIGIN}}
    target = tmp_path / "out" / "config.toml"
    r = run_py("-m", "ltcore.config", "write", "--from", str(answers), "--config", str(target))
    assert r.returncode == 0 and target.is_file()
    r = run_py("-m", "ltcore.config", "write", "--from", str(answers), "--config", str(target))
    assert r.returncode == 1 and "--force" in r.stderr
    r = run_py("-m", "ltcore.config", "write", "--from", str(answers), "--config", str(target), "--force")
    assert r.returncode == 0 and (target.parent / "config.toml.bak").is_file()
    bad = write(tmp_path / "bad.toml", "[lt]\nbogus = 1\n")
    r = run_py("-m", "ltcore.config", "write", "--from", str(bad), "--stdout")
    assert r.returncode == 1 and "unknown key" in r.stderr


def test_cli_locate_json(run_py, tmp_path):
    r = run_py("-m", "ltcore.config", "locate", cwd=tmp_path)
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert {"config", "probes", "proposal"} <= set(rep)


def test_env_config_used_by_cli(run_py, tmp_path):
    f = write(tmp_path / "env.toml", '[institution]\nname = "From env"\n')
    r = run_py("-m", "ltcore.config", "get", "institution.name", env={"LT_SUITE_CONFIG": str(f)})
    assert r.stdout.strip() == "From env"
    assert os.environ.get("LT_SUITE_CONFIG") is None
