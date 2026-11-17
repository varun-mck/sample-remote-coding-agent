"""`rca_mcp.client`: signing for a Lambda function URL."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from rca_mcp import client

_URL = "https://abcdef0123456789abcdef0123456789.lambda-url.eu-west-1.on.aws"


def test_the_region_comes_from_a_function_url_host() -> None:
    assert client._region_from_url(f"{_URL}/") == "eu-west-1"
    assert client._region_from_url("https://api.example.com") is None
    assert client._region_from_url("https://abc.execute-api.eu-west-1.amazonaws.com") is None


def test_an_env_override_signs_in_the_urls_own_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_API_URL", f"{_URL}/")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("RCA_REGION", raising=False)
    assert client.resolve_endpoint() == (_URL, "env", "eu-west-1")


def test_requests_are_signed_for_the_lambda_service(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    api: Any = client.ApiClient(
        endpoint=_URL, region="eu-west-1", transport=httpx.MockTransport(answer)
    )
    assert api.get("/tasks") == {"items": []}
    authorization = seen[0].headers["authorization"]
    assert "/eu-west-1/lambda/aws4_request" in authorization
