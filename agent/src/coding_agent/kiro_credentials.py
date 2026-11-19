"""Load Kiro API key from Secrets Manager (never cached across tasks)."""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)


def fetch_kiro_api_key() -> str:
    secret_id = os.environ.get("KIRO_API_KEY_SECRET", "").strip()
    if not secret_id:
        key = os.environ.get("KIRO_API_KEY", "").strip()
        log.info("kiro api key source=env found=%s", bool(key))
        return key
    try:
        client = boto3.client("secretsmanager")
        answer = client.get_secret_value(SecretId=secret_id)
        raw = answer.get("SecretString") or ""
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for field in ("KIRO_API_KEY", "apiKey", "api_key", "key"):
                        value = parsed.get(field)
                        if isinstance(value, str) and value.strip():
                            log.info("kiro api key source=secretsmanager found=True")
                            return value.strip()
            except json.JSONDecodeError:
                pass
        found = bool(raw)
        log.info("kiro api key source=secretsmanager found=%s", found)
        return raw
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        log.warning("kiro api key secret fetch failed code=%s", code)
        return ""
    except BotoCoreError:
        log.warning("kiro api key secret fetch failed (botocore)", exc_info=True)
        return ""
