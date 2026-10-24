# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep export ownership checks and drafting quarantine on one stamp grammar."""
from __future__ import annotations

import json

import pytest

from ltcore import quarantine


@pytest.mark.parametrize("prop,stamp,expected", [
    ("lt-module", "LT-MODULE:: Demo\n", "Demo"),
    ("lt-module", chr(0xFEFF) + "lt-module:: demo\r\n", "demo"),
    ("lt-module", '---\n"lt-module": "demo"\n---\n', "demo"),
    ("lt-module", "---\n'lt-module': 'demo'\n---\n", "demo"),
    ("lt-module", "<!-- lt-module: demo -->\n", "demo"),
    ("lt-module", "<p>lt_module :: demo</p>\r\n", "demo"),
    ("lt-module", '<div><b>LT_MODULE</b> :: <span>demo</span></div>\n', "demo"),
    ("suite.output", "  SUITE.OUTPUT : : demo\n", "demo"),
    ("suite.output", "suiteXoutput:: demo\n", None),
    ("suite.output", "lt-module:: demo\n", None),
    ("lt-module", "lt-module::\nordinary text\n", None),
    ("lt-module", "prefix lt-module:: demo\n", None),
    ("lt-module", "lt-module:: demo extra\n", None),
])
def test_export_probe_and_source_scanner_agree(repo_root, tmp_path, run_py, prop, stamp, expected):
    page = tmp_path / "output.md"
    page.write_bytes(stamp.encode("utf-8"))
    # Run the actual export factory with its normal imports, without building files.
    result = run_py("-c", """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import export_module
page = Path(sys.argv[2])
print(json.dumps(export_module.make_probe_markdown(sys.argv[3])(page, page.read_bytes())))
""", str(repo_root / "skills/lt-module-export/scripts"), str(page), prop)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected
    found = quarantine.scan(tmp_path, stamp_property=prop)
    assert [f["module_id"] for f in found] == ([] if expected is None else [expected])
