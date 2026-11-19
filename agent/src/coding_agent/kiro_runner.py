"""Run Kiro CLI headless and parse stream-json output."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coding_agent.kiro_credentials import fetch_kiro_api_key
from coding_agent.kiro_profile import AGENT_NAME, write_agent_profile
from coding_agent.state_file import load

log = logging.getLogger(__name__)

KIRO_CLI = os.environ.get("KIRO_CLI", "kiro-cli")


@dataclass
class KiroRunResult:
    success: bool
    error: str = ""
    final_text: str = ""
    credits: float | None = None


def _parse_stream_json(lines: list[str]) -> KiroRunResult:
    final_text = ""
    success = False
    error = ""
    credits: float | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type") or event.get("event")
        if kind in ("runFinished", "run_finished"):
            success = bool(event.get("success", True))
            final_text = str(event.get("result") or event.get("message") or final_text)
        if kind in ("runError", "run_error"):
            success = False
            error = str(event.get("message") or event.get("error") or error)
        if kind == "metadata":
            usage = event.get("usage") or event.get("credits")
            if isinstance(usage, dict) and "credits" in usage:
                credits = float(usage["credits"])
            elif isinstance(usage, (int, float)):
                credits = float(usage)
        if kind == "sessionUpdate":
            payload = event.get("payload") or event
            text = payload.get("text") or payload.get("content")
            if isinstance(text, str) and text.strip():
                final_text = text
    return KiroRunResult(success=success, error=error, final_text=final_text, credits=credits)


def prepare_kiro_home(kiro_home: Path) -> None:
    """Empty per-task KIRO_HOME; only the harness `rca` agent profile is written here."""
    if kiro_home.exists():
        shutil.rmtree(kiro_home, ignore_errors=True)
    kiro_home.mkdir(parents=True)


def run_kiro(
    *,
    prompt: str,
    kiro_home: Path,
    state_path: Path,
    workspace: Path,
    context_path: Path,
    provider: Any,
    branch: str,
    base_branch: str,
) -> KiroRunResult:
    write_agent_profile(
        kiro_home,
        state_path=state_path,
        workspace=workspace,
        context_path=context_path,
        provider=provider,
        branch=branch,
        base_branch=base_branch,
    )
    initial = load(state_path)
    state_path.write_text(
        json.dumps({**initial, "branch": branch, "verified": False}, indent=2) + "\n",
        encoding="utf-8",
    )
    api_key = fetch_kiro_api_key()
    env = os.environ.copy()
    env["KIRO_HOME"] = str(kiro_home)
    if api_key:
        env["KIRO_API_KEY"] = api_key
    trust = "read,write,shell,@harness,@gateway"
    cmd = [
        KIRO_CLI,
        "chat",
        "--v3",
        "--no-interactive",
        "--require-mcp-startup",
        "--output-format",
        "stream-json",
        "--trust-tools",
        trust,
        "--agent",
        AGENT_NAME,
        prompt,
    ]
    proc = subprocess.run(
        cmd,
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout_lines = (proc.stdout or "").splitlines()
    result = _parse_stream_json(stdout_lines)
    if proc.returncode != 0 and not result.error:
        result.success = False
        result.error = (proc.stderr or proc.stdout or f"kiro-cli exit {proc.returncode}")[:2000]
    if proc.returncode == 3:
        result.error = "MCP startup failed (exit 3)"
    return result
