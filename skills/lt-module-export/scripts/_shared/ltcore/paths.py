# Generated from shared/ltcore/paths.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Platform directories for lt-suite: config, data, cache and downloads.

Each function takes optional ``platform``, ``environ`` and ``home`` arguments so
tests can ask for the macOS or Windows answer on a Linux machine. With no
arguments they read ``sys.platform``, ``os.environ`` and the real home folder.

    Linux    ${XDG_CONFIG_HOME:-~/.config}/lt-suite
             ${XDG_DATA_HOME:-~/.local/share}/lt-suite
             ${XDG_CACHE_HOME:-~/.cache}/lt-suite
    macOS    ~/Library/Application Support/lt-suite      (config)
             ~/Library/Application Support/lt-suite/data (data)
             ~/Library/Caches/lt-suite                   (cache)
    Windows  %APPDATA%\\lt-suite                          (config)
             %LOCALAPPDATA%\\lt-suite\\data                (data)
             %LOCALAPPDATA%\\lt-suite\\cache               (cache)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Mapping

NEEDS: list[str] = []

APP = "lt-suite"
CONFIG_NAME = "config.toml"
WORKSPACE_CONFIG = "lt-suite.toml"


def platform_name(platform: str | None = None) -> str:
    """Return ``linux``, ``darwin`` or ``win32``. Other Unixes count as linux."""
    p = platform if platform is not None else sys.platform
    if p.startswith("win") or p == "cygwin":
        return "win32"
    if p == "darwin":
        return "darwin"
    return "linux"


def _path_type(platform: str):
    """The path class for ``platform``: the real Path when its flavour matches this OS,
    so tests can compute Windows paths on Linux and the reverse."""
    native_windows = os.name == "nt"
    if (platform == "win32") == native_windows:
        return Path
    return PureWindowsPath if platform == "win32" else PurePosixPath


def _home(platform: str, environ: Mapping[str, str], home: str | None) -> PurePath:
    cls = _path_type(platform)
    if home is not None:
        return cls(home)
    if platform == "win32":
        h = environ.get("USERPROFILE")
        if h:
            return cls(h)
    h = environ.get("HOME")
    if h:
        return cls(h)
    return cls(str(Path.home()))


def _env_dir(environ: Mapping[str, str], name: str, cls) -> PurePath | None:
    v = environ.get(name, "")
    # XDG says relative values are invalid and must be ignored.
    if v and cls(v).is_absolute():
        return cls(v)
    return None


def config_dir(platform: str | None = None, environ: Mapping[str, str] | None = None,
               home: str | None = None) -> PurePath:
    """The folder that holds ``config.toml``."""
    plat = platform_name(platform)
    env = os.environ if environ is None else environ
    cls = _path_type(plat)
    h = _home(plat, env, home)
    if plat == "win32":
        base = env.get("APPDATA") or str(h / "AppData" / "Roaming")
        return cls(base) / APP
    if plat == "darwin":
        return h / "Library" / "Application Support" / APP
    return (_env_dir(env, "XDG_CONFIG_HOME", cls) or h / ".config") / APP


def config_file(platform: str | None = None, environ: Mapping[str, str] | None = None,
                home: str | None = None) -> PurePath:
    """The platform config file path (lookup step 4)."""
    return config_dir(platform, environ, home) / CONFIG_NAME


def data_dir(platform: str | None = None, environ: Mapping[str, str] | None = None,
             home: str | None = None) -> PurePath:
    """The platform default data folder (runs, captures, state, fonts, venv)."""
    plat = platform_name(platform)
    env = os.environ if environ is None else environ
    cls = _path_type(plat)
    h = _home(plat, env, home)
    if plat == "win32":
        base = env.get("LOCALAPPDATA") or str(h / "AppData" / "Local")
        return cls(base) / APP / "data"
    if plat == "darwin":
        return h / "Library" / "Application Support" / APP / "data"
    return (_env_dir(env, "XDG_DATA_HOME", cls) or h / ".local" / "share") / APP


def cache_dir(platform: str | None = None, environ: Mapping[str, str] | None = None,
              home: str | None = None) -> PurePath:
    """The platform default cache folder (theme cache, static font instances)."""
    plat = platform_name(platform)
    env = os.environ if environ is None else environ
    cls = _path_type(plat)
    h = _home(plat, env, home)
    if plat == "win32":
        base = env.get("LOCALAPPDATA") or str(h / "AppData" / "Local")
        return cls(base) / APP / "cache"
    if plat == "darwin":
        return h / "Library" / "Caches" / APP
    return (_env_dir(env, "XDG_CACHE_HOME", cls) or h / ".cache") / APP


def environment_dir(data_dir):
    """Choose a Python environment outside git work trees, preserving lexical paths.

    Setup loads this module directly on Python 3.8 before installing dependencies.
    Resolve symlinks for safety checks, including the final venv directory.
    """
    for base in (Path(data_dir), Path(str(globals()["data_dir"]()))):
        candidate = (base / "venv").absolute()
        resolved = candidate.resolve()
        if not any((p / ".git").exists() for p in (resolved,) + tuple(resolved.parents)):
            return candidate
    raise ValueError("No environment location outside a git work tree is available. "
                     "Choose an external platform data directory and run bootstrap again.")


def downloads_dir(platform: str | None = None, environ: Mapping[str, str] | None = None,
                  home: str | None = None) -> PurePath:
    """The browser download folder: XDG_DOWNLOAD_DIR on Linux, else ~/Downloads."""
    plat = platform_name(platform)
    env = os.environ if environ is None else environ
    cls = _path_type(plat)
    h = _home(plat, env, home)
    if plat == "linux":
        v = _env_dir(env, "XDG_DOWNLOAD_DIR", cls) or _user_dirs_download(env, h, cls)
        if v is not None:
            return v
    return h / "Downloads"


def _user_dirs_download(env: Mapping[str, str], home: PurePath, cls) -> PurePath | None:
    """Read XDG_DOWNLOAD_DIR from ``user-dirs.dirs`` when the file exists."""
    cfg = _env_dir(env, "XDG_CONFIG_HOME", cls) or home / ".config"
    f = Path(str(cfg)) / "user-dirs.dirs"
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_DOWNLOAD_DIR="):
            continue
        val = line.split("=", 1)[1].strip().strip('"')
        val = val.replace("$HOME", str(home))
        if val and cls(val).is_absolute():
            return cls(val)
    return None


def expand(value: str, base: str | os.PathLike | None = None) -> str:
    """Expand ``~`` and resolve a relative path against ``base``.

    An empty string stays empty: it means "unset, use the default".
    """
    if not value:
        return value
    p = Path(os.path.expanduser(value))
    if not p.is_absolute() and base is not None:
        p = Path(base) / p
    return str(p)
