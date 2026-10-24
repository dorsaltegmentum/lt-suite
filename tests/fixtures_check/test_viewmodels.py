# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The view-model specs are the shape capture.js reads, and capture.js turns each into its
module. tests/capture holds the full roundtrip; this file keeps the check inside the
fixture package so a regenerated set cannot ship without it."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FIX, REPO, STEMS, read_json  # noqa: E402

HARNESS = REPO / "tests" / "capture" / "vm_harness.js"
CAPTURE = REPO / "skills" / "lt-module-capture" / "scripts" / "browser" / "capture.js"


def panels(page):
    """Every panel of a content page, subpage children included."""
    def rec(p):
        yield p
        for sp in (p["panelState"].get("content") or {}).get("subpages", []):
            for r in sp["rows"]:
                for c in r["cells"]:
                    for ch in c["panels"]:
                        yield from rec(ch)
    for p in page.get("allPanels", []):
        yield from rec(p)


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_page_shapes_are_structural(mid):
    vm = read_json(f"viewmodels/{mid}.json")
    assert set(vm) == {"module_id", "location", "globals", "root", "pages"}
    kinds = []
    for pg in vm["pages"]:
        if "reportInfoPanels" in pg:
            assert "allPanels" not in pg and "committed" not in pg
            kinds.append("report")
        elif "committed" in pg:
            assert "allPanels" not in pg and isinstance(pg["committed"], bool) and "index" in pg
            kinds.append("checkpoint")
        else:
            assert isinstance(pg["allPanels"], list) and pg["rows"] == []
            kinds.append("content")
    assert kinds.count("report") <= 1
    root = vm["root"]
    assert set(root) == {"lessonTitleDisplay", "hasCompletionPage", "runLocked",
                         "showPageMenuPageRemaining", "remainingFlowPagesWithoutCheckpoints",
                         "checkpointPageContainer"}
    assert root["hasCompletionPage"] is True or "report" not in kinds


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_panels_have_lt_field_names_and_an_input_only_when_answerable(mid):
    vm = read_json(f"viewmodels/{mid}.json")
    content_only = {"kcTextArea", "image", "table", "divider", "whitespace", "kcMystery", "hotspot",
                    "subpage"}
    seen = 0
    for pg in vm["pages"]:
        for p in panels(pg):
            seen += 1
            assert set(p) == {"pluginKey", "panelKey", "panelState"}
            st = p["panelState"]
            assert "content" in st
            assert set(st) <= {"content", "input", "isReadOnly"}
            if p["pluginKey"] in content_only:
                assert "input" not in st, p["pluginKey"]
            else:
                assert "input" in st, p["pluginKey"]
    assert seen


def test_a_subpage_nests_its_children_where_capture_looks():
    vm = read_json("viewmodels/c1-r104.json")
    page4 = vm["pages"][3]
    sub = next(p for p in page4["allPanels"] if p["pluginKey"] == "subpage")
    kids = sub["panelState"]["content"]["subpages"][0]["rows"][0]["cells"][0]["panels"]
    assert [k["panelKey"] for k in kids] == ["22", "23"]
    assert all("isReadOnly" not in k["panelState"] for k in kids)


def test_the_container_is_null_only_when_lt_exposes_none():
    states = {mid: read_json(f"viewmodels/{mid}.json")["root"]["checkpointPageContainer"]
              for mid in STEMS}
    assert states["c1-r104"] == {"pages": [{"index": 8, "committed": True, "title": "Checkpoint"}]}
    assert states["c1-r101"] == {"pages": []}


# ---- the real capture.js over every view model ------------------------------------------

SCRIPT = textwrap.dedent("""
    const fs = require('node:fs'), path = require('node:path');
    const H = require(process.argv[2]);
    (async () => {
      const idx = JSON.parse(fs.readFileSync(path.join(H.FIXTURES, 'modules', 'index.json'), 'utf8')).modules;
      const out = {};
      for (const { spec } of H.viewmodelSpecs()) {
        const plain = await H.runCapture(H.envFromSpec(spec, { wrap: false }));
        const wrapped = await H.runCapture(H.envFromSpec(spec, { wrap: true }));
        const mod = JSON.parse(fs.readFileSync(path.join(H.FIXTURES, 'modules', idx[spec.module_id]), 'utf8'));
        out[spec.module_id] = { plain: H.shapeOf(plain.doc), wrapped: H.shapeOf(wrapped.doc), module: H.shapeOf(mod) };
      }
      console.log(JSON.stringify(out));
    })().catch(e => { console.error(e.stack); process.exit(1); });
""")


def test_capture_js_over_every_view_model_gives_the_fixture_module(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("not run: node missing")
    for need in (HARNESS, CAPTURE):
        if not need.is_file():
            pytest.skip(f"not run: {need.relative_to(REPO).as_posix()} missing")
    js = tmp_path / "roundtrip.js"
    js.write_text(SCRIPT, encoding="utf-8")
    r = subprocess.run(["node", str(js), str(HARNESS)], capture_output=True, text=True, cwd=REPO,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert set(got) == set(STEMS)
    for mid, shapes in got.items():
        assert shapes["plain"] == shapes["module"], f"{mid}: plain view model"
        assert shapes["wrapped"] == shapes["module"], f"{mid}: wrapped view model"
