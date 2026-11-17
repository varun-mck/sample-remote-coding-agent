"""Detect setup and verify commands from a repository tree (invariant 35)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_MAKE_TARGET = re.compile(r"^([a-zA-Z0-9_.-]+):", re.MULTILINE)


def _make_targets(repo_dir: Path) -> set[str]:
    makefile = repo_dir / "Makefile"
    if not makefile.is_file():
        return set()
    return set(_MAKE_TARGET.findall(makefile.read_text(encoding="utf-8", errors="replace")))


def _package_scripts(repo_dir: Path) -> dict[str, str]:
    manifest = repo_dir / "package.json"
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        log.warning("package.json does not parse; ignoring it for command detection")
        return {}
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def detect_verify_command(repo_dir: Path) -> str:
    if "test" in _make_targets(repo_dir):
        return "make test"
    if "test" in _package_scripts(repo_dir):
        return "npm test"
    return ""


def detect_setup_command(repo_dir: Path) -> str:
    for target in ("setup", "bootstrap"):
        if target in _make_targets(repo_dir):
            return f"make {target}"
    if "setup" in _package_scripts(repo_dir):
        return "npm run setup"
    return ""
