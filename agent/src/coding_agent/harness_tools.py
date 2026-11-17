"""In-process MCP tools the harness owns: verification and branch publish."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from . import commands, credentials, git_ops
from .task_context import TaskContext

log = logging.getLogger(__name__)

HARNESS_SERVER_NAME = "harness"
HARNESS_TOOLS = "mcp__harness__*"


def _has_work(repo: Path) -> bool:
    return any(not p.startswith(".agent/") for p in git_ops.changed_paths(repo))


def build_server(ctx: TaskContext) -> McpSdkServerConfig | None:
    repo = Path(ctx.repo_dir)

    @tool(
        "run_verification",
        "Run this repository's verification command and record whether it passed.",
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    async def run_verification(_args: dict[str, Any]) -> dict[str, Any]:
        command = ctx.verify_command
        if not command:
            ctx.verified = False
            ctx.verification = {"command": "", "exitCode": None, "warning": "no verify command"}
            return {"content": [{"type": "text", "text": "No verification command is configured."}]}
        result = commands.run_command(command, cwd=repo)
        passed = result.get("exitCode") == 0
        ctx.verified = passed
        ctx.verification = dict(result)
        summary = f"exit {result.get('exitCode')}"
        warning = result.get("warning")
        if warning:
            summary = f"{summary}; {warning}"
        return {"content": [{"type": "text", "text": f"Verification finished: {summary}"}]}

    @tool(
        "publish_branch",
        "Commit, push the task branch, and prepare for opening a pull request.",
        {
            "type": "object",
            "properties": {
                "commit_message": {
                    "type": "string",
                    "description": "One-line summary for the commit and pull request title.",
                }
            },
            "required": ["commit_message"],
            "additionalProperties": False,
        },
    )
    async def publish_branch(args: dict[str, Any]) -> dict[str, Any]:
        if not _has_work(repo):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Refused: no changes outside .agent/ to publish.",
                    }
                ]
            }
        message = str(args.get("commit_message") or ctx.prompt).strip()
        git_ops.commit(repo, f"{message}\n\n{ctx.prompt}\n\nTask: {ctx.task_id}\n")
        token = credentials.fetch_token(ctx.ref.provider)
        if not token and __import__("os").environ.get("TASKS_TABLE"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Refused: no credential for this Git provider.",
                    }
                ]
            }
        git_ops.push(
            repo,
            ctx.branch,
            token=token,
            repository=ctx.ref.slug,
            host=ctx.ref.provider.host,
            username=ctx.ref.provider.git_username,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Branch {ctx.branch} pushed. Use the Gateway tool to open a pull request.",
                }
            ]
        }

    return create_sdk_mcp_server(name=HARNESS_SERVER_NAME, tools=[run_verification, publish_branch])
