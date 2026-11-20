"""`GatewayStack` synthesizes a gateway, OpenAPI targets, and Confluence MCP."""

from __future__ import annotations

from pathlib import Path

import aws_cdk
import pytest
from aws_cdk.assertions import Template
from stacks.gateway_stack import _ATLASSIAN_MCP_ENDPOINT, GatewayStack


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


def test_the_github_target_sends_the_pat_as_a_bearer_header(template: Template) -> None:
    """Without outbound auth the gateway answers every PR call with an internal error."""
    template.has_resource_properties(
        "AWS::BedrockAgentCore::GatewayTarget",
        {
            "Name": "github",
            "CredentialProviderConfigurations": [
                {
                    "CredentialProviderType": "API_KEY",
                    "CredentialProvider": {
                        "ApiKeyCredentialProvider": {
                            "ProviderArn": (
                                "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:"
                                "token-vault/default/apikeycredentialprovider/rca_github"
                            ),
                            "CredentialLocation": "HEADER",
                            "CredentialParameterName": "Authorization",
                            "CredentialPrefix": "Bearer",
                        }
                    },
                }
            ],
        },
    )


def test_the_confluence_target_uses_atlassian_mcp_with_bearer_api_key(
    template: Template,
) -> None:
    template.has_resource_properties(
        "AWS::BedrockAgentCore::GatewayTarget",
        {
            "Name": "confluence",
            "TargetConfiguration": {
                "Mcp": {
                    "McpServer": {
                        "Endpoint": _ATLASSIAN_MCP_ENDPOINT,
                        "ListingMode": "DEFAULT",
                    }
                }
            },
            "CredentialProviderConfigurations": [
                {
                    "CredentialProviderType": "API_KEY",
                    "CredentialProvider": {
                        "ApiKeyCredentialProvider": {
                            "ProviderArn": (
                                "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:"
                                "token-vault/default/apikeycredentialprovider/rca_atlassian"
                            ),
                            "CredentialLocation": "HEADER",
                            "CredentialParameterName": "Authorization",
                            "CredentialPrefix": "Bearer",
                        }
                    },
                }
            ],
        },
    )


def test_the_gateway_role_may_read_github_and_atlassian_api_keys(template: Template) -> None:
    policies = template.find_resources("AWS::IAM::Policy")
    actions: set[str] = set()
    key_resources: set[str] = set()
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = statement["Action"]
            actions.update([action] if isinstance(action, str) else action)
            resources = statement.get("Resource") or []
            if isinstance(resources, str):
                resources = [resources]
            for resource in resources:
                if "apikeycredentialprovider" in resource:
                    key_resources.add(resource)
    assert {
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetResourceApiKey",
        "secretsmanager:GetSecretValue",
    } <= actions
    assert {
        "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:"
        "token-vault/default/apikeycredentialprovider/rca_github",
        "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:"
        "token-vault/default/apikeycredentialprovider/rca_atlassian",
    } <= key_resources
