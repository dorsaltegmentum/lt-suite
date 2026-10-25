# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helpers for the install tests in tools/install_tests/.

Every test copies the repo into a temp folder first and works only on that copy.
Agent CLIs run with a temp HOME and temp config folders, so the user's real
config, plugin cache and credentials are never read or written. No test sends a
prompt to a model unless the user passes --live to the Claude Code test.

Result lines: PASS, FAIL, WARN, SKIP, MANUAL. Exit 0 when no FAIL, 1 on a FAIL,
2 on a usage error.
"""
from __future__ import annotations

import argparse
import filecmp
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent.parent  # tools/install_tests/ -> repo root

EXPECTED_SKILLS = (
    "lt-suite-setup",
    "lt-module-capture",
    "lt-module-answer",
    "lt-module-fill",
    "lt-module-export",
    "mechanistic-diagram",
)
PLUGIN_NAME = "lt-suite"
MARKETPLACE_NAME = "lt-suite"

# Folders never copied into the temp repo: build output, caches, local venvs.
COPY_IGNORE = (".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
               ".ruff_cache", "node_modules", "dist", "*.pyc")

# Import names of third-party packages (ltcore.DIST_TO_IMPORT plus PyYAML). A missing
# one is a dependency gap. Any other missing module is a self-containment failure.
THIRD_PARTY_IMPORTS = {"jsonschema", "referencing", "PIL", "numpy", "fontTools",
                       "reportlab", "pypdfium2", "scipy", "pikepdf", "fitz", "cv2",
                       "sklearn", "yaml", "rpds", "attr", "attrs", "cffi", "pycparser", "packaging"}

# Environment prefixes dropped before running an agent CLI: credentials, parent
# agent session state, Python and lt-suite settings.
ENV_DROP_PREFIXES = ("CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "GEMINI", "GOOGLE_",
                     "PYTHON", "VIRTUAL_ENV", "UV_", "LT_SUITE_", "npm_config_",
                     "NPM_CONFIG_", "XDG_", "GH_", "GITHUB_TOKEN")

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
STATUSES = ("PASS", "FAIL", "WARN", "SKIP", "MANUAL")
COMMANDS: list[dict] = []


@dataclass
class Report:
    platform: str
    items: list = field(default_factory=list)

    def add(self, status: str, check: str, detail: str = "") -> None:
        assert status in STATUSES, status
        detail = " ".join(str(detail).split())
        self.items.append({"status": status, "check": f"{self.platform}/{check}",
                           "detail": detail})
        print(f"{status:<6} {self.platform}/{check}" + (f"  {detail}" if detail else ""),
              flush=True)

    def counts(self) -> dict:
        out = {s: 0 for s in STATUSES}
        for it in self.items:
            out[it["status"]] += 1
        return out

    def finish(self, json_out: str | None) -> int:
        c = self.counts()
        print(f"# {self.platform}: " + ", ".join(f"{k} {v}" for k, v in c.items() if v))
        if json_out:
            Path(json_out).write_text(json.dumps(
                {"platform": self.platform, "counts": c, "results": self.items,
                 "commands": COMMANDS},
                indent=1) + "\n", encoding="utf-8", newline="\n")
        return 1 if c["FAIL"] else 0


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--repo", default=None,
                   help="repo root to test (copied first; default: two folders up)")
    p.add_argument("--work", default=None,
                   help="empty folder for temp files (default: a new folder in $TMPDIR)")
    p.add_argument("--keep", action="store_true", help="keep the temp folder")
    p.add_argument("--json-out", default=None, help="write results as JSON to this file")
    p.add_argument("--expect", default=",".join(EXPECTED_SKILLS),
                   help="comma-separated skill names that must load")
    p.add_argument("--present-only", action="store_true",
                   help="SKIP, not FAIL, an expected skill whose SKILL.md is not in the tree yet")
    return p


def resolve_repo(arg: str | None) -> Path:
    repo = Path(arg).expanduser().resolve() if arg else DEFAULT_REPO
    if not (repo / "skills").is_dir() or not (repo / ".claude-plugin").is_dir():
        sys.exit(f"usage error: {repo} has no skills/ or .claude-plugin/; pass --repo")
    return repo


class Workspace:
    """Temp root with a copy of the repo at <root>/repo. Removed on exit unless kept."""

    def __init__(self, repo: Path, work: str | None, keep: bool, tag: str):
        self.source = repo
        self.keep = keep
        if work:
            self.root = Path(work).expanduser().resolve() / tag
            if self.root == repo or repo in self.root.parents:
                sys.exit("usage error: the work folder must be outside the repo")
            if self.root.exists():
                sys.exit("usage error: the work folder must not exist")
            self.root.mkdir(parents=True)
        else:
            self.root = Path(tempfile.mkdtemp(prefix=f"lt-install-{tag}-"))
        if self.root == repo or repo in self.root.parents:
            sys.exit("usage error: the work folder must be outside the repo")
        self.repo = self.root / "repo"

    def __enter__(self) -> "Workspace":
        if (self.source / ".git").exists() and shutil.which("git"):
            # Match the release's Git-visible inputs. Ignore render outputs that
            # another integration check may currently be generating in examples/.
            result = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                         self.source, clean_env(self.home("selection-home")))
            if result.rc:
                raise RuntimeError("cannot select Git-visible release inputs: " + result.tail())
            self.repo.mkdir()
            for rel in sorted(set(result.out.split(chr(0))) - {""}):
                parts = Path(rel).parts
                if any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in COPY_IGNORE):
                    continue
                source, target = self.source / rel, self.repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    target.symlink_to(os.readlink(source))
                else:
                    shutil.copyfile(source, target)
        else:
            shutil.copytree(self.source, self.repo, symlinks=True,
                            ignore=shutil.ignore_patterns(*COPY_IGNORE))
        return self

    def __exit__(self, *exc) -> None:
        if self.keep:
            print(f"# kept {self.root}")
        else:
            shutil.rmtree(self.root, ignore_errors=True)

    def home(self, name: str) -> Path:
        h = self.root / name
        h.mkdir(parents=True, exist_ok=True)
        return h

    def dir(self, name: str) -> Path:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d


def clean_env(home: Path, extra: dict | None = None, keep: tuple = ()) -> dict:
    """Environment for an agent CLI: temp HOME and XDG dirs, no credentials."""
    # Pass only operational settings. Unknown credential variables stay out.
    allowed = {"PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TZ",
               "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}
    env = {k: v for k, v in os.environ.items() if k in allowed or k in keep}
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
    })
    if extra:
        env.update(extra)
    return env


@dataclass
class Run:
    rc: int
    out: str
    err: str
    missing: bool = False
    timeout: bool = False

    def tail(self, n: int = 300) -> str:
        text = (self.err.strip() or self.out.strip()).replace("\n", " | ")
        return text[-n:]


def run(cmd: list, cwd: Path, env: dict, timeout: int = 120) -> Run:
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env, timeout=timeout,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           errors="replace", encoding="utf-8")
        COMMANDS.append({"argv": [str(c) for c in cmd], "cwd": str(cwd), "exit": p.returncode})
        return Run(p.returncode, strip_ansi(p.stdout), strip_ansi(p.stderr))
    except FileNotFoundError as e:
        COMMANDS.append({"argv": [str(c) for c in cmd], "cwd": str(cwd), "exit": 127})
        return Run(127, "", str(e), missing=True)
    except subprocess.TimeoutExpired as e:
        COMMANDS.append({"argv": [str(c) for c in cmd], "cwd": str(cwd), "exit": 124})
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return Run(124, strip_ansi(out), f"timeout after {timeout}s", timeout=True)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def find_bin(explicit: str | None, envvar: str, name: str, extra: tuple = ()) -> str | None:
    for cand in (explicit, os.environ.get(envvar)):
        if cand:
            return cand if Path(cand).exists() or shutil.which(cand) else None
    found = shutil.which(name)
    if found:
        return found
    for c in extra:
        if Path(c).expanduser().is_file():
            return str(Path(c).expanduser())
    return None


def frontmatter(skill_md: Path) -> dict:
    """name and description from SKILL.md frontmatter (flat keys only; no PyYAML)."""
    out: dict = {}
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        k = re.match(r"^(name|description):\s*(.*)$", line)
        if k:
            val = k.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out[k.group(1)] = val
    return out


def tree_skills(repo: Path) -> dict:
    """{name: folder} for every skills/<name>/SKILL.md in the tree."""
    return {p.parent.name: p.parent for p in sorted((repo / "skills").glob("*/SKILL.md"))}


def expected(args) -> list:
    return [s.strip() for s in args.expect.split(",") if s.strip()]


def compare(report: Report, check: str, found: set, args, tree: dict,
            prefix: str = "") -> None:
    """One line per expected skill, plus WARN for loaded skills nobody expected."""
    want = expected(args)
    for name in want:
        label = f"{check}:{name}"
        if prefix + name in found:
            report.add("PASS", label, "loaded")
        elif name not in tree:
            report.add("SKIP" if args.present_only else "FAIL", label,
                       "no SKILL.md in the tree yet")
        else:
            report.add("FAIL", label, "SKILL.md in the tree but the agent did not load it")
    ours = {f for f in found if f.startswith(prefix)} if prefix else set()
    for extra in sorted(ours - {prefix + n for n in want}):
        report.add("WARN", f"{check}:extra", f"unexpected skill {extra}")


def file_set(folder: Path) -> set:
    out = set()
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=True):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if f.endswith(".pyc"):
                continue
            out.add(str((Path(dirpath) / f).relative_to(folder)).replace(os.sep, "/"))
    return out


def same_files(source: Path, target: Path) -> bool:
    """Compare all installed bytes, including entry points outside the manifest."""
    names = file_set(source)
    return names == file_set(target) and all(
        filecmp.cmp(source / name, target / name, shallow=False) for name in names)


def verify_manifest(skill_dir: Path) -> list:
    """Problems with scripts/_shared/MANIFEST.json: missing files or sha256 drift."""
    mf = skill_dir / "scripts" / "_shared" / "MANIFEST.json"
    if not mf.is_file():
        return []
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"MANIFEST.json unreadable: {e}"]
    problems = []
    for entry in data.get("files", []):
        p = skill_dir / entry.get("path", "")
        if not p.is_file():
            problems.append(f"missing {entry.get('path')}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != entry.get("sha256"):
            problems.append(f"sha256 differs {entry.get('path')}")
    return problems


ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _zip_add_tree(zf: zipfile.ZipFile, folder: Path, prefix: str) -> int:
    n = 0
    for rel in sorted(file_set(folder)):
        info = zipfile.ZipInfo(f"{prefix}{rel}" if prefix else rel, ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        src = folder / rel
        info.external_attr = (0o755 if os.access(src, os.X_OK) else 0o644) << 16
        zf.writestr(info, src.read_bytes())
        n += 1
    return n


def build_skill_zip(skill_dir: Path, out: Path) -> int:
    """claude.ai layout: one top folder named after the skill, SKILL.md inside it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        return _zip_add_tree(zf, skill_dir, skill_dir.name + "/")


def build_plugin_zip(repo: Path, out: Path) -> int:
    """Plugin root at the zip root (.claude-plugin/plugin.json at top level)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        return _zip_add_tree(zf, repo, "")


def symlinks_in(folder: Path) -> list:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_symlink())
