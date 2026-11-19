"""Thin MCP bridge: create_pull_request via Git host REST APIs (Gateway-backed in CDK)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from coding_agent import credentials, state_file
from coding_agent.providers import load_providers

SERVER_NAME = "gateway"


def _auth_header(provider_name: str) -> dict[str, str]:
    providers = load_providers()
    provider = next((p for p in providers if p.name == provider_name), None)
    if provider is None:
        return {}
    token = credentials.fetch_token(provider)
    if not token:
        return {}
    raw = f"{provider.git_username}:{token}".encode()
    return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}


def _bitbucket_create_pr(args: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    workspace = str(args.get("workspace") or args.get("owner") or "")
    repo = str(args.get("repo") or args.get("repo_slug") or "")
    body = {
        "title": str(args.get("title") or "Agent pull request"),
        "source": {"branch": {"name": str(args.get("head") or "")}},
        "destination": {"branch": {"name": str(args.get("base") or "main")}},
        "description": str(args.get("body") or ""),
    }
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def _github_create_pr(args: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    owner = str(args.get("owner") or "")
    repo = str(args.get("repo") or "")
    body = {
        "title": str(args.get("title") or "Agent pull request"),
        "head": str(args.get("head") or ""),
        "base": str(args.get("base") or "main"),
        "body": str(args.get("body") or ""),
        "draft": bool(args.get("draft", False)),
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            **headers,
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def build_server(state_path: Path, *, provider_name: str) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="create_pull_request",
                description="Open a pull request for the task branch.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "workspace": {"type": "string"},
                        "repo": {"type": "string"},
                        "repo_slug": {"type": "string"},
                        "head": {"type": "string"},
                        "base": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "draft": {"type": "boolean"},
                    },
                    "additionalProperties": True,
                },
            ),
        ]

    @server.call_tool()
    async def tool_handler(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != "create_pull_request":
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        providers = load_providers()
        provider = next((p for p in providers if p.name == provider_name), None)
        if provider is None:
            return [TextContent(type="text", text="Unknown provider.")]
        headers = _auth_header(provider_name)
        if not headers:
            return [TextContent(type="text", text="No credential for provider.")]
        try:
            if provider.host == "bitbucket.org":
                answer = _bitbucket_create_pr(arguments, headers)
            else:
                answer = _github_create_pr(arguments, headers)
        except urllib.error.HTTPError as err:
            detail = err.read().decode()[:2000]
            return [TextContent(type="text", text=f"HTTP {err.code}: {detail}")]
        state_file.append_hook_log(
            state_path, {"gateway": name, "response_keys": list(answer.keys())}
        )
        return [TextContent(type="text", text=json.dumps(answer))]

    return server


async def serve(state_path: Path, provider_name: str) -> None:
    server = build_server(state_path, provider_name=provider_name)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--provider", default=os.environ.get("GIT_PROVIDER", "bitbucket_cloud"))
    args = parser.parse_args()
    asyncio.run(serve(args.state.resolve(), args.provider))


if __name__ == "__main__":
    main()
