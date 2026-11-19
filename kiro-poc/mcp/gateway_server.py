"""Fake Git gateway MCP for POC (create/merge PR)."""

from __future__ import annotations

import argparse
import asyncio
import json
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

SERVER_NAME = "gateway"


def build_server(state_path: Path) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="create_pull_request",
                description="Open a pull request on the Git provider (simulated).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "head": {"type": "string"},
                        "base": {"type": "string"},
                        "draft": {"type": "boolean"},
                        "body": {"type": "string"},
                    },
                    "required": ["title", "head", "base"],
                    "additionalProperties": True,
                },
            ),
            Tool(
                name="merge_pull_request",
                description="Merge a pull request (simulated; should be denied by policy).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pull_number": {"type": "integer"},
                    },
                    "additionalProperties": True,
                },
            ),
        ]

    @server.call_tool()
    async def tool_handler(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        data = load(state_path)
        if name == "create_pull_request":
            payload = dict(arguments or {})
            data["gateway_last_call"] = payload
            pr_url = "https://github.example/pr/1"
            data["pr_url"] = pr_url
            save(state_path, data)
            body = {"html_url": pr_url, "url": pr_url, **payload}
            return [TextContent(type="text", text=json.dumps(body))]

        if name == "merge_pull_request":
            return [TextContent(type="text", text=json.dumps({"merged": True}))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def serve(state_path: Path) -> None:
    server = build_server(state_path)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.state.resolve()))


if __name__ == "__main__":
    main()
