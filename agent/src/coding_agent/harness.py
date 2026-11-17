"""Run one task: checkout, AIPDLC agent loop, record outcome."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

from . import commands, config, credentials, git_ops, options, providers, store
from .state import TaskStatus

log = logging.getLogger(__name__)

_FLEET_CONFIG_ID = "__fleet__"


def _detect_commands(repo: Path, payload: dict[str, Any]) -> tuple[str, str]:
    from . import detect

    verify = str(payload.get("verifyCommand") or "").strip()
    setup = str(payload.get("setupCommand") or "").strip()
    return (
        setup or detect.detect_setup_command(repo),
        verify or detect.detect_verify_command(repo),
    )


async def run_task(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["taskId"])
    prompt = str(payload.get("prompt") or "").strip()
    ref = providers.provider_from_payload(payload)
    cfg = config.load_config()
    work_dir = cfg.work_root / task_id
    repo_dir = work_dir / "repo"
    branch = f"agent/{task_id}"
    base_branch = str(payload.get("defaultBranch") or payload.get("baseBranch") or "main")

    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    token = credentials.fetch_token(ref.provider)
    git_ops.clone(providers.clone_url(ref), repo_dir, token=token or None)
    git_ops.create_branch(repo_dir, branch)
    git_ops.exclude_build_artifacts(repo_dir)

    setup_command, verify_command = _detect_commands(repo_dir, payload)
    if setup_command:
        commands.run_setup(setup_command, cwd=repo_dir)

    from .task_context import TaskContext

    task_ctx = TaskContext(
        task_id=task_id,
        prompt=prompt,
        ref=ref,
        work_dir=str(work_dir),
        repo_dir=str(repo_dir),
        branch=branch,
        base_branch=base_branch,
        verify_command=verify_command,
        setup_command=setup_command,
    )

    entry = cfg.entry_command
    plugin = cfg.bundle_plugin_name
    user_prompt = (
        f"/{plugin}:{entry}\n\n"
        f"Repository: {ref.url}\n"
        f"Base branch: {base_branch}\n"
        f"Task branch: {branch}\n"
        f"Verification command: {verify_command or '(none)'}\n\n"
        f"{prompt}"
    )

    store.set_status(
        {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
        "RUNNING",
        extra={"step": "deliver"},
    )

    final_text = ""
    async for message in query(
        prompt=user_prompt,
        options=options.build_options(task_ctx, cwd=repo_dir),
    ):
        if isinstance(message, AssistantMessage):
            joined = "\n".join(
                block.text for block in message.content if isinstance(block, TextBlock)
            )
            if joined.strip():
                final_text = joined
        elif isinstance(message, ResultMessage) and message.result:
            final_text = message.result

    status: TaskStatus = (
        "SUCCEEDED" if task_ctx.verified and task_ctx.pr_url else "SUCCEEDED_UNVERIFIED"
    )
    if not task_ctx.pr_url:
        status = "FAILED"
        error = "the agent loop finished without opening a pull request"
        store.set_status(
            {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
            status,
            extra={"error": error},
        )
        return {"taskId": task_id, "status": status, "error": error}

    if not task_ctx.verified and status != "FAILED":
        status = "SUCCEEDED_UNVERIFIED"

    store.record_result(
        {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
        status=status,
        pr_url=task_ctx.pr_url,
        verify_result=task_ctx.verification,
    )
    return {"taskId": task_id, "status": status, "prUrl": task_ctx.pr_url, "finalText": final_text}
