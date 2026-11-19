"""`kiro_runner.run_kiro`: the `kiro-cli chat` command line it builds."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from coding_agent import kiro_profile, kiro_runner


class _FakePopen:
    """Records the argv and finishes at once with a successful stream-json run."""

    calls: ClassVar[list[list[str]]] = []

    def __init__(self, cmd: list[str], **_kwargs: Any) -> None:
        _FakePopen.calls.append(list(cmd))
        finished = json.dumps({"type": "runFinished", "success": True, "result": "done"})
        self.stdout = io.StringIO(finished + "\n")
        self.stderr = io.StringIO("")

    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def wait(self) -> int:
        return 0


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: str) -> list[str]:
    _FakePopen.calls = []
    monkeypatch.setattr(kiro_runner.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(kiro_runner, "fetch_kiro_api_key", lambda: "")
    result = kiro_runner.run_kiro(
        prompt="update the greeting",
        kiro_home=tmp_path / "kiro_home",
        workspace=tmp_path,
        stream_log=tmp_path / ".agent" / "kiro-stream.jsonl",
        bridge_log=tmp_path / ".agent" / "gateway-bridge.log",
        **kwargs,
    )
    assert result.success
    (cmd,) = _FakePopen.calls
    return cmd


def test_model_and_effort_become_flags_before_the_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _run(monkeypatch, tmp_path, model="claude-sonnet-4.6", effort="high")
    assert cmd[-1] == "update the greeting"
    assert "--model=claude-sonnet-4.6" in cmd
    assert "--effort=high" in cmd
    assert cmd.index("--model=claude-sonnet-4.6") < len(cmd) - 1


def test_the_fleet_default_model_is_haiku_5_5(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _run(monkeypatch, tmp_path)
    assert cmd[-1] == "update the greeting"
    assert f"--model={kiro_profile.DEFAULT_KIRO_MODEL}" in cmd
    assert not [arg for arg in cmd if arg.startswith("--effort")]


def test_effort_alone_still_uses_the_fleet_default_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _run(monkeypatch, tmp_path, effort="low")
    assert "--effort=low" in cmd
    assert f"--model={kiro_profile.DEFAULT_KIRO_MODEL}" in cmd
