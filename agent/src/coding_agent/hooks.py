"""Policy helpers shared by the Kiro command hook."""

from __future__ import annotations

import re

_SEGMENT_SPLIT = re.compile(r";|&&|\|\||\||\n")
_PUSH_SEGMENT = re.compile(r"^\s*git(?:\s+-\S+)*\s+push\b")
_REMOTE_SEGMENT = re.compile(r"^\s*git(?:\s+-\S+)*\s+remote\b")

CREATE_TOOL_MARKER = "create_pull_request"
MERGE_DENY_WORDS = ("merge", "approve", "delete")


def denies_push(command: str) -> str | None:
    for segment in _SEGMENT_SPLIT.split(command):
        text = segment.strip()
        if _PUSH_SEGMENT.search(text) or _REMOTE_SEGMENT.search(text):
            return text
    return None
