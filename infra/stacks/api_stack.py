"""The control plane: task HTTP routes, the dispatch queue consumer, and optional guardrail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authorizers
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_logs as logs
from constructs import Construct
from stacks.agent_stack import AgentStack
from stacks.data_stack import DataStack
from stacks.prompt_guardrail import PromptGuardrail, add_prompt_guardrail

API_URL_OUTPUT_KEY = "ApiUrl"

ROUTES: tuple[tuple[str, str, str, str], ...] = (
    ("POST", "/tasks", "tasks", "create"),
    ("GET", "/tasks", "tasks", "list_"),
    ("GET", "/tasks/{taskId}", "tasks", "get"),
    ("POST", "/tasks/{taskId}/cancel", "tasks", "cancel"),
)

QUEUE_ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("dispatcher", "lambda_handler"),
    ("dispatcher", "dlq_handler"),
)

SELF_CHAINING_ENTRY_POINTS: frozenset[tuple[str, str]] = frozenset()

_CAPABILITIES: frozenset[str] = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "tasks-queue:send",
        "agentcore:invoke",
        "agentcore:stop",
        "guardrail:apply",
    }
)

_ENTRY_POINT_GRANTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("tasks", "create"): ("tasks:write", "tasks-queue:send", "guardrail:apply"),
    ("tasks", "list_"): ("tasks:read",),
    ("tasks", "get"): ("tasks:read",),
    ("tasks", "cancel"): ("tasks:read", "tasks:write", "agentcore:stop"),
    ("dispatcher", "lambda_handler"): ("tasks:read", "tasks:write", "agentcore:invoke"),
    ("dispatcher", "dlq_handler"): ("tasks:write",),
}


def _common_environment(
    data: DataStack, guardrail: PromptGuardrail | None, *, agent_runtime_arn: str
) -> dict[str, str]:
    return {
        "TASKS_TABLE": data.tasks.table_name,
        "TASK_QUEUE_URL": data.task_queue.queue_url,
        "AGENT_RUNTIME_ARN": agent_runtime_arn,
        "GUARDRAIL_ID": guardrail.arn if guardrail is not None else "",
        "GUARDRAIL_VERSION": guardrail.version if guardrail is not None else "",
        "LOG_LEVEL": "INFO",
    }


def _construct_id(module: str, function: str) -> str:
    return "".join(part.title() for part in f"{module}_{function}".strip("_").split("_"))


class ApiStack(cdk.Stack):
    """HTTP task API and queue consumers for a single static AgentCore runtime."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app_name: str,
        data: DataStack,
        agent: AgentStack,
        guardrail_profile_id: str | None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self._app_name = app_name
        self._data = data
        self._agent = agent
        self._guardrail = (
            add_prompt_guardrail(self, app_name=app_name, profile_id=guardrail_profile_id)
            if guardrail_profile_id
            else None
        )

        code = lambda_.Code.from_asset(
            str(Path(__file__).resolve().parents[2] / "api"),
            bundling=cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                platform="linux/arm64",
                command=[
                    "bash",
                    "-c",
                    "pip install --no-cache-dir -r requirements.txt -t /asset-output "
                    "&& cp -r src/. /asset-output",
                ],
            ),
        )
        self._code = code
        common = _common_environment(
            data, self._guardrail, agent_runtime_arn=agent.agent_runtime_arn
        )

        api = apigwv2.HttpApi(
            self,
            "Api",
            api_name=f"{app_name}-api",
            default_authorizer=apigwv2_authorizers.HttpIamAuthorizer(),
        )

        self.handler_arns: dict[str, str] = {}
        self.dispatcher_role_arn: str = ""

        for method, path, module, function in ROUTES:
            read_route = method == "GET"
            fn = self._function(
                _construct_id(module, function),
                module=module,
                function=function,
                timeout=Duration.seconds(10) if read_route else Duration.seconds(30),
                memory=256 if read_route else 512,
                environment=common,
                capabilities=_ENTRY_POINT_GRANTS[(module, function)],
            )
            api.add_routes(
                path=path,
                methods=[apigwv2.HttpMethod(method)],
                integration=apigwv2_integrations.HttpLambdaIntegration(f"{module}-{function}", fn),
            )
            self.handler_arns[f"{method} {path}"] = fn.function_arn

        dispatcher = self._function(
            _construct_id("dispatcher", "lambda_handler"),
            module="dispatcher",
            function="lambda_handler",
            timeout=Duration.minutes(1),
            memory=512,
            environment=common,
            capabilities=_ENTRY_POINT_GRANTS[("dispatcher", "lambda_handler")],
        )
        role = dispatcher.role
        if role is None:
            raise TypeError("dispatcher Lambda has no execution role")
        self.dispatcher_role_arn = role.role_arn

        dlq_handler = self._function(
            _construct_id("dispatcher", "dlq_handler"),
            module="dispatcher",
            function="dlq_handler",
            timeout=Duration.minutes(1),
            memory=512,
            environment=common,
            capabilities=_ENTRY_POINT_GRANTS[("dispatcher", "dlq_handler")],
        )

        dispatcher.add_event_source(
            lambda_event_sources.SqsEventSource(data.task_queue, batch_size=1)
        )
        dlq_handler.add_event_source(
            lambda_event_sources.SqsEventSource(data.task_dlq, batch_size=1)
        )

        cdk.CfnOutput(self, API_URL_OUTPUT_KEY, value=api.api_endpoint)
        cdk.CfnOutput(self, "DispatcherRoleArn", value=self.dispatcher_role_arn)

    def _function(
        self,
        key: str,
        *,
        module: str,
        function: str,
        timeout: Duration,
        memory: int,
        environment: dict[str, str],
        capabilities: tuple[str, ...],
    ) -> lambda_.Function:
        fn = lambda_.Function(
            self,
            key,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler=f"handlers.{module}.{function}",
            code=self._code,
            timeout=timeout,
            memory_size=memory,
            environment=environment,
            recursive_loop=lambda_.RecursiveLoop.TERMINATE,
            log_group=logs.LogGroup(
                self,
                f"{key}Logs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self._apply_grants(fn, capabilities)
        return fn

    def _apply_grants(self, fn: lambda_.Function, capabilities: tuple[str, ...]) -> None:
        data, agent = self._data, self._agent
        runtime_arn = agent.agent_runtime_arn
        for capability in capabilities:
            if capability not in _CAPABILITIES:
                raise ValueError(f"unknown capability {capability!r}")
            if capability == "tasks:read":
                data.tasks.grant_read_data(fn)
            elif capability == "tasks:write":
                data.tasks.grant_write_data(fn)
            elif capability == "tasks-queue:send":
                data.task_queue.grant_send_messages(fn)
            elif capability == "agentcore:invoke":
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        actions=["bedrock-agentcore:InvokeAgentRuntime"],
                        resources=[
                            runtime_arn,
                            f"{runtime_arn}/runtime-endpoint/*",
                        ],
                    )
                )
            elif capability == "agentcore:stop":
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        actions=["bedrock-agentcore:StopRuntimeSession"],
                        resources=[
                            runtime_arn,
                            f"{runtime_arn}/runtime-endpoint/*",
                        ],
                    )
                )
            elif capability == "guardrail:apply":
                guardrail = self._guardrail
                if guardrail is not None:
                    fn.add_to_role_policy(
                        iam.PolicyStatement(
                            actions=["bedrock:ApplyGuardrail"],
                            resources=[
                                guardrail.arn,
                                f"arn:aws:bedrock:*:{self.account}:guardrail-profile/{guardrail.profile_id}",
                            ],
                        )
                    )
