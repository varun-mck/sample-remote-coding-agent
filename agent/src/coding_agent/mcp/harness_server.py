"""Harness MCP: run_verification and publish_branch."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from coding_agent import commands, credentials, git_ops, state_file

SERVER_NAME = "harness"


def build_server(state_path: Path, workspace: Path, *, ctx_json: Path) -> Server:
    import json

    ctx = json.loads(ctx_json.read_text(encoding="utf-8"))
    repo = workspace

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="run_verification",
                description="Run this repository's verification command and record whether it passed.",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            Tool(
                name="publish_branch",
                description="Commit and push the task branch (no direct git push from shell).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "commit_message": {
                            "type": "string",
                            "description": "One-line summary for the commit.",
                        }
                    },
                    "required": ["commit_message"],
                    "additionalProperties": False,
                },
            ),
        ]

    @server.call_tool()
    async def tool_handler(name: str, arguments: dict[str, Any]) -> list[TextContent]:  # noqa: PLR0911
        data = state_file.load(state_path)
        if name == "run_verification":
            command = str(ctx.get("verify_command") or "").strip()
            if not command:
                data["verified"] = False
                data["verification"] = {"command": "", "exitCode": None}
                state_file.save(state_path, data)
                return [TextContent(type="text", text="No verification command is configured.")]
            result = commands.run_command(command, cwd=repo)
            passed = result.get("exitCode") == 0
            data["verified"] = passed
            data["verification"] = dict(result)
            state_file.save(state_path, data)
            return [
                TextContent(
                    type="text", text=f"Verification finished: exit {result.get('exitCode')}"
                )
            ]

        if name == "publish_branch":
            if not any(not p.startswith(".agent/") for p in git_ops.changed_paths(repo)):
                return [
                    TextContent(type="text", text="Refused: no changes outside .agent/ to publish.")
                ]
            message = str(arguments.get("commit_message") or "").strip()
            git_ops.commit(repo, message)
            from coding_agent.providers import load_providers, provider_for_host

            host = str(ctx.get("host") or "")
            providers = load_providers()
            provider = provider_for_host(host, providers)
            if provider is None:
                return [TextContent(type="text", text="Refused: unknown git host.")]
            token = credentials.fetch_token(provider)
            if not token:
                return [
                    TextContent(type="text", text="Refused: no credential for this Git provider.")
                ]
            git_ops.push(
                repo,
                str(ctx.get("branch") or ""),
                token=token,
                repository=str(ctx.get("slug") or ""),
                host=provider.host,
                username=provider.git_username,
            )
            data["published"] = True
            state_file.save(state_path, data)
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Branch {ctx.get('branch')} pushed. "
                        "Use the gateway create_pull_request tool to open a pull request."
                    ),
                )
            ]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def serve(state_path: Path, workspace: Path, ctx_json: Path) -> None:
    server = build_server(state_path, workspace, ctx_json=ctx_json)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.state.resolve(), args.workspace.resolve(), args.context.resolve()))


if __name__ == "__main__":
    main()
