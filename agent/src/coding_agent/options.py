"""Claude Agent SDK options for the AIPDLC harness."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import ClaudeAgentOptions

from . import config, harness_tools, hooks
from .providers import gateway_tool_prefix
from .task_context import TaskContext

ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Grep",
    "Glob",
    "Bash",
    "Skill",
    "Task",
)

DENIED_TOOLS: tuple[str, ...] = (
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "ExitPlanMode",
)


def _gateway_proxy() -> dict[str, dict[str, object]] | None:
    command = os.environ.get("GATEWAY_MCP_COMMAND", "").strip()
    if not command:
        return None
    parts = command.split()
    return (
        cast(
            "dict[str, dict[str, object]]",
            {"command": parts[0], "args": parts[1:]},
        )
        if parts
        else None
    )


def _steering_appendix(bundle_root: Path) -> str:
    steering = bundle_root / "steering"
    if not steering.is_dir():
        return ""
    chunks: list[str] = []
    for path in sorted(steering.glob("*.md")):
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(chunks)


def build_options(ctx: TaskContext, *, cwd: Path) -> ClaudeAgentOptions:
    cfg = config.load_config()
    provider = ctx.ref.provider
    harness = harness_tools.build_server(ctx)
    gateway = _gateway_proxy()
    tools = [*ALLOWED_TOOLS, harness_tools.HARNESS_TOOLS, gateway_tool_prefix(provider)]
    plugins = [{"type": "local", "path": str(path)} for path in cfg.bundle_paths]
    steering = _steering_appendix(cfg.bundle_paths[0]) if cfg.bundle_paths else ""
    mcp_servers: dict[str, object] = {}
    if harness is not None:
        mcp_servers[harness_tools.HARNESS_SERVER_NAME] = harness
    if gateway is not None:
        mcp_servers["gateway"] = gateway
    return ClaudeAgentOptions(
        cwd=str(cwd),
        allowed_tools=list(tools),
        disallowed_tools=list(DENIED_TOOLS),
        permission_mode="acceptEdits",
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers=cast(Any, mcp_servers),
        max_turns=cfg.max_turns,
        effort=cfg.effort,
        model=cfg.model,
        fallback_model=cfg.fallback_model,
        plugins=cast(Any, plugins),
        skills="all",
        env={
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
        },
        hooks=hooks.build_hooks(ctx, provider),
        system_prompt=(
            {"type": "preset", "preset": "claude_code", "append": steering} if steering else None
        ),
    )
