from __future__ import annotations

from rca_mcp.server import TOOLS, call_tool


class _Stub:
    def post(self, _path: str, body: dict) -> dict:
        return {
            "taskId": "tsk_1",
            "status": "QUEUED",
            "repositoryUrl": body["repositoryUrl"],
            "prompt": body["prompt"],
        }

    def get(self, _path: str, **_kwargs) -> dict:
        return {"taskId": "tsk_1", "status": "QUEUED"}


def test_tools_are_four() -> None:
    assert {spec.name for spec in TOOLS} == {
        "submit_task",
        "get_task",
        "list_tasks",
        "cancel_task",
    }


def test_submit_task_calls_post() -> None:
    text = call_tool(
        _Stub(), "submit_task", {"repositoryUrl": "https://github.com/a/b", "prompt": "hi"}
    )
    assert "tsk_1" in text
