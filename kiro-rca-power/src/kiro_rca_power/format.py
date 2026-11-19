"""Task records rendered as plain text for the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Mirrors `api/src/handlers/runs.py` on the fleet branch.
TASK_STATUSES = ("QUEUED", "DISPATCHED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


def _repository(item: Mapping[str, Any]) -> str:
    url = str(item.get("repositoryUrl") or "unknown repository")
    branch = item.get("branch")
    return f"{url} @ {branch}" if branch else url


def task(item: Mapping[str, Any]) -> str:
    status = str(item.get("status") or "UNKNOWN")
    done = status in TERMINAL_STATUSES
    lines = [
        f"task: {item.get('taskId', '?')}",
        f"status: {status} ({'finished' if done else 'in flight; poll get_task again'})",
        f"repository: {_repository(item)}",
    ]
    for key in ("model", "effort", "createdAt", "updatedAt", "durationSeconds", "kiroCredits"):
        if item.get(key) not in (None, ""):
            lines.append(f"{key}: {item[key]}")
    steps = item.get("steps")
    if isinstance(steps, list) and steps:
        lines.append(f"steps: {' -> '.join(str(step) for step in steps)}")
    if item.get("cancelledAt"):
        lines.append(f"cancelledAt: {item['cancelledAt']}")
    if item.get("error"):
        lines.append(f"error: {item['error']}")
    if item.get("result"):
        lines.extend(["result:", str(item["result"])])
    elif status == "SUCCEEDED":
        lines.append("result: (none recorded)")
    return "\n".join(lines)


def tasks(page: Mapping[str, Any]) -> str:
    items = [item for item in page.get("items") or [] if isinstance(item, Mapping)]
    if not items:
        return "no tasks found"
    items.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    lines = [f"{len(items)} task(s), newest first:"]
    lines.extend(
        f"- {item.get('taskId', '?')}  {item.get('status', '?')}  {_repository(item)}  "
        f"created {item.get('createdAt', '?')}"
        for item in items
    )
    return "\n".join(lines)
