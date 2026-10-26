# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""stamp_capture.py: validate before moving, media paths, write-once reference, numbered
copies, legacy fields, and refusal paths. The base document is WP02's neutral example
module (tests/fixtures/examples/module.example.json), which validates on its own.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "skills" / "lt-module-capture" / "scripts"
STAMP = Path(os.environ.get("CAPTURE_STAMP_PY", SCRIPTS / "stamp_capture.py"))
SHARED = REPO / "shared"
EXAMPLE = REPO / "tests" / "fixtures" / "examples" / "module.example.json"
REF_EXAMPLE = REPO / "tests" / "fixtures" / "examples" / "reference.example.json"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16

pytestmark = pytest.mark.skipif(not EXAMPLE.is_file() or not (SHARED / "ltcore" / "validate.py").is_file(),
                                reason="not run: WP02 example module or ltcore.validate missing")


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def run(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SHARED)
    env["LT_SUITE_DATA"] = str(tmp / "data")
    cfg = tmp / "lt-suite.toml"
    if not cfg.exists():
        cfg.write_text("schema_version = 1\n", encoding="utf-8", newline="\n")
    return subprocess.run([sys.executable, str(STAMP), "--config", str(cfg), *args],
                          capture_output=True, text=True, env=env, cwd=tmp, timeout=120, encoding="utf-8")


def layout(tmp: Path) -> tuple[Path, Path]:
    inc, cap = tmp / "data" / "incoming", tmp / "data" / "captures"
    inc.mkdir(parents=True, exist_ok=True)
    return inc, cap


def put(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8", newline="\n")
    return path


def load_stamp():
    spec = importlib.util.spec_from_file_location("stamp_capture_under_test", STAMP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_valid_incoming_module_moves_to_captures(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / f"module-{mid}.json", example())
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (cap / f"module-{mid}.json").is_file()
    assert not (inc / f"module-{mid}.json").exists()
    assert "question digest:" in r.stdout
    assert "OK: " in r.stdout
    out = json.loads((cap / f"module-{mid}.json").read_text(encoding="utf-8"))
    assert "question_digest" not in json.dumps(out), "the digest is printed, never stored"


def test_legacy_permission_field_is_dropped_with_a_note(tmp_path):
    inc, cap = layout(tmp_path)
    doc = example()
    doc["permission"] = {"unit_code": "PENDING", "checked_at": "2000-01-01T00:00:00Z", "exit_code": 0}
    mid = doc["module_id"]
    put(inc / f"module-{mid}.json", doc)
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dropped legacy field(s) permission" in r.stdout
    assert "permission" not in json.loads((cap / f"module-{mid}.json").read_text(encoding="utf-8"))


def test_a_schema_failure_exits_2_and_nothing_moves(tmp_path):
    inc, cap = layout(tmp_path)
    doc = example()
    del doc["pages"]
    mid = doc["module_id"]
    src = put(inc / f"module-{mid}.json", doc)
    (inc / "media").mkdir()
    (inc / "media" / f"lt-{mid}-slide-grid-01.png").write_bytes(PNG)
    r = run(tmp_path, mid)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "SCHEMA FAIL" in r.stdout
    assert src.is_file()
    assert not (cap / f"module-{mid}.json").exists()
    assert (inc / "media" / f"lt-{mid}-slide-grid-01.png").is_file(), "media stay until the module validates"


def test_a_rail_failure_exits_1_and_nothing_moves(tmp_path):
    inc, cap = layout(tmp_path)
    doc = example()
    report = next(p for p in doc["pages"] if p.get("is_report_page"))
    second = copy.deepcopy(report)
    second["page_index"] = max(p["page_index"] for p in doc["pages"]) + 1
    doc["pages"].append(second)
    doc["lesson"]["page_count"] = len(doc["pages"])
    doc["census"]["pages_total"] = len(doc["pages"])
    mid = doc["module_id"]
    src = put(inc / f"module-{mid}.json", doc)
    r = run(tmp_path, mid)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "RAIL FAIL" in r.stdout
    assert src.is_file() and not (cap / f"module-{mid}.json").exists()


def test_media_move_and_local_paths_are_relative_to_the_capture(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / f"module-{mid}.json", example())
    (inc / "media").mkdir()
    (inc / "media" / f"lt-{mid}-slide-grid-01.png").write_bytes(PNG)
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (cap / "media" / f"lt-{mid}-slide-grid-01.png").read_bytes() == PNG
    assert not (inc / "media" / f"lt-{mid}-slide-grid-01.png").exists()
    doc = json.loads((cap / f"module-{mid}.json").read_text(encoding="utf-8"))
    paths = [m.get("local_path") for p in doc["pages"] for c in p.get("content", []) for m in c.get("media") or []]
    assert f"media/lt-{mid}-slide-grid-01.png" in paths
    assert "1 of 1 keyed media entries resolved" in r.stdout


def test_a_different_image_already_in_captures_is_kept_and_reported(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / f"module-{mid}.json", example())
    (cap / "media").mkdir(parents=True)
    (cap / "media" / f"lt-{mid}-slide-grid-01.png").write_bytes(PNG)
    (inc / "media").mkdir()
    (inc / "media" / f"lt-{mid}-slide-grid-01.png").write_bytes(PNG + b"new")
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KEPT in incoming" in r.stdout
    assert (cap / "media" / f"lt-{mid}-slide-grid-01.png").read_bytes() == PNG
    assert (inc / "media" / f"lt-{mid}-slide-grid-01.png").is_file()


def test_the_newest_numbered_copy_wins_and_older_ones_are_set_aside(tmp_path):
    inc, cap = layout(tmp_path)
    base = example()
    mid = base["module_id"]
    old, new = copy.deepcopy(base), copy.deepcopy(base)
    old["lesson"]["title"] = "older"
    new["lesson"]["title"] = "newer"
    put(inc / f"module-{mid}.json", old)
    put(inc / f"module-{mid}.1.json", old)
    put(inc / f"module-{mid}.2.json", new)
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((cap / f"module-{mid}.json").read_text(encoding="utf-8"))["lesson"]["title"] == "newer"
    assert not list(inc.glob(f"module-{mid}*.json"))
    assert sorted(p.name for p in (inc / "superseded").iterdir()) == [f"module-{mid}.1.json", f"module-{mid}.json"]


def test_reference_moves_once_and_a_different_one_is_refused(tmp_path):
    if not REF_EXAMPLE.is_file():
        pytest.skip("not run: WP02 reference example missing")
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    ref = json.loads(REF_EXAMPLE.read_text(encoding="utf-8"))
    ref["module_id"] = mid
    put(inc / f"module-{mid}.json", example())
    put(inc / f"reference-{mid}.json", ref)
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (cap / f"reference-{mid}.json").is_file()
    first = (cap / f"reference-{mid}.json").read_bytes()

    changed = copy.deepcopy(ref)
    changed["captured_at"] = "2000-01-02T00:00:00Z"
    put(inc / f"module-{mid}.json", example())
    put(inc / f"reference-{mid}.json", changed)
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REFUSED reference" in r.stdout
    assert (cap / f"reference-{mid}.json").read_bytes() == first
    assert (inc / f"reference-{mid}.json").is_file()

    put(inc / f"reference-{mid}.json", json.loads(first))
    put(inc / f"module-{mid}.json", example())
    r = run(tmp_path, mid)
    assert r.returncode == 0
    assert "identical" in r.stdout
    assert not (inc / f"reference-{mid}.json").exists()


def test_restamp_in_place_after_more_images_arrive(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(cap / f"module-{mid}.json", example())
    (inc / "media").mkdir()
    (inc / "media" / f"lt-{mid}-slide-grid-01.png").write_bytes(PNG)
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (cap / "media" / f"lt-{mid}-slide-grid-01.png").is_file()


def test_media_dir_flag_checks_another_folder_and_moves_nothing(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / f"module-{mid}.json", example())
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / f"lt-{mid}-slide-grid-01.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    r = run(tmp_path, mid, "--media-dir", str(other))
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads((cap / f"module-{mid}.json").read_text(encoding="utf-8"))
    paths = [m.get("local_path") for p in doc["pages"] for c in p.get("content", []) for m in c.get("media") or []]
    assert any(p and p.endswith(f"elsewhere/lt-{mid}-slide-grid-01.jpg") for p in paths)
    assert not (cap / "media").exists()


@pytest.mark.parametrize("arg", ["../escape", "c1/r101", "a" * 200])
def test_unsafe_ids_exit_3(tmp_path, arg):
    layout(tmp_path)
    assert run(tmp_path, arg).returncode == 3


def test_a_file_whose_module_id_differs_from_its_name_exits_3(tmp_path):
    inc, _ = layout(tmp_path)
    put(inc / "module-c9-r999.json", example())
    r = run(tmp_path, str(inc / "module-c9-r999.json"))
    assert r.returncode == 3
    assert "not 'c9-r999'" in r.stderr


def test_no_capture_anywhere_exits_3(tmp_path):
    layout(tmp_path)
    r = run(tmp_path, "c1-r199")
    assert r.returncode == 3
    assert "no module-c1-r199.json" in r.stderr


def test_backfill_rules_for_uploads_author_keys_and_empty_slots(tmp_path):
    stamp = load_stamp()
    media = tmp_path / "media"
    media.mkdir()
    for n in ("lt-m1-5.png", "lt-m1-u5.png", "lt-m1-9.jpg"):
        (media / n).write_bytes(PNG)
    doc = {"module_id": "m1", "pages": [{"items": [
        {"media": [{"key": "5", "role": "author-supplied"}, {"key": "5", "role": "student-upload"}]},
        {"media": [{"key": "9", "role": "student-upload"}]},   # no author image shares key 9
        {"media": [{"key": None, "role": "student-upload"}]},
        {"media": [{"key": "77", "role": "student-upload", "local_path": "stale.png"}]}]}]}
    found, keyed, no_key = stamp.backfill_local_paths(doc, tmp_path / "module-m1.json", media)
    assert (found, keyed, no_key) == (3, 4, 1)
    items = doc["pages"][0]["items"]
    assert items[0]["media"][0]["local_path"] == "media/lt-m1-5.png"
    assert items[0]["media"][1]["local_path"] == "media/lt-m1-u5.png"
    assert items[1]["media"][0]["local_path"] == "media/lt-m1-9.jpg"
    assert items[3]["media"][0]["local_path"] is None, "a stale path on an upload is cleared"


def test_help_runs_without_a_config_or_data(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SHARED))
    r = subprocess.run([sys.executable, str(STAMP), "--help"], capture_output=True, text=True, env=env, cwd=tmp_path, encoding="utf-8")
    assert r.returncode == 0
    assert "Exit codes" in r.stdout


def test_a_malformed_document_is_a_schema_failure_not_a_crash(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / f"module-{mid}.json", {"module_id": mid, "pages": [None]})
    r = run(tmp_path, mid)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Traceback" not in r.stderr


def test_stamping_an_older_copy_leaves_newer_copies_alone(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / f"module-{mid}.json", example())
    put(inc / f"module-{mid}.1.json", example())
    put(inc / f"module-{mid}.2.json", example())
    r = run(tmp_path, str(inc / f"module-{mid}.1.json"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (inc / f"module-{mid}.2.json").is_file(), "a newer copy waits for its own stamp"
    assert (inc / "superseded" / f"module-{mid}.json").is_file()


def test_superseded_copies_never_overwrite_each_other(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()["module_id"]
    put(inc / "superseded" / f"module-{mid}.json", {"keep": True})
    put(inc / f"module-{mid}.json", example())
    put(inc / f"module-{mid}.1.json", example())
    r = run(tmp_path, mid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((inc / "superseded" / f"module-{mid}.json").read_text(encoding="utf-8")) == {"keep": True}
    assert (inc / "superseded" / f"module-{mid}.1.json").is_file()


@pytest.mark.parametrize('reference', [None, [], 'text', '{broken', {'module_id': 'wrong'}])
def test_invalid_reference_leaves_every_incoming_file_untouched(tmp_path, reference):
    # Reference validation must finish before module publication or any media move.
    inc, cap = layout(tmp_path)
    mid = example()['module_id']
    put(inc / f'module-{mid}.json', example())
    put(inc / f'module-{mid}.1.json', example())
    image = inc / 'media' / f'lt-{mid}-slide-grid-01.png'
    image.parent.mkdir()
    image.write_bytes(PNG)
    ref = inc / f'reference-{mid}.json'
    if reference == '{broken':
        ref.write_text(reference, encoding="utf-8", newline="\n")
    else:
        put(ref, reference)
    before = {p.relative_to(inc): p.read_bytes() for p in inc.rglob('*') if p.is_file()}
    r = run(tmp_path, mid)
    assert r.returncode == 2, r.stdout + r.stderr
    assert 'Traceback' not in r.stderr
    assert before == {p.relative_to(inc): p.read_bytes() for p in inc.rglob('*') if p.is_file()}
    assert not cap.exists() or not list(cap.rglob('*.json'))


def test_reference_identity_must_match_module_before_publication(tmp_path):
    # A schema-valid reference for another module must never be filed with this capture.
    inc, cap = layout(tmp_path)
    mid = example()['module_id']
    ref = json.loads(REF_EXAMPLE.read_text(encoding="utf-8"))
    ref['module_id'] = 'c2-r102'
    ref['run_id'] = 102
    put(inc / f'module-{mid}.json', example())
    put(inc / f'reference-{mid}.json', ref)
    r = run(tmp_path, mid)
    assert r.returncode == 2, r.stdout + r.stderr
    assert (inc / f'module-{mid}.json').is_file()
    assert (inc / f'reference-{mid}.json').is_file()
    assert not (cap / f'module-{mid}.json').exists()
    assert not (cap / f'reference-{mid}.json').exists()


def test_stamped_folders_and_files_are_private_under_an_open_umask(tmp_path):
    # Captures hold the student's answers; other accounts must not list or read them.
    inc, cap = layout(tmp_path)
    mid = example()['module_id']
    ref = json.loads(REF_EXAMPLE.read_text(encoding="utf-8"))
    ref['module_id'] = mid
    put(inc / f'module-{mid}.json', example())
    put(inc / f'module-{mid}.1.json', example())
    put(inc / f'reference-{mid}.json', ref)
    image = inc / 'media' / f'lt-{mid}-slide-grid-01.png'
    image.parent.mkdir()
    image.write_bytes(PNG)
    image.chmod(0o644)
    (tmp_path / 'data').chmod(0o755)
    old = os.umask(0)
    try:
        r = run(tmp_path, mid)
    finally:
        os.umask(old)
    assert r.returncode == 0, r.stdout + r.stderr
    for folder in (tmp_path / 'data', cap, cap / 'media', inc / 'superseded'):
        assert folder.stat().st_mode & 0o777 == 0o700, folder
    for f in (cap / f'module-{mid}.json', cap / f'reference-{mid}.json',
              cap / 'media' / f'lt-{mid}-slide-grid-01.png'):
        assert f.stat().st_mode & 0o777 == 0o600, f


def test_a_linked_media_folder_stops_the_stamp_before_any_move(tmp_path):
    inc, cap = layout(tmp_path)
    mid = example()['module_id']
    put(inc / f'module-{mid}.json', example())
    image = inc / 'media' / f'lt-{mid}-slide-grid-01.png'
    image.parent.mkdir()
    image.write_bytes(PNG)
    outside = tmp_path / 'outside'
    outside.mkdir()
    cap.mkdir()
    (cap / 'media').symlink_to(outside, target_is_directory=True)
    r = run(tmp_path, mid)
    assert r.returncode == 3, r.stdout + r.stderr
    assert 'Traceback' not in r.stderr
    assert 'is a link' in r.stderr
    assert list(outside.iterdir()) == []
    assert image.is_file()
    assert (inc / f'module-{mid}.json').is_file()
    assert not (cap / f'module-{mid}.json').exists()


def test_stamp_names_each_missing_key_with_upload_role_and_page(tmp_path):
    stamp = load_stamp()
    doc = {'module_id': 'c1-r101', 'pages': [{'page_index': 4, 'media': [
        {'key': '30', 'role': 'author-supplied'}, {'key': '30', 'role': 'student-upload'},
        {'key': None, 'role': 'student-upload'}], 'items': [{'media': [{'key': '31'}]}]}]}
    missing = []
    assert stamp.backfill_local_paths(doc, tmp_path / 'module.json', tmp_path / 'media',
                                     lambda path: False, missing) == (0, 3, 1)
    assert missing == ['page 4: missing media key 30', 'page 4: missing media key u30',
                       'page 4: missing media key 31']
