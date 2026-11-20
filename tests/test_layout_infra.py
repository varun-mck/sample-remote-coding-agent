"""`infra/` is a CDK app: `stacks` is a namespace package, `app.py` is inert on import, and
its six stack names all hang off one prefix."""

from __future__ import annotations

import json
import re

import app
import stacks
from conftest import REPO_ROOT


def test_stacks_stays_a_namespace_package() -> None:
    assert stacks.__file__ is None
    assert next(iter(stacks.__path__)).endswith("infra/stacks")
    assert not (REPO_ROOT / "infra/stacks/__init__.py").exists()


def test_the_cdk_entry_point_imports_without_building_an_app() -> None:
    assert app.APP_NAME == "rca"
    assert callable(app.main)
    assert not (REPO_ROOT / "cdk.out").exists()


def test_the_six_stack_names_share_the_default_prefix() -> None:
    assert app.DEFAULT_STACK_PREFIX == "SampleCodingAgent"
    assert app.stack_names() == (
        app.ACCOUNT_STACK_NAME,
        app.DATA_STACK_NAME,
        app.AGENT_STACK_NAME,
        app.GATEWAY_STACK_NAME,
        app.API_STACK_NAME,
        app.TASK_GATEWAY_STACK_NAME,
    )
    assert app.API_STACK_NAME == "SampleCodingAgentApi"
    assert app.TASK_GATEWAY_STACK_NAME == "SampleCodingAgentTaskGateway"


def test_the_stack_prefix_renames_all_six_stacks() -> None:
    names = app.stack_names("Foo")
    assert names == (
        "FooAccount",
        "FooData",
        "FooRuntime",
        "FooGateway",
        "FooApi",
        "FooTaskGateway",
    )
    assert len(set(names)) == 6


def test_the_account_stack_is_one_per_consuming_region() -> None:
    apse1 = app.stack_names("Foo", "ap-southeast-1")
    use1 = app.stack_names("Foo", "us-east-1")
    assert apse1.account == "FooAccountApSoutheast1"
    assert use1.account == "FooAccountUsEast1"
    assert apse1.account != use1.account
    assert (apse1.data, apse1.agent, apse1.gateway, apse1.api) == (
        app.stack_names("Foo").data,
        app.stack_names("Foo").agent,
        app.stack_names("Foo").gateway,
        app.stack_names("Foo").api,
    )


def test_the_region_suffix_is_a_legal_stack_name_token() -> None:
    for region in ("us-east-1", "ap-southeast-1", "eu-west-1"):
        name = app.stack_names(region=region).account
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", name)
        assert len(name) <= 128


def test_the_api_stack_name_is_untouched_by_the_region() -> None:
    assert app.stack_names("Foo", "eu-west-1").api == "FooApi"


def test_cdk_json_points_at_app_py() -> None:
    cdk_json = json.loads((REPO_ROOT / "infra/cdk.json").read_text())
    assert cdk_json["app"] == "python3 app.py"
