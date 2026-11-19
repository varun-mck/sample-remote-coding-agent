"""Filesystem roots for the harness."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_WORK_ROOT = "/mnt/workspace/work"
_DEFAULT_VENV_ROOT = "/tmp/venv"


@dataclass(frozen=True)
class AgentConfig:
    """Fleet defaults for the harness loop."""

    work_root: Path
    venv_root: Path


def load_config() -> AgentConfig:
    return AgentConfig(
        work_root=Path(os.environ.get("WORK_ROOT", _DEFAULT_WORK_ROOT)),
        venv_root=Path(os.environ.get("VENV_ROOT", _DEFAULT_VENV_ROOT)),
    )
