"""Queue runs: guardrail screening and task dispatch."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from . import _common, observability
from ._common import LOG, ApiError

QUEUED, DISPATCHED = "QUEUED", "DISPATCHED"
RUNNING = "RUNNING"
FLEET_CONFIG_ID = "__fleet__"

TASK_STATUSES = (
    "QUEUED",
    "DISPATCHED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
)
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

_REPO_URL = re.compile(r"^https?://[^/]+/.+/.+|^git@[^:]+:.+/.+")

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Model ids depend on the Kiro account and release, so only the shape is checked. A leading
# letter or digit keeps a value from ever reading as a `kiro-cli` flag.
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True)
class ScreenedPrompt:
    text: str


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL_STATUSES


def screen_prompt(prompt: str, *, refused: str = "no run was queued") -> ScreenedPrompt:
    if not _common.GUARDRAIL_ID:
        return ScreenedPrompt(text=prompt)
    try:
        response = _common.bedrock_runtime().apply_guardrail(
            guardrailIdentifier=_common.GUARDRAIL_ID,
            guardrailVersion=_common.GUARDRAIL_VERSION,
            source="INPUT",
            content=[{"text": {"text": prompt}}],
        )
    except (ClientError, BotoCoreError) as exc:
        raise ApiError(503, "GuardrailUnavailable", str(exc)) from exc
    action = response.get("action", "")
    if action == "GUARDRAIL_INTERVENED":
        raise ApiError(
            422,
            "PromptBlockedByGuardrail",
            f"{refused}; the prompt was blocked by the guardrail",
        )
    return ScreenedPrompt(text=prompt)


def _allowed_repository(url: str) -> None:
    if not _REPO_URL.match(url.strip()):
        raise ApiError(400, "InvalidRepositoryUrl", "repositoryUrl is not recognised")
    allowlist = os.environ.get("REPOSITORY_ALLOWLIST", "").strip()
    if not allowlist:
        return
    allowed = {part.strip() for part in allowlist.split(",") if part.strip()}
    for entry in allowed:
        if entry in url:
            return
    raise ApiError(409, "RepositoryNotAllowed", "this repository is not on the allowlist")


def _valid_model_and_effort(model: str, effort: str) -> None:
    if model and not _MODEL_ID.match(model):
        raise ApiError(
            400,
            "InvalidBody",
            "model must be a Kiro model id such as claude-sonnet-4.6 "
            "(letters, digits, '.', '_', ':', '/', '-'; at most 128 characters)",
        )
    if effort and effort not in EFFORT_LEVELS:
        raise ApiError(400, "InvalidBody", f"effort must be one of {', '.join(EFFORT_LEVELS)}")


def queue_run(
    *,
    repository_url: str,
    prompt: str,
    branch: str,
    base_branch: str = "",
    verify_command: str = "",
    setup_command: str = "",
    model: str = "",
    effort: str = "",
    team: str = "",
    submitted_by: str = "",
) -> dict[str, Any]:
    _allowed_repository(repository_url)
    _valid_model_and_effort(model, effort)
    screened = screen_prompt(prompt)
    task_id = f"tsk_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    runtime_arn = os.environ.get("AGENT_RUNTIME_ARN", "").strip()
    if not runtime_arn:
        raise ApiError(503, "RuntimeUnavailable", "no AgentCore runtime is configured")

    item: dict[str, Any] = {
        "taskId": task_id,
        "configId": FLEET_CONFIG_ID,
        "repositoryUrl": repository_url,
        "branch": branch.strip(),
        "prompt": screened.text,
        "status": QUEUED,
        "createdAt": _common.now_iso(),
        "updatedAt": _common.now_iso(),
        "agentRuntimeArn": runtime_arn,
        "sessionId": observability.session_id(task_id),
    }
    if base_branch:
        item["defaultBranch"] = base_branch
    if verify_command:
        item["verifyCommand"] = verify_command
    if setup_command:
        item["setupCommand"] = setup_command
    if model:
        item["model"] = model
    if effort:
        item["effort"] = effort
    if team:
        item["team"] = team
    if submitted_by:
        item["submittedBy"] = submitted_by

    _common.tasks_table.put_item(Item=item)
    _common.sqs.send_message(
        QueueUrl=_common.TASK_QUEUE_URL,
        MessageBody=json.dumps({"taskId": task_id}),
    )
    LOG.info("queued task %s", task_id)
    return item
