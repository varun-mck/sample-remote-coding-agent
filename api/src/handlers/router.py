"""The function URL's one entry point: match the route, then invoke the Lambda that owns it.

The task API is a Lambda function URL with `AWS_IAM` auth rather than an API Gateway HTTP
API, because the organisation's SCPs deny `apigateway:*` outright. A function URL fronts
exactly one function, and each route keeps its own Lambda -- one function serving all four
would hold the union of their grants, which is how a read route quietly acquires a write --
so this function holds `lambda:InvokeFunction` on those four and nothing else.

A function URL event is the HTTP API payload 2.0 with `routeKey` `$default` and no
`pathParameters`, so this fills both in before forwarding: the route handlers read the
event exactly as they did behind API Gateway.
"""

from __future__ import annotations

import functools
import json
import os
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any

import boto3
from botocore.config import Config

from . import _common
from ._common import LOG, ApiError


@functools.lru_cache(maxsize=1)
def _targets() -> dict[str, str]:
    """`{"METHOD /path/{param}": function ARN}`, as `ApiStack` writes `ROUTE_TARGETS`."""
    parsed = json.loads(os.environ.get("ROUTE_TARGETS") or "{}")
    if not isinstance(parsed, dict):
        raise TypeError("ROUTE_TARGETS must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


@functools.lru_cache(maxsize=1)
def _lambda() -> Any:
    """One attempt and no retries: a retried `POST /tasks` that had already queued its task
    would queue a second one. The read timeout outlasts the longest route timeout (30s)."""
    return boto3.session.Session().client(
        "lambda", config=Config(read_timeout=35, retries={"total_max_attempts": 1})
    )


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _bind(template: str, path: str) -> dict[str, str] | None:
    """The path parameters `path` binds against `template`, or `None` if it does not fit."""
    wanted, given = _segments(template), _segments(path)
    if len(wanted) != len(given):
        return None
    bound: dict[str, str] = {}
    for pattern, segment in zip(wanted, given, strict=True):
        if pattern.startswith("{") and pattern.endswith("}"):
            bound[pattern[1:-1]] = urllib.parse.unquote(segment)
        elif pattern != segment:
            return None
    return bound


def match(route_keys: Iterable[str], method: str, path: str) -> tuple[str, dict[str, str]]:
    """`(route key, path parameters)` for a request. A path no route has is a 404; a path
    some route has under another method is a 405, which is the distinction API Gateway made."""
    path_exists = False
    for key in route_keys:
        route_method, template = key.split(" ", 1)
        bound = _bind(template, path)
        if bound is None:
            continue
        if route_method == method:
            return key, bound
        path_exists = True
    if path_exists:
        raise ApiError(405, "MethodNotAllowed", f"{method} is not allowed on {path}")
    raise ApiError(404, "NotFound", f"no route for {method} {path}")


def lambda_handler(event: Mapping[str, Any], _context: Any = None) -> dict[str, Any]:
    request = event.get("requestContext", {})
    http = request.get("http", {})
    method = str(http.get("method") or "")
    path = str(http.get("path") or event.get("rawPath") or "/")
    LOG.info("%s %s request=%s", method, path, request.get("requestId"))
    targets = _targets()
    try:
        key, bound = match(targets, method, path)
    except ApiError as err:
        return _common.response(err.status, err.body)

    forwarded = {**event, "routeKey": key}
    if bound:
        forwarded["pathParameters"] = bound
    try:
        result = _lambda().invoke(FunctionName=targets[key], Payload=json.dumps(forwarded).encode())
        payload = result["Payload"].read()
        if result.get("FunctionError"):
            # The route's own `@handler` turns every exception into a response, so this is a
            # timeout, an out-of-memory kill or an import failure. The payload is not logged:
            # it is a stack trace, which is one of the places a request's token turns up.
            LOG.error("%s %s: %s failed (%s)", method, path, key, result["FunctionError"])
            return _common.response(500, {"error": "InternalError", "message": "unexpected error"})
        response = json.loads(payload)
    except Exception:
        LOG.exception("%s %s: invoking %s failed", method, path, key)
        return _common.response(500, {"error": "InternalError", "message": "unexpected error"})
    if not isinstance(response, dict):
        LOG.error("%s %s: %s returned a %s", method, path, key, type(response).__name__)
        return _common.response(500, {"error": "InternalError", "message": "unexpected error"})
    return response
