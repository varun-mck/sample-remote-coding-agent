# ruff: noqa: T201 -- this is a CLI; its printed output is the product, not a debug aid.
"""Run the AIPDLC harness locally against a repository URL."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import time
from typing import Any

from . import harness

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m coding_agent.local_run",
        description="Run one task through the harness (no AgentCore control plane).",
    )
    parser.add_argument(
        "--repository-url",
        required=True,
        help="https://host/owner/repo or git@host:owner/repo.git",
    )
    parser.add_argument("--prompt", required=True, help="what the agent should do")
    parser.add_argument("--task-id", default=None, help="default: synthetic tsk_<millis>_<hex>")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--verify-command", default="")
    parser.add_argument("--setup-command", default="")
    return parser


def _synthetic_payload(args: argparse.Namespace) -> dict[str, Any]:
    task_id = args.task_id or f"tsk_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    payload: dict[str, Any] = {
        "taskId": task_id,
        "prompt": args.prompt,
        "repositoryUrl": args.repository_url,
        "defaultBranch": args.base_branch,
    }
    if args.verify_command.strip():
        payload["verifyCommand"] = args.verify_command.strip()
    if args.setup_command.strip():
        payload["setupCommand"] = args.setup_command.strip()
    return payload


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    return await harness.run_task(payload)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    payload = _synthetic_payload(args)
    print(f"task {payload['taskId']} on {payload['repositoryUrl']}")
    result = asyncio.run(_run(payload))
    print(json.dumps(result, indent=2, default=str))
    status = str(result.get("status") or "")
    return 0 if status.startswith("SUCCEEDED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
