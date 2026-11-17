"""Bedrock spend budget and an IAM spend cap wired through AWS Budgets actions.

AWS Budgets is a global service whose canonical endpoint is `us-east-1`, so this stack
deploys there even when the fleet runs elsewhere.
"""

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_iam as iam
from constructs import Construct

BUDGET_REGION = "us-east-1"

DEFAULT_MONTHLY_BUDGET_USD = "50"

SPEND_CAP_POLICY_NAME = "rca-spend-cap-deny"

BEDROCK_COST_FILTER = {"Service": ["Amazon Bedrock"]}


def spend_cap_deny_statements() -> list[iam.PolicyStatement]:
    """Deny model and runtime invocation once the monthly Bedrock budget is exhausted."""
    return [
        iam.PolicyStatement(
            sid="DenyBedrockSpend",
            effect=iam.Effect.DENY,
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock-agentcore:InvokeAgentRuntime",
            ],
            resources=["*"],
        )
    ]


class AccountStack(cdk.Stack):
    """Monthly Bedrock budget with a 100% spend-cap IAM action."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app_name: str,
        monthly_budget_usd: str,
        alert_email: str,
        execution_role_arn: str,
        dispatcher_role_arn: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cdk.Tags.of(self).add("Application", app_name)

        self.spend_cap_policy = iam.ManagedPolicy(
            self,
            "SpendCapDeny",
            managed_policy_name=SPEND_CAP_POLICY_NAME,
            description="Deny Bedrock and AgentCore invocation when the monthly cap fires",
            statements=spend_cap_deny_statements(),
        )

        budget = budgets.CfnBudget(
            self,
            "BedrockSpend",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=float(monthly_budget_usd), unit="USD"
                ),
                cost_filters=BEDROCK_COST_FILTER,
            ),
            notifications_with_subscribers=self._notifications(alert_email),
        )

        budgets_action_role = iam.Role(
            self,
            "BudgetsActionRole",
            assumed_by=iam.ServicePrincipal("budgets.amazonaws.com"),
            description="Role AWS Budgets assumes to attach the spend-cap deny policy",
        )
        budgets_action_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                resources=[execution_role_arn, dispatcher_role_arn],
            )
        )
        budgets_action_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:GetPolicy", "iam:GetPolicyVersion"],
                resources=[self.spend_cap_policy.managed_policy_arn],
            )
        )

        budgets.CfnBudgetsAction(
            self,
            "SpendCapAt100",
            budget_name=budget.ref,
            notification_type="ACTUAL",
            action_type="APPLY_IAM_POLICY",
            approval_model="AUTOMATIC",
            execution_role_arn=budgets_action_role.role_arn,
            action_threshold=budgets.CfnBudgetsAction.ActionThresholdProperty(
                type="PERCENTAGE", value=100
            ),
            definition=budgets.CfnBudgetsAction.DefinitionProperty(
                iam_action_definition=budgets.CfnBudgetsAction.IamActionDefinitionProperty(
                    policy_arn=self.spend_cap_policy.managed_policy_arn,
                    roles=[execution_role_arn, dispatcher_role_arn],
                )
            ),
            subscribers=[],
        )

        cdk.CfnOutput(self, "SpendCapPolicyArn", value=self.spend_cap_policy.managed_policy_arn)
        cdk.CfnOutput(self, "ExecutionRoleArn", value=execution_role_arn)
        cdk.CfnOutput(self, "DispatcherRoleArn", value=dispatcher_role_arn)

    def _notifications(
        self, alert_email: str
    ) -> list[budgets.CfnBudget.NotificationWithSubscribersProperty] | None:
        address = alert_email.strip()
        if not address:
            cdk.Annotations.of(self).add_warning(
                "no budget alert address was given, so the Bedrock spend budget was created "
                "with no notifications. Deploy with "
                "'make deploy EMAIL=you@example.com' (or -c budgetAlertEmail=...) to wire it."
            )
            return None

        subscribers = [
            budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=address)
        ]
        return [
            budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type="ACTUAL",
                    comparison_operator="GREATER_THAN",
                    threshold=80,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=subscribers,
            ),
            budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type="FORECASTED",
                    comparison_operator="GREATER_THAN",
                    threshold=100,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=subscribers,
            ),
        ]
