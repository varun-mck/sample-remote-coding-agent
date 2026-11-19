"""Dispatch queued tasks to the fleet AgentCore runtime."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from . import _common, observability, runs
from ._common import LOG

FAILED = "FAILED"
_STATUS = {"#st": "status"}


def _message(record: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        parsed = json.loads(record.get("body") or "{}")
    except ValueError:
        LOG.exception("a dispatch message was not JSON; dropping it")
        return None
    return parsed if isinstance(parsed, dict) else None


def _task(task_id: str) -> dict[str, Any] | None:
    found = _common.tasks_table.get_item(Key={"taskId": task_id}).get("Item")
    if found is None:
        LOG.warning("dispatch for task %s: no such task, dropped", task_id)
    return found


def _claim(task: Mapping[str, Any]) -> bool:
    task_id = str(task["taskId"])
    try:
        _common.tasks_table.update_item(
            Key={"taskId": task_id},
            UpdateExpression="SET #st = :dispatched, dispatchedAt = :now, updatedAt = :now",
            ExpressionAttributeNames=dict(_STATUS),
            ExpressionAttributeValues={":dispatched": runs.DISPATCHED, ":now": _common.now_iso()},
            ConditionExpression=Attr("taskId").exists() & Attr("status").eq(runs.QUEUED),
        )
    except ClientError as err:
        if not _common.is_condition_failure(err):
            raise
        LOG.info("task %s was not QUEUED; this delivery invokes nothing", task_id)
        return False
    return True


def _release(task: Mapping[str, Any]) -> None:
    task_id = str(task["taskId"])
    try:
        _common.tasks_table.update_item(
            Key={"taskId": task_id},
            UpdateExpression="SET #st = :queued, updatedAt = :now",
            ExpressionAttributeNames=dict(_STATUS),
            ExpressionAttributeValues={":queued": runs.QUEUED, ":now": _common.now_iso()},
            ConditionExpression=Attr("taskId").exists() & Attr("status").eq(runs.DISPATCHED),
        )
    except ClientError:
        return


def _payload(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "taskId": str(task["taskId"]),
        "prompt": task.get("prompt", ""),
        "repositoryUrl": task.get("repositoryUrl", ""),
        "branch": task.get("branch", ""),
        "defaultBranch": task.get("defaultBranch", ""),
        "verifyCommand": task.get("verifyCommand", ""),
        "setupCommand": task.get("setupCommand", ""),
        "model": task.get("model", ""),
        "effort": task.get("effort", ""),
        "sessionId": str(task.get("sessionId") or observability.session_id(str(task["taskId"]))),
    }


def _invoke(task: Mapping[str, Any]) -> None:
    runtime_arn = str(task.get("agentRuntimeArn") or os.environ.get("AGENT_RUNTIME_ARN", ""))
    session = observability.session_id(str(task["taskId"]))
    _common.agentcore_data().invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier=observability.ENDPOINT_NAME,
        runtimeSessionId=session,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(_payload(task)).encode(),
    )


def lambda_handler(event: Mapping[str, Any], _context: object) -> dict[str, Any]:
    for record in event.get("Records") or []:
        message = _message(record)
        if message is None:
            continue
        task_id = str(message.get("taskId") or "")
        if not task_id:
            continue
        task = _task(task_id)
        if task is None:
            continue
        if runs.is_terminal(str(task.get("status", ""))):
            continue
        if not _claim(task):
            continue
        try:
            _invoke(task)
        except Exception:
            _release(task)
            raise
    return {"ok": True}


def dlq_handler(event: Mapping[str, Any], _context: object) -> dict[str, Any]:
    for record in event.get("Records") or []:
        message = _message(record)
        if message is None:
            continue
        task_id = str(message.get("taskId") or "")
        if not task_id:
            continue
        try:
            _common.tasks_table.update_item(
                Key={"taskId": task_id},
                UpdateExpression="SET #st = :failed, updatedAt = :now, statusReason = :reason",
                ExpressionAttributeNames=dict(_STATUS),
                ExpressionAttributeValues={
                    ":failed": FAILED,
                    ":now": _common.now_iso(),
                    ":reason": "dispatch failed after retries",
                },
            )
        except ClientError:
            LOG.exception("could not mark task %s FAILED", task_id)
    return {"ok": True}
