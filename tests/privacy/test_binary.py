# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Planted metadata and archive members must reach the same privacy layers as text."""
import io
import struct
import zipfile
import zlib

import pytest

PLANTED = "someone@mail.test"


@pytest.mark.parametrize("part", ["info", "xmp", "page"])
def test_pdf_layers(run_gate, make_pdf, tmp_path, part):
    args = {"info": {"info": f"({PLANTED})".encode()},
            "xmp": {"extra_stream": f"<x:xmpmeta>{PLANTED}</x:xmpmeta>".encode()},
            "page": {"page_text": PLANTED}}[part]
    p = tmp_path / "sample.pdf"
    p.write_bytes(make_pdf(**args))
    r = run_gate("--paths", str(p))
    assert r.returncode == 1 and " email" in r.stdout, r.stderr
    assert PLANTED not in r.stdout


@pytest.mark.parametrize("kind", ["text", "compressed", "international"])
def test_png_text_chunks(run_gate, make_png, tmp_path, kind):
    chunk = {"text": (b"tEXt", b"Author\0" + PLANTED.encode()),
             "compressed": (b"zTXt", b"Author\0\0" + zlib.compress(PLANTED.encode())),
             "international": (b"iTXt", b"Author\0\1\0en\0Author\0" + zlib.compress(PLANTED.encode()))}[kind]
    p = tmp_path / "sample.png"
    p.write_bytes(make_png([chunk]))
    r = run_gate("--paths", str(p))
    assert r.returncode == 1 and " email" in r.stdout, r.stderr


@pytest.mark.parametrize("kind", ["comment", "exif", "xmp"])
def test_jpeg_metadata(run_gate, tmp_path, kind):
    marker, body = {"comment": (b"\xff\xfe", PLANTED.encode()),
                    "exif": (b"\xff\xe1", b"Exif\0\0" + PLANTED.encode("utf-16-le")),
                    "xmp": (b"\xff\xe1", b"http://ns.adobe.com/xap/1.0/\0" + PLANTED.encode())}[kind]
    p = tmp_path / "sample.jpg"
    p.write_bytes(b"\xff\xd8" + marker + struct.pack(">H", len(body) + 2) + body + b"\xff\xd9")
    r = run_gate("--paths", str(p))
    assert r.returncode == 1 and " email" in r.stdout, r.stderr


@pytest.mark.parametrize("kind", ["ttf", "woff"])
def test_font_names(run_gate, make_font, make_woff, tmp_path, kind):
    p = tmp_path / ("sample." + kind)
    p.write_bytes((make_font if kind == "ttf" else make_woff)(["Neutral font", PLANTED]))
    r = run_gate("--paths", str(p))
    assert r.returncode == 1 and "#name:" in r.stdout and " email" in r.stdout, r.stderr


def test_nested_zip_member_content_and_names(run_gate, tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("plain.txt", PLANTED)
    p = tmp_path / "bundle.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("nested.zip", inner.getvalue())
        z.writestr(PLANTED + "/ok.txt", "clean")
    r = run_gate("--paths", str(p))
    assert r.returncode == 1 and "nested.zip!plain.txt:" in r.stdout
    assert ":0:" in r.stdout and " email" in r.stdout


def test_svg_and_unknown_binary(run_gate, tmp_path):
    for name, data in [("a.svg", f"<svg><text>{PLANTED}</text></svg>".encode()),
                       ("a.bin", b"\0\1\2" + PLANTED.encode() + b"\0")]:
        p = tmp_path / name
        p.write_bytes(data)
        r = run_gate("--paths", str(p))
        assert r.returncode == 1 and " email" in r.stdout


@pytest.mark.parametrize("kind", ["png", "jpeg"])
def test_truncated_metadata_is_gate_error(run_gate, tmp_path, kind):
    """An incomplete metadata segment must never be reported clean."""
    data = {"png": b"\x89PNG\r\n\x1a\n" + struct.pack(">I4s", 40, b"tEXt") + b"Author\0",
            "jpeg": b"\xff\xd8\xff\xe1" + struct.pack(">H", 40) + b"Exif\0\0"}[kind]
    p = tmp_path / ("sample." + kind)
    p.write_bytes(data)
    assert run_gate("--paths", str(p)).returncode == 2
