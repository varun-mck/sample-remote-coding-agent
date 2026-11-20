"""The `kiro-rca-power` power: remote MCP config, plugin manifest, and dispatch skill."""

from __future__ import annotations

import json
import re

from conftest import REPO_ROOT
from render_power_mcp import build_mcp_config

POWER_ROOT = REPO_ROOT / "kiro-rca-power"


def test_plugin_manifest_names_the_remote_fleet() -> None:
    data = json.loads((POWER_ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "rca-fleet"
    assert "gateway" in data["description"].lower() or "cognito" in data["description"].lower()


def test_mcp_json_schema_is_remote_only() -> None:
    cfg = build_mcp_config(
        gateway_url="https://gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        client_id="abcd",
        oauth_scope="rca/tasks.submit",
    )
    server = cfg["mcpServers"]["rca-fleet"]
    assert "url" in server
    assert "command" not in server
    assert server["oauth"]["redirectUri"] == "http://localhost:7778/oauth/callback"
    assert "rca/tasks.submit" in server["oauthScopes"]


def test_dispatch_skill_names_gateway_tools() -> None:
    text = (POWER_ROOT / "skills/dispatch-task/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "rca-tasks___submit_task" in text
    assert "rca-tasks___get_task" in text
    assert "power.json" not in text
    assert re.search(r"^name:\s*dispatch-task\s*$", text, re.MULTILINE)
