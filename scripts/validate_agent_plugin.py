#!/usr/bin/env python3
"""Validate the published Agent Plugin package and its root compatibility copy."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins" / "universal-userio"
SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)}: expected a JSON object")
    return value


def require_copy(relative_path: str) -> None:
    root_file = ROOT / relative_path
    package_file = PACKAGE / relative_path
    if root_file.read_bytes() != package_file.read_bytes():
        raise ValueError(f"root and package copies differ: {relative_path}")


manifest = load_object(PACKAGE / "plugin.json")
if manifest.get("$schema") != SCHEMA:
    raise ValueError("plugin.json: Agent Plugins 1.0 schema is required")
if manifest.get("name") != "universal-userio" or not NAME.fullmatch(manifest["name"]):
    raise ValueError("plugin.json: invalid package name")
if not isinstance(manifest.get("version"), str) or not re.fullmatch(
    r"0\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", manifest["version"]
):
    raise ValueError("plugin.json: valid version is required")
for field in ("description", "license"):
    if not isinstance(manifest.get(field), str) or not manifest[field].strip():
        raise ValueError(f"plugin.json: {field} is required")
author = manifest.get("author")
if not isinstance(author, dict) or not isinstance(author.get("name"), str):
    raise ValueError("plugin.json: author.name is required")
if set(author) - {"name", "email", "url"}:
    raise ValueError("plugin.json: author contains unsupported fields")

mcp = load_object(PACKAGE / "mcp.json")
if mcp.get("$schema") != MCP_SCHEMA or set(mcp) - {"$schema", "mcpServers"}:
    raise ValueError("mcp.json: Agent Plugins 1.0 MCP schema is required")
servers = mcp.get("mcpServers")
if not isinstance(servers, dict) or not servers:
    raise ValueError("mcp.json: at least one MCP server is required")
for name, server in servers.items():
    if not isinstance(server, dict) or server.get("type") != "streamable-http":
        raise ValueError(f"mcp.json: {name} must use streamable-http")
    url = server.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError(f"mcp.json: {name} must use HTTPS")
    if any(key in server for key in ("headers", "env", "command", "args")):
        raise ValueError(f"mcp.json: {name} has unsupported or credential fields")

skill_dir = PACKAGE / "skills" / "userio"
skill_file = skill_dir / "SKILL.md"
if not skill_file.is_file():
    raise ValueError("skills/userio/SKILL.md is required")
skill_text = skill_file.read_text(encoding="utf-8")
frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.+)\Z", skill_text, re.S)
if not frontmatter:
    raise ValueError("skills/userio/SKILL.md: frontmatter and non-empty body are required")
fields = dict(
    re.findall(r"^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.*?)\s*$", frontmatter.group(1), re.M)
)
if fields.get("name") != skill_dir.name:
    raise ValueError("skills/userio/SKILL.md: name must match its directory")
description = fields.get("description", "")
if not description or len(description) > 1024:
    raise ValueError("skills/userio/SKILL.md: description must be 1..1024 characters")
required_auth_boundaries = (
    "## Authentication boundary (fail closed)",
    "returns 401",
    "Do not retry against another connection, identity, or credential.",
    "Never obtain, read, or reuse a UserIO token",
    "Never call the UserIO backend directly over HTTP",
    "Never present results obtained under a different identity",
)
for boundary in required_auth_boundaries:
    if boundary not in skill_text:
        raise ValueError(f"skills/userio/SKILL.md: missing auth boundary: {boundary}")

for relative_path in ("plugin.json", "mcp.json", "skills/userio/SKILL.md"):
    require_copy(relative_path)

print(f"PASS {manifest['name']} {manifest['version']}: package valid and root copies match")
