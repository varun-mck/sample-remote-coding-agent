"""Guardrail screening on queued runs (no cards)."""

from __future__ import annotations

from typing import Any

import pytest
from test_api_fakes import load_handlers as _load

_RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/fleet-ABC"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_ARN", _RUNTIME_ARN)


def test_a_deploy_without_a_guardrail_screens_nothing(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUARDRAIL_ID", "")
    monkeypatch.setenv("GUARDRAIL_VERSION", "")
    _common, runs = _load("runs")
    task = runs.queue_run(repository_url="https://github.com/o/r", prompt="hello")
    assert task["prompt"] == "hello"


def test_guardrail_refusal_blocks_queue(aws: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_api_fakes import FakeGuardrail, blocked_by, install

    monkeypatch.setenv("GUARDRAIL_ID", FakeGuardrail.ARN)
    monkeypatch.setenv("GUARDRAIL_VERSION", "1")
    _common, runs = _load("runs")
    install(monkeypatch, _common, guardrail=FakeGuardrail(blocked=blocked_by("topic", "topics")))
    with pytest.raises(_common.ApiError) as raised:
        runs.queue_run(repository_url="https://github.com/o/r", prompt="blocked")
    assert raised.value.status == 422
