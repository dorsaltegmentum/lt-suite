#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy shared/ code and data into each skill folder, so one skill folder runs alone.

For every skill the file set is computed, not listed by hand:
  code   every .py file in the skill (outside _shared/) is parsed with ast; its
         ltcore imports are followed to a closure, and each module in it is copied
         to scripts/_shared/ltcore/
  data   each module in the closure declares NEEDS = [...] (paths under shared/);
         those files go to scripts/_shared/<path>
  extras shared/sync.toml lists what no import can reveal: references (to
         references/_shared/) and scripts extras (to scripts/_shared/)
Every skill also gets LICENSE.txt (copy of LICENSE), NOTICE.txt and requirements.txt
(third-party import closure exported with hashes from uv.lock), and
scripts/_shared/MANIFEST.json with {path, source, sha256} for every copy.

.py and .js copies get a first line naming their source; Markdown, JSON and TOML
copies stay byte-identical. Files in scripts/_shared/ and references/_shared/ that
the plan does not contain are removed (sync) or reported (--check).

    python3 tools/sync_shared.py                      # every skill
    python3 tools/sync_shared.py --skill lt-module-capture
    python3 tools/sync_shared.py --check [--skill NAME] [--strict]

A missing source prints "SKIP <path> (source not written yet)" and is not an error
unless --strict. Exit 0 ok, 1 drift or error (or a SKIP under --strict), 2 usage.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Import name -> distribution name (normalized). Every third-party import a skill
# makes must be listed here and pinned in a pyproject.toml dependency group.
IMPORT_TO_DIST = {
    "PIL": "pillow",
    "numpy": "numpy",
    "fontTools": "fonttools",
    "reportlab": "reportlab",
    "pypdfium2": "pypdfium2",
    "scipy": "scipy",
    "jsonschema": "jsonschema",
    "pikepdf": "pikepdf",
    "fitz": "pymupdf",
    "pymupdf": "pymupdf",
    "cv2": "opencv-python-headless",
    "sklearn": "scikit-learn",
}
GROUP_ORDER = ("core", "diagram", "export", "fill", "optional", "dev", "ci")
SKIP_DIRS = {"_shared", "__pycache__", ".venv", "node_modules", ".pytest_cache"}
IGNORED_GENERATED = ("__pycache__", ".pyc")
GEN_LINE = "Generated from shared/{src} by tools/sync_shared.py. Edit the source."


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Plan:
    skill: str
    files: dict[str, bytes] = field(default_factory=dict)    # skill-relative path -> bytes
    sources: dict[str, str] = field(default_factory=dict)    # skill-relative path -> shared path
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ import scan

def _parse(path: Path, errors: list[str], root: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        errors.append(f"{path.relative_to(root).as_posix()}: cannot parse: {exc.msg} (line {exc.lineno})")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"{path.relative_to(root).as_posix()}: cannot read: {exc}")
    return None


def _module_names(tree: ast.Module) -> set[str]:
    """Top-level names a module defines (functions, classes, assignments, imports)."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add((a.asname or a.name).split(".")[0])
    return out


def scan_imports(tree: ast.Module, *, in_ltcore: bool) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """(ltcore modules, top-level third-party candidates, (module, name) from-imports).

    ltcore modules are returned as dotted names ("ltcore", "ltcore.config"). A
    ``from ltcore import x`` is returned as ("ltcore", "x") for the caller to decide
    whether x is a module or a name defined in ltcore/__init__.py.
    """
    mods: set[str] = set()
    tops: set[str] = set()
    froms: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "ltcore" or a.name.startswith("ltcore."):
                    parts = a.name.split(".")
                    mods.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
                else:
                    tops.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and in_ltcore:
                base = "ltcore" + (f".{node.module}" if node.module else "")
                mods.add("ltcore")
                if node.module:
                    mods.add(base)
                for a in node.names:
                    froms.add((base, a.name))
            elif node.level:
                continue  # relative import inside a skill package: local
            elif node.module == "ltcore" or (node.module or "").startswith("ltcore."):
                parts = node.module.split(".")
                mods.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
                for a in node.names:
                    froms.add((node.module, a.name))
            elif node.module:
                tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == "ltcore" or arg.value.startswith("ltcore."):
                        parts = arg.value.split(".")
                        mods.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
                    else:
                        tops.add(arg.value.split(".")[0])
    return mods, tops, froms


def read_needs(tree: ast.Module) -> list[str]:
    """The module-level NEEDS list of string constants (empty when absent)."""
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == "NEEDS" and value is not None:
            try:
                v = ast.literal_eval(value)
            except ValueError:
                return []
            return [x for x in v if isinstance(x, str)]
    return []


def _module_file(shared: Path, dotted: str) -> tuple[str, Path]:
    parts = dotted.split(".")
    pkg = shared.joinpath(*parts) / "__init__.py"
    if len(parts) == 1 or pkg.is_file():
        return "/".join(parts) + "/__init__.py", pkg
    rel = "/".join(parts) + ".py"
    return rel, shared / rel


# ------------------------------------------------------------------ plan

def _skill_py_files(skill_dir: Path) -> list[Path]:
    out = []
    for p in sorted(skill_dir.rglob("*.py")):
        rel = p.relative_to(skill_dir)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        out.append(p)
    return out


def _local_names(skill_dir: Path) -> set[str]:
    names: set[str] = set()
    for p in skill_dir.rglob("*"):
        rel = p.relative_to(skill_dir)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_file() and p.suffix == ".py":
            names.add(p.stem)
        elif p.is_dir():
            names.add(p.name)
    return names


def _header(src: str, data: bytes) -> bytes:
    suffix = Path(src).suffix
    if suffix == ".py":
        line = f"# {GEN_LINE.format(src=src)}\n".encode()
    elif suffix in (".js", ".mjs", ".cjs"):
        line = f"// {GEN_LINE.format(src=src)}\n".encode()
    else:
        return data
    if data.startswith(b"#!"):
        first, _, rest = data.partition(b"\n")
        return first + b"\n" + line + rest
    return line + data


def load_sync(root: Path) -> dict:
    p = root / "shared" / "sync.toml"
    if not p.is_file():
        return {}
    with open(p, "rb") as fh:
        return tomllib.load(fh)


def load_pins(root: Path) -> tuple[dict[str, str], set[str]]:
    """normalized dist -> requirement string; and dists found only in 'optional'."""
    p = root / "pyproject.toml"
    if not p.is_file():
        return {}, set()
    with open(p, "rb") as fh:
        groups = tomllib.load(fh).get("dependency-groups", {})
    pins: dict[str, str] = {}
    where: dict[str, set[str]] = {}
    order = list(GROUP_ORDER) + sorted(g for g in groups if g not in GROUP_ORDER)
    for g in order:
        for req in groups.get(g, []):
            if not isinstance(req, str):
                continue
            name = norm(re.split(r"[<>=!~;\[ ]", req, maxsplit=1)[0])
            pins.setdefault(name, req)
            where.setdefault(name, set()).add(g)
    optional_only = {n for n, gs in where.items() if gs <= {"optional"}}
    return pins, optional_only


def frozen_requirements(root: Path, required: set[str], pins: dict[str, str]) -> list[str]:
    """Export hashed pins from the frozen lock, limited to a skill's dependency closure."""
    with open(root / "uv.lock", "rb") as fh:
        packages = tomllib.load(fh).get("package", [])
    dependencies: dict[str, set[str]] = {}
    for package in packages:
        dependencies.setdefault(norm(package["name"]), set()).update(
            norm(d["name"]) for d in package.get("dependencies", []))
    closure = set(required)
    pending = list(required)
    while pending:
        name = pending.pop()
        if name not in dependencies:
            raise ValueError(f"{name}: dependency missing from uv.lock; regenerate the lock")
        for dep in dependencies[name] - closure:
            closure.add(dep)
            pending.append(dep)
    if not required:
        return []
    command = ["uv", "export", "--frozen", "--offline", "--all-groups", "--no-header",
               "--no-annotate", "--no-emit-project", "--no-emit-local"]
    for name in sorted(set(pins) - closure):
        command.extend(["--prune", name])
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"frozen uv export exited {result.returncode}: {result.stderr.strip()}")
    lines = []
    for row in result.stdout.replace("\\\n", " ").splitlines():
        line = " ".join(row.split())
        if not line or line.startswith("#"):
            continue
        name = norm(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0])
        if name not in closure or "==" not in line or "--hash=sha256:" not in line:
            raise ValueError("frozen export contains an unhashed or unexpected dependency")
        lines.append(line)
    emitted = {norm(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0]) for line in lines}
    if required - emitted:
        raise ValueError("frozen export omitted required skill dependencies")
    return lines


def _add(plan: Plan, dest: str, data: bytes, src: str) -> None:
    plan.files[dest] = data
    plan.sources[dest] = src


def _add_shared(plan: Plan, shared: Path, rel: str, dest_prefix: str, strip: str = "") -> None:
    """Copy shared/<rel> (file or folder ending in /) to <dest_prefix>/<rel minus strip>."""
    src = shared / rel
    if rel.endswith("/"):
        if not src.is_dir():
            plan.skipped.append(f"shared/{rel}")
            return
        files = sorted(p for p in src.rglob("*") if p.is_file()
                       and "__pycache__" not in p.parts and p.name != ".DS_Store")
        if not files:
            plan.skipped.append(f"shared/{rel}")
        for f in files:
            _add_shared(plan, shared, f.relative_to(shared).as_posix(), dest_prefix, strip)
        return
    if not src.is_file():
        plan.skipped.append(f"shared/{rel}")
        return
    out = rel[len(strip):] if strip and rel.startswith(strip) else rel
    _add(plan, f"{dest_prefix}/{out}", _header(rel, src.read_bytes()), f"shared/{rel}")


def compute_plan(root: Path, skill: str) -> Plan:
    plan = Plan(skill)
    shared = root / "shared"
    skill_dir = root / "skills" / skill
    sync = load_sync(root)

    # 1. imports of the skill's own files
    ltmods: set[str] = set()
    froms: set[tuple[str, str]] = set()
    thirdparty: dict[str, set[str]] = {}
    for f in _skill_py_files(skill_dir):
        tree = _parse(f, plan.errors, root)
        if tree is None:
            continue
        m, t, fr = scan_imports(tree, in_ltcore=False)
        ltmods |= m
        froms |= fr
        for name in t:
            thirdparty.setdefault(name, set()).add(f.relative_to(root).as_posix())

    # 2. ltcore closure
    init_names: set[str] = set()
    init_tree = None
    init_file = shared / "ltcore" / "__init__.py"
    if init_file.is_file():
        init_tree = _parse(init_file, plan.errors, root)
        init_names = _module_names(init_tree) if init_tree else set()
    done: set[str] = set()
    queue: list[str] = sorted(ltmods)
    todo_froms = set(froms)
    while True:
        # "from ltcore import x": x is a module when a file exists, or when ltcore/__init__.py
        # does not define it (then it is a module not written yet, reported as SKIP).
        for base, name in sorted(todo_froms):
            if name == "*":
                continue
            cand = f"{base}.{name}"
            if _module_file(shared, cand)[1].is_file():
                queue.append(cand)
            elif base == "ltcore" and name not in init_names:
                queue.append(cand)
        todo_froms = set()
        if not queue:
            break
        mod = queue.pop(0)
        if mod in done:
            continue
        done.add(mod)
        if mod != "ltcore":
            queue.append("ltcore")
        rel, path = _module_file(shared, mod)
        if not path.is_file():
            plan.skipped.append(f"shared/{rel}")
            continue
        tree = _parse(path, plan.errors, root)
        if tree is None:
            continue
        _add(plan, f"scripts/_shared/{rel}", _header(rel, path.read_bytes()), f"shared/{rel}")
        m, t, fr = scan_imports(tree, in_ltcore=True)
        queue.extend(sorted(m - done))
        todo_froms |= fr
        for name in t:
            thirdparty.setdefault(name, set()).add(f"shared/{rel}")
        for need in read_needs(tree):
            _add_shared(plan, shared, need, "scripts/_shared")

    # 3. extras from sync.toml
    entry = (sync.get("skill") or {}).get(skill, {})
    for rel in list((sync.get("all") or {}).get("references", [])) + list(entry.get("references", [])):
        _add_shared(plan, shared, rel, "references/_shared", strip="references/")
    for rel in list((sync.get("all") or {}).get("scripts", [])) + list(entry.get("scripts", [])):
        _add_shared(plan, shared, rel, "scripts/_shared")

    # 4. requirements.txt
    local = _local_names(skill_dir) | {"ltcore", "__future__"}
    stdlib = set(sys.stdlib_module_names)
    pins, optional_only = load_pins(root)
    reqs: dict[str, str] = {}
    optional: dict[str, str] = {}
    for name, users in sorted(thirdparty.items()):
        if name in stdlib or name in local or name.startswith("_"):
            continue
        dist = IMPORT_TO_DIST.get(name)
        if dist is None:
            plan.errors.append(f"unmapped third-party import {name!r} in {', '.join(sorted(users))}; "
                               f"add it to IMPORT_TO_DIST in tools/sync_shared.py and pin it in "
                               f"pyproject.toml")
            continue
        if dist not in pins:
            plan.errors.append(f"{dist} (import {name}) is not pinned in any pyproject.toml "
                               f"dependency group")
            continue
        (optional if dist in optional_only else reqs)[dist] = pins[dist]
    lines = ["# Generated by tools/sync_shared.py from this skill's imports and uv.lock.",
             "# Python 3.11 or newer. Frozen hashed pins; edit pyproject.toml and regenerate uv.lock."]
    try:
        lines += frozen_requirements(root, set(reqs), pins)
    except (OSError, ValueError) as exc:
        plan.errors.append(f"cannot export frozen requirements: {exc}")
    if optional:
        lines.append("# Optional: imported only when installed.")
        lines += [f"# {optional[k]}" for k in sorted(optional)]
    _add(plan, "requirements.txt", ("\n".join(lines) + "\n").encode(), "generated")

    # 5. LICENSE.txt and NOTICE.txt
    lic = root / "LICENSE"
    if lic.is_file():
        _add(plan, "LICENSE.txt", lic.read_bytes(), "LICENSE")
    else:
        plan.skipped.append("LICENSE")
    _add(plan, "NOTICE.txt", notice_text(skill, plan, sync, skill_dir).encode(), "generated")

    # 6. manifest of every copy
    entries = [{"path": p, "source": plan.sources[p],
                "sha256": hashlib.sha256(plan.files[p]).hexdigest()}
               for p in sorted(plan.files)
               if p.startswith(("scripts/_shared/", "references/_shared/"))]
    manifest = {"generated_by": "tools/sync_shared.py", "skill": skill, "files": entries}
    _add(plan, "scripts/_shared/MANIFEST.json",
         (json.dumps(manifest, indent=1, sort_keys=False) + "\n").encode(), "generated")
    plan.skipped = sorted(set(plan.skipped))
    return plan


def notice_text(skill: str, plan: Plan, sync: dict, skill_dir: Path) -> str:
    globs = sync.get("notices") or {}
    entry = (sync.get("skill") or {}).get(skill, {})
    local = entry.get("notices") or {}
    bundled: list[str] = []
    for dest in sorted(plan.files):
        src = plan.sources[dest]
        if not src.startswith("shared/"):
            continue
        rel = src[len("shared/"):]
        for pat, text in globs.items():
            if fnmatch.fnmatchcase(rel, pat):
                bundled.append(f"  {dest}: {text}")
                break
    for rel, text in sorted(local.items()):
        if (skill_dir / rel).is_file():
            bundled.append(f"  {rel}: {text}")
    lines = [
        f"lt-suite: {skill}",
        "Copyright 2026 dorsaltegmentum",
        "Licensed under the GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later);",
        "see LICENSE.txt.",
        "",
        "This folder is one skill of lt-suite: https://github.com/dorsaltegmentum/lt-suite",
        "THIRD_PARTY_NOTICES.md in that repository lists every third-party component and",
        "runtime dependency.",
        "",
        "lt-suite is an independent, unofficial project. It is not affiliated with, endorsed",
        "by or sponsored by ADInstruments. Lt and kuraCloud are trademarks of their owners.",
        "",
        "Bundled third-party files in this folder:",
    ]
    lines += bundled or ["  none"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ apply / check

GENERATED_DIRS = ("scripts/_shared", "references/_shared")


def _on_disk(skill_dir: Path) -> set[str]:
    out: set[str] = set()
    for d in GENERATED_DIRS:
        base = skill_dir / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                out.add(p.relative_to(skill_dir).as_posix())
    return out


def drift(root: Path, plan: Plan) -> list[str]:
    skill_dir = root / "skills" / plan.skill
    out = []
    for rel, data in sorted(plan.files.items()):
        p = skill_dir / rel
        if not p.is_file():
            out.append(f"DRIFT skills/{plan.skill}/{rel} (missing)")
        elif p.read_bytes() != data:
            out.append(f"DRIFT skills/{plan.skill}/{rel} (differs from the generator)")
    for rel in sorted(_on_disk(skill_dir) - set(plan.files)):
        out.append(f"DRIFT skills/{plan.skill}/{rel} (orphan: no source)")
    return out


def apply(root: Path, plan: Plan) -> list[str]:
    skill_dir = root / "skills" / plan.skill
    changed = []
    for rel, data in sorted(plan.files.items()):
        p = skill_dir / rel
        if p.is_file() and p.read_bytes() == data:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        changed.append(f"wrote skills/{plan.skill}/{rel}")
    for rel in sorted(_on_disk(skill_dir) - set(plan.files)):
        (skill_dir / rel).unlink()
        changed.append(f"removed skills/{plan.skill}/{rel}")
    for d in GENERATED_DIRS:
        base = skill_dir / d
        if base.is_dir():
            for sub in sorted((x for x in base.rglob("*") if x.is_dir()), reverse=True):
                if not any(sub.iterdir()):
                    sub.rmdir()
    return changed


def skills_in(root: Path) -> list[str]:
    d = root / "skills"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", action="append", help="sync only this skill (repeatable); "
                    "writes only inside skills/<name>/")
    ap.add_argument("--check", action="store_true", help="compare with disk; write nothing")
    ap.add_argument("--strict", action="store_true", help="a missing source is an error")
    ap.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    ap.add_argument("-q", "--quiet", action="store_true", help="print only problems")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    names = a.skill or skills_in(root)
    for n in names:
        if not (root / "skills" / n).is_dir():
            print(f"error: no skill folder skills/{n}", file=sys.stderr)
            return 2
    status = 0
    for n in names:
        plan = compute_plan(root, n)
        for s in plan.skipped:
            print(f"SKIP {s} (source not written yet)")
        for e in plan.errors:
            print(f"ERROR skills/{n}: {e}")
        if plan.errors or (a.strict and plan.skipped):
            status = 1
        if a.check:
            d = drift(root, plan)
            for line in d:
                print(line)
            if d:
                status = 1
            elif not a.quiet:
                print(f"ok skills/{n} ({len(plan.files)} generated files match)")
        elif not plan.errors:
            changed = apply(root, plan)
            if not a.quiet:
                for line in changed:
                    print(line)
                print(f"synced skills/{n} ({len(plan.files)} files, {len(changed)} changed)")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
