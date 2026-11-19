"""`GatewayStack` synthesizes a gateway and Git/Atlassian OpenAPI targets."""

from __future__ import annotations

from pathlib import Path

import aws_cdk
import pytest
from aws_cdk.assertions import Template
from stacks.gateway_stack import GatewayStack


@pytest.fixture
def template(tmp_path: Path) -> Template:
    app = aws_cdk.App(outdir=str(tmp_path))
    stack = GatewayStack(
        app,
        "CodingAgentGateway",
        app_name="rca",
        env=aws_cdk.Environment(account="123456789012", region="ap-southeast-1"),
    )
    return Template.from_stack(stack)


def test_the_gateway_and_targets_are_present(template: Template) -> None:
    template.resource_count_is("AWS::BedrockAgentCore::Gateway", 1)
    template.resource_count_is("AWS::BedrockAgentCore::GatewayTarget", 5)


def test_the_gateway_url_is_exported(template: Template) -> None:
    assert "GatewayUrl" in template.to_json()["Outputs"]
