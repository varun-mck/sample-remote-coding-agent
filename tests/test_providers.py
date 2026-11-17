"""Repository URL parsing for configured git hosts."""

from __future__ import annotations

import pytest
from coding_agent.providers import Provider, parse_repository_url


def _providers() -> tuple[Provider, ...]:
    from coding_agent.providers import _default_providers

    bitbucket_dc = Provider(
        name="bitbucket-dc",
        kind="bitbucket-dc",
        host="git.example.com",
        credential_provider="rca_bb_dc",
        git_username="x-token-auth",
        gateway_target="bitbucket-dc",
        create_pr_tool="create_pull_request",
        pr_fields={},
    )
    return (*_default_providers(), bitbucket_dc)


def test_github_https_and_ssh() -> None:
    providers = _providers()
    for url in (
        "https://github.com/acme/widgets",
        "https://github.com/acme/widgets.git",
        "git@github.com:acme/widgets.git",
    ):
        ref = parse_repository_url(url, providers)
        assert ref.slug == "acme/widgets"
        assert ref.provider.kind == "github"


def test_bitbucket_cloud_urls() -> None:
    ref = parse_repository_url("https://bitbucket.org/team/app", _providers())
    assert ref.slug == "team/app"
    assert ref.provider.kind == "bitbucket-cloud"


def test_bitbucket_dc_custom_host() -> None:
    ref = parse_repository_url("https://git.example.com/proj/service", _providers())
    assert ref.slug == "proj/service"
    assert ref.provider.kind == "bitbucket-dc"


def test_unknown_host_is_refused() -> None:
    with pytest.raises(ValueError, match="no provider configured"):
        parse_repository_url("https://gitlab.com/o/r", _providers())
