#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install the body server into a host-only folder and write its systemd user unit.

The body server runs on the host, outside every agent jail, with `python3 -I`. An agent that
works in this repository can edit any file in it, so the host never runs the server from here.
This command checks tools/host/lt_body_server.py against packaging/host-files.sha256 (and
against --expect-sha256 when given), copies it into --dest as a read-only file, checks the copy
again and writes lt-body-server.service beside it. It neither enables nor starts the unit.

    python3 tools/install_body_server.py --dest <host-only folder> [--config F] [--data-dir D]
        [--origin URL] [--port N] [--python /usr/bin/python3] [--expect-sha256 HEX]

The unit serves <data>/js-bodies (created 0700 when missing) to lt.origin on the port of
browser.body_server; flags override the config. --dest must be a folder no agent can write.
The command refuses one inside this repository or the data folder, a link, or a folder that
other accounts can open. An agent able to write this repository can change the server and
its manifest together, so compare the printed sha256 with a signed release
(`git tag -v`, the release page) or pass it as --expect-sha256.

Then, on the host and outside every jail:
    systemctl --user link <dest>/lt-body-server.service
    systemctl --user start lt-body-server.service

Exit codes: 0 installed, 1 refused (hash mismatch or unsafe folder), 2 usage, 3 config error.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared"))
from ltcore import config as config_mod  # noqa: E402
from ltcore import ltjs, store as store_mod  # noqa: E402
from ltcore import __version__  # noqa: E402

SERVER_REL = "tools/host/lt_body_server.py"
SERVER = REPO / SERVER_REL
MANIFEST = REPO / "packaging" / "host-files.sha256"
COPY_NAME = "lt_body_server.py"
UNIT_NAME = "lt-body-server.service"


class Refused(Exception):
    """The install must not happen; ``code`` is the exit code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def manifest_sha(manifest: Path, rel: str = SERVER_REL) -> str:
    """The sha256 that ``manifest`` (sha256sum format) lists for ``rel``."""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Refused(1, f"cannot read {manifest}: {exc.strerror or exc}") from None
    found = [m.group(1) for m in (re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", ln) for ln in lines)
             if m and m.group(2) == rel]
    if len(found) != 1:
        raise Refused(1, f"{manifest} lists {rel} {len(found)} times; expected once")
    return found[0]


def unit_arg(value: str) -> str:
    """One ExecStart word, double-quoted with systemd's escapes for \\, ", % and $."""
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise Refused(2, f"{value!r} contains a control character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def unit_text(python: str, copy: Path, bodies: Path, origin: str, port: int) -> str:
    argv = [python, "-I", str(copy), "--dir", str(bodies), "--origin", origin, "--port", str(port)]
    return "\n".join([
        f"# Written by tools/install_body_server.py (lt-suite {__version__}). Runs on the host,",
        "# outside every agent jail. Not enabled: link it with systemctl --user link.",
        "[Unit]",
        "Description=lt-suite body server: page-script bodies for the Lt page on 127.0.0.1",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=" + " ".join(unit_arg(a) for a in argv),
        "Restart=on-failure",
        "RestartSec=2",
        "NoNewPrivileges=yes",
        "UMask=0077",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def inside(child: Path, parent: Path) -> bool:
    c, p = os.path.realpath(child), os.path.realpath(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def write_new(path: Path, data: bytes, mode: int) -> None:
    """Replace ``path`` in one step with a new file of ``mode``; never write through a link."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def install(dest: str | os.PathLike, *, origin: str, port: int, data_dir: str | os.PathLike,
            python: str, expect: str | None = None, server: Path = SERVER,
            manifest: Path = MANIFEST) -> dict:
    """Check, copy and describe. Returns {"server", "unit", "sha256", "bodies"}; raises Refused."""
    want = manifest_sha(manifest)
    data = server.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise Refused(1, f"{server} has sha256 {got}; {manifest.name} lists {want}. Refusing to install it.")
    if expect is not None and expect.lower() != got:
        raise Refused(1, f"{server} has sha256 {got}, not the expected {expect}. Refusing to install it.")
    if not os.path.isabs(python):
        raise Refused(2, f"--python must be an absolute path, got {python!r}")
    target = Path(os.path.abspath(os.path.expanduser(os.fspath(dest))))
    data_root = Path(os.path.abspath(os.path.expanduser(os.fspath(data_dir))))
    for parent, what in ((REPO, "this repository"), (server.parent, "the source folder"), (data_root, "the data folder")):
        if inside(target, parent):
            raise Refused(1, f"{target} is inside {what}, which an agent may write; choose a host-only folder")
    try:
        ltjs.private_dir(target)
        bodies = ltjs.private_dir(data_root / ltjs.BODY_DIR)
    except ltjs.RenderRefused as exc:
        raise Refused(1, str(exc)) from None
    copy = target / COPY_NAME
    write_new(copy, data, 0o400)
    if hashlib.sha256(copy.read_bytes()).hexdigest() != got:
        raise Refused(1, f"{copy} changed while it was written; remove it and run again")
    unit = target / UNIT_NAME
    write_new(unit, unit_text(python, copy, bodies, origin, port).encode("utf-8"), 0o600)
    return {"server": str(copy), "unit": str(unit), "sha256": got, "bodies": str(bodies)}


def default_python() -> str:
    for p in ("/usr/bin/python3", shutil.which("python3"), sys.executable):
        if p and os.path.isabs(p) and os.access(p, os.X_OK):
            return p
    return sys.executable


def main(argv: list[str] | None = None) -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", required=True, help="host-only folder for the server copy and its unit")
    ap.add_argument("--config", help="config file (default: the normal lookup)")
    ap.add_argument("--data-dir", help="data folder (default: LT_SUITE_DATA, then the config)")
    ap.add_argument("--origin", help="Lt origin (default: lt.origin)")
    ap.add_argument("--port", type=int, help="loopback port (default: from browser.body_server)")
    ap.add_argument("--python", default=None, help="absolute path of the host's python3")
    ap.add_argument("--expect-sha256", help="the server's sha256 from a signed release")
    a = ap.parse_args(argv)
    try:
        cfg = config_mod.load(a.config)
    except config_mod.ConfigError as exc:
        print(f"install_body_server: config: {exc}", file=sys.stderr)
        return 3
    try:
        origin = ltjs.normalize_origin(a.origin or cfg.get("lt.origin", "") or "")
    except ltjs.PlanError:
        print(f"install_body_server: set lt.origin or pass --origin https://<host>. {cfg.setting_hint()}",
              file=sys.stderr)
        return 3
    port = a.port
    if port is None:
        try:
            url = ltjs.body_server(cfg)
        except ltjs.RenderRefused as exc:
            print(f"install_body_server: {exc}", file=sys.stderr)
            return 3
        if not url:
            print("install_body_server: browser.body_server is off; set it to http://127.0.0.1:<port> "
                  "or pass --port", file=sys.stderr)
            return 3
        port = int(url.rsplit(":", 1)[1])
    if not 1024 <= port <= 65535:
        print(f"install_body_server: port {port} is outside 1024 to 65535", file=sys.stderr)
        return 2
    if a.expect_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", a.expect_sha256):
        print("install_body_server: --expect-sha256 takes 64 hex characters", file=sys.stderr)
        return 2
    data_dir = store_mod.Store(cfg, data_dir=a.data_dir).data_dir
    try:
        info = install(a.dest, origin=origin, port=port, data_dir=data_dir,
                       python=a.python or default_python(), expect=a.expect_sha256)
    except Refused as exc:
        print(f"install_body_server: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"install_body_server: {exc}", file=sys.stderr)
        return 1
    print(f"server   {info['server']} (sha256 {info['sha256']}, matches {MANIFEST.name}"
          + (" and --expect-sha256)" if a.expect_sha256 else "; compare it with a signed release)"))
    print(f"unit     {info['unit']} (not enabled, not started)")
    print(f"serves   {info['bodies']} to {origin} on http://127.0.0.1:{port}")
    print("next, on the host outside every jail:")
    print(f"    systemctl --user link {info['unit']}")
    print(f"    systemctl --user start {UNIT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
