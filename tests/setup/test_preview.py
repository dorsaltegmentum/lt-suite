# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify portable Pillow previews and catalog choices without external renderers."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/lt-suite-setup/scripts"


def test_palette_previews_without_system_renderers(run_py, tmp_path):
    p = run_py(str(SCRIPTS / "preview.py"), "palettes", "--top", "1", "--no-also",
               "--out", str(tmp_path), "--json", env={"PATH": ""})
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert len(doc["palettes"]) == 2
    for row in doc["palettes"]:
        assert row["text_min"] >= 4.5
        assert row["nontext_min"] >= 3
        assert len(row["contrast"]) >= 7
        with Image.open(row["png"]) as im:
            assert im.width >= 1000
            assert im.height > 400
    assert (tmp_path / "palettes.md").is_file()


def test_bundled_font_previews_without_system_renderers(run_py, tmp_path):
    p = run_py(str(SCRIPTS / "preview.py"), "fonts", "--ids", "noto-sans,noto-sans-mono",
               "--out", str(tmp_path), "--json", env={"PATH": ""})
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)["fonts"]
    assert len(rows) == 2
    for row in rows:
        assert row["state"] == "bundled"
        assert row["licence"] == "OFL-1.1-no-RFN"
        assert row["png"], row
        with Image.open(row["png"]) as im:
            assert im.width == 1100


def test_bold_sample_font_is_a_heavier_instance(monkeypatch):
    # The bundled sans is a variable font; bold samples need a cut static instance,
    # or the "bold" headings in the sheets render at weight 400.
    import importlib.util

    spec = importlib.util.spec_from_file_location("wp11_preview", SCRIPTS / "preview.py")
    preview = importlib.util.module_from_spec(spec)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec.loader.exec_module(preview)
    regular, _src = preview.load_font(40)
    bold, _src = preview.load_font(40, bold=True)
    assert regular.getlength("Hello world") < bold.getlength("Hello world")
