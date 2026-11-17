from __future__ import annotations

from pathlib import Path


def test_aipdlc_bundle_layout() -> None:
    root = Path(__file__).resolve().parents[1] / "agent" / "aipdlc"
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / "commands" / "deliver.md").is_file()
