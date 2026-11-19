"""Write per-task Kiro agent configuration under KIRO_HOME."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

AGENT_NAME = "rca"
_GATEWAY_SERVER = "agentcore-gateway"
DEFAULT_KIRO_MODEL = os.environ.get("KIRO_DEFAULT_MODEL", "claude-haiku-5.5").strip() or (
    "claude-haiku-5.5"
)


def kiro_model_for_task(explicit: str) -> str:
    """Task override, else the fleet default (Haiku 5.5 unless KIRO_DEFAULT_MODEL is set)."""
    value = explicit.strip()
    return value or DEFAULT_KIRO_MODEL


def write_agent_profile(kiro_home: Path, *, model: str | None = None) -> Path:
    profile = {
        "name": AGENT_NAME,
        "model": kiro_model_for_task(model or ""),
        "description": "Remote coding agent (shell verify/publish; AgentCore Gateway for provider APIs).",
        "includeMcpJson": False,
        "tools": [
            "read",
            "write",
            "shell",
            f"@{_GATEWAY_SERVER}",
        ],
        "mcpServers": {
            _GATEWAY_SERVER: {
                "command": sys.executable,
                "args": [
                    "-m",
                    "coding_agent.mcp.agentcore_gateway_bridge",
                ],
            },
        },
    }
    agents_dir = kiro_home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{AGENT_NAME}.json"
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return path
