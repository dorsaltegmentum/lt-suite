# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test recursive notes quarantine, exact probes and visible scanner failures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import quarantine


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_recursive_stamps_include_pages_journals_and_plain_markdown(tmp_path):
    paths = [write(tmp_path / directory / "module.md", "lt-module:: demo-module\n")
             for directory in ("pages", "journals/nested", "notes")]
    write(tmp_path / "course.md", "Ordinary lecture notes.")
    write(tmp_path / "ignored.txt", "lt-module:: demo-module\n")
    findings = quarantine.scan(tmp_path)
    assert {f["path"] for f in findings} == {str(p) for p in paths}
    assert all(f["reason"] == "stamp" and f["module_id"] == "demo-module" for f in findings)


def test_custom_property_is_escaped_and_exact(tmp_path):
    write(tmp_path / "custom.md", "suite.output:: demo-module\n")
    write(tmp_path / "ordinary.md", "suiteXoutput:: demo-module\n")
    write(tmp_path / "default.md", "lt-module:: demo-module\n")
    findings = quarantine.scan(tmp_path, stamp_property="suite.output")
    assert len(findings) == 1 and findings[0]["evidence"] == "suite.output:: demo-module"


@pytest.mark.parametrize("stamp", ["lt-module::\nordinary text", "prefix lt-module:: demo", "lt-module:: demo extra"])
def test_non_properties_do_not_match(tmp_path, stamp):
    write(tmp_path / "ordinary.md", stamp)
    assert quarantine.scan(tmp_path) == []


def test_stamp_quarantines_output_from_any_module(tmp_path):
    write(tmp_path / "other.md", "lt-module:: previous-module\n")
    assert quarantine.scan(tmp_path)[0]["module_id"] == "previous-module"


def test_page_title_and_logseq_filename_decoding(tmp_path):
    one = write(tmp_path / "Lt___Demo%20one.md", "lt-module:: demo\n")
    two = write(tmp_path / "two.md", "title:: Explicit title\nlt-module:: demo\n")
    by_path = {f["path"]: f["page"] for f in quarantine.scan(tmp_path)}
    assert by_path == {str(one): "Lt/Demo one", str(two): "Explicit title"}


def test_trigger_match_normalizes_whitespace_and_compatibility_characters(tmp_path):
    content = "Synthetic case content discusses the fictional example with numbered observations. " * 4
    module = {"pages": [{"page_index": 1, "content": [{"panel_key": "demo", "text": content}]}]}
    probes = quarantine.module_triggers(module)
    write(tmp_path / "old.md", content.replace("fictional", "ﬁctional").replace(" ", "\n"))
    found = quarantine.scan(tmp_path, probes)
    assert len(found) == 1 and found[0]["reason"] == "trigger-text"
    assert found[0]["module_id"] is None
    assert found[0]["evidence"] == "verbatim content match on page 1 panel demo"


def test_short_content_report_page_and_fuzzy_text_do_not_match(tmp_path):
    long_text = "A synthetic example contains several repeated observations for the scanner. " * 5
    module = {"pages": [
        {"page_index": 1, "content": [{"text": "short text"}]},
        {"page_index": 2, "is_report_page": True, "content": [{"text": long_text}]},
    ]}
    assert quarantine.module_triggers(module) == []
    probes = [{"probe": long_text[:120], "page_index": 1, "panel_key": "demo"}]
    write(tmp_path / "paraphrase.md", long_text.replace("synthetic", "invented"))
    assert quarantine.scan(tmp_path, probes) == []


def test_stamp_takes_precedence_over_trigger_and_yields_one_finding(tmp_path):
    write(tmp_path / "demo.md", "lt-module:: demo\nlong synthetic probe\n")
    probes = [{"probe": "long synthetic probe", "page_index": 1, "panel_key": "demo"}]
    assert [f["reason"] for f in quarantine.scan(tmp_path, probes)] == ["stamp"]


def test_symlinks_do_not_escape_graph_or_loop(tmp_path):
    graph = tmp_path / "graph"
    graph.mkdir()
    outside = write(tmp_path / "outside.md", "lt-module:: demo\n")
    (graph / "external.md").symlink_to(outside)
    (graph / "loop").symlink_to(graph, target_is_directory=True)
    assert quarantine.scan(graph) == []


def test_missing_directory_and_bad_encoding_are_errors(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        quarantine.scan(tmp_path / "missing")
    (tmp_path / "broken.md").write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeError):
        quarantine.scan(tmp_path)


def test_read_error_is_not_silently_skipped(tmp_path, monkeypatch):
    path = write(tmp_path / "denied.md", "lt-module:: demo\n")
    original = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            raise PermissionError("synthetic read error")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(PermissionError):
        quarantine.scan(tmp_path)


def config_file(tmp_path):
    graph = tmp_path / "graph"
    graph.mkdir()
    cfg = write(tmp_path / "config.toml", '[notes]\ngraph_path = "graph"\nstamp_property = "module-output"\n')
    return graph, cfg


def test_cli_config_recursion_and_check_exit_codes(tmp_path, run_py):
    graph, cfg = config_file(tmp_path)
    excluded = write(graph / "nested" / "excluded.md", "module-output:: demo\n")
    ordinary = write(graph / "lecture.md", "Course notes.")
    args = ("-m", "ltcore.quarantine", "--config", str(cfg), "--json")
    result = run_py(*args)
    assert result.returncode == 0
    doc = json.loads(result.stdout)
    assert doc["pages_scanned"] == 2 and len(doc["findings"]) == 1
    assert doc["graph_from"].startswith("config ")
    result = run_py(*args, "--check", str(excluded))
    assert result.returncode == 1 and json.loads(result.stdout)["quarantined"] is True
    result = run_py(*args, "--check", str(ordinary))
    assert result.returncode == 0 and json.loads(result.stdout)["quarantined"] is False
    readable = run_py("-m", "ltcore.quarantine", "--config", str(cfg), "--check", str(excluded))
    assert readable.returncode == 1 and "by stamp: module-output:: demo" in readable.stdout


def test_cli_graph_override_and_content_probe(tmp_path, run_py):
    graph, cfg = config_file(tmp_path)
    content = "Synthetic long stimulus text for testing a captured module without course material. " * 4
    write(graph / "older.md", content)
    module = write(tmp_path / "module.json", json.dumps({"pages": [{"page_index": 1, "content": [
        {"panel_key": "demo", "text": content}]}]}))
    result = run_py("-m", "ltcore.quarantine", "--config", str(cfg), "--graph", str(graph),
                    "--module", str(module), "--json")
    assert result.returncode == 0
    doc = json.loads(result.stdout)
    assert doc["graph_from"] == "flag --graph" and doc["triggers_probed"] == 1
    assert doc["findings"][0]["reason"] == "trigger-text"


def test_cli_disabled_graph_and_outside_check(tmp_path, run_py):
    cfg = write(tmp_path / "config.toml", "")
    args = ("-m", "ltcore.quarantine", "--config", str(cfg))
    result = run_py(*args, "--json")
    assert result.returncode == 0 and json.loads(result.stdout)["disabled"] is True
    assert run_py(*args, "--check", "missing.md").returncode == 3
    assert run_py(*args, "--min-len", "0").returncode == 3
    graph = tmp_path / "graph"
    graph.mkdir()
    outside = write(tmp_path / "outside.md", "lt-module:: demo\n")
    assert run_py(*args, "--graph", str(graph), "--check", str(outside)).returncode == 3


@pytest.mark.parametrize("option", ["--unknown", "--min-len", "--probe-len"])
def test_cli_usage_errors_are_three(tmp_path, run_py, option):
    graph, cfg = config_file(tmp_path)
    args = ["-m", "ltcore.quarantine", "--config", str(cfg), option]
    if option != "--unknown":
        args.append("0")
    assert run_py(*args).returncode == 3


def test_cli_invalid_module_and_missing_graph_are_three(tmp_path, run_py):
    graph, cfg = config_file(tmp_path)
    bad_module = write(tmp_path / "module.json", "[]")
    result = run_py("-m", "ltcore.quarantine", "--config", str(cfg), "--module", str(bad_module))
    assert result.returncode == 3 and "Traceback" not in result.stderr
    assert run_py("-m", "ltcore.quarantine", "--config", str(cfg), "--graph", str(graph / "missing")).returncode == 3


# These eight review probes used to turn exported output into eligible sources.
@pytest.mark.parametrize("stamp", [
    "LT-MODULE:: demo\n",
    chr(0xFEFF) + "lt-module:: demo\n",
    "---\nlt-module: demo\n---\n",
    '---\n"lt-module": "demo"\n---\n',
    "<!-- lt-module: demo -->\n",
    "lt-module :: demo\n",
    "  lt-module:: demo\n",
    "lt_module:: demo\n",
], ids=["case", "bom", "yaml", "quoted-yaml", "comment", "separator-space", "indent", "underscore"])
def test_review_stamp_probes_quarantine_cli(tmp_path, run_py, stamp):
    graph = tmp_path / "graph"
    page = graph / "output.md"
    page.parent.mkdir()
    page.write_bytes(stamp.encode("utf-8"))
    cfg = write(tmp_path / "config.toml", "")
    result = run_py("-m", "ltcore.quarantine", "--config", str(cfg),
                    "--graph", str(graph), "--check", str(page), "--json")
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["quarantined"] is True
    assert report["module_id"] == "demo"


@pytest.mark.parametrize("stamp", [
    "lt-module:: demo\r\n",
    '<p>lt-module:: demo</p>\n',
    '<div><b>LT_MODULE</b> :: <span>demo</span></div>\r\n',
    chr(0xFEFF) + '---\r\n  "LT_MODULE" : "demo"\r\n---\r\n',
])
def test_markup_and_combined_stamp_normalization(tmp_path, stamp):
    write(tmp_path / "output.md", stamp)
    assert quarantine.scan(tmp_path)[0]["module_id"] == "demo"


# Each invalid length must fail independently, even when there are no panels.
@pytest.mark.parametrize("min_len,probe_len", [(0, 120), (200, 0), (-1, 120), (200, -1), (0, 0)])
def test_trigger_lengths_must_each_be_positive(min_len, probe_len):
    with pytest.raises(ValueError, match="must be positive"):
        quarantine.module_triggers({"pages": []}, min_len=min_len, probe_len=probe_len)
