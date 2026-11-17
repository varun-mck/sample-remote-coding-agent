"""AgentCore Gateway: one gateway and OpenAPI-backed targets per Git host provider."""

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from constructs import Construct

# Minimal OpenAPI placeholders -- real provider definitions replace these at configure time.
_GITHUB_OPENAPI = """\
openapi: 3.0.3
info:
  title: GitHub REST API (placeholder)
  version: 0.0.1
servers:
  - url: https://api.github.com
paths:
  /repos/{owner}/{repo}/pulls:
    post:
      operationId: create_pull_request
      summary: Create a pull request
      parameters:
        - name: owner
          in: path
          required: true
          schema:
            type: string
        - name: repo
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
      responses:
        "201":
          description: Created
"""

_BITBUCKET_OPENAPI = """\
openapi: 3.0.3
info:
  title: Bitbucket Cloud API (placeholder)
  version: 0.0.1
servers:
  - url: https://api.bitbucket.org/2.0
paths:
  /repositories/{workspace}/{repo_slug}/pullrequests:
    post:
      operationId: create_pull_request
      summary: Create a pull request
      parameters:
        - name: workspace
          in: path
          required: true
          schema:
            type: string
        - name: repo_slug
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
      responses:
        "201":
          description: Created
"""


class GatewayStack(cdk.Stack):
    """Bedrock AgentCore Gateway with Git provider targets."""

    def __init__(
        self, scope: Construct, construct_id: str, *, app_name: str, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cdk.Tags.of(self).add("Application", app_name)

        gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=f"{app_name} AgentCore Gateway execution role",
        )
        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:InvokeGateway",
                    "bedrock-agentcore:SynchronizeGatewayTargets",
                ],
                resources=["*"],
            )
        )

        gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name=f"{app_name}-gateway",
            authorizer_type="AWS_IAM",
            role_arn=gateway_role.role_arn,
            description=f"{app_name} MCP gateway for Git provider tools",
        )

        for construct_id_suffix, target_name, schema in (
            ("GithubTarget", "github", _GITHUB_OPENAPI),
            ("BitbucketCloudTarget", "bitbucket-cloud", _BITBUCKET_OPENAPI),
        ):
            agentcore.CfnGatewayTarget(
                self,
                construct_id_suffix,
                gateway_identifier=gateway.attr_gateway_identifier,
                name=target_name,
                description=f"Placeholder OpenAPI tools for {target_name}",
                target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                    mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                        open_api_schema=agentcore.CfnGatewayTarget.ApiSchemaConfigurationProperty(
                            inline_payload=schema
                        )
                    )
                ),
            )

        self.gateway_url: str = gateway.attr_gateway_url
        self.gateway_arn: str = gateway.attr_gateway_arn

        cdk.CfnOutput(self, "GatewayUrl", value=self.gateway_url)
        cdk.CfnOutput(self, "GatewayArn", value=self.gateway_arn)
