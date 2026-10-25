#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run every install test and print one summary table.

Platforms, in order: claude-code, codex, npx-skills, zips, gemini-agy. Each test copies
the repo into its own temp folder, so the tree under test is never written. No test
sends a prompt to a model unless --live is given (Claude Code only, one short prompt).

    python3 tools/install_tests/run_all.py
    python3 tools/install_tests/run_all.py --only zips,codex --present-only
    python3 tools/install_tests/run_all.py --json-out install-results.json

Exit 0 when no test reports FAIL, 1 on any FAIL, 2 when a test crashed or on a usage error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = {
    "claude-code": ("install_claude_code.py", {"claude", "live", "plugin_zip"}),
    "codex": ("install_codex.py", {"codex"}),
    "npx-skills": ("install_npx_skills.py", {"codex", "claude", "require_network", "skills_cli"}),
    "zips": ("install_zips.py", {"python", "strict_deps", "dist"}),
    "gemini-agy": ("install_gemini_agy.py", {"gemini", "agy"}),
}
COMMON = ("repo", "work", "expect")
COMMON_FLAGS = ("keep", "present_only")


def main() -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo")
    ap.add_argument("--work")
    ap.add_argument("--expect")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--present-only", action="store_true")
    ap.add_argument("--only", help="comma-separated platforms: " + ",".join(TESTS))
    ap.add_argument("--skip", default="", help="comma-separated platforms to leave out")
    ap.add_argument("--json-out")
    ap.add_argument("--claude")
    ap.add_argument("--codex")
    ap.add_argument("--gemini")
    ap.add_argument("--agy")
    ap.add_argument("--skills-cli")
    ap.add_argument("--python")
    ap.add_argument("--dist")
    ap.add_argument("--plugin-zip")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--strict-deps", action="store_true")
    ap.add_argument("--require-network", action="store_true")
    args = ap.parse_args()
    chosen = [p.strip() for p in (args.only or ",".join(TESTS)).split(",") if p.strip()]
    skip = {p.strip() for p in args.skip.split(",") if p.strip()}
    unknown = [p for p in chosen + sorted(skip) if p not in TESTS]
    if unknown:
        print(f"usage error: unknown platform(s) {unknown}; choose from {', '.join(TESTS)}", file=sys.stderr)
        return 2

    summary, crashed = [], False
    with tempfile.TemporaryDirectory(prefix="lt-install-results-") as tmp:
        for name in (p for p in chosen if p not in skip):
            script, extras = TESTS[name]
            out = Path(tmp) / f"{name}.json"
            cmd = [sys.executable, str(HERE / script), "--json-out", str(out)]
            for key in COMMON + tuple(sorted(extras)):
                val = getattr(args, key, None)
                if isinstance(val, bool):
                    if val:
                        cmd.append("--" + key.replace("_", "-"))
                elif val:
                    cmd += ["--" + key.replace("_", "-"), str(val)]
            for key in COMMON_FLAGS:
                if getattr(args, key):
                    cmd.append("--" + key.replace("_", "-"))
            print(f"\n## {name}", flush=True)
            rc = subprocess.call(cmd)
            try:
                data = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                crashed = True
                data = {"platform": name, "counts": {}, "results": [], "crashed": True}
            data["exit"] = rc
            summary.append(data)

    print("\n| platform | PASS | FAIL | WARN | SKIP | MANUAL | exit |")
    print("|---|---|---|---|---|---|---|")
    for d in summary:
        k = d.get("counts", {})
        print(f"| {d['platform']} | {k.get('PASS', 0)} | {k.get('FAIL', 0)} | {k.get('WARN', 0)} | "
              f"{k.get('SKIP', 0)} | {k.get('MANUAL', 0)} | {'crashed' if d.get('crashed') else d['exit']} |")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    if crashed:
        return 2
    return 1 if any(d.get("counts", {}).get("FAIL") for d in summary) else 0


if __name__ == "__main__":
    sys.exit(main())
