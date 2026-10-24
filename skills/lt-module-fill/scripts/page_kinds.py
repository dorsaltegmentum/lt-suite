#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared page classification CLI. Answer sheets stay inside the reader process."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
from ltcore.page_kinds import main

if __name__ == "__main__":
    raise SystemExit(main())
