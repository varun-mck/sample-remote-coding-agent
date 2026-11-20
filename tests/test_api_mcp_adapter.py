"""`mcp_adapter`: gateway-only entry that forwards tool calls to route Lambdas."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from handlers import mcp_adapter

_GW = "gw-abc123"
_ARN = "arn:aws:lambda:us-east-1:123456789012:function:TasksRoute"


class _FakePayload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _fake_lambda(status: int, body: dict[str, Any]) -> Any:
    class _Client:
        last_event: dict[str, Any] | None = None

        def invoke(self, **kwargs: Any) -> dict[str, Any]:
            _Client.last_event = json.loads(kwargs["Payload"])
            return {
                "Payload": _FakePayload(
                    json.dumps({"statusCode": status, "body": json.dumps(body)}).encode()
                )
            }

    return _Client()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_ID", _GW)
    monkeypatch.setenv("ROUTE_TARGETS", json.dumps({"POST /tasks": _ARN}))


def _context(*, gateway_id: str = _GW, tool: str = "rca-tasks___submit_task") -> Any:
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreGatewayId": gateway_id,
                "bedrockAgentCoreToolName": tool,
                "bedrockAgentCoreAwsRequestId": "req-1",
            }
        )
    )


def test_wrong_gateway_is_refused() -> None:
    out = mcp_adapter.lambda_handler({}, _context(gateway_id="other"))
    assert out["statusCode"] == 403


def test_missing_identity_is_refused() -> None:
    out = mcp_adapter.lambda_handler({"repositoryUrl": "x"}, _context())
    assert out["statusCode"] == 403


def test_submit_task_forwards_post_with_gateway_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _fake_lambda(201, {"taskId": "tsk_x"})
    monkeypatch.setattr(mcp_adapter.router, "_targets", lambda: {"POST /tasks": _ARN})
    monkeypatch.setattr(mcp_adapter.router, "_lambda", lambda: client)
    out = mcp_adapter.lambda_handler(
        {
            "repositoryUrl": "https://github.com/o/r",
            "branch": "feat/x",
            "prompt": "fix",
            "submittedBy": "dev@example.com",
        },
        _context(),
    )
    assert out["taskId"] == "tsk_x"
    event = client.last_event
    assert event is not None
    assert event["routeKey"] == "POST /tasks"
    assert event["requestContext"]["authorizer"]["gateway"]["submittedBy"] == "dev@example.com"
    assert json.loads(event["body"])["prompt"] == "fix"


def test_get_task_maps_path_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _fake_lambda(200, {"taskId": "tsk_y"})
    monkeypatch.setattr(mcp_adapter.router, "_targets", lambda: {"GET /tasks/{taskId}": _ARN})
    monkeypatch.setattr(mcp_adapter.router, "_lambda", lambda: client)
    out = mcp_adapter.lambda_handler(
        {"taskId": "tsk_y", "submittedBy": "dev@example.com"},
        _context(tool="rca-tasks___get_task"),
    )
    assert out["taskId"] == "tsk_y"
    assert client.last_event["pathParameters"] == {"taskId": "tsk_y"}
