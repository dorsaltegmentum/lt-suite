# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the privacy gate tests.

- ``gate``: tools/privacy_gate.py imported as a module.
- ``run_gate``: run the gate as a subprocess. LT_PRIVACY_DIR points at an empty temp
  folder and PRIVACY_PATTERNS_B64 is removed, so no test reads the real private layer
  unless it passes its own. CI is removed too, because the gate refuses --excerpt when
  CI is set and GitHub Actions sets it; a test that checks that refusal passes its own.
- Builders for planted binaries: ``make_pdf``, ``make_png``, ``make_font``, ``make_woff``.
"""
from __future__ import annotations

import importlib.util
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "tools" / "privacy_gate.py"


@pytest.fixture(scope="session")
def gate():
    spec = importlib.util.spec_from_file_location("privacy_gate_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def empty_private(tmp_path) -> Path:
    d = tmp_path / "no-private"
    d.mkdir()
    return d


@pytest.fixture
def run_gate(empty_private):
    def _run(*args: str, env: dict | None = None, cwd: Path | None = None,
             timeout: int = 300) -> subprocess.CompletedProcess:
        e = {k: v for k, v in os.environ.items() if k not in ("LT_PRIVACY_DIR", "PRIVACY_PATTERNS_B64", "CI")}
        e["LT_PRIVACY_DIR"] = str(empty_private)
        e.update(env or {})
        return subprocess.run([sys.executable, str(GATE), *args], capture_output=True, text=True,
                              cwd=cwd or REPO, env=e, timeout=timeout, encoding="utf-8")
    return _run


def ids_of(stdout: str) -> list[str]:
    """Pattern ids in gate output: the last field of public lines, the first of private and shingle lines."""
    out = []
    for line in stdout.splitlines():
        first, _, rest = line.partition(" ")
        out.append(first if first.startswith(("private-", "shingle-")) else rest.split()[0] if rest else "")
    return out


@pytest.fixture
def ids():
    return ids_of


def _pdf(objects: list[bytes], info_obj: int | None) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    trailer = b"<< /Size %d /Root 1 0 R" % (len(objects) + 1)
    if info_obj:
        trailer += b" /Info %d 0 R" % info_obj
    out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


@pytest.fixture
def make_pdf():
    def _make(page_text: str = "plain page", info: bytes = b"(plain title)", extra_stream: bytes = b"") -> bytes:
        content = b"BT /F1 12 Tf 20 100 Td (" + page_text.encode("latin-1") + b") Tj ET"
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 144] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Title " + info + b" >>",
        ]
        if extra_stream:
            packed = zlib.compress(extra_stream)
            objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(packed) + packed + b"\nendstream")
        return _pdf(objs, 6)
    return _make


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


@pytest.fixture
def make_png():
    def _make(chunks: list[tuple[bytes, bytes]] = ()) -> bytes:
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
        out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
        for ctype, body in chunks:
            out += _chunk(ctype, body)
        return out + _chunk(b"IDAT", zlib.compress(b"\x00\x00")) + _chunk(b"IEND", b"")
    return _make


def _name_table(names: list[str]) -> bytes:
    strings = [s.encode("utf-16-be") for s in names]
    header = struct.pack(">HHH", 0, len(names), 6 + 12 * len(names))
    recs, off = b"", 0
    for nid, s in enumerate(strings):
        recs += struct.pack(">6H", 3, 1, 0x409, nid, len(s), off)
        off += len(s)
    return header + recs + b"".join(strings)


@pytest.fixture
def make_font():
    def _make(names: list[str]) -> bytes:
        table = _name_table(names)
        head = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
        rec = struct.pack(">4sIII", b"name", 0, 12 + 16, len(table))
        return head + rec + table
    return _make


@pytest.fixture
def make_woff():
    def _make(names: list[str], metadata: str = "") -> bytes:
        table = _name_table(names)
        comp = zlib.compress(table)
        meta = zlib.compress(metadata.encode()) if metadata else b""
        table_off = 44 + 20
        meta_off = table_off + len(comp) if meta else 0
        total = table_off + len(comp) + len(meta)
        header = struct.pack(">4sIIHHIHHIIIII", b"wOFF", 0x00010000, total, 1, 0, 12 + 16 + len(table),
                             1, 0, meta_off, len(meta), len(metadata.encode()), 0, 0)
        entry = struct.pack(">4sIIII", b"name", table_off, len(comp), len(table), 0)
        return header + entry + comp + meta
    return _make


@pytest.fixture
def make_pdf_stream():
    """A valid PDF with an unreferenced filtered metadata stream."""
    def make(payload: bytes, filters: bytes):
        return _pdf([
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>',
            b'<< /Type /Metadata /Subtype /XML /Length %d /Filter ' % len(payload)
            + filters + b' >>\nstream\n' + payload + b'\nendstream',
        ], None)
    return make
