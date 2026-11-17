"""Git host registry: URL parsing, clone URLs and Gateway tool metadata.

The fleet's allowed hosts are fixed at deploy time (`-c providers=...` in CDK) and at
runtime through `PROVIDERS_JSON`. One module owns both shapes so the harness and the
control plane cannot disagree about what a repository URL means.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

ProviderKind = Literal["github", "bitbucket-cloud", "bitbucket-dc", "mcp"]

_HTTPS = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_SSH = re.compile(r"^git@(?P<host>[^:/]+):(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$")


@dataclass(frozen=True)
class Provider:
    name: str
    kind: ProviderKind
    host: str
    credential_provider: str
    git_username: str
    gateway_target: str
    create_pr_tool: str
    pr_fields: dict[str, str]


@dataclass(frozen=True)
class RepositoryRef:
    provider: Provider
    slug: str
    url: str
    owner: str
    repo: str


def _default_providers() -> tuple[Provider, ...]:
    prefix = os.environ.get("APP_NAME", "rca")
    return (
        Provider(
            name="github",
            kind="github",
            host="github.com",
            credential_provider=f"{prefix}_github",
            git_username="x-access-token",
            gateway_target="github",
            create_pr_tool="create_pull_request",
            pr_fields={
                "repository": "repository",
                "head": "head",
                "base": "base",
                "title": "title",
                "body": "body",
                "draft": "draft",
            },
        ),
        Provider(
            name="bitbucket-cloud",
            kind="bitbucket-cloud",
            host="bitbucket.org",
            credential_provider=f"{prefix}_bitbucket_cloud",
            git_username="x-token-auth",
            gateway_target="bitbucket-cloud",
            create_pr_tool="create_pull_request",
            pr_fields={
                "repository": "repository",
                "head": "source_branch",
                "base": "destination_branch",
                "title": "title",
                "body": "description",
                "draft": "draft",
            },
        ),
    )


def load_providers() -> tuple[Provider, ...]:
    raw = os.environ.get("PROVIDERS_JSON", "").strip()
    if not raw:
        return _default_providers()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("PROVIDERS_JSON must be a JSON array")
    providers: list[Provider] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            raise ValueError("each provider entry must be an object")
        providers.append(
            Provider(
                name=str(entry["name"]),
                kind=cast(ProviderKind, entry.get("kind", "mcp")),
                host=str(entry["host"]),
                credential_provider=str(entry["credentialProvider"]),
                git_username=str(entry.get("gitUsername", "x-access-token")),
                gateway_target=str(entry.get("gatewayTarget", entry["name"])),
                create_pr_tool=str(entry.get("createPrTool", "create_pull_request")),
                pr_fields=dict(entry.get("prFields", {})),
            )
        )
    return tuple(providers)


def provider_for_host(host: str, providers: tuple[Provider, ...] | None = None) -> Provider | None:
    providers = providers or load_providers()
    host = host.lower().split(":")[0]
    for provider in providers:
        if provider.host.lower() == host:
            return provider
    return None


def parse_repository_url(url: str, providers: tuple[Provider, ...] | None = None) -> RepositoryRef:
    text = url.strip()
    match = _HTTPS.match(text) or _SSH.match(text)
    if not match:
        raise ValueError(
            f"{url!r} is not a supported repository URL; expected "
            "https://host/owner/repo or git@host:owner/repo"
        )
    host = match.group("host").lower()
    owner = match.group("owner")
    repo = match.group("repo")
    provider = provider_for_host(host, providers)
    if provider is None:
        raise ValueError(f"no provider configured for host {host!r}")
    slug = f"{owner}/{repo}"
    canonical = f"https://{provider.host}/{slug}"
    return RepositoryRef(provider=provider, slug=slug, url=canonical, owner=owner, repo=repo)


def clone_url(ref: RepositoryRef) -> str:
    return f"https://{ref.provider.host}/{ref.slug}.git"


def gateway_tool_prefix(provider: Provider) -> str:
    return f"mcp__gateway_{provider.gateway_target}__"


def provider_from_payload(payload: dict[str, Any]) -> RepositoryRef:
    url = str(payload.get("repositoryUrl") or "").strip()
    if url:
        return parse_repository_url(url)
    repository = str(payload.get("repository") or "").strip()
    if "/" not in repository:
        raise ValueError("repository must be owner/repo or pass repositoryUrl")
    return parse_repository_url(f"https://github.com/{repository}")
