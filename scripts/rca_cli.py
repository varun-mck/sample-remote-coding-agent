#!/usr/bin/env python3
# ruff: noqa: T201
"""Submit and poll remote coding tasks via the IAM-signed task API (Lambda function URL)."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

_SERVICE = "lambda"
_CACHE = Path.home() / ".rca" / "endpoint.json"


def resolve_api_url() -> str:
    env = (os.environ.get("RCA_API_URL") or "").strip()
    if env:
        return env.rstrip("/")
    if _CACHE.is_file():
        doc = json.loads(_CACHE.read_text(encoding="utf-8"))
        url = str(doc.get("apiUrl") or "").strip()
        if url:
            return url.rstrip("/")
    raise SystemExit(
        "no API URL: set RCA_API_URL or run 'make deploy' so ~/.rca/endpoint.json exists"
    )


def _region() -> str:
    return (
        os.environ.get("RCA_REGION")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
        or "us-east-1"
    )


def sign_request(method: str, url: str, body: bytes | None) -> dict[str, str]:
    parsed = urllib.parse.urlparse(url)
    headers = {"host": parsed.netloc}
    if body is not None:
        headers["content-type"] = "application/json"
    aws_req = AWSRequest(method=method, url=url, data=body, headers=headers)
    creds = boto3.Session().get_credentials()
    if creds is None:
        raise SystemExit("no AWS credentials for SigV4")
    SigV4Auth(creds.get_frozen_credentials(), _SERVICE, _region()).add_auth(aws_req)
    return dict(aws_req.headers)


def request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    base = resolve_api_url()
    url = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = sign_request(method, url, body)
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{err.code}: {detail}") from err


def cmd_submit(args: argparse.Namespace) -> int:
    item = request(
        "POST",
        "/tasks",
        {
            "repositoryUrl": args.repo,
            "branch": args.branch,
            "prompt": args.prompt,
            **({"baseBranch": args.base_branch} if args.base_branch else {}),
            **({"verifyCommand": args.verify} if args.verify else {}),
            **({"setupCommand": args.setup} if args.setup else {}),
        },
    )
    print(json.dumps(item, indent=2))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    item = request("GET", f"/tasks/{urllib.parse.quote(args.task_id, safe='')}")
    print(json.dumps(item, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    query = f"?limit={args.limit}"
    if args.status:
        query += f"&status={urllib.parse.quote(args.status)}"
    page = request("GET", f"/tasks{query}")
    print(json.dumps(page, indent=2))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    item = request("POST", f"/tasks/{urllib.parse.quote(args.task_id, safe='')}/cancel", {})
    print(json.dumps(item, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rca_cli", description="Remote coding agent task API")
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="POST /tasks")
    submit.add_argument("--repo", required=True)
    submit.add_argument("--branch", required=True)
    submit.add_argument("--prompt", required=True)
    submit.add_argument("--base-branch", default="")
    submit.add_argument("--verify", default="")
    submit.add_argument("--setup", default="")
    submit.set_defaults(func=cmd_submit)

    get = sub.add_parser("get", help="GET /tasks/{id}")
    get.add_argument("task_id")
    get.set_defaults(func=cmd_get)

    list_ = sub.add_parser("list", help="GET /tasks")
    list_.add_argument("--limit", type=int, default=25)
    list_.add_argument("--status", default="")
    list_.set_defaults(func=cmd_list)

    cancel = sub.add_parser("cancel", help="POST /tasks/{id}/cancel")
    cancel.add_argument("task_id")
    cancel.set_defaults(func=cmd_cancel)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
