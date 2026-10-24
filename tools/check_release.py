#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release checks that no single package can make: placeholders, generated files, history.

1. No "to be decided" placeholder token (T, B, D as one word) in any tracked or
   untracked-not-ignored text file.
2. Every generated file matches its generator: sync_shared.py --check --strict,
   gen_manifests.py --check, gen_fixtures.py --check, gen_config_docs.py --check.
   A missing generator is a failure.
3. Initial history uses the release identity, UTC offsets and no commit trailers.
   --public-history allows contributor identities, offsets and trailers after
   --initial-release-ref when supplied. Session links and session trailers stay banned.
4. --tag requires a matching version, dated changelog and release workflow gates.
   Public checks read no private inputs.

    python3 tools/check_release.py
    python3 tools/check_release.py --skip-generators --skip-history
Exit 0 all checks pass, 1 a check fails, 2 the checker could not run.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "dorsaltegmentum"
EMAIL = "208164922+dorsaltegmentum@users.noreply.github.com"
PLACEHOLDER = re.compile(r"\b" + "T" + "BD" + r"\b")  # split so this file does not match itself
GENERATORS = [
    ("tools/sync_shared.py", ["--check", "--strict", "--quiet"]),
    ("tools/gen_manifests.py", ["--check"]),
    ("tools/gen_fixtures.py", ["--check"]),
    ("tools/gen_config_docs.py", ["--check"]),
]
WALK_SKIP = {".git", ".venv", "dist", "__pycache__", "node_modules", ".pytest_cache"}
AGENT_LINK = re.compile(r"claude\.ai/", re.I)
AGENT_TRAILER = re.compile(r"^Claude-" + r"Session\s*:", re.I | re.M)
REVIEW_MARKER = re.compile(r"<!--\s*" + "VER" + r"IFY\b[\s\S]*?-->", re.I)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def release_files(root: Path) -> list[Path]:
    r = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if r.returncode == 0:
        names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]
        return sorted({root / n for n in names if (root / n).is_file()})
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP]
        out.extend(Path(dirpath) / f for f in filenames)
    return sorted(out)


def check_placeholders(root: Path) -> list[str]:
    fails = []
    for p in release_files(root):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", "replace")
        for n, line in enumerate(text.splitlines(), 1):
            if PLACEHOLDER.search(line):
                fails.append(f"{p.relative_to(root)}:{n}: placeholder token")
        for marker in REVIEW_MARKER.finditer(text):
            line = text.count("\n", 0, marker.start()) + 1
            fails.append(f"{p.relative_to(root)}:{line}: unresolved review marker")
    return fails


def check_generators(root: Path) -> list[str]:
    fails = []
    for script, args in GENERATORS:
        path = root / script
        if not path.is_file():
            fails.append(f"{script}: generator missing")
            continue
        r = subprocess.run([sys.executable, str(path), *args], cwd=root,
                           capture_output=True, text=True)
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
            fails.append(f"{script} {' '.join(args)} exited {r.returncode}: " + " | ".join(tail))
        else:
            print(f"ok {script} {' '.join(args)}")
    return fails


def check_history(root: Path) -> list[str]:
    """Check all reachable history under the strict initial-release policy."""
    return _check_history(root)


def _check_history(root: Path, public: bool = False, initial_ref: str | None = None) -> list[str]:
    if _git(root, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return ["no commits: nothing to release"]
    fmt = ("%H%x1f%an%x1f%ae%x1f%ai%x1f%cn%x1f%ce%x1f%ci%x1f%B%x1f"
           "%(trailers:only,unfold)%x1e")
    r = _git(root, "log", "--all", f"--format={fmt}")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip())
    initial = set()
    if initial_ref:
        baseline = _git(root, "rev-list", "--end-of-options", initial_ref)
        if baseline.returncode != 0:
            raise ValueError("initial release reference does not resolve")
        initial = set(baseline.stdout.decode().splitlines())
    fails = []
    count = 0
    for rec in r.stdout.decode("utf-8", "replace").split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        count += 1
        h, an, ae, ad, cn, ce, cd, body, trailers = rec.split("\x1f", 8)
        short = h[:12]
        strict = not public or h in initial
        if strict and (an, ae) != (NAME, EMAIL):
            fails.append(f"{short}: author is not the release identity")
        if strict and (cn, ce) != (NAME, EMAIL):
            fails.append(f"{short}: committer is not the release identity")
        if strict and not ad.endswith("+0000"):
            fails.append(f"{short}: author date offset is not +0000")
        if strict and not cd.endswith("+0000"):
            fails.append(f"{short}: committer date offset is not +0000")
        if strict and trailers.strip():
            fails.append(f"{short}: message carries a commit trailer")
        if not strict and AGENT_TRAILER.search(body):
            fails.append(f"{short}: message carries an agent session trailer")
        if AGENT_LINK.search(body):
            fails.append(f"{short}: message links an agent session")
    print(f"history: {count} commit(s) checked")
    return fails


def check_tag(root: Path, tag: str) -> list[str]:
    """Require a matching version, dated changelog and authenticated release workflow."""
    fails = []
    with open(root / "pyproject.toml", "rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    if tag != "v" + version:
        fails.append("release tag does not match project version")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    dates = re.findall(r"^## \[" + re.escape(version) + r"\] - (\d{4}-\d{2}-\d{2})\s*$",
                       changelog, re.M)
    try:
        if len(dates) != 1:
            raise ValueError
        datetime.date.fromisoformat(dates[0])
    except ValueError:
        fails.append("release requires one dated changelog heading for the project version")
    workflow_path = root / ".github" / "workflows" / "release.yml"
    if not workflow_path.is_file():
        return fails + ["release workflow is missing"]
    import yaml
    try:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError("release workflow is not valid YAML") from exc
    if not isinstance(workflow, dict):
        raise ValueError("release workflow must be a mapping")
    job = workflow.get("jobs", {}).get("publish", {})
    steps = job.get("steps", [])
    checksums = [i for i, s in enumerate(steps) if "checksums" in s.get("run", "")]
    attest = [i for i, s in enumerate(steps)
              if re.fullmatch(r"actions/attest-build-provenance@[0-9a-f]{40}", s.get("uses", ""))
              and s.get("with", {}).get("subject-path") == "dist/**/*.zip"]
    uploads = [i for i, s in enumerate(steps)
               if re.fullmatch(r"softprops/action-gh-release@[0-9a-f]{40}", s.get("uses", ""))
               and "dist/SHA256SUMS" in s.get("with", {}).get("files", "")]
    if not (checksums and attest and uploads and checksums[0] < attest[0] < uploads[0]):
        fails.append("release workflow requires checksums and provenance before archive upload")
    permissions = job.get("permissions", {})
    if any(permissions.get(p) != "write" for p in ("contents", "id-token", "attestations")):
        fails.append("release workflow requires upload and provenance permissions")
    commands = "\n".join(s.get("run", "") for s in steps[:uploads[0]] if "run" in s) if uploads else ""
    needs = job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    if "checks" not in needs or "release-tag" not in commands or "--tag" not in commands:
        fails.append("release workflow must gate publication on CI, tag ancestry and release checks")
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-generators", action="store_true", help="do not run the generators")
    ap.add_argument("--skip-history", action="store_true", help="do not check commits")
    ap.add_argument("--public-history", action="store_true",
                    help="accept contributor identities, offsets and trailers after the initial release")
    ap.add_argument("--initial-release-ref", help="initial-release history to keep under strict rules")
    ap.add_argument("--tag", help="require matching version, dated changelog and release workflow gates")
    ap.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = a.root.resolve()
    fails: list[str] = []
    try:
        fails += check_placeholders(root)
        if not a.skip_generators:
            fails += check_generators(root)
        if not a.skip_history:
            fails += _check_history(root, a.public_history, a.initial_release_ref)
        if a.tag:
            fails += check_tag(root, a.tag)
    except (OSError, RuntimeError, ValueError, ImportError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for f in fails:
        print(f"FAIL {f}")
    if not fails:
        print("release checks pass")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
