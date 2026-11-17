from __future__ import annotations

import inspect

import invoke_payload
from coding_agent import server
from handlers import dispatcher


def test_dispatcher_payload_matches_contract() -> None:
    source = inspect.getsource(dispatcher._payload)
    for field in invoke_payload.FIELDS:
        assert f'"{field}"' in source


def test_server_required_fields() -> None:
    for field in invoke_payload.REQUIRED:
        if field == "repositoryUrl":
            assert field in server._OPTIONAL_REPO or "repositoryUrl" in server._REQUIRED
        else:
            assert field in server._REQUIRED
