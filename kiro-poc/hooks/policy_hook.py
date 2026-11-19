"""Kiro CLI command hook: block git push, rewrite PR args, capture PR URL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

POC_ROOT = Path(__file__).resolve().parents[1]
if str(POC_ROOT) not in sys.path:
    sys.path.insert(0, str(POC_ROOT))

from lib.state import append_hook_log, load, save  # noqa: E402

_SEGMENT_SPLIT = re.compile(r";|&&|\|\||\||\n")
_PUSH_SEGMENT = re.compile(r"^\s*git(?:\s+-\S+)*\s+push\b")
_REMOTE_SEGMENT = re.compile(r"^\s*git(?:\s+-\S+)*\s+remote\b")

CREATE_TOOL_SUBSTRING = "create_pull_request"
MERGE_DENY_WORDS = ("merge", "approve", "delete")


def denies_push(command: str) -> str | None:
    for segment in _SEGMENT_SPLIT.split(command):
        text = segment.strip()
        if _PUSH_SEGMENT.search(text) or _REMOTE_SEGMENT.search(text):
            return text
    return None


def _extract_pr_url(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("html_url", "url", "pullRequestUrl", "prUrl"):
            value = response.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in response.values():
            found = _extract_pr_url(value)
            if found:
                return found
    if isinstance(response, list):
        for item in response:
            found = _extract_pr_url(item)
            if found:
                return found
    if isinstance(response, str):
        if "http" in response:
            for token in response.split():
                if token.startswith("http"):
                    return token.strip("\",'")
        try:
            return _extract_pr_url(json.loads(response))
        except json.JSONDecodeError:
            pass
    return ""


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
            append_hook_log(
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
        append_hook_log(
            state_path,
            {"event": event, "tool": tool_name, "decision": "deny", "reason": "gateway_policy"},
        )
        return out

    if CREATE_TOOL_SUBSTRING in lower:
        original = _tool_input(payload)
        updated = dict(original)
        updated["head"] = branch
        updated["base"] = base
        data = load(state_path)
        updated["draft"] = not bool(data.get("verified", False))
        out = {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "allow",
                "updatedInput": updated,
            }
        }
        append_hook_log(
            state_path,
            {
                "event": event,
                "tool": tool_name,
                "decision": "allow",
                "updatedInput": updated,
            },
        )
        return out

    append_hook_log(state_path, {"event": event, "tool": tool_name, "decision": "passthrough"})
    return {}


def handle_post(payload: dict[str, Any], *, state_path: Path) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    if CREATE_TOOL_SUBSTRING not in tool_name.lower():
        return {}

    response = payload.get("tool_response")
    url = _extract_pr_url(response)
    if url:
        data = load(state_path)
        data["pr_url"] = url
        save(state_path, data)
        append_hook_log(
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
            print(json.dumps({"stderr": "invalid hook stdin JSON"}), file=sys.stderr)
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
            print(json.dumps(result))
        sys.exit(0)

    if event_name in ("PostToolUse", "postToolUse"):
        handle_post(payload, state_path=state_path)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
