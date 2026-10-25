# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local inventory excerpts must be absent from every release candidate file."""
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def test_inventory_hits_absent(gate):
    private = gate.default_private_dir()
    inventory = private / "inventory-hits.txt"
    if not inventory.is_file():
        if os.environ.get("LT_REQUIRE_PRIVATE") == "1":
            pytest.fail("LT_REQUIRE_PRIVATE=1 requires the local inventory-hits.txt", pytrace=False)
        pytest.skip("local inventory-hits.txt is absent")
    needles = [(n, normalize(line)) for n, line in enumerate(inventory.read_text(encoding="utf-8").splitlines(), 1)
               if line.strip() and not line.lstrip().startswith("#")]
    assert needles, "inventory-hits.txt contains no inventory strings"
    rules = gate.load_rules(gate.PATTERNS_FILE)
    # These tags are the explicit public example exceptions; no course text or
    # personal identifiers receive an exception here.
    private_patterns, _ = gate.load_private(private)
    exception = gate.Gate(rules, gate.load_allowlist(gate.ALLOWLIST_FILE, rules), private_patterns)
    found = []
    for path in gate.git_files(REPO, untracked=True):
        f = REPO / path
        if not (f.is_file() or f.is_symlink()):
            continue
        data = gate.read_file(f, path)
        text = gate.decode_text(data)
        if text is None:
            continue
        views = {normalize(text), normalize(gate.decoded_view(text)), normalize(path)}
        for ordinal, needle in needles:
            if not any(needle in view for view in views):
                continue
            # An inventory phrase is exempt only if its entire text is a tagged
            # private regex match and that precise match has a scoped exception.
            permitted = False
            for pat in private_patterns:
                m = re.fullmatch(pat.rx.pattern, needle, pat.rx.flags | re.IGNORECASE)
                if m and pat.tag and exception.allowed(pat.num, path, m.group(), needle,
                                                       "file", pat.tag, False):
                    permitted = True
                    break
            if not permitted:
                found.append(f"inventory-{ordinal} {path}")
    if found:
        pytest.fail("Inventory matches:\n" + "\n".join(found), pytrace=False)
