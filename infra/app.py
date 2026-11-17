"""CDK entry point: Data, Agent, Gateway, Api and Account stacks."""

from __future__ import annotations

import re
from typing import NamedTuple

import aws_cdk
from stacks import account_stack
from stacks.account_stack import AccountStack
from stacks.agent_stack import AgentStack, select_agent_zones
from stacks.api_stack import ApiStack
from stacks.data_stack import DataStack
from stacks.gateway_stack import GatewayStack
from stacks.prompt_guardrail import guardrail_profile_for

APP_NAME = "rca"
DEFAULT_STACK_PREFIX = "SampleCodingAgent"


class StackNames(NamedTuple):
    account: str
    data: str
    agent: str
    gateway: str
    api: str


_REGION_WORD = re.compile(r"[A-Za-z0-9]+")


def region_suffix(region: str | None) -> str:
    if not region:
        return ""
    return "".join(
        word[:1].upper() + word[1:] for word in _REGION_WORD.findall(str(region).strip().lower())
    )


def stack_names(prefix: str = DEFAULT_STACK_PREFIX, region: str | None = None) -> StackNames:
    return StackNames(
        account=f"{prefix}Account{region_suffix(region)}",
        data=f"{prefix}Data",
        agent=f"{prefix}Runtime",
        gateway=f"{prefix}Gateway",
        api=f"{prefix}Api",
    )


_DEFAULT_NAMES = stack_names()
ACCOUNT_STACK_NAME = _DEFAULT_NAMES.account
DATA_STACK_NAME = _DEFAULT_NAMES.data
AGENT_STACK_NAME = _DEFAULT_NAMES.agent
GATEWAY_STACK_NAME = _DEFAULT_NAMES.gateway
API_STACK_NAME = _DEFAULT_NAMES.api

_DEFAULT_PLAN_MODEL = "global.anthropic.claude-opus-4-5-20251101-v1:0"
_DEFAULT_CODE_MODEL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
_DEFAULT_HAIKU_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def _context(app: aws_cdk.App, key: str, default: str) -> str:
    value = app.node.try_get_context(key)
    return str(value) if value else default


def _flag_context(app: aws_cdk.App, key: str, *, default: bool) -> bool:
    value = app.node.try_get_context(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _endpoint_az_count(app: aws_cdk.App) -> int:
    value = app.node.try_get_context("endpointAzCount")
    if str(value).strip() == "2":
        return 2
    return 1


def _agent_zones(app: aws_cdk.App, region: str | None) -> list[str]:
    override = _context(app, "agentAvailabilityZones", "")
    if override:
        return [zone.strip() for zone in override.split(",") if zone.strip()]
    if not region:
        return []
    import boto3

    answer = boto3.client("ec2", region_name=region).describe_availability_zones(
        Filters=[
            {"Name": "zone-type", "Values": ["availability-zone"]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    return select_agent_zones(
        region,
        [(zone["ZoneName"], zone["ZoneId"]) for zone in answer["AvailabilityZones"]],
        count=_endpoint_az_count(app),
    )


def _guardrail_profile(app: aws_cdk.App, region: str | None) -> str | None:
    if not _flag_context(app, "guardrailEnabled", default=True):
        return None
    override = _context(app, "guardrailProfile", "")
    if not region and not override:
        return None
    return guardrail_profile_for(region, override)


def main() -> None:
    app = aws_cdk.App()

    account_id = app.node.try_get_context("account")
    region = app.node.try_get_context("region")
    env = aws_cdk.Environment(account=account_id, region=region)

    names = stack_names(_context(app, "stackPrefix", DEFAULT_STACK_PREFIX), region)

    data = DataStack(app, names.data, app_name=APP_NAME, env=env)

    gateway = GatewayStack(app, names.gateway, app_name=APP_NAME, env=env)

    agent = AgentStack(
        app,
        names.agent,
        app_name=APP_NAME,
        alert_email=_context(app, "budgetAlertEmail", ""),
        availability_zones=_agent_zones(app, region),
        plan_model_id=_context(app, "planModelId", _DEFAULT_PLAN_MODEL),
        code_model_id=_context(app, "codeModelId", _DEFAULT_CODE_MODEL),
        haiku_model_id=_context(app, "haikuModelId", _DEFAULT_HAIKU_MODEL),
        fallback_model_id=_context(app, "fallbackModelId", _DEFAULT_CODE_MODEL),
        goal_enabled=_flag_context(app, "codeGoalEnabled", default=True),
        git_author_name=_context(app, "gitAuthorName", ""),
        git_author_email=_context(app, "gitAuthorEmail", ""),
        gateway_url=gateway.gateway_url,
        tasks_table_name=data.tasks.table_name,
        env=env,
    )

    data.tasks.grant_read_write_data(agent.execution_role)

    api = ApiStack(
        app,
        names.api,
        app_name=APP_NAME,
        data=data,
        agent=agent,
        guardrail_profile_id=_guardrail_profile(app, region),
        env=env,
    )

    AccountStack(
        app,
        names.account,
        app_name=APP_NAME,
        monthly_budget_usd=_context(
            app, "monthlyBudgetUsd", account_stack.DEFAULT_MONTHLY_BUDGET_USD
        ),
        alert_email=_context(app, "budgetAlertEmail", ""),
        execution_role_arn=agent.execution_role.role_arn,
        dispatcher_role_arn=api.dispatcher_role_arn,
        env=aws_cdk.Environment(account=account_id, region=account_stack.BUDGET_REGION),
    )

    app.synth()


if __name__ == "__main__":
    main()
