from __future__ import annotations

from coding_agent.hooks import denies_push


def test_denies_git_push() -> None:
    assert denies_push("git push origin main") is not None


def test_allows_git_status() -> None:
    assert denies_push("git status") is None
