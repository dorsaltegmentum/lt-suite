#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loopback sink for the capture page scripts: write their POSTs into <data>/incoming/.

capture.js and fetch_media.js POST module-<id>, reference-<id> and images directly to this
loopback process. Submitted answers and image bytes therefore stay out of the agent's
context. stamp_capture.py validates what lands here and moves it to
<data>/captures/.

    python3 sink.py [--port 8731] [--allow-origin https://<host>] [--token-file PATH]
    python3 sink.py --print-args [--module-only]    # ltjs.py render arguments, no server

The render arguments name the transport, this sink's URL and its token. They also carry
drop_text_patterns and post_ms when capture.drop_text_patterns or capture.timeouts.post_ms
in the config differ from the defaults built into capture.js.

Requests are refused unless all of these hold:
  * Host is 127.0.0.1:<port> (421 otherwise; blocks DNS rebinding).
  * Origin is lt.origin from the config or one given with --allow-origin. A missing or
    foreign Origin gets 403 with no CORS headers; only an allowed origin is echoed in
    Access-Control-Allow-Origin, and only it gets Access-Control-Allow-Private-Network.
  * GET /hello?n=<32 hex> proves the sink secret with HMAC-SHA256 over
    "lt-suite-sink|<nonce>|<Origin>". The page verifies it before sending content.
  * POSTs carry X-Lt-Suite-Auth: HMAC-SHA256(token, "<name>|<sha256(body)>")
    (401 otherwise). The token is never sent over HTTP. The token is 32 random bytes,
    printed at start and written to --token-file (mode 0600).
  * Bodies are at most --max-bytes (default 64 MB; 413 otherwise).

Routes:
  POST /module-<id>      application/json, a JSON object whose module_id is <id>
                         -> incoming/module-<id>.json; an existing file is kept and the new
                            one is written as module-<id>.<n>.json
  POST /reference-<id>   same checks; WRITE-ONCE: 409 when incoming/ or captures/ already
                         holds reference-<id>.json
  POST /media/lt-<id>-<key>.<ext>   a raster image (png, jpg, gif, webp, bmp) whose leading
                         bytes match the extension -> incoming/media/<name>; an identical
                         existing file is a no-op, a different one is refused (409)
  GET  /hello?n=<nonce>  HMAC proof bound to the nonce and allowed Origin
  GET  /health           "ok"
<id> must match ^[A-Za-z0-9_-]{1,64}$.

Folders the sink writes into are 0700 and its files 0600, whatever the umask. A link may
stand in for the data folder itself; a link in place of incoming/ or incoming/media/ is
refused.

SVG is refused: it is a script-bearing document and these files end up in the user's
notes folder. fetch_media.js rasterises SVG in the page before sending.

Exit codes: 0 stopped normally, 2 usage error, 3 config error or the port cannot be bound.
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402

ltcore.bootstrap([])  # standard library only
from ltcore import config, store  # noqa: E402

try:  # the renderer for the page scripts; importing it also ships it with this skill
    from ltcore import ltjs  # noqa: E402
    LTJS = Path(ltjs.__file__).resolve()
except ImportError:
    LTJS = Path(__file__).resolve().parent / "_shared" / "ltcore" / "ltjs.py"

import argparse  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import shlex  # noqa: E402
import signal  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from urllib.parse import urlsplit, parse_qs  # noqa: E402

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
# capture.js has these defaults built in; the render arguments carry a value only when the
# config sets a different one.
DEFAULT_DROP_TEXT = ["^Copyright ©"]
DEFAULT_POST_MS = 30000
AUTH_HEADER = "X-Lt-Suite-Auth"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DOC_RE = re.compile(r"^(module|reference)-([A-Za-z0-9_-]{1,64})(?:\.json)?$")
MEDIA_RE = re.compile(r"^lt-[A-Za-z0-9_-]{1,160}\.(png|jpg|jpeg|gif|webp|bmp)$", re.I)

# Allowed extensions and the bytes each file must begin with. Only raster formats are listed.
IMAGE_MAGIC = {
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],          # plus "WEBP" at offset 8, checked below
    ".bmp": [b"BM"],
}


def image_kind_ok(name: str, raw: bytes) -> tuple[bool, str]:
    """(ok, reason). The name only claims a type; the first bytes have to confirm it."""
    ext = os.path.splitext(name)[1].lower()
    sigs = IMAGE_MAGIC.get(ext)
    if not sigs:
        return False, f"extension {ext or '(none)'} is not an allowed raster image type"
    if not any(raw.startswith(s) for s in sigs):
        return False, f"leading bytes {raw[:8].hex()} do not match {ext}"
    if ext == ".webp" and raw[8:12] != b"WEBP":
        return False, "RIFF container is not WEBP"
    return True, ""


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


def private_dir(path: Path, follow_link: bool = False) -> None:
    """Make ``path`` a folder with mode 0700, whatever the umask.

    Missing folders, ancestors included, are created and set to 0700. An existing folder this
    account owns is set to 0700; one another account owns keeps its mode and gets a WARN line.
    A link in place of a folder is refused (OSError) unless ``follow_link`` is set, which the
    data root uses: a user may keep the data folder elsewhere and link to it.
    """
    missing = []
    p = path
    while not p.exists() and not p.is_symlink():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    if path.is_symlink() and not follow_link:
        raise OSError(f"unsafe data folder: {path} is a link")
    if not path.is_dir():
        raise OSError(f"unsafe data folder: {path} is not a folder")
    st = path.stat()
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        print(f"WARN: {path} belongs to another account; its mode stays {stat.S_IMODE(st.st_mode):o}",
              file=sys.stderr)
        return
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)


def publish_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` at ``path`` only if nothing is there; FileExistsError otherwise.

    The bytes go to a temporary file first and are linked to the final name in one step, so
    an interrupted write never leaves a partial file under the final name.
    """
    fd, tmp = tempfile.mkstemp(prefix=".sink-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)           # atomic, and refuses an existing name
        except FileExistsError:
            raise
        except OSError:                  # no hard links on this file system
            with open(path, "xb") as fh:
                fh.write(data)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def numbered_free(folder: Path, stem: str, ext: str) -> Path:
    """<stem><ext> when free, else <stem>.<n><ext> with the smallest free n >= 1."""
    p = folder / f"{stem}{ext}"
    n = 0
    while p.exists():
        n += 1
        p = folder / f"{stem}.{n}{ext}"
    return p


class SinkState:
    def __init__(self, incoming: Path, captures: Path, token: str, origins: set[str],
                 port: int, max_bytes: int, once: bool):
        self.incoming = incoming
        self.media = incoming / "media"
        self.captures = captures
        self.token = token
        self.origins = origins
        self.port = port
        self.max_bytes = max_bytes
        self.once = once
        self.received = 0
        self.media_count = 0
        self.lock = threading.Lock()


class Sink(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30      # seconds a connection may sit idle or trickle a request
    state: SinkState  # set on the subclass made by make_handler()
    server_version = "lt-suite-sink"
    sys_version = ""

    # -- helpers ------------------------------------------------------------------
    def _allowed_origin(self) -> str | None:
        o = self.headers.get("Origin")
        if not o:
            return None
        n = normalise_origin(o)
        return n if n and n in self.state.origins else None

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host == f"127.0.0.1:{self.state.port}"

    def _reply(self, code: int, body: bytes | str = b"", ctype: str = "text/plain; charset=utf-8",
               origin: str | None = None, close: bool = False) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {AUTH_HEADER}")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj: dict, origin: str | None, close: bool = False) -> None:
        self._reply(code, json.dumps(obj), "application/json", origin, close)

    def _gate(self) -> str | None:
        """Host and Origin checks shared by every route. Returns the allowed origin, or None
        after sending the refusal."""
        if not self._host_ok():
            self._reply(421, "refused: Host must be 127.0.0.1 with this port", close=True)
            return None
        origin = self._allowed_origin()
        if origin is None:
            self._reply(403, "refused: the request origin is not an allowed Lt origin", close=True)
            return None
        return origin

    # -- methods ------------------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802 (http.server naming)
        origin = self._gate()
        if origin is None:
            return
        self._reply(204, b"", origin=origin)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._host_ok():
            return self._reply(421, "refused: Host must be 127.0.0.1 with this port", close=True)
        if self.headers.get("Origin") and self._allowed_origin() is None:
            return self._reply(403, "refused: the request origin is not an allowed Lt origin", close=True)
        if path == "/hello":
            origin = self._gate()
            if origin is None:
                return
            nonces = parse_qs(urlsplit(self.path).query).get("n", [])
            if len(nonces) != 1 or not re.fullmatch(r"[0-9a-f]{32}", nonces[0]):
                return self._reply(400, "refused: nonce must be 32 lowercase hex characters", origin=origin)
            message = f"lt-suite-sink|{nonces[0]}|{origin}".encode("utf-8")
            proof = hmac.new(self.state.token.encode("utf-8"), message, "sha256").hexdigest()
            return self._json(200, {"proof": proof}, origin)
        if path != "/health":
            return self._reply(404, "not found", origin=self._allowed_origin())
        return self._reply(200, "ok", origin=self._allowed_origin())

    def do_POST(self):  # noqa: N802
        origin = self._gate()
        if origin is None:
            return
        given = self.headers.get(AUTH_HEADER, "")
        if not re.fullmatch(r"[0-9a-f]{64}", given):
            return self._reply(401, "refused: missing or wrong session proof", origin=origin, close=True)
        raw_len = self.headers.get("Content-Length")
        try:
            n = int(raw_len) if raw_len is not None else -1
        except ValueError:
            n = -1
        if n < 0:
            return self._reply(411, "refused: Content-Length required", origin=origin, close=True)
        if n == 0:
            return self._reply(400, "refused: empty body", origin=origin, close=True)
        if n > self.state.max_bytes:
            return self._reply(413, f"refused: body over {self.state.max_bytes} bytes",
                               origin=origin, close=True)
        raw = self.rfile.read(n)
        route = self.path.split("?", 1)[0].lstrip("/")
        expected = hmac.new(self.state.token.encode("utf-8"),
                            f"{route}|{hashlib.sha256(raw).hexdigest()}".encode("utf-8"),
                            "sha256").hexdigest()
        if len(raw) != n or not hmac.compare_digest(given, expected):
            return self._reply(401, "refused: wrong session proof", origin=origin, close=True)
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if route.startswith("media/"):
            return self._post_media(route[len("media/"):], raw, ctype, origin)
        return self._post_doc(route, raw, ctype, origin)

    def _post_doc(self, route: str, raw: bytes, ctype: str, origin: str) -> None:
        m = DOC_RE.match(route)
        if not m:
            return self._reply(400, "refused: the path must be /module-<id> or /reference-<id> "
                                    "with <id> matching ^[A-Za-z0-9_-]{1,64}$", origin=origin)
        kind, mid = m.group(1), m.group(2)
        if ctype != "application/json":
            return self._reply(415, "refused: Content-Type must be application/json", origin=origin)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
            return self._reply(400, f"refused: not JSON ({e.__class__.__name__})", origin=origin)
        if not isinstance(doc, dict) or doc.get("module_id") != mid:
            return self._reply(400, f"refused: the document's module_id is not {mid}", origin=origin)
        try:
            data = (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
        except (UnicodeEncodeError, RecursionError, ValueError) as e:
            return self._reply(400, f"refused: unencodable document ({e.__class__.__name__})", origin=origin)
        st = self.state
        with st.lock:
            try:
                private_dir(st.incoming)
            except OSError:
                return self._reply(500, "refused: incoming is not a plain folder", origin=origin)
            if kind == "reference":
                name = f"reference-{mid}.json"
                if (st.captures / name).exists() or (st.incoming / name).exists():
                    print(f"REFUSED {name}: exists; a reference is written once", flush=True)
                    return self._reply(409, (
                        f"refused: {name} already exists and a reference file is written once. "
                        f"This refusal does not affect the module upload. Capture again with "
                        f"module_only, or move the existing reference away yourself when a new "
                        f"one is really wanted."),
                        origin=origin)
                path = st.incoming / name
            else:
                path = numbered_free(st.incoming, f"module-{mid}", ".json")
            try:
                publish_bytes(path, data)
            except FileExistsError:
                return self._reply(409, f"refused: {path.name} appeared while writing", origin=origin)
            st.received += 1
        print(f"received {len(raw)} bytes -> {path}", flush=True)
        self._json(200, {"ok": True, "file": path.name}, origin)
        if st.once:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _post_media(self, raw_name: str, raw: bytes, ctype: str, origin: str) -> None:
        name = raw_name
        if "/" in name or "\\" in name or not MEDIA_RE.match(name):
            return self._reply(400, "refused: media name must be lt-<id>-<key>.<png|jpg|jpeg|gif|webp|bmp>",
                               origin=origin)
        if not (ctype.startswith("image/") or ctype == "application/octet-stream"):
            return self._reply(415, "refused: media Content-Type must be an image type", origin=origin)
        ok, why = image_kind_ok(name, raw)
        if not ok:
            print(f"REFUSED {name}: {why}", flush=True)
            return self._reply(415, f"refused: {why}", origin=origin)
        st = self.state
        with st.lock:
            try:  # private_dir refuses a link, so writes cannot leave incoming/media
                private_dir(st.incoming)
                private_dir(st.media)
            except OSError:
                return self._reply(500, "refused: incoming/media is not a plain folder inside incoming",
                                   origin=origin)
            path = st.media / name
            if path.exists():
                if path.read_bytes() == raw:
                    return self._json(200, {"ok": True, "file": name, "unchanged": True}, origin)
                print(f"REFUSED {name}: a different file of that name is already in incoming", flush=True)
                return self._reply(409, f"refused: {name} exists in incoming with different bytes; "
                                        f"run stamp_capture.py first", origin=origin)
            try:
                publish_bytes(path, raw)
            except FileExistsError:
                return self._reply(409, f"refused: {name} appeared while writing", origin=origin)
            st.media_count += 1
        print(f"media {len(raw)} bytes -> {path}", flush=True)
        self._json(200, {"ok": True, "file": name}, origin)

    def log_message(self, *a):  # quiet: one line per write is printed above
        pass


def make_handler(state: SinkState) -> type:
    return type("BoundSink", (Sink,), {"state": state})


def config_args(cfg) -> list[str]:
    """Render arguments for capture.* config values that differ from capture.js defaults."""
    out: list[str] = []
    pats = cfg.get("capture.drop_text_patterns", DEFAULT_DROP_TEXT)
    if isinstance(pats, list) and [str(x) for x in pats] != DEFAULT_DROP_TEXT:
        out += ["--arg-json", "drop_text_patterns=" + json.dumps([str(x) for x in pats], ensure_ascii=False)]
    post = cfg.get("capture.timeouts.post_ms", DEFAULT_POST_MS)
    if isinstance(post, int) and not isinstance(post, bool) and post > 0 and post != DEFAULT_POST_MS:
        out += ["--arg", f"post_ms={post}"]
    return out


def render_args(port: int, token: str, module_only: bool = False, extra: list[str] | None = None) -> list[str]:
    out = ["--arg", "transport=sink", "--arg", f"sink=http://127.0.0.1:{port}", "--arg", f"token={token}"]
    if module_only:
        out += ["--arg", "module_only=true"]
    return out + list(extra or [])


def render_command(port: int, token: str, module_only: bool = False, extra: list[str] | None = None) -> str:
    """The ltjs.py command that renders capture.js for this sink (form chosen per adapter)."""
    return (f'python3 "{LTJS}" render capture --form <expression|function> '
            + shlex.join(render_args(port, token, module_only, extra)))


def write_token(path: Path, token: str) -> None:
    """Write the token to a new file readable by the user only. An existing file or link of
    that name is removed first, so the token never lands in a file someone else can read or
    through a link pointing elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config file (default: the normal lookup)")
    ap.add_argument("--data-dir", help="data folder (overrides LT_SUITE_DATA and the config)")
    ap.add_argument("--port", type=int, help="loopback port (default: config browser.sink_port)")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="URL",
                    help="another Lt origin to accept, besides lt.origin (repeatable)")
    ap.add_argument("--token-file", help="where to write the session token "
                                         "(default: <data>/state/sink-token)")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="body size cap")
    ap.add_argument("--once", action="store_true", help="stop after the first JSON document (a probe)")
    ap.add_argument("--print-args", action="store_true",
                    help="print the ltjs.py render arguments for a running sink and exit")
    ap.add_argument("--module-only", action="store_true", help="with --print-args: a module-only re-capture")
    a = ap.parse_args(argv)

    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    st = store.Store(cfg, data_dir=a.data_dir)
    port = a.port or int(cfg.get("browser.sink_port", 8731))
    if not 1 <= port <= 65535:
        print(f"error: port {port} is out of range", file=sys.stderr)
        return 2
    token_file = Path(a.token_file).expanduser() if a.token_file else st.state_dir / "sink-token"

    if a.print_args:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError:
            print(f"error: no token at {token_file}; start the sink first", file=sys.stderr)
            return 3
        print(shlex.join(render_args(port, token, a.module_only, config_args(cfg))))
        return 0

    origins: set[str] = set()
    lt_origin = cfg.get("lt.origin", "")
    for raw in ([lt_origin] if lt_origin else []) + a.allow_origin:
        n = normalise_origin(raw)
        if n is None:
            print(f"error: {raw!r} is not an origin like https://<host>", file=sys.stderr)
            return 2
        origins.add(n)
    if not origins:
        print(f"lt.origin is not set. {cfg.setting_hint()} Or pass --allow-origin https://<host>.",
              file=sys.stderr)
        return 3
    if a.max_bytes <= 0:
        print("error: --max-bytes must be positive", file=sys.stderr)
        return 2

    try:  # answers land here: 0700 folders whatever the umask
        private_dir(st.data_dir, follow_link=True)
        private_dir(st.incoming_dir)
        if token_file.parent == st.state_dir:
            private_dir(st.state_dir)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    token = secrets.token_hex(32)
    state = SinkState(st.incoming_dir, st.captures_dir, token, origins, port, a.max_bytes, a.once)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    except OSError as e:
        print(f"error: cannot listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 3
    srv.daemon_threads = True
    write_token(token_file, token)

    def _stop(signum, frame):  # SIGTERM ends the server like Ctrl-C
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)

    print(f"sink up: listening on http://127.0.0.1:{port}, saving to {st.incoming_dir} [{st.data_source}]", flush=True)
    print(f"allowed origins: {', '.join(sorted(origins))}", flush=True)
    print(f"token file: {token_file}", flush=True)
    print(f"token: {token}", flush=True)
    extra = config_args(cfg)
    print("render args: " + shlex.join(render_args(port, token, extra=extra)), flush=True)
    print("render: " + render_command(port, token, extra=extra), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            if token_file.read_text(encoding="utf-8").strip() == token:
                token_file.unlink()
        except OSError:
            pass
    print(f"sink stopped after {state.received} document(s) and {state.media_count} image(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
