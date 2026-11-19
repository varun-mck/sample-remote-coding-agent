"""Harness MCP: run_verification and publish_branch (POC)."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

POC_ROOT = Path(__file__).resolve().parents[1]
if str(POC_ROOT) not in sys.path:
    sys.path.insert(0, str(POC_ROOT))

from lib.state import load, save  # noqa: E402

SERVER_NAME = "harness"
_GIT_IDENTITY = ("-c", "user.email=kiro-poc@local", "-c", "user.name=kiro-poc")


def _run_verify(workspace: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": f"{sys.executable} -m unittest discover -q",
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-2000:] if proc.stdout else "",
        "stderr": proc.stderr[-2000:] if proc.stderr else "",
    }


def _has_changes(workspace: Path) -> bool:
    proc = subprocess.run(
        ["git", *_GIT_IDENTITY, "status", "--porcelain"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def build_server(state_path: Path, workspace: Path) -> Server:
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
                description="Commit changes on the task branch locally (no git push).",
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
    async def tool_handler(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        data = load(state_path)
        if name == "run_verification":
            result = _run_verify(workspace)
            passed = result.get("exitCode") == 0
            data["verified"] = passed
            data["verification"] = result
            save(state_path, data)
            summary = f"Verification finished: exit {result.get('exitCode')}"
            return [TextContent(type="text", text=summary)]

        if name == "publish_branch":
            if not _has_changes(workspace):
                return [TextContent(type="text", text="Refused: no changes to commit.")]
            message = str(arguments.get("commit_message") or "agent commit").strip()
            subprocess.run(
                ["git", *_GIT_IDENTITY, "add", "-A"],
                cwd=workspace,
                check=False,
            )
            proc = subprocess.run(
                ["git", *_GIT_IDENTITY, "commit", "-m", message],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return [
                    TextContent(
                        type="text",
                        text=f"Commit failed: {proc.stderr or proc.stdout}",
                    )
                ]
            data["published"] = True
            save(state_path, data)
            branch = data.get("branch", "unknown")
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Branch {branch} committed locally. "
                        "Use the gateway create_pull_request tool to open a PR."
                    ),
                )
            ]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def serve(state_path: Path, workspace: Path) -> None:
    server = build_server(state_path, workspace)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.state.resolve(), args.workspace.resolve()))


if __name__ == "__main__":
    main()
