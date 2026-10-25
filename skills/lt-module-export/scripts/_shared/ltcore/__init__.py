# Generated from shared/ltcore/__init__.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""lt-suite shared core: config, data store, paths and the dependency bootstrap.

Skill scripts import this package from ``scripts/_shared/`` (a generated copy of
the repo's ``shared/`` folder). Entry scripts call ``ltcore.bootstrap()`` once,
right after their own Python version gate. This file parses on Python 3.8 so an
old interpreter reaches the version message instead of a SyntaxError.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

from .streams import configure_utf8


from .requirements import requirement_applies

__version__ = "0.2.1"

NEEDS = []  # type: list

MIN_PYTHON = (3, 11)
VERSION_MESSAGE = (
    "lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
    "python3 <setup skill>/scripts/setup.py bootstrap"
)

# Distribution name (as written in requirements.txt) -> import name.
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

_GUARD_ENV = "LT_SUITE_BOOTSTRAPPED"


def check_python(version_info=None):
    """Exit with the setup hint when the interpreter is older than 3.11."""
    v = sys.version_info if version_info is None else version_info
    if tuple(v[:2]) < MIN_PYTHON:
        raise SystemExit(VERSION_MESSAGE)


def _skill_requirements():
    """Requirement names from the skill's generated requirements.txt, if any.

    Layout: <skill>/requirements.txt and <skill>/scripts/_shared/ltcore/__init__.py.
    In the source tree (shared/ltcore) no such file exists and the list is empty.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    shared = os.path.dirname(here)
    if os.path.basename(shared) != "_shared":
        return []
    skill = os.path.dirname(os.path.dirname(shared))
    path = os.path.join(skill, "requirements.txt")
    names = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if not requirement_applies(line):
                    continue
                for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";", " "):
                    line = line.split(sep, 1)[0]
                names.append(line.strip().lower())
    except OSError:
        return []
    return names


def missing_modules(modules=None):
    """Import names that cannot be found. ``None`` reads the skill's requirements.txt."""
    if modules is None:
        modules = [DIST_TO_IMPORT.get(n, n.replace("-", "_")) for n in _skill_requirements()]
    return [m for m in modules if importlib.util.find_spec(m) is None]


def venv_python(data_dir):
    """Path of the interpreter in the repository-external bootstrap environment."""
    from ltcore import paths
    environment = str(paths.environment_dir(data_dir))
    if os.name == "nt":
        return os.path.join(environment, "Scripts", "python.exe")
    return os.path.join(environment, "bin", "python")


def _data_dir(argv=None):
    try:
        from ltcore import config, store
        args = list(sys.argv if argv is None else argv)[1:]
        flag = None
        for i, arg in enumerate(args):
            if arg == "--":
                break
            if arg == "--config" and i + 1 < len(args):
                flag = args[i + 1]
            elif arg.startswith("--config="):
                flag = arg.split("=", 1)[1]
        return str(store.resolve_data_dir(location=config.locate(flag))[0])
    except Exception:  # a broken config must not stop --help
        from ltcore import paths
        return str(paths.data_dir())


def bootstrap(modules=None, argv=None):
    """Re-run the current script in the external environment when a dependency is missing.

    Order: version gate; find missing imports (``modules`` or the skill's
    requirements.txt); when some are missing and the resolved environment exists and is not
    the running interpreter, run the same command there and exit with its code.
    Returns the list of still-missing import names (empty when all are present),
    so the caller decides how to fail. Never installs anything.
    """
    check_python()
    missing = missing_modules(modules)
    if not missing or os.environ.get(_GUARD_ENV) == "1":
        return missing
    py = venv_python(_data_dir(argv))
    if not os.path.isfile(py):
        return missing
    if os.path.realpath(sys.prefix) == os.path.realpath(os.path.dirname(os.path.dirname(py))):
        return missing
    args = [py] + list(sys.argv if argv is None else argv)
    env = dict(os.environ)
    env[_GUARD_ENV] = "1"
    if os.name == "nt":
        raise SystemExit(subprocess.call(args, env=env))
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(py, args, env)
    return missing  # not reached


def missing_hint(missing):
    """One line telling the user how to install what ``bootstrap()`` found missing."""
    return ("lt-suite: missing Python packages: " + ", ".join(missing) +
            ". Run the setup skill's bootstrap step: "
            "python3 <setup skill>/scripts/setup.py bootstrap")


check_python()
