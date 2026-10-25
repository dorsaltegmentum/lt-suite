# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""export_module.py end to end on the synthetic fixture captures.

These cases run the real input validator. They cover the output switches, the stamp, the
install rails (never overwrite silently, never overwrite a foreign file), the notes
formats, titles that try to leave the pages folder, the run record and the exit codes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def args(fixtures, tmp_path, make_config):
    """args(module_id, **extra) -> argv for an export of one fixture capture."""
    def build(module_id="c1-r101", *, config=None, answers=True, fill=True, extra=()):
        argv = ["--config", config or make_config(), "--module",
                fixtures / "modules" / f"module-{module_id}.json",
                "--media-dir", fixtures / "media", "--out-dir", tmp_path / "out"]
        a = fixtures / "answers" / f"answers-{module_id}.json"
        f = fixtures / "fill-reports" / f"fill-report-{module_id}.json"
        if answers and a.is_file():
            argv += ["--answers", a]
        if fill and f.is_file():
            argv += ["--fill-report", f]
        return argv + list(extra)
    return build


def graph_config(make_config, tmp_path, fmt="logseq", namespace="Study", **outputs):
    """A config with a notes graph under tmp_path and a custom page namespace, so the
    install tests also prove the namespace reaches page names, tags and whiteboards."""
    graph = tmp_path / "graph"
    (graph / "pages").mkdir(parents=True, exist_ok=True)
    out = {"notes_graph": True, "pdf": False}
    out.update(outputs)
    return make_config(notes={"graph_path": str(graph), "format": fmt,
                              "page_namespace": namespace},
                       export__outputs=out), graph


# ------------------------------------------------------------------ outputs and stamp

def test_default_export_writes_the_page_assets_and_run_record(run, args, tmp_path):
    rc, out, err = run(*args(), "--json")
    assert rc == 0, err
    report = json.loads(out)
    page = (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8")
    assert page.startswith("title:: Lt/1.1 Demo: Vorlase deficiency\ntype:: **Lt Module Output**\n")
    assert "\nlt-module:: c1-r101\n" in page
    assert (tmp_path / "out" / "assets" / "lt-c1-r101-31.png").is_file()
    for name in ("ilo-map.md", "tracker-handoff.md", "flashcard-source.md"):
        assert not (tmp_path / "out" / name).exists(), f"{name} is off by default"
    record = json.loads(Path(report["run_record"]).read_text(encoding="utf-8"))
    assert record["module_id"] == "c1-r101"
    assert record["tool_version"] and len(record["config_digest"]) == 64
    assert "permission_check" not in record
    assert report["pages_skipped"][0]["reason"] == "report-page"


def test_each_extra_output_follows_its_switch(run, args, tmp_path, make_config):
    cfg = make_config(export__outputs={"pdf": False, "ilo_map": True, "tracker_handoff": True,
                                       "flashcard_source": True},
                      export__flashcard_source={"file": "cards-source.md", "tag_prefix": "T::"})
    rc, out, err = run(*args(config=cfg))
    assert rc == 0, err
    d = tmp_path / "out"
    ilo = (d / "ilo-map.md").read_text(encoding="utf-8")
    assert ilo.startswith("# 1.1 Demo: Vorlase deficiency: learning outcome map")
    assert "1. Describe how vorlase turns tessin into quillic acid." in ilo
    assert "⚠ No item in this module cites this outcome" in ilo or "p2-1" in ilo
    tracker = (d / "tracker-handoff.md").read_text(encoding="utf-8")
    assert "- TODO Describe how vorlase" in tracker and tracker.startswith("title:: ")
    cards = (d / "cards-source.md").read_text(encoding="utf-8")
    assert cards.startswith("<!-- lt-module: c1-r101 -->\n# 1.1 Demo: Vorlase deficiency")
    assert "\nTag: T::1.1\n" in cards


def test_module_page_switch_off_writes_the_page_only_when_something_needs_it(
        run, args, tmp_path, make_config):
    cfg = make_config(export__outputs={"pdf": False, "module_page": False})
    rc, _, err = run(*args(config=cfg))
    assert rc == 0, err
    assert not (tmp_path / "out" / "module-page.md").exists()
    cfg, graph = graph_config(make_config, tmp_path, module_page=False)
    rc, _, err = run(*args(config=cfg), "--install", "--overwrite")
    assert rc == 0, err
    assert (tmp_path / "out" / "module-page.md").is_file(), "--install needs the page"


def test_week_override_and_missing_week(run, args, tmp_path):
    rc, out, err = run(*args("demo"))
    assert rc == 1 and "pass --week" in err
    rc, out, err = run(*args("demo", extra=["--week", "9.9"]))
    assert rc == 0, err
    assert (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8").startswith(
        "title:: Lt/9.9 Demo: Quick tour\n")


def test_json_stdout_is_only_the_report(run, args):
    rc, out, err = run(*args(), "--json")
    assert rc == 0
    assert set(json.loads(out)) >= {"wrote", "blocked", "links", "warnings", "run_record"}


def test_legacy_permission_field_is_dropped_with_one_note(run, args, fixtures, tmp_path):
    module = json.loads((fixtures / "modules" / "module-c1-r102.json").read_text(encoding="utf-8"))
    module["permission"] = {"unit_code": "X101", "exit_code": 0, "checked_at": "2000-01-01"}
    path = tmp_path / "legacy" / "module-c1-r102.json"
    path.parent.mkdir()
    path.write_text(json.dumps(module), encoding="utf-8", newline="\n")
    argv = args("c1-r102", answers=False, fill=False)
    argv[argv.index("--module") + 1] = path
    rc, out, err = run(*argv)
    assert rc == 0, err
    assert err.count("dropped legacy field(s): permission") == 1


# ------------------------------------------------------------------ rails on inputs

def test_invalid_module_exits_2_and_writes_nothing(run, args, fixtures, tmp_path):
    module = json.loads((fixtures / "modules" / "module-c1-r102.json").read_text(encoding="utf-8"))
    del module["census"]
    path = tmp_path / "bad" / "module-c1-r102.json"
    path.parent.mkdir()
    path.write_text(json.dumps(module), encoding="utf-8", newline="\n")
    argv = args("c1-r102", answers=False, fill=False)
    argv[argv.index("--module") + 1] = path
    rc, out, err = run(*argv)
    assert rc == 2 and "did not validate" in err
    assert not (tmp_path / "out").exists()


def test_missing_validator_exits_3_and_exports_nothing(run, args, tmp_path, monkeypatch):
    import ltcore
    monkeypatch.setitem(sys.modules, "ltcore.validate", None)
    monkeypatch.delattr(ltcore, "validate", raising=False)
    rc, out, err = run(*args())
    assert rc == 3 and "ltcore.validate is not available" in err
    assert not (tmp_path / "out").exists()


def test_answers_for_changed_questions_exit_1(run, args, fixtures, tmp_path):
    answers = json.loads((fixtures / "answers" / "answers-c1-r101.json").read_text(encoding="utf-8"))
    answers["source_module"]["question_digest"] = "0" * 64
    path = tmp_path / "answers" / "answers-c1-r101.json"
    path.parent.mkdir()
    # the pinned path is relative to the answers file, so point it at the fixture capture
    answers["source_module"]["path"] = str(fixtures / "modules" / "module-c1-r101.json")
    path.write_text(json.dumps(answers), encoding="utf-8", newline="\n")
    argv = args(answers=False, fill=False) + ["--answers", path]
    rc, out, err = run(*argv)
    assert rc in (1, 2), err
    assert "question" in err.lower() or "digest" in err.lower()
    assert not (tmp_path / "out" / "module-page.md").exists()


def test_unsafe_module_id_exits_1(run, args, export_module, tmp_path, monkeypatch, fixtures):
    from ltcore import validate
    monkeypatch.setattr(validate, "validate_file", lambda *a, **k: validate.Result("stub"))
    module = json.loads((fixtures / "modules" / "module-c1-r102.json").read_text(encoding="utf-8"))
    module["module_id"] = "../escape"
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(module), encoding="utf-8", newline="\n")
    argv = args("c1-r102", answers=False, fill=False)
    argv[argv.index("--module") + 1] = path
    rc, out, err = run(*argv)
    assert rc == 1 and "unsafe module id" in err


# ------------------------------------------------------------------ install rails

def test_install_needs_the_notes_graph_switch_and_path(run, args, make_config, tmp_path):
    rc, out, err = run(*args(), "--install")
    assert rc == 3 and "export.outputs.notes_graph" in err
    cfg = make_config(export__outputs={"pdf": False, "notes_graph": True})
    rc, out, err = run(*args(config=cfg), "--install")
    assert rc == 3 and "notes.graph_path is not set" in err


def test_install_is_refused_by_the_environment_guard(run, args, make_config, tmp_path, monkeypatch):
    cfg, graph = graph_config(make_config, tmp_path)
    monkeypatch.setenv("LT_SUITE_NO_INSTALL", "1")
    rc, out, err = run(*args(config=cfg), "--install")
    assert rc == 5 and "LT_SUITE_NO_INSTALL" in err
    assert not list((graph / "pages").iterdir())


def test_install_writes_page_and_assets_into_the_graph_only(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path)
    rc, out, err = run(*args(config=cfg), "--install")
    assert rc == 0, err
    pages = sorted(p.name for p in (graph / "pages").iterdir())
    assert pages == ["Study___1.1 Demo%3A Vorlase deficiency.md"]
    assert (graph / "assets" / "lt-c1-r101-31.png").is_file()
    assert not (graph / "whiteboards").exists(), "the whiteboard is off by default"
    written = {p.relative_to(tmp_path).parts[0] for p in tmp_path.rglob("*") if p.is_file()}
    assert written <= {"graph", "out", "data", "lt-suite.toml"}


@pytest.mark.parametrize('line_ending', [b'\n', b'\r\n'])
def test_reinstall_of_unchanged_output_is_identical_and_silent(run, args, make_config, tmp_path, line_ending):
    cfg, graph = graph_config(make_config, tmp_path)
    assert run(*args(config=cfg), "--install")[0] == 0
    page = next((graph / "pages").iterdir())
    page.write_bytes(page.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', line_ending))
    before = page.stat().st_mtime_ns
    rc, out, err = run(*args(config=cfg), "--install", "--overwrite")
    assert rc == 0, err
    assert page.stat().st_mtime_ns == before, "an identical page is not rewritten"


def test_hand_edited_page_blocks_then_replace_keeps_the_diff(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path)
    assert run(*args(config=cfg), "--install")[0] == 0
    page = next((graph / "pages").iterdir())
    page.write_text(page.read_text(encoding="utf-8") + "- my own note\n", encoding="utf-8", newline="\n")
    edited = page.read_text(encoding="utf-8")
    rc, out, err = run(*args(config=cfg), "--install", "--overwrite")
    assert rc == 4 and "carries this module's stamp" in err
    assert page.read_text(encoding="utf-8") == edited
    diff = tmp_path / "out" / (page.name + ".diff")
    assert "my own note" in diff.read_text(encoding="utf-8")
    rc, out, err = run(*args(config=cfg), "--install", "--replace", "--overwrite")
    assert rc == 0, err
    assert "my own note" not in page.read_text(encoding="utf-8")
    assert "my own note" in diff.read_text(encoding="utf-8"), "the diff of the hand edit is kept"


def test_foreign_file_is_never_overwritten_even_with_replace(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path)
    target = graph / "pages" / "Study___1.1 Demo%3A Vorlase deficiency.md"
    target.write_text("# somebody else's page\n", encoding="utf-8", newline="\n")
    for extra in ([], ["--replace"]):
        rc, out, err = run(*args(config=cfg), "--install", "--overwrite", *extra)
        assert rc == 4 and "cannot be overwritten" in err
        assert target.read_text(encoding="utf-8") == "# somebody else's page\n"
    assert not (graph / "assets").exists(), "nothing else was installed either"


def test_page_stamped_for_another_module_is_foreign(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path)
    target = graph / "pages" / "Study___1.1 Demo%3A Vorlase deficiency.md"
    target.write_text("title:: x\nlt-module:: c9-r999\n\n- other module\n", encoding="utf-8", newline="\n")
    rc, out, err = run(*args(config=cfg), "--install", "--replace")
    assert rc == 4
    assert "c9-r999" in target.read_text(encoding="utf-8")


def test_owned_page_symlink_cannot_write_outside_graph(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("title:: External\nlt-module:: c1-r101\n\n- keep this\n", encoding="utf-8", newline="\n")
    before = outside.read_bytes()
    target = graph / "pages" / "Study___1.1 Demo%3A Vorlase deficiency.md"
    target.symlink_to(outside)
    rc, _, err = run(*args(config=cfg), "--install", "--replace")
    assert rc == 3 and "escapes the notes graph" in err
    assert outside.read_bytes() == before
    assert not (graph / "assets").exists()


def test_whiteboard_only_for_logseq(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path, whiteboard=True)
    assert run(*args(config=cfg), "--install")[0] == 0
    board = graph / "whiteboards" / "Study-c1-r101.edn"
    assert ':block/original-name "Study-c1-r101"' in board.read_text(encoding="utf-8")
    cfg2, graph2 = graph_config(make_config, tmp_path / "second", fmt="obsidian", whiteboard=True)
    rc, out, err = run(*args(config=cfg2), "--json", "--overwrite")
    assert rc == 0
    assert any("no whiteboard was written" in w for w in json.loads(out)["warnings"])


# ------------------------------------------------------------------ links and citations

def test_links_point_only_at_existing_pages_once(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path)
    (graph / "pages" / "Tessin.md").write_text("title:: Tessin\n\n- a page\n", encoding="utf-8", newline="\n")
    rc, out, err = run(*args(config=cfg), "--json")
    assert rc == 0, err
    report = json.loads(out)
    assert report["links"] == ["Tessin"]
    page = (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8")
    assert page.count("[[Tessin]]") == 1, "first occurrence only"
    assert "[[Quillic" not in page, "no link is invented for a page that does not exist"
    # the question text is reproduced as captured and never linked
    assert "## Explain why tessin builds up when vorlase is missing." in page


def test_citations_follow_the_aliases(run, args, make_config, tmp_path):
    cfg = make_config(export__citations={
        "page_aliases": {"DEMO-1-1-SLIDES": "Demo slides"},
        "url_aliases": {"DEMO-1-1-TRANSCRIPT": "https://lt.example.invalid/transcript"}})
    rc, out, err = run(*args(config=cfg))
    assert rc == 0, err
    page = (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8")
    assert "([[Demo slides]], slide 4)" in page
    assert "([DEMO-1-1-TRANSCRIPT](https://lt.example.invalid/transcript), 00:05:40)" in page


def test_hidden_skip_reasons_leave_items_off_the_page(run, args, make_config, tmp_path):
    rc, out, err = run(*args())
    assert "p2-13 — already-answered" in (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8")
    cfg = make_config(export__citations={"hidden_skip_reasons": ["already-answered"]})
    rc, out, err = run(*args(config=cfg), "--overwrite")
    assert rc == 0, err
    assert "already-answered" not in (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ notes formats

def test_obsidian_page_has_front_matter_stamp_and_relative_assets(
        run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path, fmt="obsidian")
    rc, out, err = run(*args(config=cfg), "--install")
    assert rc == 0, err
    page = graph / "pages" / "Study" / "1.1 Demo- Vorlase deficiency.md"
    text = page.read_text(encoding="utf-8")
    assert text.startswith('---\ntitle: "Study/1.1 Demo: Vorlase deficiency"\n')
    assert 'tags: ["Study", "module output"]' in text
    assert "\nlt-module:: c1-r101\n" in text, "the stamp line the quarantine scan reads"
    assert "](../../assets/lt-c1-r101-32.png)" in text
    assert "\n## Describe the first sign" in text
    assert "::" not in text.split("---\n", 2)[1], "no Logseq property syntax in the front matter"


def test_markdown_format_has_no_wiki_links(run, args, make_config, tmp_path):
    cfg, graph = graph_config(make_config, tmp_path, fmt="markdown")
    (graph / "pages" / "Tessin.md").write_text("# Tessin\n", encoding="utf-8", newline="\n")
    rc, out, err = run(*args(config=cfg), "--install")
    assert rc == 0, err
    text = (graph / "pages" / "Study" / "1.1 Demo- Vorlase deficiency.md").read_text(encoding="utf-8")
    assert "[[" not in text


def test_legacy_file_name_format(run, args, make_config, tmp_path):
    graph = tmp_path / "graph"
    (graph / "pages").mkdir(parents=True)
    cfg = make_config(notes={"graph_path": str(graph), "file_name_format": "legacy"},
                      export__outputs={"pdf": False, "notes_graph": True})
    assert run(*args(config=cfg), "--install")[0] == 0
    assert [p.name for p in (graph / "pages").iterdir()] == ["Lt.1.1 Demo%3A Vorlase deficiency.md"]


@pytest.mark.parametrize("title", ["Lt/../../outside", "../../outside", "a/./b/../../../c",
                                   "Lt/NUL\x00name", "Lt/C:\\evil"])
def test_page_titles_never_leave_the_pages_folder(export_module, tmp_path, title):
    pages = tmp_path / "pages"
    pages.mkdir()
    path = export_module.safe_page_path(pages, title)
    assert path.resolve().is_relative_to(pages.resolve())
    assert path.suffix == ".md" and ".." not in path.relative_to(pages).parts


def test_stamp_probe_reads_all_three_forms(export_module):
    probe = export_module.make_probe_markdown("lt-module")
    assert probe(Path("x"), b"title:: t\nlt-module:: c1-r101\n") == "c1-r101"
    assert probe(Path("x"), b'---\nlt-module: "c1-r101"\n---\n') == "c1-r101"
    assert probe(Path("x"), b"<!-- lt-module: c1-r101 -->\n") == "c1-r101"
    assert probe(Path("x"), b"no stamp here\n") is None


def test_configured_stamp_property_is_written_and_probed(run, args, make_config, tmp_path):
    cfg = make_config(notes={"stamp_property": "mod-out"})
    rc, out, err = run(*args(config=cfg))
    assert rc == 0, err
    page = (tmp_path / "out" / "module-page.md").read_text(encoding="utf-8")
    assert "\nmod-out:: c1-r101\n" in page and "\nlt-module::" not in page


def test_every_markdown_handoff_is_quarantined(run, args, make_config, tmp_path):
    from ltcore import quarantine
    cfg = make_config(notes={"stamp_property": "worked-module"},
                      export__outputs={"ilo_map": True, "tracker_handoff": True,
                                       "flashcard_source": True})
    rc, _, err = run(*args(config=cfg))
    assert rc == 0, err
    findings = quarantine.scan(tmp_path / "out", stamp_property="worked-module")
    assert {Path(row["path"]).name for row in findings} == {
        "module-page.md", "ilo-map.md", "tracker-handoff.md", "flashcard-source.md"}


# ------------------------------------------------------------------ week, outcomes, RAT

def test_week_and_title_parsing(export_module, make_config):
    from ltcore import config
    cfg = config.load(make_config())
    parse = lambda t: export_module.lesson_week_title({"lesson": {"title": t}}, cfg)
    assert parse("1.1 Demo: Vorlase") == ("1.1", "Demo: Vorlase")
    assert parse("Week 4.2 - Renal") == ("4.2", "Renal")
    assert parse("3. Topic") == (None, "Topic")
    assert parse("7") == (None, "Module 7")
    assert parse("") == (None, "")


def test_learning_outcomes_use_the_configured_heading_pattern(export_module, make_config, fixtures):
    from ltcore import config
    cfg = config.load(make_config())
    ilo = export_module.parse_ilo_section(
        fixtures / "curriculum" / "ILO.md", "1.2", "Demo: Quillen gland function", cfg)
    assert ilo["outcomes"] == ["Describe how the quillen gland releases tessin.",
                               "Name the cell that paces release."]
    assert export_module.parse_ilo_section(
        fixtures / "curriculum" / "ILO.md", "1.2", "Unrelated title", cfg) is None


def test_ambiguous_learning_outcome_sections_match_nothing(export_module, make_config, tmp_path):
    from ltcore import config
    cfg = config.load(make_config())
    ilo = tmp_path / "ILO.md"
    ilo.write_text("## 6.4 Alpha beta\n- one\n\n## 6.4 Gamma delta\n- two\n", encoding="utf-8", newline="\n")
    assert export_module.parse_ilo_section(ilo, "6.4", "Epsilon zeta", cfg) is None
    assert export_module.parse_ilo_section(ilo, "6.4", "Gamma delta", cfg)["outcomes"] == ["two"]


def test_rat_pages_are_named_skips_until_classified(run, args):
    rc, out, err = run(*args("c1-r104", answers=False, fill=False), "--json")
    assert rc == 0, err
    report = json.loads(out)
    reasons = {p["reason"] for p in report["pages_skipped"]}
    assert "rat-unclassified" in reasons or reasons - {"report-page"}
    rc, out, err = run(*args("c1-r104", answers=False, fill=False, extra=["--rat", "trat"]), "--json", "--overwrite")
    assert rc == 0, err
    assert "rat-unclassified" not in {p["reason"] for p in json.loads(out)["pages_skipped"]}


# ------------------------------------------------------------------ finding stamped pages

def test_list_stamped_and_check_page(run, args, make_config, tmp_path, capsys):
    cfg, graph = graph_config(make_config, tmp_path)
    assert run(*args(config=cfg), "--install")[0] == 0
    ordinary = graph / "pages" / "Notes.md"
    ordinary.write_text("title:: Notes\n\n- plain\n", encoding="utf-8", newline="\n")
    page = next(p for p in (graph / "pages").iterdir() if p.name.startswith("Study___"))
    rc, out, err = run("--config", cfg, "--list-stamped")
    assert rc == 0 and "c1-r101" in out and "Notes" not in out
    assert run("--config", cfg, "--check-page", page)[0] == 1
    assert run("--config", cfg, "--check-page", ordinary)[0] == 0


# ------------------------------------------------------------------ PDFs through the export

def test_export_makes_final_pdfs_and_records_the_pass(run, args, make_config, tmp_path, need_fonts):
    cfg = make_config(export__outputs={"pdf": True},
                      style={"palettes": ["catppuccin-latte", "catppuccin-frappe"],
                             "print_palette": "catppuccin-latte"})
    rc, out, err = run(*args(config=cfg), "--json")
    assert rc == 0, err
    report = json.loads(out)
    assert report["pdf"]["passed"] is True
    names = sorted(Path(f).name for f in report["pdf"]["files"])
    assert names == ["1.1 Demo- Vorlase deficiency (catppuccin-frappe, screen).pdf",
                     "1.1 Demo- Vorlase deficiency (catppuccin-latte, print).pdf"]
    assert (tmp_path / "out" / "pdf" / ".final.json").is_file()
    assert out.lstrip().startswith("{"), "stdout holds only the JSON report"


def test_export_timestamp_honors_reproducible_build_epoch(export_module, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "946684800")
    assert export_module.now_iso() == "2000-01-01T00:00:00+00:00"


def _memory_pdf(cfg):
    import io
    import re
    import pdf_base as B
    from reportlab.pdfgen.canvas import Canvas
    faces, notes = B.resolve_faces(cfg)
    B.register_fonts(faces)
    out = io.BytesIO()
    canvas = Canvas(out)
    canvas.setFont('Sans',12)
    canvas.drawString(20,20,'A synthetic page body')
    canvas.save()
    names = set(re.findall(rb'/BaseFont\s*/([^\s/>]+)',out.getvalue()))
    return names, notes


def test_pdf_uses_pdf_fonts(font_file):
    _memory_pdf({})  # A prior export must not pin ReportLab's Sans alias.
    path = font_file('Test PDF')
    names,_ = _memory_pdf({'fonts.pdf.sans':str(path)})
    assert any(b'TestPDF' in n for n in names), names
    assert all(b'NotoSans-' not in n for n in names)


def test_diagram_fonts_do_not_reach_pdf_body(font_file):
    path = font_file('Test Diagram')
    names,_ = _memory_pdf({'fonts.diagram.sans':str(path)})
    assert any(b'NotoSans' in n for n in names)
    assert all(b'TestDiagram' not in n for n in names)


def test_pdf_gate_runs_on_every_face(font_file, monkeypatch):
    from ltcore import fonts as F
    import pdf_base as B
    bad = font_file('Refused Test',fs_type=2)
    names,notes = _memory_pdf({'fonts.pdf.sans':str(bad)})
    assert all(b'RefusedTest' not in n for n in names)
    assert any('fsType 0x0002' in n for n in notes)
    monkeypatch.setattr(F,'bundled_files',lambda e: [])
    monkeypatch.setattr(F,'_data_fonts_dir',lambda *a: None)
    monkeypatch.setattr(F,'fc_match',lambda *a,**k:None)
    monkeypatch.setattr(F,'scan_family',lambda *a,**k:[])
    monkeypatch.setattr(F,'_os_candidates',lambda *a: iter([F.Font('sans','Refused Test',bad,'system fallback',os_fallback=True)]))
    with pytest.raises(B.FontUnavailable,match='fsType 0x0002'):
        _memory_pdf({})
    monkeypatch.setattr(F,'resolve',lambda *a,**k:bad)
    assert B.find_symbol_font({'a'}) is None


def test_equilibrium_arrows_have_stand_ins(monkeypatch):
    import pdf_base as B
    monkeypatch.setattr(B,'_cmap',lambda *a:set())
    monkeypatch.setattr(B,'find_symbol_font',lambda *a:None)
    faces={name:('fake.ttf',0) for name in B.FACE_SPEC}
    plan=B.plan_glyphs(set('⇄⇌⇒'),faces)
    assert not plan['missing']
    assert plan['subs'] == {'⇄':'<->','⇌':'<=>','⇒':'=>'}


def test_svg_assets_carry_the_chain(run,args,tmp_path):
    directory=tmp_path/'diagrams'
    directory.mkdir()
    source=directory/'test.svg'
    original='<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Noto Sans">Test</text></svg>'
    source.write_text(original, encoding="utf-8", newline="\n")
    rc,_,err=run(*args(extra=['--diagram-dir',directory]))
    assert rc==0,err
    copy=tmp_path/'out/assets/lt-c1-r101-test.svg'
    assert 'Segoe UI' in copy.read_text(encoding="utf-8") and source.read_text(encoding="utf-8")==original


def _bound_images(directory):
    from PIL import Image
    from ltcore import palettes as P
    directory.mkdir(parents=True,exist_ok=True)
    linked=directory/'lt-m-u5.png'
    Image.new('RGB',(80,60),'red').save(linked)
    copies={}
    for pid,colour,printed in [('tol-bright','green',True),('catppuccin-frappe','blue',False)]:
        sibling=directory/f'{linked.stem}-{pid}.png'
        Image.new('RGB',(80,60),colour).save(sibling)
        copies[pid]={'file':sibling.name,'sha256':P.file_sha256(sibling),'print':printed}
    manifest={'upload':{'file':linked.name,'sha256':P.file_sha256(linked)},'media_sha256':None,'copies':copies}
    P.copies_manifest(linked).write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
    return linked,manifest


def _image_pdf_colours(tmp_path, linked, cfg):
    """Drive render_page's actual print/screen selection and inspect decoded PDF image pixels."""
    import pdf_base as B
    import module_pdf as M
    import pypdfium2 as pdfium
    B.register_fonts(cfg=cfg)
    page=tmp_path/'page.md'
    page.write_text('title:: Image test\ntype:: **Lt Module Output**\n\n- # Page\n\t- ![Test](assets/'+linked.name+')\n', encoding="utf-8", newline="\n")
    reports=M.render_page(page,tmp_path/'pdfs',cfg)
    out={}
    for pid,report in reports.items():
        with pdfium.PdfDocument(report['path']) as doc:
            pixels=[]
            for pg in doc:
                for obj in pg.get_objects():
                    if isinstance(obj,pdfium.PdfImage):
                        image=obj.get_bitmap().to_pil().convert('RGB')
                        pixels.append(image.getpixel((image.width//2,image.height//2)))
            out[pid]=pixels
    return out


def test_print_pdf_takes_print_palette_copy(tmp_path,make_config):
    from ltcore import config,palettes as P
    cfg=config.load(make_config(style={'palettes':['catppuccin-frappe'],'print_palette':'tol-bright'}))
    linked,_=_bound_images(tmp_path/'assets')
    colours=_image_pdf_colours(tmp_path,linked,cfg)
    assert (0,128,0) in colours['tol-bright'] and (0,0,255) in colours['catppuccin-frappe']
    P.copies_manifest(linked).unlink()
    colours=_image_pdf_colours(tmp_path,linked,cfg)
    assert (255,0,0) in colours['tol-bright'] and (0,0,255) in colours['catppuccin-frappe']


@pytest.mark.parametrize('change',['upload','disable','edited'])
def test_stale_copies_never_reach_a_pdf(tmp_path,make_config,change,capsys):
    from PIL import Image
    from ltcore import config,palettes as P
    cfg=config.load(make_config(style={'palettes':['catppuccin-frappe'],'print_palette':'tol-bright'}))
    linked,manifest=_bound_images(tmp_path/'assets')
    if change=='upload':
        Image.new('RGB',(90,70),'red').save(linked)
    elif change=='disable':
        manifest['copies']={}
        P.copies_manifest(linked).write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
    else:
        for record in manifest['copies'].values():
            (linked.parent/record['file']).write_bytes(b'edited')
    colours=_image_pdf_colours(tmp_path,linked,cfg)
    assert all((255,0,0) in values and (0,128,0) not in values and (0,0,255) not in values for values in colours.values())
    assert '--file-copies' in capsys.readouterr().err


def test_palette_copies_reach_assets(run,args,tmp_path,fixtures):
    from ltcore import palettes as P
    import shutil
    directory=tmp_path/'media'
    directory.mkdir()
    for p in (fixtures/'media').iterdir():
        if p.is_file(): shutil.copyfile(p,directory/p.name)
    source=next(p for p in directory.iterdir() if p.name.startswith('lt-c1-r101-'))
    pid=P.pdf_palette_ids({'style.palettes':['tol-bright'],'style.print_palette':'tol-bright'})[0]
    sibling=source.with_name(f'{source.stem}-{pid}{source.suffix}')
    shutil.copyfile(source,sibling)
    extra=source.with_name(f'{source.stem}-other{source.suffix}')
    shutil.copyfile(source,extra)
    manifest={'upload':{'file':source.name,'sha256':P.file_sha256(source)},'media_sha256':None,
              'copies':{pid:{'file':sibling.name,'sha256':P.file_sha256(sibling),'print':True}}}
    P.copies_manifest(source).write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
    argv=args()
    argv[argv.index('--media-dir')+1]=directory
    rc,_,err=run(*argv)
    assert rc==0,err
    assets=tmp_path/'out/assets'
    assert (assets/sibling.name).is_file() and (assets/P.copies_manifest(source).name).is_file()
    assert not (assets/extra.name).exists()


@pytest.mark.parametrize('field,value', [
    ('upload', 'invalid'), ('upload', []), ('upload', None),
    ('upload.file', []), ('upload.sha256', []), ('media_sha256', {}),
    ('copy', 'invalid'), ('copy', None), ('copy.file', []),
    ('copy.sha256', {}), ('copy.print', 'yes'),
])
def test_malformed_copy_manifest_is_rejected(tmp_path, field, value):
    """Malformed nested fields must never escape the manifest trust boundary."""
    from ltcore import palettes as P
    linked, manifest = _bound_images(tmp_path / 'assets')
    if field.startswith('copy.'):
        manifest['copies']['tol-bright'][field.split('.')[1]] = value
    elif field == 'copy':
        manifest['copies']['tol-bright'] = value
    elif field.startswith('upload.'):
        manifest['upload'][field.split('.')[1]] = value
    else:
        manifest[field] = value
    P.copies_manifest(linked).write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
    try:
        selected = P.bound_copy(linked, 'tol-bright')
    except (AttributeError, TypeError, KeyError) as exc:
        pytest.fail(f'malformed manifest escaped validation: {exc}')
    assert selected is None
    assert P.read_copies(linked) is None


def test_malformed_copy_manifest_keeps_linked_image_in_pdf(tmp_path, make_config, capsys):
    """A malformed upload field must fall back to the linked image in both PDFs."""
    from ltcore import config, palettes as P
    cfg = config.load(make_config(style={'palettes': ['catppuccin-frappe'], 'print_palette': 'tol-bright'}))
    linked, manifest = _bound_images(tmp_path / 'assets')
    manifest['upload'] = 'invalid'
    P.copies_manifest(linked).write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
    colours = _image_pdf_colours(tmp_path, linked, cfg)
    assert all((255, 0, 0) in values and (0, 128, 0) not in values and (0, 0, 255) not in values
               for values in colours.values())
    assert '--file-copies' in capsys.readouterr().err


def test_reconciled_check_export_tags_and_dispute(run,args,fixtures,tmp_path):
    rec=fixtures/'runs/c1-r108/reconciled.json'
    rc,out,err=run(*args('c1-r108',extra=['--reconciled',rec]))
    assert rc==0,err+out
    page=(tmp_path/'out/module-page.md').read_text(encoding="utf-8")
    assert "accepted by Lt's Check Answer after 2 presses" in page
    assert "Lt's key contradicts the cited sources" in page
    assert 'Cited draft:' in page and 'Dropped draft segment:' in page
    assert 'slide 2' in page and 'slide 3' in page


def test_unreconciled_unsuccessful_restore_is_never_called_accepted(run,args,tmp_path):
    rc,out,err=run(*args('c1-r108'))
    assert rc==0,err+out
    page=(tmp_path/'out/module-page.md').read_text(encoding="utf-8")
    section=page.split('p1-14')[1].split('p1-15')[0]
    assert 'Lt did not mark this correct' in section
    assert 'accepted' not in section


@pytest.mark.parametrize('pin',['source_answers','source_fill_report'])
def test_reconciled_stale_pin_is_fatal(run,args,fixtures,tmp_path,pin):
    doc=json.loads((fixtures/'runs/c1-r108/reconciled.json').read_text(encoding="utf-8"));doc[pin]['sha256']='0'*64
    p=tmp_path/'reconciled.json';p.write_text(json.dumps(doc), encoding="utf-8", newline="\n")
    rc,out,err=run(*args('c1-r108',extra=['--reconciled',p]))
    assert rc==1 and pin in err and 're-run reconcile' in err
