"""`runs.py`: guardrail screening and queueing."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from test_api_fakes import load_handlers as _load

_RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/fleet-ABC"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_ARN", _RUNTIME_ARN)
    monkeypatch.setenv("GUARDRAIL_ID", "")
    monkeypatch.setenv("GUARDRAIL_VERSION", "")


def _messages(aws: Any) -> list[dict[str, Any]]:
    received = aws.sqs.receive_message(
        QueueUrl=os.environ["TASK_QUEUE_URL"], MaxNumberOfMessages=10
    )
    return [json.loads(message["Body"]) for message in received.get("Messages", [])]


def test_queue_run_writes_task_and_one_message(aws: Any) -> None:
    common, runs = _load("runs")
    task = runs.queue_run(
        repository_url="https://github.com/owner/repo",
        prompt="add a healthcheck",
    )
    stored = common.tasks_table.get_item(Key={"taskId": task["taskId"]})["Item"]
    assert stored["status"] == runs.QUEUED
    assert stored["agentRuntimeArn"] == _RUNTIME_ARN
    assert _messages(aws) == [{"taskId": task["taskId"]}]


def test_queue_run_refuses_bad_repository_url(aws: Any) -> None:
    common, runs = _load("runs")
    with pytest.raises(common.ApiError) as raised:
        runs.queue_run(repository_url="not-a-repo", prompt="p")
    assert raised.value.status == 400
    assert _messages(aws) == []


def test_screen_prompt_passes_when_no_guardrail(aws: Any) -> None:
    _common, runs = _load("runs")
    assert runs.screen_prompt("hello").text == "hello"
