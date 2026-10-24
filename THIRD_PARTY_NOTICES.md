<!--
SPDX-FileCopyrightText: 2026 dorsaltegmentum
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Third-party notices

Third-party assets retain their upstream licences; lt-suite code uses AGPL-3.0-or-later.
Standard licence texts live in `LICENSES/`; source links below identify upstream terms and credits.
Catalogue entries describe optional assets; a catalogue listing does not bundle that font or dependency.
Derived themes adjust source values for contrast, fills, strokes, and semantic roles.
These changes carry no upstream endorsement.

## Palette families

- Okabe-Ito: [Color Universal Design](https://jfly.uni-koeln.de/color/); `LicenseRef-palette-attribution`; colour palette by Masataka Okabe and Kei Ito; citation: Wong, Nature Methods 8, 441 (2011), doi:10.1038/nmeth.1618.
- Paul Tol: [tol_colors](https://github.com/Descanonge/tol_colors); BSD-3-Clause; Copyright (c) 2019, Paul Tol, Clément Haëck; colour schemes by Paul Tol, [upstream reference](https://sronpersonalpages.nl/~pault/).
- IBM Design Language: [IBM Design Colors](https://github.com/IBM-Design/colors); CC-BY-4.0; credit International Business Machines Corporation; five colour values shipped unchanged in the catalog, [licence](https://creativecommons.org/licenses/by/4.0/).
- Petroff 6, 8, and 10: [accessible-color-cycles](https://github.com/mpetroff/accessible-color-cycles#license); CC0-1.0 for final colour cycles; credit Matthew Petroff; the upstream analysis code has its separate MIT licence.
- Modus: [modus-themes](https://github.com/protesilaos/modus-themes); GPL-3.0-or-later; Copyright (C) 2019-2026 Free Software Foundation, Inc.; themes by Protesilaos Stavrou; numeric palette values only, adapted into lt-suite themes.
- Catppuccin: [palette](https://github.com/catppuccin/palette); MIT; Copyright (c) 2021 Catppuccin; includes palette values and theme overrides.
- Everforest: [everforest](https://github.com/sainnhe/everforest); MIT; Copyright (c) 2019 sainnhe; includes palette values and theme overrides.
- Solarized: [solarized](https://github.com/altercation/solarized); MIT; Copyright (c) 2011 Ethan Schoonover.
- viridis: [BIDS colormap](https://github.com/BIDS/colormap); CC0-1.0; credit Nathaniel J. Smith, Stefan van der Walt, and Eric Firing.
- cividis: [cmaputil](https://github.com/pnnl/cmaputil); `LicenseRef-palette-attribution`; Copyright (c) 2017, Battelle Memorial Institute; credit Jamie R. Nunez, Christopher R. Anderton, and Ryan S. Renslow; citation: PLOS ONE 13(7), e0199239 (2018), doi:10.1371/journal.pone.0199239.
- Petroff/Primer composite: `petroff-10-on-primer-dark-high-contrast`; `MIT AND CC0-1.0`; Petroff-10 accents retain CC0-1.0 and Primer Dark High Contrast neutrals retain MIT; credit Matthew A. Petroff and GitHub, Inc.
- ColorBrewer: [ColorBrewer](https://colorbrewer2.org/); Apache-2.0 with [additional upstream conditions](https://github.com/axismaps/colorbrewer/blob/master/export/LICENSE.txt); Copyright (c) 2002 Cynthia Brewer, Mark Harrower, and The Pennsylvania State University; "This product includes color specifications and designs developed by Cynthia Brewer (http://colorbrewer.org/)."; retain upstream conditions, including restrictions on endorsement and product naming.
- USWDS: [U.S. Web Design System](https://github.com/uswds/uswds); CC0-1.0 for GSA colour values; credit U.S. General Services Administration; third-party fonts and icons from that repository are excluded.
- GOV.UK: [GOV.UK Frontend](https://github.com/alphagov/govuk-frontend); MIT; Copyright (c) 2017 Crown Copyright (Government Digital Service).
- GitHub Primer: [primitives](https://github.com/primer/primitives); MIT; Copyright (c) 2018 GitHub Inc.
- a11y-syntax-highlighting: [a11y-syntax-highlighting](https://github.com/ericwbailey/a11y-syntax-highlighting); MIT; Copyright (c) 2017 Eric Bailey.

The exact palette identifiers, source credits and licence expressions appear in [ATTRIBUTION.md](shared/palettes/ATTRIBUTION.md).
Both cividis and Okabe-Ito use `LicenseRef-palette-attribution`; its licence file includes the full Battelle terms.

## Fonts

Noto Sans and Noto Sans Mono supply the bundled defaults.
Bundled font directories carry upstream copyright notices and `OFL.txt`; fonts remain under OFL-1.1.
Other entries identify optional system fonts or downloads and retain their upstream licences.
Modified or subset fonts must obey the upstream Reserved Font Name conditions.
Catalog labels `OFL-1.1-no-RFN` and `OFL-1.1-RFN` both mean SPDX `OFL-1.1`; the suffix records the naming condition.
[The font catalog](shared/fonts/catalog.json) records one licence expression and source terms per optional item.
Optional font files are excluded from the release; only the two Noto defaults are bundled.

| Catalog item | Licence expression | Source terms and naming conditions |
|---|---|---|
| [atkinson-hyperlegible](https://github.com/googlefonts/atkinson-hyperlegible) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2020 Braille Institute of America, Inc.; googlefonts/atkinson-hyperlegible OFL.txt) |
| [atkinson-hyperlegible-next](https://github.com/googlefonts/atkinson-hyperlegible-next) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors) |
| [atkinson-hyperlegible-mono](https://github.com/googlefonts/atkinson-hyperlegible-next-mono) | `OFL-1.1` | OFL-1.1 (upstream OFL.txt names no Reserved Font Name; the Nerd Fonts licence audit lists it as OFL-1.1-RFN) |
| [lexend](https://github.com/googlefonts/lexend) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'RevReading Lexend' (Copyright 2018 The Lexend Project Authors) |
| [andika](https://github.com/silnrsi/font-andika) | `OFL-1.1` | OFL-1.1 with Reserved Font Names 'Andika' and 'SIL' (Copyright 2004-2026 SIL Global) |
| [luciole](https://luciole-vision.com/en/) | `CC-BY-4.0` | CC-BY-4.0 for the font files (LICENSE.txt in Luciole.zip, credit 'Luciole © Laurent Bourcellier & Jonathan Perez'); Luciole Math is OFL-1.1 |
| [inclusive-sans](https://github.com/LivKing/Inclusive-Sans) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2022 The Inclusive Sans Project Authors) |
| [opendyslexic](https://forge.hackers.town/antijingoist/opendyslexic (moved from github.com/antijingoist/opendyslexic, now a pointer README)) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'OpenDyslexic' for 2019+ releases (Copyright 2019 Abbie Gonzalez). Older releases derived from Bitstream Vera, such as Debian's 20160623, carry the Bitstream Vera licence (Nerd Fonts audit: Bitstream-Vera). |
| [noto-sans](https://github.com/notofonts/latin-greek-cyrillic) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2022 The Noto Project Authors) |
| [noto-sans-mono](https://github.com/notofonts/latin-greek-cyrillic) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2022 The Noto Project Authors) |
| [dejavu-sans](https://github.com/dejavu-fonts/dejavu-fonts) | `Bitstream-Vera AND LicenseRef-Arev-Fonts AND LicenseRef-Public-Domain` | Bitstream Vera licence for the Vera base, Arev Fonts licence for glyphs from Arev (Tavmjong Bah), DejaVu changes public domain (upstream LICENSE) |
| [dejavu-sans-mono](https://github.com/dejavu-fonts/dejavu-fonts) | `Bitstream-Vera AND LicenseRef-Arev-Fonts AND LicenseRef-Public-Domain` | Bitstream Vera licence, Arev Fonts licence, DejaVu changes public domain (upstream LICENSE) |
| [liberation-sans](https://github.com/liberationfonts/liberation-fonts) | `OFL-1.1` | OFL-1.1 with Reserved Font Names 'Liberation' (Red Hat) and Arimo, Tinos, Cousine (Google), since 2.00; 1.x releases were GPL-2.0-with-font-exception |
| [liberation-mono](https://github.com/liberationfonts/liberation-fonts) | `OFL-1.1` | OFL-1.1 with Reserved Font Names 'Liberation' and Arimo, Tinos, Cousine, since 2.00 |
| [inter](https://github.com/rsms/inter) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2016 The Inter Project Authors) |
| [source-sans-3](https://github.com/adobe-fonts/source-sans) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'Source' (Copyright 2010-2024 Adobe) |
| [source-code-pro](https://github.com/adobe-fonts/source-code-pro) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'Source' (Adobe) |
| [ibm-plex-sans](https://github.com/IBM/plex) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'Plex' (Copyright 2017 IBM Corp.) |
| [ibm-plex-mono](https://github.com/IBM/plex) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'Plex' (IBM Corp.) |
| [fira-sans](https://github.com/bBoxType/FiraSans (current); https://github.com/mozilla/Fira (4.202)) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Mozilla Foundation, Telefonica S.A., Carrois, bBox Type) |
| [fira-code](https://github.com/tonsky/FiraCode) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2014 The Fira Code Project Authors) |
| [public-sans](https://github.com/uswds/public-sans) | `OFL-1.1` | OFL-1.1; GSA modifications are CC0 in the US, and LICENSE.md says to use the combined font under OFL-1.1 |
| [readex-pro](https://github.com/ThomasJockin/readexpro) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'RevReading Lexend' (Copyright 2018 The Readex Pro Project Authors) |
| [adwaita-sans](https://gitlab.gnome.org/GNOME/adwaita-fonts (mirror https://github.com/GNOME/adwaita-fonts)) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Inter Project Authors, Renzhi Li, Jamie Gravendeel, Florian Müllner) |
| [adwaita-mono](https://gitlab.gnome.org/GNOME/adwaita-fonts (mirror https://github.com/GNOME/adwaita-fonts)) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Iosevka by Renzhi Li, customised for GNOME) |
| [iosevka](https://github.com/be5invis/Iosevka) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2015-2026 Renzhi Li). Nerd-patched builds keep OFL-1.1 for the base glyphs; icons carry their own licences (see symbols-nerd-font). |
| [jetbrains-mono](https://github.com/JetBrains/JetBrainsMono) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2020 The JetBrains Mono Project Authors) |
| [ubuntu](https://design.ubuntu.com/font (Google Fonts copy: ufl/ubuntu)) | `Ubuntu-font-1.0` | Ubuntu Font Licence 1.0 (google/fonts ufl/; not OFL) |
| [ubuntu-sans](https://github.com/canonical/Ubuntu-Sans-fonts) | `Ubuntu-font-1.0` | Ubuntu Font Licence 1.0 (LICENCE.txt in canonical/Ubuntu-Sans-fonts) |
| [cantarell](https://gitlab.gnome.org/GNOME/cantarell-fonts (mirror https://github.com/GNOME/cantarell-fonts)) | `OFL-1.1` | OFL-1.1 (no Reserved Font Name; Copyright 2009-2019 The Cantarell Authors) |
| [symbols-nerd-font](https://github.com/ryanoasis/nerd-fonts) | `MIT AND CC-BY-4.0 AND Apache-2.0 AND OFL-1.1 AND Unlicense` | MIT for the Nerd Fonts patcher and the Symbols Only package; each icon set keeps its own licence: Font Awesome CC-BY-4.0, Codicons CC-BY-4.0, Material Design Icons Apache-2.0, Octicons MIT, Devicons MIT, Seti-UI MIT, Powerline Symbols MIT, Powerline Extra MIT, IEC Power Symbols MIT, Font Awesome Extension MIT, Font Logos Unlicense, Pomicons OFL-1.1 (RFN), Weather Icons OFL-1.1 (patched-fonts/NerdFontsSymbolsOnly/README.md, license-audit.md) |
| [cascadia-mono](https://github.com/microsoft/cascadia-code) | `OFL-1.1` | OFL-1.1 with Reserved Font Name 'Cascadia Code' (Copyright (c) 2019 - Present, Microsoft Corporation) |

## Bundled icon paths and schemas

- Material Design Icons: [Pictogrammers](https://github.com/Templarian/MaterialDesign); Apache-2.0; Copyright Pictogrammers and contributors; SVG outlines extracted through Nerd Fonts, rescaled and converted to path data in `skills/mechanistic-diagram/engine/icons.py`.
- Font Awesome Extension: [font-awesome-extension](https://github.com/AndreLZGava/font-awesome-extension); MIT; Copyright (c) 2017 André Luiz Gava; five `fae_` paths extracted through Nerd Fonts and rescaled for the diagram engine.
- Nerd Fonts extraction source: [Nerd Fonts](https://github.com/ryanoasis/nerd-fonts); MIT for the patcher; Copyright (c) 2014 Ryan L McIntyre; extracted MDI and Font Awesome Extension glyphs retain the licences above; the carrier font is excluded.
- JSON Schema: [Draft 2020-12](https://json-schema.org/draft/2020-12); the handoff schemas are lt-suite code under AGPL-3.0-or-later; `$schema` links reference the external specification without bundling its meta-schema or implementation.

## Python dependencies installed separately

These packages retain their own licences and notices when installed.
The release dependency groups list them in `pyproject.toml`.

- jsonschema: [python-jsonschema](https://github.com/python-jsonschema/jsonschema); MIT; Copyright Julian Berman.
- Pillow: [Pillow](https://github.com/python-pillow/Pillow); MIT-CMU and bundled-component notices; credit Secret Labs AB, Fredrik Lundh, and Pillow contributors.
- NumPy: [NumPy](https://github.com/numpy/numpy); BSD-3-Clause and bundled-component notices; credit NumPy Developers.
- fontTools: [fonttools](https://github.com/fonttools/fonttools); MIT; credit Just van Rossum and fontTools contributors.
- ReportLab: [ReportLab](https://www.reportlab.com/); BSD-3-Clause; credit ReportLab Inc.
- pypdfium2: [pypdfium2](https://github.com/pypdfium2-team/pypdfium2); Apache-2.0 OR BSD-3-Clause for bindings; bundled PDFium and dependencies retain their own notices.
- SciPy: [SciPy](https://github.com/scipy/scipy); BSD-3-Clause and bundled-component notices; credit SciPy Developers.
- pikepdf, optional: [pikepdf](https://github.com/pikepdf/pikepdf); MPL-2.0; credit pikepdf contributors and qpdf upstream.
- PyMuPDF, optional: [PyMuPDF](https://github.com/pymupdf/PyMuPDF); AGPL-3.0-or-later for the open-source edition; credit Artifex Software, Inc.
- opencv-python-headless, optional: [opencv-python](https://github.com/opencv/opencv-python); MIT for packaging and Apache-2.0 for OpenCV; wheel-bundled FFmpeg uses LGPL-2.1-or-later and retains its own notices.
- scikit-learn, optional: [scikit-learn](https://github.com/scikit-learn/scikit-learn); BSD-3-Clause; credit scikit-learn developers.
- pytest, development: [pytest](https://github.com/pytest-dev/pytest); MIT; credit Holger Krekel and pytest contributors.
- REUSE, development: [reuse-tool](https://github.com/fsfe/reuse-tool); GPL-3.0-or-later with upstream component notices; credit Free Software Foundation Europe and contributors.
- skills-ref, development: [agentskills](https://github.com/agentskills/agentskills); Apache-2.0; credit Agent Skills contributors.

## Browser tools and system programs installed separately

- chrome-devtools-mcp: [ChromeDevTools](https://github.com/ChromeDevTools/chrome-devtools-mcp); Apache-2.0; credit Google LLC and contributors.
- Playwright MCP, optional adapter: [playwright-mcp](https://github.com/microsoft/playwright-mcp); Apache-2.0; credit Microsoft Corporation.
- librsvg: [librsvg](https://gitlab.gnome.org/GNOME/librsvg); LGPL-2.1-or-later; credit librsvg contributors.
- Poppler tools: [Poppler](https://poppler.freedesktop.org/); GPL-2.0-or-later with component-specific notices; credit Poppler contributors.
- libxml2 tools: [libxml2](https://gitlab.gnome.org/GNOME/libxml2); MIT; credit Daniel Veillard and contributors.
- qpdf: [qpdf](https://github.com/qpdf/qpdf); Apache-2.0; credit Jay Berkenbilt and contributors.
- ExifTool: [ExifTool](https://exiftool.org/); Artistic-1.0-Perl OR GPL-1.0-or-later; credit Phil Harvey.
- Potrace: [Potrace](https://potrace.sourceforge.net/); GPL-2.0-or-later; credit Peter Selinger.
- draw.io: [drawio](https://github.com/jgraph/drawio); Apache-2.0; credit JGraph Ltd.

Installed distributions may include additional components; retain their supplied licence files when redistributing them.
