"""Task HTTP routes."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any

from . import _common, observability, runs
from ._common import ApiError, handler

_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100


def _task(task_id: str) -> dict[str, Any]:
    found = _common.tasks_table.get_item(Key={"taskId": task_id}).get("Item")
    if not found:
        raise ApiError(404, "NotFound", "no task with that id", taskId=task_id)
    return found


@handler
def create(event: Mapping[str, Any]) -> tuple[int, Any]:
    body = _common.parse_body(event)
    repository_url = str(body.get("repositoryUrl") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    if not repository_url or not prompt:
        raise ApiError(400, "InvalidBody", "repositoryUrl and prompt are required")
    item = runs.queue_run(
        repository_url=repository_url,
        prompt=prompt,
        base_branch=str(body.get("baseBranch") or body.get("defaultBranch") or "").strip(),
        verify_command=str(body.get("verifyCommand") or "").strip(),
        setup_command=str(body.get("setupCommand") or "").strip(),
    )
    return 201, item


@handler
def get(event: Mapping[str, Any]) -> tuple[int, Any]:
    task_id = _common.path_param(event, "taskId")
    return 200, _task(task_id)


@handler
def list_(event: Mapping[str, Any]) -> tuple[int, Any]:
    limit = _common.int_query_param(event, "limit", default=_DEFAULT_LIMIT, maximum=_MAX_LIMIT)
    status = _common.query_param(event, "status")
    scan_kwargs: dict[str, Any] = {"Limit": limit}
    if status:
        from boto3.dynamodb.conditions import Attr

        scan_kwargs["FilterExpression"] = Attr("status").eq(status)
    page = _common.tasks_table.scan(**scan_kwargs)
    items = page.get("Items") or []
    return 200, {"items": items, "nextCursor": None}


@handler
def cancel(event: Mapping[str, Any]) -> tuple[int, Any]:
    task_id = _common.path_param(event, "taskId")
    task = _task(task_id)
    if runs.is_terminal(str(task.get("status", ""))):
        return 200, task
    now = _common.now_iso()
    _common.tasks_table.update_item(
        Key={"taskId": task_id},
        UpdateExpression="SET #st = :cancelled, cancelledAt = :now, updatedAt = :now",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":cancelled": "CANCELLED", ":now": now},
    )
    with contextlib.suppress(Exception):
        _common.agentcore_data().stop_runtime_session(
            agentRuntimeArn=str(task.get("agentRuntimeArn") or ""),
            qualifier=observability.ENDPOINT_NAME,
            runtimeSessionId=str(task.get("sessionId") or observability.session_id(task_id)),
        )
    return 200, _task(task_id)


ROUTES = {
    ("POST", "/tasks"): create,
    ("GET", "/tasks"): list_,
    ("GET", "/tasks/{taskId}"): get,
    ("POST", "/tasks/{taskId}/cancel"): cancel,
}
