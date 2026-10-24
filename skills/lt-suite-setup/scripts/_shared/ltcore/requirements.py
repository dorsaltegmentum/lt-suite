# Generated from shared/ltcore/requirements.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Select the numeric Python guards emitted by the frozen dependency export.

This module starts with the setup bootstrap on Python 3.8. Unrecognized guards
remain required; only a fully understood false guard can suppress an import.
The installer receives every original requirement, marker and hash unchanged.
"""
from __future__ import annotations

import operator
import re
import sys

NEEDS = []


def requirement_applies(line, version_info=None):
    if ";" not in line:
        return True
    marker = line.split(";", 1)[1].split(" --hash=", 1)[0].strip()
    match = re.fullmatch(
        r"(python_version|python_full_version)\s*(<=|>=|==|!=|<|>)\s*['\"](\d+(?:\.\d+)*)['\"]",
        marker)
    if match is None:
        return True
    name, op, wanted = match.groups()
    running = tuple((sys.version_info if version_info is None else version_info)[:3])
    if name == "python_version":
        running = running[:2]
    wanted = tuple(int(part) for part in wanted.split("."))
    length = max(len(running), len(wanted))
    running += (0,) * (length - len(running))
    wanted += (0,) * (length - len(wanted))
    compare = {"<": operator.lt, "<=": operator.le, "==": operator.eq,
               "!=": operator.ne, ">=": operator.ge, ">": operator.gt}
    return compare[op](running, wanted)
