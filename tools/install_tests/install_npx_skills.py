#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`npx skills add` install test: the skills CLI finds every skill and installs whole folders.

Runs `npx -y skills@<version> add <copy>` in an empty temp project with a temp HOME and
npm cache. npx needs the npm registry; with no network every check reports SKIP (or
FAIL with --require-network). Telemetry is off (DISABLE_TELEMETRY=1, DO_NOT_TRACK=1).

Checks:
  node                   Node 22.20 or newer (the skills CLI's engines field)
  list                   `add <copy> --list` names every expected skill
  install                `add <copy> --skill '*' -a codex -a claude-code -a cursor -y`
  installed:<skill>      .agents/skills/<skill>/ equals the source folder, MANIFEST.json
                         hashes match, .claude/skills/<skill> resolves
  codex-sees, claude-sees  the installed project is read by codex and claude (no model
                         call; skipped when the binary is absent)

    python3 tools/install_tests/install_npx_skills.py [--skills-cli "node /path/cli.mjs"]
"""
from __future__ import annotations

import re
import shlex
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402
import install_claude_code as icc  # noqa: E402
import install_codex as icx  # noqa: E402

NET_ERR = re.compile(r"ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|ETIMEDOUT|E403|E407|"
                     r"network|proxy|getaddrinfo|unable to get local issuer", re.I)
LIST_NAME = re.compile(r"^[│|]\s{2,}([a-z0-9]+(?:-[a-z0-9]+)*)\s*$")


def node_ok(env, cwd) -> tuple[bool, str]:
    r = c.run(["node", "--version"], cwd, env, timeout=30)
    if r.rc != 0:
        return False, f"node not runnable: {r.tail()}"
    m = re.match(r"v(\d+)\.(\d+)", r.out.strip())
    ver = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return ver >= (22, 20), r.out.strip()


def main() -> int:
    ap = c.base_parser(__doc__.split("\n")[0])
    ap.add_argument("--skills-version", default="1.7.4")
    ap.add_argument("--skills-cli", default=None,
                    help="command that runs the skills CLI (default: npx -y skills@<version>)")
    ap.add_argument("--require-network", action="store_true",
                    help="FAIL, not SKIP, when npx cannot reach the registry")
    ap.add_argument("--codex", default=None)
    ap.add_argument("--claude", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    repo = c.resolve_repo(args.repo)
    report = c.Report("npx-skills")
    cli = shlex.split(args.skills_cli) if args.skills_cli else \
        ["npx", "-y", f"skills@{args.skills_version}"]

    with c.Workspace(repo, args.work, args.keep, "npx") as ws:
        tree = c.tree_skills(ws.repo)
        home = ws.home("home")
        env = c.clean_env(home, {"npm_config_cache": str(home / ".npm"),
                                 "npm_config_registry": "https://registry.npmjs.org",
                                 "npm_config_fetch_retries": "0",
                                 "npm_config_fetch_timeout": "15000",
                                 "npm_config_update_notifier": "false",
                                 "npm_config_fund": "false", "npm_config_audit": "false"},
                          keep=("npm_config_registry", "NPM_CONFIG_REGISTRY"))
        proj = ws.dir("project")
        if not shutil.which("node"):
            report.add("SKIP", "node", "node not on PATH")
            return report.finish(args.json_out)
        ok, ver = node_ok(env, proj)
        report.add("PASS" if ok else "FAIL", "node", f"{ver} (needs 22.20+)")
        report.add("PASS", "tree", f"{len(tree)} SKILL.md in the copy: {', '.join(tree) or 'none'}")

        r = c.run(cli + ["add", ws.repo, "--list"], proj, env, timeout=args.timeout)
        if r.rc != 0:
            text = r.out + r.err
            net = bool(NET_ERR.search(text)) or r.missing or r.timeout
            status = "FAIL" if (args.require_network or not net) else "SKIP"
            report.add(status, "list", ("network or npx unavailable: " if net else "") + f"exit {r.rc}: {r.tail()}")
            return report.finish(args.json_out)
        names = {m.group(1) for line in r.out.splitlines() if (m := LIST_NAME.match(line.rstrip()))}
        found = re.search(r"Found (\d+) skill", r.out)
        report.add("PASS", "list", f"CLI found {found.group(1) if found else '?'} skill(s): {', '.join(sorted(names)) or 'none'}")
        c.compare(report, "list", names, args, tree)

        r = c.run(cli + ["add", ws.repo, "--skill", "*", "-a", "codex", "-a", "claude-code",
                         "-a", "cursor", "-y"], proj, env, timeout=args.timeout)
        report.add("PASS" if r.rc == 0 else "FAIL", "install",
                   f"exit {r.rc}" + ("" if r.rc == 0 else f": {r.tail()}"))
        if r.rc != 0:
            return report.finish(args.json_out)
        lock = proj / "skills-lock.json"
        report.add("PASS" if lock.is_file() else "WARN", "skills-lock", "present" if lock.is_file() else "absent")
        installed = set()
        for name in c.expected(args):
            dst = proj / ".agents" / "skills" / name
            link = proj / ".claude" / "skills" / name
            if name not in tree:
                report.add("SKIP" if args.present_only else "FAIL", f"installed:{name}",
                           "no SKILL.md in the tree yet")
                continue
            if not (dst / "SKILL.md").is_file():
                report.add("FAIL", f"installed:{name}", ".agents/skills/<name>/SKILL.md missing")
                continue
            installed.add(name)
            a, b = c.file_set(tree[name]), c.file_set(dst)
            probs = c.verify_manifest(dst)
            if a == b and not probs and c.same_files(tree[name], dst):
                report.add("PASS", f"installed:{name}", f"{len(b)} files equal the source")
            else:
                miss = sorted(a - b)
                report.add("FAIL", f"installed:{name}",
                           f"{len(miss)} missing (e.g. {', '.join(miss[:3])}), {len(b - a)} extra; {'; '.join(probs[:3])}")
            report.add("PASS" if (link / "SKILL.md").is_file() else "FAIL", f"claude-link:{name}",
                       ".claude/skills entry resolves" if (link / "SKILL.md").is_file() else ".claude/skills entry missing")

        exe = c.find_bin(args.codex, "CODEX_BIN", "codex", ("~/.local/bin/codex",))
        if exe:
            chome = ws.home("codex-home")
            (chome / ".codex").mkdir(exist_ok=True)
            cenv = c.clean_env(chome, {"CODEX_HOME": str(chome / ".codex")})
            skills, _, err = icx.prompt_skills([exe], proj, cenv, 120)
            if skills is None:
                report.add("FAIL", "codex-sees", err)
            else:
                c.compare(report, "codex-sees", set(skills), args, tree)
        else:
            report.add("SKIP", "codex-sees", "codex not found")

        cmd = icc.claude_cmd(args.claude)
        if cmd:
            khome = ws.home("claude-home")
            kenv = c.clean_env(khome, {"CLAUDE_CONFIG_DIR": str(khome / ".claude"),
                                       "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
            init, tail = icc.init_message(cmd + ["-p", "--output-format", "stream-json", "--verbose",
                                                 "--max-turns", "1", "--no-session-persistence",
                                                 "Reply OK."], proj, kenv, 120)
            if init is None:
                report.add("FAIL", "claude-sees", f"no system/init message: {tail}")
            else:
                c.compare(report, "claude-sees", set(init.get("skills", [])), args, tree)
        else:
            report.add("SKIP", "claude-sees", "claude not found")
    return report.finish(args.json_out)


if __name__ == "__main__":
    sys.exit(main())
