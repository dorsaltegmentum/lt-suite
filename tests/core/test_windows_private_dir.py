# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private JavaScript folders must reject Windows reparse points."""
import os
from types import SimpleNamespace

import pytest

from ltcore import ltjs


def test_private_dir_rejects_reparse_directory(tmp_path, monkeypatch):
    folder = tmp_path / "junction"
    folder.mkdir(mode=0o700)
    original = os.lstat

    def lstat(path, *args, **kwargs):
        st = original(path, *args, **kwargs)
        if path == folder:
            return SimpleNamespace(st_mode=st.st_mode, st_uid=getattr(st, "st_uid", 0),
                                   st_file_attributes=0x400)
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(ltjs.RenderRefused, match="reparse"):
        ltjs.private_dir(folder)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_private_dir_rejects_native_junction(tmp_path):
    import subprocess
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    with pytest.raises(ltjs.RenderRefused, match="reparse"):
        ltjs.private_dir(junction)
