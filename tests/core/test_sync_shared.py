# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""tools/sync_shared.py on a synthetic repository: closure, NEEDS, extras, headers,
manifest, requirements, check mode, orphans, SKIP and --strict, write boundaries."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "sync_shared.py"


def w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    # LF on every OS, like a checkout under .gitattributes eol=lf; the tool copies bytes.
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


@pytest.fixture
def fake(tmp_path) -> Path:
    r = tmp_path / "repo"
    w(r / "LICENSE", "LICENSE TEXT\n")
    w(r / "pyproject.toml", '[dependency-groups]\ncore = ["jsonschema>=4.23"]\n'
      'diagram = ["Pillow>=11.0", "numpy>=2.1"]\noptional = ["pikepdf>=9.4"]\n'
      'ci = [{include-group = "core"}, "pytest>=8"]\n')
    s = r / "shared"
    w(s / "ltcore" / "__init__.py", "NEEDS = []\ndef bootstrap():\n    return []\n")
    w(s / "ltcore" / "alpha.py", "from ltcore import beta\nimport json\nNEEDS = ['schemas/a.json']\n")
    w(s / "ltcore" / "beta.py", "from . import gamma\nNEEDS: list[str] = ['browser/x.js', 'data/']\n")
    w(s / "ltcore" / "gamma.py", "import numpy\nNEEDS = []\n")
    w(s / "ltcore" / "unused.py", "NEEDS = ['schemas/unused.json']\n")
    w(s / "ltcore" / "opt.py", "try:\n    import pikepdf\nexcept ImportError:\n    pikepdf = None\nNEEDS = []\n")
    w(s / "schemas" / "a.json", '{"a": 1}\n')
    w(s / "schemas" / "unused.json", "{}\n")
    w(s / "browser" / "x.js", "#!/usr/bin/env node\nconsole.log(1);\n")
    w(s / "data" / "one.txt", "1\n")
    w(s / "data" / "sub" / "two.txt", "2\n")
    w(s / "references" / "safety.md", "# Safety\n")
    w(s / "lexicon" / "entry.md", "---\nname: entry\n---\nbody\n")
    w(s / "fonts" / "f1" / "F.ttf", "font bytes")
    w(s / "style.defaults.toml", "[style]\n")
    w(s / "sync.toml", """
[all]
references = ["references/safety.md", "references/missing.md"]
[skill.demo]
references = ["lexicon/entry.md"]
scripts = ["fonts/", "style.defaults.toml"]
[skill.demo.notices]
"engine/icons.py" = "icon paths, Apache-2.0"
[notices]
"fonts/**" = "SIL Open Font License 1.1"
""")
    w(r / "skills" / "demo" / "SKILL.md", "---\nname: demo\n---\n")
    w(r / "skills" / "demo" / "scripts" / "run.py",
      "import sys\nfrom ltcore import alpha, bootstrap\nfrom PIL import Image\nimport helper\n")
    w(r / "skills" / "demo" / "scripts" / "helper.py", "import os\n")
    w(r / "skills" / "demo" / "engine" / "icons.py", "ICONS = {}\n")
    w(r / "skills" / "demo" / "engine" / "lazy.py",
      "import importlib\nm = importlib.import_module('ltcore.opt')\n")
    w(r / "skills" / "other" / "scripts" / "x.py", "from ltcore import gamma\n")
    _write_frozen_lock(r)
    return r


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--root", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8")


def files(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_sync_one_skill(fake):
    other_before = files(fake / "skills" / "other")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SKIP shared/references/missing.md (source not written yet)" in r.stdout
    demo = fake / "skills" / "demo"
    got = files(demo)
    want = {
        "scripts/_shared/ltcore/__init__.py", "scripts/_shared/ltcore/alpha.py",
        "scripts/_shared/ltcore/beta.py", "scripts/_shared/ltcore/gamma.py",
        "scripts/_shared/ltcore/opt.py",
        "scripts/_shared/schemas/a.json", "scripts/_shared/browser/x.js",
        "scripts/_shared/data/one.txt", "scripts/_shared/data/sub/two.txt",
        "scripts/_shared/fonts/f1/F.ttf", "scripts/_shared/style.defaults.toml",
        "scripts/_shared/MANIFEST.json",
        "references/_shared/safety.md", "references/_shared/lexicon/entry.md",
        "LICENSE.txt", "NOTICE.txt", "requirements.txt",
    }
    generated = {k for k in got if not k.startswith(("scripts/run.py", "scripts/helper.py", "engine/"))
                 and k != "SKILL.md"}
    assert generated == want
    assert "scripts/_shared/ltcore/unused.py" not in got        # not in the closure
    assert "scripts/_shared/schemas/unused.json" not in got
    # headers: .py and .js get a first line (after a shebang); data stays byte-identical
    alpha = got["scripts/_shared/ltcore/alpha.py"].decode()
    assert alpha.splitlines()[0] == ("# Generated from shared/ltcore/alpha.py by "
                                     "tools/sync_shared.py. Edit the source.")
    js = got["scripts/_shared/browser/x.js"].decode().splitlines()
    assert js[0] == "#!/usr/bin/env node" and js[1].startswith("// Generated from shared/browser/x.js")
    assert got["scripts/_shared/schemas/a.json"] == b'{"a": 1}\n'
    assert got["references/_shared/lexicon/entry.md"].startswith(b"---\n")
    assert got["LICENSE.txt"] == b"LICENSE TEXT\n"
    # requirements: Pillow and numpy pinned; pikepdf optional; stdlib and local modules ignored
    req = got["requirements.txt"].decode().splitlines()
    assert any(line.startswith("pillow==11.0.0 --hash=sha256:") for line in req)
    assert any(line.startswith("numpy==2.1.0 --hash=sha256:") for line in req)
    assert "# pikepdf>=9" in req
    assert not any(line.startswith(("helper", "json", "os", "importlib")) for line in req)
    # notice lists the bundled font and the skill-local icon file
    notice = got["NOTICE.txt"].decode()
    assert "scripts/_shared/fonts/f1/F.ttf: SIL Open Font License 1.1" in notice
    assert "engine/icons.py: icon paths, Apache-2.0" in notice
    # manifest has every copy with its sha256
    man = json.loads(got["scripts/_shared/MANIFEST.json"])
    entries = {e["path"]: e for e in man["files"]}
    assert set(entries) == {k for k in want if k.startswith(("scripts/_shared/", "references/_shared/"))
                            and k != "scripts/_shared/MANIFEST.json"}
    for path, e in entries.items():
        assert e["sha256"] == hashlib.sha256(got[path]).hexdigest()
    assert entries["references/_shared/safety.md"]["source"] == "shared/references/safety.md"
    # the other skill was not touched
    assert files(fake / "skills" / "other") == other_before


def test_check_passes_then_detects_drift_and_orphans(fake):
    assert run(fake, "--skill", "demo").returncode == 0
    r = run(fake, "--check", "--skill", "demo")
    assert r.returncode == 0 and "ok skills/demo" in r.stdout
    copy = fake / "skills" / "demo" / "scripts" / "_shared" / "ltcore" / "gamma.py"
    copy.write_text(copy.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8", newline="\n")
    orphan = fake / "skills" / "demo" / "references" / "_shared" / "stale.md"
    orphan.write_text("old\n", encoding="utf-8", newline="\n")
    cache = fake / "skills" / "demo" / "scripts" / "_shared" / "ltcore" / "__pycache__" / "x.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"\0")
    r = run(fake, "--check", "--skill", "demo")
    assert r.returncode == 1
    assert "DRIFT skills/demo/scripts/_shared/ltcore/gamma.py (differs from the generator)" in r.stdout
    assert "DRIFT skills/demo/references/_shared/stale.md (orphan: no source)" in r.stdout
    assert "__pycache__" not in r.stdout
    # a source change is drift too
    (fake / "shared" / "schemas" / "a.json").write_text('{"a": 2}\n', encoding="utf-8", newline="\n")
    assert "scripts/_shared/schemas/a.json (differs" in run(fake, "--check", "--skill", "demo").stdout
    # sync repairs everything and removes the orphan
    assert run(fake, "--skill", "demo").returncode == 0
    assert not orphan.exists()
    assert run(fake, "--check", "--skill", "demo").returncode == 0


def test_strict_turns_skip_into_failure(fake):
    r = run(fake, "--skill", "demo", "--strict")
    assert r.returncode == 1 and "SKIP shared/references/missing.md" in r.stdout
    (fake / "shared" / "references" / "missing.md").write_text("now here\n", encoding="utf-8", newline="\n")
    assert run(fake, "--skill", "demo", "--strict").returncode == 0
    assert run(fake, "--check", "--strict", "--skill", "demo").returncode == 0


def test_missing_ltcore_module_is_a_skip(fake):
    w(fake / "skills" / "demo" / "scripts" / "later.py", "from ltcore import notyet\nimport ltcore.alsonot\n")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 0
    assert "SKIP shared/ltcore/notyet.py (source not written yet)" in r.stdout
    assert "SKIP shared/ltcore/alsonot.py (source not written yet)" in r.stdout
    assert run(fake, "--skill", "demo", "--strict").returncode == 1


def test_unmapped_import_is_an_error(fake):
    w(fake / "skills" / "demo" / "scripts" / "bad.py", "import yaml\n")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 1 and "unmapped third-party import 'yaml'" in r.stdout


def test_unpinned_dist_is_an_error(fake):
    w(fake / "skills" / "demo" / "scripts" / "bad.py", "import scipy\n")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 1 and "scipy (import scipy) is not pinned" in r.stdout


def test_unreadable_skill_source_is_an_error(fake):
    bad = w(fake / "skills" / "demo" / "scripts" / "bad.py", "def (:\n")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 1 and "skills/demo/scripts/bad.py: cannot parse" in r.stdout
    bad.write_bytes(b"\xff\xfe not utf-8\n")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 1 and "skills/demo/scripts/bad.py: cannot read" in r.stdout


def test_all_skills_and_unknown_skill(fake):
    r = run(fake)
    assert r.returncode == 0
    assert (fake / "skills" / "other" / "scripts" / "_shared" / "ltcore" / "gamma.py").is_file()
    assert "numpy==2.1.0 --hash=sha256:" in (fake / "skills" / "other" / "requirements.txt").read_text(encoding="utf-8")
    assert run(fake, "--skill", "nope").returncode == 2


def test_sync_is_idempotent(fake):
    run(fake, "--skill", "demo")
    before = {p: p.stat().st_mtime_ns for p in (fake / "skills" / "demo").rglob("*") if p.is_file()}
    r = run(fake, "--skill", "demo")
    assert "0 changed" in r.stdout
    after = {p: p.stat().st_mtime_ns for p in (fake / "skills" / "demo").rglob("*") if p.is_file()}
    assert before == after


def test_real_repo_capture_sync_is_clean(repo_root):
    """The acceptance pair on the real repository: sync then check exit 0."""
    r = subprocess.run([sys.executable, str(TOOL), "--check", "--skill", "lt-module-capture"],
                       capture_output=True, text=True, cwd=repo_root, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok skills/lt-module-capture" in r.stdout


def test_notice_distinguishes_font_metadata_from_font_licences(tmp_path):
    """Font catalog code is suite data; only font binaries and licence texts use OFL."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_notice_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plan = module.Plan("demo")
    for rel in ("fonts/catalog.json", "fonts/README.md", "fonts/noto/Sans.ttf", "fonts/noto/OFL.txt"):
        dest = "scripts/_shared/" + rel
        plan.files[dest] = b""
        plan.sources[dest] = "shared/" + rel
    sync = module.load_sync(REPO)
    notice = module.notice_text("demo", plan, sync, tmp_path)
    for line in notice.splitlines():
        if "catalog.json:" in line or "README.md:" in line:
            assert "AGPL-3.0-or-later" in line, line
        if "Sans.ttf:" in line or "OFL.txt:" in line:
            assert "SIL Open Font License" in line, line


@pytest.mark.requires_bin("uv")
def test_requirements_are_frozen_hashed_and_keep_only_skill_closure(fake):
    """A skill must ship hashes and transitive pins without unrelated group packages."""
    _write_frozen_lock(fake)
    w(fake / "skills" / "demo" / "scripts" / "schema.py", "import jsonschema\n")
    before = (fake / "uv.lock").read_bytes()
    r = run(fake, "--skill", "demo")
    assert r.returncode == 0, r.stdout + r.stderr
    req = (fake / "skills" / "demo" / "requirements.txt").read_text(encoding="utf-8")
    assert 'attrs==24.0.0 --hash=sha256:' in req, req
    assert 'jsonschema==4.23.0 --hash=sha256:' in req
    assert 'pillow==11.0.0 --hash=sha256:' in req
    assert not any(line.startswith('pikepdf==') for line in req.splitlines())
    assert 'pytest' not in req and '>=' not in '\n'.join(l for l in req.splitlines() if not l.startswith('#'))
    assert (fake / "uv.lock").read_bytes() == before


def _write_frozen_lock(fake):
    """Create a complete, tiny lock for real offline uv export tests."""
    w(fake / "pyproject.toml", '[project]\nname = "demo-project"\nversion = "1"\n'
      'requires-python = ">=3.11"\n[tool.uv]\npackage = false\n'
      '[dependency-groups]\ncore = ["jsonschema>=4.23"]\n'
      'diagram = ["Pillow>=11", "numpy>=2"]\noptional = ["pikepdf>=9"]\n')
    packages = [('pillow', '11.0.0', []), ('numpy', '2.1.0', []),
                ('jsonschema', '4.23.0', ['attrs']), ('attrs', '24.0.0', []),
                ('pikepdf', '9.4.0', [])]
    lock = 'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n'
    lock += ('[[package]]\nname = "demo-project"\nversion = "1"\nsource = {virtual = "."}\n'
             '[package.dev-dependencies]\ncore = [{name = "jsonschema"}]\n'
             'diagram = [{name = "pillow"}, {name = "numpy"}]\n'
             'optional = [{name = "pikepdf"}]\n')
    for name, version, dependencies in packages:
        lock += '\n[[package]]\nname = "' + name + '"\nversion = "' + version + '"\n'
        lock += 'source = {registry = "https://pypi.org/simple"}\n'
        if dependencies:
            lock += 'dependencies = [' + ', '.join('{name = "' + d + '"}' for d in dependencies) + ']\n'
        lock += 'sdist = {url = "https://example.invalid/' + name + '.tar.gz", hash = "sha256:' + 'a'*64 + '"}\n'
    w(fake / "uv.lock", lock)


def test_missing_lock_refuses_to_publish_unpinned_requirements(fake):
    """A missing lock must fail before any generated skill file is changed."""
    (fake / "uv.lock").unlink()
    before = files(fake / "skills" / "demo")
    r = run(fake, "--skill", "demo")
    assert r.returncode == 1 and "frozen requirements" in r.stdout, r.stdout + r.stderr
    assert files(fake / "skills" / "demo") == before
