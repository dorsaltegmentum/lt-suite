#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loopback body server: hand rendered page-script bodies to the Lt page by sha256.

This runs on the host, outside every agent jail, as `python3 -I` from the copy that
tools/install_body_server.py checked against packaging/host-files.sha256. Never start it from
the repository or any other folder an agent can write. It uses the standard library only, so
that copy runs alone.

    python3 -I lt_body_server.py --dir <data>/js-bodies --origin https://<lt host> [--port 8734]

ltjs.py render (with browser.body_server set) writes each cacheable body, and each policy a
loader names, as <dir>/<sha256>.gz.b64: the gzip base64 text a stub carries, named by the
sha256 of the text it decodes to. On a page-cache miss the loader asks GET /b/<sha256>, checks
the sha256 itself, stores the body in the page cache and runs it. Anyone who can write <dir>
can only offer bytes; the loader runs nothing whose sha256 it did not name.

Requests are refused unless all of these hold:
  * Host is 127.0.0.1:<port> (421 otherwise; blocks DNS rebinding).
  * Origin is --origin. A missing or foreign Origin gets 403 and no CORS headers.
  * The method is GET or OPTIONS (405 otherwise), and a GET path is /b/<64 lowercase hex>
    (404 otherwise). There is no other route: no write, no listing, no health page.
OPTIONS answers the CORS and Private Network Access preflight for --origin only.

A body is served only when <sha256>.gz.b64 is a regular file directly inside --dir, opened
without following a link (a FIFO or folder of that name is 404), and the name still resolves
to that same file inside the same folder; at most --max-bytes (413); and its text is strict
base64 of one gzip member that decodes to at most 16 MB whose sha256 is the name (409).
Every reply closes its connection, and each connection may idle 10 s, so a page that stopped
mid-request cannot hold the server.

Exit codes: 0 stopped normally, 2 usage error, 3 the folder is unsafe or the port cannot be bound.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import re
import signal
import stat
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DEFAULT_PORT = 8734
DEFAULT_MAX_BYTES = 1024 * 1024   # the longest gzip base64 file served; bodies are about 65 KB
MAX_RAW = 16 * 1024 * 1024        # the most a body may decode to
SUFFIX = ".gz.b64"
PATH_RE = re.compile(r"/b/([0-9a-f]{64})")


def normalise_origin(value: str) -> str | None:
    """scheme://host[:port] in lower case, or None when it is not an http(s) origin."""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username is not None:
            return None
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname.lower()}{port}"
    except ValueError:
        return None


def body_matches(data: bytes, sha: str) -> bool:
    """True when ``data`` is strict base64 of one gzip member whose content hashes to ``sha``."""
    try:
        packed = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return False
    inflate = zlib.decompressobj(wbits=31)
    try:
        raw = inflate.decompress(packed, MAX_RAW + 1)
    except zlib.error:
        return False
    if len(raw) > MAX_RAW or not inflate.eof or inflate.unused_data:
        return False
    return hashlib.sha256(raw).hexdigest() == sha


class Bodies:
    """The served folder, resolved and opened once at start.

    Lookups open the file through the folder's descriptor where the platform allows it, then
    check that the name still resolves to that same file directly inside that same folder, so a
    link, a renamed folder or a folder swapped for a link serves nothing."""

    def __init__(self, folder: str | os.PathLike):
        self.root = os.path.realpath(os.fspath(folder))
        st = os.lstat(self.root)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError(f"{self.root} is not a folder")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise OSError(f"{self.root} belongs to uid {st.st_uid}, not {os.getuid()}")
        if st.st_mode & 0o022:
            raise OSError(f"{self.root} has mode {stat.S_IMODE(st.st_mode):04o}; other accounts may write it")
        self.ident = (st.st_dev, st.st_ino)
        self.fd = None
        if os.open in os.supports_dir_fd:
            self.fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                              | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))

    def read(self, sha: str, cap: int) -> tuple[int, bytes]:
        """(status, body) for GET /b/<sha>: 200 with the file's bytes, else 404, 409 or 413."""
        name = sha + SUFFIX
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0))
        try:
            if self.fd is not None:
                fd = os.open(name, flags, dir_fd=self.fd)
            else:
                fd = os.open(os.path.join(self.root, name), flags)
        except OSError:
            return 404, b""
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return 404, b""
            path = os.path.realpath(os.path.join(self.root, name))
            try:
                here, folder = os.stat(path), os.stat(self.root)
            except OSError:
                return 404, b""
            if (os.path.dirname(path) != self.root or (here.st_dev, here.st_ino) != (st.st_dev, st.st_ino)
                    or (folder.st_dev, folder.st_ino) != self.ident):
                return 404, b""
            if st.st_size > cap:
                return 413, b""
            parts, left = [], cap + 1
            while left > 0:
                part = os.read(fd, min(left, 65536))
                if not part:
                    break
                parts.append(part)
                left -= len(part)
            data = b"".join(parts)
        finally:
            os.close(fd)
        if len(data) > cap:
            return 413, b""
        if not body_matches(data, sha):
            return 409, b""
        return 200, data


class State:
    def __init__(self, bodies: Bodies, origin: str, port: int, max_bytes: int, quiet: bool = False):
        self.bodies = bodies
        self.origin = origin
        self.port = port
        self.max_bytes = max_bytes
        self.quiet = quiet


MESSAGES = {
    404: "not found",
    405: "refused: GET and OPTIONS only",
    409: "refused: the stored body does not match its sha256",
    413: "refused: the stored body is over the size cap",
    421: "refused: Host must be 127.0.0.1 with this port",
    403: "refused: the request origin is not the Lt origin",
}


class BodyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 10      # seconds a connection may sit idle or trickle a request
    state: State      # set on the subclass made by make_handler()
    server_version = "lt-suite-body-server"
    sys_version = ""

    def _origin(self) -> str | None:
        o = self.headers.get("Origin")
        return self.state.origin if o and normalise_origin(o) == self.state.origin else None

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").strip().lower() == f"127.0.0.1:{self.state.port}"

    def _reply(self, code: int, body: bytes = b"", origin: str | None = None,
               ctype: str = "text/plain; charset=utf-8") -> None:
        if code >= 400 and not body:
            body = MESSAGES.get(code, "refused").encode("utf-8")
        self.send_response(code)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
        if code == 405:
            self.send_header("Allow", "GET, OPTIONS")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)
        if not self.state.quiet:
            m = PATH_RE.fullmatch(self.path or "")
            print(f"{self.command} {'/b/' + m.group(1)[:12] if m else '-'} {code}", flush=True)

    def _gate(self) -> str | None:
        """Host and Origin checks for every route. The allowed origin, or None after refusing."""
        if not self._host_ok():
            self._reply(421)
            return None
        origin = self._origin()
        if origin is None:
            self._reply(403)
        return origin

    def do_OPTIONS(self):  # noqa: N802 (http.server naming)
        origin = self._gate()
        if origin is not None:
            self._reply(204, origin=origin)

    def do_GET(self):  # noqa: N802
        origin = self._gate()
        if origin is None:
            return
        # The request line as sent: http.server rewrites a leading // in self.path.
        sent = self.requestline.split(" ")
        m = PATH_RE.fullmatch(sent[1]) if len(sent) == 3 and sent[1] == self.path else None
        if not m:
            return self._reply(404, origin=origin)
        code, data = self.state.bodies.read(m.group(1), self.state.max_bytes)
        if code != 200:
            return self._reply(code, origin=origin)
        self._reply(200, data, origin, "text/plain; charset=us-ascii")

    def _refuse(self):
        self._reply(405)

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_TRACE = do_CONNECT = _refuse

    def log_message(self, *a):  # quiet: _reply prints one line per request
        pass


def make_handler(state: State) -> type:
    return type("BoundBodyHandler", (BodyHandler,), {"state": state})


def make_server(folder: str | os.PathLike, origin: str, port: int,
                max_bytes: int = DEFAULT_MAX_BYTES, quiet: bool = False) -> ThreadingHTTPServer:
    """A server bound to 127.0.0.1:<port> (0 picks a free port). OSError when the folder is
    unsafe or the port is taken."""
    norm = normalise_origin(origin)
    if norm is None:
        raise ValueError(f"{origin!r} is not an origin like https://<host>")
    srv = ThreadingHTTPServer(("127.0.0.1", port), BodyHandler)
    try:
        bodies = Bodies(folder)
    except BaseException:
        srv.server_close()
        raise
    srv.RequestHandlerClass = make_handler(State(bodies, norm, srv.server_address[1], max_bytes, quiet))
    srv.daemon_threads = True
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="<data>/js-bodies, written by ltjs.py render")
    ap.add_argument("--origin", required=True, help="the Lt origin, for example https://<host>")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"loopback port (default {DEFAULT_PORT})")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="largest file served")
    a = ap.parse_args(argv)
    if not 1024 <= a.port <= 65535:
        print(f"error: port {a.port} is outside 1024 to 65535", file=sys.stderr)
        return 2
    if a.max_bytes <= 0:
        print("error: --max-bytes must be positive", file=sys.stderr)
        return 2
    if normalise_origin(a.origin) is None:
        print(f"error: {a.origin!r} is not an origin like https://<host>", file=sys.stderr)
        return 2
    try:
        srv = make_server(a.dir, a.origin, a.port, a.max_bytes)
    except OSError as e:
        print(f"error: cannot serve {a.dir} on 127.0.0.1:{a.port}: {e}", file=sys.stderr)
        return 3

    def _stop(signum, frame):  # SIGTERM ends the server like Ctrl-C
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    state = srv.RequestHandlerClass.state
    print(f"body server up: http://127.0.0.1:{state.port} serving {state.bodies.root} to {state.origin}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print("body server stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
