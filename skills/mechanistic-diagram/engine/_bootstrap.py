# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Put the engine and the shared lt-suite core on sys.path.

Every engine module imports this first. The shared core (the ltcore package) is the
skill's generated copy in ../scripts/_shared; in a checkout of the lt-suite repository
without that copy, the repository's shared/ folder is used instead. When ltcore is
already imported (the test suite imports it from shared/), nothing changes.

Entry scripts call entry() inside their ``if __name__ == "__main__"`` block, after a
version gate that parses on Python 3.8.
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")

from pathlib import Path  # noqa: E402

ENGINE = Path(__file__).resolve().parent
SKILL = ENGINE.parent


def shared_dir():
    """The folder that holds the ltcore package, or None."""
    for cand in (SKILL / "scripts" / "_shared", SKILL.parent.parent / "shared"):
        if (cand / "ltcore" / "__init__.py").is_file():
            return cand
    return None


SHARED = shared_dir()
if "ltcore" not in sys.modules and SHARED is not None and str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from ltcore.streams import configure_utf8  # noqa: E402



def entry():
    """Run the shared bootstrap: re-run inside <data>/venv when a dependency is missing."""
    if SHARED is None and "ltcore" not in sys.modules:
        sys.exit("lt-suite: the shared core is missing (scripts/_shared/ltcore). "
                 "Reinstall the skill, or run tools/sync_shared.py in the repository.")
    configure_utf8()
    import ltcore
    missing = ltcore.bootstrap()
    if missing:
        sys.exit(ltcore.missing_hint(missing))
