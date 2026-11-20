"""The task gateway's Lambda target: one MCP tool call in, one route Lambda invoked.

The request interceptor overwrites `submittedBy` from the verified Cognito access token before
this function runs. This module checks `bedrockAgentCoreGatewayId` and the presence of
`submittedBy`, then builds the same forwarded event the function URL router would use.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from . import router
from ._common import LOG

_TOOL_DELIMITER = "___"

TOOL_ROUTES: dict[str, tuple[str, str]] = {
    "submit_task": ("POST /tasks", "/tasks"),
    "list_tasks": ("GET /tasks", "/tasks"),
    "get_task": ("GET /tasks/{taskId}", "/tasks/{taskId}"),
    "cancel_task": ("POST /tasks/{taskId}/cancel", "/tasks/{taskId}/cancel"),
}
_SUBMIT_FIELDS = (
    "repositoryUrl",
    "branch",
    "prompt",
    "baseBranch",
    "verifyCommand",
    "setupCommand",
    "model",
    "effort",
)


class ToolError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    @property
    def body(self) -> dict[str, Any]:
        return {"statusCode": self.status, "error": self.code, "message": self.message}


def _custom(context: Any) -> Mapping[str, Any]:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None)
    return custom if isinstance(custom, Mapping) else {}


def tool_name(qualified: str) -> str:
    _target, delimiter, name = qualified.partition(_TOOL_DELIMITER)
    return name if delimiter else qualified


def _text(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    return value.strip() if isinstance(value, str) else ""


def build_route_event(
    tool: str, arguments: Mapping[str, Any], *, submitted_by: str, request_id: str
) -> tuple[str, dict[str, Any]]:
    if tool not in TOOL_ROUTES:
        raise ToolError(404, "UnknownTool", f"no tool named {tool!r}")
    route_key, template = TOOL_ROUTES[tool]
    method = route_key.split(" ", 1)[0]
    event: dict[str, Any] = {"routeKey": route_key}

    path = template
    if "{taskId}" in template:
        task_id = _text(arguments, "taskId")
        if not task_id:
            raise ToolError(400, "MissingField", "taskId is required")
        event["pathParameters"] = {"taskId": task_id}
        path = template.replace("{taskId}", task_id)

    if tool == "submit_task":
        body = {name: _text(arguments, name) for name in _SUBMIT_FIELDS}
        event["body"] = json.dumps({name: value for name, value in body.items() if value})
    elif tool == "list_tasks":
        query = {"status": _text(arguments, "status")}
        limit = arguments.get("limit")
        if isinstance(limit, float) and limit.is_integer():
            limit = int(limit)
        if isinstance(limit, int | str) and str(limit).strip():
            query["limit"] = str(limit).strip()
        event["queryStringParameters"] = {key: value for key, value in query.items() if value}
    elif method == "POST":
        event["body"] = "{}"

    event["rawPath"] = path
    event["requestContext"] = {
        "http": {"method": method, "path": path},
        "requestId": request_id,
        "authorizer": {"gateway": {"submittedBy": submitted_by}},
    }
    return route_key, event


def _invoke(route_key: str, event: Mapping[str, Any]) -> dict[str, Any]:
    targets = router._targets()
    result = router._lambda().invoke(
        FunctionName=targets[route_key], Payload=json.dumps(event).encode()
    )
    payload = result["Payload"].read()
    if result.get("FunctionError"):
        LOG.error("mcp %s: route failed (%s)", route_key, result["FunctionError"])
        raise ToolError(500, "InternalError", "unexpected error")
    response = json.loads(payload)
    if not isinstance(response, dict):
        raise ToolError(500, "InternalError", "unexpected error")
    return response


def lambda_handler(event: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
    custom = _custom(context)
    expected_gateway = os.environ.get("GATEWAY_ID", "").strip()
    gateway_id = str(custom.get("bedrockAgentCoreGatewayId") or "")
    if not expected_gateway or gateway_id != expected_gateway:
        return {
            "statusCode": 403,
            "error": "Forbidden",
            "message": "not invoked by this deployment's task gateway",
        }

    arguments = dict(event) if isinstance(event, Mapping) else {}
    submitted_by = _text(arguments, "submittedBy")
    if not submitted_by:
        return {
            "statusCode": 403,
            "error": "Forbidden",
            "message": "the task gateway did not attach the caller's identity",
        }

    tool = tool_name(str(custom.get("bedrockAgentCoreToolName") or ""))
    request_id = str(custom.get("bedrockAgentCoreAwsRequestId") or "")
    LOG.info("mcp %s arg_keys=%s request=%s", tool, sorted(arguments), request_id)
    try:
        route_key, forwarded = build_route_event(
            tool, arguments, submitted_by=submitted_by, request_id=request_id
        )
        response = _invoke(route_key, forwarded)
    except ToolError as err:
        return err.body
    status = int(response.get("statusCode") or 500)
    try:
        body = json.loads(response.get("body") or "{}")
    except ValueError:
        body = {"error": "InternalError", "message": "unexpected error"}
    if status >= 400:
        return {"statusCode": status, **(body if isinstance(body, dict) else {})}
    return body if isinstance(body, dict) else {"result": body}
