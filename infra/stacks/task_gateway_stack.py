"""The task gateway: task submission over MCP with native Cognito users (email + password).

Kiro connects to an AgentCore Gateway with a `CUSTOM_JWT` authorizer that accepts access
tokens from one Cognito app client. Operators create users in the Cognito console (email as
username); self sign-up stays off. No SAML, Entra, or IAM Identity Center.

A tool call goes gateway -> `mcp_interceptor` (stamps `submittedBy` from the token) ->
`mcp_adapter` (the Lambda target) -> the existing route Lambdas. This gateway is separate
from `GatewayStack`'s: that one is `AWS_IAM` for the runtime, and a gateway has one
inbound authorizer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct
from stacks.api_stack import api_code

KIRO_REDIRECT_URI = "http://localhost:7778/oauth/callback"
RESOURCE_SERVER_ID = "rca"
TASKS_SCOPE = "tasks.submit"
TARGET_NAME = "rca-tasks"
IDENTITY_FIELD = "submittedBy"

INTERCEPTOR_ENTRY_POINT: tuple[str, str] = ("mcp_interceptor", "lambda_handler")
ADAPTER_ENTRY_POINT: tuple[str, str] = ("mcp_adapter", "lambda_handler")

_OUTCOME_NOTE = (
    "SUCCEEDED means the agent's run finished, not that a pull request was opened or a "
    "branch pushed: the agent's own summary, including any PR or compare link, is in the "
    "result text."
)
_TASK_ID = ("string", "The task id submit_task returned, such as tsk_1795091004790_70b66143.")
_IDENTITY = ("string", "Set by the gateway from your sign-in; leave it out.")

TASK_TOOLS: tuple[tuple[str, str, dict[str, tuple[str, str]], tuple[str, ...]], ...] = (
    (
        "submit_task",
        "Start a remote coding agent task: the fleet clones the repository, checks out the "
        "branch, and runs a Kiro agent with the prompt. The branch must already exist on the "
        "remote. Returns the task; follow it with get_task. " + _OUTCOME_NOTE,
        {
            "repositoryUrl": ("string", "Full clone URL, such as https://github.com/owner/repo."),
            "branch": ("string", "Existing remote branch the agent checks out and works on."),
            "prompt": (
                "string",
                "What the agent should do. Say 'create a PR' or 'push' only if the user asked "
                "for it.",
            ),
            "baseBranch": ("string", "Branch a pull request would target. Defaults to main."),
            "verifyCommand": (
                "string",
                "Command that proves the change works, such as `make test`.",
            ),
            "setupCommand": ("string", "Command that prepares the checkout, such as `make setup`."),
            "model": (
                "string",
                "Kiro model id for the remote run, such as claude-sonnet-4.6. Only when the user "
                "asks for one; the fleet defaults to claude-haiku-5.5.",
            ),
            "effort": (
                "string",
                "Reasoning effort for the remote run: low, medium, high, xhigh or max. Only "
                "when asked for.",
            ),
            IDENTITY_FIELD: _IDENTITY,
        },
        ("repositoryUrl", "branch", "prompt"),
    ),
    (
        "get_task",
        "Show one task: status, harness steps, duration, error and the agent's result text. "
        "QUEUED, DISPATCHED and RUNNING are in flight, so poll again (about every 30 "
        "seconds); SUCCEEDED, FAILED and CANCELLED are final. " + _OUTCOME_NOTE,
        {"taskId": _TASK_ID, IDENTITY_FIELD: _IDENTITY},
        ("taskId",),
    ),
    (
        "list_tasks",
        "List recent tasks, optionally filtered by status.",
        {
            "status": (
                "string",
                "One of QUEUED, DISPATCHED, RUNNING, SUCCEEDED, FAILED, CANCELLED.",
            ),
            "limit": ("integer", "How many tasks to return, 1 to 100."),
            IDENTITY_FIELD: _IDENTITY,
        },
        (),
    ),
    (
        "cancel_task",
        "Stop a task that is still in flight. Only call this when the user asks to cancel. "
        "A task that already finished is returned unchanged.",
        {"taskId": _TASK_ID, IDENTITY_FIELD: _IDENTITY},
        ("taskId",),
    ),
)


def _tool_definitions() -> list[agentcore.CfnGatewayTarget.ToolDefinitionProperty]:
    schema = agentcore.CfnGatewayTarget.SchemaDefinitionProperty
    return [
        agentcore.CfnGatewayTarget.ToolDefinitionProperty(
            name=name,
            description=description,
            input_schema=schema(
                type="object",
                properties={
                    key: schema(type=kind, description=text)
                    for key, (kind, text) in properties.items()
                },
                required=list(required) or None,
            ),
        )
        for name, description, properties, required in TASK_TOOLS
    ]


class TaskGatewayStack(cdk.Stack):
    """Native Cognito user pool in front of an MCP gateway for tasks."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app_name: str,
        route_arns: Mapping[str, str],
        cognito_domain_prefix: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        cdk.Tags.of(self).add("Application", app_name)

        # Do not set `sign_in_aliases` (UsernameAttributes) on updates: Cognito rejects
        # changes after create. Create users with username = work email in the console.
        pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"{app_name}-tasks",
            self_sign_up_enabled=False,
            feature_plan=cognito.FeaturePlan.LITE,
            removal_policy=RemovalPolicy.DESTROY,
        )
        domain = pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=cognito_domain_prefix or f"{app_name}-tasks-{self.account}"
            ),
        )
        resource_server = pool.add_resource_server(
            "Tasks",
            identifier=RESOURCE_SERVER_ID,
            scopes=[
                cognito.ResourceServerScope(
                    scope_name=TASKS_SCOPE, scope_description="Submit and track fleet tasks"
                )
            ],
        )

        client = pool.add_client(
            "KiroClient",
            user_pool_client_name=f"{app_name}-kiro",
            generate_secret=False,
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.resource_server(
                        resource_server,
                        cognito.ResourceServerScope(
                            scope_name=TASKS_SCOPE, scope_description="Submit and track fleet tasks"
                        ),
                    ),
                ],
                callback_urls=[KIRO_REDIRECT_URI],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        )

        code = api_code()
        interceptor = self._function(
            "McpInterceptor",
            code,
            INTERCEPTOR_ENTRY_POINT,
            timeout=Duration.seconds(5),
            environment={
                "KIRO_CLIENT_ID": client.user_pool_client_id,
                "LOG_LEVEL": "INFO",
            },
        )

        gateway_role = iam.Role(
            self,
            "TaskGatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=f"{app_name} task gateway: invoke its interceptor and adapter only",
        )
        interceptor_grant = iam.Policy(
            self,
            "InterceptorInvoke",
            roles=[gateway_role],
            statements=[
                iam.PolicyStatement(
                    actions=["lambda:InvokeFunction"], resources=[interceptor.function_arn]
                )
            ],
        )

        gateway = agentcore.CfnGateway(
            self,
            "TaskGateway",
            name=f"{app_name}-task-gateway",
            description=f"{app_name} task submission over MCP (native Cognito users)",
            protocol_type="MCP",
            role_arn=gateway_role.role_arn,
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=(
                        f"https://cognito-idp.{self.region}.amazonaws.com/"
                        f"{pool.user_pool_id}/.well-known/openid-configuration"
                    ),
                    allowed_clients=[client.user_pool_client_id],
                )
            ),
            interceptor_configurations=[
                agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["REQUEST", "RESPONSE"],
                    interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=interceptor.function_arn
                        )
                    ),
                    input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(
                        pass_request_headers=True
                    ),
                )
            ],
        )
        gateway.node.add_dependency(interceptor_grant)

        adapter = self._function(
            "McpAdapter",
            code,
            ADAPTER_ENTRY_POINT,
            timeout=Duration.seconds(40),
            environment={
                "ROUTE_TARGETS": self.to_json_string(dict(route_arns)),
                "GATEWAY_ID": gateway.attr_gateway_identifier,
                "LOG_LEVEL": "INFO",
            },
        )
        adapter.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"], resources=list(route_arns.values())
            )
        )
        adapter_grant = iam.Policy(
            self,
            "AdapterInvoke",
            roles=[gateway_role],
            statements=[
                iam.PolicyStatement(
                    actions=["lambda:InvokeFunction"], resources=[adapter.function_arn]
                )
            ],
        )

        target = agentcore.CfnGatewayTarget(
            self,
            "TasksTarget",
            gateway_identifier=gateway.attr_gateway_identifier,
            name=TARGET_NAME,
            description="Submit, list, get and cancel remote coding agent tasks",
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=adapter.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=_tool_definitions()
                        ),
                    )
                )
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE"
                )
            ],
        )
        target.node.add_dependency(adapter_grant)

        self.gateway_url: str = gateway.attr_gateway_url
        cdk.CfnOutput(self, "TaskGatewayUrl", value=gateway.attr_gateway_url)
        cdk.CfnOutput(self, "CognitoClientId", value=client.user_pool_client_id)
        cdk.CfnOutput(self, "CognitoDomain", value=domain.base_url())
        cdk.CfnOutput(self, "OAuthScope", value=f"{RESOURCE_SERVER_ID}/{TASKS_SCOPE}")
        cdk.CfnOutput(self, "UserPoolId", value=pool.user_pool_id)

    def _function(
        self,
        key: str,
        code: lambda_.Code,
        entry: tuple[str, str],
        *,
        timeout: Duration,
        environment: dict[str, str],
    ) -> lambda_.Function:
        module, function = entry
        return lambda_.Function(
            self,
            key,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler=f"handlers.{module}.{function}",
            code=code,
            timeout=timeout,
            memory_size=256,
            environment=environment,
            recursive_loop=lambda_.RecursiveLoop.TERMINATE,
            log_group=logs.LogGroup(
                self,
                f"{key}Logs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
