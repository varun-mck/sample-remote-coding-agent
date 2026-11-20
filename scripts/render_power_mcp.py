#!/usr/bin/env python3
"""Write `kiro-rca-power/mcp.json` from CloudFormation deploy outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
REDIRECT_URI = "http://localhost:7778/oauth/callback"
DEFAULT_STACK_PREFIX = "SampleCodingAgent"
STACK_SUFFIX = "TaskGateway"
OUTPUT_KEYS = ("TaskGatewayUrl", "CognitoClientId", "OAuthScope")


def build_mcp_config(*, gateway_url: str, client_id: str, oauth_scope: str) -> dict[str, Any]:
    return {
        "$schema": SCHEMA,
        "mcpServers": {
            "rca-fleet": {
                "url": gateway_url.rstrip("/") + "/mcp"
                if not gateway_url.endswith("/mcp")
                else gateway_url,
                "oauth": {"clientId": client_id, "redirectUri": REDIRECT_URI},
                "oauthScopes": ["openid", "email", oauth_scope],
            }
        },
    }


def stack_outputs(outputs_path: Path, stack_prefix: str) -> dict[str, str]:
    data = json.loads(outputs_path.read_text(encoding="utf-8"))
    stack_name = f"{stack_prefix}{STACK_SUFFIX}"
    block = data.get(stack_name)
    if not isinstance(block, dict):
        raise SystemExit(f"outputs file has no {stack_name!r} section (keys: {sorted(data)})")
    missing = [key for key in OUTPUT_KEYS if not block.get(key)]
    if missing:
        raise SystemExit(f"{stack_name} outputs missing: {', '.join(missing)}")
    return {key: str(block[key]) for key in OUTPUT_KEYS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs",
        type=Path,
        default=Path.home() / ".rca" / "stack-outputs.json",
        help="CloudFormation outputs from `make deploy`",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("kiro-rca-power/mcp.json"),
        help="Path to write",
    )
    parser.add_argument(
        "--stack-prefix",
        default=DEFAULT_STACK_PREFIX,
        help="Must match Makefile STACK_PREFIX / CDK -c stackPrefix",
    )
    args = parser.parse_args(argv)
    if not args.outputs.is_file():
        raise SystemExit(f"outputs file not found: {args.outputs}")

    out = stack_outputs(args.outputs, args.stack_prefix)
    url = out["TaskGatewayUrl"]
    if not url.endswith("/mcp"):
        url = url.rstrip("/") + "/mcp"
    config = build_mcp_config(
        gateway_url=url, client_id=out["CognitoClientId"], oauth_scope=out["OAuthScope"]
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
