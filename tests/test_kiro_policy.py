from __future__ import annotations

from coding_agent.hooks import CREATE_TOOL_MARKER, denies_push


def test_denies_git_push() -> None:
    assert denies_push("git push origin main") is not None
    assert denies_push("echo hello") is None


def test_create_pr_marker_is_stable() -> None:
    assert CREATE_TOOL_MARKER == "create_pull_request"
