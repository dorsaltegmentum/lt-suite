<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Export outputs

What `export_module.py` writes, how the page is built, and how ownership is proved.

## The page

`module-page.md` is always Logseq outline syntax. The PDF step reads it, and `--install`
places it into the notes graph (converted first when `notes.format` is not `logseq`).

```
title:: <notes.page_title with {namespace}, {week}, {title}, {module_id}>
type:: **<namespace> Module Output**
<notes.stamp_property>:: <module id>
lt-run:: <run id>
lt-source:: <capture source address>
lt-captured:: <time>
lt-exported:: <time>
lt-exported-by:: lt-suite/lt-module-export <version>
tags:: [[<namespace>]], [[module output]]
```

Below the properties come, in order: a warning block that says what the page is, a source
line, the intended learning outcomes (`N :: outcome`), then one `# Page N` heading per
page with a drafted item. Each drafted item is a `## prompt` heading, a meta line
(item id and confidence), the answer segments with clickable citations, table cells,
images and the fill status. Items that were not drafted follow under `# Not drafted`:
a blocked item as a `TODO`, any other item as one line with its skip reason.

Properties appear only at the top of the page. Logseq treats `key:: value` in the first
block as page properties and anywhere else as block properties, which clutter the outline.

## Week and title

`curriculum.week_number_pattern` finds the week in `lesson.title`. Capture 1 is the week;
an optional group 2 is the session, and the two join with a dot. The rest of the title,
minus separators, is the page title. A leading lesson label (`curriculum.patterns.lesson`)
is dropped, and a title that is only a number (`curriculum.patterns.bare_number`) becomes
`Module <number>`. With no week in the title the export stops until `--week` names one.

## Learning outcomes

`curriculum.learning_outcomes_file` holds the course's outcomes. A section starts at each
line that matches `curriculum.patterns.ilo_heading`. With two capture groups they are the
week and the title; with one it is the title; with none the heading text is the title.
The section whose week equals the module's week and whose title matches wins. Several
equally good sections give no match: a person picks, the script never guesses. The
section ends at the next heading of the same or a higher level. Outcomes are the lines
that start with `- `. `curriculum.patterns.ilo_kind` is an optional pattern for a kind line
under the heading: group 1 is the kind, an optional group 2 the discipline.

## Citations

A provenance tag becomes a reader-facing citation, never raw `[T1:...]` text:

1. the module itself: plain text `this module`;
2. a PubMed id or a DOI: a link built from the identifier;
3. `export.citations.url_aliases`: a link;
4. `export.citations.page_aliases`: a `[[page]]` link;
5. `export.citations.kind_pages`: a `[[page]]` link;
6. `export.citations.transcript_page_from_filename`, when true: a transcript path's filename stem as a `[[page]]` link;
7. anything else: the source id in bold, with no invented page.

`export.citations.transcript_page_from_filename` defaults to false. Both path separators
work, and only the final suffix is removed. Missing paths and empty or whitespace
stems fall through to the source id. Notes formats and PDFs render these links like
page aliases.

`export.citations.hidden_skip_reasons` lists skip reasons whose items are left off the
page (for example `course-feedback`). They stay in `answers.json`.

## Notes formats

| `notes.format` | Installed page | Links | Whiteboard |
|---|---|---|---|
| `logseq` | the page as built, file name per `notes.file_name_format` | `[[page]]` | yes |
| `obsidian` | YAML front matter, headings and bullets, a visible stamp line | `[[page]]` | no |
| `markdown` | the same, links reduced to plain text | none | no |

`triple-lowbar` writes `Lt___Week 1 Title.md` (slash becomes `___`). `legacy` writes
`Lt.Week 1 Title.md`. Other formats nest the page in a folder per namespace. Page titles
come from Lt data and are never trusted as paths: reserved characters become `-`, a
`..` part becomes `_`, and a title that would leave the pages folder stops the export.

Converted pages keep the stamp as a line `<property>:: <module id>` after the front
matter, so the quarantine scan finds them in every format.

## Targets and ownership

Staging writes stay inside `--out-dir`, which must be outside `curriculum.root`.
Configured output names must be single filenames with no path separators.
Existing staging targets require `--overwrite`, including copied assets and PDFs.
Use a new staging folder to keep previous exports for comparison.

The stamp property must differ from `title`, `type`, `tags`, `lt-run`, `lt-source`,
`lt-captured`, `lt-exported` and `lt-exported-by`. Every generated Markdown page is
checked for the exact quarantine property line before writing.

`--install` builds a list of targets: the page, the whiteboard and each asset. Each
target is classified before anything is written.

- Missing: written.
- Identical to this run (ignoring the `lt-exported::` line): skipped.
- This skill's output for this module, edited by hand: blocked (exit 4) with the diff
  written next to the export; `--replace` writes it after the diff is kept.
- Anything else, including another module's output: blocked, and `--replace` does not
  change that.

Proof of ownership: a page carries the stamp for this module id, a whiteboard carries
`:block/original-name "<namespace>-<module id>"`, an asset's file name starts with
`lt-<module id>-`.

## Media

Captions and alt text are escaped as literal text on one line. PDF image references,
including palette variants and symlinks, must resolve inside the page's `assets/`
folder or an asset directory explicitly supplied with `--assets`.

Lt images arrive as `<media dir>/lt-<module id>-<key>.<ext>`. Author images and student
uploads are numbered by two separate Lt counters, so one key can name both. A student
upload is saved as `u<key>`. An upload reads `u<key>` first and falls back to the bare
key only when no author image in the module shares that key. An author entry reads the
bare key and never a `u` file. An SVG beside a referenced PNG with the same stem
replaces it on the page and in the PDFs; a `.pdf` or `.drawio` reference stays a link.

## Extra files

- `ilo-map.md`: `# <week> <title>: learning outcome map`, then each outcome with the items
  whose provenance cites its number, or a warning line when none does.
- `tracker-handoff.md`: the same coverage as TODO items (Logseq `TODO`, or `- [ ]` in
  other formats).
- `flashcard-source.md`: a stamp comment, a heading, a `Tag:` line with
  `export.flashcard_source.tag_prefix`, a source line, the outcomes and each drafted item
  with its provenance tags.
- `<namespace>-<module id>.edn`: a grid of portal shapes, with ids and times derived from
  the module id and linked pages, so an unchanged re-run is byte-identical.

Every Markdown handoff includes the configured module stamp as a separate property line.
The answer skill's quarantine scanner therefore rejects handoffs as drafting sources.
