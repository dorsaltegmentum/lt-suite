#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check each skill folder against the release rules for SKILL.md and folder size.

FAIL (exit 1):
  - no SKILL.md, or no YAML frontmatter
  - name differs from the folder, breaks ^[a-z0-9]+(-[a-z0-9]+)*$, or is over 64 chars
  - description missing or over 200 chars; compatibility over 500 chars
  - name or description holds "<" or ">"
  - license missing or not AGPL-3.0-or-later; metadata.version missing
  - allowed-tools or metadata.phase present
  - body over 500 lines
  - the inline safety block from shared/references/safety.md missing or changed
  - SKILL.md or references/*.md tells the agent to fetch a loopback address
  - a symlink anywhere in the folder; folder over 10 MB
WARN: more than 100 files; frontmatter keys outside the Agent Skills fields.

    python3 tools/check_skills.py                   # every folder under skills/
    python3 tools/check_skills.py skills/lt-module-capture
Exit 0 no FAIL, 1 at least one FAIL, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
LICENSE = "AGPL-3.0-or-later"
MAX_DESCRIPTION = 200
MAX_COMPATIBILITY = 500
MAX_BODY_LINES = 500
MAX_BYTES = 10 * 1024 * 1024
WARN_FILES = 100
BEGIN = "<!-- lt-suite safety: begin -->"
END = "<!-- lt-suite safety: end -->"
LOOPBACK = re.compile(
    r"(?i)\b(fetch|curl|wget|webfetch|web_fetch|requests\.(?:get|post)|urlopen)\b[^\n]{0,160}?"
    r"(127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0)")


def _scalar(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def parse_frontmatter(text: str) -> tuple[dict | None, list[str], str]:
    """(frontmatter, problems, body). A small YAML subset: scalars, quoted strings,
    block scalars (| and >), one level of nested mapping, and simple lists."""
    if not text.startswith("---\n"):
        return None, ["no YAML frontmatter (SKILL.md must start with ---)"], text
    end = text.find("\n---\n", 4)
    if end < 0:
        if text.rstrip().endswith("\n---"):
            end = text.rstrip().rfind("\n---")
        else:
            return None, ["frontmatter has no closing ---"], text
    head = text[4:end].split("\n")
    body = text[end + 5:]
    out: dict = {}
    problems: list[str] = []
    i = 0
    while i < len(head):
        line = head[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            problems.append(f"frontmatter line {i + 2}: unexpected indent")
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if not m:
            problems.append(f"frontmatter line {i + 2}: not a key: value line")
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        i += 1
        block = []
        while i < len(head) and (not head[i].strip() or head[i][0] in " \t"):
            block.append(head[i])
            i += 1
        while block and not block[-1].strip():
            block.pop()
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            lines = [b.strip() for b in block]
            out[key] = ("\n" if rest.startswith("|") else " ").join(lines).strip()
        elif rest == "" and block:
            items = [b.strip() for b in block if b.strip()]
            if all(x.startswith("- ") or x == "-" for x in items):
                out[key] = [_scalar(x[1:]) for x in items]
            else:
                sub: dict = {}
                for x in items:
                    mm = re.match(r"^([A-Za-z0-9_-]+):(.*)$", x)
                    if mm:
                        sub[mm.group(1)] = _scalar(mm.group(2))
                    else:
                        problems.append(f"frontmatter key {key}: cannot read {x!r}")
                out[key] = sub
        elif block:
            out[key] = " ".join([_scalar(rest)] + [b.strip() for b in block]).strip()
        else:
            out[key] = _scalar(rest) if rest else ""
    return out, problems, body


def safety_block(root: Path) -> str | None:
    p = root / "shared" / "references" / "safety.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    a, b = text.find(BEGIN), text.find(END)
    if a < 0 or b < a:
        return None
    return text[a:b + len(END)]


def check_skill(skill_dir: Path, block: str) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"no SKILL.md in {skill_dir.name}"], warns
    text = skill_md.read_text(encoding="utf-8")
    fm, problems, body = parse_frontmatter(text)
    fails += problems
    if fm is not None:
        name = fm.get("name", "")
        if not name:
            fails.append("name missing")
        else:
            if name != skill_dir.name:
                fails.append(f"name {name!r} differs from the folder {skill_dir.name!r}")
            if not NAME_RE.match(name) or len(name) > 64:
                fails.append(f"name {name!r} must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be at most 64 chars")
        desc = fm.get("description", "")
        if not isinstance(desc, str) or not desc:
            fails.append("description missing")
        elif len(desc) > MAX_DESCRIPTION:
            fails.append(f"description is {len(desc)} chars; the limit is {MAX_DESCRIPTION}")
        for k in ("name", "description"):
            if isinstance(fm.get(k), str) and re.search(r"[<>]", fm[k]):
                fails.append(f"{k} contains < or >")
        comp = fm.get("compatibility")
        if isinstance(comp, str) and len(comp) > MAX_COMPATIBILITY:
            fails.append(f"compatibility is {len(comp)} chars; the limit is {MAX_COMPATIBILITY}")
        if fm.get("license") != LICENSE:
            fails.append(f"license must be {LICENSE} (found {fm.get('license')!r})")
        meta = fm.get("metadata")
        if not isinstance(meta, dict) or not meta.get("version"):
            fails.append("metadata.version missing")
        elif "phase" in meta:
            fails.append("metadata.phase is not allowed")
        if "allowed-tools" in fm:
            fails.append("allowed-tools is not allowed (Claude-only, experimental)")
        for k in sorted(set(fm) - SPEC_KEYS):
            warns.append(f"frontmatter key {k!r} is outside the Agent Skills fields")
        nbody = len(body.splitlines())
        if nbody > MAX_BODY_LINES:
            fails.append(f"body is {nbody} lines; the limit is {MAX_BODY_LINES}")
    if block not in text:
        fails.append("inline safety block missing or changed; copy it verbatim from "
                     "shared/references/safety.md")
    docs = [skill_md] + sorted(p for p in (skill_dir / "references").glob("*.md")
                               if p.is_file())
    for d in docs:
        for n, line in enumerate(d.read_text(encoding="utf-8").splitlines(), 1):
            if LOOPBACK.search(line):
                fails.append(f"{d.relative_to(skill_dir).as_posix()}:{n}: "
                             "tells the agent to fetch a loopback address")
    total = 0
    count = 0
    for p in skill_dir.rglob("*"):
        if "__pycache__" in p.parts:
            continue
        if p.is_symlink():
            fails.append(f"symlink {p.relative_to(skill_dir).as_posix()}: "
                         "skill folders carry real files only")
            continue
        if p.is_file():
            count += 1
            total += p.stat().st_size
    if total > MAX_BYTES:
        fails.append(f"folder is {total / 1048576:.1f} MB; the limit is {MAX_BYTES // 1048576} MB")
    if count > WARN_FILES:
        warns.append(f"{count} files (more than {WARN_FILES})")
    return fails, warns


def main(argv: list[str] | None = None) -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills", nargs="*", type=Path, help="skill folders (default: skills/*)")
    ap.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = a.root.resolve()
    block = safety_block(root)
    if block is None:
        print("error: shared/references/safety.md has no inline safety block", file=sys.stderr)
        return 2
    dirs = a.skills or sorted(p for p in (root / "skills").iterdir() if p.is_dir())
    status = 0
    for d in dirs:
        d = d if d.is_absolute() else (Path.cwd() / d)
        if not d.is_dir():
            print(f"error: {d} is not a folder", file=sys.stderr)
            return 2
        fails, warns = check_skill(d, block)
        for w in warns:
            print(f"WARN {d.name}: {w}")
        for f in fails:
            print(f"FAIL {d.name}: {f}")
        if fails:
            status = 1
        else:
            print(f"ok {d.name}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
