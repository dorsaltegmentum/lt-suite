# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Platform config, data, cache and download folders on Linux, macOS and Windows."""
from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath

import pytest

from ltcore import paths

HOME_POSIX = "/fakehome/u"
HOME_WIN = r"C:\fake\u"


def test_platform_name():
    assert paths.platform_name("linux") == "linux"
    assert paths.platform_name("freebsd14") == "linux"
    assert paths.platform_name("darwin") == "darwin"
    assert paths.platform_name("win32") == "win32"
    assert paths.platform_name("cygwin") == "win32"


def test_linux_defaults_without_xdg():
    env = {"HOME": HOME_POSIX}
    assert str(paths.config_dir("linux", env)) == f"{HOME_POSIX}/.config/lt-suite"
    assert str(paths.config_file("linux", env)) == f"{HOME_POSIX}/.config/lt-suite/config.toml"
    assert str(paths.data_dir("linux", env)) == f"{HOME_POSIX}/.local/share/lt-suite"
    assert str(paths.cache_dir("linux", env)) == f"{HOME_POSIX}/.cache/lt-suite"


def test_linux_xdg_overrides_and_relative_xdg_is_ignored():
    env = {"HOME": HOME_POSIX, "XDG_CONFIG_HOME": "/x/cfg", "XDG_DATA_HOME": "/x/data",
           "XDG_CACHE_HOME": "relative/cache"}
    assert str(paths.config_dir("linux", env)) == "/x/cfg/lt-suite"
    assert str(paths.data_dir("linux", env)) == "/x/data/lt-suite"
    # XDG says a relative value is invalid: fall back to ~/.cache
    assert str(paths.cache_dir("linux", env)) == f"{HOME_POSIX}/.cache/lt-suite"


def test_darwin(monkeypatch):
    env = {"HOME": HOME_POSIX, "XDG_CONFIG_HOME": "/ignored"}
    assert str(paths.config_file("darwin", env)) == \
        f"{HOME_POSIX}/Library/Application Support/lt-suite/config.toml"
    assert str(paths.data_dir("darwin", env)) == \
        f"{HOME_POSIX}/Library/Application Support/lt-suite/data"
    assert str(paths.cache_dir("darwin", env)) == f"{HOME_POSIX}/Library/Caches/lt-suite"
    assert str(paths.downloads_dir("darwin", env)) == f"{HOME_POSIX}/Downloads"


def test_win32_with_appdata():
    env = {"USERPROFILE": HOME_WIN, "APPDATA": r"D:\roam", "LOCALAPPDATA": r"D:\local"}
    assert PureWindowsPath(str(paths.config_file("win32", env))) == \
        PureWindowsPath(r"D:\roam\lt-suite\config.toml")
    assert PureWindowsPath(str(paths.data_dir("win32", env))) == \
        PureWindowsPath(r"D:\local\lt-suite\data")
    assert PureWindowsPath(str(paths.cache_dir("win32", env))) == \
        PureWindowsPath(r"D:\local\lt-suite\cache")


def test_win32_without_appdata_falls_back_to_profile():
    env = {"USERPROFILE": HOME_WIN}
    assert PureWindowsPath(str(paths.config_dir("win32", env))) == \
        PureWindowsPath(HOME_WIN + r"\AppData\Roaming\lt-suite")
    assert PureWindowsPath(str(paths.data_dir("win32", env))) == \
        PureWindowsPath(HOME_WIN + r"\AppData\Local\lt-suite\data")


@pytest.mark.parametrize("plat", ["linux", "darwin", "win32"])
def test_monkeypatched_sys_platform(monkeypatch, plat):
    """With no platform argument the functions read sys.platform at call time."""
    monkeypatch.setattr(sys, "platform", plat)
    env = {"HOME": HOME_POSIX, "USERPROFILE": HOME_WIN, "APPDATA": r"D:\roam",
           "LOCALAPPDATA": r"D:\local"}
    got = str(paths.config_file(environ=env))
    want = {"linux": f"{HOME_POSIX}/.config/lt-suite/config.toml",
            "darwin": f"{HOME_POSIX}/Library/Application Support/lt-suite/config.toml",
            "win32": r"D:\roam\lt-suite\config.toml"}[plat]
    if plat == "win32":
        assert PureWindowsPath(got) == PureWindowsPath(want)
    else:
        assert got == want


def test_linux_downloads_from_user_dirs(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Incoming"\n', encoding="utf-8")
    env = {"HOME": HOME_POSIX, "XDG_CONFIG_HOME": str(cfg)}
    assert str(paths.downloads_dir("linux", env)) == f"{HOME_POSIX}/Incoming"
    env2 = {"HOME": HOME_POSIX, "XDG_CONFIG_HOME": str(tmp_path / "none")}
    assert str(paths.downloads_dir("linux", env2)) == f"{HOME_POSIX}/Downloads"


def test_expand(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.expand("") == ""
    assert paths.expand("~/a") == str(tmp_path / "a")
    assert paths.expand("rel/x", base=tmp_path / "b") == str(tmp_path / "b" / "rel" / "x")
    absolute = tmp_path / "abs" / "x"
    assert paths.expand(str(absolute), base=tmp_path / "other") == str(absolute)
