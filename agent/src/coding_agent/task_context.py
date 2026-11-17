"""Per-task fields the harness and hooks share."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .providers import RepositoryRef


@dataclass
class TaskContext:
    task_id: str
    prompt: str
    ref: RepositoryRef
    work_dir: str
    repo_dir: str
    branch: str
    base_branch: str
    verify_command: str
    setup_command: str
    verified: bool = False
    verification: dict[str, Any] = field(default_factory=dict)
    pr_url: str = ""
    pr_tool: str = ""
