# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run a skill script the way `python3 scripts/x.py ...` would, and watch its file opens.

    python3 -E -s -B _probe.py scripts/x.py --help

sys.path[0] becomes the script's folder, sys.argv[0] the script path, __name__ is
"__main__". An audit hook records every open() of a path inside a folder listed in
LT_PROBE_FORBID (os.pathsep-separated): the repo the zip was built from. A script
that reaches back into the repo is not self-contained even when it exits 0, so the
probe then exits 97 and prints each path once on stderr.
"""
import os
import runpy
import sys
import traceback

FORBID = [os.path.realpath(p) for p in os.environ.get("LT_PROBE_FORBID", "").split(os.pathsep) if p]
HITS = []


def _hook(event, args):
    if event != "open" or not args or not FORBID:
        return
    target = args[0]
    if not isinstance(target, (str, bytes, os.PathLike)):
        return
    try:
        p = os.fsdecode(target)
        rp = os.path.realpath(p if os.path.isabs(p) else os.path.join(os.getcwd(), p))
    except (OSError, ValueError, TypeError):
        return
    for f in FORBID:
        if (rp == f or rp.startswith(f + os.sep)) and rp not in HITS:
            HITS.append(rp)


def main():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("lt_probe_streams",
        Path(__file__).resolve().parents[2] / "shared/ltcore/streams.py")
    streams = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(streams)
    streams.configure_utf8()

    if len(sys.argv) < 2:
        sys.exit("usage: _probe.py SCRIPT [ARGS...]")
    script = os.path.abspath(sys.argv[1])
    sys.argv = [sys.argv[1]] + sys.argv[2:]
    sys.path[0] = os.path.dirname(script)
    sys.addaudithook(_hook)
    code = 0
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if not isinstance(e.code, int) and e.code is not None:
            print(e.code, file=sys.stderr)
    except Exception:
        traceback.print_exc()
        code = 1
    if HITS:
        sys.stdout.flush()
        print("LT_PROBE_FORBIDDEN_OPEN " + " ".join(HITS), file=sys.stderr)
        sys.stderr.flush()
        os._exit(97)
    sys.exit(code)


if __name__ == "__main__":
    main()
