# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise staging boundaries, literal captions, quarantine, and flashcard content."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def export_args(fixtures, make_config, tmp_path):
    def build(cfg=None, module_id="c1-r101", answers=True):
        argv = ["--config", cfg or make_config(), "--module",
                fixtures / "modules" / f"module-{module_id}.json",
                "--media-dir", fixtures / "media", "--out-dir", tmp_path / "out"]
        if answers:
            argv += ["--answers", fixtures / "answers" / f"answers-{module_id}.json"]
        return argv
    return build


@pytest.mark.parametrize("name", ["absolute", "../escape.md", "sub/cards.md", r"sub\cards.md"])
def test_configured_output_names_are_single_filenames(run, export_args, make_config, tmp_path, name):
    # An absolute name or separator must never redirect a configured handoff.
    outside = tmp_path / "outside.md"
    if name == "absolute":
        name = str(outside)
    cfg = make_config(export__outputs={"flashcard_source": True},
                      export__flashcard_source={"file": name})
    rc, _, err = run(*export_args(cfg))
    assert rc == 3 and "filename" in err.lower()
    assert not outside.exists()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["page", "asset"])
def test_staging_targets_require_explicit_overwrite(run, export_args, tmp_path, kind):
    # Page and asset copies must preserve existing bytes until --overwrite is explicit.
    out = tmp_path / "out"
    target = out / ("module-page.md" if kind == "page" else "assets/lt-c1-r101-31.png")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing content")
    argv = export_args()
    rc, _, err = run(*argv)
    assert rc == 3 and "--overwrite" in err
    assert target.read_bytes() == b"existing content"
    rc, _, err = run(*argv, "--overwrite")
    assert rc == 0, err
    assert target.read_bytes() != b"existing content"


@pytest.mark.parametrize("kind", ["page", "asset-directory"])
def test_staging_symlinks_cannot_escape_even_with_overwrite(
        run, export_args, tmp_path, kind):
    # Resolved containment must precede writes through an existing symlink.
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.md"
    sentinel.write_text("keep", encoding="utf-8", newline="\n")
    if kind == "page":
        (out / "module-page.md").symlink_to(sentinel)
    else:
        (out / "assets").symlink_to(outside, target_is_directory=True)
    rc, _, err = run(*export_args(), "--overwrite")
    assert rc == 3 and "escapes" in err
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.parametrize("location", ["root", "nested", "symlink"])
def test_course_output_is_refused_before_any_folder_or_asset_write(
        run, export_args, make_config, tmp_path, location):
    # Curriculum containment must apply to export staging before mkdir or copy.
    root = tmp_path / "curriculum"
    cfg = make_config(curriculum={"root": str(root)})
    dest = root if location == "root" else root / "nested"
    if location == "symlink":
        root.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        dest = alias / "nested"
    argv = export_args(cfg)
    argv[argv.index("--out-dir") + 1] = dest
    rc, _, err = run(*argv)
    assert rc == 1 and "curriculum.root" in err
    assert not dest.exists()
    assert not root.exists() or not list(root.iterdir())


@pytest.mark.parametrize("prop", ["lt-exported-by", "lt-run", "lt-source", "lt-captured",
                                  "lt-exported", "title", "type", "tags", "LT-RUN"])
def test_reserved_stamp_properties_are_refused(run, export_args, make_config, tmp_path, prop):
    # A metadata key collision must never erase the module ownership stamp.
    cfg = make_config(notes={"stamp_property": prop})
    rc, _, err = run(*export_args(cfg))
    assert rc == 3 and "reserved" in err.lower()
    assert not (tmp_path / "out").exists()


def test_every_generated_markdown_is_quarantined(run, export_args, make_config, tmp_path):
    from ltcore import quarantine
    cfg = make_config(notes={"stamp_property": "worked-module"},
                      export__outputs={"flashcard_source": True, "ilo_map": True,
                                       "tracker_handoff": True})
    rc, _, err = run(*export_args(cfg))
    assert rc == 0, err
    out = tmp_path / "out"
    pages = set(out.glob("*.md"))
    findings = quarantine.scan(out, stamp_property="worked-module")
    assert len(pages) == 4
    assert {Path(f["path"]) for f in findings} == pages
    assert {f["module_id"] for f in findings} == {"c1-r101"}


def test_missing_generated_stamp_stops_the_write(
        run, export_args, make_config, tmp_path, export_module, monkeypatch):
    # A broken auxiliary renderer must fail closed before its unstamped page is saved.
    cfg = make_config(export__outputs={"ilo_map": True})
    monkeypatch.setattr(export_module, "build_ilo_map", lambda *a: "# unstamped\n")
    rc, _, err = run(*export_args(cfg))
    assert rc == 3 and "stamp" in err
    assert not (tmp_path / "out" / "ilo-map.md").exists()


def test_caption_injection_stays_one_literal_image_node(
        run, export_args, fixtures, tmp_path, need_fonts):
    import module_pdf
    from PIL import Image
    # Captured alt text must never create a second local image node.
    outside = tmp_path / "unrelated.png"
    Image.new("RGB", (20, 20), "red").save(outside)
    module = json.loads((fixtures / "modules/module-c1-r101.json").read_text(encoding="utf-8"))
    media = next(m for p in module["pages"] for i in p.get("items", [])
                 for m in i.get("media", []))
    media["alt"] = f"caption]({outside})\n\t\t- ![extra"
    path = tmp_path / "module.json"
    path.write_text(json.dumps(module), encoding="utf-8", newline="\n")
    argv = export_args(answers=False)
    # Media captions preserve question identity while the copied answer names its capture.
    answer = json.loads((fixtures / "answers/answers-c1-r101.json").read_text(encoding="utf-8"))
    answer["source_module"]["path"] = "module.json"
    answer_path = tmp_path / "answers.json"
    answer_path.write_text(json.dumps(answer), encoding="utf-8", newline="\n")
    argv += ["--answers", answer_path]
    argv[argv.index("--module") + 1] = path
    rc, _, err = run(*argv)
    assert rc == 0, err
    page = tmp_path / "out/module-page.md"
    _, _, blocks = module_pdf.parse(page)
    def nodes(items):
        for node in items:
            yield node
            yield from nodes(node.kids)
    images = [node.text for b in blocks for node in nodes(b["kids"])
              if module_pdf.classify(node) == "image"]
    assert len(images) == 1
    assert all(str(outside) not in module_pdf.IMG.match(t)[2] for t in images)
    assert "&#93;" in page.read_text(encoding="utf-8")
    assert "\n\t\t- ![extra" not in page.read_text(encoding="utf-8")


@pytest.mark.parametrize("variant", ["absolute", "traversal", "symlink", "palette-symlink"])
def test_pdf_images_resolve_only_inside_approved_assets(tmp_path, monkeypatch, need_fonts, variant):
    import module_pdf
    from PIL import Image
    # Direct paths and palette alternatives must not dereference files outside approved assets.
    page = tmp_path / "page"
    assets = page / "assets"
    assets.mkdir(parents=True)
    outside = tmp_path / "unrelated.png"
    Image.new("RGB", (20, 20), "red").save(outside)
    monkeypatch.setattr(module_pdf, "IMAGE_DIRS", [str(assets)])
    flavour = None
    if variant == "absolute":
        ref = str(outside)
    elif variant == "traversal":
        ref = "../unrelated.png"
    elif variant == "symlink":
        (assets / "link.png").symlink_to(outside)
        ref = "assets/link.png"
    else:
        Image.new("RGB", (20, 20), "blue").save(assets / "ok.png")
        (assets / "ok-demo.png").symlink_to(outside)
        ref, flavour = "assets/ok.png", "demo"
    with pytest.raises(ValueError, match="approved asset"):
        module_pdf.image_parts(f"![caption]({ref})", page, flavour)


def test_flashcard_handoff_keeps_table_cells_and_segment_provenance(
        run, export_args, make_config, tmp_path):
    # A handoff that only renders segments loses the actual drafted grid responses.
    cfg = make_config(export__outputs={"flashcard_source": True})
    rc, _, err = run(*export_args(cfg, "c1-r106"))
    assert rc == 0, err
    cards = (tmp_path / "out/flashcard-source.md").read_text(encoding="utf-8")
    assert "**Minute 0 · Goal**" in cards
    cell_line = next(line for line in cards.splitlines() if "hold at minute 0" in line)
    assert "10.5555/fixture.0001" not in cell_line
    assert "([10.5555/fixture.0001](https://doi.org/10.5555/fixture.0001), table 3)" in cards
    assert "hold at minute 75" in cards


@pytest.mark.parametrize("policy", [{"is_report_page": True},
    {"policy_skip": {"reason": "irat"}},
    {"rat_candidate": True, "_rat_verdict": "irat"},
    {"rat_candidate": True},
    {"rat_candidate": True, "_rat_verdict": "trat-answers-unavailable"}])
def test_flashcard_handoff_excludes_protected_pages_but_keeps_worked_pages(
        export_module, make_config, policy):
    from ltcore import config
    # Inverting the flashcard exclusion branch leaks protected answers and removes allowed ones.
    module = {"module_id": "m1-demo", "captured_at": "2000-01-01", "pages": [
        {"page_index": 1, "title": "Allowed", "items": [{"item_id": "ok", "prompt_text": "Allowed prompt"}]},
        {"page_index": 2, "title": "Excluded", "items": [{"item_id": "skip", "prompt_text": "Excluded prompt"}], **policy}]}
    answers = {"items": [
        {"item_id": "ok", "status": "drafted", "segments": [{"text": "Allowed answer"}]},
        {"item_id": "skip", "status": "drafted", "segments": [{"text": "Excluded answer"}]}]}
    cards = export_module.build_flashcard_source(module, answers, None, "1", "Demo", config.load(make_config()))
    assert "Allowed prompt" in cards and "Allowed answer" in cards
    assert "Excluded prompt" not in cards and "Excluded answer" not in cards


def test_T33_T34_rat_summary_reads_no_sheet_and_excludes_flashcards(
        tmp_path, make_config, run, export_module, fixtures):
    import hashlib
    import sys
    from ltcore import config, page_kinds, rat
    root = tmp_path / 'sheets'
    root.mkdir()
    for src in (fixtures / 'curriculum/Block A/RATs').iterdir():
        if src.is_file() and not src.name.startswith('.'):
            (root / src.name).write_bytes(src.read_bytes())
    cfgpath = make_config(rat={'answers_dir': str(root), 'trat': 'from_dir'},
                          export__outputs={'pdf': False, 'flashcard_source': True})
    cfg = config.load(cfgpath)
    modpath = fixtures / 'modules/module-c1-r109.json'
    anspath = fixtures / 'answers/answers-c1-r109.json'
    mod = json.loads(modpath.read_text(encoding="utf-8"))
    ans = json.loads(anspath.read_text(encoding="utf-8"))
    page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans,
                       answers_sha256=hashlib.sha256(anspath.read_bytes()).hexdigest(),
                       path=tmp_path / 'data/runs/c1-r109/page-kinds.json')
    opened = []
    active = [True]
    def audit(event, args):
        if active[0] and event == 'open' and isinstance(args[0], (str, bytes)):
            if Path(os.fsdecode(args[0])).is_relative_to(root):
                opened.append(args[0])
    sys.addaudithook(audit)
    try:
        rc, out, err = run('--module', modpath, '--answers', anspath,
                           '--fill-report', fixtures / 'fill-reports/fill-report-c1-r109.json',
                           '--config', cfgpath, '--out-dir', tmp_path / 'export', '--json')
    finally:
        active[0] = False
    assert rc == 0, out+err
    assert opened == []
    text = (tmp_path / 'export/module-page.md').read_text(encoding="utf-8")
    assert 'Filled from your answer sheet 1.2-team.md: 4 of 5 questions; 1 left for you.' in text
    for forbidden in ('Desert oasis', 'Amber spindle', 'p3-81', 'Choose a fictional object'):
        assert forbidden not in text
    handoff = (tmp_path / 'export/flashcard-source.md').read_text(encoding="utf-8")
    assert 'p3-' not in handoff and 'Desert oasis' not in handoff


@pytest.mark.parametrize('stale', [False, True])
def test_custom_marker_without_classification_holds_export(run, fixtures, make_config, tmp_path, stale):
    from ltcore import config, page_kinds, rat
    import hashlib
    mod = json.loads((fixtures/'modules/module-c1-r101.json').read_text(encoding="utf-8"))
    ans = json.loads((fixtures/'answers/answers-c1-r101.json').read_text(encoding="utf-8"))
    page = next(p for p in mod['pages'] if any(a['status'] == 'drafted' and a['item_id'] in
                {i['item_id'] for i in p['items']} for a in ans['items']))
    page['content'] = [{'panel_key': 'synthetic-marker', 'plugin_key': 'kcTextArea', 'kind': 'text', 'text': 'Team examination'}]
    page.pop('rat_candidate', None)
    for a in ans['items']:
        if a['status'] == 'drafted' and a['item_id'] in {i['item_id'] for i in page['items']}:
            for n, segment in enumerate(a.get('segments', [])):
                if segment['kind'] == 'claim':
                    segment['text'] = f'Invented export omission sentinel {a["item_id"]} {n} ' * 8
    cfgpath = make_config(rat__kind_markers={'trat': 'Team examination'})
    mp, ap = tmp_path/'module.json', tmp_path/'answers.json'
    mp.write_text(json.dumps(mod), encoding="utf-8", newline="\n")
    ans['source_module'].update(path=str(mp), sha256=hashlib.sha256(mp.read_bytes()).hexdigest())
    ap.write_text(json.dumps(ans), encoding="utf-8", newline="\n")
    if stale:
        cfg = config.load(cfgpath)
        page_kinds.classify(mod, rat.policy_from_config(cfg), answers=ans,
                           answers_sha256=hashlib.sha256(ap.read_bytes()).hexdigest(),
                           path=tmp_path/'data/runs/c1-r101/page-kinds.json')
        page['content'][0]['text'] += ' changed'
    mp.write_text(json.dumps(mod), encoding="utf-8", newline="\n")
    ans['source_module']['sha256'] = hashlib.sha256(mp.read_bytes()).hexdigest()
    ap.write_text(json.dumps(ans), encoding="utf-8", newline="\n")
    rc, out, err = run('--config', cfgpath, '--module', mp, '--answers', ap,
                       '--out-dir', tmp_path/'export')
    assert rc == 0, out + err
    text = (tmp_path/'export/module-page.md').read_text(encoding="utf-8")
    assert 'Team examination' not in text
    assert 'Invented export omission sentinel' not in text


def test_rat_learning_outcome_export_refuses_before_read(export_module, make_config, tmp_path, monkeypatch):
    from ltcore import config
    sheets = tmp_path/'sheets'
    sheets.mkdir()
    sheet = sheets/'outcomes.md'
    sheet.write_text('## Week 1 Demo\nInvented selection\n', encoding="utf-8", newline="\n")
    alias = tmp_path/'outcomes.md'
    alias.symlink_to(sheet)
    cfg = config.load(make_config(rat={'answers_dir': str(sheets), 'irat': 'skip', 'trat': 'skip'}))
    opened = []
    original = Path.open
    def watched(path, *args, **kwargs):
        if path.resolve() == sheet:
            opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', watched)
    with pytest.raises(ValueError, match='RAT root'):
        export_module.parse_ilo_section(alias, '1', 'Demo', cfg)
    assert opened == []
