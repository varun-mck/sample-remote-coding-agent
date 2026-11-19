"""Extract pull request URLs from gateway tool responses."""

from __future__ import annotations

import json
from typing import Any


def extract_pr_url(response: Any) -> str:  # noqa: C901, PLR0911, PLR0912
    if isinstance(response, dict):
        for key in ("html_url", "url", "pullRequestUrl", "prUrl", "links"):
            value = response.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if key == "links" and isinstance(value, dict):
                html = value.get("html") or value.get("self")
                if isinstance(html, dict) and isinstance(html.get("href"), str):
                    return html["href"]
        for value in response.values():
            found = extract_pr_url(value)
            if found:
                return found
    if isinstance(response, list):
        for item in response:
            found = extract_pr_url(item)
            if found:
                return found
    if isinstance(response, str):
        if "http" in response:
            for token in response.split():
                if token.startswith("http"):
                    return token.strip("\",'")
        try:
            return extract_pr_url(json.loads(response))
        except json.JSONDecodeError:
            pass
    return ""
