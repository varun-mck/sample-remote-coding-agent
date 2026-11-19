"""Stdio MCP server that proxies tool calls to the fleet AgentCore Gateway (/mcp, IAM SigV4)."""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any

import anyio
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

log = logging.getLogger(__name__)

SERVER_NAME = "agentcore-gateway"
_SIGV4_SERVICE = "bedrock-agentcore"
_BRIDGE_LOG_ENV = "RCA_BRIDGE_LOG"


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path = os.environ.get(_BRIDGE_LOG_ENV, "").strip()
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s gateway-bridge %(message)s",
        handlers=handlers,
        force=True,
    )


class _SigV4Auth(httpx.Auth):
    def __init__(self, *, region: str, service: str = _SIGV4_SERVICE) -> None:
        self._region = region
        self._service = service
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            msg = "no AWS credentials available for AgentCore Gateway SigV4"
            raise RuntimeError(msg)
        self._credentials = creds.get_frozen_credentials()

    def auth_flow(self, request: httpx.Request):
        headers = dict(request.headers)
        body = request.content
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers=headers,
        )
        SigV4Auth(self._credentials, self._service, self._region).add_auth(aws_request)
        request.headers.clear()
        request.headers.update(dict(aws_request.headers))
        yield request


def gateway_mcp_url() -> str:
    base = os.environ.get("BEDROCK_AGENTCORE_GATEWAY_URL", "").strip().rstrip("/")
    if not base:
        msg = "BEDROCK_AGENTCORE_GATEWAY_URL is not set"
        raise RuntimeError(msg)
    return f"{base}/mcp"


def _aws_region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
        or "us-east-1"
    )


def build_proxy_server(session: ClientSession) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        started = time.monotonic()
        result = await session.list_tools()
        tools = list(result.tools)
        log.info("list_tools count=%s duration=%.2fs", len(tools), time.monotonic() - started)
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = arguments or {}
        arg_keys = sorted(args.keys())
        started = time.monotonic()
        try:
            result: CallToolResult = await session.call_tool(name, args)
            is_error = bool(getattr(result, "isError", False))
            log.info(
                "call_tool name=%s arg_keys=%s isError=%s duration=%.2fs",
                name,
                arg_keys,
                is_error,
                time.monotonic() - started,
            )
            return [block for block in result.content if isinstance(block, TextContent)]
        except Exception:
            log.exception(
                "call_tool name=%s arg_keys=%s failed after %.2fs",
                name,
                arg_keys,
                time.monotonic() - started,
            )
            raise

    return server


async def _run() -> None:
    url = gateway_mcp_url()
    region = _aws_region()
    log.info("connecting gateway url=%s region=%s", url, region)
    auth = _SigV4Auth(region=region)
    timeout = httpx.Timeout(120.0, read=300.0)
    async with (
        httpx.AsyncClient(auth=auth, timeout=timeout) as http_client,
        streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
            _,
        ),
        ClientSession(read_stream, write_stream) as session,
        stdio_server() as (stdio_read, stdio_write),
    ):
        await session.initialize()
        proxy = build_proxy_server(session)
        init_options = proxy.create_initialization_options()
        await proxy.run(stdio_read, stdio_write, init_options)


def main() -> None:
    _configure_logging()
    anyio.run(_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proxy stdio MCP to AgentCore Gateway")
    _ = parser.parse_args()
    main()
