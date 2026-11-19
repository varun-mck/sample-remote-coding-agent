"""The four task tools, as data, and the stdio adapter that serves them.

`TOOLS` is a tuple of `ToolSpec`s whose `run` takes `(client, arguments)` and returns text.
Nothing in a tool knows about the transport, so tests drive them with a stub client and no
event loop.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from . import format as fmt
from .client import ApiClient, PowerError

LOG = logging.getLogger("kiro_rca_power.server")

SERVER_NAME = "rca-fleet"
_DATA_ENV = "RCA_POWER_DATA"

_OUTCOME_NOTE = (
    "SUCCEEDED means the agent's run finished, not that a pull request was opened or a "
    "branch pushed: the agent's own summary, including any PR or compare link, is in the "
    "result text."
)


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


def _optional(arguments: Mapping[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _task_path(arguments: Mapping[str, Any], suffix: str = "") -> str:
    task_id = urllib.parse.quote(_required(arguments, "taskId"), safe="")
    return f"/tasks/{task_id}{suffix}"


def submit_task(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    url = _required(arguments, "repositoryUrl")
    if not url.startswith(("https://", "http://", "git@")):
        raise ValueError(
            "repositoryUrl must be a full clone URL such as https://github.com/owner/repo, "
            f"not {url!r}"
        )
    body: dict[str, Any] = {
        "repositoryUrl": url,
        "branch": _required(arguments, "branch"),
        "prompt": _required(arguments, "prompt"),
    }
    for optional in ("baseBranch", "verifyCommand", "setupCommand"):
        value = _optional(arguments, optional)
        if value:
            body[optional] = value
    return "submitted\n" + fmt.task(client.post("/tasks", body))


def get_task(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    return fmt.task(client.get(_task_path(arguments)))


def list_tasks(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    return fmt.tasks(
        client.get("/tasks", status=_optional(arguments, "status"), limit=arguments.get("limit"))
    )


def cancel_task(client: ApiClient, arguments: Mapping[str, Any]) -> str:
    return fmt.task(client.post(_task_path(arguments, "/cancel"), {}))


_TASK_ID_SCHEMA = {
    "type": "string",
    "description": "The task id submit_task returned, such as tsk_1795091004790_70b66143.",
}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="submit_task",
        description=(
            "Start a remote coding agent task: the fleet clones the repository, checks out "
            "the branch, and runs a Kiro agent with the prompt. The branch must already exist "
            "on the remote. Returns the task id; follow it with get_task. " + _OUTCOME_NOTE
        ),
        input_schema={
            "type": "object",
            "properties": {
                "repositoryUrl": {
                    "type": "string",
                    "description": "Full clone URL, such as https://github.com/owner/repo.",
                },
                "branch": {
                    "type": "string",
                    "description": "Existing remote branch the agent checks out and works on.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "What the agent should do. Say 'create a PR' or 'push' only if the "
                        "user asked for it."
                    ),
                },
                "baseBranch": {
                    "type": "string",
                    "description": "Branch a pull request would target. Defaults to main.",
                },
                "verifyCommand": {
                    "type": "string",
                    "description": "Command that proves the change works, such as `make test`.",
                },
                "setupCommand": {
                    "type": "string",
                    "description": "Command that prepares the checkout, such as `make setup`.",
                },
            },
            "required": ["repositoryUrl", "branch", "prompt"],
            "additionalProperties": False,
        },
        run=submit_task,
    ),
    ToolSpec(
        name="get_task",
        description=(
            "Show one task: status, harness steps, duration, error and the agent's result "
            "text. QUEUED, DISPATCHED and RUNNING are in flight, so poll again (about every "
            "30 seconds); SUCCEEDED, FAILED and CANCELLED are final. " + _OUTCOME_NOTE
        ),
        input_schema={
            "type": "object",
            "properties": {"taskId": _TASK_ID_SCHEMA},
            "required": ["taskId"],
            "additionalProperties": False,
        },
        run=get_task,
    ),
    ToolSpec(
        name="list_tasks",
        description="List recent tasks, newest first, optionally filtered by status.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(fmt.TASK_STATUSES)},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "How many tasks to return; the API caps this at 100.",
                },
            },
            "additionalProperties": False,
        },
        run=list_tasks,
    ),
    ToolSpec(
        name="cancel_task",
        description=(
            "Stop a task that is still in flight. Only call this when the user asks to "
            "cancel. A task that already finished is returned unchanged."
        ),
        input_schema={
            "type": "object",
            "properties": {"taskId": _TASK_ID_SCHEMA},
            "required": ["taskId"],
            "additionalProperties": False,
        },
        run=cancel_task,
    ),
)

_BY_NAME = {spec.name: spec for spec in TOOLS}


def call_tool(client: ApiClient, name: str, arguments: Mapping[str, Any]) -> tuple[str, bool]:
    """`(text, is_error)` for one tool call. Expected failures become text the model can
    relay; anything else is logged with its traceback and summarised."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return f"unknown tool {name!r}", True
    try:
        return spec.run(client, arguments), False
    except (PowerError, ValueError) as exc:
        return str(exc), True
    except Exception as exc:
        LOG.exception("tool %s failed", name)
        return f"{name} failed unexpectedly: {type(exc).__name__}: {exc}", True


def build_server(client: ApiClient) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=spec.name, description=spec.description, inputSchema=spec.input_schema)
            for spec in TOOLS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        # Argument keys only: a prompt can carry anything the user pasted.
        LOG.info("call_tool %s arg_keys=%s", name, sorted((arguments or {}).keys()))
        text, is_error = await anyio.to_thread.run_sync(call_tool, client, name, arguments or {})
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)

    return server


def _configure_logging() -> None:
    # stderr only by default: stdout carries the MCP protocol.
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    data_dir = os.environ.get(_DATA_ENV, "").strip()
    if data_dir and not data_dir.startswith("${"):
        path = Path(data_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(path / "rca-power.log", encoding="utf-8"))
        except OSError:
            pass
    logging.basicConfig(
        level=os.environ.get("RCA_POWER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s rca-power %(message)s",
        handlers=handlers,
        force=True,
    )


async def _serve() -> None:
    server = build_server(ApiClient())
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    _configure_logging()
    anyio.run(_serve)


if __name__ == "__main__":
    main()
