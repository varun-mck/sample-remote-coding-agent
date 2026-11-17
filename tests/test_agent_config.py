from __future__ import annotations

from pathlib import Path

from coding_agent import config


def test_bundle_paths_prefers_environment(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setenv("AIPDLC_BUNDLE_PATH", str(bundle))
    assert config.bundle_paths() == (bundle,)


def test_load_config_reads_entry_command(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "sonnet")
    monkeypatch.setenv("ANTHROPIC_FALLBACK_MODEL", "sonnet")
    monkeypatch.setenv("AIPDLC_ENTRY_COMMAND", "ship")
    loaded = config.load_config()
    assert loaded.entry_command == "ship"
