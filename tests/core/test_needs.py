# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every ltcore module declares the shared data it opens in NEEDS.

tools/sync_shared.py copies a skill's data files from the NEEDS lists of its ltcore
import closure. A module that opens a shared/ file missing from NEEDS works in the
repository and breaks in a lone skill folder. This test runs each module under an
``open()`` audit hook and fails on any shared/ file outside the NEEDS and
OPTIONAL_NEEDS of the module's import closure.

Modules exercise their data reads through an entry in EXERCISERS below, or by
defining a no-argument ``_needs_probe()`` function that opens what the module
opens in normal use. A module with neither is only imported.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHARED = (REPO / "shared").resolve()
LTCORE = SHARED / "ltcore"

_spec = importlib.util.spec_from_file_location("sync_shared_for_tests", REPO / "tools" / "sync_shared.py")
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync  # dataclasses look the module up by name
_spec.loader.exec_module(sync)

_OPENED: list[str] | None = None


def _hook(event: str, args: tuple) -> None:
    if _OPENED is None or event != "open" or not args:
        return
    target = args[0]
    if isinstance(target, int) or target is None:
        return
    try:
        p = Path(os.fsdecode(target))
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()
        rel = p.relative_to(SHARED)
    except (ValueError, OSError, TypeError):
        return
    if p.suffix in (".py", ".pyc") or "__pycache__" in p.parts:
        return
    _OPENED.append(rel.as_posix())


if not getattr(sys, "_lt_needs_hook", False):
    sys.addaudithook(_hook)
    sys._lt_needs_hook = True


def modules() -> list[str]:
    return sorted(["ltcore"] + [f"ltcore.{p.stem}" for p in LTCORE.glob("*.py") if p.stem != "__init__"])


def _tree(mod: str) -> ast.Module:
    rel, path = sync._module_file(SHARED, mod)
    return ast.parse(path.read_text(encoding="utf-8"))


def _list_const(tree: ast.Module, name: str) -> list[str] | None:
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            return list(ast.literal_eval(value))
    return None


def closure(mod: str) -> set[str]:
    seen: set[str] = set()
    queue = [mod]
    while queue:
        m = queue.pop()
        if m in seen or not sync._module_file(SHARED, m)[1].is_file():
            continue
        seen.add(m)
        mods, _, froms = sync.scan_imports(_tree(m), in_ltcore=True)
        queue.extend(mods)
        for base, name in froms:
            if sync._module_file(SHARED, f"{base}.{name}")[1].is_file():
                queue.append(f"{base}.{name}")
        if m != "ltcore":
            queue.append("ltcore")
    return seen


def allowed(mod: str) -> list[str]:
    out: list[str] = []
    for m in closure(mod):
        t = _tree(m)
        out += (_list_const(t, "NEEDS") or []) + (_list_const(t, "OPTIONAL_NEEDS") or [])
    return out


def is_allowed(rel: str, allow: list[str]) -> bool:
    return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in allow)


def _ex_config(tmp: Path) -> None:
    from ltcore import config
    config.defaults()
    config.schema()
    config.load(cwd=tmp)


def _ex_store(tmp: Path) -> None:
    from ltcore import store
    store.Store(data_dir=tmp / "d").as_dict()


def _ex_lexicon(tmp: Path) -> None:
    import io
    from ltcore import lexicon
    import datetime as dt
    lexicon.run(lexicon.default_dir(), today=dt.date(2001, 1, 1), repo_root=REPO, out=io.StringIO())


def _ex_init(tmp: Path) -> None:
    import ltcore
    ltcore.missing_modules([])


EXERCISERS = {"ltcore": _ex_init, "ltcore.config": _ex_config, "ltcore.store": _ex_store,
              "ltcore.lexicon": _ex_lexicon}


@pytest.mark.parametrize("mod", modules())
def test_module_declares_needs(mod):
    needs = _list_const(_tree(mod), "NEEDS")
    assert needs is not None, f"{mod} has no module-level NEEDS list"
    assert all(isinstance(n, str) and not n.startswith("/") and ".." not in n for n in needs)
    for n in needs:
        target = SHARED / n
        assert (target.is_dir() if n.endswith("/") else target.is_file()), \
            f"{mod} NEEDS {n}, which does not exist under shared/"


@pytest.mark.parametrize("mod", modules())
def test_opens_only_declared_shared_files(mod, tmp_path):
    global _OPENED
    allow = allowed(mod)
    _OPENED = []
    try:
        m = importlib.import_module(mod)
        if mod in EXERCISERS:
            EXERCISERS[mod](tmp_path)
        probe = getattr(m, "_needs_probe", None)
        if callable(probe):
            probe()
        opened = list(_OPENED)
    finally:
        _OPENED = None
    bad = sorted({o for o in opened if not is_allowed(o, allow)})
    assert not bad, f"{mod} opened shared/ files missing from NEEDS: {bad} (allowed: {allow})"


def test_hook_catches_an_undeclared_open():
    """The mechanism works: the hook records shared/ opens and the allow test rejects strays."""
    global _OPENED
    _OPENED = []
    try:
        with open(SHARED / "schemas" / "config.schema.json", encoding="utf-8") as fh:
            fh.read(1)
        opened = list(_OPENED)
    finally:
        _OPENED = None
    assert "schemas/config.schema.json" in opened
    assert is_allowed("schemas/config.schema.json", allowed("ltcore.store"))  # via config
    assert not is_allowed("schemas/never-declared.json", allowed("ltcore.store"))
    assert is_allowed("lexicon/x.md", ["lexicon/"]) and not is_allowed("lexiconx.md", ["lexicon/"])
