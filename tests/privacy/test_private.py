# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private patterns stay private and institutional exceptions stay narrowly scoped."""
import base64

import pytest


@pytest.mark.parametrize("source", ["file", "env"])
def test_private_layer_redacts_even_with_excerpt(run_gate, tmp_path, source):
    private = tmp_path / "private"
    private.mkdir()
    patterns = r"\bsynthetic-owner-91\b" + "\n"
    env = {"LT_PRIVACY_DIR": str(private)}
    if source == "file":
        (private / "private-patterns.txt").write_text(patterns, encoding="utf-8", newline="\n")
    else:
        env["PRIVACY_PATTERNS_B64"] = base64.b64encode(patterns.encode()).decode()
    target = tmp_path / "a.txt"
    target.write_text("synthetic-owner-91\n", encoding="utf-8", newline="\n")
    r = run_gate("--paths", str(target), "--require-private", "--excerpt", env=env)
    assert r.returncode == 1 and r.stdout.startswith("private-")
    assert "synthetic-owner" not in r.stdout + r.stderr


@pytest.mark.parametrize("value", ["%%%", base64.b64encode(b"[broken").decode()])
def test_invalid_private_secret_fails_closed(run_gate, tmp_path, value):
    p = tmp_path / "a.txt"
    p.write_text("clean", encoding="utf-8", newline="\n")
    r = run_gate("--paths", str(p), env={"PRIVACY_PATTERNS_B64": value})
    assert r.returncode == 2
    assert value not in r.stderr


def test_untagged_private_hit_never_uses_public_wildcard(gate):
    rules = gate.load_rules(gate.PATTERNS_FILE)
    g = gate.Gate(rules, gate.load_allowlist(gate.ALLOWLIST_FILE, rules),
                  gate.parse_private("synthetic-owner", "", "synthetic"))
    assert g.scan_text("synthetic-owner", "x", "tests/privacy/test_example.py")[0].kind == "private"


def test_private_tags_only_match_declared_paths(gate):
    rules = gate.load_rules(gate.PATTERNS_FILE)
    g = gate.Gate(rules, gate.load_allowlist(gate.ALLOWLIST_FILE, rules),
                  gate.parse_private("[example-institution] Synthetic Academy", "", "synthetic"))
    assert not g.scan_text("Synthetic Academy", "x", (gate.PRIVATE_TAG_PATHS["example-institution"][0].removesuffix("**") + "config.toml"))
    assert not g.scan_text("Synthetic Academy", "x", "docs/configuration.md")
    assert g.scan_text("Synthetic Academy", "x", "docs/guide.md")


def test_email_allowlist_requires_whole_address(gate):
    """An attacker cannot put an allowed address after a private email's local part."""
    rules = gate.load_rules(gate.PATTERNS_FILE)
    g = gate.Gate(rules, gate.load_allowlist(gate.ALLOWLIST_FILE, rules), [])
    assert not g.scan_text("a@example.org", "x", "docs/guide.md")
    assert g.scan_text("a@example.org.evil.test", "x", "docs/guide.md")
