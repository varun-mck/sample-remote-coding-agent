"""MCP tools for submitting and tracking remote AIPDLC tasks."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from . import format as fmt
from .client import ApiClient, RcaError

LOG = logging.getLogger("rca_mcp.server")
SERVER_NAME = "remote-coding-agent"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[ApiClient, Mapping[str, Any]], str]


def _required(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required and must be a non-empty string")
    return value.strip()


def submit_task(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    body = {
        "repositoryUrl": _required(arguments, "repositoryUrl"),
        "prompt": _required(arguments, "prompt"),
    }
    for optional in ("verifyCommand", "setupCommand", "defaultBranch"):
        value = arguments.get(optional)
        if isinstance(value, str) and value.strip():
            body[optional] = value.strip()
    return fmt.task_created(client.post("/tasks", body))


def get_task(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    task_id = _required(arguments, "taskId")
    return fmt.task(client.get(f"/tasks/{task_id}"))


def list_tasks(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    return fmt.tasks(
        client.get("/tasks", status=arguments.get("status"), limit=arguments.get("limit"))
    )


def cancel_task(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    task_id = _required(arguments, "taskId")
    return fmt.task(client.post(f"/tasks/{task_id}/cancel", {}))


_LIMIT_SCHEMA = {
    "type": "integer",
    "minimum": 1,
    "maximum": 100,
    "description": "How many rows to ask for. The control plane caps this at 100.",
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="submit_task",
        description="Submit a scoped coding task against a Git repository URL.",
        input_schema={
            "type": "object",
            "properties": {
                "repositoryUrl": {"type": "string"},
                "prompt": {"type": "string"},
                "verifyCommand": {"type": "string"},
                "setupCommand": {"type": "string"},
                "defaultBranch": {"type": "string"},
            },
            "required": ["repositoryUrl", "prompt"],
            "additionalProperties": False,
        },
        run=submit_task,
    ),
    ToolSpec(
        name="get_task",
        description="Fetch one task by id.",
        input_schema={
            "type": "object",
            "properties": {"taskId": {"type": "string"}},
            "required": ["taskId"],
            "additionalProperties": False,
        },
        run=get_task,
    ),
    ToolSpec(
        name="list_tasks",
        description="List recent tasks.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "limit": _LIMIT_SCHEMA,
            },
            "additionalProperties": False,
        },
        run=list_tasks,
    ),
    ToolSpec(
        name="cancel_task",
        description="Cancel a running task.",
        input_schema={
            "type": "object",
            "properties": {"taskId": {"type": "string"}},
            "required": ["taskId"],
            "additionalProperties": False,
        },
        run=cancel_task,
    ),
)


def _endpoint_line(api: ApiClient) -> str:
    """The `endpoint used:` suffix for one answer, or `""` when there is nothing to report."""
    report = getattr(api, "provenance", None)
    return fmt.endpoint_provenance(*report()) if report is not None else ""


def call_tool(client: ApiClient, name: str, arguments: Mapping[str, Any]) -> str:
    for spec in TOOLS:
        if spec.name == name:
            try:
                answer = spec.run(client, arguments)
            except (RcaError, ValueError) as err:
                answer = f"Error: {err}"
            provenance = _endpoint_line(client)
            return f"{answer}\n{provenance}" if provenance else answer
    raise ValueError(f"unknown tool {name!r}")


def build_server(client: ApiClient) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=spec.name, description=spec.description, inputSchema=spec.input_schema)
            for spec in TOOLS
        ]

    @server.call_tool()
    async def tool_handler(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            text = call_tool(client, name, arguments or {})
        except ValueError as exc:
            text = str(exc)
        return [TextContent(type="text", text=text)]

    return server


async def serve(client: ApiClient) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(client)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    client = ApiClient()
    asyncio.run(serve(client))


if __name__ == "__main__":
    main()
