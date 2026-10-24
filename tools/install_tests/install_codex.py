#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Codex CLI install test: skills are discovered on three routes, with no model call.

`codex debug prompt-input` renders the prompt Codex would send, including its
<skills_instructions> list, without contacting a model. Every route runs with a temp
HOME and an empty temp CODEX_HOME, and with CODEX*/OPENAI* variables removed.

Routes:
  marketplace     codex plugin marketplace add <copy>; codex plugin add lt-suite@lt-suite;
                  skills appear as lt-suite:<name>; installed copy equals the source
  agents-skills   <project>/.agents/skills/<name>/ copied from the repo (what
                  `npx skills add` writes); skills appear as <name>
  codex-home      $CODEX_HOME/skills/<name>/ (Codex's user folder); skills appear as <name>
Each loaded skill's description must equal its SKILL.md description (no truncation).

    python3 tools/install_tests/install_codex.py [--codex PATH]
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

SKILL_LINE = re.compile(r"^- ([A-Za-z0-9_.:-]+?): (.*) \(file: ([^)]*)\)\s*$")
ROOT_LINE = re.compile(r"^- `(r\d+)` = `([^`]*)`")


def prompt_skills(cmd, cwd, env, timeout) -> tuple[dict | None, dict, str]:
    """({name: description}, {root id: path}, error) from codex debug prompt-input."""
    r = c.run(cmd + ["debug", "prompt-input", "x"], cwd, env, timeout=timeout)
    if r.rc != 0:
        return None, {}, f"exit {r.rc}: {r.tail()}"
    try:
        items = json.loads(r.out)
    except ValueError:
        return None, {}, f"prompt-input printed no JSON: {r.out[:200]}"
    text = ""
    for it in items:
        for part in it.get("content", []) or []:
            t = part.get("text", "") if isinstance(part, dict) else ""
            if "<skills_instructions>" in t:
                text = t
    if not text:
        return {}, {}, "no <skills_instructions> block (no skills found)"
    skills, roots = {}, {}
    for line in text.splitlines():
        m = SKILL_LINE.match(line)
        if m:
            skills[m.group(1)] = m.group(2)
        m = ROOT_LINE.match(line)
        if m:
            roots[m.group(1)] = m.group(2)
    return skills, roots, ""


def check_route(report, route, cmd, cwd, env, args, tree, prefix="") -> None:
    skills, roots, err = prompt_skills(cmd, cwd, env, args.timeout)
    if skills is None:
        report.add("FAIL", route, err)
        return
    ours = sorted(k for k in skills if k.startswith(prefix) and k[len(prefix):] in tree) if prefix \
        else sorted(k for k in skills if k in tree or k in c.expected(args))
    report.add("PASS", route, f"{len(skills)} skill(s) in the prompt, {len(ours)} from lt-suite; "
               f"{len(roots)} root(s)" + (f"; {err}" if err else ""))
    c.compare(report, route, set(skills), args, tree, prefix=prefix)
    for key in ours:
        name = key[len(prefix):]
        want = " ".join(c.frontmatter(tree[name] / "SKILL.md").get("description", "").split())
        got = " ".join(skills[key].split())
        if got != want:
            report.add("WARN", f"{route}:{name}:description",
                       f"prompt has {len(got)} chars, SKILL.md has {len(want)}")


def main() -> int:
    ap = c.base_parser(__doc__.split("\n")[0])
    ap.add_argument("--codex", default=None,
                    help="codex binary (default: $CODEX_BIN, codex on PATH, ~/.local/bin/codex)")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()
    repo = c.resolve_repo(args.repo)
    report = c.Report("codex")
    exe = c.find_bin(args.codex, "CODEX_BIN", "codex", ("~/.local/bin/codex",))
    if not exe:
        report.add("SKIP", "binary", "codex not found; pass --codex or set CODEX_BIN")
        return report.finish(args.json_out)
    cmd = [exe]

    with c.Workspace(repo, args.work, args.keep, "codex") as ws:
        tree = c.tree_skills(ws.repo)

        def codex_env(tag: str) -> dict:
            home = ws.home(f"home-{tag}")
            (home / ".codex").mkdir(exist_ok=True)
            return c.clean_env(home, {"CODEX_HOME": str(home / ".codex")})

        env = codex_env("marketplace")
        proj = ws.dir("project-marketplace")
        v = c.run(cmd + ["--version"], proj, env, timeout=args.timeout)
        report.add("PASS" if v.rc == 0 else "FAIL", "binary", v.out.strip() or v.tail())
        if v.rc != 0:
            return report.finish(args.json_out)
        report.add("PASS", "tree", f"{len(tree)} SKILL.md in the copy: {', '.join(tree) or 'none'}")

        # Route 1: plugin marketplace from the local path.
        add = c.run(cmd + ["plugin", "marketplace", "add", ws.repo, "--json"], proj, env, args.timeout)
        try:
            name = json.loads(add.out[add.out.index("{"):]).get("marketplaceName")
        except ValueError:
            name = None
        report.add("PASS" if add.rc == 0 and name == c.MARKETPLACE_NAME else "FAIL",
                   "marketplace-add", f"exit {add.rc}, marketplace {name!r}" + ("" if add.rc == 0 else f": {add.tail()}"))
        ref = f"{c.PLUGIN_NAME}@{c.MARKETPLACE_NAME}"
        inst = c.run(cmd + ["plugin", "add", ref], proj, env, args.timeout)
        m = re.search(r"Installed plugin root: (.+)", inst.out + inst.err)
        report.add("PASS" if inst.rc == 0 else "FAIL", "plugin-add",
                   f"exit {inst.rc}" + (f", root {Path(m.group(1).strip()).name}" if m else f": {inst.tail()}"))
        if inst.rc == 0:
            check_route(report, "marketplace", cmd, proj, env, args, tree, prefix=f"{c.PLUGIN_NAME}:")
            if m:
                root = Path(m.group(1).strip())
                for sk, src in tree.items():
                    dst = root / "skills" / sk
                    a, b = c.file_set(src), c.file_set(dst) if dst.is_dir() else set()
                    probs = c.verify_manifest(dst) if dst.is_dir() else ["folder missing"]
                    report.add("PASS" if a == b and not probs and c.same_files(src, dst) else "FAIL", f"installed-copy:{sk}",
                               f"{len(b)} files equal the source" if a == b and not probs and c.same_files(src, dst) else
                               f"{len(a - b)} missing, {len(b - a)} extra, {'; '.join(probs[:3])}")

        # Route 2: project .agents/skills, as written by `npx skills add`.
        env = codex_env("agents")
        proj = ws.dir("project-agents")
        dest = proj / ".agents" / "skills"
        dest.mkdir(parents=True)
        for sk, src in tree.items():
            shutil.copytree(src, dest / sk, symlinks=True)
        check_route(report, "agents-skills", cmd, proj, env, args, tree)

        # Route 3: $CODEX_HOME/skills.
        env = codex_env("home")
        proj = ws.dir("project-home")
        dest = Path(env["CODEX_HOME"]) / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        for sk, src in tree.items():
            shutil.copytree(src, dest / sk, symlinks=True)
        check_route(report, "codex-home", cmd, proj, env, args, tree)
    return report.finish(args.json_out)


if __name__ == "__main__":
    sys.exit(main())
