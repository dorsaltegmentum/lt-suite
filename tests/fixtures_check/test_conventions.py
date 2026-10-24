# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every fixture file must hold: only invented values, in the agreed shapes."""
from __future__ import annotations

import json
import re
import struct
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import FIX, FILL_REPORT_IDS, STEMS, read_json  # noqa: E402

TS = "2000-01-01T00:00:00Z"
GENERATED_JSON = sorted(p for d in ("modules", "answers", "references", "fill-reports", "runs",
                                    "viewmodels") for p in (FIX / d).rglob("*.json"))


def walk(node, trail=""):
    """(path, key, value) for every key in a JSON document."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{trail}/{k}", k, v
            yield from walk(v, f"{trail}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{trail}[{i}]")


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


def test_the_set_is_the_agreed_one():
    assert sorted(p.name for p in (FIX / "modules").glob("module-*.json")) == sorted(
        f"module-{s}.json" for s in STEMS.values())
    assert sorted(p.name for p in (FIX / "viewmodels").glob("*.json")) == sorted(
        f"{m}.json" for m in STEMS)
    assert len(list((FIX / "answers").glob("answers-*.json"))) == 9
    assert len(list((FIX / "references").glob("reference-*.json"))) == 9
    assert len(list((FIX / "fill-reports").glob("fill-report-*.json"))) == len(FILL_REPORT_IDS)


@pytest.mark.parametrize("path", GENERATED_JSON, ids=lambda p: p.relative_to(FIX).as_posix())
def test_every_timestamp_is_the_fixture_instant(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    for s in strings(doc):
        if re.match(r"\d{4}-\d{2}-\d{2}T", s):
            assert s == TS, (path.name, s)
    for _p, k, v in walk(doc):
        if k == "accessed":
            assert v == "2000-01-01"


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_module_constants(modules, mid):
    m = modules[mid]
    run = 100 + int(mid[-1])
    assert (m["instance"], m["course_id"], m["lesson_id"], m["run_id"]) == ("inst0001", 1, 1, run)
    assert m["module_id"] == f"c1-r{run}"
    assert m["source_url"] == (f"https://lt.example.invalid/i/inst0001/student/courses/1/runs/"
                               f"{run}/run/page/1")
    assert m["captured_at"] == TS
    assert "permission" not in m
    for p in m["pages"]:
        if p["url"]:
            assert p["url"].startswith(f"https://lt.example.invalid/i/inst0001/student/courses/1/runs/{run}/run/page/")


def test_no_legacy_permission_field_anywhere():
    for path in GENERATED_JSON:
        for p, k, _v in walk(json.loads(path.read_text(encoding="utf-8"))):
            assert k not in ("permission", "permission_check"), (path.name, p)


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_item_ids_and_panel_keys_stay_under_100(modules, mid):
    for p in modules[mid]["pages"]:
        for it in p["items"] + p["content"]:
            key = it["panel_key"]
            assert key.isdigit() and int(key) < 100, key
        for it in p["items"]:
            assert re.fullmatch(rf"p{p['page_index']}-\d{{1,2}}", it["item_id"]), it["item_id"]
    vm = read_json(f"viewmodels/{mid}.json")
    for pg in vm["pages"]:
        for pnl in pg.get("allPanels", []):
            assert int(pnl["panelKey"]) < 100


def test_viewmodel_ids_match_their_addresses():
    for mid in STEMS:
        vm = read_json(f"viewmodels/{mid}.json")
        m = re.search(r"/courses/(\d+)/runs/(\d+)/", vm["location"]["pathname"])
        assert vm["module_id"] == mid == f"c{m.group(1)}-r{m.group(2)}"
        assert vm["location"]["origin"] == "https://lt.example.invalid"


@pytest.mark.parametrize("mid", sorted(STEMS))
def test_reference_holds_the_answered_items_and_is_quarantined(modules, mid):
    ref = read_json(f"references/reference-{STEMS[mid]}.json")
    assert ref["never_load_into_drafting_context"] is True
    answered = {i["item_id"] for p in modules[mid]["pages"] for i in p["items"]
                if i["answered"] and i["type"] not in ("unmapped", "subpage")}
    assert {i["item_id"] for i in ref["items"]} == answered
    assert all("student_answer" in item for item in ref["items"])


def test_unanswered_demo_has_an_empty_quarantined_reference(modules):
    """An untouched module has no submitted answers to copy into its reference."""
    assert all(not i["answered"] for p in modules["c1-r107"]["pages"] for i in p["items"])
    ref = read_json("references/reference-demo.json")
    assert ref["items"] == []
    assert ref["never_load_into_drafting_context"] is True


def test_no_draft_cites_a_reference_file():
    for p in (FIX / "answers").glob("answers-*.json"):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"reference-[^/\"]*\.json", text), p.name


def test_references_are_the_only_files_with_student_text(modules):
    """module.json carries state only: no student answer may sit in a module."""
    for mid, ref in ((m, read_json(f"references/reference-{STEMS[m]}.json")) for m in STEMS):
        text = json.dumps(modules[mid])
        for it in ref["items"]:
            ans = it["student_answer"]
            if len(ans) > 12:
                assert ans not in text, (mid, it["item_id"])


# ---- media ---------------------------------------------------------------------------

def png_chunks(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, out = 8, []
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        out.append(data[pos + 4:pos + 8].decode("ascii"))
        pos += 12 + n
    assert pos == len(data)
    return out


def referenced_media(modules):
    names = set()
    for mid, m in modules.items():
        for p in m["pages"]:
            for holder in p["content"] + p["items"]:
                for md in holder.get("media") or []:
                    if md.get("key"):
                        pre = "u" if md.get("role") == "student-upload" else ""
                        names.add(f"lt-{mid}-{pre}{md['key']}.png")
    return names


def test_media_files_match_the_references(modules):
    on_disk = {p.name for p in (FIX / "media").glob("*.png")}
    assert on_disk == referenced_media(modules)


def test_media_are_small_png_files_without_text_chunks():
    files = sorted((FIX / "media").glob("*.png"))
    assert files
    for p in files:
        data = p.read_bytes()
        assert len(data) < 5000, p.name
        assert png_chunks(data) == ["IHDR", "PLTE", "IDAT", "IEND"], p.name
    assert not list((FIX / "media").glob("*.[!p]*"))


def test_an_author_image_and_a_student_upload_share_a_key_but_not_a_file():
    names = {p.name for p in (FIX / "media").glob("lt-c1-r103-*23.png")}
    assert names == {"lt-c1-r103-23.png", "lt-c1-r103-u23.png"}
    assert (FIX / "media/lt-c1-r103-23.png").read_bytes() != (FIX / "media/lt-c1-r103-u23.png").read_bytes()


# ---- the exported page, the course folder, the run records ----------------------------

def test_exported_page_is_14_lines_and_its_image_exists():
    text = (FIX / "pages" / "page.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) == 14
    img = re.search(r"\]\(\.\./assets/([^)]+)\)", text).group(1)
    assert (FIX / "media" / img).is_file()


def test_course_folder_has_both_naming_styles_and_one_ambiguous_week():
    cur = FIX / "curriculum"
    assert (cur / "ILO.md").is_file()
    weeks = sorted(p.relative_to(cur).as_posix() for p in cur.glob("*/*") if p.is_dir() and p.name != "RATs")
    assert weeks == ["Block A/1.2 Demo", "Block A/Week 1.1 Demo", "Block A/Week 1.3 Alpha",
                     "Block B/1.3 Beta"]
    for w in weeks:
        assert any((cur / w).rglob("*.md")), w


def test_run_records_name_the_unit_and_the_tool():
    release = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())['project']['version']
    for mid in ("c1-r101", "c1-r102", "c1-r103", "c1-r104", "c1-r106", "c1-r107"):
        r = read_json(f"runs/{mid}/run.json")
        assert (r["unit_code"], r["institution_profile"], r["tool_version"]) == ("X101", "generic", release)
        assert re.fullmatch(r"[0-9a-f]{64}", r["config_digest"])


def test_nothing_private_in_any_text_file(gen):
    files = {p.relative_to(FIX).as_posix(): p.read_bytes()
             for d in ("modules", "answers", "references", "fill-reports", "runs", "curriculum",
                       "viewmodels", "pages") for p in (FIX / d).rglob("*") if p.is_file()}
    assert gen.scan_text(files) == []
