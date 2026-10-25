# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""collect_downloads.py: the download transport's way into <data>/incoming/.

Newest " (n)" variant wins, nothing is overwritten, a reference is written once, images
keep their join name, and copy mode leaves the downloads in place.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills" / "lt-module-capture" / "scripts" / "collect_downloads.py"
SHARED = REPO / "shared"
MID = "c1-r101"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8


def run(tmp: Path, *args: str, cfg_text: str = "schema_version = 1\n") -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SHARED), LT_SUITE_DATA=str(tmp / "data"))
    cfg = tmp / "lt-suite.toml"
    cfg.write_text(cfg_text, encoding="utf-8", newline="\n")
    return subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg), *args],
                          capture_output=True, text=True, env=env, cwd=tmp, timeout=60, encoding="utf-8")


def put(path: Path, data, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


@pytest.fixture
def dl(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


def incoming(tmp: Path) -> Path:
    return tmp / "data" / "incoming"


def test_module_reference_and_images_move_into_incoming(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID})
    put(dl / f"reference-{MID}.json", {"module_id": MID})
    put(dl / f"lt-{MID}-30.png", PNG)
    put(dl / f"lt-{MID}-u31.png", PNG)
    put(dl / "unrelated.png", PNG)
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    inc = incoming(tmp_path)
    assert (inc / f"module-{MID}.json").is_file()
    assert (inc / f"reference-{MID}.json").is_file()
    assert sorted(p.name for p in (inc / "media").iterdir()) == [f"lt-{MID}-30.png", f"lt-{MID}-u31.png"]
    assert sorted(p.name for p in dl.iterdir()) == ["unrelated.png"], "move mode empties what it took"
    assert f"python3 stamp_capture.py {MID}" in r.stdout


def test_the_newest_browser_variant_wins_and_older_ones_stay(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID, "v": "stale"}, age=600)
    put(dl / f"module-{MID} (1).json", {"module_id": MID, "v": "fresh"})
    put(dl / f"lt-{MID}-30 (2).png", PNG)
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((incoming(tmp_path) / f"module-{MID}.json").read_text(encoding="utf-8"))["v"] == "fresh"
    assert (dl / f"module-{MID}.json").is_file(), "the older variant is left alone"
    assert "older copies" in r.stdout
    assert (incoming(tmp_path) / "media" / f"lt-{MID}-30.png").is_file(), "the (n) suffix is dropped"


def test_an_existing_incoming_module_is_kept_and_the_new_one_numbered(tmp_path, dl):
    put(incoming(tmp_path) / f"module-{MID}.json", {"module_id": MID, "v": "first"})
    put(dl / f"module-{MID}.json", {"module_id": MID, "v": "second"})
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((incoming(tmp_path) / f"module-{MID}.json").read_text(encoding="utf-8"))["v"] == "first"
    assert json.loads((incoming(tmp_path) / f"module-{MID}.1.json").read_text(encoding="utf-8"))["v"] == "second"


def test_a_reference_is_written_once(tmp_path, dl):
    cap = tmp_path / "data" / "captures"
    put(cap / f"reference-{MID}.json", {"module_id": MID, "v": "original"})
    put(dl / f"module-{MID}.json", {"module_id": MID})
    put(dl / f"reference-{MID}.json", {"module_id": MID, "v": "new"})
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REFUSED" in r.stdout and "written once" in r.stdout
    assert (dl / f"reference-{MID}.json").is_file(), "the refused download stays where it is"
    assert not (incoming(tmp_path) / f"reference-{MID}.json").exists()
    assert json.loads((cap / f"reference-{MID}.json").read_text(encoding="utf-8"))["v"] == "original"


def test_copy_mode_from_the_config_leaves_downloads_in_place(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID})
    r = run(tmp_path, MID, cfg_text=f'schema_version = 1\n[paths]\ndownloads_dir = "{dl.as_posix()}"\n'
                                     f'downloads_mode = "copy"\n')
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dl / f"module-{MID}.json").is_file()
    assert (incoming(tmp_path) / f"module-{MID}.json").is_file()
    assert "(copy)" in r.stdout


def test_a_bad_module_file_is_refused(tmp_path, dl):
    put(dl / f"module-{MID}.json", b"not json")
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 1
    assert "REFUSED" in r.stdout
    put(dl / f"module-{MID}.json", {"module_id": "c9-r999"})
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 1
    assert not (incoming(tmp_path) / f"module-{MID}.json").exists()


def test_a_different_image_already_in_incoming_is_refused(tmp_path, dl):
    put(incoming(tmp_path) / "media" / f"lt-{MID}-30.png", PNG)
    put(dl / f"module-{MID}.json", {"module_id": MID})
    put(dl / f"lt-{MID}-30.png", PNG + b"different")
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0
    assert "REFUSED" in r.stdout
    assert (incoming(tmp_path) / "media" / f"lt-{MID}-30.png").read_bytes() == PNG


def test_since_skips_old_files(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID}, age=3600)
    r = run(tmp_path, MID, "--downloads-dir", str(dl), "--since", "10")
    assert r.returncode == 1
    assert "no module-" in r.stdout


def test_dry_run_changes_nothing(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID})
    r = run(tmp_path, MID, "--downloads-dir", str(dl), "--dry-run")
    assert r.returncode == 0
    assert "would move" in r.stdout
    assert (dl / f"module-{MID}.json").is_file()
    assert not incoming(tmp_path).exists() or not list(incoming(tmp_path).iterdir())


def test_missing_folder_and_unsafe_ids(tmp_path):
    assert run(tmp_path, MID, "--downloads-dir", str(tmp_path / "nope")).returncode == 3
    assert run(tmp_path, "../x", "--downloads-dir", str(tmp_path)).returncode == 2


def test_platform_downloads_reads_xdg_user_dirs(tmp_path):
    spec = importlib.util.spec_from_file_location("collect_downloads_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Fetched"\n', encoding="utf-8", newline="\n")
    got = mod.platform_downloads(environ={}, home=home)
    if sys.platform.startswith("linux"):
        assert got == home / "Fetched"
    else:
        assert got.name == "Downloads"


def test_an_image_whose_bytes_do_not_match_is_refused(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID})
    put(dl / f"lt-{MID}-30.png", b"<html>not an image</html>")
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0
    assert "leading bytes" in r.stdout
    assert not (incoming(tmp_path) / "media" / f"lt-{MID}-30.png").exists()


def test_same_key_with_two_extensions_are_two_files(tmp_path, dl):
    put(dl / f"module-{MID}.json", {"module_id": MID})
    put(dl / f"lt-{MID}-30.png", PNG)
    put(dl / f"lt-{MID}-30.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 8)
    r = run(tmp_path, MID, "--downloads-dir", str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in (incoming(tmp_path) / "media").iterdir()) == [f"lt-{MID}-30.jpg", f"lt-{MID}-30.png"]


@pytest.mark.parametrize('mode', ['copy', 'move'])
def test_collection_makes_private_independent_files_under_open_umask(tmp_path, dl, mode):
    # Hard-linking browser downloads preserves their public mode and shared inode.
    files = {f'module-{MID}.json': {'module_id': MID},
             f'reference-{MID}.json': {'module_id': MID}, f'lt-{MID}-30.png': PNG}
    for name, value in files.items():
        src = put(dl / name, value)
        src.chmod(0o644)
    old = os.umask(0)
    try:
        r = run(tmp_path, MID, '--downloads-dir', str(dl), '--mode', mode)
    finally:
        os.umask(old)
    assert r.returncode == 0, r.stdout + r.stderr
    inc = incoming(tmp_path)
    for folder in [tmp_path / 'data', inc, inc / 'media']:
        assert folder.stat().st_mode & 0o777 == 0o700
    for name in files:
        dest = inc / ('media' if name.endswith('.png') else '') / name
        assert dest.stat().st_mode & 0o777 == 0o600
        if mode == 'copy':
            assert not os.path.samefile(dest, dl / name)
            assert (dl / name).stat().st_mode & 0o777 == 0o644


def test_images_collect_after_module_was_stamped(tmp_path, dl):
    # The documented second collection has images only after stamping removed incoming JSON.
    put(tmp_path / 'data' / 'captures' / f'module-{MID}.json', {'module_id': MID})
    put(dl / f'lt-{MID}-30.png', PNG)
    r = run(tmp_path, MID, '--downloads-dir', str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (incoming(tmp_path) / 'media' / f'lt-{MID}-30.png').read_bytes() == PNG
    assert 'no module-' not in r.stdout


def test_a_linked_data_folder_is_followed_and_made_private(tmp_path, dl):
    # A user may keep the data folder elsewhere and link to it; collection follows the link.
    real = tmp_path / 'elsewhere'
    real.mkdir()
    real.chmod(0o755)
    (tmp_path / 'data').symlink_to(real, target_is_directory=True)
    put(dl / f'module-{MID}.json', {'module_id': MID})
    r = run(tmp_path, MID, '--downloads-dir', str(dl))
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'Traceback' not in r.stderr
    assert (real / 'incoming' / f'module-{MID}.json').is_file()
    assert real.stat().st_mode & 0o777 == 0o700


def test_a_link_in_place_of_incoming_is_refused_without_a_traceback(tmp_path, dl):
    # A link below the data root would send collected answers to another folder.
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'incoming').symlink_to(elsewhere, target_is_directory=True)
    put(dl / f'module-{MID}.json', {'module_id': MID})
    r = run(tmp_path, MID, '--downloads-dir', str(dl))
    assert r.returncode == 3, r.stdout + r.stderr
    assert 'Traceback' not in r.stderr
    assert 'is a link' in r.stderr
    assert list(elsewhere.iterdir()) == []
    assert (dl / f'module-{MID}.json').is_file()
