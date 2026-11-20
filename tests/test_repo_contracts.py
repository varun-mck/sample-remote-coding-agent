"""Contracts on the repository's own files: the Makefile's target names, the stack prefix it
mirrors from `infra/app.py`, the wait its `deploy` recipe puts in front of the account stack,
the way its outputs reader reports a stack it cannot find, every dependency's upper bound,
pytest's distribution mode, and the agent image's pins. Each of the seven is an invariant that
fails silently or confusingly when broken."""

from __future__ import annotations

import configparser
import re
import tomllib

import app
import pytest
from conftest import REPO_ROOT

# A requirement is a name, optional extras, then everything else is the specifier.
# Deliberately not `packaging.requirements`: packaging is only a transitive
# dependency of pytest and nothing here declares it, so importing it would make this
# test depend on pip's resolution of somebody else's manifest.
_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?P<extras>\[[^\]]*\])?(?P<specifier>.*)$")

_MANIFESTS = [
    "requirements-dev.txt",
    "api/requirements.txt",
    "infra/requirements.txt",
    "agent/pyproject.toml",
]

# `agent/Dockerfile` is deliberately NOT a sixth manifest: `_declared_requirements` parses
# `.toml` with tomllib and everything else one requirement per line, and an npm `@version`
# is neither. The image's pins are held by the sibling tests below instead.
_DOCKERFILE = "agent/Dockerfile"
_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_ARG = re.compile(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", re.MULTILINE)
# Every spelling of "whatever is newest at build time". None of these has a legitimate use in
# this file, so the check is a plain substring scan rather than a parse.
_MOVING_REFERENCES = ("@latest", ":latest", "@main", "@master", "@next", "@beta", "@canary")


def _declared_requirements(manifest: str) -> list[str]:
    path = REPO_ROOT / manifest
    if path.suffix == ".toml":
        data = tomllib.loads(path.read_text())
        project = data["project"]
        requirements = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            requirements.extend(extra)
        return requirements
    return [
        line.split(" #")[0].strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_the_makefile_declares_operator_targets() -> None:
    declared = set(
        re.findall(
            r"^([A-Za-z][A-Za-z0-9_-]*):", (REPO_ROOT / "Makefile").read_text(), re.MULTILINE
        )
    )
    required = {
        "setup",
        "test",
        "lint",
        "preflight",
        "deploy",
        "budget-reset",
        "task",
        "task-get",
        "task-list",
        "power",
    }
    assert required.issubset(declared), f"missing targets: {sorted(required - declared)}"


def test_the_makefile_mirrors_the_stack_prefix_infra_owns() -> None:
    """`STACK_PREFIX` is one knob, the three stack names derive from it, and it is forwarded.

    A Makefile variable that advertises a stack name the deploy does not actually use is
    the confusing failure this test exists to prevent: `make deploy CONSOLE_STACK=Foo` used
    to deploy `SampleCodingAgentConsole` anyway and then die in the console-upload step
    reading outputs for a stack nothing wrote -- a traceback out of a JSON read, from a
    variable this file itself offered. So the default has to be the one `infra/app.py`
    owns, the three names have to derive from the knob rather than repeat a literal, and
    the knob has to reach `cdk deploy` as `-c stackPrefix=`.
    """
    makefile = (REPO_ROOT / "Makefile").read_text()

    default = re.search(r"^STACK_PREFIX \?= (\S+)$", makefile, re.MULTILINE)
    assert default is not None, (
        "Makefile declares no `STACK_PREFIX ?= <prefix>`. It is the one overridable stack "
        "variable: the three names below it are derived, so there is nothing else to set."
    )
    assert default.group(1) == app.DEFAULT_STACK_PREFIX, (
        f"Makefile defaults STACK_PREFIX to {default.group(1)!r} but `infra/app.py` defaults "
        f"to {app.DEFAULT_STACK_PREFIX!r}. `cdk deploy` names the stacks from app.py's "
        "prefix and every later step reads outputs from this file's, so a drift is a deploy "
        "that succeeds and then fails reading back stacks nothing wrote."
    )

    for variable in ("API_STACK", "AGENT_STACK", "GATEWAY_STACK", "ACCOUNT_STACK"):
        assignment = re.search(rf"^{variable} \??= (.+)$", makefile, re.MULTILINE)
        assert assignment is not None, f"Makefile no longer defines {variable}"
        value = assignment.group(1)
        assert "$(STACK_PREFIX)" in value and "SampleCodingAgent" not in value, (
            f"Makefile defines {variable} as {value!r}, which does not derive from "
            "$(STACK_PREFIX). A literal here is a name that keeps saying "
            "`SampleCodingAgent` while `-c stackPrefix=` renamed the stacks, and the "
            "symptom is a crash in the console-upload step rather than a second deployment."
        )

    assert "-c stackPrefix=$(STACK_PREFIX)" in makefile, (
        "the `$(CDK) deploy --all` line no longer forwards `-c stackPrefix=$(STACK_PREFIX)`. "
        "Unforwarded, STACK_PREFIX renames only the stacks this file READS -- the deploy "
        "still writes the default ones -- which is exactly the advertised-knob-that-does-"
        "nothing this variable replaced."
    )


@pytest.mark.parametrize("manifest", _MANIFESTS)
def test_every_declared_dependency_carries_an_upper_bound(manifest: str) -> None:
    requirements = _declared_requirements(manifest)
    assert requirements, f"{manifest} declares nothing; the parser is probably wrong"
    for requirement in requirements:
        match = _REQUIREMENT.match(requirement)
        assert match is not None, f"{manifest}: cannot parse {requirement!r}"
        assert "<" in match.group("specifier"), (
            f"invariant 29: {manifest} declares {requirement!r} with no upper bound. "
            "`mcp>=1.2.0` resolved to 2.0.0, which removed a method; `pytest>=8.0` "
            "resolved to 9 and turned a green checkout red. setupCommand runs before "
            "verification on every run that verifies, so an unbounded floor fails "
            "runs for reasons nothing in the diff explains."
        )


def test_pytest_ini_keeps_the_loadfile_distribution() -> None:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#",))
    parser.read(REPO_ROOT / "pytest.ini")
    section = parser["pytest"]

    assert "--dist loadfile" in section["addopts"], (
        "handler modules bind table names at import, so several test files "
        "`importlib.reload` them; only `loadfile` keeps a file's tests on one xdist "
        "worker, which is what makes the reload order a within-file contract"
    )
    assert "-n auto" in section["addopts"]
    assert section["asyncio_mode"] == "auto"
    assert section["testpaths"] == "tests"
    assert section["pythonpath"].split() == [
        "agent/src",
        "api/src",
        "tests",
        "infra",
        "scripts",
    ]


def _dockerfile_args() -> dict[str, str]:
    """The `ARG NAME=default` table, i.e. the versions a plain `docker build` would use."""
    return dict(_ARG.findall((REPO_ROOT / _DOCKERFILE).read_text()))


def _dockerfile() -> str:
    """The Dockerfile with its ARG defaults substituted -- what the build actually runs.

    Substituted rather than pattern-matched around `${...}`, so the test reads the same
    string the build does and an indirection cannot hide a moving reference.
    """
    text = (REPO_ROOT / _DOCKERFILE).read_text()
    for name, value in _dockerfile_args().items():
        text = text.replace(f"${{{name}}}", value).replace(f"${name}", value)
    return text


def test_the_agent_image_exists_where_the_asset_builds_it() -> None:
    assert (REPO_ROOT / _DOCKERFILE).is_file(), (
        f"{_DOCKERFILE} is missing. `infra/stacks/agent_stack.py` builds "
        "`DockerImageAsset(directory=<repo>/agent)`, so without this file `cdk synth` fails "
        "on a real machine -- and nothing in this suite builds an image, so a green run here "
        "is exactly what a missing Dockerfile looks like."
    )


def test_every_stage_of_the_agent_image_is_arm64() -> None:
    froms = [
        line
        for line in (REPO_ROOT / _DOCKERFILE).read_text().splitlines()
        if line.startswith("FROM")
    ]
    assert froms, f"{_DOCKERFILE} declares no stage; the parser is probably wrong"
    for line in froms:
        assert "--platform=linux/arm64" in line, (
            f"invariant 2: {line!r} does not pin ARM64, and this one is silent -- "
            "CloudFormation accepts an x86 image and AgentCore then never reaches READY, "
            "with no logs to say why. It must agree with `ecr_assets.Platform.LINUX_ARM64` "
            "in `infra/stacks/agent_stack.py`."
        )


def test_the_agent_image_pins_the_kiro_cli_and_smoke_checks_it() -> None:
    text = _dockerfile()
    assert "KIRO_CLI_VERSION=" in text, (
        "invariant 34: Kiro CLI must be pinned with ARG KIRO_CLI_VERSION in the Dockerfile."
    )
    logical = re.sub(r"\\\n\s*", " ", text).splitlines()
    install = [line for line in logical if "kiro-cli --version" in line]
    assert install, (
        "invariant 3: `kiro-cli --version` must run in the same RUN that installs the CLI."
    )


def test_the_agent_image_carries_no_baked_kiro_bundle() -> None:
    raw = (REPO_ROOT / _DOCKERFILE).read_text()
    assert "COPY kiro-bundle" not in raw and "KIRO_BUNDLE_PATH" not in raw, (
        f"{_DOCKERFILE} must not bake agents, skills or steering; those come from the target repo."
    )
    assert "COPY codeguard" not in raw and "CODEGUARD_PATH" not in raw, (
        f"{_DOCKERFILE} must not bake a Codeguard bundle."
    )
    instructions = [
        line
        for line in re.sub(r"\\\n\s*", " ", raw).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    joined = "\n".join(instructions)
    assert "AWS_DOCS_MCP" not in joined and "aws-documentation-mcp" not in joined, (
        "the image must not install the AWS documentation MCP server at build time."
    )
    profile = (REPO_ROOT / "agent/src/coding_agent/kiro_profile.py").read_text()
    assert "agentcore_gateway_bridge" in profile and "harness_server" not in profile, (
        "fleet Kiro sessions must use the AgentCore Gateway MCP bridge only."
    )


def test_the_agent_image_installs_nothing_from_a_moving_reference() -> None:
    raw = (REPO_ROOT / _DOCKERFILE).read_text()
    for moving in _MOVING_REFERENCES:
        assert moving not in raw, (
            f"invariant 34: {_DOCKERFILE} contains {moving!r}. A moving reference makes the "
            "image irreproducible and lets a compromised release land in every container "
            "without a diff in this repository. The scan covers comments too, so a reason "
            "for a pin has to be written without spelling the thing it rules out."
        )

    for name, value in _dockerfile_args().items():
        if not name.endswith("_VERSION"):
            continue
        assert _EXACT_VERSION.match(value), (
            f"invariant 34: `ARG {name}={value}` is not an exact version. Every version this "
            "image installs is a reviewed edit to one `ARG`, not something that moves by "
            "itself."
        )

    stages: set[str] = set()
    images: list[str] = []
    for line in raw.splitlines():
        if line.startswith("FROM"):
            operands = [token for token in line.split()[1:] if not token.startswith("--")]
            images.append(operands[0])
            if len(operands) == 3 and operands[1].upper() == "AS":
                stages.add(operands[2])
        elif line.startswith("COPY"):
            # A `--from=` that names a stage declared above is an intra-build reference, not
            # an image; anything else is a registry pull and needs a version tag.
            images.extend(
                token.split("=", 1)[1]
                for token in line.split()
                if token.startswith("--from=") and token.split("=", 1)[1] not in stages
            )

    assert images, f"{_DOCKERFILE} references no image; the parser is probably wrong"
    for image in images:
        _, _, tag = image.rpartition(":")
        assert tag and tag[0].isdigit(), (
            f"invariant 34: the image reference {image!r} carries no version tag. A tag whose "
            "first character is not a digit is not a version, it is a channel, and a channel "
            "moves under the build."
        )


def test_the_agent_image_does_not_bake_kiro_content_and_sets_no_work_root() -> None:
    raw = (REPO_ROOT / _DOCKERFILE).read_text()

    assert "COPY kiro-bundle" not in raw, (
        f"{_DOCKERFILE} must not copy a kiro-bundle tree into the image."
    )
    assert "WORK_ROOT" not in raw, (
        "invariant 26: the image must set no work-root environment variable. A Dockerfile "
        "`ENV` cannot be *unset* by a runtime's `environmentVariables`, only overridden, so "
        "one added here for local convenience makes the runtime's value unremovable."
    )
    for config_file in (".kiro/settings/mcp.json",):
        assert f"COPY {config_file}" not in raw, (
            f"invariant 10: {_DOCKERFILE} must not bake global Kiro MCP settings into the image."
        )


def test_the_agent_image_carries_no_metrics_collector() -> None:
    """Nothing in the image, and nothing in the tree, exports the CLI's OTel metrics.

    AWS documents two supported paths for the CLI's metrics -- a bearer token the CLI posts
    with directly, and a vendor gateway -- and this repository takes neither. A bearer token is a long-term IAM-user credential,
    which this container's whole credential story (one repository-scoped PAT, never in the
    environment) refuses; the gateway is a vendor endpoint invariant 33 blocks by name.

    So this asserts the absence in every place an exporter could appear, because each of them
    fails silently on its own: a collector with no permission exports nothing, exporter
    variables with no collector retry against a closed port, and a `cloudwatch:PutMetricData`
    grant with neither is one more thing stolen container credentials reach (invariant 32b).
    """
    raw = (REPO_ROOT / _DOCKERFILE).read_text()
    args = _dockerfile_args()

    assert "ENABLE_OTEL_COLLECTOR" not in args and "ADOT_COLLECTOR_VERSION" not in args, (
        f"{_DOCKERFILE} declares a collector build arg again ({sorted(args)}). The collector was "
        f"removed with the feature it served; reintroducing it means reintroducing an exporter "
        f"endpoint, the CloudWatch grant and the boundary widening that ceilings it."
    )
    # Instructions only, so the paragraph at the bottom of the Dockerfile that explains why no
    # collector is here does not fail the test that keeps it true.
    instructions = [
        line
        for line in re.sub(r"\\\n\s*", " ", raw).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    offending = [line for line in instructions if "otel" in line.lower()]
    assert offending == [], (
        f"{_DOCKERFILE} carries {offending}, an OTel collector or `otel` path again. The ~1.3 GB "
        f"ARM64 image is built on the operator's own machine against AgentCore's 2 GB cap "
        f"(invariant 2), and nothing in this suite can measure an image."
    )

    for line in raw.splitlines():
        if not line.startswith("ENV "):
            continue
        assert "OTEL_" not in line and "CLAUDE_CODE_ENABLE_TELEMETRY" not in line, (
            f"invariant 26: {_DOCKERFILE} sets {line!r}. `CLAUDE_CODE_ENABLE_TELEMETRY` is fleet "
            "policy in `provisioner._fleet_environment`, pinned to `0` and converged on every "
            "provision; an `ENV` here can be overridden by a runtime's `environmentVariables` "
            "but never unset, so an exporter switched on in the image could not be switched off "
            "without a rebuild."
        )

    # The collector's configuration directory: if it re-appears, the corresponding collector is back.
    assert not (REPO_ROOT / "agent" / "otel").exists(), (
        "agent/otel/ is back. It held the collector configuration -- a `sigv4auth` extension, a "
        "loopback OTLP receiver and an `otlphttp` exporter -- and nothing reads it now."
    )


def test_mypy_excludes_the_synthesized_cloud_assembly() -> None:
    """`make lint` must survive a tree that has been deployed from.

    `infra` is one of mypy's four declared bases, `make deploy` synthesizes into
    `infra/cdk.out/`, and an `ecr_assets.DockerImageAsset` copies the whole agent source tree
    in there as `asset.<hash>/src/coding_agent/`. Without an exclude mypy finds every module
    in that package twice and stops with "Source file found twice under different module
    names", having checked nothing.

    Worth pinning because of how it presents rather than how it breaks: `cdk.out/` is in
    `.gitignore`, so a clean tree passes, the identical tree one deploy later fails, and
    `git status` shows nothing that could explain it. The message names an `__init__.py` in a
    directory the operator has never heard of.
    """
    config = (REPO_ROOT / "mypy.ini").read_text(encoding="utf-8")
    excludes = [
        line.split("=", 1)[1].strip() for line in config.splitlines() if line.startswith("exclude")
    ]
    assert excludes, (
        "mypy.ini declares no `exclude`, so `make lint` fails on any tree somebody has run "
        "`make deploy` (or `cdk synth`) in -- see this test's docstring for why that is "
        "invisible from `git status`"
    )
    pattern = re.compile(excludes[0].replace("(?x)", ""), re.VERBOSE)
    assert pattern.search("infra/cdk.out/asset.abc123/src/coding_agent/state.py"), (
        f"mypy.ini's exclude {excludes[0]!r} does not match a synthesized asset path. The "
        f"duplicate it has to keep out is the agent source the Docker image asset copies into "
        f"cdk.out, not cdk.out's own JSON."
    )
    # The exclude must not reach the tree it is meant to leave alone: `agent/src` is checked
    # through its own base, and an over-broad pattern here would silently stop checking it.
    assert not pattern.search("agent/src/coding_agent/state.py"), (
        f"mypy.ini's exclude {excludes[0]!r} also matches the real agent source, so the copy "
        f"is not what is being skipped -- the package is"
    )
