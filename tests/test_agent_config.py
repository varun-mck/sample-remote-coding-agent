from __future__ import annotations

from pathlib import Path

from coding_agent import config


def test_load_config_reads_work_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORK_ROOT", str(tmp_path / "work"))
    monkeypatch.setenv("VENV_ROOT", str(tmp_path / "venv"))
    loaded = config.load_config()
    assert loaded.work_root == tmp_path / "work"
    assert loaded.venv_root == tmp_path / "venv"
