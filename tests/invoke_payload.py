"""Invocation payload contract between dispatcher and container."""

from __future__ import annotations

from typing import Any

REQUIRED: tuple[str, ...] = ("taskId", "prompt", "repositoryUrl", "branch")
OPTIONAL: tuple[str, ...] = (
    "defaultBranch",
    "verifyCommand",
    "setupCommand",
    "model",
    "effort",
    "sessionId",
)
FIELDS: tuple[str, ...] = REQUIRED + OPTIONAL


def payload(**overrides: Any) -> dict[str, Any]:
    complete: dict[str, Any] = {
        "taskId": "tsk_1700000000000_abcdef",
        "prompt": "make the tests pass",
        "repositoryUrl": "https://github.com/acme/widgets",
        "branch": "feature/agent-task",
        "defaultBranch": "main",
        "verifyCommand": "make test",
        "setupCommand": "make setup",
        "model": "claude-sonnet-4.6",
        "effort": "high",
        "sessionId": "tsk_1700000000000_abcdef-822b04fc976432ceb11bc5cbfab01400",
    }
    return {**complete, **overrides}


def required_only(**overrides: Any) -> dict[str, Any]:
    complete = payload()
    return {**{field: complete[field] for field in REQUIRED}, **overrides}
