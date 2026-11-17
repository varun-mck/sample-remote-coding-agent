"""Short-lived Git credentials from AgentCore Identity. Never cached."""

from __future__ import annotations

import logging
import os

from botocore.exceptions import BotoCoreError, ClientError

from .providers import Provider

log = logging.getLogger(__name__)

_DEFAULT_WORKLOAD = "rca_git_wl"


def _client():
    import boto3

    return boto3.client("bedrock-agentcore")


def _workload_name() -> str:
    return os.environ.get("IDENTITY_WORKLOAD_NAME", _DEFAULT_WORKLOAD).strip() or _DEFAULT_WORKLOAD


def fetch_token(provider: Provider) -> str:
    """Return an access token or API key for `provider`, or ``\"\"`` when unavailable."""
    table = os.environ.get("TASKS_TABLE", "").strip()
    if not table:
        return os.environ.get("GIT_TOKEN", "").strip()

    name = provider.credential_provider
    try:
        wl = _client().get_workload_access_token(workloadName=_workload_name())
        token = wl.get("workloadAccessToken", "")
        if not token:
            return ""
        # GitHub and Bitbucket Cloud both use an AgentCore Identity *API key* credential
        # provider (PAT / app password), not OAuth or a GitHub App install flow.
        if provider.kind in ("github", "bitbucket-cloud"):
            resp = _client().get_resource_api_key(
                workloadIdentityToken=token,
                resourceCredentialProviderName=name,
                scopes=["repository"],
            )
            return str(resp.get("apiKey") or "")
        resp = _client().get_resource_oauth2_token(
            workloadIdentityToken=token,
            resourceCredentialProviderName=name,
            scopes=["repository"],
            oauth2Flow="MACHINE_TO_MACHINE",
        )
        return str(resp.get("accessToken") or "")
    except (ClientError, BotoCoreError):
        log.warning("could not fetch credential for provider %s", name, exc_info=True)
        return ""
