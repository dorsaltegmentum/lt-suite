# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migrate user-owned diagram text written with v0.1.0's locale default."""
import locale
from pathlib import Path
import sys


def read_legacy_text(path):
    """Prefer UTF-8. Rewrite a legacy locale file once, with an LF diagnostic."""
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Decode bytes explicitly: locale text opens are forbidden for new I/O.
        encoding = locale.getencoding()
        text = path.read_bytes().decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"lt-suite: migrated {path} from {encoding} to UTF-8 with LF", file=sys.stderr)
        return text


def read_legacy_bytes(path):
    """Migrate text before byte comparisons; preserve valid UTF-8 hash inputs."""
    read_legacy_text(path)
    return Path(path).read_bytes()
