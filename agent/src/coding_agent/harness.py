"""Run one task: checkout, Kiro agent loop, record outcome."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from . import commands, config, credentials, git_ops, providers, store
from .kiro_runner import prepare_kiro_home, run_kiro
from .state import TaskStatus
from .state_file import load

log = logging.getLogger(__name__)

_FLEET_CONFIG_ID = "__fleet__"

_DELIVER_INSTRUCTIONS = """\
Complete the task using the harness workflow:
1. Implement the requested changes.
2. Call run_verification.
3. Call publish_branch with a clear commit message.
4. Call gateway create_pull_request with title and body summarizing the change.

Never merge or approve pull requests. Never run git push or git remote from shell.
"""


def _detect_commands(repo: Path, payload: dict[str, Any]) -> tuple[str, str]:
    from . import detect

    verify = str(payload.get("verifyCommand") or "").strip()
    setup = str(payload.get("setupCommand") or "").strip()
    return (
        setup or detect.detect_setup_command(repo),
        verify or detect.detect_verify_command(repo),
    )


def _task_prompt(
    payload: dict[str, Any], ref: providers.RepositoryRef, branch: str, base: str, verify: str
) -> str:
    body = str(payload.get("prompt") or "").strip()
    return (
        f"{_DELIVER_INSTRUCTIONS}\n\n"
        f"Repository: {ref.url}\n"
        f"Base branch: {base}\n"
        f"Task branch: {branch}\n"
        f"Verification command: {verify or '(none)'}\n\n"
        f"{body}"
    )


async def run_task(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["taskId"])
    ref = providers.provider_from_payload(payload)
    cfg = config.load_config()
    work_dir = cfg.work_root / task_id
    repo_dir = work_dir / "repo"
    kiro_home = work_dir / "kiro_home"
    state_path = work_dir / ".agent" / "state.json"
    context_path = work_dir / ".agent" / "context.json"
    branch = str(payload.get("branch") or payload.get("taskBranch") or "").strip()
    if not branch:
        raise ValueError(
            "the invocation payload is missing branch (existing remote branch to check out)"
        )
    base_branch = str(payload.get("defaultBranch") or payload.get("baseBranch") or "main")

    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    token = credentials.fetch_token(ref.provider)
    git_ops.clone(providers.clone_url(ref), repo_dir, token=token or None)
    git_ops.checkout_branch(repo_dir, branch)
    git_ops.exclude_build_artifacts(repo_dir)

    setup_command, verify_command = _detect_commands(repo_dir, payload)
    if setup_command:
        commands.run_setup(setup_command, cwd=repo_dir)

    context_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "branch": branch,
                "base_branch": base_branch,
                "verify_command": verify_command,
                "host": ref.provider.host,
                "slug": ref.slug,
                "owner": ref.owner,
                "repo": ref.repo,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps({"branch": branch, "verified": False, "published": False}, indent=2) + "\n",
        encoding="utf-8",
    )

    prepare_kiro_home(kiro_home)

    store.set_status(
        {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
        "RUNNING",
        extra={"step": "deliver"},
    )

    user_prompt = _task_prompt(payload, ref, branch, base_branch, verify_command)
    result = run_kiro(
        prompt=user_prompt,
        kiro_home=kiro_home,
        state_path=state_path,
        workspace=repo_dir,
        context_path=context_path,
        provider=ref.provider,
        branch=branch,
        base_branch=base_branch,
    )

    final_state = load(state_path)
    pr_url = str(final_state.get("pr_url") or "")
    verified = bool(final_state.get("verified"))

    if result.credits is not None:
        store.set_status(
            {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
            "RUNNING",
            extra={"kiroCredits": result.credits},
        )

    if not result.success:
        store.set_status(
            {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
            "FAILED",
            extra={"error": result.error or "kiro run failed"},
        )
        return {"taskId": task_id, "status": "FAILED", "error": result.error}

    if not pr_url:
        store.set_status(
            {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
            "FAILED",
            extra={"error": "the agent loop finished without opening a pull request"},
        )
        return {
            "taskId": task_id,
            "status": "FAILED",
            "error": "the agent loop finished without opening a pull request",
        }

    status: TaskStatus = "SUCCEEDED" if verified and pr_url else "SUCCEEDED_UNVERIFIED"
    store.record_result(
        {"taskId": task_id, "configId": _FLEET_CONFIG_ID},
        status=status,
        pr_url=pr_url,
        verify_result=final_state.get("verification") or {},
    )
    return {
        "taskId": task_id,
        "status": status,
        "prUrl": pr_url,
        "finalText": result.final_text,
        "kiroCredits": result.credits,
    }
