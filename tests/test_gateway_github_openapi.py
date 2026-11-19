"""The GitHub gateway target OpenAPI must match GitHub's create-pull-request contract."""

from __future__ import annotations

from stacks.gateway_stack import _GITHUB_OPENAPI

# Fields Kiro passes to github___create_pull_request (see runtime gateway-bridge logs).
_KIRO_TOOL_FIELDS = frozenset({"owner", "repo", "title", "head", "base", "body", "draft"})

# Body fields GitHub documents for POST /repos/{owner}/{repo}/pulls.
_GITHUB_BODY_FIELDS = frozenset(
    {"title", "head", "base", "body", "draft", "maintainer_can_modify", "head_repo", "issue"}
)


def test_create_pull_request_path_and_operation() -> None:
    assert "POST /repos/{owner}/{repo}/pulls" not in _GITHUB_OPENAPI  # YAML uses paths block
    assert "/repos/{owner}/{repo}/pulls:" in _GITHUB_OPENAPI
    assert "operationId: create_pull_request" in _GITHUB_OPENAPI
    assert "https://api.github.com" in _GITHUB_OPENAPI


def test_request_body_lists_properties_kiro_and_github_expect() -> None:
    """A bare `type: object` body (the old placeholder) gives the gateway nothing to map."""
    for field in ("title", "head", "base", "body", "draft"):
        assert f"{field}:" in _GITHUB_OPENAPI
    assert "required:" in _GITHUB_OPENAPI
    assert "- title" in _GITHUB_OPENAPI
    assert "- head" in _GITHUB_OPENAPI
    assert "- base" in _GITHUB_OPENAPI
    assert _KIRO_TOOL_FIELDS.issubset(_GITHUB_BODY_FIELDS | {"owner", "repo"})


def test_error_responses_are_declared() -> None:
    assert '"403":' in _GITHUB_OPENAPI
    assert '"422":' in _GITHUB_OPENAPI
