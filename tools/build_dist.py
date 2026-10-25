#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build deterministic, self-contained skill and plugin upload archives.

Run ``python3 tools/build_dist.py --out dist --check`` from any directory.
Each target folder contains <skill>-<target>-<version>.zip. Perplexity UI puts SKILL.md
at the archive root; the other skill targets wrap files in <skill>/. The
lt-suite-plugin-<version>.zip archive wraps a skills-only plugin in lt-suite/.
Gemini archives retain only name, description, license and metadata frontmatter.
--check also verifies upload limits and reopens every archive to check its layout.
Prerequisite drift checks always run, including when --check is omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile

from gen_manifests import expected_skills

REPO = Path(__file__).resolve().parent.parent
TARGETS = (
    "claude-ai", "claude-api", "gemini-app", "gemini-enterprise",
    "perplexity-api", "perplexity-ui",
)
GEMINI_KEYS = {"name", "description", "license", "metadata"}
STAMP = (1980, 1, 1, 0, 0, 0)
IGNORED = {"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"}
FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"}


class BuildError(Exception):
    """A source tree or archive violates a distribution contract."""


def prerequisites(root: Path) -> None:
    """Run checks in the source root before creating any output files."""
    for name in ("gen_manifests.py", "sync_shared.py"):
        tool = root / "tools" / name
        if not tool.is_file():
            raise BuildError(f"required prerequisite tool missing: tools/{name}")
        result = subprocess.run(
            [sys.executable, str(tool), "--check", *(["--strict"] if name == "sync_shared.py" else [])], encoding='utf-8', cwd=root,
            capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.returncode:
            # Checkers print path lists, never file excerpts.
            details = (result.stdout + result.stderr).strip()
            raise BuildError(f"tools/{name} --check failed ({result.returncode})\n{details}")


def files_under(folder: Path, *, plugin: bool = False, allowed: set[str] | None = None, root: Path | None = None) -> dict[str, bytes]:
    """Read runtime files, rejecting links instead of silently losing them."""
    result = {}
    for base, dirs, files in os.walk(folder, followlinks=False):
        here = Path(base)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED
                         and (not plugin or d not in {"hooks", "bin"}))
        for name in dirs + sorted(files):
            path = here / name
            if path.is_symlink():
                raise BuildError(f"symlink cannot ship: {path.relative_to(folder)}")
        for name in sorted(files):
            path = here / name
            if name.endswith((".pyc", ".pyo")) or name in {".DS_Store", "Thumbs.db"}:
                continue
            if not path.is_file():
                raise BuildError(f"non-regular file cannot ship: {path.relative_to(folder)}")
            relative = path.relative_to(folder).as_posix()
            if allowed is not None and root is not None:
                repo_name = path.relative_to(root).as_posix()
                generated = False
                skill = next((parent for parent in path.parents if parent.parent == root / "skills"), None)
                if skill is not None:
                    manifest = skill / "scripts/_shared/MANIFEST.json"
                    if manifest.is_file():
                        entries = json.loads(manifest.read_bytes())["files"]
                        generated = any(e["path"] == path.relative_to(skill).as_posix()
                                        and e["source"].startswith("shared/") for e in entries)
                    forbidden = (name.startswith(".env") or name in {"model-answer.json", "approvals.json", "module.json", "reference.json", "answers.json"}
                                 or re.fullmatch(r"(?:module|reference|answers)-.*\.json", name)
                                 or path.suffix == ".toml" and not name.endswith(".example.toml") and not generated)
                    if forbidden:
                        raise BuildError(f"private runtime file cannot ship: {repo_name}")
                if repo_name not in allowed:
                    raise BuildError(f"unapproved distribution file: {repo_name}")
            result[relative] = path.read_bytes()
    return result


def font_licenses(files: dict[str, bytes], root: Path = REPO) -> None:
    """Require the bundled font's own license, including copyright notices."""
    catalog = json.loads((root / "shared/fonts/catalog.json").read_text(encoding='utf-8'))
    approved = {}
    for entry in catalog["fonts"]:
        for item in (entry.get("bundled") or {}).get("files", []):
            approved[item["file"]] = item["sha256"]
    for name in files:
        font = PurePosixPath(name)
        if font.suffix.lower() not in FONT_SUFFIXES:
            continue
        relative = str(font).split("/fonts/", 1)[-1]
        if relative not in approved or hashlib.sha256(files[name]).hexdigest() != approved[relative]:
            raise BuildError(f"unapproved font or sha256 mismatch: {name}")
        found = False
        for parent in font.parents:
            if str(parent) == ".":
                break
            for other in files:
                p = PurePosixPath(other)
                if p.parent == parent and re.match(r"(?i)^(ofl|licen[cs]e|copying)([._-]|$)", p.name):
                    if files[other].strip():
                        found = True
                        break
            if found or parent.name == "fonts":
                break
        if not found:
            raise BuildError(f"bundled font missing its license file: {name}")


def gemini_frontmatter(data: bytes) -> bytes:
    """Filter whole top-level YAML blocks without rewriting values or body."""
    lines = data.decode("utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise BuildError("SKILL.md needs YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise BuildError("SKILL.md frontmatter has no closing delimiter")
    blocks: list[tuple[str, list[str]]] = []
    seen = set()
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s|$)", line)
        if match:
            key = match[1]
            if key in seen:
                raise BuildError(f"duplicate frontmatter field: {key}")
            seen.add(key)
            blocks.append((key, [line]))
        elif line.strip() and not line[0].isspace() and not line.startswith("#"):
            raise BuildError("unsupported top-level frontmatter syntax")
        elif blocks:
            blocks[-1][1].append(line)
    if not {"name", "description"} <= seen:
        raise BuildError("SKILL.md frontmatter needs name and description")
    kept = [line for key, block in blocks if key in GEMINI_KEYS for line in block]
    return (lines[0] + "".join(kept) + "".join(lines[end:])).encode("utf-8")


def legal_files(root: Path, allowed: set[str] | None = None) -> dict[str, bytes]:
    result = {}
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.read_bytes().strip():
            raise BuildError(f"required license or notice file missing: {name}")
        result[name] = path.read_bytes()
    agpl = root / "LICENSES/AGPL-3.0-or-later.txt"
    if agpl.is_file() and agpl.read_bytes() != result["LICENSE"]:
        raise BuildError("LICENSE differs from LICENSES/AGPL-3.0-or-later.txt")
    if (root / "LICENSES").is_dir():
        result.update({"LICENSES/" + k: v for k, v in files_under(root / "LICENSES", allowed=allowed, root=root).items()})
    return result


def merge_legal(files: dict[str, bytes], legal: dict[str, bytes]) -> dict[str, bytes]:
    for name in legal.keys() & files.keys():
        if files[name] != legal[name]:
            raise BuildError(f"skill conflicts with release legal file: {name}")
    return {**files, **legal}


def plan(root: Path) -> dict[str, tuple[str, dict[str, bytes]]]:
    """Snapshot inputs and compute every output before publishing archives."""
    source = tomllib.loads((root / "packaging/plugin.toml").read_text(encoding="utf-8"))
    name, version = source["name"], source["version"]
    if name != "lt-suite" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise BuildError("packaging/plugin.toml needs lt-suite and a safe semantic version")
    allowed = set(json.loads((root / "packaging/runtime-files.json").read_bytes()))
    if not allowed or any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts for n in allowed):
        raise BuildError("invalid distribution file allowlist")
    # The reviewed file allowlist also supports a source export before its first commit.
    # Once tracked files exist, every allowlisted input must be tracked too.
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True)
    names = set(tracked.stdout.decode().split("\0")) - {""} if tracked.returncode == 0 else set()
    if names and allowed - names:
        raise BuildError("distribution allowlist contains untracked inputs")
    legal = legal_files(root, allowed)
    result = {}
    plugin = dict(legal)
    skills = expected_skills(root)
    for skill in skills:
        if skill.is_symlink() or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill.name):
            raise BuildError(f"invalid skill directory: {skill.name}")
        files = files_under(skill, allowed=allowed, root=root)
        font_licenses(files, root)
        # Validate and transform once; non-Gemini variants retain original bytes.
        gemini = gemini_frontmatter(files["SKILL.md"])
        files = merge_legal(files, legal)
        for target in TARGETS:
            prefix = "" if target == "perplexity-ui" else skill.name + "/"
            members = {prefix + k: v for k, v in files.items()}
            if target.startswith("gemini-"):
                members[prefix + "SKILL.md"] = gemini
            result[f"{target}/{skill.name}-{target}-{version}.zip"] = (target, members)
        plugin.update({f"skills/{skill.name}/{k}": v for k, v in files_under(skill, plugin=True, allowed=allowed, root=root).items()})
    for relative in ("plugin.json", ".claude-plugin/plugin.json"):
        manifest = json.loads((root / relative).read_bytes())
        for field in ("hooks", "mcpServers"):
            manifest.pop(field, None)
        if manifest.get("name") != name or manifest.get("version") != version:
            raise BuildError(f"manifest identity differs from packaging/plugin.toml: {relative}")
        plugin[relative] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    for relative in ("README.md", "AGENTS.md"):
        path = root / relative
        if path.is_file() and not path.is_symlink():
            plugin[relative] = path.read_bytes()
    for folder in ("rules", "docs"):
        if (root / folder).is_dir():
            plugin.update({folder + "/" + k: v for k, v in files_under(root / folder, plugin=True, allowed=allowed, root=root).items()})
    result[f"lt-suite-plugin-{version}.zip"] = ("plugin", {name + "/" + k: v for k, v in plugin.items()})
    return result


def validate_members(target: str, members: dict[str, bytes], limits: bool) -> None:
    """Validate path safety, legal files, layouts and documented upload limits."""
    if any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n for n in members):
        raise BuildError("archive contains an unsafe member path")
    prefix = "" if target == "perplexity-ui" else next(iter(members)).split("/", 1)[0] + "/"
    if prefix and any(not n.startswith(prefix) for n in members):
        raise BuildError(f"{target} needs exactly one top-level folder")
    if any(prefix + n not in members for n in ("LICENSE", "THIRD_PARTY_NOTICES.md")):
        raise BuildError(f"{target} is missing license or notices")
    if target == "plugin":
        if prefix + ".claude-plugin/plugin.json" not in members:
            raise BuildError("plugin is missing its Claude manifest")
        if any(set(PurePosixPath(n).parts) & {"hooks", "bin"} for n in members):
            raise BuildError("upload plugin contains hooks or bin")
    elif prefix + "SKILL.md" not in members:
        raise BuildError(f"{target} is missing SKILL.md")
    if not limits:
        return
    total = sum(map(len, members.values()))
    max_bytes, max_files = 200_000_000, 5000
    if target == "claude-api":
        max_bytes = 30_000_000
    elif target.startswith("gemini-"):
        max_bytes, max_files = 100_000_000, 100
        if any(len(PurePosixPath(n).parts) - 1 > 10 for n in members):
            raise BuildError(f"{target} exceeds directory depth 10")
    elif target.startswith("perplexity-"):
        max_bytes, max_files = 32 * 1024 * 1024, 100
    if total > max_bytes or len(members) > max_files:
        raise BuildError(f"{target} upload limit exceeded: {len(members)} files, {total} bytes")
    if target in {"claude-ai", "plugin"} and any(len(data) > 30_000_000 for data in members.values()):
        raise BuildError(f"{target} exceeds 30 MB per-file limit")


def write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, STAMP)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)


def check_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != sorted(members) or archive.testzip() is not None:
            raise BuildError(f"archive members or checksum differ: {path.name}")
        for info in archive.infolist():
            if (info.compress_type != zipfile.ZIP_STORED or info.date_time != STAMP or info.create_system != 3
                    or info.external_attr != (stat.S_IFREG | 0o644) << 16
                    or archive.read(info) != members[info.filename]):
                raise BuildError(f"archive bytes or metadata differ: {path.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    parser.add_argument("--out", type=Path, default=Path("dist"), help="output directory (relative to source root)")
    parser.add_argument("--check", action="store_true", help="also verify upload limits and archive bytes")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out = (root / args.out).resolve()
    try:
        if out == root or out in root.parents or out.is_relative_to(root / "skills"):
            raise BuildError("output must be outside source inputs")
        prerequisites(root)
        outputs = plan(root)
        for target, members in outputs.values():
            validate_members(target, members, args.check)
        existing = {p.relative_to(out).as_posix() for p in out.rglob("*.zip")}
        if existing - outputs.keys():
            raise BuildError("output contains stale archives; choose a fresh output directory")
        # Stage the whole build in TMPDIR. Source or validation failures publish nothing.
        with tempfile.TemporaryDirectory(prefix="lt-dist-") as scratch:
            stage = Path(scratch)
            for relative, (_, members) in outputs.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                write_zip(path, members)
                if args.check:
                    check_zip(path, members)
            for relative in sorted(outputs):
                path = out / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                # Copy bytes across filesystems without inheriting source permissions.
                path.write_bytes((stage / relative).read_bytes())
        print(f"Built {len(outputs)} deterministic archives for {len(TARGETS)} skill targets and plugin upload.")
        return 0
    except (BuildError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"build_dist: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
