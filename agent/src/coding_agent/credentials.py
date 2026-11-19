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
    name = provider.credential_provider
    table = os.environ.get("TASKS_TABLE", "").strip()
    if not table:
        token = os.environ.get("GIT_TOKEN", "").strip()
        log.info(
            "credential provider=%s source=env found=%s",
            name,
            bool(token),
        )
        return token

    try:
        wl = _client().get_workload_access_token(workloadName=_workload_name())
        token = wl.get("workloadAccessToken", "")
        if not token:
            log.warning("credential provider=%s workload token empty", name)
            return ""
        if provider.kind in ("github", "bitbucket-cloud"):
            resp = _client().get_resource_api_key(
                workloadIdentityToken=token,
                resourceCredentialProviderName=name,
            )
            api_key = str(resp.get("apiKey") or "")
            log.info("credential provider=%s kind=api_key found=%s", name, bool(api_key))
            return api_key
        resp = _client().get_resource_oauth2_token(
            workloadIdentityToken=token,
            resourceCredentialProviderName=name,
            scopes=["repository"],
            oauth2Flow="MACHINE_TO_MACHINE",
        )
        access = str(resp.get("accessToken") or "")
        log.info("credential provider=%s kind=oauth2 found=%s", name, bool(access))
        return access
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        log.warning("credential provider=%s failed code=%s", name, code)
        return ""
    except BotoCoreError:
        log.warning("credential provider=%s failed (botocore)", name, exc_info=True)
        return ""
