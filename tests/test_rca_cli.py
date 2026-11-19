from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import rca_cli  # noqa: E402


def test_resolve_api_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_API_URL", "https://example.lambda-url.on.aws/")
    assert rca_cli.resolve_api_url() == "https://example.lambda-url.on.aws"


def test_resolve_api_url_from_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RCA_API_URL", raising=False)
    cache = tmp_path / "endpoint.json"
    cache.write_text(json.dumps({"apiUrl": "https://cached.example/"}), encoding="utf-8")
    monkeypatch.setattr(rca_cli, "_CACHE", cache)
    assert rca_cli.resolve_api_url() == "https://cached.example"


def test_sign_request_adds_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = mock.Mock(access_key="AKIA", secret_key="secret", token=None)
    session = mock.Mock()
    session.get_credentials.return_value.get_frozen_credentials.return_value = frozen
    monkeypatch.setattr(rca_cli.boto3, "Session", mock.Mock(return_value=session))

    def _sign(_auth: object, aws_request: object) -> None:
        aws_request.headers["Authorization"] = "AWS4-HMAC-SHA256 test"

    monkeypatch.setattr(rca_cli.SigV4Auth, "add_auth", _sign)
    headers = rca_cli.sign_request("GET", "https://api.example/tasks", None)
    assert headers.get("Authorization") == "AWS4-HMAC-SHA256 test"


def _capture_submit(monkeypatch: pytest.MonkeyPatch, *extra: str) -> dict[str, object]:
    sent: dict[str, object] = {}

    def _request(method: str, path: str, payload: dict[str, object] | None = None) -> object:
        sent.update(payload or {})
        return {"taskId": "tsk_1"}

    monkeypatch.setattr(rca_cli, "request", _request)
    base = ["submit", "--repo", "https://github.com/o/r", "--branch", "b", "--prompt", "p"]
    assert rca_cli.main([*base, *extra]) == 0
    return sent


def test_submit_sends_model_and_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture_submit(monkeypatch, "--model", "claude-sonnet-4.6", "--effort", "high")
    assert sent["model"] == "claude-sonnet-4.6"
    assert sent["effort"] == "high"


def test_submit_omits_model_and_effort_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture_submit(monkeypatch)
    assert "model" not in sent
    assert "effort" not in sent


def test_submit_refuses_an_unknown_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        _capture_submit(monkeypatch, "--effort", "extreme")
