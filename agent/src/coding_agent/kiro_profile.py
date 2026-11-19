"""Write per-task Kiro agent configuration under KIRO_HOME."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from coding_agent.providers import Provider, gateway_tool_prefix

AGENT_NAME = "rca"


def _hook_command(branch: str, base: str, state: Path) -> str:
    script = Path(__file__).resolve().parent / "mcp" / "policy_hook.py"
    parts = [
        sys.executable,
        str(script),
        f"--state={state}",
        f"--branch={branch}",
        f"--base={base}",
    ]
    return " ".join(parts)


def write_agent_profile(
    kiro_home: Path,
    *,
    state_path: Path,
    workspace: Path,
    context_path: Path,
    provider: Provider,
    branch: str,
    base_branch: str,
) -> Path:
    gateway_prefix = gateway_tool_prefix(provider).removeprefix("mcp__").replace("__", "/")
    profile = {
        "name": AGENT_NAME,
        "description": "Remote coding agent harness (verification, publish, draft PR).",
        "includeMcpJson": True,
        "tools": [
            "read",
            "write",
            "shell",
            "@harness",
            "@harness/run_verification",
            "@harness/publish_branch",
            "@gateway",
            "@gateway/create_pull_request",
        ],
        "mcpServers": {
            "harness": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "coding_agent.mcp.harness_server",
                    f"--state={state_path}",
                    f"--workspace={workspace}",
                    f"--context={context_path}",
                ],
            },
            "gateway": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "coding_agent.mcp.gateway_server",
                    f"--state={state_path}",
                    f"--provider={provider.name}",
                ],
            },
        },
        "hooks": [
            {
                "name": "policy-pre-tool",
                "trigger": "PreToolUse",
                "matcher": "*",
                "action": {
                    "type": "command",
                    "command": _hook_command(branch, base_branch, state_path),
                },
                "timeout": 30,
            },
            {
                "name": "policy-post-tool",
                "trigger": "PostToolUse",
                "matcher": "*",
                "action": {
                    "type": "command",
                    "command": _hook_command(branch, base_branch, state_path),
                },
                "timeout": 30,
            },
        ],
    }
    _ = gateway_prefix
    agents_dir = kiro_home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{AGENT_NAME}.json"
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return path
