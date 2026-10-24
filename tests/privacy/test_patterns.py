# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every public pattern has strings it must match and strings it must not match.

A pattern added to tools/privacy/patterns.toml without a row here fails
test_every_pattern_has_cases, so no pattern ships untested.
"""
from __future__ import annotations

import pytest

CASES: dict[str, tuple[list[str], list[str]]] = {
    "home-path": (["see /home/someone/x", "/Users/Some.One/notes", r"C:\Users\someone\Desktop"],
                  ["/home/", "/home/<user>/x", "x/home/a is not a root", "~/Notes/Logseq"]),
    "agent-state-path": (["~/.claude/projects/x", "~/.codex/sessions/2", "~/.gemini/history"],
                         ["~/.claude/skills", "~/.codex/config.toml"]),
    "tmp-claude": (["/tmp/claude-1000/scratch"], ["/tmp/claude/x", "/tmp/cache-12"]),
    "lt-tenant-host": (["https://acme-uni.lt.app/i/", "SCHOOL1.LT.APP"],
                       ["https://<tenant>.lt.app", "lt.app", "example.lt.application"]),
    "lt-instance-path": (["/i/0a1b2c3d/student/"], ["/i/<inst>/", "/i/0a1b2c3/", "/i/0A1B2C3G/"]),
    "lt-run-url": (["/courses/123/runs/45678"], ["/courses/1/runs/2", "/courses/123/runs/4567"]),
    "module-id": (["module-c123-r45678.json", "c999-r123456"], ["c1-r102", "c12-r45678", "ac123-r45678"]),
    "panel-id": (["p3-243", "p12-12345"], ["p3-24", "p123-456", "p3-123456"]),
    "unit-code": (["unit ABCD1234 here"], ["ABC1234", "ABCDE1234", "abcd1234", "SHA256"]),
    "d-id": (["see D7.30", "D1.2 and D12.345"], ["D3.js", "D123.4", "DD1.2"]),
    "f-id": (["finding F12 says", "F3"], ["F1234", "#F1F5F9", "AF12", "F12a"]),
    "section-ref": (["§10", "§ 4"], ["section 10", "§x"]),
    "private-doc": (["read PROMPT.md first", "SPEC.md", "NEXT-PROMPT", "decisions/log", "recon/notes",
                     "progress.md"],
                    ["prompt.md", "decision log", "reconnect", "progress bar"]),
    "phase-ref": (["Phase 3 work"], ["phase 3", "Phases 3", "Phase three"]),
    "study-date": (["on 2026-10-24", "2019-01-31"], ["2000-01-01", "2030-01-01", "2026-1-24", "20261024"]),
    "user-said": (["as the user asked", "The user said no", "the user\nprefers dark"],
                  ["the user's quote", "the user's words", "the user agreed", "a user asked"]),
    "email": (["write to someone@mail.test", "a.b+c@sub.example.org"],
              ["chrome-devtools-mcp@1.12.0", "@decorator", "user@localhost"]),
    "lt-request-claim": (["Lt reviewed the logs", "ADInstruments has approved this", "Lt asked for it",
                          "kuraCloud team endorsed"],
                         ["Lt's model answer is permitted", "Lt reviews nothing", "the Lt page asked",
                          "Lt permitted"]),
    "lt-staff": (["Lt staff", "LT  Staff"], ["Lt staffing", "Lt's staff"]),
    "agent-host-stamp": (['host="Claude"'], ['host="Codex"', "host='Claude'"]),
    "session-path": (["https://claude.ai/code/session_01ABC", "claude.ai/artifact/xyz"],
                     ["https://claude.ai/", "claude.com/artifact/x"]),
    "grade": (["graded PASS", "marked FAIL", "Group 42"], ["graded pass", "Group A", "Group 1234"]),
    "secret-token": (["ghp_" + "a" * 36, "sk-" + "b" * 24, "AKIA" + "C" * 16,
                      "-----BEGIN RSA PRIVATE KEY-----"],
                     ["ghp_short", "task-" + "b" * 24, "-----BEGIN PUBLIC KEY-----"]),
    "agent-trailer": (["Co-Authored-By: X <x@example.org>", "claude-session: abc"],
                      ["see Co-Authored-By: lines", "Signed-off-by: dorsaltegmentum"]),
}


@pytest.fixture(scope="module")
def rules(gate):
    return gate.load_rules(gate.PATTERNS_FILE)


def test_every_pattern_has_cases(rules):
    assert {p.id for p in rules.patterns} == set(CASES)


@pytest.mark.parametrize("pid", sorted(CASES))
def test_positive_and_negative(rules, pid):
    rx = next(p.rx for p in rules.patterns if p.id == pid)
    pos, neg = CASES[pid]
    for s in pos:
        assert rx.search(s), f"{pid} misses {s!r}"
    for s in neg:
        assert not rx.search(s), f"{pid} matches {s!r}"


def test_public_file_carries_no_owner_literal(gate):
    """Layer 1 holds generic shapes only; owner literals live in the private layer."""
    text = gate.PATTERNS_FILE.read_text(encoding="utf-8").lower()
    for word in ("logseq", "anki", "canvas", "chrome", "firefox", "obsidian"):
        assert word not in text


@pytest.mark.parametrize("line", [
    "Lt's model answer is permitted",
    "the user's quote",
    "the user's words",
    "Model answers stay ON by default; Lt support pages describe the setting.",
])
def test_review_strings_pass(run_gate, tmp_path, line):
    f = tmp_path / "ok.txt"
    f.write_text(line + "\n", encoding="utf-8")
    r = run_gate("--paths", str(f))
    assert r.returncode == 0, r.stdout + r.stderr


def test_lexicon_review_by_passes_only_in_lexicon(gate):
    """review_by dates pass in shared/lexicon/** on their own line, nowhere else."""
    rules = gate.load_rules(gate.PATTERNS_FILE)
    g = gate.Gate(rules, gate.load_allowlist(gate.ALLOWLIST_FILE, rules), [])
    line = "review_by: 2027-10-24\n"
    assert g.scan_text(line, "x", "shared/lexicon/vocabulary.md") == []
    assert g.scan_text(line, "x", "skills/lt-module-fill/references/_shared/lexicon/a.md") == []
    for path, text in (("docs/guide.md", line), ("shared/lexicon/a.md", "reviewed on 2027-10-24\n")):
        assert [h.pid for h in g.scan_text(text, "x", path)] == ["study-date"]
