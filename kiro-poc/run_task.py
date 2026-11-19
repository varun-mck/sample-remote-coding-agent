#!/usr/bin/env python3
"""Run one Kiro CLI headless task in a throwaway workspace (POC)."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

POC_ROOT = Path(__file__).resolve().parent
REPO_ROOT = POC_ROOT.parent
FIXTURE_DEFAULT = POC_ROOT / "fixtures" / "sample-repo"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

KIRO_CLI = os.environ.get("KIRO_CLI", "kiro-cli")
_GIT_IDENTITY = ("-c", "user.email=kiro-poc@local", "-c", "user.name=kiro-poc")


def _task_id() -> str:
    return f"poc_{secrets.token_hex(4)}"


def _copy_fixture(fixture: Path, workspace: Path) -> None:
    """Copy fixture tree into workspace root (not nested under fixture name)."""
    for child in fixture.iterdir():
        target = workspace / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def _git(
    workspace: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *_GIT_IDENTITY, *args],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed ({proc.returncode}): {detail}")
    return proc


def _init_git(workspace: Path, branch: str) -> None:
    _git(workspace, "init", "-b", "main")
    _git(workspace, "add", "-A")
    commit = _git(workspace, "commit", "-m", "initial fixture", check=False)
    if commit.returncode != 0:
        combined = f"{commit.stdout}\n{commit.stderr}".lower()
        if "nothing to commit" in combined or "nothing added to commit" in combined:
            _git(workspace, "commit", "--allow-empty", "-m", "initial fixture")
        else:
            raise RuntimeError(
                f"git commit failed ({commit.returncode}): "
                f"{(commit.stderr or commit.stdout).strip()}"
            )
    _git(workspace, "checkout", "-b", branch)


def _hook_command(branch: str, base: str, state: Path) -> list[str]:
    script = POC_ROOT / "hooks" / "policy_hook.py"
    return [
        str(PYTHON),
        str(script),
        f"--state={state}",
        f"--branch={branch}",
        f"--base={base}",
    ]


def write_agent_profile(
    workspace: Path,
    *,
    state: Path,
    branch: str,
    base: str,
) -> Path:
    harness = POC_ROOT / "mcp" / "harness_server.py"
    gateway = POC_ROOT / "mcp" / "gateway_server.py"
    hook_cmd = " ".join(_hook_command(branch, base, state))

    profile = {
        "name": "kiro-poc",
        "description": "POC agent mirroring sample-remote-coding-agent harness policy.",
        "includeMcpJson": False,
        "tools": [
            "read",
            "write",
            "shell",
            "@harness",
            "@harness/run_verification",
            "@harness/publish_branch",
            "@gateway",
            "@gateway/create_pull_request",
        ],
        "mcpServers": {
            "harness": {
                "command": str(PYTHON),
                "args": [
                    str(harness),
                    f"--state={state}",
                    f"--workspace={workspace}",
                ],
            },
            "gateway": {
                "command": str(PYTHON),
                "args": [str(gateway), f"--state={state}"],
            },
        },
        "hooks": [
            {
                "name": "policy-pre-tool",
                "trigger": "PreToolUse",
                "matcher": "*",
                "action": {"type": "command", "command": hook_cmd},
                "timeout": 30,
            },
            {
                "name": "policy-post-tool",
                "trigger": "PostToolUse",
                "matcher": "*",
                "action": {"type": "command", "command": hook_cmd},
                "timeout": 30,
            },
        ],
    }
    agents_dir = workspace / ".kiro" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / "kiro-poc.json"
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return path


def write_fallback_config(
    workspace: Path,
    *,
    state: Path,
    branch: str,
    base: str,
) -> None:
    harness = POC_ROOT / "mcp" / "harness_server.py"
    gateway = POC_ROOT / "mcp" / "gateway_server.py"
    hook_cmd = " ".join(_hook_command(branch, base, state))

    mcp = {
        "mcpServers": {
            "harness": {
                "command": str(PYTHON),
                "args": [
                    str(harness),
                    f"--state={state}",
                    f"--workspace={workspace}",
                ],
            },
            "gateway": {
                "command": str(PYTHON),
                "args": [str(gateway), f"--state={state}"],
            },
        }
    }
    settings = workspace / ".kiro" / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")

    hooks_doc = {
        "version": "v1",
        "hooks": [
            {
                "name": "policy-pre-tool",
                "trigger": "PreToolUse",
                "matcher": "*",
                "action": {"type": "command", "command": hook_cmd},
                "timeout": 30,
            },
            {
                "name": "policy-post-tool",
                "trigger": "PostToolUse",
                "matcher": "*",
                "action": {"type": "command", "command": hook_cmd},
                "timeout": 30,
            },
        ],
    }
    hooks_dir = workspace / ".kiro" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "kiro-poc.json").write_text(
        json.dumps(hooks_doc, indent=2) + "\n", encoding="utf-8"
    )


def parse_stream_json(lines: list[str]) -> dict[str, Any]:
    started = False
    terminal: dict[str, Any] | None = None
    message_chunks: list[str] = []
    session_id: str | None = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}

        if not started:
            if (
                etype == "runStarted"
                and data.get("payloadSchema") == "acp"
                and data.get("acpProtocolVersion") == 1
            ):
                started = True
            continue

        if etype == "runFinished":
            terminal = {
                "type": "runFinished",
                "status": data.get("status"),
                "sessionId": data.get("sessionId"),
                "finalText": data.get("finalText", ""),
            }
            session_id = data.get("sessionId") or session_id
        elif etype == "runError":
            terminal = {
                "type": "runError",
                "stage": data.get("stage"),
                "message": data.get("message", ""),
            }
        elif etype == "sessionUpdate" and isinstance(data.get("update"), dict):
            upd = data["update"]
            if upd.get("type") == "agent_message_chunk":
                chunk = upd.get("text") or upd.get("content")
                if isinstance(chunk, str):
                    message_chunks.append(chunk)
        elif etype == "metadata" and isinstance(data.get("sessionId"), str):
            session_id = data["sessionId"]

    return {
        "started": started,
        "terminal": terminal,
        "messageText": "".join(message_chunks),
        "sessionId": session_id,
    }


def run_kiro(
    workspace: Path,
    prompt: str,
    *,
    use_agent: bool,
    fallback_config: bool,
) -> tuple[int, list[str], str]:
    cmd = [
        KIRO_CLI,
        "chat",
        "--v3",
        "--no-interactive",
        "--require-mcp-startup",
        "--output-format",
        "stream-json",
        "--trust-tools",
        "read,write,shell,@harness,@gateway",
    ]
    if use_agent and not fallback_config:
        cmd.extend(["--agent", "kiro-poc"])
    cmd.append(prompt)

    proc = subprocess.run(
        cmd,
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": os.environ.get("HOME", str(Path.home()))},
    )
    lines = (proc.stdout or "").splitlines()
    stderr = proc.stderr or ""
    return proc.returncode, lines, stderr


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kiro POC task")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--fixture", type=Path, default=FIXTURE_DEFAULT)
    parser.add_argument("--keep", action="store_true", help="Keep workspace; print path")
    parser.add_argument("--fallback-config", action="store_true")
    parser.add_argument("--no-agent-flag", action="store_true", help="Do not pass --agent kiro-poc")
    parser.add_argument("--workspace", type=Path, help="Use existing workspace instead of temp")
    args = parser.parse_args()

    task_id = _task_id()
    branch = f"agent/{task_id}"

    if args.workspace:
        workspace = args.workspace.resolve()
    else:
        workspace = Path(tempfile.mkdtemp(prefix="kiro-poc-"))
        _copy_fixture(args.fixture.resolve(), workspace)

    if not args.workspace:
        _init_git(workspace, branch)

    state_path = workspace / ".agent" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "branch": branch,
                "base_branch": "main",
                "verified": False,
                "published": False,
                "pr_url": None,
                "hook_log": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if args.fallback_config:
        write_fallback_config(workspace, state=state_path, branch=branch, base="main")
    else:
        write_agent_profile(workspace, state=state_path, branch=branch, base="main")

    user_prompt = (
        f"Repository workspace: {workspace}\n"
        f"Task branch: {branch}\n"
        f"Base branch: main\n\n"
        "Use the harness MCP tools run_verification and publish_branch for verify and "
        "commit (never git push). Use gateway create_pull_request to open a PR.\n\n"
        f"{args.prompt}"
    )

    exit_code, lines, stderr = run_kiro(
        workspace,
        user_prompt,
        use_agent=not args.no_agent_flag,
        fallback_config=args.fallback_config,
    )
    stream = parse_stream_json(lines)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}

    summary = {
        "taskId": task_id,
        "workspace": str(workspace),
        "branch": branch,
        "kiroExitCode": exit_code,
        "stream": stream,
        "state": state,
        "stderrTail": stderr[-4000:] if stderr else "",
    }

    print(json.dumps(summary, indent=2))

    if args.keep:
        print(f"workspace kept at: {workspace}", file=sys.stderr)

    if not stream.get("started"):
        sys.exit(2)
    if stream.get("terminal", {}).get("type") == "runError":
        sys.exit(1)
    if exit_code not in (0, 1):
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
