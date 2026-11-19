"""AgentCore entrypoint: accept a task and run the harness off-thread."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from . import harness, store
from .state import TaskState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

app = BedrockAgentCoreApp()
_REQUIRED = ("taskId", "prompt", "branch")
_OPTIONAL_REPO = ("repositoryUrl", "repository")


def _validate(payload: dict[str, Any]) -> None:
    missing = [field for field in _REQUIRED if not payload.get(field)]
    if missing:
        raise ValueError(f"the invocation payload is missing {', '.join(missing)}")
    if not any(payload.get(field) for field in _OPTIONAL_REPO):
        raise ValueError("the invocation payload needs repositoryUrl or repository")


def _run(payload: dict[str, Any], async_task_id: int) -> None:
    fleet_key: TaskState = {"taskId": str(payload["taskId"]), "configId": "__fleet__"}
    try:
        asyncio.run(harness.run_task(payload))
    except Exception as exc:
        log.exception("task %s failed", payload.get("taskId"))
        store.set_status(fleet_key, "FAILED", extra={"error": str(exc)[:2000]})
    finally:
        app.complete_async_task(async_task_id)


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    _validate(payload)
    async_task_id = app.add_async_task("coding-task", {"taskId": payload["taskId"]})
    threading.Thread(
        target=_run,
        args=(payload, async_task_id),
        daemon=True,
        name=f"coding-task-{payload['taskId']}",
    ).start()
    return {"status": "accepted", "taskId": payload["taskId"]}


if __name__ == "__main__":
    app.run()
