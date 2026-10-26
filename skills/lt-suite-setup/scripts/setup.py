#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""lt-suite first-time setup: bootstrap, locate, write, set, fetch-font, ping, lt-chrome, doctor.

This file starts on Python 3.8 and imports nothing outside the standard library
until a command needs the shared core (``ltcore``), which needs Python 3.11.
``bootstrap`` runs on any 3.8+ interpreter: it finds a Python 3.11+, builds
an environment outside git work trees and installs the dependencies of the installed lt-suite skills. Every
other command re-runs itself inside that venv when the current interpreter is
older than 3.11.

Commands (global flags --config and --data-dir go before or after the command):

  bootstrap   find Python 3.11+, build an external environment, install requirements
  locate      show which config file and data folder apply and whether they are writable
  write       validate a set of answers and write the config file (.bak kept)
  set         change one key in the existing config file
  fetch-font  download one catalog font into <data>/fonts after explicit approval
  ping        render the browser-check script and verify the browser tool's reply
  lt-chrome   plan browser startup, install a stable launcher or print a host snippet
  doctor      check Python, packages, binaries, fonts, palettes, data folder, browser

Exit codes: 0 ok, 1 a check or an action failed, 2 usage error or crash,
3 the user must act (no Python 3.11, no network, a download needs a yes).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

if sys.version_info < (3, 8):
    sys.exit("lt-suite setup needs Python 3.8 or newer to start. Install Python 3.11 "
             "(for example: uv python install 3.11), then run: python3.11 setup.py bootstrap")

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
SHARED_DIR = SCRIPTS_DIR / "_shared"
# Load the shared stream helper without importing the Python 3.11 core.
_stream_spec = importlib.util.spec_from_file_location(
    "lt_suite_streams", SHARED_DIR / "ltcore" / "streams.py")
_stream_module = importlib.util.module_from_spec(_stream_spec)
_stream_spec.loader.exec_module(_stream_module)
# Load this stdlib helper directly so bootstrap still starts before Python 3.11.
_req_spec = importlib.util.spec_from_file_location(
    "lt_suite_requirement_guards", SHARED_DIR / "ltcore" / "requirements.py")
_req_module = importlib.util.module_from_spec(_req_spec)
_req_spec.loader.exec_module(_req_module)
requirement_applies = _req_module.requirement_applies
MIN_PYTHON = (3, 11)
GUARD_ENV = "LT_SUITE_BOOTSTRAPPED"
MCP_PACKAGE = "chrome-devtools-mcp@1.12.0"
# BEGIN generated lt-chrome host data from packaging/plugin.toml and shared/schemas/config.schema.json
CHROME_HOST_DATA = {'browser_keys': ['adapter', 'chromium', 'chromium_user_data_dir', 'devtools_url', 'js_tmp_dir', 'lib_mode', 'lib_port', 'sink_port', 'transport'], 'enabled': ['list_pages', 'select_page', 'navigate_page', 'evaluate_script', 'take_screenshot', 'wait_for'], 'disabled': ['new_page', 'click', 'click_at', 'drag', 'fill', 'fill_form', 'hover', 'press_key', 'type_text', 'upload_file', 'handle_dialog'], 'integrity': 'sha512-lAGpH0zXT1ZdaPefP1KK/MvxwihOlu7VGQ7p4wbhD9OhN17vUC4KWk/ImUYR9DE4s+7r4r0ySV4xr2OkTa7ZMA==', 'node_gate': {'min_major': 20, '20': 19, '22': 12}, 'version': '0.2.3', 'fixed_args': ['--no-usage-statistics', '--no-performance-crux', '--no-category-input', '--no-category-network', '--no-category-memory', '--no-category-performance', '--no-category-emulation', '--redact-network-headers']}
# END generated lt-chrome host data
KNOWN_SKILLS = ("lt-suite-setup", "lt-module-capture", "lt-module-answer",
                "lt-module-fill", "lt-module-export", "mechanistic-diagram")

# Distribution name (as written in requirements.txt) -> import name. Same table as
# ltcore.DIST_TO_IMPORT; tests/setup/test_setup_bootstrap.py keeps them equal. It is
# repeated here because ltcore cannot be imported on Python older than 3.11.
DIST_TO_IMPORT = {
    "jsonschema": "jsonschema",
    "rpds-py": "rpds",
    "pillow": "PIL",
    "numpy": "numpy",
    "fonttools": "fontTools",
    "reportlab": "reportlab",
    "pypdfium2": "pypdfium2",
    "scipy": "scipy",
    "pikepdf": "pikepdf",
    "pymupdf": "fitz",
    "opencv-python-headless": "cv2",
    "scikit-learn": "sklearn",
}

# (program, needed for, install hint). "export" programs are needed by
# lt-module-export and mechanistic-diagram; "optional" ones add features.
BINARIES = (
    ("rsvg-convert", "export", "apt install librsvg2-bin | brew install librsvg"),
    ("pdfinfo", "export", "apt install poppler-utils | brew install poppler"),
    ("pdftoppm", "export", "apt install poppler-utils | brew install poppler"),
    ("pdftotext", "export", "apt install poppler-utils | brew install poppler"),
    ("xmllint", "export", "apt install libxml2-utils | brew install libxml2"),
    ("qpdf", "optional", "apt install qpdf | brew install qpdf (PDF metadata strip)"),
    ("exiftool", "optional", "apt install libimage-exiftool-perl | brew install exiftool"),
    ("potrace", "optional", "apt install potrace | brew install potrace (diagram icons)"),
    ("drawio", "optional", "https://github.com/jgraph/drawio-desktop/releases (draw.io export)"),
)
PARTS = ("python", "packages", "binaries", "fonts", "palettes", "config", "data", "browser")


# --------------------------------------------------------------------- small helpers

def say(*parts):
    print(*parts)
    sys.stdout.flush()


def err(*parts):
    print(*parts, file=sys.stderr)
    sys.stderr.flush()


def quote_command(argv):
    """One shell command line for a normal terminal on this platform."""
    argv = [str(a) for a in argv]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(a) for a in argv)


def run(argv, timeout=60, env=None, cwd=None):
    """Run a program; return (code, stdout, stderr). A missing program gives code 127."""
    # Python probes print executable paths through a pipe. Match Node's UTF-8
    # output even when the caller's Windows code page cannot encode that path.
    child_env = dict(os.environ if env is None else env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([str(a) for a in argv], encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout, env=child_env, cwd=cwd)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "not found: " + str(argv[0])
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %s s" % timeout
    except OSError as exc:
        return 126, "", str(exc)


def tail(text, lines=8):
    rows = [r for r in (text or "").strip().splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


def load_paths_module():
    """Load ltcore/paths.py by file path. It is stdlib-only and parses on 3.8, and loading
    it directly skips ltcore/__init__.py, which exits on Python older than 3.11."""
    for base in (SHARED_DIR, SKILL_DIR.parent.parent / "shared"):
        f = base / "ltcore" / "paths.py"
        if f.is_file():
            spec = importlib.util.spec_from_file_location("lt_suite_paths", str(f))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    err("error: scripts/_shared/ltcore/paths.py is missing, so this skill folder is incomplete. "
        "Reinstall the skill (a source checkout needs: python3 tools/sync_shared.py).")
    raise SystemExit(2)


def shared_root():
    """The folder that holds the ltcore package: scripts/_shared in an installed skill,
    shared/ in a source checkout."""
    for base in (SHARED_DIR, SKILL_DIR.parent.parent / "shared"):
        if (base / "ltcore" / "__init__.py").is_file():
            return base
    return None


def import_ltcore(argv=None):
    """Make the shared core importable. Needs Python 3.11; main() has already re-run
    this script in the venv when the interpreter was older."""
    root = shared_root()
    if root is None:
        err("error: scripts/_shared/ltcore is missing, so this skill folder is incomplete. "
            "Reinstall the skill (a source checkout needs: python3 tools/sync_shared.py).")
        raise SystemExit(2)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import ltcore  # noqa: F401
    args = list(sys.argv[1:] if argv is None else argv)
    data_flag = _flag_value(args, "--data-dir")
    previous = os.environ.get("LT_SUITE_DATA")
    try:
        if data_flag:
            data, _source = resolve_data_dir(data_flag, _flag_value(args, "--config"))
            os.environ["LT_SUITE_DATA"] = str(data)
        ltcore.bootstrap(argv=[str(Path(__file__).resolve())] + args)
    finally:
        if data_flag:
            if previous is None:
                os.environ.pop("LT_SUITE_DATA", None)
            else:
                os.environ["LT_SUITE_DATA"] = previous
    return ltcore


# ------------------------------------------------------- stdlib config and data lookup

def read_toml_value(path, table, key):
    """Read one string value from a TOML file. Uses tomllib on 3.11+, a line scan on
    older Pythons (enough for ``[paths] data_dir = "..."``). Returns None when absent."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        import tomllib  # 3.11+
    except ImportError:
        tomllib = None
    if tomllib is not None:
        try:
            v = tomllib.loads(text).get(table, {}).get(key)
        except Exception:
            return None
        return v if isinstance(v, str) else None
    current = ""
    for line in text.splitlines():
        m = re.match(r"^\s*\[\s*([A-Za-z0-9_.\"' -]+?)\s*\]\s*(?:#.*)?$", line)
        if m and not line.lstrip().startswith("[["):
            current = m.group(1).strip().strip("\"'")
            continue
        if current != table:
            continue
        m = re.match(r"^\s*%s\s*=\s*(\"((?:[^\"\\]|\\.)*)\"|'([^']*)')\s*(?:#.*)?$" % re.escape(key), line)
        if m:
            if m.group(3) is not None:
                return m.group(3)
            try:
                return json.loads('"' + m.group(2) + '"')
            except ValueError:
                return m.group(2)
    return None


def locate_config_file(flag, environ, cwd, paths_mod):
    """(path or None, source). Same order as ltcore.config.locate."""
    def absolute(p):
        q = Path(os.path.expanduser(str(p)))
        return q if q.is_absolute() else cwd / q
    if flag:
        return absolute(flag), "flag --config"
    if environ.get("LT_SUITE_CONFIG"):
        return absolute(environ["LT_SUITE_CONFIG"]), "env LT_SUITE_CONFIG"
    ws = cwd / "lt-suite.toml"
    if ws.is_file():
        return ws, "cwd ./lt-suite.toml"
    plat = Path(str(paths_mod.config_file(environ=environ)))
    if plat.is_file():
        return plat, "platform"
    return None, "none"


def resolve_data_dir(flag=None, config_flag=None, environ=None, cwd=None):
    """(data folder, source) using only the standard library; the same answer as
    ltcore.store.resolve_data_dir."""
    env = os.environ if environ is None else environ
    here = Path.cwd() if cwd is None else Path(cwd)
    pm = load_paths_module()
    if flag:
        return Path(pm.expand(str(flag), str(here))), "flag --data-dir"
    if env.get("LT_SUITE_DATA"):
        return Path(pm.expand(env["LT_SUITE_DATA"], str(here))), "env LT_SUITE_DATA"
    cfg, _src = locate_config_file(config_flag, env, here, pm)
    if cfg is not None and cfg.is_file():
        v = read_toml_value(cfg, "paths", "data_dir")
        if v:
            return Path(pm.expand(v, str(cfg.parent))), "config paths.data_dir"
    return Path(str(pm.data_dir(environ=env))), "default"


def environment_dir(data_dir):
    """Resolve an environment outside git work trees, including worktree symlinks."""
    pm = load_paths_module()
    if hasattr(pm, "environment_dir"):
        return Path(pm.environment_dir(data_dir))
    # The stdlib setup entry point also supports previously installed shared cores.
    for base in (Path(data_dir), Path(str(pm.data_dir()))):
        candidate = (base / "venv").absolute()
        resolved = candidate.resolve()
        if not any((p / ".git").exists() for p in (resolved,) + tuple(resolved.parents)):
            return candidate
    raise ValueError("No environment location outside a git work tree is available. "
                     "Choose an external platform data directory and run bootstrap again.")


def venv_python_path(data_dir):
    venv = environment_dir(data_dir)
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def probe_dir(path):
    """Can a file be created at or under path? (ok, detail)."""
    p = Path(path)
    probe = p
    while not probe.exists():
        if probe.parent == probe:
            return False, "no existing parent folder for %s" % p
        probe = probe.parent
    if not probe.is_dir():
        return False, "%s is not a folder" % probe
    try:
        fd, name = tempfile.mkstemp(prefix=".lt-suite-probe-", dir=str(probe))
        os.close(fd)
        os.unlink(name)
    except OSError as exc:
        return False, "cannot write in %s: %s" % (probe, exc.strerror or exc)
    return True, "writable (%s)" % probe


def ensure_modern_python_if_venv(argv, data):
    """On an interpreter older than 3.11, re-run this script inside <data>/venv when it
    exists. Returns when there is nothing to re-run (or the guard says it already ran)."""
    py = venv_python_path(data)
    if py.is_file() and os.environ.get(GUARD_ENV) != "1":
        env = dict(os.environ)
        env[GUARD_ENV] = "1"
        args = [str(py), str(Path(__file__).resolve())] + list(argv)
        sys.stdout.flush()
        if os.name == "nt":
            raise SystemExit(subprocess.call(args, env=env))
        os.execve(str(py), args, env)


def ensure_modern_python(argv):
    """Commands that need ltcore run on 3.11+. On an older interpreter, re-run this script
    inside <data>/venv when it exists; otherwise say what to do and exit 2."""
    if sys.version_info >= MIN_PYTHON:
        return
    flag_data = _flag_value(argv, "--data-dir")
    flag_cfg = _flag_value(argv, "--config")
    data, _src = resolve_data_dir(flag_data, flag_cfg)
    ensure_modern_python_if_venv(argv, data)
    cmd = quote_command([sys.executable, Path(__file__).resolve(), "bootstrap"])
    err("This command needs Python 3.11 or newer; this interpreter is %d.%d. Build the "
        "environment first: %s" % (sys.version_info[0], sys.version_info[1], cmd))
    raise SystemExit(2)


def _flag_value(argv, name):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


# ------------------------------------------------------------------------- bootstrap

def python_info(cmd):
    """(version tuple, real executable) for a command list, or None when it fails."""
    code, out, _e = run(list(cmd) + ["-c", "import sys; print(sys.executable); "
                                     "print('%d.%d.%d' % sys.version_info[:3])"], timeout=30)
    rows = out.strip().splitlines()
    if code != 0 or len(rows) < 2:
        return None
    try:
        ver = tuple(int(x) for x in rows[-1].split("."))
    except ValueError:
        return None
    return ver, rows[0]


def find_python(flag=None):
    """First Python 3.11+ from: --python, uv, version-named programs on PATH, the py
    launcher (Windows), the interpreter running this script. Returns
    (executable, version tuple, how) or None."""
    tried = []
    candidates = []
    if flag:
        candidates.append(([flag], "--python"))
    uv = shutil.which("uv")
    if uv:
        code, out, _e = run([uv, "python", "find", ">=3.11"], timeout=60)
        path = out.strip().splitlines()[-1] if out.strip() else ""
        if code == 0 and path:
            candidates.append(([path], "uv python find"))
    for name in ("python3.13", "python3.12", "python3.11", "python3.14", "python3", "python"):
        exe = shutil.which(name)
        if exe:
            candidates.append(([exe], "PATH " + name))
    if os.name == "nt" and shutil.which("py"):
        for v in ("-3.13", "-3.12", "-3.11"):
            candidates.append((["py", v], "py launcher " + v))
    if sys.version_info >= MIN_PYTHON:
        candidates.append(([sys.executable], "this interpreter"))
    for cmd, how in candidates:
        info = python_info(cmd)
        if info is None:
            tried.append("%s: does not run" % " ".join(cmd))
            continue
        ver, exe = info
        if ver >= MIN_PYTHON:
            return exe, ver, how
        tried.append("%s: Python %d.%d is too old" % (" ".join(cmd), ver[0], ver[1]))
    find_python.tried = tried
    return None


find_python.tried = []


def install_routes():
    return "\n".join([
        "No Python 3.11 or newer was found. Install one, then run bootstrap again:",
        "  any platform with uv   uv python install 3.11        (https://docs.astral.sh/uv/)",
        "  macOS with Homebrew    brew install python@3.13",
        "  Windows                winget install Python.Python.3.13   (or python.org)",
        "  Debian or Ubuntu       sudo apt install python3.12 python3.12-venv",
        "Ubuntu 22.04 ships Python 3.10 and macOS developer tools ship 3.9: use uv there.",
    ])


def parse_requirements(path):
    """(required lines, optional lines) from a generated requirements.txt. Optional lines
    are the commented ones after the '# Optional' marker."""
    required, optional = [], []
    in_optional = False
    try:
        rows = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("requirements manifest %s is unreadable or missing: %s" % (path, exc)) from exc
    for row in rows:
        s = row.strip()
        if not s:
            continue
        if s.startswith("#"):
            if s.lower().startswith("# optional"):
                in_optional = True
            elif in_optional:
                optional.append(s.lstrip("# ").strip())
            continue
        required.append(s.split("#", 1)[0].strip())
    if not required:
        raise ValueError("requirements manifest %s has no required dependencies" % path)
    return required, optional


def dist_name(req):
    m = re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", req)
    return re.sub(r"[-_.]+", "-", m.group(0)).lower() if m else ""


def installed_skill_dirs(skills_dir):
    base = Path(skills_dir)
    out = []
    own = SKILL_DIR.resolve()
    seen = set()
    for name in KNOWN_SKILLS:
        d = base / name
        if (d / "SKILL.md").is_file() or d.resolve() == own:
            out.append(d)
            seen.add(d.resolve())
    if own not in seen:
        out.insert(0, own)
    return out


def collect_requirements(skills_dir):
    """Union of the installed skills' requirements.txt: (required, optional, skills,
    conflicts). Keep distinct marker variants and their complete hash sets.
    Different pins for the same distribution and marker are conflicts."""
    required, optional, skills, conflicts = {}, {}, [], []
    for d in installed_skill_dirs(skills_dir):
        req, opt = parse_requirements(d / "requirements.txt")
        skills.append(d.name)
        for line in req:
            n = dist_name(line)
            marker = line.split(";", 1)[1].split(" --hash=", 1)[0].strip() if ";" in line else ""
            key = (n, marker)
            if n and key not in required:
                required[key] = line
            elif n and required[key] != line:
                conflicts.append("%s: %r in %s, %r kept" % (n, line, d.name, required[key]))
        for line in opt:
            n = dist_name(line)
            marker = line.split(";", 1)[1].split(" --hash=", 1)[0].strip() if ";" in line else ""
            key = (n, marker)
            if n and key not in optional and key not in required:
                optional[key] = line
    return ([required[k] for k in sorted(required)], [optional[k] for k in sorted(optional)],
            skills, conflicts)


def network_targets(environ):
    """host, port pairs to try: the proxy when one is set, else the package index."""
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        val = environ.get(var)
        if val:
            m = re.match(r"^(?:[a-z0-9+.-]+://)?(?:[^@/]*@)?([^:/]+)(?::(\d+))?", val, re.I)
            if m:
                return [(m.group(1), int(m.group(2) or 3128))]
    return [("pypi.org", 443)]


def have_network(environ=None, timeout=5.0):
    env = os.environ if environ is None else environ
    for host, port in network_targets(env):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _modules_missing(venv_py, import_names):
    """Import names the venv cannot find."""
    if not import_names:
        return []
    code = ("import importlib.util, json, sys\n"
            "print(json.dumps([m for m in %s if importlib.util.find_spec(m) is None]))"
            % json.dumps(list(import_names)))
    rc, out, _e = run([venv_py, "-c", code], timeout=60)
    if rc != 0:
        return list(import_names)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return list(import_names)


def cmd_bootstrap(a):
    env = os.environ
    here = Path.cwd()
    data, src = resolve_data_dir(a.data_dir, a.config)
    note = ""
    ok, detail = probe_dir(data)
    if not ok:
        ws = here / ".lt-suite" / "data"
        ws_ok, _d = probe_dir(ws)
        if ws_ok and not a.data_dir:
            note = ("The data folder %s (%s) is not writable: %s. Using the workspace folder "
                    "%s instead; 'setup.py write --workspace' makes the config point at it."
                    % (data, src, detail, ws))
            data, src = ws, "workspace fallback"
        else:
            err("error: the data folder %s (%s) is not writable: %s" % (data, src, detail))
            err("Run 'setup.py locate' to see the workspace layout, or pass --data-dir.")
            return 3
    try:
        venv = environment_dir(data)
    except ValueError as exc:
        err("error: %s" % exc)
        return 1
    ok, detail = probe_dir(venv.parent)
    if not ok:
        err("error: environment folder %s is not writable: %s" % (venv, detail))
        err("Run bootstrap in a normal terminal with access to the platform data directory.")
        return 3
    py_flag = a.python
    found = find_python(py_flag)
    if found is None:
        err(install_routes())
        for t in find_python.tried:
            err("  tried " + t)
        return 3
    py, ver, how = found
    skills_dir = Path(a.skills_dir) if a.skills_dir else SKILL_DIR.parent
    try:
        required, optional, skills, conflicts = collect_requirements(skills_dir)
    except ValueError as exc:
        err("error: %s" % exc)
        return 1
    if a.with_optional:
        required = required + optional
    uv = shutil.which("uv")
    plan = {
        "python": py, "python_version": "%d.%d.%d" % ver, "found_by": how,
        "data_dir": str(data), "data_dir_source": src, "venv": str(venv),
        "skills": skills, "requirements": required, "optional_requirements": optional,
        "installer": "uv" if uv else "pip", "conflicts": conflicts,
    }
    if a.dry_run:
        _emit(plan, a.json)
        return 0
    if note:
        say(note)
    venv_py = venv_python_path(data)
    existing = python_info([venv_py]) if venv_py.is_file() else None
    if existing is None or existing[0] < MIN_PYTHON:
        if venv.exists():
            if (venv / "pyvenv.cfg").is_file():
                shutil.rmtree(str(venv), ignore_errors=True)
            else:
                err("error: %s exists and is not a Python environment; move it away." % venv)
                return 1
        venv.parent.mkdir(parents=True, exist_ok=True)
        cmd = [uv, "venv", "--python", py, str(venv)] if uv else [py, "-m", "venv", str(venv)]
        rc, out, e = run(cmd, timeout=300)
        if rc != 0 or not venv_py.is_file():
            err("error: could not create the environment with: " + quote_command(cmd))
            err(tail(e or out))
            return 1
        say("created %s with Python %d.%d.%d" % (venv, ver[0], ver[1], ver[2]))
    else:
        say("reusing %s (Python %d.%d.%d)" % (venv, existing[0][0], existing[0][1], existing[0][2]))
    plan["venv_python"] = str(venv_py)
    for c in conflicts:
        say("note: conflicting pins: " + c)
    reqfile = venv / "lt-suite-requirements.txt"
    with reqfile.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(required) + ("\n" if required else ""))
    want = {dist_name(r): DIST_TO_IMPORT.get(dist_name(r), dist_name(r).replace("-", "_"))
            for r in required if requirement_applies(r, ver)}
    missing = _modules_missing(str(venv_py), sorted(set(want.values())))
    plan["missing_before"] = missing
    if not missing:
        say("all %d packages are already installed in the environment." % len(want))
        plan["installed"] = []
        _emit(plan, a.json, quiet=True)
        return 0
    want_net = a.network
    if want_net == "auto":
        want_net = "yes" if have_network(env) else "no"
    if want_net == "no":
        rerun = quote_command([sys.executable, Path(__file__).resolve(), "bootstrap",
                               "--data-dir", data])
        err("No network is reachable from this shell, so the packages (%s) were not installed."
            % ", ".join(missing))
        err("Run this one command in a normal terminal, then continue here:")
        err("  " + rerun)
        return 3
    if uv:
        cmd = [uv, "pip", "install", "--python", str(venv_py),
               "--require-hashes", "--only-binary", ":all:", "-r", str(reqfile)]
    else:
        cmd = [str(venv_py), "-m", "pip", "install", "--disable-pip-version-check",
               "--require-hashes", "--only-binary", ":all:", "-r", str(reqfile)]
    say("installing: " + quote_command(cmd))
    rc, out, e = run(cmd, timeout=1800)
    if rc != 0:
        err("error: the install failed (exit %d)." % rc)
        err(tail(e or out, 14))
        return 1
    still = _modules_missing(str(venv_py), sorted(set(want.values())))
    plan["installed"] = [m for m in missing if m not in still]
    if still:
        err("error: still missing after the install: " + ", ".join(still))
        return 1
    say("installed %d package group(s) into %s" % (len(plan["installed"]), venv))
    say("Entry scripts now re-run themselves inside this environment when a package is missing.")
    _emit(plan, a.json, quiet=True)
    return 0


def _emit(plan, as_json, quiet=False):
    if as_json:
        say(json.dumps(plan, indent=1))
    elif not quiet:
        for k in ("python", "python_version", "found_by", "data_dir", "data_dir_source", "venv",
                  "installer"):
            say("%-16s %s" % (k, plan[k]))
        say("%-16s %s" % ("skills", ", ".join(plan["skills"])))
        say("%-16s %s" % ("requirements", ", ".join(plan["requirements"]) or "none"))


# ---------------------------------------------------------------------------- locate

def cmd_locate(a):
    ensure_modern_python(a.argv)
    import_ltcore(a.argv)
    from ltcore import config
    rep = config.locate_report(a.config)
    pm = load_paths_module()
    data = rep["probes"].get("data_dir", {}).get("path")
    venv_py = venv_python_path(data) if data else None
    rep["environment"] = {
        "python": "%d.%d.%d" % sys.version_info[:3],
        "venv_exists": bool(venv_py and venv_py.is_file()),
        "platform_config_file": str(pm.config_file()),
        "tools": {n: bool(shutil.which(n)) for n in ("uv", "node", "npx", "rsvg-convert")},
    }
    if a.json:
        say(json.dumps(rep, indent=1))
        return 0
    c = rep["config"]
    say("config    %s  (%s, %s)" % (c["path"] or c["target"], c["source"],
                                    "exists" if c["exists"] else "no file yet"))
    for name, label in (("config_dir", "config folder"), ("data_dir", "data folder"),
                        ("loopback", "loopback")):
        p = rep["probes"].get(name)
        if p:
            say("probe     %-14s %-4s %s" % (label, "OK" if p["ok"] else "FAIL", p["detail"]))
    prop = rep["proposal"]
    say("proposal  %s  (%s)" % (prop["path"], prop["reason"]))
    if prop["workspace"]:
        say("          workspace layout: write with 'setup.py write --workspace --answers FILE'")
        for sect, vals in prop["values"].items():
            for k, v in vals.items():
                say("          %s.%s = %s" % (sect, k, json.dumps(v)))
    env = rep["environment"]
    say("python    %s  venv %s" % (env["python"], "built" if env["venv_exists"] else "not built"))
    return 0


# ----------------------------------------------------------------------- write and set

CREDENTIAL_KEY = re.compile(r"(?i)(password|passwd|cookie|secret|api[_-]?key|storage[_-]?state|"
                            r"bearer|session[_-]?id|(?<![a-z])token(?![a-z]))")
CREDENTIAL_URL = re.compile(r"(?i)://[^/\s:@]+:[^/\s@]+@|[?&](token|password|key|secret)=")


def credential_findings(doc, prefix=""):
    """Keys or values that look like credentials. Credentials are never stored."""
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            where = prefix + str(k)
            if CREDENTIAL_KEY.search(str(k)):
                out.append(where + " (key name)")
            out.extend(credential_findings(v, where + "."))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.extend(credential_findings(v, "%s[%d]." % (prefix.rstrip("."), i)))
    elif isinstance(doc, str) and CREDENTIAL_URL.search(doc):
        out.append(prefix.rstrip(".") + " (value looks like a credential)")
    return out


def read_answers(src):
    if src == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(src).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError("%s: cannot read: %s" % (src, exc.strerror or exc))
    try:
        doc = json.loads(text)
    except ValueError:
        import tomllib
        try:
            doc = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError("%s: neither JSON nor TOML: %s" % (src, exc))
    if not isinstance(doc, dict):
        raise ValueError("%s: the top level must be a table (object)" % src)
    return doc


def deep_merge(base, over):
    """Tables merge key by key; arrays and scalars in over replace those in base."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _target_for_write(a, config_mod):
    """(Location, target Path). --workspace puts the config in ./lt-suite.toml."""
    if a.workspace and not a.config:
        target = Path.cwd() / "lt-suite.toml"
        return config_mod.Location(target, "cwd ./lt-suite.toml", target, "cwd ./lt-suite.toml")
    loc = config_mod.locate(a.config)
    return config_mod.Location(loc.path, loc.source, loc.target, loc.target_source)


def cmd_write(a):
    ensure_modern_python(a.argv)
    import_ltcore(a.argv)
    from ltcore import config, store
    try:
        doc = read_answers(a.answers)
        doc.setdefault("schema_version", config.SCHEMA_VERSION)
        bad = credential_findings(doc)
        if bad:
            err("error: credentials are never stored in the config: " + ", ".join(bad))
            return 1
        loc = _target_for_write(a, config)
        if a.workspace:
            for sect, vals in config.WORKSPACE_VALUES.items():
                t = doc.setdefault(sect, {})
                for k, v in vals.items():
                    t.setdefault(k, v)
        if a.merge and loc.target.is_file():
            doc = deep_merge(config.read_toml(loc.target), doc)
        config.build(doc, config.Location(loc.target, loc.source, loc.target, loc.target_source))
        if a.stdout:
            sys.stdout.write(config.render(doc))
            return 0
        out = config.write(doc, loc.target, force=a.force or a.merge)
    except (ValueError, config.ConfigError) as exc:
        err("error: %s" % exc)
        return 1
    say("wrote %s" % out)
    try:
        cfg = config.load(out)
        st = store.Store(cfg).ensure()
        say("data folder %s  [%s]" % (st.data_dir, st.data_source))
    except Exception as exc:  # the config is written; a folder problem is reported by doctor
        err("note: could not prepare the data folder: %s" % exc)
    say("Next: python3 \"%s\" bootstrap --config \"%s\"" % (Path(__file__).resolve(), out))
    return 0


def parse_value(text):
    """A command-line value: JSON when it parses (true, 5, ["a"]), else the plain string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def cmd_set(a):
    ensure_modern_python(a.argv)
    import_ltcore(a.argv)
    from ltcore import config
    keys = a.key.split(".")
    if not all(re.match(r"^[A-Za-z0-9_-]+$", k) for k in keys):
        err("error: %r is not a dotted config key" % a.key)
        return 2
    node = value = parse_value(a.value)
    for k in reversed(keys):
        node = {k: node}
    loc = config.locate(a.config)
    if loc.path is None or not loc.exists:
        err("error: no config file to edit at %s; run 'setup.py write' first." % loc.target)
        return 1
    try:
        bad = credential_findings(node)
        if bad:
            err("error: credentials are never stored in the config: " + ", ".join(bad))
            return 1
        doc = deep_merge(config.read_toml(loc.path), node)
        out = config.write(doc, loc.path, force=True)
    except config.ConfigError as exc:
        err("error: %s" % exc)
        return 1
    say("set %s = %s in %s (previous file kept as %s.bak)" % (a.key, json.dumps(value), out, out.name))
    return 0


# ------------------------------------------------------------------------- fetch-font

def cmd_fetch_font(a, opener=None):
    """Download one catalog font into <data>/fonts/<id>/ with ltcore.fonts.fetch, which
    checks every file against its pinned sha256 and writes nothing on a mismatch. The
    download needs the user's yes, passed as --yes."""
    ensure_modern_python(a.argv)
    import_ltcore(a.argv)
    from ltcore import config, store
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _catalog
    fonts = _catalog.fonts_module()
    if fonts is None:
        err("error: ltcore.fonts is missing from this install; reinstall the skill")
        return 1
    try:
        cfg = config.load(a.config, validate=False)
    except config.ConfigError as exc:
        err("error: %s" % exc)
        return 1
    plan = _catalog.fetch_plan(a.font)
    if plan is None:
        err("error: no font %r in the catalog. 'preview.py fonts' lists the ids." % a.font)
        return 1
    entry, files, licence = plan
    if not files:
        err("error: %s has no pinned download. Install it from your package manager: %s"
            % (entry["family"], entry.get("packages") or "see the font catalog"))
        return 1
    if cfg.get("fonts.allow_download", True) is False:
        err("fonts.allow_download is false in the config, so nothing is downloaded. "
            "Set it to true with: setup.py set fonts.allow_download true")
        return 3
    st = store.Store(cfg, data_dir=a.data_dir)
    dest = st.fonts_dir
    say("font     %s (%s)  licence: %s" % (entry["family"], entry["id"], licence))
    for url, sha, name in files:
        say("download %s  sha256 %s...  as %s" % (url, sha[:12], name))
    say("into     %s" % (dest / entry["id"]))
    if not a.yes:
        err("Nothing downloaded yet. Ask the user whether to fetch these files, then re-run "
            "with --yes.")
        return 3
    kwargs = {"dest_root": dest}
    if opener is not None:
        kwargs["opener"] = opener
    try:
        written = fonts.fetch(entry["id"], cfg, **kwargs)
    except fonts.FontError as exc:
        err("error: %s" % exc)
        return 1
    for p in written:
        say("wrote %s" % p)
    say("Use it with: setup.py set fonts.%s \"%s\"" % (entry.get("role", "sans"), entry["family"]))
    say("For diagrams: setup.py set fonts.diagram.%s \"%s\"" % (entry.get("role", "sans"), entry["family"]))
    return 0


# ---------------------------------------------------------------------------- ping

def cmd_ping(a):
    """Browser check helpers. ``ping render`` writes the ping script for the configured
    adapter through ltcore.ltjs; ``ping verify`` checks the reply the browser tool gave."""
    ensure_modern_python(a.argv)
    import_ltcore(a.argv)
    from ltcore import config, ltjs
    try:
        cfg = config.load(a.config, validate=False)
    except config.ConfigError as exc:
        err("error: %s" % exc)
        return 1
    if a.ping_cmd == "render":
        args = {}
        origin = cfg.get("lt.origin", "")
        if origin:
            args["origin"] = origin
        try:
            info = ltjs.render(str(SCRIPTS_DIR / "browser" / "ping.js"), form=a.form,
                               stub=a.stub, args=args, cfg=cfg, out_dir=a.out_dir)
        except ltjs.RenderRefused as exc:
            err("error: %s" % exc)
            return 1
        except OSError as exc:
            err("error: could not write the rendered script: %s" % exc)
            return 1
        say(json.dumps(info))
        return 0
    try:
        reply = json.loads(Path(a.reply_file).read_text(encoding="utf-8") if a.reply_file else a.reply)
    except (ValueError, OSError, TypeError):
        err("error: the reply is not JSON. Paste the JSON the browser tool returned, unchanged.")
        return 1
    if isinstance(reply, str):  # some tools return the JSON as a quoted string
        try:
            reply = json.loads(reply)
        except ValueError:
            pass
    if not isinstance(reply, dict):
        err("error: the reply must be a JSON object.")
        return 1
    if reply.get("ok") is not True:
        why = reply.get("fatal") or reply.get("reason") or "the script did not report ok"
        err("FAIL ping: %s" % why)
        if reply.get("fatal") == "forbidden-route":
            err("The guard refuses to run on a completion page, a report page or a preview. "
                "Put the Lt tab on an ordinary page of the run and try again.")
        return 1
    problems = []
    if reply.get("lt_client") is not True:
        problems.append("the Lt client (window.ko) is not loaded in this tab; open an Lt run page "
                        "and wait for it to finish loading")
    expected = cfg.get("lt.origin", "")
    actual = reply.get("origin")
    if not isinstance(actual, str) or not actual:
        problems.append("the reply has no origin; run the rendered ping in the Lt tab again")
    elif expected and actual.rstrip("/") != expected.rstrip("/"):
        problems.append("the tab's origin %s differs from lt.origin %s" % (actual, expected))
    if problems:
        for p in problems:
            err("FAIL ping: " + p)
        return 1
    say("PASS ping: origin %s, path kind %s, Lt client present"
        % (reply.get("origin"), reply.get("path_kind", "?")))
    if a.record_dir:
        from ltcore import config as config_mod
        loc = config_mod.locate(a.config)
        if loc.path is None or not loc.exists:
            err("note: no config file yet, so %s was not recorded; write the config first" % a.record_dir)
            return 0
        doc = deep_merge(config_mod.read_toml(loc.path), {"browser": {"js_tmp_dir": a.record_dir}})
        config_mod.write(doc, loc.path, force=True)
        say("recorded browser.js_tmp_dir = %s in %s" % (json.dumps(a.record_dir), loc.path))
    return 0


# ---------------------------------------------------------------------------- doctor

class Result(object):
    def __init__(self, part, status, text):
        self.part, self.status, self.text = part, status, text

    def as_dict(self):
        return {"part": self.part, "status": self.status, "detail": self.text}


def required_distributions(skills_dir):
    """(own required dists, other skills' required dists, optional dists)."""
    own, other, optional = [], [], []
    for d in installed_skill_dirs(skills_dir):
        req, opt = parse_requirements(d / "requirements.txt")
        target = own if d.resolve() == SKILL_DIR.resolve() else other
        target.extend(dist_name(r) for r in req if requirement_applies(r))
        optional.extend(dist_name(r) for r in opt if requirement_applies(r))
    return own, [n for n in other if n not in own], optional


def import_name(dist):
    return DIST_TO_IMPORT.get(dist, dist.replace("-", "_"))


def check_python(ctx):
    v = sys.version_info
    where = "%d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable)
    venv_py = venv_python_path(ctx["data"])
    inside = os.path.realpath(sys.prefix) == os.path.realpath(str(environment_dir(ctx["data"])))
    state = "inside the setup environment" if inside else (
        "setup environment built" if venv_py.is_file() else "no setup environment")
    if v < MIN_PYTHON:
        return [Result("python", "FAIL", "%s is older than 3.11. Run: %s" % (
            where, quote_command([sys.executable, Path(__file__).resolve(), "bootstrap"])))]
    return [Result("python", "PASS", "%s (%s)" % (where, state))]


def check_packages(ctx):
    skills_dir = ctx["skills_dir"]
    try:
        own, other, optional = required_distributions(skills_dir)
    except ValueError as exc:
        return [Result("packages", "FAIL", str(exc))]
    out = []
    miss_own = [n for n in sorted(set(own)) if importlib.util.find_spec(import_name(n)) is None]
    miss_other = [n for n in sorted(set(other)) if importlib.util.find_spec(import_name(n)) is None]
    hint = "run: " + quote_command([sys.executable, Path(__file__).resolve(), "bootstrap"])
    if miss_own:
        out.append(Result("packages", "FAIL", "setup needs %s; %s" % (", ".join(miss_own), hint)))
    elif own:
        out.append(Result("packages", "PASS", "setup packages present: %s" % ", ".join(sorted(set(own)))))
    else:
        out.append(Result("packages", "PASS", "setup needs no third-party packages"))
    if miss_other:
        out.append(Result("packages", "WARN", "other skills need %s; %s" % (", ".join(miss_other), hint)))
    elif other:
        out.append(Result("packages", "PASS", "other skills' packages present: %s"
                          % ", ".join(sorted(set(other)))))
    have_opt = [n for n in sorted(set(optional)) if importlib.util.find_spec(import_name(n))]
    if optional:
        out.append(Result("packages", "PASS", "optional packages installed: %s"
                          % (", ".join(have_opt) or "none")))
    return out


def check_binaries(ctx):
    out = []
    for name, need, hint in BINARIES:
        path = shutil.which(name)
        if path:
            out.append(Result("binaries", "PASS", "%s at %s" % (name, path)))
        elif need == "export":
            out.append(Result("binaries", "WARN", "%s not found; lt-module-export and "
                              "mechanistic-diagram need it (%s)" % (name, hint)))
        else:
            out.append(Result("binaries", "WARN", "%s not found; optional (%s)" % (name, hint)))
    return out


def check_data(ctx):
    data, src = ctx["data"], ctx["data_src"]
    ok, detail = probe_dir(data)
    out = [Result("data", "PASS" if ok else "FAIL", "%s [%s]: %s" % (data, src, detail))]
    return out


def check_config(ctx):
    from ltcore import config
    try:
        cfg = config.load(ctx["config"])
    except config.ConfigError as exc:
        return [Result("config", "FAIL", str(exc).replace("\n", " "))]
    out = []
    if cfg.path is None:
        out.append(Result("config", "WARN", "no config file; built-in defaults apply. "
                          "Run the setup flow to write one."))
    else:
        out.append(Result("config", "PASS", "%s (%s) is valid" % (cfg.path, cfg.source)))
    for w in cfg.warnings:
        out.append(Result("config", "WARN", w))
    if not cfg.get("lt.origin", ""):
        out.append(Result("config", "WARN", "lt.origin is not set; capture and fill need it"))
    out.append(Result("config", "PASS", "fill.check_answer.enabled = %s" % str(cfg.get("fill.check_answer.enabled")).lower()))
    ctx["cfg"] = cfg
    return out


def check_fonts(ctx):
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _catalog
    return [Result("fonts", s, t) for s, t in _catalog.check_fonts(ctx.get("cfg"))]


def check_palettes(ctx):
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _catalog
    return [Result("palettes", s, t) for s, t in _catalog.check_palettes(ctx.get("cfg"))]


def chrome_launcher():
    return SHARED_DIR / "lt_chrome" / "lt-chrome.cjs"


def chrome_plan(config_path=None):
    node = shutil.which("node")
    if not node:
        raise ValueError("no node on PATH: lt-chrome cannot start")
    env = dict(os.environ)
    if config_path:
        env["LT_SUITE_CONFIG"] = str(config_path)
    rc, output, _ = run([node, chrome_launcher(), "--plan", "--redact"], env=env)
    if rc:
        raise ValueError("lt-chrome plan failed; reinstall lt-suite and check Node.js")
    try:
        return json.loads(output)
    except ValueError:
        raise ValueError("lt-chrome plan returned invalid JSON; reinstall lt-suite")


def cmd_lt_chrome(a):
    ensure_modern_python(a.argv)
    import_ltcore(a.argv)
    from ltcore import paths
    target = Path(str(paths.config_dir())) / "lt-chrome.cjs"
    if a.chrome_cmd == "plan":
        try:
            result = chrome_plan(a.config)
        except ValueError as exc:
            err(str(exc))
            return 1
        say(json.dumps(result, indent=2))
        return 0
    if a.chrome_cmd == "install":
        if target.is_symlink():
            err("lt-chrome install refuses a symlink target")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".lt-chrome-", delete=False) as temp:
                name = Path(temp.name)
                temp.write(chrome_launcher().read_bytes())
                os.fchmod(temp.fileno(), 0o644) if hasattr(os, "fchmod") else os.chmod(name, 0o644)
            if target.is_symlink():
                err("lt-chrome install refuses a symlink target")
                return 1
            os.replace(name, target)
        finally:
            if name is not None and name.exists():
                name.unlink()
        say(str(target))
        return 0
    server = {"command": "node", "args": [str(target)]}
    forward = ["LT_SUITE_CONFIG", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"]
    if a.host == "codex":
        server.update(enabled_tools=CHROME_HOST_DATA["enabled"], startup_timeout_sec=30,
                      default_tools_approval_mode="prompt", env_vars=forward)
        say('[mcp_servers."lt-chrome"]')
        for key, value in server.items():
            say("%s = %s" % (key, json.dumps(value)))
    else:
        if a.host == "gemini-settings":
            server.update(excludeTools=CHROME_HOST_DATA["disabled"], trust=False)
        elif a.host == "antigravity":
            server["disabledTools"] = CHROME_HOST_DATA["disabled"]
        if a.host in ("vscode", "copilot"):
            server["type"] = "stdio"
        say(json.dumps({"servers" if a.host == "vscode" else "mcpServers": {"lt-chrome": server}}, indent=2))
    return 0


def check_devtools_file(folder, browser):
    out = []
    folder = Path(folder)
    if not folder.is_dir():
        out.append(Result("browser", "WARN", 'folder %s not found: choose browser.chromium = "other" and set '
                          'browser.chromium_user_data_dir (Snap and Flatpak keep profiles elsewhere)' % folder))
    try:
        lines = (folder / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        text = ("no DevToolsActivePort in %s: start %s, open chrome://inspect/#remote-debugging "
                "and turn on remote debugging" % (folder, browser))
        out.append(Result("browser", "WARN", text))
        return out
    except (OSError, UnicodeError):
        return out + [Result("browser", "FAIL", "DevToolsActivePort in %s cannot be read" % folder)]
    if len(lines) < 2 or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]{1,64}", lines[1]):
        out.append(Result("browser", "FAIL", "DevToolsActivePort in %s has an unexpected second line" % folder))
    elif not re.fullmatch(r"[0-9]{1,5}", lines[0]) or not 1 <= int(lines[0]) <= 65535:
        out.append(Result("browser", "FAIL", "DevToolsActivePort in %s has an invalid port" % folder))
    else:
        out.append(Result("browser", "PASS", "DevToolsActivePort found, port %d" % int(lines[0])))
    return out


def probe_devtools(url):
    # No proxies and no redirects: only the explicitly configured loopback listener.
    import urllib.request
    from urllib.parse import urlsplit
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    port = urlsplit(url).port
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url.rstrip("/") + "/json/version", timeout=3) as response:
            data = json.loads(response.read(65537))
        ws = urlsplit(data.get("webSocketDebuggerUrl", ""))
        loopback = (ws.scheme in ("ws", "wss") and ws.hostname in ("127.0.0.1", "::1")
                    and not ws.username and not ws.password)
        return Result("browser", "PASS" if loopback else "FAIL",
                      "/json/version on port %d names a %s WebSocket" % (port, "loopback" if loopback else "non-loopback"))
    except (OSError, ValueError, TypeError, AttributeError):
        return Result("browser", "FAIL", "/json/version on port %d could not be checked" % port)


def check_browser(ctx):
    cfg = ctx.get("cfg")
    adapter = cfg.get("browser.adapter", "auto") if cfg is not None else "auto"
    from ltcore import paths
    out = []
    def add(status, text):
        out.append(Result("browser", status, text))
    config_path = ctx.get("config")
    try:
        plan = chrome_plan(config_path)
    except ValueError as exc:
        add("WARN", str(exc))
        plan = None
    if plan:
        add("FAIL" if plan["action"] == "refuse" else "PASS", "lt-chrome plan: %s (config %s, %s)" %
            (plan["action"], plan["config"], plan["config_source"]))
        if plan["reason"]:
            add("FAIL" if plan["action"] == "refuse" else "WARN", plan["reason"])
        version = plan["node_version"]
        major, minor = [int(v) for v in version.split(".")[:2]]
        gate = CHROME_HOST_DATA["node_gate"]
        old = major < gate["min_major"] or (str(major) in gate and minor < gate[str(major)])
        add("FAIL" if old else "WARN" if major == 21 else "PASS",
            "node %s%s" % (version, " is too old" if old else
                " is outside chrome-devtools-mcp's supported range" if major == 21 else ""))
        if cfg is not None and getattr(cfg, "path", None) and plan["action"] != "refuse":
            for key, value in plan["browser"].items():
                expected = cfg.get("browser." + key, "")
                if key == "devtools_url":
                    import hashlib
                    digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
                    if digest != plan.get("devtools_url_sha256"):
                        add("FAIL", "lt-chrome reads value hidden but lt-suite reads value hidden for browser.devtools_url")
                    continue
                elif key == "chromium_user_data_dir" and value:
                    value = (plan["profile_dir"] if plan["action"] == "start" and plan["route"] == "auto-connect"
                             else paths.expand(value, Path(plan["config"]).parent))
                if value != expected:
                    add("FAIL", "lt-chrome reads %s but lt-suite reads %s for browser.%s" % (value, expected, key))
    quiet = adapter in ("claude-in-chrome", "playwright-mcp", "manual-paste") or (plan and plan["action"] == "serve-none")
    if quiet:
        add("PASS", "adapter %s: lt-chrome serves no tools%s" % (adapter,
            "; setup step 7 checks Claude in Chrome" if adapter == "claude-in-chrome" else ""))
    elif adapter == "auto":
        add("PASS", "adapter auto: the agent picks Claude in Chrome, then lt-chrome (%s), then Playwright MCP, then manual paste" % MCP_PACKAGE)
    npx = shutil.which("npx")
    if not quiet or adapter == "playwright-mcp":
        add("PASS" if npx else "WARN", "npx found at %s" % npx if npx else "npx not found; install Node.js with npm")
    starts = bool(plan and plan["action"] == "start" and not quiet)
    cache = Path(plan["cache_dir"]) if plan else Path(str(paths.cache_dir()))
    records = []
    for pattern in ("last-start-*.json", "last-fallback-*.json"):
        for file in sorted((cache / "lt-chrome").glob(pattern)):
            try:
                record = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                add("WARN", "lt-chrome start record cannot be read")
                continue
            records.append(record)
            via, when = record.get("via", "unknown"), record.get("time", "unknown")
            if record.get("action") == "fallback":
                matches = starts and plan["browser"] == {"adapter":adapter, "chromium":"chrome", "chromium_user_data_dir":"", "devtools_url":""}
                if matches:
                    add("PASS", "lt-chrome started the 0.1.0 fallback via %s at %s; that matches your browser settings" % (via, when))
                else:
                    text = ("lt-chrome started the 0.1.0 fallback via %s at %s: that agent did not pass the plugin folder, "
                            "so it ignored browser.adapter = %s and browser.chromium = %s and offers up to 15 tools. "
                            "Run setup.py lt-chrome snippet --host <host>, or turn lt-chrome off in that agent." %
                            (via, when, adapter, cfg.get("browser.chromium", "chrome") if cfg else "chrome"))
                    if cfg is not None and cfg.get("browser.devtools_url", ""):
                        text += " browser.devtools_url is set."
                    if record.get("reason") == "no lt-chrome launcher under the plugin folder":
                        text += "; the plugin folder lacks the launcher: reinstall lt-suite"
                    add("WARN", text)
            else:
                add("PASS", "lt-chrome last start via %s at %s: %s" % (via, when, record.get("action")))
                current = str(getattr(cfg, "path", None) or config_path or "")
                if str(record.get("config") or "") != current:
                    add("WARN", "lt-chrome last started with config %s, but lt-suite reads %s: the agent runs lt-chrome from another folder; set LT_SUITE_CONFIG or use the platform config" % (record.get("config"), current))
    if starts and not records:
        add("WARN", "lt-chrome has no start record: no agent session has started it yet, the agent could not run node, or the cache folder is not writable.")
    if starts:
        folder = cache / "lt-chrome" / "run"
        add("WARN" if plan["reason"] else "PASS", "%s cannot be written: lt-chrome's npx runs in the agent's working folder, as in 0.1.0" % folder if plan["reason"] else "npx working folder %s" % folder)
        if plan["route"] == "auto-connect":
            add("PASS", "auto-connect to %s, user data folder %s" % (plan["browser"]["chromium"], plan["profile_dir"]))
            out.extend(check_devtools_file(plan["profile_dir"], plan["browser"]["chromium"]))
        else:
            from urllib.parse import urlsplit
            url = cfg.get("browser.devtools_url", "") if cfg else ""
            if url:
                add("PASS", "browser-url on loopback port %d" % urlsplit(url).port)
                if ctx.get("probe_devtools"):
                    out.append(probe_devtools(url))
    if plan and any(r.get("action") == "fallback" for r in records):
        stable = plan["chrome_stable_profile_dir"]
        if not (starts and plan["profile_dir"] == stable):
            out.extend(check_devtools_file(stable, "chrome"))
    prefetch = not quiet and (adapter in ("chrome-devtools-mcp", "antigravity") or ctx.get("prefetch_mcp"))
    if prefetch and starts:
        # The launcher's command contains the hardened npx prefix, without browser flags.
        prefix = plan["argv"][:plan["argv"].index("-y") + 2]
        env = dict(os.environ)
        for key in plan["env_removed"]:
            env.pop(key, None)
        env.update(plan["env_set"])
        rc, _, _ = run(prefix + ["--version"], env=env, timeout=240, cwd=plan["cwd"])
        add("PASS" if rc == 0 else "WARN", "%s pre-fetched" % MCP_PACKAGE if rc == 0 else "could not pre-fetch %s" % MCP_PACKAGE)
        npm = shutil.which("npm")
        if npm:
            rc, integrity, _ = run([npm, "view", MCP_PACKAGE, "dist.integrity"], env=env, cwd=plan["cwd"], timeout=30)
            matches = rc == 0 and integrity.strip() == CHROME_HOST_DATA["integrity"]
            add("PASS" if matches else "WARN", "registry integrity %s the pinned sha512" % ("matches" if matches else "differs from"))
    elif not quiet:
        add("PASS", "pre-fetch of %s skipped (use --prefetch-mcp)" % MCP_PACKAGE)
    stable_launcher = Path(str(paths.config_dir())) / "lt-chrome.cjs"
    if stable_launcher.is_file():
        node = shutil.which("node")
        if node:
            rc, output, _ = run([node, stable_launcher, "--print-pins"])
            try:
                pins = json.loads(output) if rc == 0 else {}
            except ValueError:
                pins = {}
            expected = {"browser_keys": CHROME_HOST_DATA["browser_keys"], "lt_suite_version": CHROME_HOST_DATA["version"], "package": MCP_PACKAGE,
                        "fixed_args": CHROME_HOST_DATA["fixed_args"], "node_gate": CHROME_HOST_DATA["node_gate"]}
            if pins != expected:
                add("WARN", "stable launcher %s is %s; run setup.py lt-chrome install" % (stable_launcher, pins.get("lt_suite_version", "unknown version")))
    from ltcore import ltjs
    tmp = ltjs.out_dir_for(cfg)
    try:
        ltjs.private_dir(tmp)
    except ltjs.RenderRefused as exc:
        out.append(Result("browser", "FAIL", str(exc)))
    else:
        ok, detail = probe_dir(tmp)
        out.append(Result("browser", "PASS" if ok else "FAIL", "js_tmp_dir %s: %s" % (tmp, detail)))
    return out


def check_rat(ctx):
    from ltcore import config, rat, page_kinds  # shared closure for standalone setup
    try:
        cfg = config.load(ctx["config"])
        report = rat.roots(rat.policy_from_config(cfg))
    except (config.ConfigError, rat.RatError):
        return [Result("config", "FAIL", "RAT root configuration is invalid")]
    rows = [Result("config", "PASS", "RAT reading: irat=%s trat=%s" %
                   (report["read"]["irat"], report["read"]["trat"]))]
    for root in report["roots"]:
        rows.append(Result("config", "PASS" if root["exists"] else "WARN",
                           "%s: exists=%s files=%d" % (root["key"], root["exists"], root["files_seen"])))
    return rows


def cmd_doctor(a):
    data, src = resolve_data_dir(a.data_dir, a.config)
    if sys.version_info < MIN_PYTHON:
        # Re-run inside the setup environment when there is one; without it only the
        # checks that need no ltcore run, and Python itself is the failure to fix.
        ensure_modern_python_if_venv(a.argv, data)
        results = check_python({"data": data, "data_src": src})
        results += check_binaries({})
        return _report(results, a)
    import_ltcore(a.argv)
    skills_dir = Path(a.skills_dir) if a.skills_dir else SKILL_DIR.parent
    ctx = {"data": data, "data_src": src, "skills_dir": skills_dir, "config": a.config,
           "prefetch_mcp": a.prefetch_mcp, "probe_devtools": a.probe_devtools}
    results = []
    for fn in (check_python, check_packages, check_binaries, check_data, check_config,
               check_fonts, check_palettes, check_browser, check_rat):
        try:
            results.extend(fn(ctx))
        except Exception as exc:
            err("doctor crashed in %s: %s" % (fn.__name__, exc))
            err(tail(traceback.format_exc(), 6))
            return 2
    return _report(results, a)


def _report(results, a):
    require = set(a.require or [])
    if "all" in require:
        require = set(PARTS)
    for r in results:
        if r.status == "WARN" and r.part in require:
            r.status = "FAIL"
            r.text += " (required)"
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        counts[r.status] += 1
    if a.json:
        say(json.dumps({"results": [r.as_dict() for r in results], "counts": counts}, indent=1))
    else:
        for r in results:
            say("%-4s %-9s %s" % (r.status, r.part, r.text))
        say("doctor: %d PASS, %d WARN, %d FAIL" % (counts["PASS"], counts["WARN"], counts["FAIL"]))
    return 1 if counts["FAIL"] else 0


# ------------------------------------------------------------------------------ CLI

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="config file (else LT_SUITE_CONFIG, ./lt-suite.toml, "
                        "the platform file)")
    common.add_argument("--data-dir", default=argparse.SUPPRESS, help="data folder (else LT_SUITE_DATA, the config, the "
                        "platform default)")
    ap = argparse.ArgumentParser(prog="setup.py", description=__doc__, parents=[common],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("bootstrap", parents=[common],
                       help="find Python 3.11+, build an external environment, install requirements")
    p.add_argument("--python", help="use this interpreter (must be 3.11 or newer)")
    p.add_argument("--skills-dir", help="folder that holds the installed skill folders "
                   "(default: the parent of this skill)")
    p.add_argument("--with-optional", action="store_true", help="also install optional packages")
    p.add_argument("--network", choices=("auto", "yes", "no"), default="auto",
                   help="auto probes the package index; yes forces the install; no prints the "
                   "command for a normal terminal")
    p.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("locate", parents=[common], help="show config and data folder and probes")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("write", parents=[common], help="validate answers and write the config")
    p.add_argument("--answers", required=True, help="JSON or TOML file of config values, - for stdin")
    p.add_argument("--force", action="store_true", help="replace an existing file (keeps .bak)")
    p.add_argument("--merge", action="store_true", help="merge the answers over the existing file")
    p.add_argument("--stdout", action="store_true", help="print the TOML instead of writing it")
    p.add_argument("--workspace", action="store_true",
                   help="write ./lt-suite.toml with the workspace data folder, download "
                   "transport and copy mode (for sandboxed agents)")

    p = sub.add_parser("set", parents=[common], help="change one key in the existing config")
    p.add_argument("key", help="dotted key, for example browser.js_tmp_dir")
    p.add_argument("value", help="JSON value (true, 5, [\"a\"]) or a plain string")

    p = sub.add_parser("fetch-font", parents=[common], help="download one catalog font")
    p.add_argument("font", help="font id from 'preview.py fonts'")
    p.add_argument("--yes", action="store_true", help="explicit download approval was received")

    p = sub.add_parser("ping", help="browser check: render the ping script, verify its reply")
    pp = p.add_subparsers(dest="ping_cmd", required=True)
    r = pp.add_parser("render", parents=[common], help="render ping.js for the configured adapter")
    r.add_argument("--form", choices=("expression", "function"), help="default from browser.adapter")
    r.add_argument("--stub", dest="stub", action="store_true", default=None)
    r.add_argument("--no-stub", dest="stub", action="store_false")
    r.add_argument("--out-dir", help="folder for the rendered file (default browser.js_tmp_dir, "
                   "else <OS temp>/lt-suite-js-<uid>)")
    v = pp.add_parser("verify", parents=[common], help="check the reply the browser tool returned")
    g = v.add_mutually_exclusive_group(required=True)
    g.add_argument("--reply", help="the JSON the browser tool returned")
    g.add_argument("--reply-file", help="a file holding that JSON")
    v.add_argument("--record-dir", help="on success, store this folder as browser.js_tmp_dir")

    p = sub.add_parser("lt-chrome", parents=[common], help="configure browser server startup")
    chrome = p.add_subparsers(dest="chrome_cmd", required=True)
    r = chrome.add_parser("plan", parents=[common], help="show the redacted launcher plan")
    r.add_argument("--json", action="store_true")
    chrome.add_parser("install", parents=[common], help="copy a stable launcher after user approval")
    r = chrome.add_parser("snippet", parents=[common], help="print a settings-aware host config")
    r.add_argument("--host", required=True, choices=("codex", "cursor", "vscode", "copilot", "gemini-settings", "antigravity"))

    p = sub.add_parser("doctor", parents=[common], help="check the install")
    p.add_argument("--require", action="append", choices=PARTS + ("all",),
                   help="turn WARN lines of this part into FAIL (repeatable)")
    p.add_argument("--skills-dir", help="folder that holds the installed skill folders")
    p.add_argument("--probe-devtools", action="store_true", help="probe the configured loopback /json/version endpoint")
    p.add_argument("--prefetch-mcp", action="store_true",
                   help="run npx -y %s --version now" % MCP_PACKAGE)
    p.add_argument("--json", action="store_true")
    return ap


def main(argv=None):
    _stream_module.configure_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    a = ap.parse_args(argv)
    for key in ("config", "data_dir"):
        if not hasattr(a, key):
            setattr(a, key, None)
    if not a.cmd:
        ap.print_help()
        return 2
    a.argv = argv
    handlers = {"bootstrap": cmd_bootstrap, "locate": cmd_locate, "write": cmd_write,
                "set": cmd_set, "fetch-font": cmd_fetch_font, "ping": cmd_ping,
                "doctor": cmd_doctor, "lt-chrome": cmd_lt_chrome}
    try:
        return handlers[a.cmd](a)
    except KeyboardInterrupt:
        return 2
    except Exception as exc:
        if a.cmd == "doctor":
            err("doctor crashed: %s" % exc)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
