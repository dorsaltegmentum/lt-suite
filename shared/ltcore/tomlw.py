# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal TOML writer for lt-suite config files (standard library only).

Python reads TOML with ``tomllib`` but cannot write it. This module writes the
subset the config uses: tables, arrays of tables, strings, integers, booleans,
floats and lists. ``tomllib.loads(dumps(doc)) == doc`` holds for every document
built from those types; ``None`` and other types raise ``TypeError`` naming the key.

    from ltcore import tomlw
    text = tomlw.dumps({"lt": {"origin": "https://lt.example.invalid"}})
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

NEEDS: list[str] = []

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
            "\n": "\\n", "\f": "\\f", "\r": "\\r"}
_LINE_LIMIT = 88


def _key(k: str) -> str:
    if not isinstance(k, str):
        raise TypeError(f"TOML keys must be strings, got {type(k).__name__}: {k!r}")
    return k if _BARE_KEY.fullmatch(k) else _basic_string(k)


def _basic_string(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _string(s: str) -> str:
    # Literal strings keep regular expressions readable: '\d' instead of "\\d".
    if "\\" in s and "'" not in s and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in s):
        return "'" + s + "'"
    return _basic_string(s)


def _is_table(v: Any) -> bool:
    return isinstance(v, dict)


def _is_table_array(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0 and all(isinstance(x, dict) for x in v)


def _value(v: Any, where: str) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        items = [_value(x, f"{where}[{i}]") for i, x in enumerate(v)]
        one_line = "[" + ", ".join(items) + "]"
        if len(one_line) <= _LINE_LIMIT or not items:
            return one_line
        return "[\n" + "".join(f"    {x},\n" for x in items) + "]"
    if isinstance(v, dict):
        parts = [f"{_key(k)} = {_value(x, f'{where}.{k}')}" for k, x in v.items()]
        return "{" + ", ".join(parts) + "}" if parts else "{}"
    if v is None:
        raise TypeError(f"{where}: TOML has no null; leave the key out or use \"\"")
    raise TypeError(f"{where}: cannot write a {type(v).__name__} to TOML")


def _emit_table(lines: list[str], path: list[str], table: dict, header: str | None) -> None:
    scalars = [(k, v) for k, v in table.items()
               if not (_is_table(v) and v) and not _is_table_array(v)]
    subtables = [(k, v) for k, v in table.items() if _is_table(v) and v]
    arrays = [(k, v) for k, v in table.items() if _is_table_array(v)]
    if header is not None:
        if lines:
            lines.append("")
        lines.append(header)
    dotted = ".".join(_key(p) for p in path) or "<root>"
    for k, v in scalars:
        lines.append(f"{_key(k)} = {_value(v, f'{dotted}.{k}' if path else k)}")
    for k, v in subtables:
        sub = path + [k]
        # A table holding only subtables needs no header of its own.
        only_children = all((_is_table(x) and x) or _is_table_array(x) for x in v.values())
        h = None if only_children else "[" + ".".join(_key(p) for p in sub) + "]"
        _emit_table(lines, sub, v, h)
    for k, arr in arrays:
        sub = path + [k]
        for entry in arr:
            _emit_table(lines, sub, entry, "[[" + ".".join(_key(p) for p in sub) + "]]")


def dumps(doc: dict, header: Iterable[str] | str | None = None) -> str:
    """Serialize ``doc`` to TOML text. ``header`` lines become ``#`` comments on top."""
    if not isinstance(doc, dict):
        raise TypeError("the TOML document must be a dict")
    lines: list[str] = []
    if header:
        for h in ([header] if isinstance(header, str) else list(header)):
            for part in h.splitlines() or [""]:
                lines.append(("# " + part).rstrip())
        lines.append("")
    body: list[str] = []
    _emit_table(body, [], doc, None)
    lines.extend(body)
    return "\n".join(lines).rstrip("\n") + "\n"


def dump(doc: dict, fp, header: Iterable[str] | str | None = None) -> None:
    """Write ``doc`` as TOML to the text file object ``fp``."""
    fp.write(dumps(doc, header=header))
