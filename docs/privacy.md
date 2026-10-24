<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Release privacy checks, local evidence, and failure handling. -->

# Privacy gate

Run `tools/privacy_gate.py` before sharing source files, generated documents, or release archives.
The gate reads local files and Git objects. It sends no content over the network.
It does not change scanned files. A clean result covers the selected files and loaded layers.

```sh
python3 tools/privacy_gate.py --all --history --require-private --shingles "$LT_PRIVACY_DIR/shingles.bin"
python3 tools/privacy_gate.py --paths skills/lt-module-capture --require-private --shingles "$LT_PRIVACY_DIR/shingles.bin"
```

Python 3.11 or newer is required. PDF text extraction needs `pdftotext` from Poppler,
or the optional `pypdfium2` package. Compressed WOFF2 fonts need `fontTools` with Brotli.
Ordinary font name tables, PNG chunks, JPEG metadata, and archive members use Python's standard library.

## Scope and results

The default scope checks both staged blobs and working files from `git ls-files`.
`--all` adds untracked files that Git does not ignore. Differing staged views carry `@index`.
`--paths` accepts individual files or directories, including files outside the repository.
Directory scans omit Git-ignored outputs and common cache directories.
Use an explicit file argument to inspect an omitted file.
Repository paths selected with `--paths` also include their staged blobs.
File and directory names are checked. Symbolic links contribute their target text without following the link.

Text checks decode JSON string escapes, base64 and base64url blobs longer than 16 characters,
HTML entities and percent escapes through three rounds. Each view also removes Unicode format characters.
Binary checks include PDF Info, XMP and page text; PNG text chunks; JPEG EXIF, XMP and comments;
font name records, metadata and printable text from every font table;
SVG text; and nested ZIP, TAR and gzip members. Unknown binaries contribute printable strings.
Embedded data URIs and compressed diagram payloads are inspected recursively.
Every gzip member contributes its header and payload.
PDF stream chains support Flate, ASCII85, ASCIIHex, LZW and RunLength decoding.
Unsupported PDF filters and predictors cause exit 2.
The gate refuses encrypted ZIP members and extraction limits instead of declaring them clean.

`REUSE.toml` override annotations that name an upstream licence select third-party files.
The blanket project annotation and overrides carrying only the project licence retain every layer.
Third-party files use private patterns and `course` shingles. Public generic patterns and owner-prose
shingles do not apply to upstream metadata. The same selection covers generated copies declared
in REUSE, decoded payloads, names and historical blobs. This selection adds no allowlist entry.
Release checks must still load the private layer and course index.

OpenType `post` version 2 glyph indices are numeric structure. Each Pascal glyph-name record
receives its own text scan, so neighboring glyph identifiers cannot form a course sentence.
Private patterns and course shingles still inspect the numeric structure.
Other font tables retain their text scans.
The email rule treats escaped control characters as separators, retaining diagnostic positions.
An approved address following a separator remains approved; other addresses still block.

`--history` checks every reachable commit's author, committer, UTC offsets and message.
It requires the release identity configured in `tools/privacy/patterns.toml`.
Initial release commits carry no trailers. Historical blobs and names are checked,
including files removed from the current working tree.

Public pull-request CI uses `--public-history`, which implies `--history` and reads no private inputs.
The command ignores private-layer environment variables and refuses `--require-private`,
`--private-dir` and `--shingles`. Public patterns still scan all selected content and history blobs.
`--initial-release-ref v0.1.0` selects the default trusted baseline.
Its ancestors retain the strict release identity, UTC and trailer rules.
Descendant commits may use contributor identities, timezones and `Signed-off-by` trailers.
Only a valid DCO trailer receives its contributor-email exception; the same address elsewhere blocks.
Other trailers and agent-session links still block. Unrelated histories remain strict.
If the baseline is absent, every commit remains strict until the initial release exists.

```sh
python3 tools/privacy_gate.py --all --public-history --initial-release-ref v0.1.0
```

| Exit | Meaning |
| --- | --- |
| 0 | No unallowed hits in the selected scope. |
| 1 | One or more privacy hits. |
| 2 | Invalid arguments, unreadable inputs, missing extractors, corrupt data, limits, or another gate error. |

Public findings print `path:line:column pattern-id`.
Private findings print `private-N path:line`.
Shingle findings print `shingle-HASH8 path:line class`; prose review items go to standard error.
Line zero denotes a filename. Archive members use `archive!member`;
decoded binary views use suffixes such as `#meta`, `#text` and `#name`.
The summary on standard error names the loaded layers and scanned file count.

Output omits matching text. `--excerpt` adds up to 60 characters for public findings only.
Use that option locally. A set `CI` environment variable makes `--excerpt` fail with exit 2,
including empty and false-like values. The refusal happens before scanning or printing findings.

## Public and private patterns

`tools/privacy/patterns.toml` contains generic identifier shapes and release history rules.
`tools/privacy/allowlist.toml` contains scoped exceptions with a required reason.
Each exception selects a path glob and pattern identifier. Optional `match`, `line_match`
and `scope` fields constrain the matching text, whole line and file or commit-message context.
`**` spans folders; `*` stays inside a folder.

Pattern definitions and their tests have a public-pattern exception inside their own folders.
That exception never suppresses private patterns or shingles.
Example institutions use narrow private tags in the documented example paths.
Private tags have fixed path restrictions enforced during loading and matching.
`example-institution` permits the institution example described in [configuration](configuration.md) and that configuration page.
`example-format` permits only the designated institution example folder. Entries can narrow these sets.
Unknown tags and broader path globs cause exit 2.
Numbered private findings cannot receive public allowlist entries.

Keep private files outside the checkout. Set `LT_PRIVACY_DIR` to their folder.
The default is a sibling directory named after the checkout with `-private` appended.
`private-patterns.txt` contains one Python regular expression per line.
Blank lines and comment lines are ignored. Optional `[tag]` prefixes identify approved example exceptions.
Patterns use multiline matching. Add `(?i)` when a pattern needs case-insensitive matching.

CI may load the same UTF-8 text from the base64-encoded `PRIVACY_PATTERNS_B64` secret.
File and environment patterns are combined. Invalid patterns and malformed secrets cause exit 2.
`--require-private` requires at least one loaded private pattern.
Without that flag, a missing private layer produces a notice and public-only checks can continue.
Fork CI without the secret needs a maintainer's local private-layer review before merging.

## Course-text shingles

The private index stores salted 64-bit BLAKE2b hashes of eight-word windows.
Normalization folds case, resolves escapes, removes punctuation, and splits words.
Salt and hashes stay outside the checkout. The index contains no plaintext course material.
Keep `shingles.bin`, `shingle-salt`, `shingle-sources.txt` and `shingle-allowlist.toml` together.

Every window carries one of three classes:

| Class | Source | Effect |
| --- | --- | --- |
| `course` | course text, submitted work and case content | Blocks: exit 1, printed as `shingle-HASH8 path:line course`. |
| `prose-flagged` | owner prose whose source window covered a layer 1 or layer 2 match | Blocks, printed with class `prose-flagged`. |
| `prose` | other owner prose (notes, briefs, run reviews) | A review item on standard error: `review shingle-HASH8 path:line prose`. The exit code does not change. |

The summary line counts blocking hits per class and the review items.
`--review-limit N` caps the listed review items (default 200); the count stays complete.
A `prose-flagged` window catches a copied sentence even when the copy dropped the punctuation
that the layer 1 or layer 2 pattern needs, for example a date or an identifier written with spaces.

```sh
python3 tools/privacy_gate.py build-shingles --from "$PRIVATE_SOURCE" \
  --out "$LT_PRIVACY_DIR/shingles.bin" --sources "$LT_PRIVACY_DIR/shingle-sources.txt"
```

`--out` may name the index file or its folder. The builder creates the salt when it is absent
and reads the private patterns from the same folder to mark `prose-flagged` windows.
The summary on standard error counts windows per class.

The sources file holds globs relative to `--from`, grouped by section headers:

| Section | Meaning |
| --- | --- |
| `[course]` | Course text. Every window is `course`. A glob may end in `:12-30,45` to take only those lines of a file that otherwise ports into the release. |
| `[generated]` | Course data written by the suite's own tools, and per-run scripts. Windows that a benign file also contains are dropped; the rest are `course`. |
| `[prose]` | Owner prose. Windows that a benign file also contains are dropped. |
| `[benign]` | Release candidates (ported code, schemas, docs). Their windows only remove windows from the generated and prose classes. |
| `[skip]` | Never read. A skip match wins over every other section. |

Lines before the first header are `[course]`. The prefixes `-` (benign), `!` (skip) and `~` (prose)
override the section for one line. A file that several sections match takes, in order:
a whole-file course glob, generated, course line ranges, prose, benign.

A course line range explicitly designates protected text inside another file.
Every window in that range remains indexed, even when benign files contain its individual words
or its entire phrase. Reviewed phrase exceptions require private allowlist entries.

JSON contributes string values (object keys are dropped). Python and JavaScript contribute string
literals and comments, so retained executable tokens do not create findings. Other text contributes
its whole content; PDF sources contribute extracted page text. Benign files contribute every word.
Clinical text inside code strings still enters the index. Repeated eight-word windows remain eligible.
The builder and scanner skip windows containing eight single-character tokens, such as option letters.
This rule also applies to older indexes and printable font indices.
Every window containing a longer token remains eligible for matching.

Review benign overlaps one at a time in the private `shingle-allowlist.toml`:

```toml
[[allow]]
hash = "0123456789abcdef"
path = "docs/example.md"
reason = "Reviewed generic wording shared with an earlier technical document."
```

Each entry needs a hash (full 16 digits; a prefix of 8 or more digits also works), a path glob or
list of globs, and a reason. An entry silences blocking hits and review items alike.
The gate reports the first unlisted window per line, so a reviewed line can need one entry per window.
Course wording, personal details and private identifiers receive no entry.
The index is too large for a CI secret; layer 3 runs in local release checks only.

## Inventory regression

`tests/privacy/test_inventory_hits.py` reads the local `inventory-hits.txt`.
It checks every inventoried excerpt against tracked and untracked release text,
after case folding and whitespace normalization. Findings print inventory ordinals and paths.
Approved institutional examples retain their scoped exceptions.

```sh
LT_REQUIRE_PRIVATE=1 python3 -m pytest tests/privacy/test_inventory_hits.py -q
```

The test skips without a local inventory. `LT_REQUIRE_PRIVATE=1` turns missing evidence into failure.
Run the gate again after shared-code synchronization and artifact generation.
Resolve findings with the owning package before release integration.

## RAT folders

RAT sheets and answer-bearing payloads, rendered scripts, logs and fill reports stay local. Only the shared reader opens sheets. Classification output contains sheet names and counts, with paths replaced by root keys and hashes. Export never opens a sheet. See [RAT answers](rat-answers.md).
