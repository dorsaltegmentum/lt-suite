# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise manifest generation, platform contracts and failing drift checks."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "tools/gen_manifests.py"
OUTPUTS = [
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    "plugin.json", "mcp.json", ".mcp.json", "mcp_config.json",
    "gemini-extension.json", "rules/lt-suite-safety.md",
    "examples/mcp/codex-config.toml", "examples/mcp/cursor-mcp.json",
    "examples/mcp/vscode-mcp.json", "examples/mcp/antigravity-mcp.json",
    "examples/mcp/gemini-settings.json", "examples/mcp/copilot-mcp-config.json",
]
DISABLED = {
    "new_page", "click", "click_at", "drag", "fill", "fill_form", "hover", "press_key",
    "type_text", "upload_file", "handle_dialog",
}
ENABLED = {
    "list_pages", "select_page", "navigate_page", "evaluate_script",
    "take_screenshot", "wait_for",
}
ARGS = [
    "-y", "chrome-devtools-mcp@1.12.0", "--autoConnect",
    "--no-usage-statistics", "--no-performance-crux",
    "--no-category-input", "--no-category-network", "--no-category-memory",
    "--no-category-performance", "--no-category-emulation", "--redact-network-headers",
]


@pytest.fixture
def source(tmp_path):
    # A private temp tree keeps tests independent of concurrent skill writers.
    for name in ("packaging/plugin.toml", "shared/references/safety.md",
                 "packaging/lt-chrome-bootstrap.cjs", "shared/lt_chrome/lt-chrome.cjs",
                 "shared/schemas/config.schema.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    config = tmp_path / "packaging/plugin.toml"
    text = config.read_text(encoding='utf-8')
    # This synthetic tree deliberately contains one skill.
    import re
    # This synthetic release stays at 0.1.0, including its sample skill below.
    text = re.sub(r'^version = .*?$', 'version = "0.1.0"', text, flags=re.M)
    text = re.sub(r"^skills = .*?$", 'skills = ["sample"]', text, flags=re.M)
    if "skills =" not in text:
        text = 'skills = ["sample"]\n' + text
    config.write_text(text, encoding='utf-8', newline="\n")
    (tmp_path / "AGENTS.md").write_text("Runtime guide.\n", encoding='utf-8', newline="\n")
    skill = tmp_path / "skills/sample/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: sample\nmetadata:\n  version: "0.1.0"\n---\nBody.\n', encoding='utf-8', newline="\n")
    return tmp_path


def run(root, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--root", str(root), *args],
        encoding='utf-8', capture_output=True, text=True, env=env, cwd=root.parent,
    )


def read(root, name):
    return json.loads((root / name).read_bytes())


def test_generates_every_manifest_and_check_is_read_only(source):
    # Catches omitted outputs and a check command that rewrites drift away.
    result = run(source)
    assert result.returncode == 0, result.stderr
    assert all((source / name).is_file() for name in OUTPUTS)
    before = {name: (source / name).read_bytes() for name in OUTPUTS}
    assert run(source, "--check").returncode == 0
    assert before == {name: (source / name).read_bytes() for name in OUTPUTS}


@pytest.mark.parametrize("name", OUTPUTS)
@pytest.mark.parametrize("mutation", ["change", "missing"])
def test_check_fails_on_each_drifted_or_missing_output(source, name, mutation):
    # Catches a no-op drift check, JSON-only comparison and incomplete coverage.
    assert run(source).returncode == 0
    target = source / name
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b" ")
    before = target.read_bytes() if target.exists() else None
    result = run(source, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert name in result.stdout.splitlines()
    assert (target.read_bytes() if target.exists() else None) == before


def test_manifest_fields_and_json_contracts(source):
    # Catches incompatible author shapes, missing identity and closed-schema keys.
    assert run(source).returncode == 0
    for name in OUTPUTS:
        if name.endswith(".json"):
            assert isinstance(read(source, name), dict)
    fields = {
        "name", "version", "description", "author", "homepage", "repository",
        "license", "keywords",
    }
    for name in ("plugin.json", ".claude-plugin/plugin.json"):
        data = read(source, name)
        assert fields <= data.keys()
        assert data["name"] == "lt-suite"
        assert data["version"] == "0.1.0"
        assert data["author"] == {
            "name": "dorsaltegmentum", "url": "https://github.com/dorsaltegmentum",
        }
        assert data["homepage"] == data["repository"] == "https://github.com/dorsaltegmentum/lt-suite"
        assert data["license"] == "AGPL-3.0-or-later"
        assert "unofficial" in data["description"].lower()
        assert isinstance(data["keywords"], list) and data["keywords"]
        assert all(isinstance(word, str) for word in data["keywords"])
        assert all(isinstance(data[key], str) and data[key] for key in fields - {"author", "keywords"})
    portable = read(source, "plugin.json")
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert set(portable) <= fields | {"$schema", "extensions"}
    marketplace = read(source, ".claude-plugin/marketplace.json")
    assert marketplace["name"] == "lt-suite"
    assert marketplace["owner"]["name"] == "dorsaltegmentum"
    assert len(marketplace["plugins"]) == 1
    assert marketplace["plugins"][0]["name"] == "lt-suite"
    assert marketplace["plugins"][0]["source"] == "./"
    gemini = read(source, "gemini-extension.json")
    assert gemini["name"] == "lt-suite"
    assert gemini["version"] == "0.1.0"
    assert gemini["description"]
    assert gemini["contextFileName"] == "AGENTS.md"
    portable_mcp = read(source, "mcp.json")
    assert set(portable_mcp) == {"$schema", "mcpServers"}
    assert portable_mcp["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert set(portable_mcp["mcpServers"]) == {"lt-chrome"}
    server = portable_mcp["mcpServers"]["lt-chrome"]
    assert server["type"] == "stdio"
    assert set(server) <= {"type", "command", "args", "env", "cwd"}


def test_icon_reaches_the_claude_manifest_without_closed_schema_fields(source):
    """A listing icon must survive generation without breaking other manifest schemas."""
    assert run(source).returncode == 0
    assert read(source, ".claude-plugin/plugin.json")["icon"] == "./assets/icon.png"
    assert "icon" not in read(source, ".claude-plugin/marketplace.json")["plugins"][0]
    assert "icon" not in read(source, "plugin.json")
    assert "icon" not in read(source, "gemini-extension.json")


def test_pinned_mcp_commands_and_platform_tool_filters(source):
    # Catches input tools returning through a platform's wrong filter spelling.
    assert run(source).returncode == 0
    for name in OUTPUTS:
        if not name.endswith(".json") or "plugin" in name and name != "gemini-extension.json":
            continue
        data = read(source, name)
        key = "servers" if name.endswith("vscode-mcp.json") else "mcpServers"
        server = data[key]["lt-chrome"]
        if name.startswith("examples/"):
            assert server["command"] == "npx"
            assert server["args"] == ["--no-workspaces", *ARGS]
            assert server["env"]["CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY"] == "1"
        else:
            assert server["command"] == "node"
            assert server["args"][0] == "-e"
            assert server["args"][2] == "--"
            assert "via=" + name in server["args"]
    for name in ("gemini-extension.json", "examples/mcp/gemini-settings.json"):
        server = read(source, name)["mcpServers"]["lt-chrome"]
        assert set(server["excludeTools"]) == DISABLED
    # Gemini's extension reference excludes trust from extension server fields.
    assert "trust" not in read(source, "gemini-extension.json")["mcpServers"]["lt-chrome"]
    assert read(source, "examples/mcp/gemini-settings.json")["mcpServers"]["lt-chrome"]["trust"] is False
    for name in ("mcp_config.json", "examples/mcp/antigravity-mcp.json"):
        assert set(read(source, name)["mcpServers"]["lt-chrome"]["disabledTools"]) == DISABLED
    codex = tomllib.loads((source / "examples/mcp/codex-config.toml").read_text(encoding='utf-8'))
    server = codex["mcp_servers"]["lt-chrome"]
    assert set(server["enabled_tools"]) == ENABLED
    assert not ENABLED & DISABLED
    assert server["command"] == "npx" and server["args"] == ["--no-workspaces", *ARGS]
    assert server["startup_timeout_sec"] == 30
    assert server["default_tools_approval_mode"] == "prompt"


@pytest.mark.parametrize("metadata", [
    'metadata:\n  version: "9.9.9"',
    'metadata:\n  version: "9.9.9" # old release',
    'metadata: {version: "9.9.9"}',
    'metadata:\n  other: value',
    'description: no metadata',
])
def test_check_rejects_skill_version_mismatch_without_editing(source, metadata):
    # Catches stale skill versions and matching version strings in body prose.
    assert run(source).returncode == 0
    path = source / "skills/sample/SKILL.md"
    content = f'---\nname: sample\n{metadata}\n---\nmetadata:\n  version: "0.1.0"\n'
    path.write_text(content, encoding='utf-8', newline="\n")
    result = run(source, "--check")
    assert result.returncode == 1
    assert "skills/sample/SKILL.md" in result.stdout
    assert "metadata.version" in result.stdout
    assert path.read_text(encoding='utf-8') == content
    assert run(source).returncode == 0
    assert path.read_text(encoding='utf-8') == content


@pytest.mark.parametrize("metadata", [
    'metadata:\n  version: "0.1.0" # release',
    "metadata:\n  version: '0.1.0'",
    'metadata: {version: "0.1.0"}',
])
def test_check_accepts_skill_metadata_version(source, metadata):
    path = source / "skills/sample/SKILL.md"
    path.write_text(f"---\nname: sample\n{metadata}\n---\nBody.\n", encoding='utf-8', newline="\n")
    assert run(source).returncode == 0
    assert run(source, "--check").returncode == 0


def test_source_changes_reach_manifests_and_safety_rule(source):
    # Catches hardcoded identity, stale safety copies and missing always-on trigger.
    assert run(source).returncode == 0
    path = source / "packaging/plugin.toml"
    path.write_text(path.read_text(encoding='utf-8').replace('version = "0.1.0"', 'version = "0.2.0"'), encoding='utf-8', newline="\n")
    safety = source / "shared/references/safety.md"
    safety.write_text(safety.read_text(encoding='utf-8') + "\nAdditional safety instruction.\n", encoding='utf-8', newline="\n")
    assert run(source, "--check").returncode == 1
    assert run(source).returncode == 0
    for name in ("plugin.json", ".claude-plugin/plugin.json", "gemini-extension.json"):
        assert read(source, name)["version"] == "0.2.0"
    rule = (source / "rules/lt-suite-safety.md").read_text(encoding='utf-8')
    assert rule.startswith("---\ntrigger: always_on\n---\n")
    assert rule.endswith(safety.read_text(encoding='utf-8'))


def test_coordinate_click_is_denied_on_each_denylist_platform(source):
    # click_at can appear when vision is enabled; every deny-list client must exclude it.
    assert run(source).returncode == 0
    for name, field in (("gemini-extension.json", "excludeTools"),
                        ("examples/mcp/gemini-settings.json", "excludeTools"),
                        ("mcp_config.json", "disabledTools"),
                        ("examples/mcp/antigravity-mcp.json", "disabledTools")):
        assert "click_at" in read(source, name)["mcpServers"]["lt-chrome"][field], name


@pytest.mark.parametrize("mutation", ["missing", "extra", "empty"])
def test_generation_rejects_incomplete_skill_set(source, mutation):
    # A missing SKILL must fail both generation and drift checking.
    assert run(source).returncode == 0
    if mutation == "extra":
        (source / "skills/extra").mkdir()
    else:
        (source / "skills/sample/SKILL.md").unlink()
        if mutation == "empty":
            (source / "skills/sample").rmdir()
    assert run(source).returncode != 0
    assert run(source, "--check").returncode != 0


def test_context_file_is_required_by_check(source):
    assert run(source).returncode == 0
    (source / "AGENTS.md").unlink()
    result = run(source, "--check")
    assert result.returncode != 0
    assert "AGENTS.md" in result.stdout + result.stderr


@pytest.mark.parametrize("metadata", [
    'metadata:\n  nested:\n    version: "0.1.0"',
    'metadata:\n  version: "0.1.0"\nmetadata:\n  version: "0.1.0"',
])
def test_nested_or_duplicate_metadata_fails_closed(source, metadata):
    assert run(source).returncode == 0
    path = source / "skills/sample/SKILL.md"
    path.write_text(f"---\nname: sample\n{metadata}\n---\nBody.\n", encoding='utf-8', newline="\n")
    assert run(source, "--check").returncode == 1


def test_playwright_snippets_enforce_reviewed_tools(source):
    # Extra Playwright tools can bypass page-side guards.
    assert run(source).returncode == 0
    allowed = {"browser_tabs", "browser_navigate", "browser_evaluate", "browser_take_screenshot", "browser_wait_for"}
    denied = {"browser_click", "browser_close", "browser_console_messages", "browser_drag", "browser_drop",
              "browser_emulate_media", "browser_file_upload", "browser_fill_form", "browser_find",
              "browser_handle_dialog", "browser_hover", "browser_navigate_back", "browser_network_request",
              "browser_network_requests", "browser_press_key", "browser_resize", "browser_run_code_unsafe",
              "browser_select_option", "browser_snapshot", "browser_type"}
    data = tomllib.loads((source / "examples/mcp/playwright-codex.toml").read_text(encoding='utf-8'))
    server = data["mcp_servers"]["lt-playwright"]
    assert set(server["enabled_tools"]) == allowed
    assert server["args"] == ["-y", "@playwright/mcp@0.0.83"]
    assert not allowed & denied
    for target, field in (("gemini", "excludeTools"), ("antigravity", "disabledTools")):
        path = f"examples/mcp/playwright-{target}.json"
        server = read(source, path)["mcpServers"]["lt-playwright"]
        assert set(server[field]) == denied
        assert server["args"] == ["-y", "@playwright/mcp@0.0.83"]
        (source / path).write_text('{}', encoding='utf-8', newline="\n")
        assert run(source, "--check").returncode == 1


def test_public_package_declares_all_six_skills():
    # A reduced declaration must not silently shrink every generated distribution.
    data = tomllib.loads((REPO / 'packaging/plugin.toml').read_text(encoding='utf-8'))
    assert set(data['skills']) == {'lt-suite-setup', 'lt-module-capture', 'lt-module-answer',
                                   'lt-module-fill', 'lt-module-export', 'mechanistic-diagram'}


def test_playwright_filter_sets_cover_the_observed_runtime_catalog():
    # A denylist copied from memory can leave a real write-capable tool exposed.
    catalog = read(REPO, "packaging/playwright-0.0.83-tools.json")
    config = tomllib.loads((REPO / "packaging/plugin.toml").read_text(encoding='utf-8'))["playwright"]
    assert catalog["package"] == "@playwright/mcp"
    assert catalog["version"] == "0.0.83"
    assert len(catalog["tools"]) == len(set(catalog["tools"])) == 25
    allowed, denied = set(config["enabled"]), set(config["disabled"])
    assert len(allowed) == 5 and len(denied) == 20
    assert not allowed & denied
    assert allowed | denied == set(catalog["tools"])


def test_check_reports_stale_pins_block(source):
    assert run(source).returncode == 0
    launcher = source / "shared/lt_chrome/lt-chrome.cjs"
    launcher.write_text(launcher.read_text(encoding='utf-8').replace('"package":"chrome-devtools-mcp@1.12.0"', '"package":"stale"'), encoding='utf-8', newline="\n")
    result = run(source, "--check")
    assert result.returncode == 1
    assert "shared/lt_chrome/lt-chrome.cjs" in result.stdout


def test_examples_carry_hardening_env(source):
    assert run(source).returncode == 0
    for name in OUTPUTS:
        if not name.startswith("examples/mcp/"):
            continue
        if name.endswith(".toml"):
            data = tomllib.loads((source / name).read_text(encoding='utf-8'))["mcp_servers"]
        else:
            doc = read(source, name)
            data = doc.get("mcpServers", doc.get("servers"))
        env = data["lt-chrome"]["env"]
        assert env == {"CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY": "1", "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1", "CHROME_DEVTOOLS_MCP_CATEGORY_EXPERIMENTAL_THIRD_PARTY": ""}
