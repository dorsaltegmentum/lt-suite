#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gemini CLI and Antigravity install test: runs the CLIs when present, else prints MANUAL steps.

Static checks (always): the files each platform reads exist and parse.
  Gemini CLI:   gemini-extension.json (name, version, contextFileName target present),
                skills/<name>/SKILL.md
  Antigravity:  plugin.json (name), mcp_config.json, rules/*.md with `trigger: always_on`,
                skills/<name>/SKILL.md
CLI checks (only when the binary is found; temp HOME, no credentials, no prompt):
  gemini --version; gemini extensions validate <copy>; gemini extensions link <copy>;
  gemini extensions list; gemini skills list --all
  agy --version; agy plugin validate <copy>; agy plugin install <copy>; agy plugin list --json
Without a binary each CLI step is reported as MANUAL with the exact command.

    python3 tools/install_tests/install_gemini_agy.py
    python3 tools/install_tests/install_gemini_agy.py --gemini "npx -y @google/gemini-cli@0.65.0"
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

GEMINI_MANUAL = [
    ("gemini-validate", "gemini extensions validate <repo>"),
    ("gemini-link", "gemini extensions link <repo>   (or: gemini extensions install <repo>)"),
    ("gemini-list", "gemini extensions list   -> lt-suite 0.2.3, enabled"),
    ("gemini-skills", "gemini skills list --all   -> the 6 lt-suite skills"),
    ("gemini-session", "gemini, then /skills list   -> the 6 skills; /mcp   -> lt-chrome with input tools excluded"),
]
AGY_MANUAL = [
    ("agy-validate", "agy plugin validate <repo>   (record any schema rejection)"),
    ("agy-install", "agy plugin install <repo>"),
    ("agy-list", "agy plugin list --json   -> lt-suite enabled"),
    ("agy-session", "agy, then /plugin (Installed tab)   -> 6 skills, rules/lt-suite-safety.md always on"),
]


def tool_cmd(arg: str | None, envvar: str, name: str) -> list | None:
    if arg:
        parts = shlex.split(arg)
        return parts if c.find_bin(parts[0], "", parts[0]) else None
    found = c.find_bin(None, envvar, name)
    return [found] if found else None


def load_json(report, check, path: Path) -> dict | None:
    if not path.is_file():
        report.add("FAIL", check, f"{path.name} missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        report.add("FAIL", check, f"{path.name} is not JSON: {e}")
        return None


def static_checks(report, repo: Path) -> None:
    ext = load_json(report, "gemini-static:manifest", repo / "gemini-extension.json")
    if ext is not None:
        missing = [k for k in ("name", "version", "description") if not ext.get(k)]
        ctx = ext.get("contextFileName")
        ctx_list = ctx if isinstance(ctx, list) else [ctx] if ctx else []
        absent = [f for f in ctx_list if not (repo / f).is_file()]
        ok = not missing and ctx_list and not absent
        report.add("PASS" if ok else "FAIL", "gemini-static:manifest",
                   f"name {ext.get('name')!r}, version {ext.get('version')!r}"
                   + (f"; missing {missing}" if missing else "")
                   + (f"; context file absent: {absent}" if absent else "")
                   + ("; no contextFileName" if not ctx_list else ""))
    plug = load_json(report, "agy-static:plugin.json", repo / "plugin.json")
    if plug is not None:
        report.add("PASS" if plug.get("name") == c.PLUGIN_NAME else "FAIL", "agy-static:plugin.json",
                   f"name {plug.get('name')!r}")
    mcp = load_json(report, "agy-static:mcp_config.json", repo / "mcp_config.json")
    if mcp is not None:
        servers = mcp.get("mcpServers", {})
        report.add("PASS" if "lt-chrome" in servers else "FAIL", "agy-static:mcp_config.json",
                   f"servers {sorted(servers)}")
    rules = sorted((repo / "rules").glob("*.md")) if (repo / "rules").is_dir() else []
    on = [r.name for r in rules if re.search(r"^trigger:\s*always_on\s*$", r.read_text(encoding="utf-8"), re.M)]
    report.add("PASS" if on else "FAIL", "agy-static:rules",
               f"always-on rule file(s): {', '.join(on)}" if on else "no rules/*.md with trigger: always_on")


def cli_steps(report, prefix, cmd, steps, cwd, env, timeout, expect_names) -> None:
    for check, argv, needle in steps:
        r = c.run(cmd + argv, cwd, env, timeout=timeout)
        text = r.out + "\n" + r.err
        ok = r.rc == 0 and (needle is None or re.search(needle, text))
        report.add("PASS" if ok else "FAIL", f"{prefix}-{check}",
                   f"exit {r.rc}" + ("" if ok else f": {r.tail()}"))
        if check == "skills" and r.rc == 0:
            found = {n for n in expect_names if re.search(rf"\b{re.escape(n)}\b", text)}
            for n in expect_names:
                report.add("PASS" if n in found else "FAIL", f"{prefix}-skills:{n}",
                           "listed" if n in found else "not listed")


def main() -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = c.base_parser(__doc__.split("\n")[0])
    ap.add_argument("--gemini", default=None, help="gemini command (default: $GEMINI_BIN or gemini on PATH)")
    ap.add_argument("--agy", default=None, help="agy command (default: $AGY_BIN or agy on PATH)")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    repo = c.resolve_repo(args.repo)
    report = c.Report("gemini-agy")

    with c.Workspace(repo, args.work, args.keep, "gemini") as ws:
        tree = c.tree_skills(ws.repo)
        report.add("PASS", "tree", f"{len(tree)} SKILL.md in the copy: {', '.join(tree) or 'none'}")
        static_checks(report, ws.repo)
        want = [n for n in c.expected(args) if n in tree]
        proj = ws.dir("project")

        gem = tool_cmd(args.gemini, "GEMINI_BIN", "gemini")
        if gem:
            env = c.clean_env(ws.home("home-gemini"), {"GEMINI_CLI_TRUST_WORKSPACE": "true"})
            cli_steps(report, "gemini", gem, [
                ("version", ["--version"], None),
                ("validate", ["extensions", "validate", str(ws.repo)], None),
                ("link", ["extensions", "link", str(ws.repo)], None),
                ("list", ["extensions", "list"], re.escape(c.PLUGIN_NAME)),
                ("skills", ["skills", "list", "--all"], None),
            ], proj, env, args.timeout, want)
        else:
            for check, line in GEMINI_MANUAL:
                report.add("MANUAL", check, line)

        agy = tool_cmd(args.agy, "AGY_BIN", "agy")
        if agy:
            env = c.clean_env(ws.home("home-agy"))
            cli_steps(report, "agy", agy, [
                ("version", ["--version"], None),
                ("validate", ["plugin", "validate", str(ws.repo)], None),
                ("install", ["plugin", "install", str(ws.repo)], None),
                ("list", ["plugin", "list", "--json"], re.escape(c.PLUGIN_NAME)),
            ], proj, env, args.timeout, want)
            report.add("MANUAL", "agy-session", AGY_MANUAL[-1][1])
        else:
            for check, line in AGY_MANUAL:
                report.add("MANUAL", check, line)
    return report.finish(args.json_out)


if __name__ == "__main__":
    sys.exit(main())
