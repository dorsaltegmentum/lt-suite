# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate platform manifests from packaging/plugin.toml using Python 3.11+.

Run from any directory. --check renders into a temporary directory, compares
bytes without changing the release tree, and checks skill metadata.version.
The portable MCP schema has no tool filters; use the Codex config snippet for
its allowlist. Gemini and Antigravity receive their native denylist fields.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib

SCHEMA_BASE = "https://agent-plugins.org/schemas/1.0.0/"
SPDX = (
    "# SPDX-FileCopyrightText: 2026 dorsaltegmentum\n"
    "# SPDX-License-" "Identifier: " "AGPL-3.0-or-later\n"
)


def expected_skills(root: Path) -> list[Path]:
    """Fail closed when the declared skill set is incomplete or ambiguous."""
    source = tomllib.loads((root / "packaging/plugin.toml").read_text(encoding="utf-8"))
    names = source["skills"]
    if (not isinstance(names, list) or not names or len(names) != len(set(names))
            or any(not isinstance(n, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", n) for n in names)):
        raise ValueError("packaging/plugin.toml needs a nonempty unique skill list")
    actual = {p.name for p in (root / "skills").iterdir() if p.is_dir()}
    if actual != set(names):
        raise ValueError("skill folders differ from declared set: " + ", ".join(sorted(actual ^ set(names))))
    paths = [root / "skills" / n for n in sorted(names)]
    for path in paths:
        if path.is_symlink() or not (path / "SKILL.md").is_file() or (path / "SKILL.md").is_symlink():
            raise ValueError(f"skill missing regular SKILL.md: {path.name}")
    return paths


def json_bytes(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render(root: Path) -> tuple[dict[str, bytes], str]:
    """Read the two sources and return every generated file, in stable order."""
    with (root / "packaging/plugin.toml").open("rb") as stream:
        source = tomllib.load(stream)
    identity = {
        key: source[key]
        for key in (
            "name", "version", "description", "homepage", "repository",
            "license", "keywords",
        )
    }
    identity["author"] = {"name": source["author"], "url": source["author_url"]}
    expected_skills(root)
    servers = source["mcp"]
    disabled = source["tool_filters"]["input_disabled"]
    enabled = source["tool_filters"]["codex_enabled"]
    portable_servers = {
        name: {"type": "stdio", **server} for name, server in servers.items()
    }
    gemini_servers = {
        name: {**server, "excludeTools": disabled} for name, server in servers.items()
    }
    antigravity = {"mcpServers": {
        name: {**server, "disabledTools": disabled} for name, server in servers.items()
    }}
    output = {
        # Listing fields belong only in the Claude plugin manifest. Agent Plugins
        # and Gemini have closed schemas; Claude marketplace entries reject icon.
        ".claude-plugin/plugin.json": json_bytes({**identity, "icon": source["icon"]}),
        ".claude-plugin/marketplace.json": json_bytes({
            "name": source["marketplace_name"],
            "description": source["description"],
            "owner": source["owner"],
            "plugins": [{
                "name": source["name"], "source": "./",
                "description": source["description"], "version": source["version"],
            }],
        }),
        "plugin.json": json_bytes({"$schema": SCHEMA_BASE + "plugin.schema.json", **identity}),
        "mcp.json": json_bytes({
            "$schema": SCHEMA_BASE + "mcp.schema.json", "mcpServers": portable_servers,
        }),
        ".mcp.json": json_bytes({"mcpServers": servers}),
        "mcp_config.json": json_bytes(antigravity),
        "gemini-extension.json": json_bytes({
            "name": source["name"], "version": source["version"],
            "description": source["description"], "contextFileName": "AGENTS.md",
            # The extension reference disallows trust here; settings allow false.
            "mcpServers": gemini_servers,
        }),
        "rules/lt-suite-safety.md": (
            b"---\ntrigger: always_on\n---\n\n"
            + (root / "shared/references/safety.md").read_bytes()
        ),
        "examples/mcp/cursor-mcp.json": json_bytes({"mcpServers": servers}),
        "examples/mcp/vscode-mcp.json": json_bytes({"servers": portable_servers}),
        "examples/mcp/antigravity-mcp.json": json_bytes(antigravity),
        "examples/mcp/gemini-settings.json": json_bytes({"mcpServers": {
            name: {**server, "trust": False} for name, server in gemini_servers.items()
        }}),
        "examples/mcp/copilot-mcp-config.json": json_bytes({"mcpServers": portable_servers}),
    }
    # JSON string/array syntax is also valid TOML for these string-only values.
    codex = SPDX + "# Generated by tools/gen_manifests.py. Merge into config.toml.\n"
    for name, server in servers.items():
        codex += (
            f"\n[mcp_servers.{json.dumps(name)}]\n"
            f"command = {json.dumps(server['command'])}\n"
            f"args = {json.dumps(server['args'])}\n"
            f"enabled_tools = {json.dumps(enabled)}\n"
            "startup_timeout_sec = 30\n"
            'default_tools_approval_mode = "prompt"\n'
        )
    output["examples/mcp/codex-config.toml"] = codex.encode("utf-8")
    pw = source["playwright"]
    server = {"command": pw["command"], "args": pw["args"]}
    for target, field in (("gemini", "excludeTools"), ("antigravity", "disabledTools")):
        output[f"examples/mcp/playwright-{target}.json"] = json_bytes({
            "mcpServers": {"lt-playwright": {**server, field: pw["disabled"]}},
        })
    output["examples/mcp/playwright-codex.toml"] = (SPDX +
        '# Optional alternative browser server. Do not enable additional capabilities.\n'
        '[mcp_servers.lt-playwright]\n'
        f'command = {json.dumps(pw["command"])}\n'
        f'args = {json.dumps(pw["args"])}\n'
        f'enabled_tools = {json.dumps(pw["enabled"])}\n'
        'default_tools_approval_mode = "prompt"\n').encode()
    return output, source["version"]


def yaml_scalar(value: str) -> str | None:
    """Read a release-version scalar, refusing unsupported YAML structures."""
    match = re.fullmatch(r'\s*("[^"\n]*"|\x27[^\x27\n]*\x27|[^\s#{}\[\],]+)\s*(?:#.*)?', value)
    if match is None:
        return None
    scalar = match.group(1)
    return scalar[1:-1] if scalar.startswith(('"', "'")) else scalar


def skill_version(path: Path) -> str | None:
    """Read metadata.version from the frontmatter, never the Markdown body.

    Release skills use a nested YAML mapping. A simple inline mapping is also
    accepted. Unsupported or ambiguous metadata fails closed during --check;
    this function deliberately does not implement general YAML.
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    if match is None:
        return None
    lines = match.group(1).splitlines()
    versions = []
    metadata_seen = False
    for index, line in enumerate(lines):
        field = re.match(r"^metadata:\s*(.*)$", line)
        if field is None:
            continue
        if metadata_seen:
            return None
        metadata_seen = True
        rest = field.group(1).strip()
        if rest.startswith("{"):
            flow = re.fullmatch(r"\{(.*?)\}\s*(?:#.*)?", rest)
            if flow is None:
                return None
            for item in flow.group(1).split(","):
                key, separator, value = item.partition(":")
                if separator and key.strip() == "version":
                    versions.append(yaml_scalar(value))
        elif not rest or rest.startswith("#"):
            indent = None
            for child in lines[index + 1:]:
                if not child.strip() or child.lstrip().startswith("#"):
                    continue
                if not child.startswith(" "):
                    break
                width = len(child) - len(child.lstrip(" "))
                if indent is None:
                    indent = width
                version = re.match(r"^ +version:\s*(.*)$", child)
                if width == indent and version:
                    versions.append(yaml_scalar(version.group(1)))
    return versions[0] if len(versions) == 1 else None


def write_files(destination: Path, output: dict[str, bytes]) -> None:
    for name, content in output.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without changing files")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Release root (defaults to the script's repository)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        output, version = render(root)
        if not args.check:
            write_files(root, output)
            print(f"Generated {len(output)} files.")
            return 0
        if not (root / "AGENTS.md").is_file():
            raise ValueError("Gemini contextFileName is missing: AGENTS.md")
        drift = []
        # Materialize a full candidate tree before comparing exact bytes.
        with tempfile.TemporaryDirectory(prefix="lt-suite-manifests-", dir=os.environ.get("TMPDIR")) as temp:
            candidate = Path(temp)
            write_files(candidate, output)
            for name in sorted(output):
                path = root / name
                if not path.is_file() or path.read_bytes() != (candidate / name).read_bytes():
                    drift.append(name)
        for name in drift:
            print(name)
        problems = []
        for folder in expected_skills(root):
            path = folder / "SKILL.md"
            actual = skill_version(path)
            if actual != version:
                problems.append(path.relative_to(root).as_posix())
                print(f"{problems[-1]}: metadata.version {actual!r}; expected {version!r}")
        if drift or problems:
            return 1
        print(f"No manifest drift; skill versions match {version}.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Manifest generation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
