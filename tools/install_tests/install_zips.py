#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-skill zip test: each zip works alone after extraction into an empty folder.

Zips come from tools/build_dist.py when the copy has it (or from --dist), else this
script builds claude.ai-layout zips (one top folder per skill) from the copy. Every
zip with exactly one SKILL.md is extracted into its own empty folder, away from the
repo, and checked:

  layout          SKILL.md at the top or one folder down; no symlinks, no '..' members
  files           LICENSE.txt, NOTICE.txt and requirements.txt present (DESIGN section 1)
  refs            every scripts/, references/, assets/, engine/, examples/ path named in
                  SKILL.md exists in the zip
  manifest        scripts/_shared/MANIFEST.json hashes match
  help:<entry>    `python3 scripts/<entry> --help` exits 0, run from the skill folder and
                  again by absolute path from another empty folder, with -E -s -B, a temp
                  HOME and LT_SUITE_DATA, under an audit hook that fails any open() inside
                  the source repo (_probe.py)
  import-validate `import ltcore.validate` with scripts/_shared on sys.path, where shipped

A missing third-party module (jsonschema, Pillow, ...) is WARN, since the setup skill's
bootstrap installs those; --strict-deps makes it FAIL, --python picks an interpreter
that has them. Any other missing module or file is FAIL.

    python3 tools/install_tests/install_zips.py [--dist DIR] [--python PATH]
"""
from __future__ import annotations

import re
import stat
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

PROBE = Path(__file__).resolve().parent / "_probe.py"
MAIN_RE = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", re.M)
REF_RE = re.compile(r"(?<![\w/.<-])((?:scripts|references|assets|engine|examples)/[A-Za-z0-9_./-]*[A-Za-z0-9_/])")
MISSING_MOD = re.compile(r"No module named '([\w.]+)'")


def entries(skill: Path) -> list:
    out = []
    for sub in ("scripts", "engine"):
        for p in sorted((skill / sub).glob("*.py")) if (skill / sub).is_dir() else []:
            if not p.name.startswith("_") and MAIN_RE.search(p.read_text(encoding="utf-8", errors="replace")):
                out.append(p)
    return out


def extract(zp: Path, dest: Path) -> list:
    """Extract safely; return problems (unsafe or symlink members)."""
    problems = []
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0]:
                problems.append(f"unsafe member {name}")
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                problems.append(f"symlink member {name}")
                continue
            zf.extract(info, dest)
    return problems


def classify_failure(text: str) -> tuple[str, str]:
    mods = MISSING_MOD.findall(text)
    tops = {m.split(".")[0] for m in mods}
    if "LT_PROBE_FORBIDDEN_OPEN" in text:
        return "escape", text[text.index("LT_PROBE_FORBIDDEN_OPEN"):][:240]
    if tops and tops <= c.THIRD_PARTY_IMPORTS:
        return "dependency", "missing " + ", ".join(sorted(tops))
    if tops:
        return "self", "missing module " + ", ".join(sorted(tops - c.THIRD_PARTY_IMPORTS))
    hits = sorted(m for m in c.THIRD_PARTY_IMPORTS if re.search(rf"\b{re.escape(m)}\b", text))
    if hits and re.search(r"bootstrap|install|missing", text, re.I):
        return "dependency", "bootstrap reports " + ", ".join(hits)
    last = [ln for ln in text.strip().splitlines() if ln.strip()][-1:] or ["no output"]
    return "other", last[0][:240]


def run_entry(py, script: Path, skill: Path, ws, env) -> tuple[bool, str, str]:
    """(ok, kind, detail) over two runs: relative from the skill folder, absolute from elsewhere."""
    rel = script.relative_to(skill).as_posix()
    elsewhere = ws.dir("elsewhere")
    for cwd, target in ((skill, rel), (elsewhere, str(script))):
        r = c.run([py, "-E", "-s", "-B", PROBE, target, "--help"], cwd, env, timeout=120)
        if r.rc != 0 or not (r.out.strip() or r.err.strip()):
            kind, why = classify_failure(r.err + "\n" + r.out)
            where = "skill folder" if cwd == skill else "other folder"
            return False, kind, f"exit {r.rc} from {where}: {why}"
    return True, "", "exit 0 from the skill folder and from another folder"


def main() -> int:
    ap = c.base_parser(__doc__.split("\n")[0])
    ap.add_argument("--dist", default=None, help="folder of zips already built (skips building)")
    ap.add_argument("--builder", choices=("auto", "dist", "harness"), default="auto",
                    help="auto: tools/build_dist.py when present, else build here")
    ap.add_argument("--python", default=sys.executable, help="interpreter for the --help runs")
    ap.add_argument("--strict-deps", action="store_true", help="a missing third-party module is FAIL")
    args = ap.parse_args()
    repo = c.resolve_repo(args.repo)
    report = c.Report("zips")

    with c.Workspace(repo, args.work, args.keep, "zips") as ws:
        tree = c.tree_skills(ws.repo)
        report.add("PASS", "tree", f"{len(tree)} SKILL.md in the copy: {', '.join(tree) or 'none'}")
        for name in c.expected(args):
            if name not in tree:
                report.add("SKIP" if args.present_only else "FAIL", f"zip:{name}", "no SKILL.md in the tree yet")

        zips: list = []
        if args.dist:
            zips = sorted(Path(args.dist).resolve().rglob("*.zip"))
            report.add("PASS" if zips else "FAIL", "build", f"{len(zips)} zip(s) from --dist")
        else:
            bd = ws.repo / "tools" / "build_dist.py"
            if args.builder in ("auto", "dist") and bd.is_file():
                out = ws.dir("dist")
                r = c.run([sys.executable, bd, "--out", out], ws.repo, c.clean_env(ws.home("home-build")), timeout=600)
                zips = sorted(out.rglob("*.zip"))
                report.add("PASS" if r.rc == 0 and zips else "FAIL", "build",
                           f"tools/build_dist.py exit {r.rc}, {len(zips)} zip(s)" + ("" if r.rc == 0 else f": {r.tail()}"))
            elif args.builder == "dist":
                report.add("FAIL", "build", "tools/build_dist.py not in the tree yet")
            if not zips and args.builder == "dist":
                return report.finish(args.json_out)
            if not zips:
                out = ws.dir("harness-zips")
                for name, folder in tree.items():
                    c.build_skill_zip(folder, out / f"{name}.zip")
                zips = sorted(out.glob("*.zip"))
                report.add("WARN" if bd.is_file() else "PASS", "build-fallback",
                           f"{len(zips)} claude.ai-layout zip(s) built by the harness"
                           + ("" if bd.is_file() else " (tools/build_dist.py not in the tree yet)"))

        env = c.clean_env(ws.home("home-run"), {"LT_SUITE_DATA": str(ws.dir("lt-data")),
                                                 "LT_SUITE_CACHE": str(ws.dir("lt-cache")),
                                                 "LT_SUITE_CONFIG": str(ws.root / "no-config.toml"),
                                                 "LT_PROBE_FORBID": f"{repo}{c.os.pathsep}{ws.repo}"})
        for zp in zips:
            with zipfile.ZipFile(zp) as zf:
                skill_mds = [n for n in zf.namelist() if n.endswith("SKILL.md") and n.count("/") <= 1]
                names = zf.namelist()
            label = zp.stem
            if len(skill_mds) != 1:
                kind = "plugin zip" if any(n == ".claude-plugin/plugin.json" or
                    n.endswith("/.claude-plugin/plugin.json") for n in names) else "not a skill zip"
                report.add("PASS" if kind == "plugin zip" else "WARN", f"zip:{label}", f"{kind}, {len(names)} members; skipped here")
                continue
            dest = ws.dir(f"unzip/{label}")
            problems = extract(zp, dest)
            skill = (dest / skill_mds[0]).parent
            fm = c.frontmatter(skill / "SKILL.md")
            bad_name = skill != dest and fm.get("name") != skill.name
            report.add("FAIL" if problems or bad_name else "PASS", f"{label}:layout",
                       f"{len(names)} members, SKILL.md at {skill_mds[0]}"
                       + (f"; {'; '.join(problems[:3])}" if problems else "")
                       + (f"; frontmatter name {fm.get('name')!r} differs from folder" if bad_name else ""))
            pyc = [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
            if pyc:
                report.add("WARN", f"{label}:bytecode", f"{len(pyc)} cached bytecode member(s)")
            lacking = [f for f in ("LICENSE.txt", "NOTICE.txt", "requirements.txt") if not (skill / f).is_file()]
            report.add("FAIL" if lacking else "PASS", f"{label}:files",
                       "missing " + ", ".join(lacking) if lacking else "LICENSE.txt, NOTICE.txt, requirements.txt present")
            text = (skill / "SKILL.md").read_text(encoding="utf-8", errors="replace")
            refs = sorted(set(REF_RE.findall(text)))
            gone = [r for r in refs if not (skill / r.rstrip("/")).exists()]
            report.add("FAIL" if gone else "PASS", f"{label}:refs",
                       f"{len(gone)} of {len(refs)} referenced path(s) missing: {', '.join(gone[:5])}" if gone
                       else f"{len(refs)} referenced path(s) all present")
            probs = c.verify_manifest(skill)
            has_mf = (skill / "scripts" / "_shared" / "MANIFEST.json").is_file()
            if (skill / "scripts").is_dir():
                report.add("FAIL" if probs or not has_mf else "PASS", f"{label}:manifest",
                           "; ".join(probs[:3]) if probs else ("hashes match" if has_mf else "scripts/_shared/MANIFEST.json missing"))
            found = entries(skill)
            if not found:
                report.add("SKIP" if not (skill / "scripts").is_dir() else "WARN", f"{label}:help",
                           "no entry script (a scripts/ or engine/ .py with a __main__ block)")
            for script in found:
                ok, kind, detail = run_entry(args.python, script, skill, ws, env)
                status = "PASS" if ok else ("WARN" if kind == "dependency" and not args.strict_deps else "FAIL")
                report.add(status, f"{label}:help:{script.relative_to(skill).as_posix()}",
                           (f"[{kind}] " if kind else "") + detail)
            if (skill / "scripts" / "_shared" / "ltcore" / "validate.py").is_file():
                r = c.run([args.python, "-E", "-s", "-B", "-c",
                           "import sys; sys.path.insert(0, 'scripts/_shared'); import ltcore.validate"],
                          skill, env, timeout=120)
                kind, why = classify_failure(r.err) if r.rc else ("", "")
                status = "PASS" if r.rc == 0 else ("WARN" if kind == "dependency" and not args.strict_deps else "FAIL")
                report.add(status, f"{label}:import-validate", "imports" if r.rc == 0 else f"[{kind}] {why}")
    return report.finish(args.json_out)


if __name__ == "__main__":
    sys.exit(main())
