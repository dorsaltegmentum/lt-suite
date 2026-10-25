# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The .drawio masters and the interactive page name lt-suite as their host, carry no agent or vendor
stamp, and load fonts from the network only with fonts.page_fonts = "google"."""
import re
import sys

import pytest

from diagram_testkit import ENGINE, run_engine

VENDOR_HOST = 'host="' + "Claude" + '"'      # built from parts, so this file does not carry the stamp itself


def drawios(case):
    return sorted(case.glob("*.drawio")) + sorted((case / "deliver").glob("*.drawio"))


def test_drawio_parses_on_this_python():
    # the nested-quote f-string that needed Python 3.12 is gone: the module compiles on 3.11
    compile((ENGINE / "drawio.py").read_text(encoding="utf-8"), "drawio.py", "exec")
    assert sys.version_info >= (3, 11)


def test_host_is_lt_suite(toy_build):
    files = drawios(toy_build)
    assert len(files) >= 2
    for f in files:
        head = f.read_text(encoding="utf-8")[:400]
        m = re.search(r'<mxfile host="([^"]*)" agent="([^"]*)"', head)
        assert m, f.name
        assert m.group(1) == "lt-suite"
        assert m.group(2).startswith("lt-suite mechanistic-diagram engine")


def test_no_vendor_stamp_anywhere(toy_build):
    pages = list(toy_build.glob("*.html"))
    assert pages
    for f in drawios(toy_build) + pages + list(toy_build.glob("final-*.svg")):
        text = f.read_text(encoding="utf-8")
        assert VENDOR_HOST not in text, f.name
        assert "window.claude" not in text, f.name
        assert "claude.ai" not in text, f.name


def test_local_fonts_by_default(toy_build):
    for f in drawios(toy_build):
        text = f.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text, f.name
        assert "fontSource=" not in text, f.name
    page = next(toy_build.glob("*.html")).read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in page
    assert "@font-face" in page                     # the faces travel inside the page


def test_google_fonts_only_when_asked(toy_copy, tmp_path, engine_env):
    cfg = tmp_path / "google.toml"
    cfg.write_text('[fonts]\npage_fonts = "google"\n', encoding="utf-8", newline="\n")
    r = run_engine("drawio.py", toy_copy, "--trust-case", "--config", cfg, cwd=toy_copy.parent, env=engine_env, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    text = (toy_copy / "toy-case.drawio").read_text(encoding="utf-8")
    assert "fontSource=https%3A%2F%2Ffonts.googleapis.com" in text
    assert 'host="lt-suite"' in text


@pytest.mark.parametrize("name", ["drawio.py", "make_artifact.py", "mkverify.py", "render.py"])
def test_engine_sources_carry_no_vendor_host(name):
    text = (ENGINE / name).read_text(encoding="utf-8")
    assert VENDOR_HOST not in text
    assert "window.claude" not in text
