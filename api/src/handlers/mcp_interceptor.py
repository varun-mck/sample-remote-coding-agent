"""REQUEST/RESPONSE interceptor for the Cognito-backed task gateway."""

from __future__ import annotations

import base64
import binascii
import json
import os
from collections.abc import Mapping
from typing import Any

from ._common import LOG

VERSION = "1.0"
IDENTITY_FIELD = "submittedBy"


def _header(headers: Mapping[str, Any], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted and isinstance(value, str):
            return value
    return ""


def token_claims(headers: Mapping[str, Any]) -> dict[str, Any]:
    auth = _header(headers, "authorization").strip()
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def pool_user(claims: Mapping[str, Any], *, client_id: str) -> str:
    """The pool user's sign-in name from a Cognito access token, or empty."""
    if claims.get("token_use") != "access" or not client_id:
        return ""
    if claims.get("client_id") != client_id:
        return ""
    username = claims.get("username")
    return username.strip() if isinstance(username, str) else ""


def _refusal(body: Mapping[str, Any], message: str) -> dict[str, Any]:
    return {
        "interceptorOutputVersion": VERSION,
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "body": {
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {"content": [{"type": "text", "text": message}], "isError": True},
                },
            }
        },
    }


def hide_identity_field(result: Mapping[str, Any]) -> dict[str, Any]:
    tools = []
    for tool in result.get("tools") or []:
        if not isinstance(tool, Mapping):
            tools.append(tool)
            continue
        schema = dict(tool.get("inputSchema") or {})
        properties = {
            key: value
            for key, value in (schema.get("properties") or {}).items()
            if key != IDENTITY_FIELD
        }
        if "properties" in schema:
            schema["properties"] = properties
        if "required" in schema:
            schema["required"] = [name for name in schema["required"] if name != IDENTITY_FIELD]
        tools.append({**tool, "inputSchema": schema})
    return {**result, "tools": tools}


def _on_request(mcp: Mapping[str, Any]) -> dict[str, Any]:
    request = mcp.get("gatewayRequest") or {}
    body = dict(request.get("body") or {})
    if body.get("method") == "tools/call":
        user = pool_user(
            token_claims(request.get("headers") or {}),
            client_id=os.environ.get("KIRO_CLIENT_ID", "").strip(),
        )
        if not user:
            LOG.warning("tools/call refused: token does not name a Cognito user")
            return _refusal(body, "Sign in again; this Cognito token does not name a user.")
        params = dict(body.get("params") or {})
        arguments = dict(params.get("arguments") or {})
        arguments[IDENTITY_FIELD] = user
        params["arguments"] = arguments
        body["params"] = params
    return {
        "interceptorOutputVersion": VERSION,
        "mcp": {"transformedGatewayRequest": {"body": body}},
    }


def _on_response(mcp: Mapping[str, Any]) -> dict[str, Any]:
    request_body = (mcp.get("gatewayRequest") or {}).get("body") or {}
    response = mcp.get("gatewayResponse") or {}
    body = response.get("body") or {}
    if request_body.get("method") == "tools/list" and isinstance(body, Mapping):
        result = body.get("result")
        if isinstance(result, Mapping):
            body = {**body, "result": hide_identity_field(result)}
    return {
        "interceptorOutputVersion": VERSION,
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": response.get("statusCode") or 200,
                "body": body,
            }
        },
    }


def lambda_handler(event: Mapping[str, Any], _context: Any = None) -> dict[str, Any]:
    mcp = event.get("mcp") or {}
    # REQUEST events may carry `"gatewayResponse": null`; only a value marks a RESPONSE.
    if mcp.get("gatewayResponse") is not None:
        return _on_response(mcp)
    return _on_request(mcp)
