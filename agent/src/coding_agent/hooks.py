"""Policy hooks: no direct git push, and pull requests pinned to this task."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast

from claude_agent_sdk import HookContext, HookJSONOutput, HookMatcher
from claude_agent_sdk.types import HookEvent, HookInput

from .providers import Provider, gateway_tool_prefix
from .task_context import TaskContext

_SEGMENT_SPLIT = re.compile(r";|&&|\|\||\||\n")
_PUSH_SEGMENT = re.compile(r"^\s*git(?:\s+-\S+)*\s+push\b")
_REMOTE_SEGMENT = re.compile(r"^\s*git(?:\s+-\S+)*\s+remote\b")


def denies_push(command: str) -> str | None:
    for segment in _SEGMENT_SPLIT.split(command):
        text = segment.strip()
        if _PUSH_SEGMENT.search(text) or _REMOTE_SEGMENT.search(text):
            return text
    return None


def build_hooks(ctx: TaskContext, provider: Provider) -> dict[HookEvent, list[HookMatcher]]:  # noqa: C901
    prefix = gateway_tool_prefix(provider)
    create_tool = f"{prefix}{provider.create_pr_tool}"

    async def pre_tool_use(
        hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        inp = cast(Mapping[str, Any], hook_input)
        tool_name = str(inp.get("tool_name", ""))
        if tool_name == "Bash":
            tool_payload = inp.get("tool_input")
            command = str(
                tool_payload.get("command", "") if isinstance(tool_payload, Mapping) else ""
            )
            blocked = denies_push(command)
            if blocked:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "git push and git remote are denied; call publish_branch instead."
                        ),
                    }
                }
        if tool_name == create_tool:
            tool_input = inp.get("tool_input")
            original = dict(tool_input) if isinstance(tool_input, Mapping) else {}
            updated = dict(original)
            fields = provider.pr_fields
            if "head" in fields:
                updated[fields["head"]] = ctx.branch
            if "base" in fields:
                updated[fields["base"]] = ctx.base_branch
            if "repository" in fields:
                updated[fields["repository"]] = ctx.ref.slug
            if "draft" in fields:
                updated[fields["draft"]] = not ctx.verified
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated,
                }
            }
        if tool_name.startswith(prefix) and any(
            word in tool_name.lower() for word in ("merge", "approve", "delete")
        ):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "merge and approve are not allowed on this agent.",
                }
            }
        return {}

    async def post_tool_use(
        hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        inp = cast(Mapping[str, Any], hook_input)
        tool_name = str(inp.get("tool_name", ""))
        if tool_name == create_tool:
            response = inp.get("tool_response")
            url = _extract_pr_url(response)
            if url:
                ctx.pr_url = url
                ctx.pr_tool = create_tool
        return {}

    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
    }


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
    if isinstance(response, str) and "http" in response:
        for token in response.split():
            if token.startswith("http"):
                return token.strip("\",'")
    return ""
