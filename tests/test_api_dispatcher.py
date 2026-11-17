"""`dispatcher.py`: SQS consumer."""

from __future__ import annotations

import json
from typing import Any

import invoke_payload
import pytest
from test_api_fakes import FakeAgentCore, install
from test_api_fakes import load_handlers as _load

TASK_ID = "tsk_1700000000000_abcdef"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/fleet-ABC"
SEEDED_AT = "2026-01-01T00:00:00.000Z"


def _seed_task(common: Any, *, status: str = "QUEUED") -> None:
    common.tasks_table.put_item(
        Item={
            "taskId": TASK_ID,
            "configId": "__fleet__",
            "prompt": "add a healthcheck",
            "status": status,
            "repositoryUrl": "https://github.com/owner/repo",
            "agentRuntimeArn": RUNTIME_ARN,
            "sessionId": f"{TASK_ID}-seeded",
            "createdAt": SEEDED_AT,
            "updatedAt": SEEDED_AT,
        }
    )


def _record(task_id: str = TASK_ID) -> dict[str, Any]:
    return {
        "messageId": "1",
        "eventSource": "aws:sqs",
        "body": json.dumps({"taskId": task_id}),
    }


def test_dispatch_claims_and_invokes(aws: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    common, dispatcher = _load("dispatcher")
    fake = FakeAgentCore("bedrock-agentcore")
    install(monkeypatch, common, data=fake)
    _seed_task(common)
    dispatcher.lambda_handler({"Records": [_record()]}, None)
    item = common.tasks_table.get_item(Key={"taskId": TASK_ID})["Item"]
    assert item["status"] == "DISPATCHED"
    invoke = fake.calls_to("InvokeAgentRuntime")
    assert invoke
    payload = json.loads(invoke[0]["payload"].decode())
    assert payload["taskId"] == TASK_ID
    for field in invoke_payload.REQUIRED:
        assert field in payload


def test_dispatch_drops_unknown_task(aws: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    common, dispatcher = _load("dispatcher")
    fake = FakeAgentCore("bedrock-agentcore")
    install(monkeypatch, common, data=fake)
    dispatcher.lambda_handler({"Records": [_record("tsk_missing")]}, None)
    assert not fake.calls_to("InvokeAgentRuntime")


def test_dlq_marks_failed(aws: Any) -> None:
    common, dispatcher = _load("dispatcher")
    _seed_task(common, status="DISPATCHED")
    dispatcher.dlq_handler({"Records": [_record()]}, None)
    item = common.tasks_table.get_item(Key={"taskId": TASK_ID})["Item"]
    assert item["status"] == "FAILED"
