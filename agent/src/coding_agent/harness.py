"""Run one task: checkout, Kiro agent loop, record outcome."""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import commands, config, credentials, git_ops, providers, store
from .kiro_profile import kiro_model_for_task
from .kiro_runner import prepare_kiro_home, run_kiro
from .state import TaskState

log = logging.getLogger(__name__)

_FLEET_CONFIG_ID = "__fleet__"
_BRIDGE_LOG_TAIL_LINES = 80

_TASK_PREAMBLE = """\
You are working in a checked-out Git repository for a remote coding task.

Use `shell` for local commands (including verification when a command is given).
Use the AgentCore Gateway MCP tools (list tools if needed) for Git host APIs, Jira,
Confluence, and other configured providers — tool names are usually `{provider}___{operation}`.

Complete what the user asked. Push or publish only when the task requires it.
End with a clear summary of what you did and any links (PR, page, ticket, etc.).
Never merge or approve pull requests.
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
    prefix = providers.gateway_tool_prefix(ref.provider)
    return (
        f"{_TASK_PREAMBLE}\n\n"
        f"Repository: {ref.url}\n"
        f"Base branch: {base}\n"
        f"Task branch: {branch}\n"
        f"Verification command: {verify or '(none)'}\n"
        f"Gateway tool prefix hint: {prefix}___\n\n"
        f"{body}"
    )


@contextlib.contextmanager
def _step(task_key: TaskState, name: str) -> Iterator[None]:
    started = time.monotonic()
    log.info("step start: %s", name)
    store.set_status(task_key, "RUNNING", step=name)
    try:
        yield
    except Exception:
        log.exception("step failed: %s after %.1fs", name, time.monotonic() - started)
        raise
    else:
        log.info("step done: %s (%.1fs)", name, time.monotonic() - started)


def _git_head_sha(repo_dir: Path) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip()[:12] if out.returncode == 0 else "?"
    except OSError:
        return "?"


def _tail_bridge_log(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    tail = lines[-_BRIDGE_LOG_TAIL_LINES:]
    if not tail:
        return
    log.info("gateway bridge log tail (%s lines):", len(tail))
    for line in tail:
        log.info("  bridge: %s", line[:500])


async def run_task(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["taskId"])
    fleet_key: TaskState = {"taskId": task_id, "configId": _FLEET_CONFIG_ID}
    ref = providers.provider_from_payload(payload)
    cfg = config.load_config()
    work_dir = cfg.work_root / task_id
    repo_dir = work_dir / "repo"
    kiro_home = work_dir / "kiro_home"
    agent_dir = work_dir / ".agent"
    stream_log = agent_dir / "kiro-stream.jsonl"
    bridge_log = agent_dir / "gateway-bridge.log"
    branch = str(payload.get("branch") or payload.get("taskBranch") or "").strip()
    if not branch:
        raise ValueError(
            "the invocation payload is missing branch (existing remote branch to check out)"
        )
    base_branch = str(payload.get("defaultBranch") or payload.get("baseBranch") or "main")
    model = str(payload.get("model") or "").strip()
    effort = str(payload.get("effort") or "").strip()

    log.info(
        "task %s repository=%s branch=%s model=%s effort=%s prompt_len=%s",
        task_id,
        ref.url,
        branch,
        kiro_model_for_task(model),
        effort or "(default)",
        len(str(payload.get("prompt") or "")),
    )

    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)

    with _step(fleet_key, "resolve-repository"):
        _ = ref

    token = ""
    with _step(fleet_key, "fetch-credential"):
        token = credentials.fetch_token(ref.provider)

    with _step(fleet_key, "clone"):
        git_ops.clone(
            providers.clone_url(ref),
            repo_dir,
            token=token or None,
            branch=branch,
        )

    with _step(fleet_key, "checkout"):
        git_ops.checkout_branch(repo_dir, branch, token=token or None)
        git_ops.exclude_build_artifacts(repo_dir)
        log.info("task %s HEAD %s", task_id, _git_head_sha(repo_dir))

    setup_command, verify_command = _detect_commands(repo_dir, payload)
    if setup_command:
        with _step(fleet_key, "setup"):
            setup_result = commands.run_setup(setup_command, cwd=repo_dir)
            log.info(
                "setup exit=%s command=%s",
                setup_result.get("exitCode"),
                setup_command[:200],
            )

    prepare_kiro_home(kiro_home)

    store.set_status(fleet_key, "RUNNING", step="kiro")

    user_prompt = _task_prompt(payload, ref, branch, base_branch, verify_command)
    with _step(fleet_key, "kiro"):
        result = run_kiro(
            prompt=user_prompt,
            kiro_home=kiro_home,
            workspace=repo_dir,
            stream_log=stream_log,
            bridge_log=bridge_log,
            git_env=git_ops.kiro_git_env(
                repo=repo_dir,
                token=token or None,
                askpass_dir=agent_dir / "git-askpass",
                username=ref.provider.git_username,
            ),
            model=model,
            effort=effort,
        )

    _tail_bridge_log(bridge_log)

    extra: dict[str, Any] = {
        "durationSeconds": round(result.duration_seconds, 2),
    }
    if result.credits is not None:
        extra["kiroCredits"] = result.credits

    if not result.success:
        store.record_result(
            fleet_key,
            status="FAILED",
            result=result.final_text or None,
            extra={**extra, "error": (result.error or "kiro run failed")[:2000]},
        )
        return {
            "taskId": task_id,
            "status": "FAILED",
            "error": result.error,
            "result": result.final_text,
        }

    with _step(fleet_key, "record-result"):
        store.record_result(
            fleet_key,
            status="SUCCEEDED",
            result=result.final_text or "(no final message from agent)",
            extra=extra,
        )

    return {
        "taskId": task_id,
        "status": "SUCCEEDED",
        "result": result.final_text,
        "kiroCredits": result.credits,
        "durationSeconds": result.duration_seconds,
    }
