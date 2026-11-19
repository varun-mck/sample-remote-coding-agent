"""Load Kiro API key from Secrets Manager (never cached across tasks)."""

from __future__ import annotations

import json
import logging
import os

import boto3

log = logging.getLogger(__name__)


def fetch_kiro_api_key() -> str:
    secret_id = os.environ.get("KIRO_API_KEY_SECRET", "").strip()
    if not secret_id:
        return os.environ.get("KIRO_API_KEY", "").strip()
    client = boto3.client("secretsmanager")
    answer = client.get_secret_value(SecretId=secret_id)
    raw = answer.get("SecretString") or ""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key in ("KIRO_API_KEY", "apiKey", "api_key", "key"):
                    value = parsed.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        except json.JSONDecodeError:
            pass
    return raw
