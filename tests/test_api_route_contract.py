from __future__ import annotations

import importlib

from handlers import dispatcher, tasks


def test_task_routes_are_declared() -> None:
    assert ("POST", "/tasks") in tasks.ROUTES
    assert ("GET", "/tasks/{taskId}") in tasks.ROUTES


def test_dispatcher_has_no_http_routes() -> None:
    assert not hasattr(dispatcher, "ROUTES")
    mod = importlib.import_module("handlers.dispatcher")
    assert callable(mod.lambda_handler)
