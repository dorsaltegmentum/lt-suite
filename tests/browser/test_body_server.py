# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The loopback body server, the bodies ltjs.py render publishes for it, and its installer.

- tools/host/lt_body_server.py runs in-process on an ephemeral 127.0.0.1 port. It answers only
  GET /b/<sha256> and the preflight, only for the Lt origin and only with Host 127.0.0.1:<port>.
  It refuses traversal, links, FIFOs, a swapped folder, oversized and mismatched files.
- With browser.body_server set, render writes the body and the policy as <sha256>.gz.b64 under
  <data>/js-bodies; the loader then names the policy by sha256. Unset, render writes nothing.
- End to end, a node page fetches from the real server, verifies, stores and runs the body; with
  the server down the loader falls back and the fallback stub fills the page cache.
- tools/install_body_server.py copies the server only when its sha256 matches
  packaging/host-files.sha256, refuses folders an agent may write and writes a unit it never starts.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ltcore import config, ltjs

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ORIGIN = "https://lt.example.invalid"
PLAN = HERE / "fixtures" / "plan-demo.json"
needs_node = pytest.mark.requires_bin("node")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server_mod = load_module("lt_body_server", REPO / "tools" / "host" / "lt_body_server.py")
installer = load_module("install_body_server", REPO / "tools" / "install_body_server.py")


def packed(text: str) -> tuple[str, str]:
    """(gzip base64, sha256 of the text), as ltjs.py publishes a body."""
    return ltjs._payload(text)


@pytest.fixture
def bodies(tmp_path):
    d = tmp_path / "data" / "js-bodies"
    d.parent.mkdir()
    d.mkdir(mode=0o700)
    return d


@pytest.fixture
def serve(bodies):
    servers = []

    def start(folder=bodies, **kw):
        srv = server_mod.make_server(folder, ORIGIN, 0, quiet=True, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]
    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def request(port, method, path, *, host=None, origin=ORIGIN, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host if host is not None else f"127.0.0.1:{port}"}
    if origin is not None:
        h["Origin"] = origin
    h.update(headers or {})
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders()
    r = c.getresponse()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    c.close()
    return out


def put_body(bodies: Path, text: str = "async () => 'served body'") -> tuple[str, str]:
    b64, sha = packed(text)
    ltjs.publish(bodies, b64, sha)
    return b64, sha


# --------------------------------------------------------------------------- server

def test_serves_a_published_body_to_the_lt_origin_only(serve, bodies):
    b64, sha = put_body(bodies)
    port = serve()
    status, h, data = request(port, "GET", f"/b/{sha}")
    assert (status, data) == (200, b64.encode())
    assert h["access-control-allow-origin"] == ORIGIN and h["vary"] == "Origin"
    assert h["connection"] == "close" and h["cache-control"] == "no-store"
    assert h["x-content-type-options"] == "nosniff"
    for origin in ("https://evil.example", "http://lt.example.invalid", "null", None):
        status, h, data = request(port, "GET", f"/b/{sha}", origin=origin)
        assert status == 403 and "access-control-allow-origin" not in h and b64.encode() not in data


def test_preflight_answers_cors_and_private_network_for_the_lt_origin_only(serve):
    port = serve()
    ask = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Private-Network": "true"}
    status, h, data = request(port, "OPTIONS", "/b/" + "0" * 64, headers=ask)
    assert status == 204 and data == b"" and h["content-length"] == "0"
    assert h["access-control-allow-origin"] == ORIGIN
    assert h["access-control-allow-private-network"] == "true"
    assert h["access-control-allow-methods"] == "GET"
    for origin in ("https://evil.example", None):
        status, h, _ = request(port, "OPTIONS", "/b/" + "0" * 64, origin=origin, headers=ask)
        assert status == 403
        assert not any(k.startswith("access-control-") for k in h)


@pytest.mark.parametrize("host", ["localhost:{port}", "127.0.0.1", "127.0.0.1:1", "evil.example",
                                  "evil.example:{port}", "[::1]:{port}", ""])
def test_host_header_must_name_loopback_and_this_port(serve, bodies, host):
    _, sha = put_body(bodies)
    port = serve()
    for method in ("GET", "OPTIONS"):
        status, h, data = request(port, method, f"/b/{sha}", host=host.format(port=port))
        assert status == 421 and "access-control-allow-origin" not in h


@pytest.mark.parametrize("path", [
    "/b/../js-bodies/{sha}", "/b/%2e%2e/{sha}", "/b/{sha}/", "/b/{sha}?x=1", "/b/{sha}.gz.b64",
    "/b/{upper}", "//b/{sha}", "/b/{sha}#x", "/", "/b/", "/health", "/{sha}.gz.b64",
    "/b/{short}", "/b/..%2f..%2fetc%2fpasswd",
])
def test_only_exact_sha256_paths_are_served(serve, bodies, path):
    _, sha = put_body(bodies)
    port = serve()
    status, h, _ = request(port, "GET", path.format(sha=sha, upper=sha.upper(), short=sha[:63]))
    assert status == 404
    assert h["access-control-allow-origin"] == ORIGIN


def test_unknown_hash_is_404_and_folder_is_never_listed(serve, bodies):
    put_body(bodies)
    port = serve()
    assert request(port, "GET", "/b/" + "a" * 64)[0] == 404
    status, _, data = request(port, "GET", "/b/")
    assert status == 404 and b".gz.b64" not in data


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD"])
def test_no_write_or_other_methods(serve, bodies, method):
    _, sha = put_body(bodies)
    port = serve()
    status, h, _ = request(port, method, f"/b/{sha}", headers={"Content-Length": "0"})
    assert status == 405 and h["allow"] == "GET, OPTIONS"
    assert sorted(p.name for p in bodies.iterdir()) == [f"{sha}.gz.b64"]


def test_size_cap_and_hash_mismatch_are_refused(serve, bodies):
    b64, sha = put_body(bodies)
    port = serve(max_bytes=len(b64) - 1)
    assert request(port, "GET", f"/b/{sha}")[0] == 413
    other, _ = packed("async () => 'another body'")
    (bodies / f"{sha}.gz.b64").write_text(other, encoding="utf-8", newline="\n")
    port = serve()
    assert request(port, "GET", f"/b/{sha}")[0] == 409
    (bodies / f"{sha}.gz.b64").write_text(b64 + "\n", encoding="utf-8", newline="\n")
    assert request(port, "GET", f"/b/{sha}")[0] == 409


def test_body_check_needs_strict_base64_of_one_gzip_member(monkeypatch):
    text = b"async () => 1"
    sha = hashlib.sha256(text).hexdigest()
    one = base64.b64encode(gzip.compress(text, mtime=0))
    assert server_mod.body_matches(one, sha)
    assert not server_mod.body_matches(base64.b64encode(gzip.compress(text) + gzip.compress(b"")), sha)
    assert not server_mod.body_matches(base64.b64encode(gzip.compress(text) + b"trailing"), sha)
    assert not server_mod.body_matches(one[:-4], sha)
    assert not server_mod.body_matches(b"!" + one, sha)
    monkeypatch.setattr(server_mod, "MAX_RAW", len(text) - 1)
    assert not server_mod.body_matches(one, sha)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX links and FIFOs")
def test_links_fifos_folders_and_a_swapped_folder_serve_nothing(serve, bodies, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    b64, sha = put_body(outside, "async () => 'outside the folder'")
    os.symlink(outside / f"{sha}.gz.b64", bodies / f"{sha}.gz.b64")
    _, fifo = packed("fifo")
    os.mkfifo(bodies / f"{fifo}.gz.b64")
    _, folder = packed("folder")
    (bodies / f"{folder}.gz.b64").mkdir()
    port = serve()
    for name in (sha, fifo, folder):
        assert request(port, "GET", f"/b/{name}")[0] == 404
    _, inside = put_body(bodies, "async () => 'inside'")
    assert request(port, "GET", f"/b/{inside}")[0] == 200
    os.rename(bodies, tmp_path / "moved")
    os.symlink(outside, bodies)
    put_body(outside, "async () => 'inside'")
    assert request(port, "GET", f"/b/{inside}")[0] == 404


def test_server_refuses_an_unsafe_folder(bodies, tmp_path):
    os.chmod(bodies, 0o770)
    with pytest.raises(OSError, match="other accounts"):
        server_mod.make_server(bodies, ORIGIN, 0)
    plain = tmp_path / "file"
    plain.write_text("x", encoding="utf-8", newline="\n")
    with pytest.raises(OSError, match="not a folder"):
        server_mod.make_server(plain, ORIGIN, 0)


def test_server_cli_refuses_bad_arguments(bodies, capsys):
    assert server_mod.main(["--dir", str(bodies), "--origin", ORIGIN, "--port", "80"]) == 2
    assert server_mod.main(["--dir", str(bodies), "--origin", "lt.example.invalid"]) == 2
    assert server_mod.main(["--dir", str(bodies / "missing"), "--origin", ORIGIN, "--port", "8734"]) == 3


def test_server_uses_the_standard_library_only():
    import ast
    tree = ast.parse((REPO / "tools" / "host" / "lt_body_server.py").read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= set(sys.stdlib_module_names)


# --------------------------------------------------------------------------- render

def write_cfg(tmp_path, extra=""):
    p = tmp_path / "lt-suite.toml"
    p.write_text(f'[lt]\norigin = "{ORIGIN}"\n[browser]\n{extra}', encoding="utf-8", newline="\n")
    return config.load(p)


def test_body_server_config_accepts_only_loopback_with_a_high_port(tmp_path):
    assert ltjs.body_server(write_cfg(tmp_path, 'body_server = "http://127.0.0.1:8734/"\n')) == "http://127.0.0.1:8734"
    assert ltjs.body_server(write_cfg(tmp_path)) == ""
    for bad in ("http://127.0.0.1:80", "http://localhost:8734", "https://127.0.0.1:8734",
                "http://[::1]:8734", "http://127.0.0.1:8734/b", "http://127.0.0.1:99999"):
        with pytest.raises(config.ConfigError, match="body_server"):
            write_cfg(tmp_path, f'body_server = "{bad}"\n')


def test_render_without_body_server_writes_no_bodies(tmp_path):
    cfg = write_cfg(tmp_path)
    info = ltjs.render("guard", cache=True, plan=str(PLAN), cfg=cfg, out_dir=tmp_path / "o",
                       data_dir=tmp_path / "data")
    assert info["body_server"] is None and info["published"] == []
    assert not (tmp_path / "data").exists()
    assert "url_map" in Path(info["path"]).read_text(encoding="utf-8")


def test_render_publishes_body_and_policy_and_the_loader_names_the_policy(tmp_path):
    cfg = write_cfg(tmp_path, 'body_server = "http://127.0.0.1:8734"\n')
    data = tmp_path / "data"
    loader = ltjs.render("guard", cache=True, plan=str(PLAN), cfg=cfg, out_dir=tmp_path / "o", data_dir=data)
    stub = ltjs.render("guard", stub=True, plan=str(PLAN), cfg=cfg, out_dir=tmp_path / "o", data_dir=data)
    folder = data / "js-bodies"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 2 and f"{loader['body_sha256']}.gz.b64" in names
    assert loader["published"] == stub["published"] and loader["body_server"] == "http://127.0.0.1:8734"
    for p in folder.iterdir():
        raw = gzip.decompress(base64.b64decode(p.read_bytes()))
        assert hashlib.sha256(raw).hexdigest() + ".gz.b64" == p.name
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    policy = next(p for p in folder.iterdir() if p.name != f"{loader['body_sha256']}.gz.b64")
    pol = json.loads(gzip.decompress(base64.b64decode(policy.read_bytes())))
    assert pol["mode"] == "plan" and pol["origin"] == ORIGIN
    text = Path(loader["path"]).read_text(encoding="utf-8")
    assert "url_map" not in text and policy.name[:64] in text and 'SERVER = "http://127.0.0.1:8734"' in text
    assert "url_map" in Path(stub["path"]).read_text(encoding="utf-8")
    assert policy.name[:64] in Path(stub["path"]).read_text(encoding="utf-8")


def test_generated_scripts_are_never_published(tmp_path):
    cfg = write_cfg(tmp_path, 'body_server = "http://127.0.0.1:8734"\n')
    source = tmp_path / "_fill-demo-p1.js"
    source.write_text('return {ok:true, answer:"private synthetic answer"};', encoding="utf-8", newline="\n")
    info = ltjs.render(str(source), cache=True, cfg=cfg, out_dir=tmp_path / "o", data_dir=tmp_path / "data")
    assert info["published"] == [] and not (tmp_path / "data").exists()
    assert "SERVER" not in Path(info["path"]).read_text(encoding="utf-8")


def test_unwritable_bodies_folder_keeps_the_inline_loader(tmp_path):
    cfg = write_cfg(tmp_path, 'body_server = "http://127.0.0.1:8734"\n')
    data = tmp_path / "data"
    (data / "js-bodies").mkdir(parents=True, mode=0o700)
    os.chmod(data / "js-bodies", 0o755)
    info = ltjs.render("guard", cache=True, plan=str(PLAN), cfg=cfg, out_dir=tmp_path / "o", data_dir=data)
    assert info["body_server"] is None and "unsafe" in info["body_server_error"]
    text = Path(info["path"]).read_text(encoding="utf-8")
    assert "SERVER" not in text and "url_map" in text


def test_a_folder_in_place_of_a_body_keeps_the_inline_loader(tmp_path):
    cfg = write_cfg(tmp_path, 'body_server = "http://127.0.0.1:8734"\n')
    data = tmp_path / "data"
    first = ltjs.render("guard", cache=True, cfg=cfg, out_dir=tmp_path / "o", data_dir=data)
    body = data / "js-bodies" / f"{first['body_sha256']}.gz.b64"
    body.unlink()
    body.mkdir()
    info = ltjs.render("guard", cache=True, cfg=cfg, out_dir=tmp_path / "o", data_dir=data)
    assert info["body_server"] is None and info["body_server_error"]
    assert "SERVER" not in Path(info["path"]).read_text(encoding="utf-8")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="POSIX links")
def test_publish_keeps_identical_bytes_and_replaces_anything_else(tmp_path):
    folder = tmp_path / "js-bodies"
    b64, sha = packed("async () => 'publish'")
    path = ltjs.publish(folder, b64, sha)
    before = path.stat().st_ino
    assert ltjs.publish(folder, b64, sha).stat().st_ino == before
    path.write_text("tampered", encoding="utf-8", newline="\n")
    ltjs.publish(folder, b64, sha)
    assert path.read_text(encoding="utf-8") == b64
    target = tmp_path / "elsewhere"
    target.write_text("keep", encoding="utf-8", newline="\n")
    path.unlink()
    os.symlink(target, path)
    ltjs.publish(folder, b64, sha)
    assert not path.is_symlink() and path.read_text(encoding="utf-8") == b64
    assert target.read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in folder.iterdir()) == [f"{sha}.gz.b64"]
    with pytest.raises(ltjs.RenderRefused):
        ltjs.publish(folder, b64, "../" + sha[3:])


def test_cache_status_expects_the_hashes_its_loaders_ask_for(tmp_path):
    cfg = write_cfg(tmp_path)
    status = ltjs.render("cache_status", stub=True, args={"names": "guard,nonexistent"}, cfg=cfg,
                         out_dir=tmp_path / "o")
    guard = ltjs.render("guard", cache=True, cfg=cfg, out_dir=tmp_path / "o")
    literal = Path(status["path"]).read_text(encoding="utf-8").split(")()(JSON.parse(", 1)[1]
    args = json.loads(json.JSONDecoder().raw_decode(literal)[0])
    assert status["cacheable"] is True
    assert args["expect"] == {"guard": guard["body_sha256"]}
    assert args["budget"] == ltjs.CACHE_BUDGET and args["version"]


# --------------------------------------------------------------------------- end to end

def node_sequence(tmp_path, files, fetch_origin=ORIGIN):
    spec = tmp_path / f"seq-{len(list(tmp_path.glob('seq-*')))}.json"
    spec.write_text(json.dumps({"mode": "sequence", "files": [str(f) for f in files],
                                "fetch_origin": fetch_origin}), encoding="utf-8", newline="\n")
    p = subprocess.run(["node", str(HERE / "run_rendered.js"), str(spec)], capture_output=True,
                       text=True, timeout=60, cwd=REPO, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@needs_node
def test_page_fetches_verifies_stores_and_runs_a_served_body(serve, bodies, tmp_path):
    port = serve()
    cfg = write_cfg(tmp_path, f'body_server = "http://127.0.0.1:{port}"\n')
    opts = dict(plan=str(PLAN), cfg=cfg, out_dir=tmp_path / "o", data_dir=bodies.parent)
    loader = ltjs.render("guard", cache=True, **opts)
    out = node_sequence(tmp_path, [loader["path"], loader["path"]])
    first, second = out["results"]
    assert first["ok"] is True and first["policy"] == "plan", first
    assert second["ok"] is True
    stored = {f"lt-suite:js:{Path(p).name[:64]}" for p in loader["published"]}
    assert stored <= set(out["keys"]) and "lt-suite:js:index" in out["keys"]


@needs_node
def test_server_down_falls_back_and_the_stub_fills_the_page_cache(bodies, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = write_cfg(tmp_path, f'body_server = "http://127.0.0.1:{port}"\n')
    opts = dict(plan=str(PLAN), cfg=cfg, out_dir=tmp_path / "o", data_dir=bodies.parent)
    loader = ltjs.render("guard", cache=True, **opts)
    stub = ltjs.render("guard", stub=True, **opts)
    miss, ran, hit = node_sequence(tmp_path, [loader["path"], stub["path"], loader["path"]])["results"]
    assert miss["need"] == "payload" and miss["reason"] == "not-cached,server-unreachable"
    assert ran["ok"] is True and hit["ok"] is True


@needs_node
def test_a_tampered_served_body_executes_nothing(serve, bodies, tmp_path):
    port = serve()
    cfg = write_cfg(tmp_path, f'body_server = "http://127.0.0.1:{port}"\n')
    loader = ltjs.render("guard", cache=True, cfg=cfg, out_dir=tmp_path / "o", data_dir=bodies.parent)
    (bodies / f"{loader['body_sha256']}.gz.b64").write_text(packed("async () => ({injected:true})")[0], encoding="utf-8", newline="\n")
    out = node_sequence(tmp_path, [loader["path"]])
    assert out["results"][0]["reason"] == "not-cached,server-http-409"
    assert out["keys"] == []


# --------------------------------------------------------------------------- installer

@pytest.fixture
def host(tmp_path):
    return {"dest": tmp_path / "host" / "lt-suite", "data": tmp_path / "data"}


def test_installer_copies_a_matching_server_and_writes_a_unit_it_does_not_start(host):
    info = installer.install(host["dest"], origin=ORIGIN, port=8734, data_dir=host["data"],
                             python=sys.executable)
    copy, unit = Path(info["server"]), Path(info["unit"])
    assert copy.read_bytes() == (REPO / "tools/host/lt_body_server.py").read_bytes()
    assert info["sha256"] == installer.manifest_sha(installer.MANIFEST)
    assert stat.S_IMODE(copy.stat().st_mode) == 0o400
    assert stat.S_IMODE(host["dest"].stat().st_mode) == 0o700
    assert stat.S_IMODE((host["data"] / "js-bodies").stat().st_mode) == 0o700
    text = unit.read_text(encoding="utf-8")
    assert f'ExecStart="{sys.executable}" "-I" "{copy}" "--dir" "{host["data"] / "js-bodies"}"' in text
    assert f'"--origin" "{ORIGIN}" "--port" "8734"' in text and "NoNewPrivileges=yes" in text
    again = installer.install(host["dest"], origin=ORIGIN, port=8734, data_dir=host["data"],
                              python=sys.executable)
    assert again["sha256"] == info["sha256"]


def test_installer_refuses_a_changed_server_or_an_unexpected_hash(host, tmp_path):
    changed = tmp_path / "src" / "lt_body_server.py"
    changed.parent.mkdir()
    changed.write_bytes((REPO / "tools/host/lt_body_server.py").read_bytes() + b"\n# edited\n")
    with pytest.raises(installer.Refused, match="Refusing"):
        installer.install(host["dest"], origin=ORIGIN, port=8734, data_dir=host["data"],
                          python=sys.executable, server=changed)
    with pytest.raises(installer.Refused, match="not the expected"):
        installer.install(host["dest"], origin=ORIGIN, port=8734, data_dir=host["data"],
                          python=sys.executable, expect="0" * 64)
    assert not host["dest"].exists()


def test_installer_refuses_folders_an_agent_may_write(host, tmp_path):
    for dest in (REPO / "tools" / "host" / "copy", host["data"] / "bin"):
        with pytest.raises(installer.Refused, match="inside"):
            installer.install(dest, origin=ORIGIN, port=8734, data_dir=host["data"], python=sys.executable)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    os.chmod(shared, 0o777)
    with pytest.raises(installer.Refused, match="unsafe"):
        installer.install(shared, origin=ORIGIN, port=8734, data_dir=host["data"], python=sys.executable)
    assert not (REPO / "tools" / "host" / "copy").exists()


def test_unit_words_are_quoted_for_systemd():
    assert installer.unit_arg('/a b/"c"%d$e\\f') == '"/a b/\\"c\\"%%d$$e\\\\f"'
    with pytest.raises(installer.Refused):
        installer.unit_arg("/a\nb")


def test_host_manifest_matches_the_server():
    line = (REPO / "packaging" / "host-files.sha256").read_text(encoding="utf-8").strip()
    digest = hashlib.sha256((REPO / "tools/host/lt_body_server.py").read_bytes()).hexdigest()
    assert line == f"{digest}  tools/host/lt_body_server.py"
