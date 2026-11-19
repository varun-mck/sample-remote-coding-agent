from __future__ import annotations

from pathlib import Path

import pytest
from coding_agent import store
from coding_agent.kiro_runner import KiroRunResult, parse_stream_json


def test_parse_stream_json_success() -> None:
    import json

    lines = [json.dumps({"type": "runFinished", "success": True, "result": "All done."})]
    assert parse_stream_json(lines).final_text == "All done."


def test_truncate_result() -> None:
    assert store.truncate_result("short") == "short"
    long = "x" * 100_000
    out = store.truncate_result(long)
    assert "truncated" in out


@pytest.mark.asyncio
async def test_harness_succeeds_without_pr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from coding_agent import harness, providers

    monkeypatch.setenv("WORK_ROOT", str(tmp_path / "work"))
    ref = providers.parse_repository_url("https://github.com/o/r")
    monkeypatch.setattr(providers, "provider_from_payload", lambda _p: ref)
    monkeypatch.setattr(providers, "clone_url", lambda r: r.url)

    def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(harness.credentials, "fetch_token", lambda _provider: "")
    monkeypatch.setattr(harness.git_ops, "clone", _noop)
    monkeypatch.setattr(harness.git_ops, "checkout_branch", _noop)
    monkeypatch.setattr(harness.git_ops, "exclude_build_artifacts", _noop)
    monkeypatch.setattr(harness, "prepare_kiro_home", _noop)
    kiro_kwargs: dict[str, object] = {}

    def _run_kiro(**kwargs: object) -> KiroRunResult:
        kiro_kwargs.update(kwargs)
        return KiroRunResult(success=True, final_text="Task complete.")

    monkeypatch.setattr(harness, "run_kiro", _run_kiro)
    recorded: list[tuple[str, str | None]] = []

    def _record(state, status, *, result=None, extra=None):
        recorded.append((status, result))

    monkeypatch.setattr(harness.store, "record_result", _record)
    monkeypatch.setattr(harness.store, "set_status", _noop)

    out = await harness.run_task(
        {
            "taskId": "tsk_test",
            "repositoryUrl": "https://github.com/o/r",
            "branch": "feat/x",
            "prompt": "do thing",
            "model": "claude-sonnet-4.6",
            "effort": "high",
        }
    )
    assert out["status"] == "SUCCEEDED"
    assert recorded and recorded[-1][0] == "SUCCEEDED"
    assert kiro_kwargs["model"] == "claude-sonnet-4.6"
    assert kiro_kwargs["effort"] == "high"
