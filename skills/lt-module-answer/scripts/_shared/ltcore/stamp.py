# Generated from shared/ltcore/stamp.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recognize module ownership stamps in Markdown, front matter and HTML.

Each expression captures the module identifier in group 1. Export ownership
checks and notes quarantine use these same expressions and precedence.
"""
from __future__ import annotations

import re
from functools import lru_cache

NEEDS: list[str] = []


@lru_cache(maxsize=128)
def stamp_regexes(prop: str) -> tuple[re.Pattern[str], ...]:
    """Parse one-token stamps with case, separator, whitespace and markup tolerance.

    Hyphens and underscores in property names are equivalent. Other property
    characters remain literal. Whitespace cannot consume another property line.
    """
    if not prop or not prop.strip() or any(c in prop for c in "\r\n"):
        raise ValueError("notes.stamp_property must be a nonempty single-line property")
    key = "".join("[-_]" if c in "-_" else re.escape(c) for c in prop)
    key = rf'''["']?{key}["']?'''
    space = r"[^\S\r\n]*"
    # Tags can wrap the property, separator or value without becoming its data.
    gap = rf"{space}(?:</?[A-Za-z][^>\r\n]*>{space})*"
    start = rf"^\ufeff?{gap}"
    value = r'''["']?([^\s"'<>]+)["']?'''
    end = rf"{gap}\r?$"
    flags = re.MULTILINE | re.IGNORECASE
    return (
        re.compile(rf"{start}{key}{gap}:{space}:{gap}{value}{end}", flags),
        re.compile(rf"{start}{key}{gap}:(?!:){gap}{value}{end}", flags),
        re.compile(rf"{start}<!--{gap}{key}{gap}:{{1,2}}{gap}{value}{gap}-->{end}", flags),
    )


def parse_stamp(text: str, prop: str = "lt-module") -> str | None:
    """Return the stamp's module identifier, preserving its original case."""
    for pattern in stamp_regexes(prop):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None
