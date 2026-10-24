<!-- SPDX-FileCopyrightText: 2026 dorsaltegmentum -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Python installation and dependency bootstrap. -->

# Python

lt-suite requires Python 3.11 or newer for its runtime scripts.

The setup bootstrap can start under Python 3.8 or newer. It locates a suitable interpreter and creates a virtual environment in the configured data directory.

From a repository checkout, run:

```bash
python3 skills/lt-suite-setup/scripts/setup.py bootstrap
python3 skills/lt-suite-setup/scripts/setup.py doctor
```

For an installed skill, replace the relative script path with its absolute installation path.

## Install an interpreter

With [uv](https://docs.astral.sh/uv/), run:

```bash
uv python install 3.11
```

Run the setup bootstrap again after installing Python.

On Linux, use an available Python 3.11+ package or uv. Ubuntu 22.04's default Python 3.10 needs an additional interpreter.

On macOS, use uv, Homebrew Python or the [Python installer](https://www.python.org/downloads/). A Command Line Tools interpreter can be older than the runtime requirement.

On Windows, use uv or the Python installer. Use `py -3.11` in place of `python3` when the Python launcher provides that interpreter.

## Dependency environments

The bootstrap installs dependencies from the installed skills' generated `requirements.txt` files.

Keep these dependencies inside the suite's virtual environment. This avoids modifying a system-managed Python installation or bypassing PEP 668 protections.

A network-restricted agent can print a bootstrap command for a normal terminal. Run that command yourself, then retry setup.

## Rendering tools

Python packages do not supply every rendering binary.

Diagram and PDF workflows also use `rsvg-convert`, `pdfinfo`, `pdftoppm`, `pdftotext` and `xmllint`. Optional tools include `qpdf`, `exiftool`, `potrace` and draw.io Desktop.

Run `doctor` to identify the tools needed by your selected workflow. See [troubleshooting](../troubleshooting.md).
