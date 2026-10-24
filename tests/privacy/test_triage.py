# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Third-party declarations retain private checks and respect font record boundaries."""
import json
import struct

import pytest


PHRASE = 'violet lanterns illuminate seven copper bridges beside amber rivers'
OWNER = 'synthetic-owner-91'
EMAIL = '208164922+dorsaltegmentum@users.noreply.github.com'


def declare(repo, license='OFL-1.1'):
    repo.mkdir(exist_ok=True)
    (repo / 'REUSE.toml').write_text(
        'version = 1\n[[annotations]]\npath = ["vendor/**"]\n'
        'precedence = "override"\nSPDX-FileCopyrightText = "Example Font Authors"\n'
        f'SPDX-License-Identifier = {json.dumps(license)}\n')
    vendor = repo / 'vendor'
    vendor.mkdir(exist_ok=True)
    return vendor


def index(gate, tmp_path):
    priv = tmp_path / 'private'
    priv.mkdir()
    salt = b's' * 32
    (priv / 'shingle-salt').write_text(salt.hex())
    (priv / 'private-patterns.txt').write_text(OWNER)
    gate.write_index(priv / 'shingles.bin', {
        'course': {h for _, h in gate.text_windows(PHRASE, salt)},
        'prose-flagged': {h for _, h in gate.text_windows('the library staff approved a fresh layout for spring', salt)},
    })
    return priv


def sfnt(tag, table):
    return (struct.pack('>IHHHH', 0x10000, 1, 16, 0, 0)
            + struct.pack('>4sIII', tag, 0, 28, len(table)) + table)


@pytest.mark.parametrize('license,expected', [('OFL-1.1', 0), ('AGPL-3.0-or-later AND MIT', 0),
                                              ('AGPL-3.0-or-later', 1)])
def test_reuse_declaration_controls_public_layer(run_gate, tmp_path, make_font, license, expected):
    # Public metadata must pass only when REUSE declares an upstream component.
    repo = tmp_path / 'repo'
    p = declare(repo, license) / 'sample.ttf'
    p.write_bytes(make_font(['Font Designer <designer@foundry.test>', 'released 2022-03-04']))
    r = run_gate('--repo', str(repo), '--paths', str(p))
    assert r.returncode == expected, r.stdout + r.stderr
    if expected:
        assert ' email' in r.stdout and 'study-date' in r.stdout
    else:
        assert r.stdout == ''


@pytest.mark.parametrize('part', ['name', 'meta', 'opaque', 'woff-meta', 'text'])
@pytest.mark.parametrize('layer', ['private', 'course'])
def test_declared_third_party_keeps_private_and_course(run_gate, gate, tmp_path, make_font, make_woff,
                                                      part, layer):
    # A REUSE override must never hide planted private text in names or metadata.
    repo = tmp_path / 'repo'
    vendor = declare(repo)
    priv = index(gate, tmp_path)
    planted = OWNER if layer == 'private' else PHRASE
    if part == 'name':
        data = make_font(['Neutral Font', planted])
    elif part == 'meta':
        raw = planted.encode()
        data = sfnt(b'meta', struct.pack('>IIII4sII', 1, 0, 0, 1, b'dl01', 28, len(raw)) + raw)
    elif part == 'opaque':
        data = sfnt(b'ZZZZ', planted.encode())
    elif part == 'woff-meta':
        data = make_woff(['Neutral Font'] * 4, '<metadata>' + planted + '</metadata>')
    else:
        data = planted.encode()
    p = vendor / ('sample.woff' if part == 'woff-meta' else 'sample.bin')
    p.write_bytes(data)
    r = run_gate('--repo', str(repo), '--paths', str(p), '--require-private',
                 '--shingles', str(priv / 'shingles.bin'), env={'LT_PRIVACY_DIR': str(priv)})
    assert r.returncode == 1, r.stdout + r.stderr
    assert ('private-' if layer == 'private' else 'shingle-') in r.stdout
    assert planted not in r.stdout


def test_third_party_uses_course_shingles_only(run_gate, gate, tmp_path):
    repo = tmp_path / 'repo'
    p = declare(repo) / 'notice.txt'
    p.write_text('the library staff approved a fresh layout for spring')
    priv = index(gate, tmp_path)
    r = run_gate('--repo', str(repo), '--paths', str(p), '--shingles', str(priv / 'shingles.bin'))
    assert r.returncode == 0 and r.stdout == '', r.stdout + r.stderr


def test_undeclared_font_retains_public_checks(run_gate, tmp_path, make_font):
    p = tmp_path / 'sample.ttf'
    p.write_bytes(make_font(['designer@foundry.test']))
    r = run_gate('--paths', str(p))
    assert r.returncode == 1 and ' email' in r.stdout


@pytest.mark.parametrize('kind,expected', [('indices', 0), ('letters', 0), ('sentence', 1)])
def test_post_names_do_not_form_cross_record_course_prose(run_gate, gate, tmp_path, kind, expected):
    # Pascal length bytes and adjacent glyph identifiers cannot form a sentence.
    priv = tmp_path / 'private'
    priv.mkdir()
    salt = b's' * 32
    (priv / 'shingle-salt').write_text(salt.hex())
    sentence = 'Violet lanterns illuminate copper bridges beside amber rivers'
    gate.write_index(priv / 'shingles.bin', {
        gate.shingle_hash(list('abcdefgh'), salt), gate.shingle_hash(sentence.lower().split(), salt),
    })
    if kind != 'indices':
        names = [sentence if kind == 'sentence' else ' '.join(chr(i) for i in range(65, 73))]
        indices = [258]
    else:
        # Valid custom-name indices contain TAB + A..H as big-endian bytes.
        indices = list(range(0x941, 0x949))
        names = ['glyph' + str(i) for i in range(max(indices) - 257)]
    table = struct.pack('>I', 0x20000) + bytes(28) + struct.pack('>H', len(indices))
    table += b''.join(struct.pack('>H', i) for i in indices)
    table += b''.join(bytes([len(n)]) + n.encode() for n in names)
    p = tmp_path / 'sample.ttf'
    p.write_bytes(sfnt(b'post', table))
    r = run_gate('--paths', str(p), '--shingles', str(priv / 'shingles.bin'))
    assert r.returncode == expected, r.stdout + r.stderr
    if expected:
        assert 'shingle-' in r.stdout


def test_post_structure_keeps_course_checks(run_gate, gate, tmp_path):
    """A structural table exemption must not hide a complete multiword sentence."""
    sentence = 'Red ox may eat hay in a pen'
    priv = tmp_path / 'private'
    priv.mkdir()
    salt = b's' * 32
    (priv / 'shingle-salt').write_text(salt.hex())
    gate.write_index(priv / 'shingles.bin', {gate.shingle_hash(sentence.lower().split(), salt)})
    table = struct.pack('>I', 0x20000) + sentence.encode().ljust(28, b'\0') + bytes(2)
    p = tmp_path / 'sample.ttf'
    p.write_bytes(sfnt(b'post', table))
    result = run_gate('--paths', str(p), '--shingles', str(priv / 'shingles.bin'))
    assert result.returncode == 1 and 'shingle-' in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize('escape', [r'\x1f', r'\x1e', r'\u001f', r'\n', r'\t'])
def test_email_rule_separates_escaped_control_prefixes(run_gate, tmp_path, escape):
    # Escape spelling must not become part of an approved email local part.
    p = tmp_path / 'sample.py'
    p.write_text("record = 'Example" + escape + EMAIL + "'\n")
    r = run_gate('--paths', str(p))
    assert r.returncode == 0, r.stdout + r.stderr
    p.write_text("record = 'Example" + escape + "private.person@foundry.test'\n")
    r = run_gate('--paths', str(p))
    assert r.returncode == 1 and ' email' in r.stdout


def test_email_prefix_without_escape_remains_part_of_address(run_gate, tmp_path):
    p = tmp_path / 'sample.txt'
    p.write_text('x1f' + EMAIL)
    r = run_gate('--paths', str(p))
    assert r.returncode == 1 and ' email' in r.stdout
