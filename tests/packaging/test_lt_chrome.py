# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Probe the configured MCP server over stdio without opening or operating a browser."""

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCUMENTED_TOOLS = {
    "close_page", "evaluate_script", "get_console_message", "get_css_styles",
    "lighthouse_audit", "list_console_messages", "list_pages", "navigate_page",
    "new_page", "screencast_start", "screencast_stop", "select_page",
    "take_screenshot", "take_snapshot", "wait_for",
}
REQUIRED_TOOLS = {
    "list_pages", "select_page", "navigate_page", "evaluate_script",
    "take_screenshot", "wait_for",
}
FLAGS = {
    "--no-category-input": "--categoryInput",
    "--no-category-network": "--categoryNetwork",
    "--no-category-memory": "--categoryMemory",
    "--no-category-performance": "--categoryPerformance",
    "--no-category-emulation": "--categoryEmulation",
    "--redact-network-headers": "--redactNetworkHeaders",
}
NETWORK_ERRORS = ("ENOTFOUND", "EAI_AGAIN", "ENETUNREACH", "ECONNREFUSED", "ETIMEDOUT", "ENETDOWN")


@pytest.fixture(scope="module")
def configured_server():
    with (REPO / "packaging/plugin.toml").open("rb") as stream:
        config = tomllib.load(stream)["mcp"]["lt-chrome"]
    executable = shutil.which(config["command"])
    unavailable = pytest.fail if os.environ.get("CI") else pytest.skip
    if executable is None:
        unavailable("not run: npx is unavailable; configured MCP runtime cannot start")
    # Package lookup and all subprocess working files stay outside the release tree.
    with tempfile.TemporaryDirectory(prefix="lt-chrome-probe-", dir=os.environ.get("TMPDIR")) as scratch:
        command = [executable, *config["args"]]
        try:
            help_result = subprocess.run(command + ["--help"], cwd=scratch, capture_output=True,
                                         text=True, timeout=180)
        except subprocess.TimeoutExpired:
            unavailable("not run: npx package lookup timed out; network/package availability is unverified")
        if help_result.returncode and any(code in help_result.stderr for code in NETWORK_ERRORS):
            unavailable("not run: npx cannot reach the package registry and the pinned package is unavailable")
        assert help_result.returncode == 0, "configured MCP --help failed: " + help_result.stderr[-1500:]
        yield command, scratch, help_result.stdout


def test_server_flags_match_pinned_cli_help(configured_server):
    # A misspelled category flag can be accepted by a permissive CLI yet expose unsafe tools.
    command, _, help_text = configured_server
    assert "chrome-devtools-mcp@1.12.0" in command
    for flag, option in FLAGS.items():
        assert flag in command, flag
        assert option in help_text, option


def test_runtime_tools_list_stays_within_documented_set(configured_server):
    # Probe the real generated server configuration, rather than assuming deny lists work.
    command, scratch, _ = configured_server
    generated = json.loads((REPO / ".mcp.json").read_bytes())["mcpServers"]["lt-chrome"]
    assert [shutil.which(generated["command"]), *generated["args"]] == command
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=scratch, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
        lines = queue.Queue()

        def read_lines():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()

        def send(message):
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def response(request_id):
            deadline = time.monotonic() + 60
            while True:
                try:
                    line = lines.get(timeout=max(0, deadline - time.monotonic()))
                except queue.Empty:
                    pytest.fail("configured MCP server timed out before JSON-RPC response")
                assert line is not None, "configured MCP server exited before JSON-RPC response"
                item = json.loads(line)
                if item.get("id") == request_id:
                    assert "error" not in item, item.get("error")
                    return item["result"]

        try:
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "1.0", "capabilities": {},
                "clientInfo": {"name": "lt-suite-filter-test", "version": "1.0"}}})
            # Accept the server's negotiated protocol version when this probe asks for 1.0.
            assert response(1).get("protocolVersion")
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            names = set()
            params = {}
            for request_id in range(2, 7):
                send({"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params})
                result = response(request_id)
                names.update(tool["name"] for tool in result["tools"])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
                params = {"cursor": cursor}
            else:
                pytest.fail("MCP tools/list exceeded five pages")
            assert names <= DOCUMENTED_TOOLS, "unexpected tools: " + ", ".join(sorted(names - DOCUMENTED_TOOLS))
            assert REQUIRED_TOOLS <= names, "configured filters removed a required browser verb"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=2)
            process.stdin.close()
            process.stdout.close()


def test_probe_fails_in_ci_when_npx_is_missing(monkeypatch):
    # Registry and executable outages must fail CI instead of hiding the runtime probe.
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    with pytest.raises(pytest.fail.Exception, match='npx is unavailable'):
        next(configured_server.__wrapped__())


def test_probe_uses_the_resolved_executable(monkeypatch):
    # Windows resolves npx.cmd; CreateProcess cannot resolve a bare npx string.
    monkeypatch.setattr(shutil, 'which', lambda name: 'synthetic-npx.cmd')
    called = []

    def run(command, **kwargs):
        called.append(command)
        return subprocess.CompletedProcess(command, 0, 'synthetic help', '')

    monkeypatch.setattr(subprocess, 'run', run)
    fixture = configured_server.__wrapped__()
    try:
        command, _, _ = next(fixture)
        assert command[0] == called[0][0] == 'synthetic-npx.cmd'
    finally:
        fixture.close()


def test_probe_timeout_fails_in_ci(monkeypatch):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setattr(shutil, 'which', lambda name: 'synthetic-npx.cmd')

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(pytest.fail.Exception, match='timed out'):
        next(configured_server.__wrapped__())
