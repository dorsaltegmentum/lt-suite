<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Upstream terms and credits for the shipped palette values. -->

# Palette attribution

lt-suite ships colour values from the palettes below in `shared/palettes/catalog.json` (and, for three of them, hand-tuned themes in `shared/palettes/overrides/`). The catalog structure and the derivation code are lt-suite's own (AGPL-3.0-or-later). The colour values keep the licence or attribution terms of their source, listed here per family. No source forbids reuse, so every entry ships.

The ranking that picks the default palettes reads only the colour values. Licence and attribution fields are added afterwards and never change a score (`tests/style/test_catalog.py` checks this).

## Licences by family

| Family | Palettes | Licence | Copyright or credit | Source |
|---|---|---|---|---|
| Okabe-Ito | `okabe-ito` | LicenseRef-palette-attribution | Colour Universal Design palette by Masataka Okabe and Kei Ito (https://jfly.uni-koeln.de/color/); popularised by B. Wong, Nature Methods 8, 441 (2011). Used with credit as the authors ask. | https://jfly.uni-koeln.de/color/ |
| Paul Tol | `tol-bright`, `tol-high-contrast`, `tol-vibrant`, `tol-muted`, `tol-medium-contrast`, `tol-light` | BSD-3-Clause | Copyright (c) 2019, Paul Tol, Clément Haëck (tol_colors package); colour schemes © Paul Tol | https://sronpersonalpages.nl/~pault/ |
| IBM Design Language | `ibm-colourblind` | CC-BY-4.0 | IBM Design Language colour values, International Business Machines Corporation | https://www.ibm.com/design/language/color/ |
| Petroff 2021 | `petroff-6`, `petroff-8`, `petroff-10` | CC0-1.0 | Colour cycles by Matthew A. Petroff, dedicated to the public domain (CC0-1.0); see M. A. Petroff, "Accessible Color Sequences for Data Visualization", arXiv:2107.02270 | https://github.com/mpetroff/accessible-color-cycles |
| Modus | `modus-operandi`, `modus-operandi-tinted`, `modus-operandi-deuteranopia`, `modus-operandi-tritanopia`, `modus-vivendi`, `modus-vivendi-tinted`, `modus-vivendi-deuteranopia`, `modus-vivendi-tritanopia` | GPL-3.0-or-later | Copyright (C) Free Software Foundation, Inc. (modus-themes by Protesilaos Stavrou); colour values only | https://github.com/protesilaos/modus-themes |
| Catppuccin | `catppuccin-latte`, `catppuccin-frappe`, `catppuccin-macchiato`, `catppuccin-mocha` | MIT | Copyright (c) 2021 Catppuccin | https://github.com/catppuccin/palette |
| Everforest | `everforest-dark-medium`, `everforest-light-medium` | MIT | Copyright (c) 2019 sainnhe | https://github.com/sainnhe/everforest |
| Solarized | `solarized-dark`, `solarized-light` | MIT | Copyright (c) 2011 Ethan Schoonover | https://github.com/altercation/solarized |
| viridis family | `viridis` | CC0-1.0 | viridis by Stéfan van der Walt and Nathaniel Smith (BIDS mpl-colormaps), CC0-1.0 public domain dedication | https://bids.github.io/colormap/ |
| viridis family | `cividis` | LicenseRef-palette-attribution | Nuñez, Anderton and Renslow, PLOS ONE 2018; values Copyright (c) 2017 Battelle Memorial Institute (see below) | https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199239 |
| ColorBrewer | `colorbrewer-set2-n3`, `colorbrewer-dark2-n3`, `colorbrewer-paired-n4` | Apache-2.0 | Copyright (c) 2002 Cynthia Brewer, Mark Harrower, and The Pennsylvania State University | https://colorbrewer2.org/ |
| USWDS | `uswds` | CC0-1.0 | U.S. Web Design System, General Services Administration; public domain in the United States, worldwide CC0-1.0 waiver | https://designsystem.digital.gov/design-tokens/color/overview/ |
| GOV.UK | `govuk` | MIT | Copyright (c) 2017 Crown Copyright (Government Digital Service) | https://design-system.service.gov.uk/styles/colour/ |
| GitHub Primer | `primer-light-colorblind`, `primer-dark-colorblind`, `primer-light-tritanopia`, `primer-dark-tritanopia`, `primer-light-high-contrast`, `primer-dark-high-contrast` | MIT | Copyright (c) 2018 GitHub Inc. | https://github.com/primer/primitives |
| a11y-syntax-highlighting | `a11y-syntax-dark` | MIT | Copyright (c) 2017 Eric Bailey | https://github.com/ericwbailey/a11y-syntax-highlighting |

## Notices

- ColorBrewer (Apache-2.0): "This product includes color specifications and designs developed by Cynthia Brewer (http://colorbrewer.org/)." The name "ColorBrewer" is not used in any product name.
- Modus (GPL-3.0-or-later): only colour values are taken from modus-themes; GPLv3 section 13 allows combining them with AGPL-3.0 code.
- IBM (CC-BY-4.0): colour values from IBM Design Language, International Business Machines Corporation, https://github.com/IBM-Design/colors. Values unchanged.
- Petroff (CC0-1.0): the colour cycles are dedicated to the public domain; the repository's MIT licence covers its code only.
- Catppuccin (MIT) and Everforest (MIT): the full licence texts are in `LICENSES/MIT.txt`; copyright lines above.

## Composite dark default

`petroff-10-on-primer-dark-high-contrast` is Petroff-10's ten accent colours (CC0-1.0, Matthew A. Petroff) on the five neutral colours of GitHub Primer Dark High Contrast: background, surface, text, muted text and border (MIT, Copyright (c) 2018 GitHub Inc.). It holds no other colour values. Licence expression: `MIT AND CC0-1.0`.

## LicenseRef-palette-attribution

Two catalog entries carry colour values whose authors grant use with credit rather than an SPDX licence. lt-suite ships them under this identifier, with the credit below.

### Okabe-Ito (`okabe-ito`)

Colour Universal Design palette by Masataka Okabe and Kei Ito, https://jfly.uni-koeln.de/color/. The authors ask for credit when the palette is used. Popularised by B. Wong, "Points of view: Color blindness", Nature Methods 8, 441 (2011), doi:10.1038/nmeth.1618. Hex values as published in R grDevices `palette.colors("Okabe-Ito")`.

### cividis (`cividis`)

Jamie R. Nuñez, Christopher R. Anderton and Ryan S. Renslow, "Optimizing colormaps with consideration for color vision deficiency to enable accurate interpretation of scientific data", PLOS ONE 13(7): e0199239 (2018), doi:10.1371/journal.pone.0199239. Colour values rounded from the authors' table `colormaps/cividis.txt` in https://github.com/pnnl/cmaputil, distributed under this licence:

```text
Copyright (c) 2017, Battelle Memorial Institute

1.  Battelle Memorial Institute (hereinafter Battelle) hereby grants
permission to any person or entity lawfully obtaining a copy of this software
and associated documentation files (hereinafter “the Software”) to
redistribute and use the Software in source and binary forms, with or without
modification. Such person or entity may use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and may permit
others to do so, subject to the following conditions:

+ Redistributions of source code must retain the above copyright notice, this
list of conditions and the following disclaimers.

+ Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.

+ Other than as used herein, neither the name Battelle Memorial Institute or
Battelle may be used in any form whatsoever without the express written
consent of Battelle.

2.  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL BATTELLE OR CONTRIBUTORS BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
