"""The system of record: the Tasks table and the task queue pair.

This stack is separated from the other stacks by *lifecycle*, not tidiness. The table is
`RemovalPolicy.RETAIN` (invariant 22), because teardown is operational rather than
`cdk destroy`, which would remove every task record.

`Tasks` is keyed only by `taskId` -- one partition key, no sort key and no GSI.

No physical names on the table or queues. The names reach the handlers as Lambda
environment variables (`TASK_TABLE`, `TASK_QUEUE_URL`, ...).
"""

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class DataStack(cdk.Stack):
    """The tasks table and the task queue pair, exposed as attributes `ApiStack` reads."""

    def __init__(
        self, scope: Construct, construct_id: str, *, app_name: str, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cdk.Tags.of(self).add("Application", app_name)

        self.tasks = dynamodb.TableV2(
            self,
            "Tasks",
            partition_key=dynamodb.Attribute(name="taskId", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.task_queue, self.task_dlq = self._queue_with_dlq(
            "Task", visibility=cdk.Duration.minutes(6)
        )

    def _queue_with_dlq(
        self, name: str, *, visibility: cdk.Duration
    ) -> tuple[sqs.Queue, sqs.Queue]:
        """Create `{name}Queue` and `{name}Dlq`, wired by a redrive policy."""
        dlq = sqs.Queue(
            self,
            f"{name}Dlq",
            retention_period=cdk.Duration.days(14),
            enforce_ssl=True,
            visibility_timeout=visibility,
        )
        queue = sqs.Queue(
            self,
            f"{name}Queue",
            enforce_ssl=True,
            visibility_timeout=visibility,
            dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=3),
        )
        return queue, dlq
