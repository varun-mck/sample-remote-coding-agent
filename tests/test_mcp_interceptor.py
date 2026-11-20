"""`mcp_interceptor`: stamp `submittedBy` on REQUEST, hide it on `tools/list` RESPONSE."""

from __future__ import annotations

import base64
import json

import pytest
from handlers import mcp_interceptor

_CLIENT = "kiro-client-id"


def _bearer(payload: dict[str, object]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"Bearer x.{body}.y"


def test_pool_user_accepts_cognito_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIRO_CLIENT_ID", _CLIENT)
    claims = {
        "token_use": "access",
        "client_id": _CLIENT,
        "username": "dev@example.com",
    }
    assert mcp_interceptor.pool_user(claims, client_id=_CLIENT) == "dev@example.com"


def test_pool_user_refuses_wrong_client() -> None:
    claims = {"token_use": "access", "client_id": "other", "username": "a@b.com"}
    assert mcp_interceptor.pool_user(claims, client_id=_CLIENT) == ""


def test_request_overwrites_submitted_by(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIRO_CLIENT_ID", _CLIENT)
    event = {
        "mcp": {
            "gatewayRequest": {
                "headers": {
                    "Authorization": _bearer(
                        {
                            "token_use": "access",
                            "client_id": _CLIENT,
                            "username": "ops@corp.com",
                        }
                    )
                },
                "body": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "submit_task",
                        "arguments": {"submittedBy": "evil", "prompt": "p"},
                    },
                },
            }
        }
    }
    out = mcp_interceptor.lambda_handler(event)
    args = out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]
    assert args["submittedBy"] == "ops@corp.com"
    assert args["prompt"] == "p"


def test_request_with_null_gateway_response_passes_through() -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    event = {"mcp": {"gatewayRequest": {"body": body}, "gatewayResponse": None}}
    out = mcp_interceptor.lambda_handler(event)
    assert "transformedGatewayResponse" not in out["mcp"]
    assert out["mcp"]["transformedGatewayRequest"]["body"] == body


def test_response_passes_initialize_result_through() -> None:
    result = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}
    event = {
        "mcp": {
            "gatewayRequest": {"body": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
            "gatewayResponse": {"statusCode": 200, "body": result},
        }
    }
    out = mcp_interceptor.lambda_handler(event)
    assert out["mcp"]["transformedGatewayResponse"] == {"statusCode": 200, "body": result}


def test_response_strips_submitted_by_from_tool_schemas() -> None:
    tools = {
        "tools": [
            {
                "name": "submit_task",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "submittedBy": {"type": "string"},
                    },
                    "required": ["prompt", "submittedBy"],
                },
            }
        ]
    }
    stripped = mcp_interceptor.hide_identity_field(tools)
    props = stripped["tools"][0]["inputSchema"]["properties"]
    assert "submittedBy" not in props
    assert stripped["tools"][0]["inputSchema"]["required"] == ["prompt"]
