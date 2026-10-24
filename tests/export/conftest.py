# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the export skill tests.

``skills/lt-module-export/scripts`` goes on sys.path so the tests import
``export_module``, ``module_pdf``, ``module_pdf_qc`` and ``pdf_base`` by name, the way
the scripts import each other. ``ltcore`` comes from ``shared/`` (tests/conftest.py).

``make_config`` writes a TOML config for one test: data and cache folders under the
test's temp folder (the cache is shared by the session, so static font instances are
cut once), the dotted week pattern and the learning-outcome heading pattern the
synthetic course material uses, and the output switches a test names.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "skills" / "lt-module-export" / "scripts"
FIXTURES = REPO / "tests" / "fixtures"
DATA = Path(__file__).resolve().parent / "data"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

WEEK_PATTERN = r"(?i)^\s*(?:week\s+)?(\d+)\.(\d+)"
ILO_HEADING = r"^##\s+(?:Week\s+)?(\d+\.\d+)\s+(.+?)\s*$"


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v))          # a JSON string is a valid TOML basic string


@pytest.fixture(scope="session")
def font_cache(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("export-font-cache")


@pytest.fixture
def make_config(tmp_path, font_cache):
    """make_config(**tables) -> path of a config file. Tables are keyed by table name,
    e.g. ``make_config(export_outputs={"pdf": False})`` writes ``[export.outputs]``."""
    def build(**tables) -> Path:
        base = {
            "paths": {"data_dir": str(tmp_path / "data"), "cache_dir": str(font_cache)},
            "curriculum": {"learning_outcomes_file": str(FIXTURES / "curriculum" / "ILO.md"),
                           "week_number_pattern": WEEK_PATTERN},
            "curriculum.patterns": {"ilo_heading": ILO_HEADING},
            "export.outputs": {"pdf": False},
        }
        for key, values in tables.items():
            base.setdefault(key.replace("__", "."), {}).update(values)
        lines = ["schema_version = 1", ""]
        for table, values in base.items():
            lines.append(f"[{table}]")
            lines += [f"{k} = {toml_value(v)}" for k, v in values.items()]
            lines.append("")
        path = tmp_path / "lt-suite.toml"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    return build


@pytest.fixture
def fixtures() -> Path:
    if not (FIXTURES / "modules" / "module-c1-r101.json").is_file():
        pytest.skip("not run: tests/fixtures is missing (WP03)")
    return FIXTURES


@pytest.fixture
def export_module():
    import export_module as mod
    return mod


@pytest.fixture
def page_dir(tmp_path) -> Path:
    """A folder with the synthetic exported page and its one image."""
    pytest.importorskip("PIL")
    from PIL import Image
    d = tmp_path / "page"
    (d / "assets").mkdir(parents=True)
    shutil.copy(FIXTURES / "pages" / "page.md", d / "page.md")
    Image.new("RGB", (300, 200), (200, 220, 240)).save(d / "assets" / "lt-c1-r101-31.png")
    Image.new("RGB", (300, 200), (200, 220, 240)).save(d / "assets" / "lt-m1-demo-1.png")
    return d


@pytest.fixture
def need_fonts():
    """Skip PDF tests when the shared font module or the PDF libraries are missing."""
    for name in ("reportlab", "PIL", "fontTools", "pypdfium2"):
        pytest.importorskip(name)
    try:
        from ltcore import fonts  # noqa: F401
    except ImportError:
        pytest.skip("not run: ltcore.fonts is missing (WP09)")


@pytest.fixture
def run(export_module, capsys):
    """run(argv) -> (exit code, stdout, stderr) of export_module.main(argv)."""
    def go(*argv) -> tuple[int, str, str]:
        try:
            rc = export_module.main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 3
        out = capsys.readouterr()
        return rc, out.out, out.err
    return go
