"""DynamoDB task status writes for the harness."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .state import TaskState, TaskStatus

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {key: _ddb_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_ddb_safe(item) for item in value]
    return value


def _table() -> Any | None:
    name = os.environ.get("TASKS_TABLE", "").strip()
    if not name:
        return None
    return boto3.resource("dynamodb").Table(name)


def _key(state: TaskState) -> dict[str, str] | None:
    task_id = state.get("taskId")
    if not task_id:
        log.warning("no taskId on state; skipping the write")
        return None
    return {"taskId": str(task_id)}


def set_status(
    state: TaskState,
    status: TaskStatus,
    *,
    step: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    table = _table()
    if table is None:
        return
    key = _key(state)
    if key is None:
        return
    names = {"#status": "status"}
    values: dict[str, Any] = {":s": status, ":now": _now()}
    sets = ["#status = :s", "updatedAt = :now"]
    if step:
        sets.append("steps = list_append(if_not_exists(steps, :empty), :step)")
        values[":empty"] = []
        values[":step"] = [step]
    for index, (name, value) in enumerate(sorted((extra or {}).items())):
        sets.append(f"#e{index} = :e{index}")
        names[f"#e{index}"] = name
        values[f":e{index}"] = _ddb_safe(value)
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except (ClientError, BotoCoreError) as exc:
        log.warning("could not write status %s for task %s: %s", status, key["taskId"], exc)


def record_result(
    state: TaskState,
    *,
    status: TaskStatus,
    pr_url: str | None = None,
    verify_result: Mapping[str, Any] | None = None,
) -> None:
    extra: dict[str, Any] = {}
    if pr_url:
        extra["prUrl"] = pr_url
    if verify_result is not None:
        extra["verifyResult"] = dict(verify_result)
    set_status(state, status, extra=extra)
