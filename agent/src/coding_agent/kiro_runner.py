"""Run Kiro CLI headless and parse stream-json output."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coding_agent.kiro_credentials import fetch_kiro_api_key
from coding_agent.kiro_profile import AGENT_NAME, write_agent_profile

log = logging.getLogger(__name__)

KIRO_CLI = os.environ.get("KIRO_CLI", "kiro-cli")
_SUMMARY_TAIL = 120


@dataclass
class KiroRunResult:
    success: bool
    error: str = ""
    final_text: str = ""
    credits: float | None = None
    duration_seconds: float = 0.0


def _log_stream_event(event: dict[str, Any]) -> None:
    kind = event.get("type") or event.get("event") or "?"
    if kind in ("runFinished", "run_finished"):
        log.info("kiro stream: runFinished success=%s", event.get("success"))
        return
    if kind in ("runError", "run_error"):
        log.warning(
            "kiro stream: runError %s",
            str(event.get("message") or event.get("error") or "")[:_SUMMARY_TAIL],
        )
        return
    if kind == "metadata":
        usage = event.get("usage") or event.get("credits")
        if usage:
            log.info("kiro stream: metadata usage=%s", usage)
        return
    payload = event.get("payload") or event.get("data") or event
    if isinstance(payload, dict):
        tool_name = payload.get("tool_name") or payload.get("toolName")
        if tool_name:
            status = payload.get("status") or payload.get("tool_status") or "?"
            log.info("kiro stream: tool %s status=%s", tool_name, status)
            return
        text = payload.get("text") or payload.get("content")
        if isinstance(text, str) and text.strip():
            snippet = text.strip().replace("\n", " ")[:_SUMMARY_TAIL]
            log.info("kiro stream: assistant …%s", snippet)
            return
    log.debug("kiro stream: %s", kind)


def parse_stream_json(lines: list[str]) -> KiroRunResult:
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
        _log_stream_event(event)
        kind = event.get("type") or event.get("event")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind in ("runFinished", "run_finished"):
            success = bool(event.get("success", data.get("status") not in ("error", "failed")))
            final_text = str(
                event.get("result") or event.get("message") or data.get("finalText") or final_text
            )
        if kind in ("runError", "run_error"):
            success = False
            error = str(event.get("message") or event.get("error") or data.get("message") or error)
        if kind == "metadata":
            usage = event.get("usage") or event.get("credits") or data.get("usage")
            if isinstance(usage, dict) and "credits" in usage:
                credits = float(usage["credits"])
            elif isinstance(usage, (int, float)):
                credits = float(usage)
        if kind == "sessionUpdate":
            payload = event.get("payload") or data.get("update") or data or event
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
    workspace: Path,
    stream_log: Path,
    bridge_log: Path,
) -> KiroRunResult:
    write_agent_profile(kiro_home)
    api_key = fetch_kiro_api_key()
    env = os.environ.copy()
    env["KIRO_HOME"] = str(kiro_home)
    env["RCA_BRIDGE_LOG"] = str(bridge_log)
    if api_key:
        env["KIRO_API_KEY"] = api_key
    trust = "read,write,shell,@agentcore-gateway"
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
    log.info(
        "kiro-cli starting agent=%s prompt_bytes=%s cwd=%s",
        AGENT_NAME,
        len(prompt.encode("utf-8")),
        workspace,
    )
    stream_log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stdout_lines: list[str] = []
    with (
        stream_log.open("w", encoding="utf-8") as stream_fh,
        subprocess.Popen(
            cmd,
            cwd=workspace,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        ) as proc,
    ):
        assert proc.stdout is not None
        assert proc.stderr is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            stream_fh.write(line)
            stream_fh.flush()
            stdout_lines.append(line.rstrip("\n"))
        stderr_text = proc.stderr.read()
        returncode = proc.wait()
    duration = time.monotonic() - started
    for err_line in stderr_text.splitlines():
        if err_line.strip():
            log.warning("kiro-cli stderr: %s", err_line[:500])
    result = parse_stream_json(stdout_lines)
    result.duration_seconds = duration
    log.info("kiro-cli finished exit=%s duration=%.1fs", returncode, duration)
    if returncode != 0 and not result.error:
        result.success = False
        result.error = (
            stderr_text or "\n".join(stdout_lines[-3:]) or f"kiro-cli exit {returncode}"
        )[:2000]
    if returncode == 3:
        result.success = False
        result.error = "MCP startup failed (exit 3)"
    if not result.success and not result.error:
        result.error = "kiro run did not report success"
    return result
