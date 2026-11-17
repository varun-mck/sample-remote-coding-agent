"""`tasks.py`: create, list, get and cancel."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from conftest import api_event
from test_api_fakes import FakeAgentCore, install
from test_api_fakes import load_handlers as _load

_RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/fleet-ABC"


def _body(out: dict[str, Any]) -> dict[str, Any]:
    return json.loads(out["body"])


@pytest.fixture(autouse=True)
def _env(aws: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_ARN", _RUNTIME_ARN)
    monkeypatch.setenv("GUARDRAIL_ID", "")
    monkeypatch.setenv("GUARDRAIL_VERSION", "")


def _create_event(**body: Any) -> dict[str, Any]:
    return api_event("POST", "/tasks", body=body)


def test_create_queues_a_task(aws: Any) -> None:
    common, tasks = _load("tasks")
    out = tasks.create(_create_event(repositoryUrl="https://github.com/o/r", prompt="fix it"))
    item = _body(out)
    assert out["statusCode"] == 201
    stored = common.tasks_table.get_item(Key={"taskId": item["taskId"]})["Item"]
    assert stored["status"] == "QUEUED"
    assert stored["configId"] == "__fleet__"
    messages = aws.sqs.receive_message(QueueUrl=os.environ["TASK_QUEUE_URL"])
    bodies = [json.loads(m["Body"]) for m in messages.get("Messages", [])]
    assert bodies == [{"taskId": item["taskId"]}]


def test_create_refuses_missing_fields(aws: Any) -> None:
    _common, tasks = _load("tasks")
    out = tasks.create(_create_event(prompt="only prompt"))
    assert out["statusCode"] == 400


def test_get_returns_one_task(aws: Any) -> None:
    common, tasks = _load("tasks")
    common.tasks_table.put_item(
        Item={
            "taskId": "tsk_test_abc",
            "status": "QUEUED",
            "prompt": "p",
            "repositoryUrl": "https://github.com/o/r",
        }
    )
    out = tasks.get(
        api_event("GET", "/tasks/{taskId}", path_parameters={"taskId": "tsk_test_abc"}),
        None,
    )
    assert _body(out)["taskId"] == "tsk_test_abc"


def test_get_404s_when_missing(aws: Any) -> None:
    _common, tasks = _load("tasks")
    out = tasks.get(api_event("GET", "/tasks/{taskId}", path_parameters={"taskId": "tsk_nope"}))
    assert out["statusCode"] == 404


def test_list_returns_items(aws: Any) -> None:
    common, tasks = _load("tasks")
    common.tasks_table.put_item(
        Item={"taskId": "tsk_a", "status": "SUCCEEDED", "prompt": "one", "repositoryUrl": "u"}
    )
    out = tasks.list_(api_event("GET", "/tasks"))
    body = _body(out)
    assert len(body["items"]) >= 1


def test_cancel_marks_cancelled_and_stops_session(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    common, tasks = _load("tasks")
    fake = FakeAgentCore("bedrock-agentcore")
    install(monkeypatch, common, data=fake)
    common.tasks_table.put_item(
        Item={
            "taskId": "tsk_live",
            "status": "RUNNING",
            "prompt": "p",
            "agentRuntimeArn": _RUNTIME_ARN,
            "sessionId": "x" * 40,
        }
    )
    out = tasks.cancel(
        api_event("POST", "/tasks/{taskId}/cancel", path_parameters={"taskId": "tsk_live"}),
    )
    assert _body(out)["status"] == "CANCELLED"
    assert fake.calls_to("StopRuntimeSession")


def test_cancel_refuses_terminal(aws: Any) -> None:
    common, tasks = _load("tasks")
    common.tasks_table.put_item(Item={"taskId": "tsk_done", "status": "SUCCEEDED", "prompt": "p"})
    out = tasks.cancel(
        api_event("POST", "/tasks/{taskId}/cancel", path_parameters={"taskId": "tsk_done"}),
    )
    assert out["statusCode"] == 200
    assert _body(out)["status"] == "SUCCEEDED"
