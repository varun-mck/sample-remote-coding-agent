"""`router.py`: the function URL's entry point, which invokes each route's own Lambda."""

from __future__ import annotations

import io
import json
from types import ModuleType
from typing import Any

import pytest
from conftest import api_event
from test_api_fakes import load_handlers as _load

_TARGETS = {
    "POST /tasks": "arn:aws:lambda:us-east-1:123456789012:function:create",
    "GET /tasks": "arn:aws:lambda:us-east-1:123456789012:function:list",
    "GET /tasks/{taskId}": "arn:aws:lambda:us-east-1:123456789012:function:get",
    "POST /tasks/{taskId}/cancel": "arn:aws:lambda:us-east-1:123456789012:function:cancel",
}


class FakeLambda:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, *, FunctionName: str, Payload: bytes) -> dict[str, Any]:  # noqa: N803
        self.calls.append((FunctionName, json.loads(Payload)))
        return dict(self.result)


def _function_url_event(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """What a function URL sends: payload 2.0 with `$default` and no `pathParameters`."""
    event = api_event(method, path, **kwargs)
    event["routeKey"] = "$default"
    return event


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("ROUTE_TARGETS", json.dumps(_TARGETS))
    _common, module = _load("router")
    module._targets.cache_clear()
    return module


def _install(
    monkeypatch: pytest.MonkeyPatch, router: ModuleType, result: dict[str, Any]
) -> FakeLambda:
    fake = FakeLambda(result)
    monkeypatch.setattr(router, "_lambda", lambda: fake)
    return fake


def _ok(body: Any, status: int = 200) -> dict[str, Any]:
    payload = {"statusCode": status, "headers": {}, "body": json.dumps(body)}
    return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(payload).encode())}


def test_a_templated_path_is_forwarded_with_its_path_parameters_and_route_key(
    router: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, router, _ok({"taskId": "tsk_1"}))
    out = router.lambda_handler(_function_url_event("POST", "/tasks/tsk_1/cancel"))
    assert out["statusCode"] == 200
    assert json.loads(out["body"]) == {"taskId": "tsk_1"}
    [(target, forwarded)] = fake.calls
    assert target == _TARGETS["POST /tasks/{taskId}/cancel"]
    assert forwarded["routeKey"] == "POST /tasks/{taskId}/cancel"
    assert forwarded["pathParameters"] == {"taskId": "tsk_1"}


def test_a_route_without_parameters_forwards_none(
    router: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, router, _ok({"items": []}))
    router.lambda_handler(_function_url_event("GET", "/tasks", query={"limit": "5"}))
    [(target, forwarded)] = fake.calls
    assert target == _TARGETS["GET /tasks"]
    assert "pathParameters" not in forwarded
    assert forwarded["queryStringParameters"] == {"limit": "5"}


def test_a_trailing_slash_matches_the_same_route(
    router: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, router, _ok({"items": []}))
    router.lambda_handler(_function_url_event("GET", "/tasks/"))
    assert fake.calls[0][0] == _TARGETS["GET /tasks"]


def test_an_unknown_path_is_404_and_invokes_nothing(
    router: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, router, _ok({}))
    out = router.lambda_handler(_function_url_event("GET", "/configurations"))
    assert out["statusCode"] == 404
    assert json.loads(out["body"])["error"] == "NotFound"
    assert fake.calls == []


def test_a_known_path_under_another_method_is_405(
    router: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, router, _ok({}))
    out = router.lambda_handler(_function_url_event("DELETE", "/tasks/tsk_1"))
    assert out["statusCode"] == 405
    assert fake.calls == []


def test_a_route_that_crashed_is_a_500_without_its_stack_trace(
    router: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = json.dumps({"errorMessage": "ghp_abcdefghijkl leaked", "stackTrace": []})
    _install(
        monkeypatch,
        router,
        {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": io.BytesIO(trace.encode())},
    )
    out = router.lambda_handler(_function_url_event("GET", "/tasks"))
    assert out["statusCode"] == 500
    assert "ghp_" not in out["body"]


def test_an_invoke_failure_is_a_500(router: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def invoke(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("throttled")

    monkeypatch.setattr(router, "_lambda", lambda: Broken())
    out = router.lambda_handler(_function_url_event("GET", "/tasks"))
    assert out["statusCode"] == 500


def test_the_lambda_client_never_retries_an_invoke(router: ModuleType) -> None:
    """A retried `POST /tasks` that had already queued its task would queue a second one."""
    router._lambda.cache_clear()
    config = router._lambda().meta.config
    assert config.retries["total_max_attempts"] == 1
    assert config.read_timeout > 30
    router._lambda.cache_clear()
