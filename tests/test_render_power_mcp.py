"""`scripts/render_power_mcp.py` turns task-gateway outputs into `mcp.json`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from render_power_mcp import build_mcp_config, main, stack_outputs

_PREFIX = "SampleCodingAgent"


def test_build_mcp_config_appends_mcp_path() -> None:
    cfg = build_mcp_config(
        gateway_url="https://gw.example/mcp",
        client_id="cid",
        oauth_scope="rca/tasks.submit",
    )
    assert cfg["mcpServers"]["rca-fleet"]["url"] == "https://gw.example/mcp"
    assert cfg["mcpServers"]["rca-fleet"]["oauthScopes"] == ["openid", "email", "rca/tasks.submit"]


def test_main_writes_from_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    outputs = tmp_path / "out.json"
    outputs.write_text(
        json.dumps(
            {
                f"{_PREFIX}TaskGateway": {
                    "TaskGatewayUrl": "https://abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
                    "CognitoClientId": "client-1",
                    "OAuthScope": "rca/tasks.submit",
                }
            }
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "mcp.json"
    assert (
        main(
            [
                "--outputs",
                str(outputs),
                "--out",
                str(dest),
                "--stack-prefix",
                _PREFIX,
            ]
        )
        == 0
    )
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["mcpServers"]["rca-fleet"]["oauth"]["clientId"] == "client-1"


def test_stack_outputs_reads_expected_keys(tmp_path: Path) -> None:
    path = tmp_path / "o.json"
    path.write_text(
        json.dumps(
            {
                f"{_PREFIX}TaskGateway": {
                    "TaskGatewayUrl": "u",
                    "CognitoClientId": "c",
                    "OAuthScope": "rca/tasks.submit",
                }
            }
        ),
        encoding="utf-8",
    )
    assert stack_outputs(path, _PREFIX)["OAuthScope"] == "rca/tasks.submit"
