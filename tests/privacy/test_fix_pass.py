# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic leaks exercise release review regressions through the gate CLI."""
import base64
import gzip
import io
import json
import struct
import subprocess
import zlib

import pytest

LEAK = '/home/someone/private-notes'
PHRASE = 'violet lanterns illuminate seven copper bridges beside amber rivers'


def git(repo, *args, data=None):
    return subprocess.run(['git', '-C', str(repo), *args], input=data,
                          capture_output=True, check=True).stdout.strip().decode()


@pytest.mark.parametrize('mode', [[], ['--all']])
def test_staged_leak_survives_working_tree_cleanup(run_gate, tmp_path, mode):
    """Scanning only the working file loses the actual staged leak."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    oid = git(repo, 'hash-object', '-w', '--stdin', data=LEAK.encode())
    git(repo, 'update-index', '--add', '--cacheinfo', '100644', oid, 'a.txt')
    (repo / 'a.txt').write_text('clean')
    r = run_gate('--repo', str(repo), *mode)
    assert r.returncode == 1 and 'home-path' in r.stdout, r.stdout + r.stderr
    assert '@index' in r.stdout


def encode(kind, text):
    if kind == 'json':
        return json.dumps(text)[1:-1]
    if kind == 'unicode':
        return ''.join('\\u%04x' % ord(c) for c in text)
    if kind == 'base64':
        return base64.b64encode(text.encode()).decode()
    if kind == 'base64url':
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')
    if kind == 'percent':
        return ''.join('%%%02x' % b for b in text.encode())
    if kind == 'html':
        return ''.join('&#%d;' % ord(c) for c in text)
    if kind == 'format':
        return chr(0x200B).join(text)
    raise AssertionError(kind)


@pytest.mark.parametrize('kinds', [
    ['base64'], ['base64url'], ['format'], ['unicode', 'json'],
    ['unicode', 'unicode'], ['base64', 'percent', 'html'],
    ['html', 'base64', 'json'], ['html'],
])
@pytest.mark.parametrize('layer', ['public', 'private', 'shingle'])
def test_recursive_decoding_reaches_every_layer(run_gate, gate, tmp_path, kinds, layer):
    """One decoding pass, missing HTML unescaping, or missing Cf removal hides leaks."""
    plain = {'public': LEAK, 'private': 'synthetic-owner-91', 'shingle': PHRASE}[layer]
    text = plain
    for kind in kinds:
        text = encode(kind, text)
    target = tmp_path / 'target.json'
    target.write_text(json.dumps({'payload': text}))
    args = []
    private = tmp_path / 'private'
    private.mkdir()
    if layer == 'private':
        (private / 'private-patterns.txt').write_text('synthetic-owner-91\n')
        args += ['--private-dir', str(private), '--require-private']
    elif layer == 'shingle':
        salt = b's' * 32
        (private / 'shingle-salt').write_text(salt.hex())
        gate.write_index(private / 'shingles.bin', {h for _, h in gate.text_windows(PHRASE, salt)})
        args += ['--shingles', str(private / 'shingles.bin')]
    r = run_gate('--paths', str(target), *args)
    marker = {'public': 'home-path', 'private': 'private-', 'shingle': 'shingle-'}[layer]
    assert r.returncode == 1 and marker in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize('tag', ['example-institution', 'example-format'])
@pytest.mark.parametrize('path', ['docs/**', 'examples/**', 'designated-example-traversal'])
def test_private_tag_paths_cannot_widen(run_gate, gate, tmp_path, tag, path):
    """A named tag cannot turn arbitrary documentation into an example exception."""
    if path == "designated-example-traversal":
        path = gate.PRIVATE_TAG_PATHS["example-institution"][0].removesuffix("**") + "../../docs/**"
    target = tmp_path / 'a.txt'
    target.write_text('Synthetic Academy')
    al = tmp_path / 'allow.toml'
    al.write_text(f'[[allow]]\npath = "{path}"\npattern = "private:{tag}"\nreason = "Synthetic test"\n')
    r = run_gate('--allowlist', str(al), '--paths', str(target))
    assert r.returncode == 2 and 'fixed' in r.stderr, r.stdout + r.stderr


def test_designated_course_ranges_keep_benign_vocabulary(run_gate, tmp_path):
    """Individual benign words must never erase a designated course sentence."""
    src, private = tmp_path / 'source', tmp_path / 'private'
    src.mkdir()
    private.mkdir()
    (src / 'course.txt').write_text(PHRASE + '\n')
    (src / 'benign.txt').write_text('\n'.join(reversed(PHRASE.split())))
    (private / 'shingle-sources.txt').write_text('[course]\ncourse.txt:1\n[benign]\nbenign.txt\n')
    r = run_gate('build-shingles', '--from', str(src), '--out', str(private))
    assert r.returncode == 0, r.stderr
    target = tmp_path / 'a.txt'
    target.write_text(PHRASE)
    r = run_gate('--paths', str(target), '--shingles', str(private / 'shingles.bin'))
    assert r.returncode == 1 and 'course' in r.stdout, r.stdout + r.stderr


def test_every_gzip_member_header_is_scanned(run_gate, tmp_path):
    """Decompressing all members while scanning one header misses later filenames."""
    def member(name):
        buf = io.BytesIO()
        with gzip.GzipFile(filename=name, fileobj=buf, mode='wb', mtime=0) as f:
            f.write(b'clean\n')
        return buf.getvalue()
    # GzipFile strips directory components, so plant a synthetic email as filename.
    target = tmp_path / 'a.gz'
    target.write_bytes(member('clean') + member('someone@mail.test'))
    r = run_gate('--paths', str(target))
    assert r.returncode == 1 and 'email' in r.stdout and 'gzip-header' in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize('ci', ['true', '1', 'false', ''])
def test_ci_refuses_excerpts_before_any_output(run_gate, tmp_path, ci):
    """A set CI variable must prohibit excerpts even when empty or false-like."""
    target = tmp_path / 'a.txt'
    target.write_text(LEAK)
    r = run_gate('--paths', str(target), '--excerpt', env={'CI': ci})
    assert r.returncode == 2 and r.stdout == '', r.stdout + r.stderr
    assert LEAK not in r.stderr


def test_run_gate_drops_inherited_ci(run_gate, tmp_path, monkeypatch):
    """GitHub Actions sets CI, so every --excerpt test exited 2 there until run_gate dropped it."""
    monkeypatch.setenv('CI', 'true')
    target = tmp_path / 'a.txt'
    target.write_text(LEAK)
    r = run_gate('--paths', str(target), '--excerpt')
    assert r.returncode == 1 and LEAK in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize('tag', [b'meta', b'ZZZZ'])
@pytest.mark.parametrize('kind', ['ttf', 'woff'])
def test_all_font_tables_are_scanned(run_gate, tmp_path, tag, kind):
    """Scanning only name records misses OpenType meta and vendor text tables."""
    raw = LEAK.encode()
    table = struct.pack('>IIII4sII', 1, 0, 0, 1, b'dl01', 28, len(raw)) + raw if tag == b'meta' else raw
    if kind == 'ttf':
        data = struct.pack('>IHHHH', 0x10000, 1, 16, 0, 0)
        data += struct.pack('>4sIII', tag, 0, 28, len(table)) + table
    else:
        packed = zlib.compress(table)
        if len(packed) >= len(table):
            packed = table
        data = struct.pack('>4sIIHHIHHIIIII', b'wOFF', 0x10000, 64 + len(packed), 1, 0,
                           28 + len(table), 1, 0, 0, 0, 0, 0, 0)
        data += struct.pack('>4sIIII', tag, 64, len(packed), len(table), 0) + packed
    target = tmp_path / ('a.' + kind)
    target.write_bytes(data)
    r = run_gate('--paths', str(target))
    assert r.returncode == 1 and 'home-path' in r.stdout, r.stdout + r.stderr


def lzw_literals(data):
    """Short independent LZW fixture: clear, nine-bit literals, end-of-data."""
    bits = ''.join(format(c, '09b') for c in [256, *data, 257])
    bits += '0' * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, 'big')


@pytest.mark.parametrize('filters', [
    ['ASCII85Decode'], ['ASCIIHexDecode'], ['LZWDecode'], ['RunLengthDecode'],
    ['ASCII85Decode', 'FlateDecode'], ['ASCIIHexDecode', 'RunLengthDecode'],
])
def test_pdf_filter_chains_expose_xmp(run_gate, make_pdf_stream, tmp_path, filters):
    """Trying only Flate silently discards supported filtered XMP streams."""
    payload = ('<x:xmpmeta>' + LEAK + '</x:xmpmeta>').encode()
    for filt in reversed(filters):
        if filt == 'FlateDecode':
            payload = zlib.compress(payload)
        elif filt == 'ASCII85Decode':
            payload = base64.a85encode(payload) + b'~>'
        elif filt == 'ASCIIHexDecode':
            payload = payload.hex().encode() + b'>'
        elif filt == 'LZWDecode':
            payload = lzw_literals(payload)
        elif filt == 'RunLengthDecode':
            payload = bytes([len(payload) - 1]) + payload + bytes([128])
    target = tmp_path / 'a.pdf'
    target.write_bytes(make_pdf_stream(payload, b'[' + b' '.join(b'/' + f.encode() for f in filters) + b']'))
    r = run_gate('--paths', str(target))
    assert r.returncode == 1 and '#xmp:' in r.stdout and 'home-path' in r.stdout, r.stdout + r.stderr


def test_pdf_unsupported_filter_fails_closed(run_gate, make_pdf_stream, tmp_path):
    target = tmp_path / 'a.pdf'
    target.write_bytes(make_pdf_stream(b'opaque', b'/UnknownDecode'))
    r = run_gate('--paths', str(target))
    assert r.returncode == 2 and 'filter' in r.stderr, r.stdout + r.stderr


def test_explicit_paths_scan_selected_index_blobs(run_gate, tmp_path):
    """Package scans must inspect selected staged bytes alongside working files."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    oid = git(repo, 'hash-object', '-w', '--stdin', data=LEAK.encode())
    git(repo, 'update-index', '--add', '--cacheinfo', '100644', oid, 'a.txt')
    target = repo / 'a.txt'
    target.write_text('clean')
    r = run_gate('--repo', str(repo), '--paths', str(target))
    assert r.returncode == 1 and '@index' in r.stdout and 'home-path' in r.stdout, r.stdout + r.stderr


def test_directory_scan_skips_git_ignored_outputs_but_explicit_file_scans_them(run_gate, tmp_path):
    """Directory package scans use the release file set; explicit files remain inspectable."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / '.gitignore').write_text('output.txt\n')
    target = repo / 'output.txt'
    target.write_text(LEAK)
    r = run_gate('--repo', str(repo), '--paths', str(repo))
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_gate('--repo', str(repo), '--paths', str(target))
    assert r.returncode == 1 and 'home-path' in r.stdout, r.stdout + r.stderr


def test_concatenated_gzip_payloads_retain_cross_member_leaks(run_gate, tmp_path):
    """Inspecting member headers must preserve the concatenated decompressed stream."""
    target = tmp_path / 'a.gz'
    target.write_bytes(gzip.compress(b'/ho', mtime=0) + gzip.compress(b'me/someone/private-notes', mtime=0))
    r = run_gate('--paths', str(target))
    assert r.returncode == 1 and 'home-path' in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("filter_byte", range(5))
def test_pdf_png_predictor_exposes_decoded_strings(gate, filter_byte):
    # Two equal rows exercise each PNG predictor, including the previous-row state.
    row = b"(synthetic predictor text)"
    previous = bytes(len(row))
    encoded = bytearray()
    for _ in range(2):
        encoded.append(filter_byte)
        for i, value in enumerate(row):
            left = row[i - 1] if i else 0
            up = previous[i]
            upper_left = previous[i - 1] if i else 0
            p = left + up - upper_left
            distances = [abs(p - left), abs(p - up), abs(p - upper_left)]
            paeth = [left, up, upper_left][distances.index(min(distances))]
            prediction = [0, left, up, (left + up) // 2, paeth][filter_byte]
            encoded.append((value - prediction) % 256)
        previous = row
    payload = zlib.compress(bytes(encoded))
    header = b"/DecodeParms << /Predictor 15 /Columns " + str(len(row)).encode() + b" >>"
    assert gate._pdf_filter(payload, b"FlateDecode", header) == row * 2


def test_pdf_predictor_rejects_truncated_rows(gate):
    with pytest.raises(gate.GateError, match="row"):
        gate._pdf_filter(zlib.compress(b"\x02abc"), b"FlateDecode",
                         b"/DecodeParms << /Predictor 12 /Columns 7 >>")


def test_encoded_pdf_bytes_are_not_literal_string_metadata(run_gate, make_pdf_stream, tmp_path):
    # ASCII85 characters resemble PDF syntax, but decode to seven nontext bytes.
    payload = b"(F123)abc~>"
    target = tmp_path / "encoded.pdf"
    target.write_bytes(make_pdf_stream(payload, b"/ASCII85Decode"))
    result = run_gate("--paths", str(target))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("tag", [b"name", b"ZZZZ", b"glyf"])
@pytest.mark.parametrize("layer", ["private", "course"])
def test_embedded_pdf_font_keeps_private_checks(run_gate, gate, make_pdf_stream, tmp_path, tag, layer):
    private = tmp_path / "private"
    private.mkdir()
    (private / "private-patterns.txt").write_text("synthetic-owner-91")
    marker = b"synthetic-owner-91" if layer == "private" else PHRASE.encode()
    extra = []
    if layer == "course":
        salt = b"s" * 32
        (private / "shingle-salt").write_text(salt.hex())
        gate.write_index(private / "shingles.bin", {h for _, h in gate.text_windows(PHRASE, salt)})
        extra = ["--shingles", str(private / "shingles.bin")]
    if tag == b"name":
        table = struct.pack(">HHH", 0, 1, 18) + struct.pack(">HHHHHH", 1, 0, 0, 1, len(marker), 0) + marker
    else:
        table = marker
    font = struct.pack(">IHHHH", 0x10000, 1, 16, 0, 0)
    font += struct.pack(">4sIII", tag, 0, 28, len(table)) + table
    target = tmp_path / "font.pdf"
    target.write_bytes(make_pdf_stream(zlib.compress(font), b"/FlateDecode"))
    result = run_gate("--paths", str(target), "--private-dir", str(private), "--require-private", *extra)
    expected = "private-" if layer == "private" else "shingle-"
    assert result.returncode == 1 and expected in result.stdout, result.stdout + result.stderr


def test_embedded_pdf_font_numeric_tables_are_not_pdf_strings(run_gate, make_pdf_stream, tmp_path):
    table = b"(F123)"
    font = struct.pack(">IHHHH", 0x10000, 1, 16, 0, 0)
    font += struct.pack(">4sIII", b"glyf", 0, 28, len(table)) + table
    target = tmp_path / "geometry.pdf"
    target.write_bytes(make_pdf_stream(zlib.compress(font), b"/FlateDecode"))
    result = run_gate("--paths", str(target))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("joined,expected", [(False, 0), (True, 0)])
def test_pdf_independent_mapping_records_do_not_form_prose(
        run_gate, gate, make_pdf_stream, tmp_path, joined, expected):
    private = tmp_path / "private"
    private.mkdir()
    salt = b"s" * 32
    (private / "shingle-salt").write_text(salt.hex())
    gate.write_index(private / "shingles.bin", {gate.shingle_hash(list("abcdefgh"), salt)})
    letters = [chr(code) for code in range(65, 73)]
    payload = ("(" + " ".join(letters) + ")" if joined else
               " ".join("<%04x>" % ord(letter) for letter in letters)).encode()
    target = tmp_path / "mappings.pdf"
    target.write_bytes(make_pdf_stream(payload, b"[]"))
    result = run_gate("--paths", str(target), "--shingles", str(private / "shingles.bin"))
    assert result.returncode == expected, result.stdout + result.stderr
