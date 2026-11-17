"""Task status vocabulary and small result shapes for the harness."""

from __future__ import annotations

from typing import Literal, TypedDict

TaskStatus = Literal[
    "QUEUED",
    "DISPATCHED",
    "RUNNING",
    "SUCCEEDED",
    "SUCCEEDED_UNVERIFIED",
    "FAILED",
    "CANCELLED",
]

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"SUCCEEDED", "SUCCEEDED_UNVERIFIED", "FAILED", "CANCELLED"}
)


class CommandResult(TypedDict, total=False):
    command: str
    exitCode: int | None
    output: str
    warning: str | None


class TaskState(TypedDict, total=False):
    taskId: str
    configId: str
