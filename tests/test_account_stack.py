"""`AccountStack` synthesizes with budget, spend-cap policy and budget action."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aws_cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template
from stacks import account_stack
from stacks.account_stack import AccountStack

_ALERT_EMAIL = "ops@example.com"
_EXECUTION_ROLE = "arn:aws:iam::123456789012:role/rca/rca-execution"
_DISPATCHER_ROLE = "arn:aws:iam::123456789012:role/rca-dispatcher-role"


def _build(tmp_path: Path, *, alert_email: str = _ALERT_EMAIL) -> AccountStack:
    app = aws_cdk.App(outdir=str(tmp_path))
    return AccountStack(
        app,
        "CodingAgentAccount",
        app_name="rca",
        monthly_budget_usd=account_stack.DEFAULT_MONTHLY_BUDGET_USD,
        alert_email=alert_email,
        execution_role_arn=_EXECUTION_ROLE,
        dispatcher_role_arn=_DISPATCHER_ROLE,
        env=aws_cdk.Environment(account="123456789012", region=account_stack.BUDGET_REGION),
    )


@pytest.fixture
def template(tmp_path: Path) -> Template:
    return Template.from_stack(_build(tmp_path))


def _properties(template: Template, kind: str) -> list[dict[str, Any]]:
    return [resource.get("Properties", {}) for resource in template.find_resources(kind).values()]


def test_the_default_monthly_budget_is_fifty() -> None:
    assert account_stack.DEFAULT_MONTHLY_BUDGET_USD == "50"


def test_the_budget_measures_bedrock_monthly_spend(template: Template) -> None:
    (budget,) = _properties(template, "AWS::Budgets::Budget")
    data = budget["Budget"]
    assert data["BudgetLimit"] == {"Amount": 50.0, "Unit": "USD"}
    assert data["CostFilters"] == account_stack.BEDROCK_COST_FILTER


def test_the_spend_cap_policy_is_created(template: Template) -> None:
    policies = _properties(template, "AWS::IAM::ManagedPolicy")
    assert any(
        props.get("ManagedPolicyName") == account_stack.SPEND_CAP_POLICY_NAME for props in policies
    )


def test_the_budget_action_attaches_the_spend_cap_at_100_percent(template: Template) -> None:
    (action,) = _properties(template, "AWS::Budgets::BudgetsAction")
    assert action["ActionType"] == "APPLY_IAM_POLICY"
    assert action["NotificationType"] == "ACTUAL"
    assert action["ActionThreshold"] == {"Type": "PERCENTAGE", "Value": 100}
    roles = action["Definition"]["IamActionDefinition"]["Roles"]
    assert roles == [_EXECUTION_ROLE, _DISPATCHER_ROLE]


def test_the_budget_action_notifies_the_alert_address(template: Template) -> None:
    """CloudFormation's early validation rejects a budget action with no subscribers."""
    (action,) = _properties(template, "AWS::Budgets::BudgetsAction")
    assert action["Subscribers"] == [{"Type": "EMAIL", "Address": _ALERT_EMAIL}]


def test_an_empty_alert_address_leaves_the_budget_with_no_notifications(tmp_path: Path) -> None:
    stack = _build(tmp_path, alert_email="")
    template = Template.from_stack(stack)
    (budget,) = _properties(template, "AWS::Budgets::Budget")
    assert "NotificationsWithSubscribers" not in budget
    template.resource_count_is("AWS::Budgets::BudgetsAction", 0)
    Annotations.from_stack(stack).has_warning(
        "*", Match.string_like_regexp("no budget alert address")
    )
