"""The fixtures every later phase builds on."""

from __future__ import annotations

import json
import os

from conftest import AwsFixture, api_event


def test_the_aws_fixture_creates_tables_and_queues(aws: AwsFixture) -> None:
    names = set(aws.dynamodb.list_tables()["TableNames"])
    assert "Tasks" in names

    tasks = aws.dynamodb.describe_table(TableName="Tasks")["Table"]
    assert [key["AttributeName"] for key in tasks["KeySchema"]] == ["taskId"]


def test_the_task_queue_has_a_dead_letter_queue(aws: AwsFixture) -> None:
    policy = json.loads(
        aws.sqs.get_queue_attributes(QueueUrl=aws.task_queue_url, AttributeNames=["RedrivePolicy"])[
            "Attributes"
        ]["RedrivePolicy"]
    )
    dlq_arn = aws.sqs.get_queue_attributes(QueueUrl=aws.task_dlq_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    assert policy["deadLetterTargetArn"] == dlq_arn
    assert str(policy["maxReceiveCount"]) == "3"


def test_api_event_builds_an_api_gateway_v2_event() -> None:
    event = api_event("POST", "/tasks", body={"prompt": "add a --version flag"})
    assert event["version"] == "2.0"
    assert event["routeKey"] == "POST /tasks"
    assert event["rawPath"] == "/tasks"
    assert event["requestContext"]["http"]["method"] == "POST"
    assert json.loads(event["body"]) == {"prompt": "add a --version flag"}
    assert "pathParameters" not in event
    assert "queryStringParameters" not in event


def test_fake_credentials_are_injected_so_a_missed_mock_cannot_bill() -> None:
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "testing"
    assert os.environ["AWS_REGION"] == "ap-southeast-1"
    assert "AWS_PROFILE" not in os.environ
