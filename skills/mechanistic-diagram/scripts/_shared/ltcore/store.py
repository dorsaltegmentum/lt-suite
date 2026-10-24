# Generated from shared/ltcore/store.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where lt-suite keeps its files: the data folder layout and the cache folder.

Data folder, first hit wins: an explicit argument (a script's --data-dir flag),
$LT_SUITE_DATA, config paths.data_dir, then the platform default. The cache folder
follows the same order with $LT_SUITE_CACHE and paths.cache_dir.

    <data>/captures/            module-<id>.json, reference-<id>.json, media/
    <data>/incoming/            sink.py writes here; stamp_capture.py moves files on
    <data>/runs/<module_id>/    run.json, logs/, fill-js/, fill-report.json,
                                approvals.json, model-answer.json
    <data>/state/               calibration.json, ledger.json, applied.json
    <data>/lexicon/             user lexicon entries proposed by run review
    <data>/fonts/               fonts fetched by setup
    <data>/venv/                Python environment built by setup.py bootstrap

Resolving a path never creates it; ``Store.ensure()`` does. Every path reports
where it came from (flag, env LT_SUITE_DATA, config paths.data_dir, default).

    python3 -m ltcore.store [--json] [--config F] [--data-dir D]
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from typing import Iterator, Mapping  # noqa: E402

from ltcore import config as config_mod  # noqa: E402
from ltcore import paths  # noqa: E402

NEEDS: list[str] = []

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _cfg_value(cfg: "config_mod.Config | None", key: str) -> str:
    if cfg is None:
        return ""
    v = cfg.get(key, "")
    return v if isinstance(v, str) else ""


def resolve_data_dir(flag: str | os.PathLike | None = None, *,
                     cfg: "config_mod.Config | None" = None,
                     environ: Mapping[str, str] | None = None,
                     cwd: str | os.PathLike | None = None,
                     location: "config_mod.Location | None" = None) -> tuple[Path, str]:
    """(data folder, where it came from). Never creates the folder.

    Without ``cfg`` the config is located and read when that works; a broken or
    missing config falls through to the platform default here, because the caller
    that needs the config reports its errors itself.
    """
    env = os.environ if environ is None else environ
    here = Path.cwd() if cwd is None else Path(cwd)
    if flag:
        return Path(paths.expand(str(flag), here)), "flag --data-dir"
    if env.get(config_mod.ENV_DATA):
        return Path(paths.expand(env[config_mod.ENV_DATA], here)), f"env {config_mod.ENV_DATA}"
    if cfg is None:
        cfg = _try_config(env, here, location)
    v = _cfg_value(cfg, "paths.data_dir")
    if v:
        return Path(v), "config paths.data_dir"
    return Path(str(paths.data_dir(environ=env))), "default"


def resolve_cache_dir(flag: str | os.PathLike | None = None, *,
                      cfg: "config_mod.Config | None" = None,
                      environ: Mapping[str, str] | None = None,
                      cwd: str | os.PathLike | None = None) -> tuple[Path, str]:
    """(cache folder, where it came from). Never creates the folder."""
    env = os.environ if environ is None else environ
    here = Path.cwd() if cwd is None else Path(cwd)
    if flag:
        return Path(paths.expand(str(flag), here)), "flag --cache-dir"
    if env.get(config_mod.ENV_CACHE):
        return Path(paths.expand(env[config_mod.ENV_CACHE], here)), f"env {config_mod.ENV_CACHE}"
    if cfg is None:
        cfg = _try_config(env, here, None)
    v = _cfg_value(cfg, "paths.cache_dir")
    if v:
        return Path(v), "config paths.cache_dir"
    return Path(str(paths.cache_dir(environ=env))), "default"


def _try_config(env: Mapping[str, str], here: Path,
                location: "config_mod.Location | None") -> "config_mod.Config | None":
    try:
        if location is not None:
            if not location.exists:
                return None
            user = config_mod.read_toml(location.path)
            return config_mod.build(user, location, validate=False, cwd=here)
        return config_mod.load(environ=env, cwd=here, validate=False)
    except (config_mod.ConfigError, OSError):
        return None


def check_id(module_id: str) -> str:
    """A module id is used as a folder name; refuse anything that could escape it."""
    if not isinstance(module_id, str) or not _ID_RE.match(module_id) or ".." in module_id:
        raise ValueError(f"not a safe module id: {module_id!r}")
    return module_id


class Store:
    """Resolved lt-suite paths. Nothing is created until ``ensure()``."""

    def __init__(self, cfg: "config_mod.Config | None" = None, *,
                 data_dir: str | os.PathLike | None = None,
                 cache_dir: str | os.PathLike | None = None,
                 environ: Mapping[str, str] | None = None,
                 cwd: str | os.PathLike | None = None):
        env = os.environ if environ is None else environ
        here = Path.cwd() if cwd is None else Path(cwd)
        if cfg is None:
            cfg = _try_config(env, here, None)
        self.config = cfg
        self.data_dir, self.data_source = resolve_data_dir(data_dir, cfg=cfg, environ=env, cwd=here)
        self.cache_dir, self.cache_source = resolve_cache_dir(cache_dir, cfg=cfg, environ=env, cwd=here)

    def _sub(self, key: str, default: str) -> tuple[Path, str]:
        v = _cfg_value(self.config, key) or default
        from_user = self.config is not None and self.config.where(key).startswith("config")
        src = f"config {key}" if from_user else "default"
        p = Path(os.path.expanduser(v))
        if p.is_absolute():
            return p, src
        return self.data_dir / p, f"{src}, inside the data folder"

    # -- captures ---------------------------------------------------------------
    @property
    def captures_dir(self) -> Path:
        return self._sub("paths.captures_dir", "captures")[0]

    @property
    def media_dir(self) -> Path:
        return self.captures_dir / "media"

    @property
    def incoming_dir(self) -> Path:
        return self.data_dir / "incoming"

    def capture_path(self, module_id: str) -> Path:
        return self.captures_dir / f"module-{check_id(module_id)}.json"

    def reference_path(self, module_id: str) -> Path:
        return self.captures_dir / f"reference-{check_id(module_id)}.json"

    # -- runs -------------------------------------------------------------------
    @property
    def runs_dir(self) -> Path:
        return self._sub("paths.runs_dir", "runs")[0]

    def run_dir(self, module_id: str) -> Path:
        return self.runs_dir / check_id(module_id)

    def run_path(self, module_id: str) -> Path:
        return self.run_dir(module_id) / "run.json"

    def approvals_path(self, module_id: str) -> Path:
        return self.run_dir(module_id) / "approvals.json"

    def fill_report_path(self, module_id: str) -> Path:
        return self.run_dir(module_id) / "fill-report.json"

    def model_answer_path(self, module_id: str) -> Path:
        return self.run_dir(module_id) / "model-answer.json"

    def logs_dir(self, module_id: str) -> Path:
        return self.run_dir(module_id) / "logs"

    def fill_js_dir(self, module_id: str) -> Path:
        return self.run_dir(module_id) / "fill-js"

    # -- state ------------------------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def calibration_path(self) -> Path:
        return self.state_dir / "calibration.json"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.json"

    @property
    def applied_path(self) -> Path:
        return self.state_dir / "applied.json"

    # -- user additions ---------------------------------------------------------
    @property
    def lexicon_dir(self) -> Path:
        return self.data_dir / "lexicon"

    @property
    def fonts_dir(self) -> Path:
        return self.data_dir / "fonts"

    @property
    def venv_dir(self) -> Path:
        return self.data_dir / "venv"

    # -- cache ------------------------------------------------------------------
    @property
    def themes_cache_dir(self) -> Path:
        return self.cache_dir / "themes"

    @property
    def font_cache_dir(self) -> Path:
        return self.cache_dir / "font-cache"

    # -- actions ----------------------------------------------------------------
    def layout(self) -> dict[str, Path]:
        """The folders ``ensure()`` creates."""
        return {
            "captures": self.captures_dir,
            "media": self.media_dir,
            "incoming": self.incoming_dir,
            "runs": self.runs_dir,
            "state": self.state_dir,
            "lexicon": self.lexicon_dir,
            "fonts": self.fonts_dir,
        }

    def ensure(self) -> "Store":
        """Create the data layout (not the venv, not the cache)."""
        for p in self.layout().values():
            p.mkdir(parents=True, exist_ok=True)
        return self

    def iter_runs(self) -> Iterator[tuple[Path, dict]]:
        """Every run record on disk (runs/<module_id>/run.json), sorted by path."""
        d = self.runs_dir
        if not d.is_dir():
            return
        for p in sorted(d.glob("*/run.json")):
            try:
                yield p, json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"warning: unreadable run record {p}: {exc}", file=sys.stderr)

    def as_dict(self) -> dict:
        def entry(p: Path, where: str) -> dict:
            return {"path": str(p), "exists": p.exists(), "where": where}

        data_where = self.data_source
        cap, cap_src = self._sub("paths.captures_dir", "captures")
        runs, runs_src = self._sub("paths.runs_dir", "runs")
        return {
            "data_dir": entry(self.data_dir, self.data_source),
            "cache_dir": entry(self.cache_dir, self.cache_source),
            "config": str(self.config.path) if self.config and self.config.path else None,
            "paths": {
                "captures": entry(cap, cap_src),
                "incoming": entry(self.incoming_dir, f"{data_where}, fixed name"),
                "runs": entry(runs, runs_src),
                "state": entry(self.state_dir, f"{data_where}, fixed name"),
                "calibration": entry(self.calibration_path, "state folder"),
                "ledger": entry(self.ledger_path, "state folder"),
                "applied": entry(self.applied_path, "state folder"),
                "lexicon": entry(self.lexicon_dir, f"{data_where}, fixed name"),
                "fonts": entry(self.fonts_dir, f"{data_where}, fixed name"),
                "venv": entry(self.venv_dir, f"{data_where}, fixed name"),
                "themes_cache": entry(self.themes_cache_dir, self.cache_source),
                "font_cache": entry(self.font_cache_dir, self.cache_source),
            },
            "run_records": sum(1 for _ in self.iter_runs()),
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.store", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--config", help="config file")
    ap.add_argument("--data-dir", help="data folder (overrides LT_SUITE_DATA and the config)")
    ap.add_argument("--cache-dir", help="cache folder (overrides LT_SUITE_CACHE and the config)")
    a = ap.parse_args(argv)
    try:
        cfg = config_mod.load(a.config)
    except config_mod.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    st = Store(cfg, data_dir=a.data_dir, cache_dir=a.cache_dir)
    d = st.as_dict()
    if a.json:
        print(json.dumps(d, indent=1))
        return 0
    print(f"data   {d['data_dir']['path']}   [{d['data_dir']['where']}]")
    print(f"cache  {d['cache_dir']['path']}   [{d['cache_dir']['where']}]")
    print(f"config {d['config'] or 'none (built-in defaults)'}")
    for k, v in d["paths"].items():
        mark = "ok " if v["exists"] else "-- "
        print(f"  {mark}{k:<13} {v['path']}   [{v['where']}]")
    print(f"run records {d['run_records']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
