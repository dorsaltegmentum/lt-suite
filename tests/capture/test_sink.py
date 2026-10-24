# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""sink.py against a real running server on a spare loopback port.

The sink is the only process that writes bytes a web page sent, so it is tested end to end:
start it, send requests the way a browser would (Origin header, preflight, token), and
check what reaches the disk. Covers the origin allow-list, the session token, the Host check,
the body cap, the content-type and id checks, write-once references, numbered module copies
and the media branch's magic-byte check.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SINK = Path(os.environ.get("CAPTURE_SINK_PY", REPO / "skills" / "lt-module-capture" / "scripts" / "sink.py"))
SHARED = REPO / "shared"
ORIGIN = "https://lt.example.invalid"
MID = "c1-r101"
MAX = 64 * 1024

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
NOT_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"AVI " + b"\x00" * 8


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def env_for(tmp: Path) -> dict:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(SHARED)
    e["LT_SUITE_DATA"] = str(tmp / "data")
    return e


def write_config(tmp: Path, origin: str = ORIGIN) -> Path:
    cfg = tmp / "lt-suite.toml"
    cfg.write_text(f'schema_version = 1\n[lt]\norigin = "{origin}"\n', encoding="utf-8")
    return cfg


class Sink:
    def __init__(self, tmp: Path, *extra: str, origin: str = ORIGIN):
        self.tmp = tmp
        self.port = free_port()
        self.data = tmp / "data"
        self.incoming = self.data / "incoming"
        self.captures = self.data / "captures"
        self.token_file = tmp / "token"
        self.cfg = write_config(tmp, origin)
        self.proc = subprocess.Popen(
            [sys.executable, str(SINK), "--config", str(self.cfg), "--port", str(self.port),
             "--token-file", str(self.token_file), "--max-bytes", str(MAX), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env_for(tmp), cwd=tmp)
        for _ in range(200):
            if self.proc.poll() is not None:
                raise AssertionError(f"sink exited early: {self.proc.stdout.read()}")
            if self.token_file.exists() and self.token_file.read_text().strip():
                try:
                    if self.request("GET", "/health", origin=None)[0] == 200:
                        break
                except OSError:
                    pass
            time.sleep(0.05)
        else:
            raise AssertionError("sink never came up")
        self.token = self.token_file.read_text().strip()

    def request(self, method, path, body=b"", *, origin=ORIGIN, token=None, ctype="application/json",
                host=None, headers=None):
        if isinstance(body, str):
            body = body.encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": ctype}
        if method == "POST":
            h["Content-Length"] = str(len(body))
        if origin is not None:
            h["Origin"] = origin
        if token is not None:
            name = path.split("?", 1)[0].lstrip("/")
            digest = hashlib.sha256(body).hexdigest()
            h["X-Lt-Suite-Auth"] = hmac.new(token.encode(), f"{name}|{digest}".encode(), "sha256").hexdigest()
        if host is not None:
            h["Host"] = host
        h.update(headers or {})
        c.request(method, path, body=body if method == "POST" else None, headers=h)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, text, hdrs

    def post(self, path, body, **kw):
        kw.setdefault("token", self.token)
        return self.request("POST", path, body, **kw)

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return self.proc.stdout.read()


@pytest.fixture
def sink(tmp_path):
    s = Sink(tmp_path)
    yield s
    s.stop()


def doc(mid=MID, **extra):
    return json.dumps(dict({"module_id": mid, "schema_version": 1}, **extra))


# --- origin, host and token ---------------------------------------------------------------

def test_allowed_origin_and_token_write_a_module(sink):
    status, text, h = sink.post(f"/module-{MID}", doc())
    assert status == 200
    assert json.loads(text) == {"ok": True, "file": f"module-{MID}.json"}
    assert h["access-control-allow-origin"] == ORIGIN
    assert h["access-control-allow-private-network"] == "true"
    assert json.loads((sink.incoming / f"module-{MID}.json").read_text())["module_id"] == MID


def test_foreign_origin_is_403_with_no_cors_headers(sink):
    status, _, h = sink.post(f"/module-{MID}", doc(), origin="https://evil.example.invalid")
    assert status == 403
    assert "access-control-allow-origin" not in h
    assert "access-control-allow-private-network" not in h
    assert not sink.incoming.exists() or not list(sink.incoming.iterdir())


def test_missing_origin_is_403(sink):
    status, _, h = sink.post(f"/module-{MID}", doc(), origin=None)
    assert status == 403
    assert "access-control-allow-origin" not in h


def test_wrong_or_missing_token_is_401(sink):
    assert sink.post(f"/module-{MID}", doc(), token="0" * 64)[0] == 401
    assert sink.request("POST", f"/module-{MID}", doc())[0] == 401
    assert not (sink.incoming / f"module-{MID}.json").exists()


def test_preflight_answers_only_the_allowed_origin(sink):
    status, _, h = sink.request("OPTIONS", f"/module-{MID}", headers={
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, x-lt-suite-auth",
        "Access-Control-Request-Private-Network": "true"})
    assert status == 204
    assert h["access-control-allow-origin"] == ORIGIN
    assert "X-Lt-Suite-Auth" in h["access-control-allow-headers"]
    assert h["access-control-allow-private-network"] == "true"
    status, _, h = sink.request("OPTIONS", f"/module-{MID}", origin="https://evil.example.invalid")
    assert status == 403
    assert "access-control-allow-origin" not in h


def test_a_foreign_host_header_is_refused(sink):
    """A page on a rebinding domain reaches 127.0.0.1 with its own name in Host."""
    status, _, _ = sink.post(f"/module-{MID}", doc(), host=f"rebind.example.invalid:{sink.port}")
    assert status == 421


def test_health_needs_no_origin_but_refuses_a_foreign_one(sink):
    assert sink.request("GET", "/health", origin=None)[0] == 200
    assert sink.request("GET", "/health", origin="https://evil.example.invalid")[0] == 403


def test_page_scripts_are_not_served(sink):
    assert sink.request("GET", "/capture.js")[0] == 404
    assert sink.request("GET", "/sink.py")[0] == 404


# --- body checks ----------------------------------------------------------------------------

def test_oversize_body_is_413_and_nothing_is_written(sink):
    big = json.dumps({"module_id": MID, "pad": "x" * (MAX + 10)})
    assert sink.post(f"/module-{MID}", big)[0] == 413
    assert not (sink.incoming / f"module-{MID}.json").exists()


def test_json_route_needs_application_json(sink):
    assert sink.post(f"/module-{MID}", doc(), ctype="text/plain")[0] == 415
    assert sink.post(f"/module-{MID}", doc(), ctype="application/json; charset=utf-8")[0] == 200


@pytest.mark.parametrize("body", [b"\x00\x01not json", b"[1, 2]", b'{"module_id": "c9-r999"}', b"{}"])
def test_bad_documents_are_400(sink, body):
    assert sink.post(f"/module-{MID}", body)[0] == 400
    assert not (sink.incoming / f"module-{MID}.json").exists()


@pytest.mark.parametrize("path", ["/module-../../evil", "/module-..%2Fevil", "/module-" + "a" * 65,
                                  "/module-a.b", "/answers-c1-r101", "/module-", "/x/module-c1-r101"])
def test_unsafe_ids_and_routes_are_400(sink, path):
    status, _, _ = sink.post(path, doc())
    assert status == 400, path
    assert not list(sink.tmp.glob("evil*"))
    assert not (sink.tmp.parent / "evil").exists()


# --- write rules --------------------------------------------------------------------------------

def test_an_existing_module_is_kept_and_new_copies_are_numbered(sink):
    for v in (1, 2, 3):
        status, text, _ = sink.post(f"/module-{MID}", doc(v=v))
        assert status == 200
    names = sorted(p.name for p in sink.incoming.glob("module-*"))
    assert names == [f"module-{MID}.1.json", f"module-{MID}.2.json", f"module-{MID}.json"]
    assert json.loads((sink.incoming / f"module-{MID}.json").read_text())["v"] == 1
    assert json.loads((sink.incoming / f"module-{MID}.2.json").read_text())["v"] == 3


def test_a_reference_is_written_once(sink):
    assert sink.post(f"/reference-{MID}", doc(v=1))[0] == 200
    first = (sink.incoming / f"reference-{MID}.json").read_bytes()
    status, text, _ = sink.post(f"/reference-{MID}", doc(v=2))
    assert status == 409
    assert "written once" in text and "module_only" in text
    assert (sink.incoming / f"reference-{MID}.json").read_bytes() == first
    assert sorted(p.name for p in sink.incoming.glob("reference-*")) == [f"reference-{MID}.json"]
    assert sink.post(f"/reference-{MID}.json", doc(v=3))[0] == 409, "the .json spelling is the same file"


def test_a_reference_already_in_captures_is_refused(sink):
    sink.captures.mkdir(parents=True)
    (sink.captures / f"reference-{MID}.json").write_text(doc(v=0))
    assert sink.post(f"/reference-{MID}", doc(v=1))[0] == 409
    assert not (sink.incoming / f"reference-{MID}.json").exists()


# --- media ----------------------------------------------------------------------------------------

def test_media_lands_verbatim_in_incoming_media(sink):
    for name, data in (("lt-c1-r101-1.png", PNG), ("lt-c1-r101-2.jpg", JPG),
                       ("lt-c1-r101-3.gif", GIF), ("lt-c1-r101-u4.webp", WEBP)):
        status, text, _ = sink.post(f"/media/{name}", data, ctype="image/png")
        assert status == 200, (name, text)
    media = sink.incoming / "media"
    assert sorted(p.name for p in media.iterdir()) == ["lt-c1-r101-1.png", "lt-c1-r101-2.jpg",
                                                       "lt-c1-r101-3.gif", "lt-c1-r101-u4.webp"]
    assert (media / "lt-c1-r101-1.png").read_bytes() == PNG


@pytest.mark.parametrize("name,data,want", [
    ("lt-c1-r101-x.jpg", PNG, 415),        # the extension is a claim; the magic is evidence
    ("lt-c1-r101-x.webp", NOT_WEBP, 415),
    ("lt-c1-r101-x.svg", b"<svg/>", 400),   # svg is not an allowed name
    ("lt-c1-r101-x.pdf", b"%PDF-1.4", 400),
    ("lt-c1-r101-x", PNG, 400),
    ("evil.png", PNG, 400),                 # must be lt-<id>-<key>
    ("..%2Flt-c1-r101-x.png", PNG, 400),
])
def test_media_refusals(sink, name, data, want):
    assert sink.post(f"/media/{name}", data, ctype="image/png")[0] == want
    assert not (sink.incoming / "media").exists() or not list((sink.incoming / "media").iterdir())


def test_media_traversal_never_escapes(sink):
    sink.post("/media/../../lt-c1-r101-evil.png", PNG, ctype="image/png")
    assert not (sink.data / "lt-c1-r101-evil.png").exists()
    assert not (sink.tmp / "lt-c1-r101-evil.png").exists()


def test_media_needs_an_image_content_type(sink):
    assert sink.post("/media/lt-c1-r101-1.png", PNG, ctype="text/plain")[0] == 415


def test_media_identical_repost_is_a_noop_and_a_different_one_is_refused(sink):
    assert sink.post("/media/lt-c1-r101-1.png", PNG, ctype="image/png")[0] == 200
    status, text, _ = sink.post("/media/lt-c1-r101-1.png", PNG, ctype="image/png")
    assert status == 200 and json.loads(text)["unchanged"] is True
    assert sink.post("/media/lt-c1-r101-1.png", PNG + b"x", ctype="image/png")[0] == 409
    assert (sink.incoming / "media" / "lt-c1-r101-1.png").read_bytes() == PNG


# --- process ----------------------------------------------------------------------------------------

def test_token_file_is_private_and_removed_on_stop(tmp_path):
    s = Sink(tmp_path)
    try:
        if os.name == "posix":
            assert (s.token_file.stat().st_mode & 0o777) == 0o600
        assert len(s.token) == 64
    finally:
        out = s.stop()
    assert not s.token_file.exists()
    assert f"listening on http://127.0.0.1:{s.port}" in out
    assert "render args: --arg transport=sink" in out


def test_print_args_reads_the_running_token(tmp_path):
    s = Sink(tmp_path)
    try:
        r = subprocess.run([sys.executable, str(SINK), "--config", str(s.cfg), "--port", str(s.port),
                            "--token-file", str(s.token_file), "--print-args", "--module-only"],
                           capture_output=True, text=True, env=env_for(tmp_path), cwd=tmp_path)
        assert r.returncode == 0, r.stderr
        assert r.stdout.split() == ["--arg", "transport=sink", "--arg", f"sink=http://127.0.0.1:{s.port}",
                                    "--arg", f"token={s.token}", "--arg", "module_only=true"]
    finally:
        s.stop()


def test_print_args_carries_capture_config_that_differs_from_the_defaults(tmp_path):
    cfg = tmp_path / "custom.toml"
    cfg.write_text(f'schema_version = 1\n[lt]\norigin = "{ORIGIN}"\n'
                   "[capture]\ndrop_text_patterns = ['^Copyright', '^All rights reserved']\n"
                   "[capture.timeouts]\npost_ms = 45000\n", encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("t0ken\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SINK), "--config", str(cfg), "--port", "8731",
                        "--token-file", str(token_file), "--print-args"],
                       capture_output=True, text=True, env=env_for(tmp_path), cwd=tmp_path, timeout=30)
    assert r.returncode == 0, r.stderr
    import shlex
    argv = shlex.split(r.stdout)
    assert argv[:6] == ["--arg", "transport=sink", "--arg", "sink=http://127.0.0.1:8731", "--arg", "token=t0ken"]
    k = argv.index("--arg-json")
    name, _, value = argv[k + 1].partition("=")
    assert name == "drop_text_patterns" and json.loads(value) == ["^Copyright", "^All rights reserved"]
    assert argv[argv.index("post_ms=45000") - 1] == "--arg"


def test_no_origin_configured_exits_3_with_the_setup_hint(tmp_path):
    cfg = tmp_path / "lt-suite.toml"
    cfg.write_text("schema_version = 1\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SINK), "--config", str(cfg), "--port", str(free_port())],
                       capture_output=True, text=True, env=env_for(tmp_path), cwd=tmp_path, timeout=30)
    assert r.returncode == 3
    assert "lt.origin is not set" in r.stderr


def test_allow_origin_flag_adds_an_origin(tmp_path):
    s = Sink(tmp_path, "--allow-origin", "https://second.example.invalid/")
    try:
        assert s.post(f"/module-{MID}", doc(), origin="https://second.example.invalid")[0] == 200
        assert s.post(f"/module-{MID}", doc(), origin=ORIGIN)[0] == 200
    finally:
        s.stop()


def test_a_busy_port_exits_3(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        cfg = write_config(tmp_path)
        r = subprocess.run([sys.executable, str(SINK), "--config", str(cfg), "--port", str(port),
                            "--token-file", str(tmp_path / "t")],
                           capture_output=True, text=True, env=env_for(tmp_path), cwd=tmp_path, timeout=30)
    assert r.returncode == 3
    assert "cannot listen" in r.stderr


def test_unencodable_or_deep_json_is_400_not_a_crash(sink):
    assert sink.post(f"/module-{MID}", '{"module_id": "%s", "x": "\\ud800"}' % MID)[0] == 400
    deep = '{"module_id": "%s", "x": %s}' % (MID, "[" * 100000 + "]" * 100000)
    status = sink.post(f"/module-{MID}", deep)[0]
    assert status in (400, 413)
    assert sink.request("GET", "/health", origin=None)[0] == 200, "the sink is still up"


def test_an_origin_with_a_bad_port_is_403(sink):
    assert sink.post(f"/module-{MID}", doc(), origin="https://lt.example.invalid:bad")[0] == 403


def test_an_existing_token_file_or_link_is_replaced_not_followed(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    link = tmp_path / "token"
    try:
        link.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    s = Sink(tmp_path)
    try:
        assert victim.read_text() == "keep me"
        assert not s.token_file.is_symlink()
    finally:
        s.stop()


def test_hello_proves_the_sink_secret_and_binds_origin_and_nonce(sink):
    nonce = "0123456789abcdef" * 2
    status, text, _ = sink.request("GET", f"/hello?n={nonce}")
    assert status == 200
    expected = hmac.new(sink.token.encode(),
                        f"lt-suite-sink|{nonce}|{ORIGIN}".encode(), "sha256").hexdigest()
    assert json.loads(text) == {"proof": expected}
    assert sink.token not in text
    assert sink.request("GET", "/hello?n=bad")[0] == 400
    assert sink.request("GET", f"/hello?n={nonce}", origin=None)[0] == 403


def test_legacy_raw_token_is_refused(sink):
    status, _, _ = sink.request("POST", f"/module-{MID}", doc(),
                                headers={"X-Lt-Suite-Token": sink.token})
    assert status == 401
    assert not (sink.incoming / f"module-{MID}.json").exists()


def test_authentication_binds_payload_and_destination(sink):
    body = doc()
    digest = hashlib.sha256(body.encode()).hexdigest()
    auth = hmac.new(sink.token.encode(), f"module-{MID}|{digest}".encode(), "sha256").hexdigest()
    assert sink.request("POST", f"/reference-{MID}", body, headers={"X-Lt-Suite-Auth": auth})[0] == 401
    assert sink.request("POST", f"/module-{MID}", body + " ", headers={"X-Lt-Suite-Auth": auth})[0] == 401
    assert sink.request("POST", f"/module-{MID}", body, headers={"X-Lt-Suite-Auth": auth})[0] == 200


@pytest.mark.parametrize('userinfo', ['intruder', '', ':password'])
def test_origin_userinfo_is_rejected_before_normalisation(sink, userinfo):
    assert sink.post(f"/module-{MID}", doc(), origin=f"https://{userinfo}@lt.example.invalid")[0] == 403
    assert not (sink.incoming / f"module-{MID}.json").exists()


def test_media_directory_symlink_cannot_redirect_writes(sink):
    outside = sink.tmp / "outside"
    outside.mkdir()
    (sink.incoming / "media").symlink_to(outside, target_is_directory=True)
    assert sink.post(f"/media/lt-{MID}-1.png", PNG, ctype="image/png")[0] == 500
    assert list(outside.iterdir()) == []


def test_sink_folders_and_files_are_private_under_an_open_umask(tmp_path):
    # Answers must not be listable or readable by other accounts, whatever the umask.
    (tmp_path / "data").mkdir()
    (tmp_path / "data").chmod(0o755)
    old = os.umask(0)
    try:
        s = Sink(tmp_path)
    finally:
        os.umask(old)
    try:
        assert s.post(f"/module-{MID}", doc())[0] == 200
        assert s.post(f"/reference-{MID}", doc())[0] == 200
        assert s.post(f"/media/lt-{MID}-1.png", PNG, ctype="image/png")[0] == 200
    finally:
        s.stop()
    for folder in (s.data, s.incoming, s.incoming / "media"):
        assert folder.stat().st_mode & 0o777 == 0o700, folder
    for f in (s.incoming / f"module-{MID}.json", s.incoming / f"reference-{MID}.json",
              s.incoming / "media" / f"lt-{MID}-1.png"):
        assert f.stat().st_mode & 0o777 == 0o600, f


def test_a_linked_incoming_folder_cannot_redirect_documents(sink):
    outside = sink.tmp / "outside"
    outside.mkdir()
    sink.incoming.rmdir()
    sink.incoming.symlink_to(outside, target_is_directory=True)
    assert sink.post(f"/module-{MID}", doc())[0] == 500
    assert sink.post(f"/media/lt-{MID}-1.png", PNG, ctype="image/png")[0] == 500
    assert list(outside.iterdir()) == []
