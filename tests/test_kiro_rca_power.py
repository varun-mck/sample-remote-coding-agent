"""The `kiro-rca-power` power: its configuration resolution, its signing, its tools, and the
Agent Plugins rules its manifest has to follow for Kiro to load it.

Requests are signed for real against the fake credentials `conftest._aws_env` installs and
captured by an `httpx.MockTransport`, so nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import REPO_ROOT
from kiro_rca_power import client as power_client
from kiro_rca_power import server as power_server
from kiro_rca_power.client import ApiClient, ConfigError, Settings, load_settings

POWER_ROOT = REPO_ROOT / "kiro-rca-power"
_LAMBDA_URL = "https://abc123def.lambda-url.us-east-1.on.aws/"
_OTHER_URL = "https://xyz789.lambda-url.eu-west-1.on.aws/"


def _write(home: Path, name: str, data: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / name).write_text(json.dumps(data), encoding="utf-8")


# -- configuration -------------------------------------------------------------------


def test_power_json_outranks_endpoint_json_and_environment(tmp_path: Path) -> None:
    _write(tmp_path, "endpoint.json", {"apiUrl": _LAMBDA_URL, "region": "us-east-1"})
    _write(tmp_path, "power.json", {"apiUrl": _OTHER_URL, "profile": "team-sso"})
    settings = load_settings(home=tmp_path, environ={"RCA_API_URL": "https://env.example/"})
    assert settings.api_url == _OTHER_URL.rstrip("/")
    assert settings.source == "~/.rca/power.json"
    assert settings.profile == "team-sso"
    # power.json points at another deployment, so endpoint.json's region must not leak in.
    assert settings.region == "eu-west-1"


def test_endpoint_json_outranks_environment(tmp_path: Path) -> None:
    _write(tmp_path, "endpoint.json", {"apiUrl": _LAMBDA_URL})
    settings = load_settings(home=tmp_path, environ={"RCA_API_URL": _OTHER_URL})
    assert settings.api_url == _LAMBDA_URL.rstrip("/")
    assert settings.region == "us-east-1"


def test_environment_is_the_last_resort(tmp_path: Path) -> None:
    settings = load_settings(
        home=tmp_path, environ={"RCA_API_URL": _OTHER_URL, "RCA_AWS_PROFILE": "ops"}
    )
    assert settings.source == "RCA_API_URL"
    assert settings.profile == "ops"


@pytest.mark.parametrize("unset", ["", "   ", "${RCA_API_URL}", "${user_config.api_url}"])
def test_blank_and_unexpanded_values_count_as_absent(tmp_path: Path, unset: str) -> None:
    """A client that passes `mcp.json` env through unexpanded hands over `${...}` literally."""
    _write(tmp_path, "power.json", {"apiUrl": unset, "region": unset})
    _write(tmp_path, "endpoint.json", {"apiUrl": _LAMBDA_URL})
    settings = load_settings(home=tmp_path, environ={"RCA_REGION": unset})
    assert settings.api_url == _LAMBDA_URL.rstrip("/")
    assert settings.region == "us-east-1"


def test_explicit_region_pin_outranks_the_url(tmp_path: Path) -> None:
    _write(tmp_path, "endpoint.json", {"apiUrl": _LAMBDA_URL})
    settings = load_settings(home=tmp_path, environ={"RCA_REGION": "ap-southeast-2"})
    assert settings.region == "ap-southeast-2"


def test_endpoint_region_is_used_when_the_url_names_none(tmp_path: Path) -> None:
    _write(
        tmp_path, "endpoint.json", {"apiUrl": "https://tasks.example.com/", "region": "us-west-2"}
    )
    assert load_settings(home=tmp_path, environ={}).region == "us-west-2"


def test_region_is_read_from_a_lambda_url_host() -> None:
    assert power_client.region_from_url(_LAMBDA_URL) == "us-east-1"
    assert power_client.region_from_url("https://tasks.example.com/") is None


def test_missing_configuration_names_the_fix(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"make deploy.*power\.json"):
        load_settings(home=tmp_path, environ={})


def test_a_plain_http_endpoint_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "power.json", {"apiUrl": "http://insecure.example/"})
    with pytest.raises(ConfigError, match="https://"):
        load_settings(home=tmp_path, environ={})


def test_unreadable_config_names_the_file(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "power.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"power\.json"):
        load_settings(home=tmp_path, environ={})


# -- signing and HTTP ------------------------------------------------------------------


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    url: str = _LAMBDA_URL,
    region: str = "us-east-1",
) -> ApiClient:
    settings = Settings(api_url=url.rstrip("/"), region=region, profile=None, source="test")
    return ApiClient(settings, transport=httpx.MockTransport(handler))


def _recorder(
    response: httpx.Response,
) -> tuple[list[httpx.Request], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return seen, handler


def test_requests_are_signed_for_the_lambda_service() -> None:
    """A Function URL with `AWS_IAM` auth rejects an `execute-api` signature outright."""
    seen, handler = _recorder(httpx.Response(200, json={"items": []}))
    _client(handler).get("/tasks")
    authorization = seen[0].headers["authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 ")
    assert re.search(r"Credential=[^/]+/\d{8}/us-east-1/lambda/aws4_request", authorization)
    assert "x-amz-date" in seen[0].headers


def test_the_signed_body_is_the_body_sent() -> None:
    seen, handler = _recorder(httpx.Response(201, json={"taskId": "tsk_1", "status": "QUEUED"}))
    _client(handler).post("/tasks", {"prompt": "x"})
    assert json.loads(seen[0].content) == {"prompt": "x"}
    assert seen[0].headers["content-type"] == "application/json"


@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
def test_only_get_and_post_are_reachable(method: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("a refused method must not reach the network")

    with pytest.raises(ValueError, match="GET and POST only"):
        _client(handler).request(method, "/tasks/tsk_1")


def test_a_bare_iam_403_is_explained() -> None:
    _, handler = _recorder(httpx.Response(403, json={"Message": "Forbidden"}))
    with pytest.raises(power_client.ApiError, match="lambda:InvokeFunctionUrl") as caught:
        _client(handler).get("/tasks")
    assert caught.value.status == 403


def test_an_api_error_body_is_relayed() -> None:
    body = {"error": "InvalidBody", "message": "repositoryUrl, branch, and prompt are required"}
    _, handler = _recorder(httpx.Response(400, json=body))
    with pytest.raises(power_client.ApiError, match="400 InvalidBody: repositoryUrl"):
        _client(handler).post("/tasks", {})


def test_missing_credentials_name_the_profile_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    _, handler = _recorder(httpx.Response(200, json={}))
    with pytest.raises(ConfigError, match="profile"):
        _client(handler).get("/tasks")


# -- tools ----------------------------------------------------------------------------


def test_submit_task_posts_exactly_the_fleet_contract() -> None:
    task = {"taskId": "tsk_1", "status": "QUEUED", "repositoryUrl": "https://github.com/o/r"}
    seen, handler = _recorder(httpx.Response(201, json=task))
    text, is_error = power_server.call_tool(
        _client(handler),
        "submit_task",
        {
            "repositoryUrl": "https://github.com/o/r",
            "branch": "feat/x",
            "prompt": "update the greeting",
            "baseBranch": "main",
            "verifyCommand": " ",
        },
    )
    assert not is_error, text
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/tasks"
    assert json.loads(seen[0].content) == {
        "repositoryUrl": "https://github.com/o/r",
        "branch": "feat/x",
        "prompt": "update the greeting",
        "baseBranch": "main",
    }
    assert "tsk_1" in text
    assert "in flight" in text


def test_submit_task_refuses_an_owner_repo_shorthand_locally() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("a malformed URL must not reach the network")

    text, is_error = power_server.call_tool(
        _client(handler), "submit_task", {"repositoryUrl": "o/r", "branch": "b", "prompt": "p"}
    )
    assert is_error
    assert "full clone URL" in text


def test_get_task_quotes_the_id_and_renders_the_result() -> None:
    task = {
        "taskId": "tsk_1",
        "status": "SUCCEEDED",
        "steps": ["clone", "kiro", "record-result"],
        "result": "Opened https://github.com/o/r/pull/7",
    }
    seen, handler = _recorder(httpx.Response(200, json=task))
    text, is_error = power_server.call_tool(_client(handler), "get_task", {"taskId": "tsk/1"})
    assert not is_error
    assert seen[0].url.raw_path == b"/tasks/tsk%2F1"
    assert "clone -> kiro -> record-result" in text
    assert "pull/7" in text
    assert "finished" in text


def test_list_tasks_sends_only_the_filters_given() -> None:
    seen, handler = _recorder(httpx.Response(200, json={"items": [], "nextCursor": None}))
    text, _ = power_server.call_tool(_client(handler), "list_tasks", {"status": "RUNNING"})
    assert dict(seen[0].url.params) == {"status": "RUNNING"}
    assert text == "no tasks found"


def test_cancel_task_posts_an_empty_body() -> None:
    seen, handler = _recorder(httpx.Response(200, json={"taskId": "tsk_1", "status": "CANCELLED"}))
    power_server.call_tool(_client(handler), "cancel_task", {"taskId": "tsk_1"})
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/tasks/tsk_1/cancel"
    assert json.loads(seen[0].content) == {}


def test_api_failures_become_tool_errors_not_exceptions() -> None:
    _, handler = _recorder(httpx.Response(404, json={"error": "NotFound", "message": "no task"}))
    text, is_error = power_server.call_tool(_client(handler), "get_task", {"taskId": "tsk_9"})
    assert is_error
    assert text == "404 NotFound: no task"


def test_every_tool_schema_closes_its_arguments() -> None:
    assert [spec.name for spec in power_server.TOOLS] == [
        "submit_task",
        "get_task",
        "list_tasks",
        "cancel_task",
    ]
    for spec in power_server.TOOLS:
        assert spec.input_schema["additionalProperties"] is False, spec.name


# -- the package Kiro installs ---------------------------------------------------------


def test_mcp_json_follows_the_agent_plugins_rules() -> None:
    """Kiro drops a server entry that breaks these, and says little about why."""
    manifest = json.loads((POWER_ROOT / "mcp.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"$schema", "mcpServers"}
    for name, server in manifest["mcpServers"].items():
        assert server["type"] == "stdio", name
        command = server["command"]
        assert command.startswith("./") and " " not in command, name
        assert "${" not in command, f"{name}: placeholders are never expanded in command"
        assert (POWER_ROOT / command).is_file(), f"{name}: {command} is missing"
        env = server.get("env", {})
        assert not {"PLUGIN_ROOT", "PLUGIN_DATA"} & set(env), name
        for value in env.values():
            for placeholder in re.findall(r"\$\{[^}]*\}", value):
                assert placeholder in ("${PLUGIN_ROOT}", "${PLUGIN_DATA}"), (
                    f"{name}: {placeholder} is not expanded by a conforming client"
                )


def test_the_launcher_is_executable() -> None:
    launcher = POWER_ROOT / "bin" / "rca-power"
    assert os.access(launcher, os.X_OK)
    assert launcher.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


def test_plugin_json_carries_the_required_fields() -> None:
    manifest = json.loads((POWER_ROOT / "plugin.json").read_text(encoding="utf-8"))
    for field in ("$schema", "name", "version", "description", "author", "keywords"):
        assert manifest.get(field), field
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", manifest["name"])
    assert manifest["author"].get("name")


def test_every_skill_has_front_matter() -> None:
    skills = sorted((POWER_ROOT / "skills").glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n"), skill
        assert f"name: {skill.parent.name}\n" in text, skill
        assert "\ndescription: " in text, skill
