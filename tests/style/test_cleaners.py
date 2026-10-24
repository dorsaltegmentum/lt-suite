# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.cleaners: argv placeholders, stage filtering, timeouts, atomic replacement
and the built-in PDF metadata strip. No test needs a real cleaner program or the
network; PDF tests skip when neither pikepdf nor qpdf is available.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from ltcore import cleaners


def _entry(**changes):
    entry = {
        "name": "test-cleaner",
        "stage": "fill-text",
        "argv": ["test-cleaner", "{in}", "{out}"],
    }
    entry.update(changes)
    return entry


def _completed(argv, returncode=0, stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)


def _minimal_pdf() -> bytes:
    """Build one blank page, document information and an exact xref table."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << >> /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\nendstream",
        b"<< /Author (Somebody) /Producer (X) >>",
    ]
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(data)


def test_build_argv_replaces_only_supported_tokens():
    entry = _entry(argv=[
        "cleaner", "{in}", "{out}", "{pdf}", "--x={in}",
        "{other}", "a{b}c", "{in}:{out}:{pdf}",
    ])
    assert cleaners.build_argv(entry, src="source", dst="target", pdf="document") == [
        "cleaner", "source", "target", "document", "--x=source",
        "{other}", "a{b}c", "source:target:document",
    ]


def test_build_argv_preserves_tokens_inside_replacement_values():
    assert cleaners.build_argv(
        _entry(), src="name{out}.txt", dst="target.txt"
    ) == ["test-cleaner", "name{out}.txt", "target.txt"]


def test_build_argv_requires_output_value():
    with pytest.raises(cleaners.CleanerError, match=r"\{out\}"):
        cleaners.build_argv(_entry(), src="source")


def test_cleaners_for_filters_orders_and_defaults():
    entries = [
        _entry(name="first"),
        _entry(name="pdf", stage="pdf", argv=["pdf-tool", "{pdf}"]),
        _entry(name="second", timeout_s=17),
    ]
    selected = cleaners.cleaners_for("fill-text", entries=entries)
    assert [entry["name"] for entry in selected] == ["first", "second"]
    assert [entry["timeout_s"] for entry in selected] == [60, 17]
    assert "timeout_s" not in entries[0]
    selected[0]["argv"].append("extra")
    assert entries[0]["argv"] == ["test-cleaner", "{in}", "{out}"]


def test_cleaners_for_entries_override_config():
    class UnusedConfig:
        def get(self, *args):
            raise AssertionError("entries must override config")

    assert cleaners.cleaners_for("pdf", cfg=UnusedConfig(), entries=[]) == []


def test_cleaners_for_rejects_unknown_stage():
    with pytest.raises(cleaners.CleanerError, match="unknown cleaner stage"):
        cleaners.cleaners_for("unknown", entries=[])


@pytest.mark.parametrize("stage", ["fill-text", "diagram"])
@pytest.mark.parametrize(
    ("output", "status"),
    [(b"cleaned text", "cleaned"), (b"original text", "unchanged")],
)
def test_run_cleaner_success_and_unchanged(tmp_path, stage, output, status):
    path = tmp_path / "document.txt"
    path.write_bytes(b"original text")

    def runner(argv, *, capture_output, text, timeout):
        assert capture_output is True
        assert text is True
        assert timeout == 19
        assert Path(argv[1]).name == "in.txt"
        assert Path(argv[2]).name == "out.txt"
        assert Path(argv[1]).read_bytes() == b"original text"
        assert path.read_bytes() == b"original text"
        Path(argv[2]).write_bytes(output)
        return _completed(argv)

    result = cleaners.run_cleaner(_entry(stage=stage, timeout_s=19), path, runner=runner)
    assert result.status == status
    assert result.returncode == 0
    assert result.path == str(path)
    assert path.read_bytes() == output


def test_nonzero_exit_preserves_original(tmp_path):
    path = tmp_path / "document.txt"
    path.write_bytes(b"original")

    def runner(argv, **kwargs):
        Path(argv[1]).write_bytes(b"modified input")
        Path(argv[2]).write_bytes(b"untrusted output")
        return _completed(argv, returncode=4, stderr="cleaner failed")

    result = cleaners.run_cleaner(_entry(), path, runner=runner)
    assert result.status == "failed"
    assert result.returncode == 4
    assert "cleaner failed" in result.detail
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("output", [None, b""])
def test_missing_or_empty_output_preserves_original(tmp_path, output):
    path = tmp_path / "document.txt"
    path.write_bytes(b"original")

    def runner(argv, **kwargs):
        if output is not None:
            Path(argv[2]).write_bytes(output)
        return _completed(argv)

    result = cleaners.run_cleaner(_entry(), path, runner=runner)
    assert result.status == "failed"
    assert path.read_bytes() == b"original"


def test_fake_timeout_preserves_original(tmp_path):
    path = tmp_path / "document.txt"
    path.write_bytes(b"original")

    def runner(argv, *, capture_output, text, timeout):
        assert timeout == 7
        Path(argv[1]).write_bytes(b"modified input")
        Path(argv[2]).write_bytes(b"partial output")
        raise subprocess.TimeoutExpired(argv, timeout)

    result = cleaners.run_cleaner(_entry(timeout_s=7), path, runner=runner)
    assert result.status == "timeout"
    assert path.read_bytes() == b"original"


def test_missing_executable_returns_failed(tmp_path):
    path = tmp_path / "document.txt"
    path.write_bytes(b"original")

    def runner(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    result = cleaners.run_cleaner(_entry(), path, runner=runner)
    assert result.status == "failed"
    assert "test-cleaner" in result.detail
    assert path.read_bytes() == b"original"


def test_real_python_cleaner_removes_zero_width_space(tmp_path):
    script = tmp_path / "clean.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "text = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "Path(sys.argv[2]).write_text(text.replace('\\u200b', ''), encoding='utf-8')\n",
        encoding="utf-8",
    )
    path = tmp_path / "document.txt"
    path.write_text("one\u200btwo", encoding="utf-8")
    entry = _entry(argv=[sys.executable, str(script), "{in}", "{out}"])
    result = cleaners.run_cleaner(entry, path)
    assert result.status == "cleaned"
    assert path.read_bytes() == b"onetwo"


def test_real_timeout_finishes_within_three_seconds(tmp_path):
    path = tmp_path / "document.txt"
    path.write_bytes(b"original")
    entry = _entry(
        argv=[sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_s=1,
    )
    start = time.monotonic()
    result = cleaners.run_cleaner(entry, path)
    elapsed = time.monotonic() - start
    assert result.status == "timeout"
    assert elapsed < 3
    assert path.read_bytes() == b"original"


def test_pdf_cleaner_rejects_non_pdf_output(tmp_path):
    path = tmp_path / "document.pdf"
    original = _minimal_pdf()
    path.write_bytes(original)

    def runner(argv, **kwargs):
        Path(argv[1]).write_bytes(b"not a PDF")
        return _completed(argv)

    entry = _entry(stage="pdf", argv=["pdf-cleaner", "{pdf}"])
    result = cleaners.run_cleaner(entry, path, runner=runner)
    assert result.status == "failed"
    assert "%PDF-" in result.detail
    assert path.read_bytes() == original


def test_run_stage_preserves_cleaner_and_file_order(tmp_path):
    paths = [tmp_path / "first.txt", tmp_path / "second.txt"]
    for path in paths:
        path.write_bytes(b"text")

    def runner(argv, **kwargs):
        Path(argv[2]).write_bytes(Path(argv[1]).read_bytes())
        return _completed(argv)

    entries = [_entry(name="one"), _entry(name="two")]
    results = cleaners.run_stage("fill-text", paths, entries=entries, runner=runner)
    assert [(result.name, result.path) for result in results] == [
        (name, str(path)) for name in ("one", "two") for path in paths
    ]
    assert all(result.status == "unchanged" for result in results)


def test_strip_pdf_metadata_removes_info_and_is_deterministic(tmp_path):
    if shutil.which("qpdf") is None:
        pytest.importorskip("pikepdf", reason="PDF metadata stripping needs qpdf or pikepdf")
    path = tmp_path / "document.pdf"
    path.write_bytes(_minimal_pdf())

    first = cleaners.strip_pdf_metadata(path)
    assert first.status == "cleaned", first.detail
    cleaned = path.read_bytes()
    assert cleaned.startswith(b"%PDF-")
    assert b"/Author" not in cleaned
    assert b"Somebody" not in cleaned

    second = cleaners.strip_pdf_metadata(path)
    assert second.status == "unchanged", second.detail
    assert path.read_bytes() == cleaned


def test_residual_metadata_without_exiftool(tmp_path, monkeypatch):
    monkeypatch.setattr(cleaners.shutil, "which", lambda name: None)
    assert cleaners.residual_metadata(tmp_path / "document.pdf") is None


def test_finalize_without_cleaners_or_builtin_strip(tmp_path):
    path = tmp_path / "document.pdf"
    original = _minimal_pdf()
    path.write_bytes(original)
    assert cleaners.finalize(
        [path], stage="pdf", entries=[], strip_metadata=False
    ) == []
    assert path.read_bytes() == original


def test_import_without_pikepdf(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "pikepdf", None)
        module = importlib.reload(cleaners)
        assert module.STAGES == ("fill-text", "pdf", "diagram")
        assert module.NEEDS == []


def test_run_one_replaces_placeholders_and_runs_without_shell(tmp_path):
    src, dst = tmp_path / "a b.json", tmp_path / "out.json"
    src.write_text("{}", encoding="utf-8")
    seen = {}

    def runner(argv, *, capture_output, text, timeout):
        seen["argv"], seen["timeout"] = argv, timeout
        Path(argv[2]).write_text("{}", encoding="utf-8")
        return _completed(argv)

    r = cleaners.run_one(_entry(argv=["tool", "{in}", "{out}", "--x={in}"]), {"in": src, "out": dst},
                         timeout_s=9, runner=runner)
    assert seen["argv"] == ["tool", str(src), str(dst), f"--x={src}"]
    assert seen["timeout"] == 9
    assert r.status == "ran" and r["status"] == "ran" and r.ok and r.returncode == 0


@pytest.mark.parametrize("case", ["exit", "timeout", "missing-program", "no-out", "empty-out"])
def test_run_one_raises_on_every_failure(tmp_path, case):
    src, dst = tmp_path / "in.txt", tmp_path / "out.txt"
    src.write_text("x", encoding="utf-8")

    def runner(argv, **kw):
        if case == "exit":
            return _completed(argv, returncode=2, stderr="broken")
        if case == "timeout":
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        if case == "missing-program":
            raise FileNotFoundError(argv[0])
        if case == "empty-out":
            Path(argv[2]).write_bytes(b"")
        return _completed(argv)

    with pytest.raises(cleaners.CleanerError):
        cleaners.run_one(_entry(), {"in": src, "out": dst}, timeout_s=3, runner=runner)


def test_run_one_checks_a_pdf_placeholder(tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(_minimal_pdf())
    entry = _entry(stage="pdf", argv=["tool", "{pdf}"])

    def breaks(argv, **kw):
        Path(argv[1]).write_bytes(b"garbage")
        return _completed(argv)

    with pytest.raises(cleaners.CleanerError, match="%PDF-"):
        cleaners.run_one(entry, {"pdf": pdf}, runner=breaks)
    pdf.write_bytes(_minimal_pdf())
    assert cleaners.run_one(entry, {"pdf": str(pdf)}, runner=lambda a, **k: _completed(a)).ok
    with pytest.raises(cleaners.CleanerError, match="timeout_s"):
        cleaners.run_one(entry, {"pdf": pdf}, timeout_s=0, runner=breaks)


def test_run_one_real_program(tmp_path):
    src, dst = tmp_path / "in.txt", tmp_path / "out.txt"
    src.write_text("a\u200bb", encoding="utf-8")
    code = ("import sys,pathlib;t=pathlib.Path(sys.argv[1]).read_text('utf-8');"
            "pathlib.Path(sys.argv[2]).write_text(t.replace('\\u200b',''),'utf-8')")
    cleaners.run_one(_entry(argv=[sys.executable, "-c", code, "{in}", "{out}"]), {"in": src, "out": dst})
    assert dst.read_text(encoding="utf-8") == "ab"


def test_strip_pdf_returns_a_dict_and_leaves_no_metadata(tmp_path):
    pikepdf = pytest.importorskip("pikepdf")
    path = tmp_path / "document.pdf"
    path.write_bytes(_minimal_pdf())
    first = cleaners.strip_pdf(path)
    assert first["status"] == "cleaned" and first["file"] == str(path), first["detail"]
    assert cleaners.metadata_left(path.read_bytes()) == []
    with pikepdf.open(path) as pdf:
        assert "/Info" not in pdf.trailer and "/Metadata" not in pdf.Root
    assert cleaners.strip_pdf(path)["status"] == "already-clean"
    path.write_bytes(b"not a pdf")
    assert cleaners.strip_pdf(path)["status"] == "error"


def test_result_reads_as_attributes_and_keys():
    r = cleaners.Result("n", "pdf", "/x.pdf", "unchanged", "d")
    assert r["status"] == r.status == "unchanged" and r.get("file") == "/x.pdf" and r.ok
    assert set(r.keys()) >= {"name", "stage", "path", "status", "detail"}
    assert not cleaners.Result("n", "pdf", "/x.pdf", "failed").ok
