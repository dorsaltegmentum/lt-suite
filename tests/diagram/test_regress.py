# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression: a fresh build of the worked example matches tests/diagram/reference/toy-case.manifest.json
(sha256 of geometry.json, each palette's SVG and each .drawio; each PNG within 0.5 % of its reference
thumbnail, because librsvg versions anti-alias differently).

After a deliberate visual change, rebuild the example and record it again:
    python3 skills/mechanistic-diagram/engine/build.py skills/mechanistic-diagram/examples/toy-case
    python3 skills/mechanistic-diagram/engine/regress.py skills/mechanistic-diagram/examples/toy-case \\
        --write-manifest tests/diagram/reference/toy-case.manifest.json
"""
import json

import regress
from diagram_testkit import HERE, run_engine

MANIFEST = HERE / "reference" / "toy-case.manifest.json"


def test_manifest_records_every_palette_and_file():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert man["slug"] == "toy-case"
    assert len(man["palettes"]) >= 2
    for p in man["palettes"]:
        assert f"final-{p}.svg" in man["files"]
        thumb = man["files"][f"final-{p}.png"]["thumbnail"]
        assert (MANIFEST.parent / thumb).is_file()
    assert "geometry.json" in man["files"]
    assert "toy-case.drawio" in man["files"]
    assert man["png_tolerance"] == 0.005


def test_fresh_build_matches_the_manifest(toy_build, engine_env):
    r = run_engine("regress.py", toy_build, "--manifest", MANIFEST, cwd=toy_build.parent, env=engine_env)
    assert r.returncode == 0, r.stdout[-3000:]
    assert "REGRESSION PASSED" in r.stdout


def test_a_changed_build_fails(toy_copy, engine_env):
    geo = toy_copy / "geometry.json"
    data = json.loads(geo.read_text(encoding="utf-8"))
    data["nodes"]["m_low"]["x"] += 1
    geo.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    svg = next(toy_copy.glob("final-*.svg"))
    svg.write_text(svg.read_text(encoding="utf-8").replace("<text ", '<text data-moved="1" ', 1), encoding="utf-8", newline="\n")
    r = run_engine("regress.py", toy_copy, "--manifest", MANIFEST, cwd=toy_copy.parent, env=engine_env)
    assert r.returncode == 1
    assert "FAIL geometry.json" in r.stdout
    assert f"FAIL {svg.name}" in r.stdout


def test_png_tolerance(tmp_path):
    from PIL import Image
    a = Image.new("RGBA", (960, 200), (255, 255, 255, 255))
    a.save(tmp_path / "a.png")
    b = a.copy()
    for x in range(0, 960, 100):              # a few scattered pixels: well inside the tolerance
        b.putpixel((x, 100), (0, 0, 0, 255))
    b.save(tmp_path / "b.png")
    import numpy as np
    ta, tb = np.asarray(regress.thumbnail(tmp_path / "a.png")), np.asarray(regress.thumbnail(tmp_path / "b.png"))
    share = float((abs(ta.astype(int) - tb.astype(int)).max(axis=2) > regress.PNG_DELTA).mean())
    assert share <= regress.PNG_TOLERANCE


def test_drawio_digest_ignores_agent_and_fonts():
    a = b'<mxfile host="lt-suite" agent="lt-suite mechanistic-diagram engine 1"><x s="data:font/ttf;base64,AAAA"/></mxfile>'
    b = b'<mxfile host="lt-suite" agent="lt-suite mechanistic-diagram engine 2"><x s="data:font/ttf;base64,BBBB"/></mxfile>'
    c = b'<mxfile host="other" agent="lt-suite mechanistic-diagram engine 2"><x s="data:font/ttf;base64,BBBB"/></mxfile>'
    assert regress.drawio_digest(a) == regress.drawio_digest(b)
    assert regress.drawio_digest(a) != regress.drawio_digest(c)
