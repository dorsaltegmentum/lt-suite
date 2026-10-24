<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# PDF export

`module_pdf.py` typesets a module page to A4 PDFs, `module_pdf_qc.py` checks them, and
`pdf_base.py` holds the page frame, fonts, themes and styles.

## Palettes and fonts

One PDF per palette in `style.palettes`, plus `style.print_palette` when it is not listed.
The print palette comes first and keeps a white page (the palette's own page colour
becomes the colour of banded table rows). The others are screen copies. File names are
`<title> (<palette id>, print|screen).pdf`.

A palette becomes a theme through `ltcore.palettes.derive_theme`. Text colours must reach
4.5:1 on the page colour and on the banded-row colour; a colour that falls short moves
toward the text colour until it does, and the PDF step prints a note. Without the palette
module the built-in light (print) and dark (screen) themes are used, with a note.
This check also covers warning glyphs and coloured group headings.

Fonts come from `ltcore.fonts` (`fonts.sans`, `fonts.mono`, or the explicit
`fonts.sans_path` and `fonts.mono_path`). ReportLab reads TrueType outlines only. Faces:
regular, bold, italic, bold italic and semibold of the sans font, and regular, bold and
italic of the mono font. A face the family lacks falls back to its upright or bold file,
with a note.

### Characters the sans font lacks

Before building, the script checks every character of the page. A character the sans
faces lack is drawn from the mono font, then from an installed symbol font found through
the shared font resolver, then replaced by an ASCII stand-in (`!` for the warning
sign, `->` for an arrow). A character no font covers stops the build (exit 1) with the
list. Each substitution prints a note and is recorded in `.build.json`, so QC compares
the text a PDF actually carries.

## Page layout rules

The template is a plain list of nodes. `module_pdf.py` changes four behaviours:

1. A paragraph never breaks across a page unless it is taller than 90 percent of one.
2. A heading never ends a page alone; it stays with the first thing under it.
3. A "continued" label lands on the page it labels, inside the unit it labels.
4. A table breaks only between rows, and the next page repeats its header row.

An image and its caption are one unit. The image shrinks to fit the space left, down to
75 percent, before it moves to the next page. Transparent images are flattened onto white.
An image with a sibling named `<stem>-<palette id>.<ext>` is drawn in that palette's PDF
(the print palette always uses the image the page links). Every image has rounded
corners, 3 percent of its shorter side, between 4 and 10 points. An SVG stays vector
when PyMuPDF is installed (rsvg-convert makes a one-page PDF and PyMuPDF places it);
otherwise it is rasterised to a PNG at twice its size and a note says so.

## QC gates

`module_pdf_qc.py` reads every content line of the page from the source file, never
through the renderer's tree, and fails a PDF on any of:

- a content line missing from the PDF (compared as text, whitespace ignored);
- a heading or meta line that ends a page;
- an empty bullet, or literal Markdown (`**`, `[[`, backticks, a raw link);
- a "continued" label that is not the first line of its page, or a missing or stray label;
- a paragraph broken across a page;
- a thin page (under 700 characters and under 15 percent of the frame in images), except the last;
- an image count that differs from the page, a wrong footer, or page properties missing
  from the PDF's Subject;
- palettes that carry different text.

QC runs on the drafts, which still carry their metadata. The final pass then removes it.

## Final pass

1. Each `[[text_cleaners]]` entry with `stage = "pdf"` runs on every PDF, in config order.
   The command is an argument list with `{pdf}`, never a shell string.
2. When `pdf.strip_metadata` is on, the built-in strip removes document info and XMP
   (exiftool, then a qpdf or pikepdf rewrite). The strip needs one of those tools; with
   none installed the pass fails instead of shipping unstripped PDFs.
3. Final verification compares page counts, image dimensions and RGB pixels against
   the draft rendered at scale 0.3. Any difference fails the export.

`<out>/.final.json` records each step. The PDF Author is empty unless `pdf.author` is set.
`--draft` skips this pass.

## Optional dependencies

| Package or tool | Used for | Without it |
|---|---|---|
| PyMuPDF | vector SVG figures | SVG figures become PNG |
| qpdf, exiftool, pikepdf | metadata strip | the final pass fails when the strip is on |
| rsvg-convert | SVG to PDF or PNG | SVG figures cannot be drawn |
