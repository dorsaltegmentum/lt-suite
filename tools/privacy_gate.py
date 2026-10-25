# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Privacy gate: fail when a release file or commit carries personal or course data.

Layers:
  1. Public patterns, tools/privacy/patterns.toml (generic shapes only).
  2. Private regexes, <private dir>/private-patterns.txt and the PRIVACY_PATTERNS_B64
     environment variable (base64 of the same file). The private dir is LT_PRIVACY_DIR,
     else the folder "<repo folder name>-private" beside the repo.
  3. Course-text shingles (--shingles INDEX): salted 64-bit BLAKE2b hashes of every
     informative 8-word window of private sources, each tagged with a class.
     Windows made entirely of single-character tokens are skipped in builds and scans.
     "course" windows
     (course text) always block. "prose-flagged" windows (owner prose whose source
     span matched a layer 1 or layer 2 pattern) block. "prose" windows (other owner
     prose) print to stderr as review items and do not change the exit code. The
     salt and the shingle allowlist sit beside the index.

What is scanned: working and staged file contents (recursive JSON, base64, HTML and URL decoding;
PDF metadata, XMP and page text; PNG text chunks and ICC names; JPEG comment, EXIF and
XMP; font tables and WOFF metadata; zip, tar and gzip members and their headers;
data URIs and compressed diagram payloads; printable strings of any other binary),
symlink targets, file and folder names, and with --history every commit's author,
committer, offsets and message plus every blob at every path in any commit.

Exit 0: no hit outside the allowlist. Exit 1: at least one hit. Exit 2: the gate
could not run (bad argument, unreadable input, missing extractor, a size or depth
limit, a crash, or --require-private with no private pattern loaded).

Output, one line per hit, no matched text unless --excerpt (local runs only):
  path:line:col pattern-id      public hit (line 0 is the file or folder name)
  private-<n> path:line         private-layer hit
  shingle-<hash8> path:line <class>   blocking shingle hit (class course or prose-flagged)
Review items go to stderr as "review shingle-<hash8> path:line prose".
Binary parts and archive members print as "file.pdf#meta", "a.zip!dir/b.txt";
history blobs as "path@<blob id>", commits as "commit:<id>".

    python3 tools/privacy_gate.py                       tracked files
    python3 tools/privacy_gate.py --all --history --require-private --shingles INDEX
    python3 tools/privacy_gate.py --paths skills/lt-module-fill --require-private
    python3 tools/privacy_gate.py build-shingles --from SRC_DIR --out PRIVATE_DIR
"""
from __future__ import annotations

import argparse
import base64
import binascii
import bisect
import hashlib
import html
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import tokenize
import unicodedata
import urllib.parse
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("privacy_gate.py needs Python 3.11 or newer.")
import tomllib  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PATTERNS_FILE = REPO / "tools" / "privacy" / "patterns.toml"
ALLOWLIST_FILE = REPO / "tools" / "privacy" / "allowlist.toml"
ENV_DIR = "LT_PRIVACY_DIR"
ENV_B64 = "PRIVACY_PATTERNS_B64"
PRIVATE_FILE = "private-patterns.txt"
SALT_FILE = "shingle-salt"
SHINGLE_ALLOW_FILE = "shingle-allowlist.toml"
SOURCES_FILE = "shingle-sources.txt"
INDEX_FILE = "shingles.bin"
SHINGLE_N = 8
INDEX_MAGIC_V1 = b"LTSHGL1\n"    # one hash array, read as class "course"
INDEX_MAGIC = b"LTSHGL2\n"       # three counts, then one sorted hash array per class
CLASSES = ("course", "prose-flagged", "prose")   # most severe first
BLOCKING = {"course", "prose-flagged"}
WALK_SKIP = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
MAX_DEPTH = 4                    # nested archives and embedded payloads
MAX_MEMBER = 256 * 1024 * 1024   # one decompressed member or stream
MAX_TOTAL = 2 * 1024 ** 3        # all bytes extracted from one top-level file
MAX_MEMBERS = 100_000            # archive members per top-level file
EXCERPT = 60
DECODE_DEPTH = 3
PRIVATE_TAG_PATHS = {
    "example-institution": ("examples/usyd-md/**", "docs/configuration.md"),
    "example-format": ("examples/usyd-md/**",),
}

ALLOW_KEYS = {"path", "pattern", "reason", "match", "line_match", "scope"}
PATTERN_KEYS = {"id", "regex", "flags", "scope", "description"}


class GateError(Exception):
    """The gate cannot run: exit 2."""


def default_private_dir() -> Path:
    env = os.environ.get(ENV_DIR)
    if env:
        return Path(env).expanduser()
    return REPO.parent / f"{REPO.name}-private"


# ---------------------------------------------------------------- rules


@dataclass
class Pattern:
    id: str
    rx: re.Pattern
    scope: str = "all"


@dataclass
class PrivatePattern:
    num: str
    rx: re.Pattern
    tag: str | None = None


@dataclass
class Allow:
    globs: list[re.Pattern]
    patterns: set[str]
    reason: str
    match: re.Pattern | None = None
    line_match: re.Pattern | None = None
    scope: str = "file"


@dataclass
class Rules:
    patterns: list[Pattern]
    identity: dict
    never_allow: set[str]
    self_paths: list[str]


def glob_re(glob: str) -> re.Pattern:
    """Compile a repo-relative glob: "**" spans folders, "*" and "?" stay inside one."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z", re.S)


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot read {path.name}: {exc}") from None


def _compile(rx: str, flags: int, where: str) -> re.Pattern:
    try:
        return re.compile(rx, flags)
    except re.error as exc:
        raise GateError(f"{where}: regex does not compile ({exc.msg})") from None


def load_rules(path: Path) -> Rules:
    data = _toml(path)
    pats: list[Pattern] = []
    seen: set[str] = set()
    for n, entry in enumerate(data.get("pattern", []), 1):
        bad = set(entry) - PATTERN_KEYS
        if bad or "id" not in entry or "regex" not in entry:
            raise GateError(f"{path.name} pattern {n}: needs id and regex, unknown keys {sorted(bad)}")
        pid = entry["id"]
        if pid in seen:
            raise GateError(f"{path.name}: duplicate pattern id {pid}")
        seen.add(pid)
        flag_text = entry.get("flags", "")
        if set(flag_text) - {"i"}:
            raise GateError(f"{path.name} pattern {pid}: flags may only be \"i\"")
        flags = re.MULTILINE | (re.IGNORECASE if "i" in flag_text else 0)
        scope = entry.get("scope", "all")
        if scope not in ("all", "commit-message"):
            raise GateError(f"{path.name} pattern {pid}: scope must be all or commit-message")
        rx = _compile(entry["regex"], flags, f"{path.name} pattern {pid}")
        if rx.match(""):
            raise GateError(f"{path.name} pattern {pid}: regex matches the empty string")
        pats.append(Pattern(pid, rx, scope))
    if not pats:
        raise GateError(f"{path.name}: no patterns")
    policy = data.get("policy", {})
    return Rules(pats, data.get("identity", {}), set(policy.get("never_allow", [])),
                 list(policy.get("self_paths", [])))


def load_third_party(repo: Path) -> list[re.Pattern]:
    """REUSE override annotations with upstream licences select private/course scans.

    The project's blanket annotation and same-licence header overrides do not
    declare third-party content. Missing REUSE metadata keeps every layer active.
    """
    path = repo / "REUSE.toml"
    if not path.exists():
        return []
    globs = []
    for entry in _toml(path).get("annotations", []):
        if entry.get("precedence") != "override":
            continue
        licence = entry.get("SPDX-License-Identifier", "")
        licences = [licence] if isinstance(licence, str) else licence
        if not isinstance(licences, list) or any(not isinstance(x, str) for x in licences):
            raise GateError("REUSE.toml: invalid SPDX licence annotation")
        ids = set(re.findall(r"[A-Za-z0-9.+-]+", " ".join(licences)))
        if not ids - {"AGPL-3.0-or-later", "AND", "OR", "WITH"}:
            continue
        paths = entry.get("path", [])
        paths = [paths] if isinstance(paths, str) else paths
        if not isinstance(paths, list) or any(not isinstance(x, str) or not x for x in paths):
            raise GateError("REUSE.toml: invalid third-party path annotation")
        globs.extend(glob_re(p) for p in paths)
    return globs


def _within_fixed_glob(candidate: str, limit: str) -> bool:
    """Accept exact paths or narrower globs under a fixed literal folder."""
    if any(part in ("", ".", "..") for part in candidate.split("/")) or "\\" in candidate:
        return False
    return candidate.startswith(limit[:-2]) if limit.endswith("/**") else candidate == limit


def load_allowlist(path: Path, rules: Rules) -> list[Allow]:
    data = _toml(path)
    ids = {p.id for p in rules.patterns}
    out: list[Allow] = []
    for n, entry in enumerate(data.get("allow", []), 1):
        where = f"{path.name} entry {n}"
        bad = set(entry) - ALLOW_KEYS
        if bad:
            raise GateError(f"{where}: unknown keys {sorted(bad)}")
        reason = str(entry.get("reason", "")).strip()
        if not reason:
            raise GateError(f"{where}: reason is required")
        globs = entry.get("path", "**")
        globs = [globs] if isinstance(globs, str) else list(globs)
        pats = entry.get("pattern")
        if not pats:
            raise GateError(f"{where}: pattern is required")
        pats = {pats} if isinstance(pats, str) else set(pats)
        scope = entry.get("scope", "file")
        if scope not in ("file", "commit-message"):
            raise GateError(f"{where}: scope must be file or commit-message")
        for p in pats:
            if p == "*" or p in rules.never_allow:
                if scope != "file" or not all(any(g.startswith(s) for s in rules.self_paths) for g in globs):
                    raise GateError(f"{where}: {p!r} may be allowlisted only under "
                                    f"{', '.join(rules.self_paths)}")
            elif p.startswith("private:"):
                if not re.fullmatch(r"private:[a-z0-9-]+", p):
                    raise GateError(f"{where}: bad private tag {p!r}")
                fixed = PRIVATE_TAG_PATHS.get(p.removeprefix("private:"), ())
                if (scope != "file" or "path" not in entry or not globs
                        or not all(any(_within_fixed_glob(g, limit) for limit in fixed) for g in globs)):
                    raise GateError(f"{where}: {p!r} must stay within its fixed path restrictions")
            elif p.startswith(("private-", "shingle")):
                raise GateError(f"{where}: numbered private hits and shingle hits are "
                                "allowlisted only in the private dir")
            elif p not in ids:
                raise GateError(f"{where}: unknown pattern id {p!r}")
        out.append(Allow(
            globs=[glob_re(g) for g in globs], patterns=pats, reason=reason,
            match=_compile(entry["match"], 0, f"{where} match") if entry.get("match") else None,
            line_match=(_compile(entry["line_match"], 0, f"{where} line_match")
                        if entry.get("line_match") else None),
            scope=scope))
    return out


PRIVATE_LINE = re.compile(r"^(?:\[(?P<tag>[a-z0-9-]+)\]\s+)?(?P<rx>.+?)\s*$")


def parse_private(text: str, prefix: str, origin: str) -> list[PrivatePattern]:
    """One regex per line; "#" starts a comment line; "[tag] regex" sets an allowlist tag."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = PRIVATE_LINE.match(line.strip())
        rx = _compile(m["rx"], re.MULTILINE, f"{origin} line {n}")
        if rx.match(""):
            raise GateError(f"{origin} line {n}: regex matches the empty string")
        out.append(PrivatePattern(f"{prefix}{n}", rx, m["tag"]))
    return out


def load_private(private_dir: Path) -> tuple[list[PrivatePattern], list[str]]:
    """Return the private patterns and the names of the sources that gave at least one."""
    pats: list[PrivatePattern] = []
    loaded: list[str] = []
    f = private_dir / PRIVATE_FILE
    if f.is_file():
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise GateError(f"cannot read the private pattern file: {exc.__class__.__name__}") from None
        got = parse_private(text, "", "private pattern file")
        pats += got
        if got:
            loaded.append("file")
    b64 = "".join(os.environ.get(ENV_B64, "").split())
    if b64:
        try:
            text = base64.b64decode(b64, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise GateError(f"{ENV_B64} is not base64 of UTF-8 text") from None
        known = {(p.tag, p.rx.pattern) for p in pats}
        got = parse_private(text, "e", ENV_B64)
        pats += [p for p in got if (p.tag, p.rx.pattern) not in known]
        if got:
            loaded.append("env")
    return pats, loaded


# ---------------------------------------------------------------- shingles

ESC_U = re.compile(r"\\u([0-9a-fA-F]{4})")
ESC_C = re.compile(r"\\[nrt]")
TAG = re.compile(r"</?[A-Za-z][^<>]{0,200}>")
SPLIT = re.compile(r"[\W_]+")


def _unescape_u(m: re.Match) -> str:
    return chr(int(m.group(1), 16))


def words(line: str) -> list[str]:
    """Normalise one line the same way for the index and the scan."""
    line = ESC_U.sub(_unescape_u, line)
    line = ESC_C.sub(" ", line)
    line = html.unescape(line)
    line = TAG.sub(" ", line)
    line = unicodedata.normalize("NFKC", line).casefold()
    return [w for w in SPLIT.split(line) if w]


def shingle_hash(window: list[str], salt: bytes) -> int:
    digest = hashlib.blake2b(" ".join(window).encode("utf-8", "surrogatepass"), digest_size=8,
                             key=salt).digest()
    return int.from_bytes(digest, "big")


def informative_window(window: list[str]) -> bool:
    """Ignore runs made entirely of single-character labels or font indices."""
    return any(len(word) > 1 for word in window)


JSON_KEY = re.compile(r'"[A-Za-z_][\w.-]{0,63}"\s*:')


def tokens(text: str, start_line: int = 1,
           flaggers: list[re.Pattern] | None = None) -> list[tuple[str, int, bool]]:
    """(word, line number, flagged) for every word of text. With flaggers (build time
    only), a word is flagged when a flagger match covers part of it."""
    lines = text.split("\n")
    marks: dict[int, list[tuple[int, int]]] = {}
    if flaggers:
        starts = [0]
        for line in lines[:-1]:
            starts.append(starts[-1] + len(line) + 1)
        for rx in flaggers:
            for m in rx.finditer(text):
                if m.end() == m.start():
                    continue
                a = bisect.bisect_right(starts, m.start()) - 1
                b = bisect.bisect_right(starts, m.end() - 1) - 1
                for i in range(a, b + 1):
                    lo = m.start() - starts[i] if i == a else 0
                    hi = m.end() - starts[i] if i == b else len(lines[i])
                    marks.setdefault(i, []).append((lo, hi))
    out: list[tuple[str, int, bool]] = []
    for i, line in enumerate(lines):
        ws = words(line)
        flags = [False] * len(ws)
        for lo, hi in marks.get(i, ()):
            first = len(words(line[:lo]))
            if 0 < lo < len(line) and line[lo - 1].isalnum() and line[lo].isalnum():
                first -= 1          # the match starts inside a word
            for k in range(max(first, 0), min(len(words(line[:hi])), len(ws))):
                flags[k] = True
        out.extend((w, start_line + i, f) for w, f in zip(ws, flags))
    return out


def text_windows(text: str, salt: bytes):
    """Yield (first word's line, hash) for each informative 8-word window."""
    toks = tokens(text)
    for i in range(len(toks) - SHINGLE_N + 1):
        window = [t[0] for t in toks[i:i + SHINGLE_N]]
        if informative_window(window):
            yield toks[i][1], shingle_hash(window, salt)


def read_salt(private_dir: Path) -> bytes:
    f = private_dir / SALT_FILE
    try:
        salt = bytes.fromhex(f.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        raise GateError(f"cannot read the shingle salt ({SALT_FILE} beside the index)") from None
    if not 16 <= len(salt) <= 64:
        raise GateError(f"{SALT_FILE}: salt must be 16 to 64 bytes")
    return salt


def write_index(path: Path, classes: dict[str, set[int]] | set[int]) -> None:
    """Write the index; a plain set is written as class "course". A hash in several
    classes keeps the most severe one."""
    if not isinstance(classes, dict):
        classes = {"course": set(classes)}
    if set(classes) - set(CLASSES):
        raise GateError(f"unknown shingle classes {sorted(set(classes) - set(CLASSES))}")
    arrays, seen = [], set()
    for c in CLASSES:
        hs = sorted(set(classes.get(c, ())) - seen)
        seen.update(hs)
        arrays.append(hs)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(INDEX_MAGIC + struct.pack(">3Q", *(len(a) for a in arrays)))
        for a in arrays:
            f.write(struct.pack(f">{len(a)}Q", *a))
    os.replace(tmp, path)


class Shingles:
    """Class-tagged hash index. scan() returns blocking hits (the first per line and
    severity) and collects prose review items in self.reviews. The private allowlist
    silences both."""

    def __init__(self, index: Path):
        try:
            raw = index.read_bytes()
        except OSError as exc:
            raise GateError(f"cannot read the shingle index: {exc.strerror}") from None
        if raw.startswith(INDEX_MAGIC_V1) and len(raw) >= 16:
            counts, off = (struct.unpack(">Q", raw[8:16])[0], 0, 0), 16
        elif raw.startswith(INDEX_MAGIC) and len(raw) >= 32:
            counts, off = struct.unpack(">3Q", raw[8:32]), 32
        else:
            raise GateError("shingle index: bad header")
        if len(raw) != off + 8 * sum(counts):
            raise GateError("shingle index: size does not match its counts")
        self.classes: dict[str, frozenset[int]] = {}
        for c, n in zip(CLASSES, counts):
            self.classes[c] = frozenset(struct.unpack(f">{n}Q", raw[off:off + 8 * n]))
            off += 8 * n
        self.counts = dict(zip(CLASSES, counts))
        self.salt = read_salt(index.parent)
        self.reviews: list[Hit] = []
        self.allow: list[tuple[str, list[re.Pattern]]] = []
        self.exact_allow: dict[str, list[re.Pattern]] = {}
        allow_file = index.parent / SHINGLE_ALLOW_FILE
        if allow_file.is_file():
            for n, entry in enumerate(_toml(allow_file).get("allow", []), 1):
                h = str(entry.get("hash", "")).lower()
                g = entry.get("path")
                globs = [g] if isinstance(g, str) else list(g or [])
                if (not re.fullmatch(r"[0-9a-f]{8,16}", h) or not str(entry.get("reason", "")).strip()
                        or not globs or any(not isinstance(x, str) or not x for x in globs)):
                    raise GateError(f"{SHINGLE_ALLOW_FILE} entry {n}: needs hash (8 to 16 hex), "
                                    "path and reason")
                pats = [glob_re(x) for x in globs]
                if len(h) == 16:
                    self.exact_allow.setdefault(h, []).extend(pats)
                else:
                    self.allow.append((h, pats))

    def classify(self, h: int) -> str | None:
        for c in CLASSES:
            if h in self.classes[c]:
                return c
        return None

    def allowed(self, h: int, path: str) -> bool:
        hx = f"{h:016x}"
        return (any(g.match(path) for g in self.exact_allow.get(hx, ()))
                or any(hx.startswith(a) and any(g.match(path) for g in gs) for a, gs in self.allow))

    def scan(self, text: str, where: str, path: str, name: bool = False,
             course_only: bool = False) -> list["Hit"]:
        hits, done = [], set()
        toks = tokens(text)
        for i in range(len(toks) - SHINGLE_N + 1):
            line = toks[i][1]
            window = [t[0] for t in toks[i:i + SHINGLE_N]]
            if not informative_window(window):
                continue
            h = shingle_hash(window, self.salt)
            cls = self.classify(h)
            if cls is None or (course_only and cls != "course"):
                continue
            key = (line, cls in BLOCKING)
            if key in done or self.allowed(h, path):
                continue
            done.add(key)
            hit = Hit("shingle", f"{h:016x}"[:8], where, 0 if name else line, 0, note=cls)
            (hits if cls in BLOCKING else self.reviews).append(hit)
        return hits


# ---------------------------------------------------------------- hits


@dataclass(frozen=True, order=True)
class Hit:
    kind: str
    pid: str
    where: str
    line: int
    col: int
    excerpt: str = field(default="", compare=False)
    note: str = field(default="", compare=False)    # shingle class

    def render(self, excerpt: bool = False) -> str:
        if self.kind == "private":
            return f"private-{self.pid} {self.where}:{self.line}"
        if self.kind == "shingle":
            return f"shingle-{self.pid} {self.where}:{self.line}" + (f" {self.note}" if self.note else "")
        out = f"{self.where}:{self.line}:{self.col} {self.pid}"
        if excerpt and self.excerpt:
            out += "  | " + self.excerpt.replace("\n", " ")
        return out


# ---------------------------------------------------------------- extraction

PDF_STREAM = re.compile(rb"(?<![A-Za-z])stream(?:\r\n|\r|\n)")
PDF_HEX = re.compile(rb"(?<!<)<([0-9A-Fa-f\s]{4,})>(?!>)")
PDF_SPECIAL = re.compile(rb"[()\\]")
XMP = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.S)
PDF_ESC = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}
ASCII_RUN = re.compile(rb"(?:[\x20-\x7e\t]|[\xc2-\xf4][\x80-\xbf]{1,3}){6,}")
UTF16LE_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")
UTF16BE_RUN = re.compile(rb"(?:\x00[\x20-\x7e]){6,}")
DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+(?:;[\w=.+-]+)*;base64,([A-Za-z0-9+/=\s]{8,})")
DRAWIO = re.compile(r"<diagram\b[^>]*>\s*([A-Za-z0-9+/=\s]{16,}?)\s*</diagram>")
FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1", b"ttcf", b"wOFF", b"wOF2")
JSON_SLASH = re.compile(r"\\/")


def inflate(data: bytes, wbits: int = zlib.MAX_WBITS) -> bytes:
    """Decompress zlib or raw deflate data; refuse output above MAX_MEMBER."""
    d = zlib.decompressobj(wbits)
    out = d.decompress(data, MAX_MEMBER)
    if d.unconsumed_tail:
        raise GateError(f"compressed data expands beyond {MAX_MEMBER} bytes")
    return out


def printable_strings(data: bytes) -> str:
    runs = [m.group().decode("utf-8", "replace") for m in ASCII_RUN.finditer(data)]
    runs += [m.group().decode("utf-16-le", "replace") for m in UTF16LE_RUN.finditer(data)]
    runs += [m.group().decode("utf-16-be", "replace") for m in UTF16BE_RUN.finditer(data)]
    return "\n".join(runs)


def pdf_literals(body: bytes) -> list[bytes]:
    """Every PDF literal string, with balanced nested parentheses and escapes resolved."""
    out: list[bytes] = []
    pos, n = 0, len(body)
    while True:
        start = body.find(b"(", pos)
        if start < 0:
            return out
        buf, depth, i = bytearray(), 1, start + 1
        while i < n and depth:
            m = PDF_SPECIAL.search(body, i)
            if not m:
                buf += body[i:]
                i = n
                break
            buf += body[i:m.start()]
            c = body[m.start()]
            i = m.end()
            if c == 0x5C:  # backslash
                if i >= n:
                    break
                e = body[i]
                if e in PDF_ESC:
                    buf += PDF_ESC[e]
                    i += 1
                elif 0x30 <= e <= 0x37:
                    j = i
                    while j < min(i + 3, n) and 0x30 <= body[j] <= 0x37:
                        j += 1
                    buf.append(int(body[i:j], 8) & 0xFF)
                    i = j
                elif e in (0x0A, 0x0D):
                    i += 1
                else:
                    buf.append(e)
                    i += 1
            elif c == 0x28:
                depth += 1
                buf.append(c)
            else:
                depth -= 1
                if depth:
                    buf.append(c)
        if len(buf) >= 2:
            out.append(bytes(buf))
        pos = i


def _pdf_string(b: bytes) -> str:
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace")
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", "replace")
    return b.decode("latin-1")


def _pdf_runlength(data: bytes) -> bytes:
    out, pos = bytearray(), 0
    while pos < len(data):
        count = data[pos]
        pos += 1
        if count == 128:
            return bytes(out)
        needed = count + 1 if count < 128 else 1
        if pos + needed > len(data):
            raise GateError("PDF RunLength stream is truncated")
        out += data[pos:pos + needed] if count < 128 else data[pos:pos + 1] * (257 - count)
        pos += needed
        if len(out) > MAX_MEMBER:
            raise GateError("PDF RunLength stream exceeds extraction limit")
    raise GateError("PDF RunLength stream has no end marker")


def _pdf_lzw(data: bytes, early: int = 1) -> bytes:
    """PDF LZW, including clear/end codes, 9 to 12-bit codes and EarlyChange."""
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    out, bit, width, previous = bytearray(), 0, 9, b""
    while bit + width <= len(data) * 8:
        code = 0
        for offset in range(width):
            pos = bit + offset
            code = (code << 1) | ((data[pos // 8] >> (7 - pos % 8)) & 1)
        bit += width
        if code == 257:
            return bytes(out)
        if code == 256:
            table = table[:258]
            width, previous = 9, b""
            continue
        if code < len(table) and table[code]:
            entry = table[code]
        elif code == len(table) and previous:
            entry = previous + previous[:1]
        else:
            raise GateError("PDF LZW stream has an invalid code")
        out += entry
        if len(out) > MAX_MEMBER:
            raise GateError("PDF LZW stream exceeds extraction limit")
        if previous and len(table) < 4096:
            table.append(previous + entry[:1])
            if width < 12 and len(table) + early == 1 << width:
                width += 1
        previous = entry
    raise GateError("PDF LZW stream has no end marker")


def _pdf_predictor(data: bytes, header: bytes) -> bytes:
    """Undo PNG row prediction after Flate/LZW, keeping extraction limits active."""
    def parameter(name, default):
        match = re.search(rb"/" + name + rb"\s+(\d+)\b", header)
        return int(match[1]) if match else default

    predictor = parameter(b"Predictor", 1)
    if predictor == 1:
        return data
    colors, bits, columns = (parameter(b"Colors", 1), parameter(b"BitsPerComponent", 8),
                             parameter(b"Columns", 1))
    if colors < 1 or columns < 1 or bits not in {1, 2, 4, 8, 16}:
        raise GateError("PDF predictor has invalid row dimensions")
    width = (colors * columns * bits + 7) // 8
    if width > MAX_MEMBER:
        raise GateError("PDF predictor row exceeds extraction limit")
    if not 10 <= predictor <= 15:
        raise GateError("PDF filter predictor is unsupported")
    if len(data) % (width + 1):
        raise GateError("PDF predictor row is truncated")
    stride = max(1, (colors * bits + 7) // 8)
    previous = bytes(width)
    out = bytearray()
    for offset in range(0, len(data), width + 1):
        method = data[offset]
        if method > 4:
            raise GateError("PDF predictor row has an invalid PNG filter")
        row = bytearray(data[offset + 1:offset + 1 + width])
        for i in range(width):
            left = row[i - stride] if i >= stride else 0
            up = previous[i]
            upper_left = previous[i - stride] if i >= stride else 0
            if method == 0:
                prediction = 0
            elif method == 1:
                prediction = left
            elif method == 2:
                prediction = up
            elif method == 3:
                prediction = (left + up) // 2
            else:
                p = left + up - upper_left
                distances = (abs(p - left), abs(p - up), abs(p - upper_left))
                prediction = (left, up, upper_left)[distances.index(min(distances))]
            row[i] = (row[i] + prediction) & 255
        out.extend(row)
        previous = row
    return bytes(out)


def _pdf_filter(data: bytes, name: bytes, header: bytes) -> bytes:
    aliases = {b"Fl": b"FlateDecode", b"A85": b"ASCII85Decode", b"AHx": b"ASCIIHexDecode",
               b"LZW": b"LZWDecode", b"RL": b"RunLengthDecode"}
    name = aliases.get(name, name)
    try:
        if name == b"FlateDecode":
            result = inflate(data)
        elif name == b"ASCII85Decode":
            compact = re.sub(rb"\s", b"", data)
            if not compact.endswith(b"~>"):
                raise GateError("PDF ASCII85 filter has no end marker")
            result = base64.a85decode(compact if compact.startswith(b"<~") else b"<~" + compact,
                                      adobe=True)
        elif name == b"ASCIIHexDecode":
            compact = re.sub(rb"\s", b"", data)
            if not compact.endswith(b">"):
                raise GateError("PDF ASCIIHex filter has no end marker")
            compact = compact[:-1]
            result = bytes.fromhex((compact + (b"0" if len(compact) % 2 else b"")).decode("ascii"))
        elif name == b"LZWDecode":
            early = re.search(rb"/EarlyChange\s+([01])\b", header)
            result = _pdf_lzw(data, int(early[1]) if early else 1)
        elif name == b"RunLengthDecode":
            result = _pdf_runlength(data)
        else:
            raise GateError("PDF stream uses an unsupported filter")
    except (ValueError, zlib.error) as exc:
        raise GateError(f"PDF filter does not decode: {exc.__class__.__name__}") from None
    if len(result) > MAX_MEMBER:
        raise GateError("PDF filter exceeds extraction limit")
    if name in {b"FlateDecode", b"LZWDecode"}:
        result = _pdf_predictor(result, header)
    return result


def pdf_bodies(data: bytes) -> list[bytes]:
    """Decode supported stream filter chains; refuse opaque or malformed streams."""
    bodies, total, consumed = [], 0, 0
    outside, outside_start = bytearray(), 0
    objects = list(re.finditer(rb"\b[0-9]+\s+[0-9]+\s+obj\b", data))
    starts = [m.start() for m in objects]
    for m in PDF_STREAM.finditer(data):
        if m.start() < consumed:
            continue
        obj = bisect.bisect_right(starts, m.start()) - 1
        if obj < 0:
            raise GateError("PDF stream has no object dictionary")
        header = data[objects[obj].end():m.start()].strip()
        if not header.endswith(b">>"):
            raise GateError("PDF stream dictionary is malformed")
        # Decode escaped PDF names before identifying filters and parameters.
        header = re.sub(rb"#[0-9a-fA-F]{2}", lambda x: bytes([int(x[0][1:], 16)]), header)
        length = re.search(rb"/Length\s+(\d+)(?![\d])", header)
        indirect = length and re.match(rb"\s+\d+\s+R\b", header[length.end():])
        if length and not indirect:
            end = m.end() + int(length[1])
            tail = re.match(rb"\s*endstream\b", data[end:])
            if end > len(data) or not tail:
                raise GateError("PDF stream length is invalid")
            consumed = end + tail.end()
        else:
            end = data.find(b"endstream", m.end())
            if end < 0:
                raise GateError("PDF stream has no end marker")
            consumed = end + len(b"endstream")
        body = data[m.end():end]
        # Compressed bytes are data, never PDF literal-string syntax.
        outside.extend(data[outside_start:m.end()])
        outside.extend(b"\n")
        outside_start = end
        filt = re.search(rb"/Filter\s*(\[[^\]]*\]|/[A-Za-z0-9]+)", header, re.S)
        if b"/Filter" in header and not filt:
            raise GateError("PDF uses an unsupported indirect filter")
        if filt:
            names = re.findall(rb"/([A-Za-z0-9]+)", filt[1])
            for name in names:
                body = _pdf_filter(body, name, header)
                total += len(body)
                if total > MAX_TOTAL:
                    raise GateError("PDF streams exceed extraction budget")
        bodies.append(body)
    outside.extend(data[outside_start:])
    return [bytes(outside)] + bodies


def pdf_text(data: bytes) -> str:
    exe = shutil.which("pdftotext")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(data)
        if exe:
            r = subprocess.run([exe, "-q", "-enc", "UTF-8", str(src), "-"], capture_output=True,
                               timeout=300)
            if r.returncode != 0:
                raise GateError(f"pdftotext failed with exit {r.returncode}")
            return r.stdout.decode("utf-8", "replace")
        try:
            import pypdfium2  # type: ignore
        except ImportError:
            raise GateError("a PDF needs pdftotext (poppler) or pypdfium2 to read its page text") from None
        doc = pypdfium2.PdfDocument(str(src))
        try:
            return "\n".join(page.get_textpage().get_text_range() for page in doc)
        finally:
            doc.close()


def pdf_units(data: bytes) -> list[tuple[str, str]]:
    strings, xmp, fonts = [], [], []
    numeric_tables = {"glyf", "gvar", "head", "hhea", "hmtx", "loca", "maxp", "OS/2", "cmap",
                      "fpgm", "prep", "cvt ", "avar", "fvar", "HVAR", "MVAR", "STAT", "gasp",
                      "kern", "vhea", "vmtx", "GDEF", "GPOS", "GSUB"}
    for n, body in enumerate(pdf_bodies(data)):
        if body[:4] in FONT_MAGIC:
            # Font programs contain numeric tables and separately encoded text records.
            # Preserve private checks on numeric bytes without parsing them as PDF strings.
            for label, text in font_units(body):
                if label.removeprefix("#") in numeric_tables or label == "#post-structure":
                    fonts.append((f"#pdf-font-structure-{n}{label}", text))
                else:
                    fonts.append((f"#pdf-font-{n}{label}", text))
            continue
        strings += [_pdf_string(s) for s in pdf_literals(body)]
        for m in PDF_HEX.finditer(body):
            hexs = re.sub(rb"\s", b"", m.group(1))
            if len(hexs) % 2:
                hexs += b"0"
            strings.append(_pdf_string(bytes.fromhex(hexs.decode())))
        xmp += [m.group().decode("utf-8", "replace") for m in XMP.finditer(body)]
    # Separate PDF string records do not create prose across CMap character entries.
    # The rendered page text supplies the complete reading-order view.
    return ([(f"#meta-{i}", text) for i, text in enumerate(strings)]
            + [("#xmp", "\n".join(xmp)), ("#text", pdf_text(data))] + fonts)


def png_units(data: bytes) -> list[tuple[str, str]]:
    units, pos = [], 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        if pos + 12 + length > len(data):
            raise GateError("PNG chunk is truncated")
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        name = ctype.decode("latin-1")
        try:
            if ctype == b"tEXt":
                key, _, txt = body.partition(b"\0")
                units.append((f"#{name}", key.decode("latin-1") + "\n" + txt.decode("latin-1")))
            elif ctype == b"zTXt":
                key, _, rest = body.partition(b"\0")
                units.append((f"#{name}", key.decode("latin-1") + "\n" + inflate(rest[1:]).decode("latin-1")))
            elif ctype == b"iTXt":
                key, _, rest = body.partition(b"\0")
                flag, rest = rest[0], rest[2:]
                lang, _, rest = rest.partition(b"\0")
                tkey, _, txt = rest.partition(b"\0")
                if flag:
                    txt = inflate(txt)
                units.append((f"#{name}", "\n".join(x.decode("utf-8", "replace")
                                                    for x in (key, lang, tkey, txt))))
            elif ctype == b"iCCP":
                key, _, rest = body.partition(b"\0")
                units.append((f"#{name}", key.decode("latin-1") + "\n" + printable_strings(inflate(rest[1:]))))
            elif ctype == b"eXIf":
                units.append((f"#{name}", printable_strings(body)))
        except (zlib.error, IndexError) as exc:
            raise GateError(f"PNG {name} chunk does not decode: {exc}") from None
        if ctype == b"IEND":
            break
    return units


def _text_bytes(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def jpeg_units(data: bytes) -> list[tuple[str, str]]:
    units, pos = [], 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xDA, 0xD9):
            break
        (length,) = struct.unpack(">H", data[pos + 2:pos + 4])
        if length < 2 or pos + 2 + length > len(data):
            raise GateError("JPEG metadata segment is truncated")
        body = data[pos + 4:pos + 2 + length]
        pos += 2 + length
        if marker == 0xFE:
            units.append(("#COM", _text_bytes(body)))
        elif marker == 0xE1 and body.startswith(b"http://ns.adobe.com/xap/1.0/\0"):
            units.append(("#XMP", body.split(b"\0", 1)[1].decode("utf-8", "replace")))
        elif marker == 0xE1:
            units.append(("#EXIF", printable_strings(body)))
        elif 0xE0 <= marker <= 0xEF:
            units.append((f"#APP{marker - 0xE0}", printable_strings(body)))
    return units


def _name_table(t: bytes) -> list[str]:
    _, count, str_off = struct.unpack(">HHH", t[:6])
    out = []
    for i in range(count):
        pid, eid, _, _, length, off = struct.unpack(">6H", t[6 + 12 * i:18 + 12 * i])
        raw = t[str_off + off:str_off + off + length]
        if pid in (0, 3) or (pid == 2 and eid == 1):
            out.append(raw.decode("utf-16-be", "replace"))
        else:
            out.append(raw.decode("mac_roman", "replace"))
    return out


def _font_table(tag: bytes, table: bytes) -> list[tuple[str, str]]:
    """Decode name records and inspect text in every OpenType table."""
    if tag == b"name":
        return [("#name", "\n".join(_name_table(table)))]
    if tag == b"post" and table[:4] == b"\x00\x02\x00\x00":
        # Version 2 has numeric indices followed by Pascal glyph-name records.
        # TAB + A..H in an index array otherwise looks like eight course words.
        (count,) = struct.unpack(">H", table[32:34])
        off = 34 + 2 * count
        if off > len(table):
            raise GateError("post glyph index array is truncated")
        units = [("#post-structure", printable_strings(table[:off]))]
        names = 0
        while off < len(table):
            length = table[off]
            off += 1
            if off + length > len(table):
                raise GateError("post glyph name is truncated")
            names += 1
            units.append((f"#post-name-{names}", _text_bytes(table[off:off + length])))
            off += length
        indices = struct.unpack(f">{count}H", table[34:34 + 2 * count])
        if indices and max(indices) >= 258 + names:
            raise GateError("post glyph name index is out of range")
        return units
    return [("#" + tag.decode("ascii", "replace"), printable_strings(table))]


def _sfnt_units(data: bytes, base: int = 0) -> list[tuple[str, str]]:
    (num,) = struct.unpack(">H", data[base + 4:base + 6])
    units = []
    for i in range(num):
        tag, _, off, length = struct.unpack(">4sIII", data[base + 12 + 16 * i:base + 28 + 16 * i])
        if off + length > len(data):
            raise GateError("font table is truncated")
        units.extend(_font_table(tag, data[off:off + length]))
    return units


def _woff_units(data: bytes) -> list[tuple[str, str]]:
    (num,) = struct.unpack(">H", data[12:14])
    meta_off, meta_len, _, priv_off, priv_len = struct.unpack(">5I", data[24:44])
    units = []
    for i in range(num):
        tag, off, comp, orig, _ = struct.unpack(">4sIIII", data[44 + 20 * i:64 + 20 * i])
        if off + comp > len(data):
            raise GateError("WOFF table is truncated")
        table = data[off:off + comp]
        table = inflate(table) if comp < orig else table
        if len(table) != orig:
            raise GateError("WOFF table length mismatch")
        units.extend(_font_table(tag, table))
    if meta_len:
        units.append(("#woff-meta", inflate(data[meta_off:meta_off + meta_len]).decode("utf-8", "replace")))
    if priv_len:
        units.append(("#woff-private", printable_strings(data[priv_off:priv_off + priv_len])))
    return units


def font_units(data: bytes) -> list[tuple[str, str]]:
    try:
        if data.startswith(b"wOF2"):
            try:
                from fontTools.ttLib import TTFont  # type: ignore
                font = TTFont(io.BytesIO(data))
            except ImportError:
                raise GateError("a WOFF2 font needs fontTools with brotli to read its name table") from None
            try:
                return [unit for tag in font.reader.keys()
                        for unit in _font_table(tag.encode("ascii"), font.getTableData(tag))]
            finally:
                font.close()
        if data.startswith(b"wOFF"):
            return _woff_units(data)
        if data.startswith(b"ttcf"):
            (num,) = struct.unpack(">I", data[8:12])
            units = []
            for i in range(num):
                (off,) = struct.unpack(">I", data[12 + 4 * i:16 + 4 * i])
                units += _sfnt_units(data, off)
            return units
        return _sfnt_units(data)
    except (struct.error, zlib.error, IndexError) as exc:
        raise GateError(f"font table does not parse: {exc}") from None


def decode_text(data: bytes) -> str | None:
    """Return the text of a text file, or None for binary data."""
    for bom, codec in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                       (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            try:
                return data.decode(codec)
            except UnicodeDecodeError:
                return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace") if not re.search(rb"[\x00-\x08\x0e-\x1f]", data[:8192]) else None


JSON_ESCAPE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')
BASE64_BLOB = re.compile(r'(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/-]{17,}={0,2}(?![A-Za-z0-9_+/=-])')
# Escaped control delimiters are separators, including in the undecoded view.
# Mask them at equal length only for email matching so diagnostic positions stay exact.
EMAIL_SEPARATOR = re.compile(r'\\(?:[bfnrt]|x(?:[01][0-9a-fA-F]|20|7[fF])|u00(?:[01][0-9a-fA-F]|20|7[fF]))')


def decoded_view(text: str) -> str:
    """Resolve one round of JSON string, HTML entity and URL escapes."""
    out = JSON_ESCAPE.sub(lambda m: json.loads('"' + m.group() + '"'), text)
    if "&" in out:
        out = html.unescape(out)
    if "%" in out:
        out = urllib.parse.unquote(out)
    return out


def _base64_text(match: re.Match) -> str:
    raw = match.group()
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        text = decoded.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return raw
    # Random binary values and compressed assets use the binary extractors instead.
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in text):
        return raw
    return text


def decoded_views(text: str):
    """Bound recursive decoding to three rounds and MAX_TOTAL decoded bytes.

    Remove format characters at each round, including characters exposed by escapes.
    Keep alternative views so an encoding inside one field cannot hide another field.
    """
    seen, frontier, total = {text}, [text], len(text)
    yield text
    for _ in range(DECODE_DEPTH):
        following = []
        for original in frontier:
            plain = "".join(c for c in original if unicodedata.category(c) != "Cf")
            for view in dict.fromkeys((plain, decoded_view(plain), BASE64_BLOB.sub(_base64_text, plain))):
                view = "".join(c for c in view if unicodedata.category(c) != "Cf")
                if view in seen:
                    continue
                total += len(view)
                if total > MAX_TOTAL:
                    raise GateError("text decoding exceeds the extraction budget")
                seen.add(view)
                following.append(view)
                yield view
        frontier = following
        if not frontier:
            break


def embedded_units(text: str) -> list[tuple[str, bytes | str]]:
    """Data URIs (bytes) and compressed draw.io diagram payloads (text) inside a text file."""
    out: list[tuple[str, bytes | str]] = []
    for k, m in enumerate(DATA_URI.finditer(text), 1):
        try:
            out.append((f"#data-uri-{k}", base64.b64decode(re.sub(r"\s", "", m.group(1)))))
        except (binascii.Error, ValueError):
            raise GateError(f"data URI {k} is not valid base64") from None
    plain = html.unescape(text) if "&lt;diagram" in text else text
    for k, m in enumerate(DRAWIO.finditer(plain), 1):
        try:
            raw = inflate(base64.b64decode(re.sub(r"\s", "", m.group(1))), -15)
        except (binascii.Error, ValueError, zlib.error):
            continue
        out.append((f"#diagram-{k}", urllib.parse.unquote(raw.decode("utf-8", "replace"))))
    return out


def gzip_header(data: bytes) -> tuple[str, int]:
    """Original file name and comment from a gzip header, and the header length."""
    flags, pos, parts = data[3], 10, []
    if flags & 4:
        (xlen,) = struct.unpack("<H", data[pos:pos + 2])
        pos += 2 + xlen
    for bit in (8, 16):
        if flags & bit:
            end = data.index(b"\0", pos)
            parts.append(data[pos:end].decode("latin-1"))
            pos = end + 1
    if flags & 2:
        pos += 2
    return "\n".join(parts), pos


def gzip_members(data: bytes):
    """Yield every member header and payload, bounding total decompression."""
    total, rest = 0, data
    while rest:
        if rest[:2] != b"\x1f\x8b":
            if not rest.strip(b"\0"):
                break
            raise GateError("gzip has unrecognized trailing data")
        header, _ = gzip_header(rest)
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        payload = d.decompress(rest, MAX_MEMBER - total)
        total += len(payload)
        if d.unconsumed_tail or total >= MAX_MEMBER:
            raise GateError(f"gzip data expands beyond {MAX_MEMBER} bytes")
        if not d.eof:
            raise GateError("gzip data is truncated")
        yield header, payload
        rest = d.unused_data


# ---------------------------------------------------------------- the gate


class Gate:
    def __init__(self, rules: Rules, allows: list[Allow], private: list[PrivatePattern],
                 shingles: Shingles | None = None, third_party: list[re.Pattern] | None = None):
        self.rules, self.allows, self.private, self.shingles = rules, allows, private, shingles
        self.third_party = third_party or []
        self.budget = [0, 0]  # bytes extracted, members opened for the current top-level file

    def is_third_party(self, path: str, scope: str = "file") -> bool:
        return scope == "file" and any(g.match(path) for g in self.third_party)

    # -- allowlist
    def allowed(self, pid: str, path: str, matched: str, line: str, scope: str,
                tag: str | None = None, public: bool = True) -> bool:
        if not public and (scope != "file" or not any(
                glob_re(g).match(path) for g in PRIVATE_TAG_PATHS.get(tag, ()))):
            return False
        for a in self.allows:
            if a.scope != scope:
                continue
            if public:
                if pid not in a.patterns and "*" not in a.patterns:
                    continue
            elif not tag or f"private:{tag}" not in a.patterns:
                continue
            if scope == "file" and not any(g.match(path) for g in a.globs):
                continue
            if a.match and not a.match.search(matched):
                continue
            if a.line_match and not a.line_match.search(line):
                continue
            return True
        return False

    # -- text
    def _layers(self, text: str, where: str, path: str, scope: str, min_end: int,
                structural: bool = False) -> list[Hit]:
        starts = [0] + [m.end() for m in re.finditer("\n", text)]

        def locate(pos: int) -> tuple[int, int, str]:
            i = bisect.bisect_right(starts, pos) - 1
            end = starts[i + 1] - 1 if i + 1 < len(starts) else len(text)
            return i + 1, pos - starts[i] + 1, text[starts[i]:end]

        hits: list[Hit] = []
        for p in ([] if structural or self.is_third_party(path, scope) else self.rules.patterns):
            if p.scope == "commit-message" and scope != "commit-message":
                continue
            view = EMAIL_SEPARATOR.sub(lambda m: " " * len(m.group()), text) if p.id == "email" else text
            for m in p.rx.finditer(view):
                if m.end() == m.start() or m.end() <= min_end:
                    continue
                line, col, ltext = locate(m.start())
                if self.allowed(p.id, path, m.group(), ltext, scope):
                    continue
                hits.append(Hit("public", p.id, where, line, col, text[m.start():m.start() + EXCERPT]))
        for pp in self.private:
            done = set()
            for m in pp.rx.finditer(text):
                if m.end() == m.start() or m.end() <= min_end:
                    continue
                line, _, ltext = locate(m.start())
                if line in done or self.allowed(pp.num, path, m.group(), ltext, scope, pp.tag, False):
                    continue
                done.add(line)
                hits.append(Hit("private", pp.num, where, line, 0))
        return hits

    def scan_text(self, text: str, where: str, path: str, scope: str = "file",
                  depth: int = 0, embedded: bool = True, min_end: int = 0) -> list[Hit]:
        """Scan text with every layer, then its decoded view, then embedded payloads.
        Matches that end at or before min_end are skipped (name scans pass the length of
        the parent folders, which were scanned already)."""
        text = text.replace("\r\n", "\n")
        hits, seen, payloads = [], set(), set()
        for view in decoded_views(text):
            for h in self._layers(view, where, path, scope, min_end if view == text else 0):
                key = h.kind, h.pid, h.line
                if key not in seen:
                    seen.add(key)
                    hits.append(h)
            if not embedded:
                continue
            if self.shingles:
                for h in self.shingles.scan(view, where, path,
                                           course_only=self.is_third_party(path, scope)):
                    key = h.kind, h.pid, h.line
                    if key not in seen:
                        seen.add(key)
                        hits.append(h)
            for label, payload in embedded_units(view):
                if (label, payload) in payloads:
                    continue
                payloads.add((label, payload))
                if depth >= MAX_DEPTH:
                    raise GateError(f"{where}: embedded payloads nested deeper than {MAX_DEPTH}")
                if isinstance(payload, bytes):
                    hits += self.scan_bytes(payload, where + label, path, depth + 1)
                else:
                    hits += self.scan_text(payload, where + label, path, scope, depth + 1)
        return hits

    def scan_name(self, full: str, start: int, where: str, path: str) -> list[Hit]:
        """Scan the last component of a path (it starts at index start of full). The parent
        folders stay in the text so lookbehinds see them. Hits print as line 0."""
        hits = [Hit(h.kind, h.pid, where, 0, h.col if h.kind == "public" else 0)
                for h in self.scan_text(full, where, path, embedded=False, min_end=start)]
        if self.shingles:
            name = full[start:]
            for text in decoded_views(name):
                hits += self.shingles.scan(text, where, path, name=True,
                                           course_only=self.is_third_party(path))
        return hits

    def scan_names(self, rels: list[str], where_prefix: str = "", glob_for: str | None = None) -> list[Hit]:
        """File and folder names. A folder is scanned and matched against globs with a
        trailing "/". Inside an archive, where_prefix is "<archive>!" and globs use the archive."""
        hits, seen = [], set()
        for rel in rels:
            parts = rel.rstrip("/").split("/")
            for i in range(len(parts)):
                is_dir = i < len(parts) - 1 or rel.endswith("/")
                prefix = "/".join(parts[:i + 1]) + ("/" if is_dir else "")
                if prefix in seen:
                    continue
                seen.add(prefix)
                start = len("/".join(parts[:i])) + (1 if i else 0)
                hits += self.scan_name(prefix, start, where_prefix + prefix.rstrip("/"),
                                       glob_for if glob_for is not None else prefix)
        return hits

    # -- bytes
    def _spend(self, nbytes: int, where: str) -> None:
        self.budget[0] += nbytes
        self.budget[1] += 1
        if self.budget[0] > MAX_TOTAL or self.budget[1] > MAX_MEMBERS:
            raise GateError(f"{where}: more than {MAX_TOTAL} bytes or {MAX_MEMBERS} members extracted")

    def scan_bytes(self, data: bytes, where: str, path: str, depth: int = 0) -> list[Hit]:
        if depth > MAX_DEPTH:
            raise GateError(f"{where}: archives nested deeper than {MAX_DEPTH}")
        if data.startswith(b"%PDF-") or (b"%PDF-" in data[:1024] and decode_text(data) is None):
            return self._units(pdf_units(data), where, path, depth)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return self._units(png_units(data), where, path, depth)
        if data.startswith(b"\xff\xd8\xff"):
            return self._units(jpeg_units(data), where, path, depth)
        if data[:4] in FONT_MAGIC:
            return self._units(font_units(data), where, path, depth)
        if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            return self._zip(data, where, path, depth)
        if data.startswith(b"\x1f\x8b"):
            hits, joined = [], bytearray()
            try:
                for n, (header, inner) in enumerate(gzip_members(data), 1):
                    self._spend(len(inner), where)
                    if header:
                        hits += self.scan_text(header, where + f"#gzip-header-{n}", path, embedded=False)
                    joined += inner
            except (zlib.error, ValueError, IndexError, struct.error) as exc:
                raise GateError(f"{where}: gzip data does not decode: {exc.__class__.__name__}") from None
            return hits + self.scan_bytes(bytes(joined), where + "!gunzip", path, depth + 1)
        if len(data) > 262 and data[257:262] == b"ustar":
            return self._tar(data, where, path, depth)
        text = decode_text(data)
        if text is not None:
            return self.scan_text(text, where, path, depth=depth)
        return self._units([("#strings", printable_strings(data))], where, path, depth)

    def _units(self, units: list[tuple[str, str]], where: str, path: str, depth: int) -> list[Hit]:
        hits = []
        for label, text in units:
            if text:
                if label.startswith("#pdf-font-structure-") or label == "#post-structure":
                    for view in decoded_views(text):
                        hits += self._layers(view, where + label, path, "file", 0, structural=True)
                        if self.shingles:
                            hits += self.shingles.scan(view, where + label, path, course_only=True)
                    continue
                hits += self.scan_text(text, where + label, path, depth=depth)
        return hits

    def _zip(self, data: bytes, where: str, path: str, depth: int) -> list[Hit]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise GateError(f"{where}: zip archive does not open: {exc}") from None
        hits: list[Hit] = []
        with zf:
            if zf.comment:
                hits += self.scan_text(_text_bytes(zf.comment), where + "#zip-comment", path, embedded=False)
            infos = zf.infolist()
            hits += self.scan_names([i.filename for i in infos], where + "!", path)
            for info in infos:
                member = info.filename.rstrip("/")
                if info.comment:
                    hits += self.scan_text(_text_bytes(info.comment), f"{where}!{member}#comment", path,
                                           embedded=False)
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise GateError(f"{where}!{member}: encrypted zip member")
                if info.file_size > MAX_MEMBER:
                    raise GateError(f"{where}!{member}: member larger than {MAX_MEMBER} bytes")
                self._spend(info.file_size, where)
                try:
                    blob = zf.read(info)
                except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError) as exc:
                    raise GateError(f"{where}!{member}: does not decompress: {exc}") from None
                hits += self.scan_bytes(blob, f"{where}!{member}", path, depth + 1)
        return hits

    def _tar(self, data: bytes, where: str, path: str, depth: int) -> list[Hit]:
        hits: list[Hit] = []
        try:
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                infos = tf.getmembers()
                hits += self.scan_names([i.name + ("/" if i.isdir() else "") for i in infos],
                                        where + "!", path)
                for info in infos:
                    member = info.name.rstrip("/")
                    meta = [info.uname, info.gname, info.linkname] + [
                        f"{k}={v}" for k, v in sorted(info.pax_headers.items())]
                    meta_text = "\n".join(x for x in meta if x)
                    if meta_text:
                        hits += self.scan_text(meta_text, f"{where}!{member}#tar-header", path,
                                               embedded=False)
                    if not info.isfile():
                        continue
                    if info.size > MAX_MEMBER:
                        raise GateError(f"{where}!{member}: member larger than {MAX_MEMBER} bytes")
                    self._spend(info.size, where)
                    f = tf.extractfile(info)
                    if f is not None:
                        hits += self.scan_bytes(f.read(), f"{where}!{member}", path, depth + 1)
        except tarfile.TarError as exc:
            raise GateError(f"{where}: tar archive does not open: {exc}") from None
        return hits

    # -- files
    def scan_blob(self, data: bytes, where: str, path: str) -> list[Hit]:
        """Scan one top-level file or blob; errors name it."""
        self.budget = [0, 0]
        try:
            return self.scan_bytes(data, where, path)
        except GateError as exc:
            msg = str(exc)
            raise GateError(msg if msg.startswith(where) else f"{where}: {msg}") from None


# ---------------------------------------------------------------- file sets


def git(repo: Path, *args: str, binary: bool = False, check: bool = True,
        stdin: bytes | None = None) -> subprocess.CompletedProcess:
    if not shutil.which("git"):
        raise GateError("git is not on PATH")
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=stdin,
                       text=not binary and stdin is None,
                       encoding="utf-8" if not binary and stdin is None else None)
    if check and r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else r.stderr.decode("utf-8", "replace")
        raise GateError(f"git {args[0]} failed: {err.strip()[:200]}")
    return r


def git_files(repo: Path, untracked: bool) -> list[str]:
    args = ["ls-files", "-z", "--cached"] + (["--others", "--exclude-standard"] if untracked else [])
    out = git(repo, *args, binary=True).stdout.decode("utf-8", "surrogateescape")
    return sorted({p for p in out.split("\0") if p})


def read_file(f: Path, rel: str) -> bytes:
    """Bytes git would store: the link text for a symlink, else the file contents."""
    try:
        if f.is_symlink():
            return os.fsencode(os.readlink(f))
        return f.read_bytes()
    except OSError as exc:
        raise GateError(f"cannot read {rel}: {exc.strerror}") from None


def _walk_error(exc: OSError) -> None:
    raise GateError(f"cannot list {exc.filename}: {exc.strerror}")


def walk_paths(paths: list[str], repo: Path) -> list[tuple[Path, str]]:
    """Expand --paths into (file, display path). Names are relative to the repo when the
    path is inside it, else relative to the argument's parent folder."""
    out: list[tuple[Path, str]] = []
    directory_files: set[Path] = set()
    for arg in paths:
        p = Path(arg)
        if not p.exists() and not p.is_symlink():
            raise GateError(f"no such file or folder: {arg}")
        absolute = p.parent.resolve() / p.name if p.is_symlink() else p.resolve()
        try:
            base = repo.resolve()
            absolute.relative_to(base)
        except ValueError:
            base = absolute.parent
        if absolute.is_symlink() or absolute.is_file():
            out.append((absolute, absolute.relative_to(base).as_posix()))
            continue
        for root, dirs, files in os.walk(absolute, onerror=_walk_error):
            dirs[:] = sorted(d for d in dirs if d not in WALK_SKIP)
            for name in sorted(files):
                f = Path(root) / name
                out.append((f, f.relative_to(base).as_posix()))
                if f.is_relative_to(repo.resolve()):
                    directory_files.add(f)
    if directory_files and (repo / ".git").exists():
        names = [f.relative_to(repo.resolve()).as_posix() for f in sorted(directory_files)]
        result = git(repo, "check-ignore", "-z", "--stdin", binary=True, check=False,
                     stdin=("\0".join(names) + "\0").encode("utf-8", "surrogateescape"))
        if result.returncode not in (0, 1):
            raise GateError("git check-ignore failed")
        ignored = set(result.stdout.decode("utf-8", "surrogateescape").split("\0"))
        out = [(f, rel) for f, rel in out if f not in directory_files or rel not in ignored]
    return out


def blob_id(data: bytes, algo: str) -> str:
    return hashlib.new(algo, b"blob %d\0" % len(data) + data).hexdigest()


# ---------------------------------------------------------------- history


def cat_blobs(repo: Path, shas: list[str]):
    """Yield (sha, bytes) for each blob id, through one git cat-file --batch process."""
    if not shas:
        return
    out = git(repo, "cat-file", "--batch", binary=True, stdin=("\n".join(shas) + "\n").encode()).stdout
    pos = 0
    for sha in shas:
        nl = out.index(b"\n", pos)
        head = out[pos:nl].decode().split()
        if len(head) != 3 or head[1] != "blob":
            raise GateError(f"git cat-file: unexpected reply for {sha[:12]}")
        size = int(head[2])
        yield sha, out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1


def scan_history(gate: Gate, repo: Path, scanned: set[tuple[str, str]], *,
                 public_history: bool = False, initial_release_ref: str = "v0.1.0") -> tuple[list[Hit], int]:
    """Commit identity, offsets and messages, and every (blob, path) pair in any commit that
    the working-tree scan did not already cover with identical bytes."""
    shas = git(repo, "rev-list", "--all").stdout.split()
    if not shas:
        print("privacy gate: no commits; --history has nothing to check", file=sys.stderr)
        return [], 0
    strict = set(shas)
    if public_history:
        baseline = git(repo, "rev-parse", "--verify", "--end-of-options",
                       initial_release_ref + "^{commit}", check=False)
        if baseline.returncode == 0:
            oid = baseline.stdout.strip()
            for sha in shas:
                if sha == oid:
                    continue
                ancestry = git(repo, "merge-base", "--is-ancestor", oid, sha, check=False)
                if ancestry.returncode not in (0, 1):
                    raise GateError("cannot establish initial-release ancestry")
                if ancestry.returncode == 0:
                    strict.remove(sha)
        else:
            print("privacy gate: initial release ref absent; all history stays strict", file=sys.stderr)
    ident = gate.rules.identity
    name, email, offset = ident.get("name"), ident.get("email"), ident.get("offset", "+0000")
    if not name or not email:
        raise GateError("patterns.toml [identity] needs name and email")
    fmt = "%H%x00%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd%x00%B%x1e"
    raw = git(repo, "log", "--all", "--date=raw", f"--format={fmt}", binary=True).stdout
    hits: list[Hit] = []
    commits = 0
    for rec in raw.decode("utf-8", "replace").split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        sha, an, ae, ad, cn, ce, cd, msg = rec.split("\0", 7)
        commits += 1
        where = f"commit:{sha[:12]}"
        if sha in strict and (an, ae) != (name, email):
            hits.append(Hit("public", "commit-author", where, 0, 0))
        if sha in strict and (cn, ce) != (name, email):
            hits.append(Hit("public", "commit-committer", where, 0, 0))
        if sha in strict and (not ad.endswith(" " + offset) or not cd.endswith(" " + offset)):
            hits.append(Hit("public", "commit-offset", where, 0, 0))
        trailers = git(repo, "interpret-trailers", "--parse", binary=True, stdin=msg.encode()).stdout
        parsed = trailers.decode("utf-8", "replace").splitlines()
        scoped = gate
        rejected = bool(parsed) and sha in strict
        if sha not in strict:
            dco_allows = []
            for trailer in parsed:
                key, _, value = trailer.partition(":")
                identity = re.fullmatch(r"\s*[^<>\n]+ <([^<>\s]+@[^<>\s]+)>\s*", value)
                if key.lower() != "signed-off-by" or not identity:
                    rejected = True
                    continue
                address = re.escape(identity[1])
                dco_allows.append(Allow([], {"email"}, "Public contributor DCO identity",
                    match=re.compile("^" + address + "$"),
                    line_match=re.compile(r"^Signed-off-by[ \t]*:[ \t]*[^<>\n]+ <" + address + r">[ \t]*$",
                                          re.IGNORECASE), scope="commit-message"))
            scoped = Gate(gate.rules, gate.allows + dco_allows, gate.private, gate.shingles,
                          gate.third_party)
        hits += scoped.scan_text(msg, where, "", scope="commit-message")
        if rejected:
            hits.append(Hit("public", "commit-trailer", where, 1, 1))
    if commits != len(shas):
        raise GateError(f"git log listed {commits} commits, rev-list {len(shas)}")
    pairs: dict[str, set[str]] = {}
    for sha in shas:
        listing = git(repo, "ls-tree", "-r", "-z", "--full-tree", sha, binary=True).stdout
        for entry in listing.decode("utf-8", "surrogateescape").split("\0"):
            if not entry:
                continue
            meta, _, p = entry.partition("\t")
            _, otype, oid = meta.split()
            if otype == "blob":
                pairs.setdefault(oid, set()).add(p)
    todo = sorted(oid for oid, ps in pairs.items() if any((oid, p) not in scanned for p in ps))
    for oid, data in cat_blobs(repo, todo):
        for p in sorted(pairs[oid]):
            if (oid, p) not in scanned:
                hits += gate.scan_blob(data, f"{p}@{oid[:12]}", p)
    hits += gate.scan_names(sorted({p for ps in pairs.values() for p in ps}))
    return hits, commits


# ---------------------------------------------------------------- build-shingles

JS_STRINGS = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`|//[^\n]*|/\*.*?\*/',
                        re.S)
JSON_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
JSON_COLON = re.compile(r"\s*:")
PY_PREFIX = re.compile(r"^[rRbBuUfF]{1,2}(?=['\"])")
SECTIONS = ("course", "generated", "prose", "benign", "skip")
SOURCE_RANGES = re.compile(r"^(?P<glob>.+?):(?P<ranges>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)$")


def _line_starts(text: str) -> list[int]:
    return [0] + [m.end() for m in re.finditer("\n", text)]


def _json_segments(text: str) -> list[tuple[int, str]]:
    """String values of JSON text with their line numbers; object keys are dropped."""
    import json
    starts = _line_starts(text)
    out = []
    for m in JSON_STRING.finditer(text):
        if JSON_COLON.match(text, m.end()):
            continue
        try:
            value = json.loads(m.group())
        except ValueError:
            value = m.group()[1:-1]
        out.append((bisect.bisect_right(starts, m.start()), value))
    return out


def source_segments(file: Path, data: bytes) -> list[tuple[int, str]]:
    """(first line, text) segments of one source file: string values for JSON, string
    literals and comments for Python and JavaScript, extracted text for PDFs, the whole
    text for anything else. Executable code tokens never enter the index."""
    suffix = file.suffix.lower()
    text = decode_text(data)
    if text is None:
        return [(1, pdf_text(data))] if data.startswith(b"%PDF-") or b"%PDF-" in data[:1024] else []
    text = text.replace("\r\n", "\n")
    if suffix in (".json", ".jsonl"):
        return _json_segments(text)
    if suffix == ".py":
        kinds = {tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", -1)}
        out = []
        try:
            for t in tokenize.generate_tokens(io.StringIO(text).readline):
                if t.type in kinds:
                    s = PY_PREFIX.sub("", t.string) if t.type == tokenize.STRING else t.string
                    out.append((t.start[0], s))
        except (tokenize.TokenError, SyntaxError):
            return [(1, text)]
        return out
    if suffix in (".js", ".mjs", ".cjs", ".ts"):
        starts = _line_starts(text)
        return [(bisect.bisect_right(starts, m.start()), m.group()) for m in JS_STRINGS.finditer(text)]
    # Prose may hold pasted JSON: drop key names so no window is made of structure alone.
    return [(1, JSON_KEY.sub(" ", text))]


@dataclass
class SourceRule:
    section: str
    glob: re.Pattern
    ranges: list[tuple[int, int]] | None = None


def parse_sources(text: str) -> list[SourceRule]:
    """Sources file. "[course]", "[generated]", "[prose]", "[benign]" and "[skip]" start
    sections (lines before the first header are course). "generated" marks course files
    that embed release code (per-run scripts): their windows count as course after the
    benign windows are removed. Prefixes "-" (benign), "!" (skip) and "~" (prose) override
    the section. A course glob may end in ":12-30,45" to take only those lines. "#" starts
    a comment line."""
    rules: list[SourceRule] = []
    section = "course"
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = re.fullmatch(r"\[([a-z]+)\]", line)
        if head:
            if head[1] not in SECTIONS:
                raise GateError(f"shingle sources line {n}: unknown section [{head[1]}]")
            section = head[1]
            continue
        kind = section
        if line[0] in "-!~":
            kind = {"-": "benign", "!": "skip", "~": "prose"}[line[0]]
            line = line[1:].strip()
        ranges = None
        m = SOURCE_RANGES.match(line)
        if m:
            if kind != "course":
                raise GateError(f"shingle sources line {n}: line ranges apply to course sources only")
            line, ranges = m["glob"], []
            for part in m["ranges"].split(","):
                a, _, b = part.partition("-")
                lo, hi = int(a), int(b or a)
                if not 1 <= lo <= hi:
                    raise GateError(f"shingle sources line {n}: bad line range {part}")
                ranges.append((lo, hi))
        if not line:
            raise GateError(f"shingle sources line {n}: empty glob")
        rules.append(SourceRule(kind, glob_re(line), ranges))
    if not any(r.section in ("course", "generated") for r in rules):
        raise GateError("the shingle sources file names no course or generated source")
    return rules


def file_plan(rel: str, rules: list[SourceRule]):
    """Return line number -> class for one file, or None when the file is skipped or
    matches no rule. Classes: "course" (whole-file course rule), "generated", "ranged"
    (lines a course range names), "prose", "benign" or None. A whole-file course rule
    wins, then generated; other lines take course ranges, then prose, then benign."""
    hit = [r for r in rules if r.glob.match(rel)]
    if not hit or any(r.section == "skip" for r in hit):
        return None
    if any(r.section == "course" and r.ranges is None for r in hit):
        return lambda n: "course"
    if any(r.section == "generated" for r in hit):
        return lambda n: "generated"
    ranges = [rg for r in hit if r.section == "course" and r.ranges for rg in r.ranges]
    rest = next((s for s in ("prose", "benign") if any(r.section == s for r in hit)), None)
    if not ranges and rest is None:
        return None
    return lambda n: "ranged" if any(lo <= n <= hi for lo, hi in ranges) else rest


def split_by_class(segments: list[tuple[int, str]], line_class) -> list[tuple[str, int, str]]:
    """Cut segments where the line class changes: (class, first line, text) runs."""
    out = []
    for start, text in segments:
        cur, first, buf = None, start, []
        for i, line in enumerate(text.split("\n")):
            c = line_class(start + i)
            if c != cur:
                if cur and buf:
                    out.append((cur, first, "\n".join(buf)))
                cur, first, buf = c, start + i, []
            buf.append(line)
        if cur and buf:
            out.append((cur, first, "\n".join(buf)))
    return out


def build_shingles(src_root: Path, out_dir: Path, sources: Path, patterns: Path = PATTERNS_FILE) -> int:
    """Build the index from the sources file.

    course        = whole-file course windows
                  + generated windows that no benign file contains (per-run scripts embed
                    release code; that code is not course text)
                  + every window explicitly designated by a course line range
    prose-flagged = prose windows whose source span matched a layer 1 or 2 pattern
    prose         = other prose windows that no benign file contains
    """
    if not src_root.is_dir():
        raise GateError(f"--from is not a folder: {src_root}")
    try:
        rules = parse_sources(sources.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GateError(f"cannot read the shingle sources file: {exc.strerror}") from None
    out_dir.mkdir(parents=True, exist_ok=True)
    salt_file = out_dir / SALT_FILE
    if not salt_file.exists():
        fd = os.open(salt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(os.urandom(32).hex() + "\n")
    salt = read_salt(out_dir)
    private, _ = load_private(out_dir)
    flaggers = [p.rx for p in load_rules(patterns).patterns if p.scope == "all"] + [p.rx for p in private]
    sets: dict[str, set[int]] = {c: set() for c in ("course", "generated", "prose-flagged", "prose", "benign")}
    ranged: set[int] = set()
    files = {"course": 0, "generated": 0, "ranged": 0, "prose": 0, "benign": 0}
    for root, dirs, names in os.walk(src_root, onerror=_walk_error):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(names):
            f = Path(root) / name
            rel = f.relative_to(src_root).as_posix()
            plan = None if f.is_symlink() else file_plan(rel, rules)
            if plan is None:
                continue
            try:
                data = f.read_bytes()
            except OSError as exc:
                raise GateError(f"cannot read source {rel}: {exc.strerror}") from None
            try:
                parts = [p for p in split_by_class(source_segments(f, data), plan) if p[0] != "benign"]
            except GateError as exc:
                raise GateError(f"source {rel}: {exc}") from None
            text = decode_text(data)
            if text is not None:
                whole = text.replace("\r\n", "\n")
                benign_lines = [p for p in split_by_class([(1, whole)], plan) if p[0] == "benign"]
                if benign_lines:
                    # Exact benign windows affect generated data and prose only.
                    parts.append(("benign", 1, whole))
            for cls in {p[0] for p in parts}:
                files[cls] += 1
            for cls, start, seg in parts:
                toks = tokens(seg, start, flaggers if cls == "prose" else None)
                for i in range(len(toks) - SHINGLE_N + 1):
                    win = toks[i:i + SHINGLE_N]
                    window = [t[0] for t in win]
                    if not informative_window(window):
                        continue
                    h = shingle_hash(window, salt)
                    if cls == "ranged":
                        ranged.add(h)
                    elif cls == "prose" and any(t[2] for t in win):
                        sets["prose-flagged"].add(h)
                    else:
                        sets[cls].add(h)
    kept = ranged
    course = sets["course"] | (sets["generated"] - sets["benign"]) | kept
    flagged = sets["prose-flagged"] - course
    prose = sets["prose"] - sets["benign"] - course - flagged
    write_index(out_dir / INDEX_FILE, {"course": course, "prose-flagged": flagged, "prose": prose})
    print(f"build-shingles: course {len(course)} windows ({files['course']} course files, "
          f"{files['generated']} generated files, {len(kept)} of {len(ranged)} windows from course "
          f"ranges in {files['ranged']} files); prose-flagged {len(flagged)} and prose {len(prose)} "
          f"from {files['prose']} prose files; {files['benign']} benign files; "
          f"index holds {len(course) + len(flagged) + len(prose)}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- main


def staged_blobs(repo: Path, selected: list[str] | None = None) -> dict[str, bytes]:
    """Read selected index blobs once per object, independently of working bytes."""
    choices = []
    for name in selected or []:
        p = Path(name)
        absolute = p.parent.resolve() / p.name
        choices.append((absolute, p.is_dir() and not p.is_symlink()))
    staged = git(repo, "ls-files", "--stage", "-z", binary=True).stdout
    by_oid: dict[str, list[str]] = {}
    for entry in staged.decode("utf-8", "surrogateescape").split("\0"):
        if not entry:
            continue
        meta, rel = entry.split("\t", 1)
        if selected is not None and not any(repo / rel == p or (directory and (repo / rel).is_relative_to(p))
                                            for p, directory in choices):
            continue
        mode, oid, stage = meta.split()
        if mode == "160000":
            raise GateError(f"{rel}: submodule contents cannot be scanned")
        if stage != "0":
            raise GateError(f"{rel}: unresolved index stages")
        by_oid.setdefault(oid, []).append(rel)
    blobs = {}
    for oid, data in cat_blobs(repo, sorted(by_oid)):
        for rel in by_oid[oid]:
            blobs[rel] = data
    return blobs


def run_scan(args: argparse.Namespace) -> int:
    if args.excerpt and "CI" in os.environ:
        raise GateError("--excerpt is refused when CI is set")
    if args.public_history:
        if args.require_private or args.shingles or args.private_dir:
            raise GateError("--public-history cannot be combined with private-layer options")
        args.history = True
    rules = load_rules(Path(args.patterns))
    allows = load_allowlist(Path(args.allowlist), rules)
    private_dir = Path(args.private_dir) if args.private_dir else default_private_dir()
    private, loaded = ([], []) if args.public_history else load_private(private_dir)
    if args.require_private and not loaded:
        raise GateError(f"--require-private: no private pattern loaded ({PRIVATE_FILE} in the private "
                        f"dir or {ENV_B64}); set {ENV_DIR} or the secret")
    if not loaded:
        print("privacy gate: private layer not loaded; public patterns only", file=sys.stderr)
    shingles = Shingles(Path(args.shingles)) if args.shingles else None
    repo = Path(args.repo).resolve() if args.repo else REPO
    gate = Gate(rules, allows, private, shingles, load_third_party(repo))

    if args.paths:
        files = walk_paths(args.paths, repo)
        index_blobs = staged_blobs(repo, args.paths) if (repo / ".git").exists() else {}
    else:
        index_blobs = staged_blobs(repo)
        files = []
        for rel in git_files(repo, untracked=args.all):
            f = repo / rel
            if f.is_symlink() or f.is_file():
                if rel not in index_blobs or read_file(f, rel) != index_blobs[rel]:
                    files.append((f, rel))
    algo = "sha1"
    if args.history:
        algo = git(repo, "rev-parse", "--show-object-format", check=False).stdout.strip() or "sha1"
    hits: list[Hit] = []
    scanned: set[tuple[str, str]] = set()
    working_hashes: dict[str, bytes] = {}
    for f, rel in files:
        data = read_file(f, rel)
        working_hashes[rel] = hashlib.sha256(data).digest()
        hits += gate.scan_blob(data, rel, rel)
        if args.history:
            scanned.add((blob_id(data, algo), rel))
    working_paths = {rel for _, rel in files}
    for rel, data in sorted(index_blobs.items()):
        where = rel + "@index" if rel in working_paths else rel
        if working_hashes.get(rel) != hashlib.sha256(data).digest():
            hits += gate.scan_blob(data, where, rel)
        if args.history:
            scanned.add((blob_id(data, algo), rel))
    hits += gate.scan_names([rel for _, rel in files] + sorted(index_blobs))
    commits = 0
    if args.history:
        h, commits = scan_history(gate, repo, scanned, public_history=args.public_history,
                                  initial_release_ref=args.initial_release_ref)
        hits += h
    hits = sorted(set(hits), key=lambda h: (h.where, h.line, h.col, h.kind, h.pid))
    for h in hits:
        print(h.render(args.excerpt))
    layers = ["public"] + (["private"] if loaded else []) + (["shingles"] if shingles else [])
    detail = ""
    if shingles:
        blocking = {(h.where, h.line) for h in hits if h.kind == "shingle"}
        reviews = sorted({r for r in shingles.reviews if (r.where, r.line) not in blocking},
                         key=lambda h: (h.where, h.line, h.pid))
        for r in reviews[:args.review_limit]:
            _say("review " + r.render())
        if len(reviews) > args.review_limit:
            _say(f"review: {len(reviews) - args.review_limit} more not listed (--review-limit)")
        per = {c: sum(1 for h in hits if h.kind == "shingle" and h.note == c) for c in sorted(BLOCKING)}
        detail = (f"; shingle hits: {', '.join(f'{c} {n}' for c, n in per.items())}; "
                  f"{len(reviews)} prose review items")
    print(f"privacy gate: {len(files) + len(index_blobs)} files, {commits} commits, {len(hits)} hits "
          f"(layers: {', '.join(layers)}){detail}", file=sys.stderr)
    return 1 if hits else 0


def main(argv: list[str] | None = None) -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["build-shingles"]:
            ap = argparse.ArgumentParser(prog="privacy_gate.py build-shingles",
                                         description="Build the salted shingle index from private sources.")
            ap.add_argument("--from", dest="src", required=True, help="folder the source globs are relative to")
            ap.add_argument("--out", required=True, help=f"private dir: {INDEX_FILE} and {SALT_FILE} go here")
            ap.add_argument("--sources", help=f"globs file (default: <out>/{SOURCES_FILE})")
            ap.add_argument("--patterns", default=str(PATTERNS_FILE), help=argparse.SUPPRESS)
            try:
                a = ap.parse_args(argv[1:])
            except SystemExit as exc:
                return 0 if exc.code == 0 else 2
            output = Path(a.out)
            out = output.parent if output.suffix == ".bin" else output
            result = build_shingles(Path(a.src), out,
                                    Path(a.sources) if a.sources else out / SOURCES_FILE, Path(a.patterns))
            if output.suffix == ".bin" and output.name != INDEX_FILE:
                os.replace(out / INDEX_FILE, output)
            return result
        ap = argparse.ArgumentParser(prog="privacy_gate.py", description=__doc__.split("\n\n")[0])
        mode = ap.add_mutually_exclusive_group()
        mode.add_argument("--paths", nargs="+", metavar="PATH", help="scan these files or folders")
        mode.add_argument("--all", action="store_true",
                          help="scan tracked plus untracked files that git does not ignore")
        ap.add_argument("--history", action="store_true",
                        help="also check every commit: identity, offsets, messages, old blobs")
        ap.add_argument("--public-history", action="store_true",
                        help="public-only history scan; permit contributor identities and DCO after the initial release")
        ap.add_argument("--initial-release-ref", default="v0.1.0",
                        help="strict initial-history baseline for --public-history (default v0.1.0)")
        ap.add_argument("--require-private", action="store_true",
                        help="exit 2 when the private layer does not load")
        ap.add_argument("--shingles", metavar="INDEX", help="shingle index (salt and allowlist beside it)")
        ap.add_argument("--review-limit", type=int, default=200, metavar="N",
                        help="list at most N prose review items on stderr (default 200)")
        ap.add_argument("--excerpt", action="store_true",
                        help="print 60 characters of each public hit (local runs only, never in CI)")
        ap.add_argument("--repo", help="repository root (default: this tool's repository)")
        ap.add_argument("--patterns", default=str(PATTERNS_FILE), help=argparse.SUPPRESS)
        ap.add_argument("--allowlist", default=str(ALLOWLIST_FILE), help=argparse.SUPPRESS)
        ap.add_argument("--private-dir", help=f"private dir (default: ${ENV_DIR}, else the folder "
                                              "beside the repo named <repo>-private)")
        try:
            args = ap.parse_args(argv)
        except SystemExit as exc:
            return 0 if exc.code == 0 else 2
        return run_scan(args)
    except GateError as exc:
        _say(f"privacy gate: error: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - a crash must never read as a pass or a hit
        _say(f"privacy gate: crashed: {exc.__class__.__name__}: {exc}")
        return 2


def _say(msg: str) -> None:
    try:
        print(msg, file=sys.stderr)
    except Exception:  # noqa: BLE001 - reporting must not change the exit code
        pass


if __name__ == "__main__":
    sys.exit(main())
