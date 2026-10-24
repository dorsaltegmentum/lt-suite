# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Print or compare the question digest binding a draft to its capture."""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore

ltcore.bootstrap()
from ltcore import digest


if __name__ == "__main__":
    raise SystemExit(digest.main())
