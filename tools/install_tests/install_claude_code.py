#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude Code install test: the plugin validates and its skills load, with no model call.

Runs on a temp copy of the repo with a temp HOME and CLAUDE_CONFIG_DIR, and with every
CLAUDE*/ANTHROPIC* variable removed, so the user's config and credentials stay out.

Checks:
  validate-marketplace   claude plugin validate <copy> --strict --json
  validate-plugin        claude plugin validate <copy>/.claude-plugin/plugin.json --strict --json
  plugin-dir-details     claude --plugin-dir <copy> plugin details lt-suite (component inventory)
  plugin-dir-session     claude -p --plugin-dir <copy> --output-format stream-json: the
                         system/init message lists the session's skills. No credentials
                         exist in the temp config, and the process is stopped after that
                         first message, so no prompt reaches a model.
  plugin-zip-details     the same inventory from a plugin .zip (--plugin-zip, or one built here)
  marketplace-install    claude plugin marketplace add <copy>; claude plugin install
                         lt-suite@lt-suite; plugin details; installed copy equals the source
  live (opt-in --live)   one short prompt with the caller's ANTHROPIC_API_KEY or
                         CLAUDE_CODE_OAUTH_TOKEN; asks the model to list lt-suite skills

    python3 tools/install_tests/install_claude_code.py
    python3 tools/install_tests/install_claude_code.py --claude "npx -y @anthropic-ai/claude-code@2.1.293"
"""
from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def claude_cmd(arg: str | None) -> list | None:
    if arg:
        parts = shlex.split(arg)
        return parts if c.find_bin(parts[0], "", parts[0]) else None
    found = c.find_bin(None, "CLAUDE_BIN", "claude")
    return [found] if found else None


def parse_details(text: str) -> tuple[set, set]:
    """Skill names and MCP server names from `claude plugin details` output."""
    skills, mcp, mode = set(), set(), None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(Skills|Agents|Hooks|MCP servers|LSP servers) \(\d+\)\s*(.*)$", s)
        if m:
            mode, rest = m.group(1), m.group(2)
        elif mode in ("Skills", "MCP servers") and s and not s.startswith(("Projected", "Always")):
            rest = s
        else:
            mode = None if not s else mode
            continue
        tokens = re.split(r"[\s,]+", rest.split("(")[0])
        bucket = skills if mode == "Skills" else mcp if mode == "MCP servers" else None
        if bucket is not None:
            bucket.update(t for t in tokens if NAME_RE.match(t))
    return skills, mcp


def details_check(report, check, cmd, cwd, env, args, tree) -> None:
    r = c.run(cmd, cwd, env, timeout=args.timeout)
    if r.rc != 0 or "Component inventory" not in r.out:
        report.add("FAIL", check, f"exit {r.rc}: {r.tail()}")
        return
    skills, mcp = parse_details(r.out)
    report.add("PASS", check, f"inventory lists {len(skills)} skill(s): {', '.join(sorted(skills)) or 'none'}")
    c.compare(report, check, skills, args, tree)
    report.add("PASS" if "lt-chrome" in mcp else "FAIL", f"{check}:mcp-lt-chrome",
               "lt-chrome server listed" if "lt-chrome" in mcp else f"MCP servers seen: {sorted(mcp)}")


def validate_check(report, check, cmd, cwd, env, timeout) -> None:
    r = c.run(cmd, cwd, env, timeout=timeout)
    try:
        data = json.loads(r.out)
    except ValueError:
        report.add("FAIL", check, f"exit {r.rc}, no JSON: {r.tail()}")
        return
    man = data.get("manifest") or {}
    errs = [e.get("message", "") for e in man.get("errors", [])]
    warns = [w.get("message", "") if isinstance(w, dict) else str(w) for w in man.get("warnings", [])]
    contents = data.get("contents") or []
    report.add("PASS" if data.get("success") and r.rc == 0 else "FAIL", check,
               f"exit {r.rc}, target {Path(str(data.get('target', ''))).name}, "
               f"{len(errs)} error(s), {len(warns)} warning(s), {len(contents)} content issue(s)"
               + (f": {errs[0]}" if errs else ""))
    for w in warns[:5]:
        report.add("WARN", f"{check}:warning", w)
    for f in ((data.get("directory") or {}).get("findings") or [])[:10]:
        report.add("WARN", f"{check}:directory", f"{f.get('code')} ({f.get('severity')}): {f.get('title')}")


def init_message(cmd, cwd, env, timeout) -> tuple[dict | None, str]:
    """Start claude, return the first system/init stream-json message, then stop it."""
    try:
        p = subprocess.Popen([str(x) for x in cmd], cwd=str(cwd), env=env, text=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, errors="replace", encoding="utf-8")
    except FileNotFoundError as e:
        return None, str(e)
    q: queue.Queue = queue.Queue()

    def reader():
        for line in p.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    found, seen = None, []
    try:
        while True:
            line = q.get(timeout=timeout)
            if line is None:
                break
            seen.append(line.strip()[:200])
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "system" and d.get("subtype") == "init":
                found = d
                break
    except queue.Empty:
        seen.append(f"timeout after {timeout}s")
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    c.COMMANDS.append({"argv": [str(x) for x in cmd], "cwd": str(cwd),
                       "exit": p.returncode, "stopped_after_init": found is not None})
    err = (p.stderr.read() if p.stderr else "")[-300:]
    return found, " | ".join(seen[-3:]) + (" | " + err.strip() if err.strip() else "")


def main() -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = c.base_parser(__doc__.split("\n")[0])
    ap.add_argument("--claude", default=None,
                    help="claude command (default: $CLAUDE_BIN or claude on PATH); may be an npx line")
    ap.add_argument("--plugin-zip", default=None, help="plugin zip from build_dist.py to load")
    ap.add_argument("--live", action="store_true",
                    help="send one short prompt using ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()
    repo = c.resolve_repo(args.repo)
    report = c.Report("claude-code")
    cmd = claude_cmd(args.claude)
    if not cmd:
        report.add("SKIP", "binary", "claude not found; pass --claude or set CLAUDE_BIN")
        return report.finish(args.json_out)

    with c.Workspace(repo, args.work, args.keep, "claude") as ws:
        tree = c.tree_skills(ws.repo)
        home = ws.home("home")
        env = c.clean_env(home, {"CLAUDE_CONFIG_DIR": str(home / ".claude"),
                                 "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        proj = ws.dir("project")
        v = c.run(cmd + ["--version"], proj, env, timeout=args.timeout)
        report.add("PASS" if v.rc == 0 else "FAIL", "binary", v.out.strip() or v.tail())
        if v.rc != 0:
            return report.finish(args.json_out)
        report.add("PASS", "tree", f"{len(tree)} SKILL.md in the copy: {', '.join(tree) or 'none'}")

        validate_check(report, "validate-marketplace",
                       cmd + ["plugin", "validate", ws.repo, "--strict", "--json"], proj, env, args.timeout)
        validate_check(report, "validate-plugin",
                       cmd + ["plugin", "validate", ws.repo / ".claude-plugin" / "plugin.json",
                              "--strict", "--json"], proj, env, args.timeout)

        details_check(report, "plugin-dir-details",
                      cmd + ["--plugin-dir", ws.repo, "plugin", "details", c.PLUGIN_NAME],
                      proj, env, args, tree)

        session = cmd + ["-p", "--plugin-dir", ws.repo, "--output-format", "stream-json",
                         "--verbose", "--max-turns", "1", "--no-session-persistence", "Reply OK."]
        init, tail = init_message(session, proj, env, args.timeout)
        if init is None:
            report.add("FAIL", "plugin-dir-session", f"no system/init message: {tail}")
        else:
            plugins = [p for p in init.get("plugins", []) if p.get("name") == c.PLUGIN_NAME]
            report.add("PASS" if plugins else "FAIL", "plugin-dir-session",
                       f"apiKeySource={init.get('apiKeySource')}, plugin "
                       + (f"{plugins[0].get('source')} {plugins[0].get('version')}" if plugins else "not loaded"))
            c.compare(report, "plugin-dir-session", set(init.get("skills", [])), args, tree,
                      prefix=f"{c.PLUGIN_NAME}:")

        if args.plugin_zip:
            pz, how = Path(args.plugin_zip).resolve(), "given"
        else:
            pz, how = ws.root / "zips" / f"{c.PLUGIN_NAME}-plugin.zip", "built here from the copy"
            c.build_plugin_zip(ws.repo, pz)
        report.add("PASS" if pz.is_file() else "FAIL", "plugin-zip", f"{pz.name} ({how})")
        if pz.is_file():
            details_check(report, "plugin-zip-details",
                          cmd + ["--plugin-dir", pz, "plugin", "details", c.PLUGIN_NAME],
                          proj, env, args, tree)

        add = c.run(cmd + ["plugin", "marketplace", "add", ws.repo, "--json"], proj, env, args.timeout)
        ok = add.rc == 0 and '"outcome":"ok"' in add.out.replace(" ", "")
        report.add("PASS" if ok else "FAIL", "marketplace-add", add.out.strip()[-200:] or add.tail())
        ref = f"{c.PLUGIN_NAME}@{c.MARKETPLACE_NAME}"
        inst = c.run(cmd + ["plugin", "install", ref, "--json"], proj, env, args.timeout)
        ok = inst.rc == 0 and '"outcome":"ok"' in inst.out.replace(" ", "")
        report.add("PASS" if ok else "FAIL", "marketplace-install", inst.out.strip()[-200:] or inst.tail())
        if ok:
            details_check(report, "installed-details", cmd + ["plugin", "details", ref], proj, env, args, tree)
            lst = c.run(cmd + ["plugin", "list", "--json"], proj, env, args.timeout)
            try:
                entry = next(e for e in json.loads(lst.out) if e.get("id") == ref)
                installed = Path(entry["installPath"])
            except (ValueError, StopIteration, KeyError) as e:
                report.add("FAIL", "installed-copy", f"plugin list --json unreadable: {e}")
            else:
                for name, src in tree.items():
                    dst = installed / "skills" / name
                    a, b = c.file_set(src), c.file_set(dst) if dst.is_dir() else set()
                    probs = c.verify_manifest(dst) if dst.is_dir() else ["folder missing"]
                    if a == b and not probs and c.same_files(src, dst):
                        report.add("PASS", f"installed-copy:{name}", f"{len(b)} files equal the source")
                    else:
                        report.add("FAIL", f"installed-copy:{name}",
                                   f"{len(a - b)} missing, {len(b - a)} extra, {'; '.join(probs[:3])}")

        if args.live:
            keys = [k for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN") if os.environ.get(k)]
            if not keys:
                report.add("SKIP", "live", "--live needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN")
            else:
                live_env = c.clean_env(home, {"CLAUDE_CONFIG_DIR": str(home / ".claude-live")}, keep=tuple(keys))
                r = c.run(cmd + ["-p", "--plugin-dir", ws.repo, "--max-turns", "1",
                                 "--no-session-persistence", "--output-format", "json",
                                 "List the names of your skills that start with lt-suite:, "
                                 "comma-separated, nothing else."], proj, live_env, args.timeout)
                try:
                    text = json.loads(r.out).get("result", "")
                except ValueError:
                    text = r.out
                names = set(re.findall(r"lt-suite:([a-z0-9-]+)", text))
                report.add("PASS" if r.rc == 0 else "FAIL", "live", f"exit {r.rc}: {text[:200]}")
                c.compare(report, "live", names, args, tree)
    return report.finish(args.json_out)


if __name__ == "__main__":
    sys.exit(main())
