"""Light documentation contracts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_DELETED_PATHS = (
    "mcp/",
    "plugins/sample-rca",
    "kiro-poc/",
    "CLAUDE.md",
    "SUCCEEDED_UNVERIFIED",
)


def test_agents_md_lists_core_invariants() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "add_async_task" in text
    assert "agentcore_gateway_bridge" in text or "Gateway MCP" in text
    assert "free-form" in text or "free form" in text.lower() or "no mandatory PR" in text


def test_docs_do_not_reference_removed_paths() -> None:
    for path in (
        ROOT / "README.md",
        ROOT / "docs" / "operations.md",
        ROOT / "docs" / "security.md",
    ):
        text = path.read_text(encoding="utf-8")
        for fragment in _DELETED_PATHS:
            assert fragment not in text, f"{path.name} still mentions {fragment!r}"


def test_architecture_doc_exists() -> None:
    assert (ROOT / "docs" / "architecture.md").is_file()
    assert "mermaid" in (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")


def test_makefile_declares_operator_targets() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("setup:", "test:", "lint:", "deploy:", "task:", "task-get:", "task-list:"):
        assert re.search(rf"^{re.escape(target)}", makefile, re.MULTILINE), target
