"""Static fleet defaults: the per-role model routing table and the filesystem roots.

There is no per-repository configuration schema in this rewrite. Everything here is
either a constant or read from one environment variable, so a misconfiguration is a
deploy-time fact rather than a table row nobody can find.

Timeouts deliberately live in `commands.py`, not here, so the two command runners
have exactly one owner for how long they may take.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from claude_agent_sdk import EffortLevel

log = logging.getLogger(__name__)

# The work root lives on the AgentCore session-storage mount. It is read from the
# environment rather than baked into the image because a Dockerfile `ENV` cannot be
# *unset* by a runtime's `environmentVariables`, only overridden (invariant 26) --
# the provisioner forwards it.
_DEFAULT_WORK_ROOT = "/mnt/workspace/work"

# The virtualenv root stays on /tmp, OFF the session-storage mount, deliberately.
# The mount's cap is 1 GB and a virtualenv for this repository is ~692 MB of
# perfectly reconstructible bytes; moving it onto the mount is the obvious-looking
# tidy-up that fills the session and kills the task.
_DEFAULT_VENV_ROOT = "/tmp/venv"

# A model is either one of the CLI's aliases, a Bedrock model / inference-profile id,
# or a full ARN. A validated *pattern* rather than three hard-coded ids, because
# regional and `global.` inference profiles are legitimate values (invariant 21) and
# new Claude ids appear faster than this file is edited.
#
# The region-prefix group is `[a-z]{2,6}\.` and not `[a-z]{2}\.`: the real prefixes
# are `us.`, `eu.`, `apac.` and `global.`, so a two-letter bound would reject
# `apac.anthropic.claude-...` outright.
MODEL_RE = re.compile(
    r"^(?:"
    r"opus|sonnet|haiku"
    r"|(?:[a-z]{2,6}\.)?anthropic\.[a-z0-9.:-]+"
    r"|arn:aws:bedrock:[a-z0-9-]*:[0-9]*:[a-z-]+/[A-Za-z0-9.:/-]+"
    r")$"
)


# The four model variables the provisioner forwards into every runtime, named here so the
# agent side has one owner for the contract rather than four strings scattered across two
# deployment units. `api/src/handlers/provisioner._fleet_environment` ALREADY sets all
# four -- this tuple documents and asserts the agent's half of the deal, and the other half
# lives in a separate deployment unit, so it is pointed at rather than edited.
#
# The three alias variables matter because `ROLES` routes by alias: with
# `ANTHROPIC_DEFAULT_OPUS_MODEL` unset, `model="opus"` is resolved by the CLI to whatever
# it would pick rather than to the Bedrock id this fleet chose. `load_config` says so out
# loud for the haiku one, which is the one that looks decorative and is not.
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
            "opus/sonnet/haiku, a Bedrock model or inference-profile id such as "
            "global.anthropic.claude-sonnet-4-5-20250929-v1:0, or a full "
            "arn:aws:bedrock:... ARN"
        )
    return value


@dataclass(frozen=True)
class RoleConfig:
    """One row of the model-routing table. `model=None` falls back to the fleet default."""

    model: str | None
    effort: EffortLevel
    turns: int


# The routing table. The roles differ enormously in what they need and in what they
# cost, so the granularity is per role and not per configuration.
#
# `code` stays at effort `high` (invariant 8): at `medium` effort a coding role can
# report the task complete with no source edits, while the same task at `high` produces
# real changes. The no-op guard in `nodes/code.py` bounds this: under-performance
# raises rather than reporting success quietly.
#
# `review` keeps Opus at 40 turns because it is the one component whose failure is
# silent -- a missed veto is a confident green PR containing untested code.
ROLES: Mapping[str, RoleConfig] = {
    "plan": RoleConfig(model="opus", effort="high", turns=200),
    "replan": RoleConfig(model="opus", effort="high", turns=200),
    "code": RoleConfig(model="sonnet", effort="high", turns=200),
    "review": RoleConfig(model="opus", effort="low", turns=40),
}

DEFAULT_BUNDLE_ROOT = "/app/aipdlc"
DEFAULT_ENTRY_COMMAND = "deliver"
DEFAULT_BUNDLE_PLUGIN = "aipdlc"

# Ordered worst-to-best, so "configured below its default" is a comparison and not a
# table of special cases.
_EFFORT_ORDER: tuple[EffortLevel, ...] = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class AgentConfig:
    """Fleet defaults for the harness loop."""

    model: str
    work_root: Path
    venv_root: Path
    bundle_paths: tuple[Path, ...]
    bundle_plugin_name: str
    entry_command: str
    fallback_model: str | None
    max_turns: int
    effort: EffortLevel


def bundle_paths() -> tuple[Path, ...]:
    configured = os.environ.get("AIPDLC_BUNDLE_PATH", "").strip()
    if configured:
        return tuple(Path(part) for part in configured.split(":") if part)
    in_tree = Path(__file__).resolve().parents[2] / "aipdlc"
    return (in_tree,) if in_tree.is_dir() else (Path(DEFAULT_BUNDLE_ROOT),)


def _role_config(role: str, default: RoleConfig) -> RoleConfig:
    """Apply the per-role environment overrides, warning when one lowers a default.

    Lowering is allowed -- an operator may need it -- but never silent: the whole
    point of invariant 8 is that a quietly weakened role produces a plausible-looking
    run that changed nothing.
    """
    prefix = f"ROLE_{role.upper()}_"
    model = os.environ.get(f"{prefix}MODEL") or default.model
    if model is not None:
        validate_model(model)

    effort: EffortLevel = default.effort
    configured_effort = os.environ.get(f"{prefix}EFFORT")
    if configured_effort in _EFFORT_ORDER:
        effort = cast(EffortLevel, configured_effort)
        if _EFFORT_ORDER.index(effort) < _EFFORT_ORDER.index(default.effort):
            log.warning(
                "role %s is configured at effort %s, below its default %s; at medium "
                "effort a coding role once reported the task complete with zero "
                "source edits against 214 insertions at high (invariant 8)",
                role,
                effort,
                default.effort,
            )
    elif configured_effort:
        log.warning(
            "ignoring ROLE_%s_EFFORT=%r: not an effort level", role.upper(), configured_effort
        )

    turns = default.turns
    configured_turns = os.environ.get(f"{prefix}TURNS")
    if configured_turns and configured_turns.isdigit():
        turns = int(configured_turns)
        if turns < default.turns:
            log.warning(
                "role %s is configured at %d turns, below its default %d; a planning "
                "role that exhausts its turns returns no usable spec and the task "
                "dies with nothing implemented",
                role,
                turns,
                default.turns,
            )
    elif configured_turns:
        # Said out loud for the same reason the effort branch is: a typo'd budget that
        # falls back silently is indistinguishable from an unset one, and the operator who
        # set it believes it took effect.
        log.warning("ignoring ROLE_%s_TURNS=%r: not a number", role.upper(), configured_turns)
    return RoleConfig(model=model, effort=effort, turns=turns)


def _env_flag(name: str, *, default: bool) -> bool:
    """Read `name` as a switch. Unset is `default`; `0`, `false` and `no` are off."""
    configured = os.environ.get(name, "").strip().lower()
    if not configured:
        return default
    return configured not in ("0", "false", "no")


def load_config() -> AgentConfig:
    """Build the configuration from the environment, with the fleet defaults."""
    fleet_model = os.environ.get("ANTHROPIC_MODEL", "sonnet")
    validate_model(fleet_model)
    # Validated like any other model, so a typo'd fallback fails here rather than at the
    # moment the primary model becomes unavailable -- which is the one moment nobody is
    # watching, and the one moment the fallback is the whole run.
    fallback_model = validate_model(os.environ.get("ANTHROPIC_FALLBACK_MODEL", "sonnet"))
    entry = (
        os.environ.get("AIPDLC_ENTRY_COMMAND", DEFAULT_ENTRY_COMMAND).strip()
        or DEFAULT_ENTRY_COMMAND
    )
    plugin = (
        os.environ.get("AIPDLC_PLUGIN_NAME", DEFAULT_BUNDLE_PLUGIN).strip() or DEFAULT_BUNDLE_PLUGIN
    )
    turns = int(os.environ.get("AIPDLC_MAX_TURNS", "200"))
    configured_effort = os.environ.get("AIPDLC_EFFORT", "high")
    effort: EffortLevel = configured_effort if configured_effort in _EFFORT_ORDER else "high"

    return AgentConfig(
        model=fleet_model,
        work_root=Path(os.environ.get("WORK_ROOT", _DEFAULT_WORK_ROOT)),
        venv_root=Path(os.environ.get("VENV_ROOT", _DEFAULT_VENV_ROOT)),
        bundle_paths=bundle_paths(),
        bundle_plugin_name=plugin,
        entry_command=entry,
        fallback_model=fallback_model,
        max_turns=turns,
        effort=effort,
    )
