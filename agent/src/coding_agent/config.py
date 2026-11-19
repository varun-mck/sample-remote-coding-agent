"""Filesystem roots and model-id validation for the harness."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_WORK_ROOT = "/mnt/workspace/work"
_DEFAULT_VENV_ROOT = "/tmp/venv"

MODEL_RE = re.compile(
    r"^(?:"
    r"opus|sonnet|haiku"
    r"|(?:[a-z]{2,6}\.)?anthropic\.[a-z0-9.:-]+"
    r"|arn:aws:bedrock:[a-z0-9-]*:[0-9]*:[a-z-]+/[A-Za-z0-9.:/-]+"
    r")$"
)

MODEL_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)


def validate_model(value: str) -> str:
    """Return `value` if it names a model this fleet may use, else raise."""
    if not MODEL_RE.match(value or ""):
        raise ValueError(
            f"{value!r} is not a usable model: expected one of the aliases "
            "opus/sonnet/haiku, a Bedrock model or inference-profile id, or a full "
            "arn:aws:bedrock:... ARN"
        )
    return value


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
