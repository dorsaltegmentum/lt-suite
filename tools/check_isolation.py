#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prove each skill runs from its own folder alone, the way a single-skill install gets it.

For every skill: copy skills/<name>/ alone into an empty temp folder, then from a
different working folder run every entry script with --help. The skill's file
set declares the entries: every non-private .py directly in scripts/ or engine/,
including files without a main guard. Empty discovery fails. Where the skill
ships scripts/_shared/ltcore/validate.py, run
``python -c "import ltcore.validate"`` with scripts/_shared on the path. Any
ImportError or non-zero exit fails the skill.

By default the current interpreter runs the scripts. --venv builds a fresh venv per
skill from its requirements.txt first (needs network; CI uses it).

    python3 tools/check_isolation.py                      # every skill
    python3 tools/check_isolation.py --skill lt-module-fill
    python3 tools/check_isolation.py --venv
Exit 0 every skill passes, 1 a failure, 2 usage error.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENTRY_DIRS = ("scripts", "engine")


def entry_scripts(skill_dir: Path) -> list[Path]:
    """Use the skill's file set, never source-code heuristics, to declare entries."""
    out = []
    for sub in ENTRY_DIRS:
        d = skill_dir / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.py")):
            if p.name.startswith("_"):
                continue
            out.append(p)
    return out


def _clean_env(tmp: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTHON", "LT_SUITE_", "VIRTUAL_ENV", "UV_"))}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LT_SUITE_DATA"] = str(tmp / "data")
    env["LT_SUITE_CACHE"] = str(tmp / "cache")
    cfg = tmp / "config.toml"
    cfg.write_text("schema_version = 1\n", encoding="utf-8", newline="\n")
    env["LT_SUITE_CONFIG"] = str(cfg)  # an empty config: defaults only, never the user's file
    env["LT_SUITE_NO_INSTALL"] = "1"
    return env


def build_venv(skill_dir: Path, where: Path) -> tuple[Path | None, str]:
    venv = where / "venv"
    r = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        return None, f"venv creation failed: {r.stderr.strip()[-400:]}"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    req = skill_dir / "requirements.txt"
    if req.is_file() and any(line.strip() and not line.startswith("#")
                             for line in req.read_text(encoding="utf-8").splitlines()):
        r = subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(req)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            return None, f"pip install -r requirements.txt failed: {r.stderr.strip()[-600:]}"
    return py, "venv from requirements.txt"


def check_skill(name: str, *, root: Path, use_venv: bool, timeout: int) -> list[str]:
    src = root / "skills" / name
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="lt-iso-") as t:
        tmp = Path(t)
        dst = tmp / "alone" / name
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                        symlinks=True)
        elsewhere = tmp / "cwd"
        elsewhere.mkdir()
        py: Path | str = sys.executable
        if use_venv:
            vpy, msg = build_venv(dst, tmp)
            if vpy is None:
                return [msg]
            py = vpy
        env = _clean_env(tmp)
        scripts = entry_scripts(dst)
        if not scripts:
            fails.append("entry discovery incomplete: no entry scripts in scripts/ or engine/")
        for s in scripts:
            rel = s.relative_to(dst).as_posix()
            try:
                r = subprocess.run([str(py), str(s), "--help"], cwd=elsewhere, env=env,
                                   capture_output=True, text=True, timeout=timeout, encoding="utf-8")
            except subprocess.TimeoutExpired:
                fails.append(f"{rel} --help did not finish in {timeout} s")
                continue
            if r.returncode != 0:
                tail = (r.stderr or r.stdout).strip().splitlines()[-6:]
                fails.append(f"{rel} --help exited {r.returncode}: " + " | ".join(tail))
            else:
                print(f"  ok {name}/{rel} --help")
        shared = dst / "scripts" / "_shared"
        if (shared / "ltcore" / "validate.py").is_file():
            env2 = dict(env)
            env2["PYTHONPATH"] = str(shared)
            r = subprocess.run([str(py), "-c", "import ltcore.validate"], cwd=elsewhere,
                               env=env2, capture_output=True, text=True, timeout=timeout, encoding="utf-8")
            if r.returncode != 0:
                tail = r.stderr.strip().splitlines()[-6:]
                fails.append("import ltcore.validate failed: " + " | ".join(tail))
            else:
                print(f"  ok {name}: import ltcore.validate")
    return fails


def main(argv: list[str] | None = None) -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", action="append", help="check only this skill (repeatable)")
    ap.add_argument("--venv", action="store_true", help="build a venv per skill from "
                    "requirements.txt instead of using this interpreter")
    ap.add_argument("--timeout", type=int, default=120, help="seconds per script")
    ap.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = a.root.resolve()
    skills_dir = root / "skills"
    names = a.skill or sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
    if not names:
        print("FAIL entry discovery incomplete: no skills")
        return 1
    status = 0
    for n in names:
        if not (skills_dir / n).is_dir():
            print(f"error: no skill folder skills/{n}", file=sys.stderr)
            return 2
        print(f"{n}:")
        fails = check_skill(n, root=root, use_venv=a.venv, timeout=a.timeout)
        for f in fails:
            print(f"FAIL {n}: {f}")
        if fails:
            status = 1
        else:
            print(f"ok {n}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
