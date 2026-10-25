# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify distribution contracts through real builds and isolated extraction."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "tools/build_dist.py"
TARGETS = (
    "claude-ai", "claude-api", "gemini-app", "gemini-enterprise",
    "perplexity-api", "perplexity-ui",
)
BODY = "\n# Example\n\nKeep compatibility: in this body unchanged.\n"
FRONT = (
    "---\nname: sample\ndescription: >-\n  An example skill\n  with folded text.\n"
    "license: AGPL-3.0-or-later\ncompatibility: Python 3.11+\n"
    "allowed-tools:\n  - Bash\nunknown-field: |\n  Removed text.\n"
    "metadata:\n  version: '0.1.0'\n  label: 'compatibility: preserved'\n---\n"
)


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    put(root, "packaging/plugin.toml", 'name = "lt-suite"\nversion = "0.1.0"\nskills = ["sample"]\n')
    manifest = {"name": "lt-suite", "version": "0.1.0", "description": "Example."}
    for name in ("plugin.json", ".claude-plugin/plugin.json"):
        put(root, name, json.dumps({**manifest, "hooks": {"Stop": []}}))
    put(root, "LICENSE", "Example project license.\n")
    put(root, "THIRD_PARTY_NOTICES.md", "Example third-party notices.\n")
    put(root, "LICENSES/OFL-1.1.txt", "Example font license.\n")
    put(root, "skills/sample/SKILL.md", FRONT + BODY)
    put(root, "skills/sample/scripts/_shared/helper.py", "VALUE = 'alone'\n")
    put(root, "skills/sample/scripts/hello.py", (
        "import argparse\nfrom _shared.helper import VALUE\n"
        "argparse.ArgumentParser(description=VALUE).parse_args()\n"
    ))
    put(root, "skills/sample/scripts/_shared/fonts/example/font.ttf", b"example font")
    put(root, "skills/sample/scripts/_shared/fonts/example/OFL.txt", "Font copyright and OFL.\n")
    put(root, "skills/sample/scripts/__pycache__/ignored.pyc", b"cache")
    put(root, "skills/sample/hooks/hooks.json", "{}")
    put(root, "bin/tool", "unused")
    put(root, "hooks/hooks.json", "{}")
    for tool in ("gen_manifests", "sync_shared"):
        put(root, f"tools/{tool}.py", (
            "import sys\nfrom pathlib import Path\n"
            f"assert sys.argv[1:] == {['--check', '--strict'] if tool == 'sync_shared' else ['--check']!r}\n"
            f"Path('{tool}.checked').write_text('checked')\n"
        ))
    put(root, "shared/fonts/catalog.json", json.dumps({"fonts": [{"bundled": {"files": [
        {"file": "example/font.ttf", "sha256": hashlib.sha256(b"example font").hexdigest()}]}}]}))
    approve_files(root)
    return root


def approve_files(root):
    names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
             and "__pycache__" not in p.parts]
    put(root, "packaging/runtime-files.json", json.dumps(sorted(names)))


def run(root, out, *args):
    return subprocess.run(
        [sys.executable, str(BUILDER), "--root", str(root), "--out", str(out), *args],
        encoding='utf-8', cwd=root.parent, capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def archive(out, target):
    return out / target / f"sample-{target}-0.1.0.zip"


def test_builds_all_layouts_and_runs_both_drift_checks(source, tmp_path):
    # Catches a missing target or a builder that bypasses prerequisite checks.
    out = tmp_path / "dist"
    result = run(source, out)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = {f"{target}/sample-{target}-0.1.0.zip" for target in TARGETS}
    expected.add("lt-suite-plugin-0.1.0.zip")
    assert {p.relative_to(out).as_posix() for p in out.rglob("*.zip")} == expected
    declared = tomllib.loads((REPO / "packaging/dist.toml").read_text(encoding='utf-8'))
    assert {p.name for p in out.iterdir() if p.is_dir()} == set(declared["skill_targets"])
    assert declared["compression"] == "stored"
    assert declared["root_skill_targets"] == ["perplexity-ui"]
    for name in ("gen_manifests", "sync_shared"):
        assert (source / f"{name}.checked").read_text(encoding='utf-8') == "checked"
    for target in TARGETS:
        with zipfile.ZipFile(archive(out, target)) as z:
            prefix = "" if target == "perplexity-ui" else "sample/"
            assert prefix + "SKILL.md" in z.namelist()
            if prefix:
                assert {n.split('/')[0] for n in z.namelist()} == {"sample"}
            assert prefix + "scripts/hello.py" in z.namelist()
            assert not any("__pycache__" in n for n in z.namelist())


def test_builds_byte_identical_archives_with_fixed_metadata(source, tmp_path):
    # Catches timestamps, source modes or traversal order leaking into archives.
    a, b = tmp_path / "a", tmp_path / "b"
    assert run(source, a, "--check").returncode == 0
    for p in source.rglob("*"):
        if p.is_file():
            os.utime(p, (1700000000, 1700000000))
            if os.name != "nt":
                p.chmod(0o700)
    assert run(source, b, "--check").returncode == 0
    first = {p.relative_to(a): hashlib.sha256(p.read_bytes()).hexdigest() for p in a.rglob("*.zip")}
    second = {p.relative_to(b): hashlib.sha256(p.read_bytes()).hexdigest() for p in b.rglob("*.zip")}
    assert first and first == second
    for p in a.rglob("*.zip"):
        with zipfile.ZipFile(p) as z:
            assert z.namelist() == sorted(z.namelist())
            for info in z.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)
                assert info.create_system == 3
                assert stat.S_IMODE(info.external_attr >> 16) == 0o644


@pytest.mark.parametrize("target", TARGETS)
def test_each_layout_carries_project_and_font_licenses(source, tmp_path, target):
    # Catches omission of legal files when a skill leaves the repository.
    out = tmp_path / "dist"
    assert run(source, out).returncode == 0
    prefix = "" if target == "perplexity-ui" else "sample/"
    with zipfile.ZipFile(archive(out, target)) as z:
        assert z.read(prefix + "LICENSE") == b"Example project license.\n"
        assert z.read(prefix + "THIRD_PARTY_NOTICES.md") == b"Example third-party notices.\n"
        assert z.read(prefix + "LICENSES/OFL-1.1.txt") == b"Example font license.\n"
        assert z.read(prefix + "scripts/_shared/fonts/example/OFL.txt") == b"Font copyright and OFL.\n"


def test_plugin_has_one_root_licenses_and_no_hooks_or_bin(source, tmp_path):
    # Catches wrapping errors and executable hooks in the upload package.
    out = tmp_path / "dist"
    assert run(source, out).returncode == 0
    with zipfile.ZipFile(out / "lt-suite-plugin-0.1.0.zip") as z:
        assert {n.split('/')[0] for n in z.namelist()} == {"lt-suite"}
        assert "lt-suite/skills/sample/SKILL.md" in z.namelist()
        assert "lt-suite/LICENSE" in z.namelist()
        assert "lt-suite/THIRD_PARTY_NOTICES.md" in z.namelist()
        assert "lt-suite/LICENSES/OFL-1.1.txt" in z.namelist()
        assert "lt-suite/skills/sample/scripts/_shared/fonts/example/OFL.txt" in z.namelist()
        assert not any(set(Path(n).parts) & {"hooks", "bin"} for n in z.namelist())
        for name in ("plugin.json", ".claude-plugin/plugin.json"):
            assert "hooks" not in json.loads(z.read("lt-suite/" + name))


@pytest.mark.parametrize("target", ["gemini-app", "gemini-enterprise"])
def test_gemini_strips_only_unsupported_frontmatter(source, tmp_path, target):
    # Catches filtering nested metadata or rewriting the Markdown body.
    out = tmp_path / "dist"
    assert run(source, out).returncode == 0
    with zipfile.ZipFile(archive(out, target)) as z:
        text = z.read("sample/SKILL.md").decode()
    head, body = text[4:].split("---\n", 1)
    keys = [line.split(":", 1)[0] for line in head.splitlines() if line and not line[0].isspace()]
    assert keys == ["name", "description", "license", "metadata"]
    assert "  An example skill\n  with folded text.\n" in head
    assert "  label: 'compatibility: preserved'\n" in head
    assert body == BODY
    with zipfile.ZipFile(archive(out, "claude-ai")) as z:
        assert z.read("sample/SKILL.md").decode() == FRONT + BODY


@pytest.mark.parametrize("target", TARGETS)
def test_extracted_skill_runs_help_without_repository(source, tmp_path, target):
    # Catches loss of a sibling dependency when only one archive is installed.
    out = tmp_path / "dist"
    assert run(source, out).returncode == 0
    alone = tmp_path / "alone"
    with zipfile.ZipFile(archive(out, target)) as z:
        z.extractall(alone)
    skill = alone if target == "perplexity-ui" else alone / "sample"
    result = subprocess.run(
        [sys.executable, "scripts/hello.py", "--help"], encoding='utf-8', cwd=skill,
        env={k: v for k, v in os.environ.items() if not k.startswith("PYTHON")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout and "alone" in result.stdout


@pytest.mark.parametrize("tool", ["gen_manifests", "sync_shared"])
def test_refuses_drift_without_writing_archives(source, tmp_path, tool):
    # Catches a failed subprocess being ignored or run after publication.
    put(source, f"tools/{tool}.py", "raise SystemExit(1)\n")
    out = tmp_path / "dist"
    result = run(source, out)
    assert result.returncode == 1
    assert tool in result.stderr
    assert not list(out.rglob("*.zip"))


def test_missing_tools_fail_closed_in_partial_tree(source, tmp_path):
    # Catches partial-tree support silently skipping a missing prerequisite.
    for tool in ("gen_manifests", "sync_shared"):
        (source / f"tools/{tool}.py").unlink()
    result = run(source, tmp_path / "dist")
    assert result.returncode == 1
    assert "missing" in result.stderr.lower()
    assert not list((tmp_path / "dist").rglob("*.zip"))


def test_missing_font_license_fails_before_archives(source, tmp_path):
    # Catches distributing a bundled font after its copyright text disappears.
    (source / "skills/sample/scripts/_shared/fonts/example/OFL.txt").unlink()
    out = tmp_path / "dist"
    result = run(source, out)
    assert result.returncode == 1
    assert "font" in result.stderr.lower() and "license" in result.stderr.lower()
    assert not list(out.rglob("*.zip"))


def test_release_assets_have_unique_basenames(source, tmp_path):
    # Release uploads flatten directory names and would overwrite target variants.
    out = tmp_path / "dist"
    assert run(source, out).returncode == 0
    names = [p.name for p in out.rglob("*.zip")]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("name", ["LICENSE", "THIRD_PARTY_NOTICES.md"])
def test_missing_legal_files_fail_before_archives(source, tmp_path, name):
    # Catches a partial release that ships without mandatory legal material.
    (source / name).unlink()
    out = tmp_path / "dist"
    result = run(source, out)
    assert result.returncode == 1 and name in result.stderr
    assert not list(out.rglob("*.zip"))


def test_check_enforces_upload_file_limit(source, tmp_path):
    # Catches a --check flag that accepts an archive the target cannot upload.
    for n in range(101):
        put(source, f"skills/sample/assets/file-{n}.txt", "example")
    approve_files(source)
    out = tmp_path / "dist"
    result = run(source, out, "--check")
    assert result.returncode == 1 and "upload limit" in result.stderr
    assert not list(out.rglob("*.zip"))


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks requires Windows privileges")
def test_symlinks_cannot_escape_the_skill(source, tmp_path):
    # Catches dereferencing a repository or external file into an uploaded skill.
    (source / "skills/sample/linked").symlink_to(source / "packaging", target_is_directory=True)
    result = run(source, tmp_path / "dist")
    assert result.returncode == 1 and "symlink" in result.stderr


def test_real_answer_skill_archive_runs_digest_help(source, tmp_path):
    # Catches actual bootstrap imports escaping the unpacked skill folder.
    real = REPO / "skills/lt-module-answer"
    dest = source / "skills/lt-module-answer"
    shutil.copytree(real, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    path = source / "packaging/plugin.toml"
    path.write_text(path.read_text(encoding='utf-8').replace('["sample"]', '["sample", "lt-module-answer"]'), encoding='utf-8')
    approve_files(source)
    out = tmp_path / "dist"
    result = run(source, out)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(out / "claude-ai/lt-module-answer-claude-ai-0.1.0.zip") as z:
        z.extractall(tmp_path / "alone")
    result = subprocess.run(
        [sys.executable, "scripts/question_digest.py", "--help"],
        encoding='utf-8', cwd=tmp_path / "alone/lt-module-answer", capture_output=True, text=True,
        env={**{k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "LT_SUITE_"))},
             "LT_SUITE_NO_INSTALL": "1", "PYTHONNOUSERSITE": "1"}, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("name", [
    "reference-c1-r101.json", "module-demo.json", "answers-demo.json", "model-answer.json",
    "approvals.json", "lt-suite.toml", ".env", ".env.local", "unknown.txt",
])
@pytest.mark.parametrize("approved", [False, True])
def test_private_or_unapproved_files_fail_before_publication(source, tmp_path, name, approved):
    # A stray in-place runtime file must never reach either upload layout.
    put(source, "skills/sample/" + name, "synthetic private fixture")
    if approved and name != "unknown.txt":
        approve_files(source)
    out = tmp_path / "dist"
    result = run(source, out, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert name in result.stderr
    assert not list(out.rglob("*.zip"))


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_skill_set_is_complete(source, tmp_path, mutation):
    # Discovery must not quietly omit a missing skill or accept an unexpected folder.
    if mutation == "missing":
        (source / "skills/sample/SKILL.md").unlink()
    else:
        put(source, "skills/extra/SKILL.md", FRONT + BODY)
    result = run(source, tmp_path / "dist")
    assert result.returncode == 1
    assert "skill" in result.stderr.lower()


def test_archives_use_stored_members_across_zlib_versions(source, tmp_path):
    # Deflate output depends on the interpreter's zlib build.
    out = tmp_path / "dist"
    assert run(source, out).returncode == 0
    with zipfile.ZipFile(archive(out, "claude-ai")) as z:
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())


def test_conflicting_project_license_fails(source, tmp_path):
    put(source, "skills/sample/LICENSE", "Conflicting license.")
    approve_files(source)
    result = run(source, tmp_path / "dist")
    assert result.returncode == 1 and "conflicts" in result.stderr


def test_empty_font_license_fails(source, tmp_path):
    put(source, "skills/sample/scripts/_shared/fonts/example/OFL.txt", "")
    result = run(source, tmp_path / "dist")
    assert result.returncode == 1 and "font" in result.stderr


def test_output_inside_sources_fails(source):
    result = run(source, "skills")
    assert result.returncode == 1 and "outside source" in result.stderr


def test_stale_archive_fails(source, tmp_path):
    out = tmp_path / "dist"
    put(out, "old.zip", b"stale")
    result = run(source, out)
    assert result.returncode == 1 and "stale" in result.stderr


@pytest.mark.parametrize("mutation", ["name", "bytes", "timestamp", "permissions", "compression"])
def test_reopened_archive_detects_tampering(tmp_path, monkeypatch, mutation):
    # Archive verification must reject wrong members, contents and reproducibility metadata.
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    import build_dist as builder
    path = tmp_path / "tampered.zip"
    info = zipfile.ZipInfo("wrong" if mutation == "name" else "member", (1981, 1, 1, 0, 0, 0) if mutation == "timestamp" else (1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (0o700 if mutation == "permissions" else 0o644)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED if mutation == "compression" else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"wrong" if mutation == "bytes" else b"expected")
    with pytest.raises(builder.BuildError):
        builder.check_zip(path, {"member": b"expected"})


def test_gemini_limit_is_enforced_independently_of_perplexity(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    import build_dist as builder
    files = {"sample/LICENSE": b"license", "sample/THIRD_PARTY_NOTICES.md": b"notice", "sample/SKILL.md": b"skill"}
    files.update({f"sample/file-{i}": b"example" for i in range(98)})
    with pytest.raises(builder.BuildError, match="upload limit"):
        builder.validate_members("gemini-app", files, True)


def test_differing_agpl_copy_blocks_build(source, tmp_path):
    put(source, "LICENSES/AGPL-3.0-or-later.txt", "A different license text")
    approve_files(source)
    result = run(source, tmp_path / "dist")
    assert result.returncode == 1 and "LICENSE differs" in result.stderr


def test_unapproved_license_directory_file_cannot_bypass_allowlist(source, tmp_path):
    # Legal-directory extras share every archive and need the same file approval boundary.
    put(source, 'LICENSES/unreviewed.txt', 'Synthetic unreviewed material.')
    result = run(source, tmp_path / 'dist')
    assert result.returncode == 1 and 'unreviewed.txt' in result.stderr


@pytest.mark.parametrize('kind',['foreign','replacement','otc'])
def test_font_pins_reject_unapproved_bytes(source,tmp_path,kind):
    if kind=='foreign':
        put(source,'skills/sample/scripts/_shared/fonts/foreign/font.ttf',b'foreign')
        put(source,'skills/sample/scripts/_shared/fonts/foreign/OFL.txt','A licence')
    elif kind=='otc':
        put(source,'skills/sample/scripts/_shared/fonts/example/font.otc',b'collection')
    else:
        put(source,'skills/sample/scripts/_shared/fonts/example/font.ttf',b'replacement')
    approve_files(source)
    result=run(source,tmp_path/'dist')
    assert result.returncode != 0 and 'unapproved font or sha256 mismatch' in result.stderr


def test_fill_palette_runtime_files_are_approved():
    prefix='skills/lt-module-fill/scripts/_shared/'
    names=set(json.loads((REPO/'packaging/runtime-files.json').read_text(encoding='utf-8')))
    for name in ['ltcore/palettes.py','palettes/catalog.json','palettes/ATTRIBUTION.md',
                 'palettes/overrides/catppuccin-latte.toml','palettes/overrides/catppuccin-frappe.toml',
                 'palettes/overrides/everforest-dark-medium.toml']:
        assert prefix+name in names and (REPO/(prefix+name)).is_file()
    notice=(REPO/'skills/lt-module-fill/NOTICE.txt').read_text(encoding='utf-8')
    assert 'Catppuccin' in notice and 'sainnhe' in notice and 'MIT' in notice


def test_archives_keep_launcher_without_bootstrap(source, tmp_path):
    # A missing allowlist entry must not silently remove the standalone launcher.
    launcher = 'scripts/_shared/lt_chrome/lt-chrome.cjs'
    put(source, 'skills/sample/' + launcher, '// synthetic launcher\n')
    put(source, 'packaging/lt-chrome-bootstrap.cjs', '// generation input only\n')
    approve_files(source)
    out = tmp_path/'dist'
    result = run(source,out)
    assert result.returncode == 0, result.stderr
    for target in TARGETS:
        with zipfile.ZipFile(archive(out,target)) as z:
            assert any(name.endswith(launcher) for name in z.namelist())
            assert not any('bootstrap.cjs' in name for name in z.namelist())
    with zipfile.ZipFile(out/'lt-suite-plugin-0.1.0.zip') as z:
        assert any(name.endswith(launcher) for name in z.namelist())
        assert not any('bootstrap.cjs' in name for name in z.namelist())


@pytest.mark.parametrize('skill,script',[
    ('lt-module-fill','scripts/browser/check_answer.js'),
    ('lt-module-answer','scripts/reconcile.py')])
def test_check_and_reconcile_standalone_assets(source,tmp_path,skill,script):
    shutil.copytree(REPO/'skills'/skill,source/'skills'/skill,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    include_runtime_font_pins(source)
    p=source/'packaging/plugin.toml';p.write_text(p.read_text(encoding='utf-8').replace('["sample"]',json.dumps(['sample',skill])), encoding='utf-8')
    approve_files(source);out=tmp_path/'dist';result=run(source,out)
    assert result.returncode==0,result.stderr
    with zipfile.ZipFile(out/f'claude-ai/{skill}-claude-ai-0.1.0.zip') as archive:
        for member in (script,'scripts/_shared/schemas/reconciled.schema.json'):
            assert archive.read(skill+'/'+member)==(source/'skills'/skill/member).read_bytes()


@pytest.mark.parametrize('skill,member',[
    ('lt-module-fill','scripts/browser/check_answer.js'),
    ('lt-module-answer','scripts/reconcile.py'),
    ('lt-module-fill','scripts/_shared/schemas/reconciled.schema.json'),
    ('lt-module-answer','scripts/_shared/schemas/reconciled.schema.json')])
def test_unallowlisted_check_asset_blocks_build(source,tmp_path,skill,member):
    shutil.copytree(REPO/'skills'/skill,source/'skills'/skill,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    include_runtime_font_pins(source)
    p=source/'packaging/plugin.toml';p.write_text(p.read_text(encoding='utf-8').replace('["sample"]',json.dumps(['sample',skill])), encoding='utf-8')
    approve_files(source);manifest=source/'packaging/runtime-files.json'
    files=json.loads(manifest.read_text(encoding='utf-8'));files.remove('skills/'+skill+'/'+member);manifest.write_text(json.dumps(files), encoding='utf-8')
    result=run(source,tmp_path/'dist');assert result.returncode==1
    assert 'unapproved distribution file: skills/'+skill+'/'+member in result.stderr


def test_runtime_font_pins_read_utf8_under_cp1252(source, monkeypatch):
    import io
    monkeypatch.setattr(io, 'text_encoding', lambda encoding, *args: encoding or 'cp1252')
    include_runtime_font_pins(source)


def test_builder_font_catalog_reads_utf8_under_cp1252(monkeypatch):
    import importlib.util
    import io
    monkeypatch.syspath_prepend(str(REPO / 'tools'))
    spec = importlib.util.spec_from_file_location('j7_builder', BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(io, 'text_encoding', lambda encoding, *args: encoding or 'cp1252')
    builder.font_licenses({}, REPO)


def include_runtime_font_pins(source):
    """Copied runtime skills need their reviewed Noto pins beside the synthetic font."""
    path = source/'shared/fonts/catalog.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    runtime = json.loads((REPO/'shared/fonts/catalog.json').read_text(encoding='utf-8'))
    catalog['fonts'].extend(entry for entry in runtime['fonts'] if entry.get('bundled'))
    path.write_text(json.dumps(catalog), encoding='utf-8')
