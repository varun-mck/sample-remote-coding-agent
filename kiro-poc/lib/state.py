"""Read and update the POC task state file shared by hooks and MCP servers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def append_hook_log(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    data = load(path)
    log = data.setdefault("hook_log", [])
    if isinstance(log, list):
        log.append(entry)
    save(path, data)
    return data
