"""AgentCore Gateway MCP bridge and Kiro profile wiring."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import httpx
import pytest
from coding_agent import store
from coding_agent.kiro_profile import write_agent_profile
from coding_agent.kiro_runner import parse_stream_json
from coding_agent.mcp import agentcore_gateway_bridge as bridge
from mcp.types import CallToolResult, TextContent


def test_summarize_tool_result_redacts_tokens_and_truncates() -> None:
    long = "x" * 900
    result = CallToolResult(
        content=[TextContent(type="text", text=f"failed ghp_abc1234567890 {long}")],
        isError=True,
    )
    text = bridge.summarize_tool_result(result)
    assert "ghp_" not in text
    assert "***" in text
    assert text.endswith("…")


def test_summarize_tool_result_empty() -> None:
    assert bridge.summarize_tool_result(CallToolResult(content=[], isError=True)) == (
        "(empty tool result)"
    )


def test_gateway_mcp_url_appends_mcp_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_AGENTCORE_GATEWAY_URL", "https://example.gateway.aws")
    assert bridge.gateway_mcp_url() == "https://example.gateway.aws/mcp"


def test_sigv4_auth_adds_authorization_header() -> None:
    frozen = mock.Mock(access_key="AKIA", secret_key="secret", token="token")
    request = httpx.Request("POST", "https://gw.example/mcp", content=b"{}")
    with mock.patch("boto3.Session") as session_cls:
        session_cls.return_value.get_credentials.return_value.get_frozen_credentials.return_value = frozen
        auth = bridge._SigV4Auth(region="us-east-1")

        def _sign(aws_request: object) -> None:
            aws_request.headers["Authorization"] = "AWS4-HMAC-SHA256 signed"

        with mock.patch("botocore.auth.SigV4Auth.add_auth", side_effect=_sign):
            signed = next(auth.auth_flow(request))
    assert signed.headers.get("Authorization") == "AWS4-HMAC-SHA256 signed"


def test_profile_uses_only_agentcore_gateway_mcp(tmp_path: Path) -> None:
    kiro_home = tmp_path / "kiro"
    path = write_agent_profile(kiro_home)
    doc = json.loads(path.read_text())
    assert doc.get("includeMcpJson") is False
    assert "hooks" not in doc
    servers = doc.get("mcpServers") or {}
    assert set(servers) == {"agentcore-gateway"}
    assert "agentcore_gateway_bridge" in servers["agentcore-gateway"]["args"][1]
    tools = doc.get("tools") or []
    assert "shell" in tools
    assert "@harness" not in str(tools)


def test_stream_parser_run_finished() -> None:
    lines = [
        json.dumps({"type": "runFinished", "success": True, "result": "Done."}),
    ]
    result = parse_stream_json(lines)
    assert result.success
    assert result.final_text == "Done."


def test_truncate_result_respects_byte_limit() -> None:
    text = "x" * 70_000
    out = store.truncate_result(text)
    assert len(out.encode("utf-8")) <= 60_050


def test_execution_boundary_may_invoke_gateway() -> None:
    import execution_boundary
    from stacks import agent_stack

    gateway_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/abc"
    statements = agent_stack.execution_role_boundary_statements(
        account=execution_boundary.ACCOUNT,
        region=execution_boundary.REGION,
        asset_repository_arn="arn:aws:ecr:us-east-1:123456789012:repository/x",
        gateway_arn=gateway_arn,
    )
    invoke = [s.to_json() for s in statements if s.to_json().get("Sid") == "MayInvokeGateway"]
    assert len(invoke) == 1
    action = invoke[0]["Action"]
    if isinstance(action, str):
        action = [action]
    assert action == ["bedrock-agentcore:InvokeGateway"]
    resource = invoke[0]["Resource"]
    if isinstance(resource, str):
        resource = [resource]
    assert resource == ["arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/abc"]
