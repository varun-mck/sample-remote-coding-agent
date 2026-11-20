"""AgentCore Gateway: Git/Atlassian OpenAPI targets and Atlassian Rovo MCP for Confluence."""

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from constructs import Construct

# Minimal OpenAPI placeholders -- real provider definitions replace these at configure time.
# Minimal GitHub pull-request create schema for AgentCore Gateway. Path and body fields match
# https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request and the arguments Kiro
# sends (owner, repo, title, head, base, body, draft). Outbound auth is not in the spec; the
# target attaches the `<app>_github` API-key provider as `Authorization: Bearer <PAT>`.
_GITHUB_OPENAPI = """\
openapi: 3.0.3
info:
  title: GitHub REST API (pull request create)
  version: 0.1.0
  description: >-
    Minimal OpenAPI for AgentCore Gateway MCP tools. Replace or extend from the official
    GitHub OpenAPI export when adding more operations.
servers:
  - url: https://api.github.com
paths:
  /repos/{owner}/{repo}/pulls:
    post:
      operationId: create_pull_request
      summary: Create a pull request
      description: >-
        Creates a pull request. Requires a PAT with pull-request write access on the
        repository. The gateway target must attach an outbound API-key credential provider.
      parameters:
        - name: owner
          in: path
          required: true
          description: Repository owner login.
          schema:
            type: string
        - name: repo
          in: path
          required: true
          description: Repository name without .git.
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - title
                - head
                - base
              properties:
                title:
                  type: string
                  description: Pull request title.
                head:
                  type: string
                  description: Branch containing changes (same repo or user:branch).
                base:
                  type: string
                  description: Branch to merge into.
                body:
                  type: string
                  description: Pull request description.
                draft:
                  type: boolean
                  description: Create as draft.
                maintainer_can_modify:
                  type: boolean
      responses:
        "201":
          description: Created
        "403":
          description: Forbidden
        "422":
          description: Validation failed
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

_JIRA_OPENAPI = """\
openapi: 3.0.3
info:
  title: Jira Cloud API (minimal)
  version: 0.0.1
servers:
  - url: https://api.atlassian.com
paths:
  /ex/jira/{cloudId}/rest/api/3/issue/{issueIdOrKey}:
    get:
      operationId: get_issue
      parameters:
        - name: cloudId
          in: path
          required: true
          schema:
            type: string
        - name: issueIdOrKey
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: OK
  /ex/jira/{cloudId}/rest/api/3/issue/{issueIdOrKey}/comment:
    post:
      operationId: add_comment
      parameters:
        - name: cloudId
          in: path
          required: true
          schema:
            type: string
        - name: issueIdOrKey
          in: path
          required: true
          schema:
            type: string
      responses:
        "201":
          description: Created
"""

_ATLASSIAN_MCP_ENDPOINT = "https://mcp.atlassian.com/v2/mcp?tools=all"

_JENKINS_OPENAPI = """\
openapi: 3.0.3
info:
  title: Jenkins API (minimal)
  version: 0.0.1
servers:
  - url: https://jenkins.example.com
paths:
  /job/{job}/api/json:
    get:
      operationId: get_build_status
      parameters:
        - name: job
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: OK
"""


class GatewayStack(cdk.Stack):
    """Bedrock AgentCore Gateway with Git and Atlassian tool targets."""

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

        # The PAT behind `{app_name}_github` is the one git already uses; created outside CDK.
        github_provider = f"{app_name}_github"
        atlassian_provider = f"{app_name}_atlassian"
        github_provider_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default/"
            f"apikeycredentialprovider/{github_provider}"
        )
        atlassian_provider_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default/"
            f"apikeycredentialprovider/{atlassian_provider}"
        )
        # Outbound API-key auth: the gateway exchanges its own workload token for the key, and
        # Identity reads the key's secret with the gateway role's credentials.
        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetResourceApiKey",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:token-vault/default",
                    github_provider_arn,
                    atlassian_provider_arn,
                ],
            )
        )
        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity!*"
                ],
            )
        )

        gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name=f"{app_name}-gateway",
            authorizer_type="AWS_IAM",
            role_arn=gateway_role.role_arn,
            description=f"{app_name} MCP gateway for Git and Atlassian tools",
        )

        def _bearer_api_key_auth(provider_arn: str) -> list[Any]:
            return [
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="API_KEY",
                    credential_provider=agentcore.CfnGatewayTarget.CredentialProviderProperty(
                        api_key_credential_provider=agentcore.CfnGatewayTarget.ApiKeyCredentialProviderProperty(
                            provider_arn=provider_arn,
                            credential_location="HEADER",
                            credential_parameter_name="Authorization",
                            credential_prefix="Bearer",
                        )
                    ),
                )
            ]

        github_auth = _bearer_api_key_auth(github_provider_arn)
        atlassian_auth = _bearer_api_key_auth(atlassian_provider_arn)

        for construct_id_suffix, target_name, schema, auth in (
            ("GithubTarget", "github", _GITHUB_OPENAPI, github_auth),
            ("BitbucketCloudTarget", "bitbucket-cloud", _BITBUCKET_OPENAPI, None),
            ("JiraTarget", "jira", _JIRA_OPENAPI, None),
            ("JenkinsTarget", "jenkins", _JENKINS_OPENAPI, None),
        ):
            target = agentcore.CfnGatewayTarget(
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
                credential_provider_configurations=auth,
            )
            # The role's Identity grants must exist before the target validates its auth.
            target.node.add_dependency(gateway_role)

        confluence_mcp = agentcore.CfnGatewayTarget(
            self,
            "ConfluenceMcpTarget",
            gateway_identifier=gateway.attr_gateway_identifier,
            name="confluence",
            description="Atlassian Rovo MCP (Confluence tools via service-account API key)",
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    mcp_server=agentcore.CfnGatewayTarget.McpServerTargetConfigurationProperty(
                        endpoint=_ATLASSIAN_MCP_ENDPOINT,
                        listing_mode="DEFAULT",
                    )
                )
            ),
            credential_provider_configurations=atlassian_auth,
        )
        confluence_mcp.node.add_dependency(gateway_role)

        self.gateway_url: str = gateway.attr_gateway_url
        self.gateway_arn: str = gateway.attr_gateway_arn

        cdk.CfnOutput(self, "GatewayUrl", value=self.gateway_url)
        cdk.CfnOutput(self, "GatewayArn", value=self.gateway_arn)
