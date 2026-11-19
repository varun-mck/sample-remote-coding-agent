"""Agent stack uses PUBLIC network mode (no VPC)."""

from __future__ import annotations

from pathlib import Path


def test_agent_stack_declares_public_network_mode() -> None:
    text = (Path(__file__).resolve().parents[1] / "infra/stacks/agent_stack.py").read_text()
    assert 'network_mode="PUBLIC"' in text
    assert "SubnetType.PRIVATE_WITH_EGRESS" not in text.split("class AgentStack")[1]
