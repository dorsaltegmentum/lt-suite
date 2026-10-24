# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Student uploads and author images never share a file, even when Lt gives them one key.

Lt numbers student uploads and lesson (author) images with two separate counters, so one
key can name both. Saved under the bare key, an upload and an author image would overwrite
each other and the export would embed one picture for both. The rule has two halves:

* the media step saves an upload as ``lt-<module>-u<key>`` and an author image as
  ``lt-<module>-<key>``;
* the export reads an upload from ``u<key>`` first and falls back to the bare key only when
  no author image anywhere in the module shares that key. An author entry always reads the
  bare key and never a ``u`` file.

The cases drive ``export_module.main()`` end to end on a synthetic one-module capture in a
temporary folder and read which file each entry received from the arguments the export
hands to ``build_module_page``. The input validator is replaced by one that accepts, because
these cases test file joining, not schemas (tests/export/test_export_module.py runs the real
validator on the fixture captures).
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

MID = "m1"      # module id, so the media prefix is lt-m1


def entry(key, role):
    return {"key": str(key), "role": role, "alt": f"{role} {key}"}


def author(key):
    return entry(key, "author-supplied")


def upload(key):
    return entry(key, "student-upload")


def item_page(index, item_id, *media):
    return {"page_index": index, "title": f"Page {index}",
            "items": [{"item_id": item_id, "prompt_text": "q", "media": list(media)}],
            "content": []}


def content_page(index, *media):
    return {"page_index": index, "title": f"Page {index}", "items": [],
            "content": [{"media": list(media)}]}


@pytest.fixture
def run_case(tmp_path, make_config, export_module, run, monkeypatch):
    from ltcore import validate
    monkeypatch.setattr(validate, "validate_file",
                        lambda *a, **k: validate.Result("accepting stub"))
    counter = [0]

    def go(pages, on_disk, answers=None, diagrams=None) -> dict:
        counter[0] += 1
        work = tmp_path / f"case{counter[0]}"
        media = work / "media"
        media.mkdir(parents=True)
        for name, blob in on_disk.items():
            (media / f"lt-{MID}-{name}.png").write_bytes(blob)
        module = {"module_id": MID, "run_id": 1, "source_url": "https://lt.example.invalid/m",
                  "captured_at": "2000-01-01T00:00:00Z", "lesson": {"title": "1.1: Test"},
                  "census": {"by_type": {}}, "pages": pages}
        (work / "module.json").write_text(json.dumps(module), encoding="utf-8")
        argv = ["--config", make_config(), "--module", work / "module.json",
                "--media-dir", media, "--out-dir", work / "out", "--json"]
        if answers is not None:
            (work / "answers.json").write_text(json.dumps(answers), encoding="utf-8")
            argv += ["--answers", work / "answers.json"]
        if diagrams:
            (work / "diag").mkdir()
            for name, blob in diagrams.items():
                (work / "diag" / name).write_bytes(blob)
            argv += ["--diagram-dir", work / "diag"]

        seen = {}
        real = export_module.build_module_page

        def spy(*a, **k):
            bound = inspect.signature(real).bind(*a, **k).arguments
            for key in ("media_rows", "page_figures", "asset_rows"):
                seen[key] = bound.get(key) or {}
            return real(*a, **k)

        monkeypatch.setattr(export_module, "build_module_page", spy)
        rc, out, err = run(*argv)
        res = {"rc": rc, "stderr": err.strip(), "report": {}, "copied": {}, **seen}
        if rc == 0:
            res["report"] = json.loads(out)
            res["copied"] = {f.name: f.read_bytes()
                             for f in sorted((work / "out" / "assets").iterdir())}
        return res
    return go


def names(rows: dict, key) -> list[str]:
    return [r["name"] for r in rows.get(key, [])]


def test_collision_each_entry_gets_its_own_file(run_case):
    A, U = b"author-23", b"upload-23"
    res = run_case([content_page(1, author(23)), item_page(9, "p9-5", upload(23))],
                   {"23": A, "u23": U})
    assert res["rc"] == 0, res["stderr"]
    assert names(res["page_figures"], 1) == ["lt-m1-23.png"]
    assert names(res["media_rows"], "p9-5") == ["lt-m1-u23.png"]
    assert res["copied"] == {"lt-m1-23.png": A, "lt-m1-u23.png": U}
    assert res["report"]["media"]["keys_not_found"] == []


def test_legacy_upload_without_collision_takes_the_bare_file(run_case):
    res = run_case([content_page(1, author(8)), item_page(9, "p9-1", upload(7))],
                   {"8": b"author-8", "7": b"legacy-7"})
    assert res["rc"] == 0, res["stderr"]
    assert names(res["media_rows"], "p9-1") == ["lt-m1-7.png"]
    assert names(res["page_figures"], 1) == ["lt-m1-8.png"]
    assert res["report"]["media"]["keys_not_found"] == []


@pytest.mark.parametrize("label, pages", [
    ("author later on a content panel",
     [item_page(2, "p2-1", upload(23)), content_page(5, author(23))]),
    ("author later on an item",
     [item_page(2, "p2-1", upload(23)), item_page(5, "p5-1", author(23))]),
])
def test_legacy_upload_colliding_with_an_author_key_is_missing(run_case, label, pages):
    res = run_case(pages, {"23": b"author-23"})
    assert res["rc"] == 0, res["stderr"]
    assert names(res["media_rows"], "p2-1") == [], "the upload must not take the author's file"
    assert res["report"]["media"]["keys_not_found"] == ["23"]
    got = names(res["page_figures"], 5) + names(res["media_rows"], "p5-1")
    assert got == ["lt-m1-23.png"], "the author keeps the bare file"
    assert res["report"]["media"]["available"] == 1, "one image counted, not two"


def test_author_entry_never_takes_an_upload_file(run_case):
    res = run_case([item_page(2, "p2-1", author(7))], {"u7": b"upload-7"})
    assert res["rc"] == 0, res["stderr"]
    assert names(res["media_rows"], "p2-1") == []
    assert res["report"]["media"]["keys_not_found"] == ["7"]
    assert "lt-m1-u7.png" not in res["copied"]


def test_svg_replaces_a_raster_ref_once_and_other_refs_stay_links(run_case):
    diag = {"x.png": b"png", "x.svg": b"<svg/>", "x.pdf": b"%PDF", "x.drawio": b"<mxfile/>",
            "y.svg": b"<svg/>", "y.pdf": b"%PDF", "z.png": b"png-z"}
    refs = {"p2-1": ["x.png", "x.pdf", "x.drawio"], "p2-2": ["x.png", "x.svg"],
            "p2-3": ["y.pdf"], "p2-4": ["z.png"]}
    pages = [{"page_index": 2, "title": "Page 2", "content": [],
              "items": [{"item_id": i, "prompt_text": f"q{i}", "media": []} for i in refs]}]
    answers = {"module_id": MID, "items": [
        {"item_id": i, "status": "drafted", "segments": [], "asset_refs": r}
        for i, r in refs.items()]}
    res = run_case(pages, {}, answers=answers, diagrams=diag)
    assert res["rc"] == 0, res["stderr"]
    rows = res["asset_rows"]
    assert names(rows, "p2-1") == ["lt-m1-x.svg"]
    assert names(rows, "p2-2") == ["lt-m1-x.svg"]
    assert names(rows, "p2-3") == []
    assert names(rows, "p2-4") == ["lt-m1-z.png"]


def _capture_scripts() -> Path:
    return Path(__file__).resolve().parents[2] / "skills" / "lt-module-capture" / "scripts"


def test_stamp_capture_points_each_entry_at_its_own_file(tmp_path):
    stamp = _capture_scripts() / "stamp_capture.py"
    if not stamp.is_file():
        pytest.skip("not run: lt-module-capture stamp_capture.py is missing (WP05)")
    import importlib.util
    spec = importlib.util.spec_from_file_location("stamp_capture_under_test", stamp)
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    (tmp_path / "media").mkdir()
    for name in ("23", "u23", "7", "9"):
        (tmp_path / "media" / f"lt-{MID}-{name}.png").write_bytes(name.encode())
    doc = {"module_id": MID, "pages": [
        content_page(1, author(23), author(9)),
        item_page(2, "p2-1", upload(23)), item_page(3, "p3-1", upload(7)),
        item_page(4, "p4-1", dict(upload(9), local_path="media/lt-m1-9.png"))]}
    S.backfill_local_paths(doc, tmp_path / "module.json", tmp_path / "media")
    lp = {it["item_id"]: it["media"][0].get("local_path")
          for pg in doc["pages"] for it in pg["items"]}
    auth = [m.get("local_path") for m in doc["pages"][0]["content"][0]["media"]]
    assert lp["p2-1"] == "media/lt-m1-u23.png"
    assert auth[0] == "media/lt-m1-23.png"
    assert lp["p3-1"] == "media/lt-m1-7.png"
    assert lp["p4-1"] is None, "a stale bare path is cleared when no u-file exists"


def test_media_step_keeps_the_u_prefix_for_uploads():
    src = _capture_scripts() / "browser" / "fetch_media.js"
    if not src.is_file():
        pytest.skip("not run: lt-module-capture fetch_media.js is missing (WP05)")
    import re
    text = src.read_text(encoding="utf-8")
    assert re.search(r"role\s*===\s*['\"]student-upload['\"]\s*\?\s*`u\$\{", text)
    assert "`lt-${moduleId}-${stem}${ext}`" in text
