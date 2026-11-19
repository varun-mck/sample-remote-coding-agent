"""Kiro CLI command hook: block git push, rewrite PR args, capture PR URL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from coding_agent import state_file
from coding_agent.hooks import CREATE_TOOL_MARKER, MERGE_DENY_WORDS, denies_push
from coding_agent.mcp._pr_url import extract_pr_url

CREATE_TOOL_SUBSTRING = CREATE_TOOL_MARKER


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("tool_input")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _shell_command(payload: dict[str, Any]) -> str:
    tool_input = _tool_input(payload)
    return str(tool_input.get("command") or tool_input.get("cmd") or "")


def handle_pre(
    payload: dict[str, Any],
    *,
    state_path: Path,
    branch: str,
    base: str,
) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    event = str(payload.get("hook_event_name") or "PreToolUse")

    if tool_name in ("execute_bash", "shell") or "bash" in tool_name.lower():
        blocked = denies_push(_shell_command(payload))
        if blocked:
            out = {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "git push and git remote are denied; call publish_branch instead."
                    ),
                }
            }
            state_file.append_hook_log(
                state_path,
                {"event": event, "tool": tool_name, "decision": "deny", "reason": "git_push"},
            )
            return out

    lower = tool_name.lower()
    if any(word in lower for word in MERGE_DENY_WORDS) and CREATE_TOOL_SUBSTRING not in lower:
        out = {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": "merge and approve are not allowed on this agent.",
            }
        }
        state_file.append_hook_log(
            state_path,
            {"event": event, "tool": tool_name, "decision": "deny", "reason": "gateway_policy"},
        )
        return out

    if CREATE_TOOL_SUBSTRING in lower:
        original = _tool_input(payload)
        updated = dict(original)
        updated["head"] = branch
        updated["base"] = base
        data = state_file.load(state_path)
        updated["draft"] = not bool(data.get("verified", False))
        out = cast(
            dict[str, Any],
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": "allow",
                    "updatedInput": updated,
                }
            },
        )
        state_file.append_hook_log(
            state_path,
            {"event": event, "tool": tool_name, "decision": "allow", "updatedInput": updated},
        )
        return out

    state_file.append_hook_log(
        state_path, {"event": event, "tool": tool_name, "decision": "passthrough"}
    )
    return {}


def handle_post(payload: dict[str, Any], *, state_path: Path) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    if CREATE_TOOL_SUBSTRING not in tool_name.lower():
        return {}

    response = payload.get("tool_response")
    url = extract_pr_url(response)
    if url:
        data = state_file.load(state_path)
        data["pr_url"] = url
        state_file.save(state_path, data)
        state_file.append_hook_log(
            state_path,
            {"event": "PostToolUse", "tool": tool_name, "pr_url": url},
        )
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base", default="main")
    args = parser.parse_args()

    raw = sys.stdin.read()
    payload: dict[str, Any] = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            sys.stderr.write(json.dumps({"stderr": "invalid hook stdin JSON"}) + "\n")
            sys.exit(2)

    event_name = str(payload.get("hook_event_name") or "")
    state_path = args.state.resolve()

    if event_name in ("PreToolUse", "preToolUse"):
        result = handle_pre(
            payload,
            state_path=state_path,
            branch=args.branch,
            base=args.base,
        )
        if result:
            sys.stdout.write(json.dumps(result) + "\n")
        sys.exit(0)

    if event_name in ("PostToolUse", "postToolUse"):
        handle_post(payload, state_path=state_path)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
