"""`TaskGatewayStack`: Cognito JWT authoriser and Lambda MCP target for task tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import aws_cdk
import pytest
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Template
from stacks.task_gateway_stack import TARGET_NAME, TaskGatewayStack

_ROUTE_ARNS = {
    "POST /tasks": "arn:aws:lambda:ap-southeast-1:123456789012:function:Create",
    "GET /tasks": "arn:aws:lambda:ap-southeast-1:123456789012:function:List",
    "GET /tasks/{taskId}": "arn:aws:lambda:ap-southeast-1:123456789012:function:Get",
    "POST /tasks/{taskId}/cancel": "arn:aws:lambda:ap-southeast-1:123456789012:function:Cancel",
}


@pytest.fixture
def template(tmp_path: Path) -> Template:
    with patch(
        "stacks.task_gateway_stack.api_code",
        return_value=lambda_.Code.from_inline("def handler(event,context): pass"),
    ):
        app = aws_cdk.App(outdir=str(tmp_path))
        stack = TaskGatewayStack(
            app,
            "TaskGw",
            app_name="rca",
            route_arns=_ROUTE_ARNS,
            env=aws_cdk.Environment(account="123456789012", region="ap-southeast-1"),
        )
        return Template.from_stack(stack)


def test_the_gateway_uses_cognito_jwt_and_passes_headers(template: Template) -> None:
    template.has_resource_properties(
        "AWS::BedrockAgentCore::Gateway",
        {
            "AuthorizerType": "CUSTOM_JWT",
            "InterceptorConfigurations": [
                {
                    "InterceptionPoints": ["REQUEST", "RESPONSE"],
                    "InputConfiguration": {"PassRequestHeaders": True},
                }
            ],
        },
    )


def test_the_tasks_target_exposes_four_tools(template: Template) -> None:
    template.has_resource_properties(
        "AWS::BedrockAgentCore::GatewayTarget",
        {
            "Name": TARGET_NAME,
            "CredentialProviderConfigurations": [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
        },
    )


def test_no_saml_identity_provider(template: Template) -> None:
    assert len(template.find_resources("AWS::Cognito::UserPoolIdentityProvider")) == 0
