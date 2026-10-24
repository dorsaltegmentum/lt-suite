# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shingle layer: classes, the sources file, the builder and the private allowlist.

Every corpus here is synthetic. A course window blocks; a prose window blocks only when
its source span matched a layer 1 or 2 pattern, else it is a review item on stderr.
"""
import json

import pytest

PHRASE = "violet lanterns illuminate seven copper bridges beside amber rivers"
PROSE = "the walrus committee prefers quiet harbours during long winter evenings"
FLAGGED = "on 2021-03-04 the heron archive moved into a quiet basement vault near the old canal bridge"
GENERATED = "lemon kites carry nine paper whistles above silver meadows today"
TOOL = "helper prints status lines while the loader waits for input"


def corpus(tmp_path, files: dict[str, str], sources: str):
    src = tmp_path / "corpus"
    for rel, text in files.items():
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    priv = tmp_path / "private"
    priv.mkdir(exist_ok=True)
    (priv / "shingle-sources.txt").write_text(sources, encoding="utf-8")
    return src, priv


def target(tmp_path, text: str, name: str = "target.txt"):
    f = tmp_path / "release" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


def test_eight_word_repeat_is_detected(gate, tmp_path):
    salt = b"s" * 32
    (tmp_path / "shingle-salt").write_text(salt.hex())
    hashes = {h for _, h in gate.text_windows(PHRASE, salt)}
    gate.write_index(tmp_path / "shingles.bin", hashes)
    index = gate.Shingles(tmp_path / "shingles.bin")
    hits = index.scan(PHRASE.upper().replace(" ", "\n", 1), "a.txt", "a.txt")
    assert hits and hits[0].note == "course"
    assert not index.scan("seven harmless words appear alone in this sentence", "a.txt", "a.txt")
    first = f"{next(iter(hashes)):016x}"
    (tmp_path / "shingle-allowlist.toml").write_text(
        f'[[allow]]\nhash = "{first}"\npath = "docs/**"\nreason = "Synthetic overlap"\n')
    index = gate.Shingles(tmp_path / "shingles.bin")
    assert index.allowed(int(first, 16), "docs/guide.md")
    assert not index.allowed(int(first, 16), "answers.json")


@pytest.mark.parametrize("text", [" ".join(chr(c) for c in range(65, 73)), "1 2 3 4 5 6 7 8"])
def test_single_character_windows_are_skipped_by_builder_and_scanner(run_gate, gate, tmp_path, text):
    """Alphabet and option-label runs must neither enter an index nor trigger an older index."""
    src, priv = corpus(tmp_path, {"course.txt": text}, "[course]\ncourse.txt\n")
    result = run_gate("build-shingles", "--from", str(src), "--out", str(priv))
    assert result.returncode == 0, result.stderr
    index = gate.Shingles(priv / "shingles.bin")
    assert index.classify(gate.shingle_hash(gate.words(text), index.salt)) is None
    assert list(gate.text_windows(text, index.salt)) == []
    gate.write_index(priv / "shingles.bin", {gate.shingle_hash(gate.words(text), index.salt)})
    result = run_gate("--paths", str(target(tmp_path, text)), "--shingles", str(priv / "shingles.bin"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_eight_word_course_sentence_still_blocks(run_gate, tmp_path):
    """Discarding option letters must preserve ordinary course-sentence detection."""
    sentence = "Violet lanterns illuminate copper bridges beside amber rivers"
    src, priv = corpus(tmp_path, {"course.txt": sentence}, "[course]\ncourse.txt\n")
    result = run_gate("build-shingles", "--from", str(src), "--out", str(priv))
    assert result.returncode == 0, result.stderr
    result = run_gate("--paths", str(target(tmp_path, sentence)), "--shingles", str(priv / "shingles.bin"))
    assert result.returncode == 1 and "course" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("entry", [
    'hash = "0123456789abcdef"\nreason = "no path"\n',
    'hash = "0123456789abcdef"\npath = "docs/a.md"\n',
    'hash = "xyz"\npath = "docs/a.md"\nreason = "bad hash"\n',
])
def test_shingle_allowlist_needs_hash_path_and_reason(gate, tmp_path, entry):
    salt = b"s" * 32
    (tmp_path / "shingle-salt").write_text(salt.hex())
    gate.write_index(tmp_path / "shingles.bin", set())
    (tmp_path / "shingle-allowlist.toml").write_text("[[allow]]\n" + entry)
    with pytest.raises(gate.GateError, match="needs hash"):
        gate.Shingles(tmp_path / "shingles.bin")


def test_course_blocks_and_plain_prose_is_a_review_item(run_gate, tmp_path):
    """Class 1 (course) span fails; class 2 (prose) span without a personal pattern passes."""
    src, priv = corpus(tmp_path, {"fixtures/module-a.json": json.dumps({"prompt": PHRASE}),
                                  "notes/log.md": PROSE + "\n"},
                       "[course]\nfixtures/module-*.json\n[prose]\nnotes/**\n")
    assert run_gate("build-shingles", "--from", str(src), "--out", str(priv)).returncode == 0
    index = str(priv / "shingles.bin")
    r = run_gate("--paths", str(target(tmp_path, "intro\n" + PHRASE + "\n")), "--shingles", index)
    assert r.returncode == 1, r.stderr
    assert r.stdout.split() == ["shingle-" + r.stdout[8:16], "target.txt:2", "course"]
    r = run_gate("--paths", str(target(tmp_path, PROSE + "\n", "prose.txt")), "--shingles", index)
    assert r.returncode == 0 and r.stdout == "", r.stdout
    assert "review shingle-" in r.stderr and "prose.txt:1 prose" in r.stderr
    assert "1 prose review items" in r.stderr
    assert "walrus" not in r.stdout + r.stderr


def test_prose_span_with_a_public_pattern_blocks(run_gate, tmp_path):
    """A prose window that covered a layer 1 match in its source blocks, even when the
    release copy dropped the punctuation that layer 1 needs."""
    src, priv = corpus(tmp_path, {"notes/log.md": FLAGGED + "\n"}, "[course]\nnone/*.json\n[prose]\nnotes/**\n")
    assert run_gate("build-shingles", "--from", str(src), "--out", str(priv)).returncode == 0
    copy = FLAGGED.replace("2021-03-04", "2021 03 04")
    r = run_gate("--paths", str(target(tmp_path, copy + "\n")), "--shingles", str(priv / "shingles.bin"))
    assert r.returncode == 1 and r.stdout.rstrip().endswith("prose-flagged"), r.stdout + r.stderr
    # Windows that never touched the date stay review items.
    tail = "the heron archive moved into a quiet basement vault near the old canal bridge"
    r = run_gate("--paths", str(target(tmp_path, tail + "\n", "tail.txt")), "--shingles", str(priv / "shingles.bin"))
    assert r.returncode == 0 and "tail.txt:1 prose" in r.stderr, r.stdout + r.stderr


def test_prose_span_with_a_private_pattern_blocks(run_gate, tmp_path):
    src, priv = corpus(tmp_path, {"notes/log.md": "met synthetic-owner-91 near the eastern pier at dawn again\n"},
                       "[course]\nnone/*.json\n[prose]\nnotes/**\n")
    (priv / "private-patterns.txt").write_text(r"\bsynthetic-owner-91\b" + "\n")
    assert run_gate("build-shingles", "--from", str(src), "--out", str(priv)).returncode == 0
    copy = "met synthetic owner 91 near the eastern pier at dawn again"
    r = run_gate("--paths", str(target(tmp_path, copy + "\n")), "--shingles", str(priv / "shingles.bin"))
    assert r.returncode == 1 and "prose-flagged" in r.stdout, r.stdout + r.stderr
    assert "synthetic" not in r.stdout + r.stderr


def test_generated_files_lose_benign_windows(run_gate, tmp_path):
    """Per-run scripts embed release code: only their course text blocks."""
    src, priv = corpus(tmp_path, {
        "runs/a/fill.js": f'const note = "{TOOL}";\nconst answer = "{GENERATED}";\n',
        "plugins/lib.js": f'const note = "{TOOL}";\n',
    }, "[generated]\nruns/**/*.js\n[benign]\nplugins/**\n")
    assert run_gate("build-shingles", "--from", str(src), "--out", str(priv)).returncode == 0
    index = str(priv / "shingles.bin")
    assert run_gate("--paths", str(target(tmp_path, GENERATED)), "--shingles", index).returncode == 1
    assert run_gate("--paths", str(target(tmp_path, TOOL, "tool.txt")), "--shingles", index).returncode == 0


def test_course_ranges_need_phrase_exceptions(run_gate, gate, tmp_path):
    """Every designated course window blocks until its exact phrase is reviewed."""
    ported = ("# helper prints status lines while the loader waits for input\n"
              "# helper prints status lines while the loader waits for input, example: the patient "
              "presented with jaundice\n"
              "def run(): return 'helper prints status lines while the loader waits'\n")
    src, priv = corpus(tmp_path, {"plugins/tool.py": ported},
                       "[course]\nplugins/tool.py:2\n[benign]\nplugins/**\n")
    r = run_gate("build-shingles", "--from", str(src), "--out", str(priv))
    assert r.returncode == 0 and "from course ranges in 1 files" in r.stderr, r.stderr
    index = str(priv / "shingles.bin")
    leak = "for input, example: the patient presented with jaundice"
    assert run_gate("--paths", str(target(tmp_path, leak)), "--shingles", index).returncode == 1
    generic = "helper prints status lines while the loader waits"
    assert run_gate("--paths", str(target(tmp_path, generic, "g.txt")), "--shingles", index).returncode == 1
    salt = gate.read_salt(priv)
    hashes = [h for _, h in gate.text_windows(generic, salt)]
    (priv / 'shingle-allowlist.toml').write_text(''.join(
        f'[[allow]]\nhash = "{h:016x}"\npath = "g.txt"\nreason = "Reviewed synthetic phrase"\n'
        for h in hashes))
    assert run_gate("--paths", str(target(tmp_path, generic, "g.txt")), "--shingles", index).returncode == 0
    assert run_gate("--paths", str(target(tmp_path, generic, "other.txt")), "--shingles", index).returncode == 1


def test_sources_file_syntax(gate):
    rules = gate.parse_sources("# c\nfixtures/*.json\n-plugins/**\n[prose]\nnotes/**\n~extra.md\n"
                               "[course]\nplugins/a.py:3-5,9\n[skip]\n**/*.png\n")
    assert [(r.section, r.ranges) for r in rules] == [
        ("course", None), ("benign", None), ("prose", None), ("prose", None),
        ("course", [(3, 5), (9, 9)]), ("skip", None)]
    plan = gate.file_plan("plugins/a.py", rules)
    assert [plan(n) for n in (2, 3, 9, 10)] == ["benign", "ranged", "ranged", "benign"]
    assert gate.file_plan("x/y.png", rules) is None
    for bad in ("[nope]\na\n", "[prose]\nnotes/a.md:3\n", "a.py:5-2\n", "[prose]\nnotes/**\n"):
        with pytest.raises(gate.GateError):
            gate.parse_sources(bad)


def test_builder_accepts_index_file_output(run_gate, tmp_path):
    """The documented --out INDEX spelling must produce an index, rather than a folder."""
    src, _ = corpus(tmp_path, {"a.json": json.dumps({"prompt": PHRASE})}, "")
    sources = tmp_path / "sources.txt"
    sources.write_text("*.json\n")
    output = tmp_path / "private2" / "shingles.bin"
    r = run_gate("build-shingles", "--from", str(src), "--out", str(output), "--sources", str(sources))
    assert r.returncode == 0, r.stderr
    assert output.is_file()
    r = run_gate("--paths", str(target(tmp_path, PHRASE)), "--shingles", str(output))
    assert r.returncode == 1 and r.stdout.startswith("shingle-")
    assert "violet" not in r.stdout


def test_short_or_repetitive_text_still_enters_index(gate):
    """Every eight-word window is indexed, including repeated vocabulary."""
    assert list(gate.text_windows("alpha beta alpha beta alpha beta alpha beta", b"s" * 32))


def test_source_code_tokens_do_not_enter_corpus(gate, tmp_path):
    code = 'const copper = lanterns.map(value => value + bridges);\nconst prompt = ' + json.dumps(PHRASE)
    segments = gate.source_segments(tmp_path / "a.js", code.encode())
    assert any(PHRASE in s for _, s in segments) and all(n == 2 for n, _ in segments)
    assert not any("lanterns.map" in s for _, s in segments)
    py = f'values = [item for item in range(30)]\nprompt = {PHRASE!r}\n'
    segments = gate.source_segments(tmp_path / "a.py", py.encode())
    assert (2, PHRASE) in [(n, s.strip("'")) for n, s in segments]
    assert not any("range(30)" in s for _, s in segments)
    js = json.dumps({"a_key_with_many_words_in_it": {"prompt": PHRASE}}, indent=1)
    segments = gate.source_segments(tmp_path / "a.json", js.encode())
    assert segments == [(3, PHRASE)]


def test_index_versions_and_corruption(gate, tmp_path):
    p = tmp_path / "shingles.bin"
    (tmp_path / "shingle-salt").write_text((b"s" * 32).hex())
    p.write_bytes(b"not an index")
    with pytest.raises(gate.GateError, match="bad header"):
        gate.Shingles(p)
    p.write_bytes(gate.INDEX_MAGIC_V1 + (1).to_bytes(8, "big") + (7).to_bytes(8, "big"))
    assert gate.Shingles(p).classify(7) == "course"
    gate.write_index(p, {"course": {1}, "prose-flagged": {1, 2}, "prose": {2, 3}})
    idx = gate.Shingles(p)
    assert [idx.classify(h) for h in (1, 2, 3, 4)] == ["course", "prose-flagged", "prose", None]
    p.write_bytes(p.read_bytes()[:-1])
    with pytest.raises(gate.GateError, match="size"):
        gate.Shingles(p)


def test_encoded_text_and_names_use_shingle_layer(gate, tmp_path):
    """URL escaping and an archive filename cannot hide a known course phrase."""
    salt = b"s" * 32
    (tmp_path / "shingle-salt").write_text(salt.hex())
    gate.write_index(tmp_path / "shingles.bin", {h for _, h in gate.text_windows(PHRASE, salt)})
    rules = gate.load_rules(gate.PATTERNS_FILE)
    scanner = gate.Gate(rules, [], [], gate.Shingles(tmp_path / "shingles.bin"))
    assert any(h.kind == "shingle" for h in scanner.scan_text(PHRASE.replace(" ", "%20"), "x", "x"))
    assert any(h.kind == "shingle" for h in scanner.scan_names([PHRASE.replace(" ", "-") + ".txt"]))
