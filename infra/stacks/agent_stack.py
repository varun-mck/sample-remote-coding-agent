"""`AgentStack`: the fleet AgentCore runtime, its ARM64 image, and the execution-role boundary.

The runtime uses **PUBLIC** network mode (invariant 9): no VPC, NAT, or interface
endpoints. Account-wide controls (WAF, spend budget) live in `infra/stacks/account_stack.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from constructs import Construct

# Spelled out rather than imported, for the same reason `api_stack.ROUTES` is a literal:
# `api/src` is not on the CDK app's import path. It must stay identical to
# `api/src/handlers/observability.py::LOG_GROUP_PREFIX`, because the per-configuration
# policy scopes the container's `logs:*` grants to this prefix and a boundary that does not
# cover it denies every log line the runtime writes -- which surfaces as a runtime with no
# logs, i.e. as the one symptom that makes the cause unfindable.
RUNTIME_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/runtimes"


def execution_role_boundary_statements(
    *,
    account: str,
    region: str,
    asset_repository_arn: str,
    gateway_arn: str = "",
    app_name: str = "rca",
) -> list[iam.PolicyStatement]:
    """The permissions boundary every per-configuration execution role is minted under.

    A boundary is a **ceiling**, not a grant: nothing here gives a role anything. It is
    therefore the union of what `provisioner._execution_role_policy` grants and no wider --
    an action the inline policy allows and this list omits is silently denied at runtime,
    and an action this list allows and the inline policy omits is dead surface a future
    inline policy could take without review. Both halves of that sentence are now
    enforced rather than promised:
    `tests/test_api_grant_contract.py::test_the_boundary_permits_every_action_the_inline_policy_grants`
    for the first, and `::test_the_boundary_permits_no_action_the_inline_policy_does_not_grant`
    reading the classification in `tests/execution_boundary.py` for the second.

    The *resource* dimension is deliberately wider in three statements -- `MayTouchOwnRows`,
    `MayUseGitHubCredential` and `MayWriteRuntimeLogs`, each for the reason
    its own comment below gives -- and each of the four is declared in
    `execution_boundary.WIDER_RESOURCES`, so widening a fifth costs a line in that table with
    a reason rather than passing as a silent change. Same idiom as
    `provisioner._execution_role_policy`'s docstring pointing back at this ceiling.

    `app_name` is absent: the only statement that needed it was `MayReadOneSecret`, naming
    the `rca/github/*` secret prefix, and the container uses no secret. A boundary
    parameter that no statement reads is a signature a reader has to check against the
    body.

    `asset_repository_arn` is the one parameter that is not a name: the union above cannot
    be written without naming the repository the agent image lives in, and this function is
    stack-free (below), so it cannot reach the asset itself. It is an ARN rather than an
    image URI because at synth time `ecr_assets.DockerImageAsset.image_uri` is an
    unresolved CDK token -- there is no `<account>.dkr.ecr...` string here to parse, only a
    `${Token[...]}` placeholder -- whereas `.repository.repository_arn` is a token that
    resolves to exactly the repository the provisioner derives from `AGENT_IMAGE_URI` at run
    time. Required rather than defaulted, for the reason `provisioner._fleet_network`'s
    docstring gives about `live`: a default is how a future caller loses the grant without
    noticing, and a ceiling one action too narrow is silent at run time.

    Pure and stack-free on purpose: `iam.PolicyStatement.to_json()` needs no `Stack`, so
    `tests/test_grant_contract.py` reads these statements directly. That test is the only
    thing standing between invariant 21 and a fleet that cannot invoke a model, because
    this stack cannot be synthesized in CI (the image asset needs a Docker daemon).
    """
    statements: list[iam.PolicyStatement] = [
        iam.PolicyStatement(
            sid="MayTouchOwnRows",
            effect=iam.Effect.ALLOW,
            # The four actions the container performs, and no `DeleteItem`, no `Scan`, no
            # `BatchWriteItem`: `configurations.list_` scans, but that is a control-plane
            # route, not a thing any runtime does.
            actions=[
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Query",
            ],
            # `table/*` rather than the two table ARNs: `DataStack` owns the names and is a
            # sibling stack, so naming them here would couple two deploys for no security.
            # The `dynamodb:LeadingKeys` fence that stops one repository reading another's
            # rows lives in the per-configuration policy (invariant 31); a boundary's job
            # is to stop a *wider action*, not to restate a condition -- restating it here
            # would mean every future fence had to be written twice to work at all.
            resources=[f"arn:aws:dynamodb:{region}:{account}:table/*"],
        ),
        iam.PolicyStatement(
            sid="MayUseGitHubCredential",
            effect=iam.Effect.ALLOW,
            # The ceiling over `provisioner._execution_role_policy`'s `UseGitHubCredential`:
            # the two calls that exchange this configuration's id for the GitHub token
            # AgentCore Identity holds. The Secrets Manager read a refresh needs is
            # `MayRefreshGitHubCredential` below.
            actions=[
                "bedrock-agentcore:GetWorkloadAccessToken",
                "bedrock-agentcore:GetResourceApiKey",
                "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                "bedrock-agentcore:GetResourceOauth2Token",
            ],
            # Declared in `execution_boundary.WIDER_RESOURCES`, and the widest entry in this
            # list by some distance. It is not a choice: `GetWorkloadAccessTokenForUserId`
            # is denied on an individual `workload-identity/<name>` ARN and allowed only on
            # `workload-identity-directory/*`, and `GetResourceOauth2Token` is denied on the
            # credential provider's own ARN, on `token-vault/default/*` and on
            # `token-vault/*`, and allowed only on `*`. No condition key exists for the user
            # id either. So neither this ceiling nor the grant under it can name one
            # repository's credential. `docs/security.md`'s "What remains exposed" states the
            # consequence rather than letting the shape of this list imply a fence.
            resources=["*"],
        ),
        iam.PolicyStatement(
            sid="MayRefreshGitHubCredential",
            effect=iam.Effect.ALLOW,
            # The ceiling over `provisioner._execution_role_policy`'s `RefreshGitHubCredential`.
            # AgentCore Identity reads the GitHub App's client secret **with the caller's
            # permissions** when it refreshes a token, so a container that cannot read it
            # exhausts its current token and cannot refresh.
            #
            # `GetSecretValue` only, and declared in `execution_boundary.WIDER_RESOURCES`: the
            # boundary is minted once per deploy, before any GitHub App is connected, so it can
            # only name Identity's own service-linked prefix. The grant names the one literal
            # ARN. The trailing `*` also absorbs the random suffix every secret ARN carries.
            actions=["secretsmanager:GetSecretValue"],
            resources=[
                f"arn:aws:secretsmanager:{region}:{account}:secret:bedrock-agentcore-identity!*"
            ],
        ),
        iam.PolicyStatement(
            sid="MayReadKiroApiKey",
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[
                f"arn:aws:secretsmanager:{region}:{account}:secret:{app_name}/kiro-api-key*"
            ],
        ),
        iam.PolicyStatement(
            sid="MayWriteRuntimeLogs",
            effect=iam.Effect.ALLOW,
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            # Tier 1 #9: the runtime log-group namespace, not the account's. The trailing
            # `*` sits directly after the prefix with no `/`, which is what makes one
            # resource cover both the log-group ARN and the `...:log-stream:*` form the
            # inline policy spells separately. Region and account are wildcards because the
            # name prefix is the fence.
            resources=[f"arn:aws:logs:*:*:log-group:{RUNTIME_LOG_GROUP_PREFIX}*"],
        ),
        iam.PolicyStatement(
            sid="MayAuthenticateToEcr",
            effect=iam.Effect.ALLOW,
            actions=["ecr:GetAuthorizationToken"],
            # `*` is what the action *requires*, not laziness: `ecr:GetAuthorizationToken`
            # supports no resource types, so a scoped ARN here matches no authorization at
            # all. The token it hands back grants nothing on its own -- it still authorizes
            # against `MayPullAgentImage`, which names one repository.
            resources=["*"],
        ),
        iam.PolicyStatement(
            sid="MayPullAgentImage",
            effect=iam.Effect.ALLOW,
            # This pair is what makes `CreateAgentRuntime` succeed at all: the **runtime**
            # pulls `AGENT_IMAGE_URI` with the execution role before any container code
            # runs, and without these four actions the create fails `AccessDenied` with a
            # null `errorMessage` and no runtime is ever created.
            actions=[
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            ],
            # The repository the asset lives in, which is the one
            # `provisioner._asset_repository_arn` derives from `AGENT_IMAGE_URI`: one
            # repository named from one source, so the ceiling cannot drift off the grant.
            resources=[asset_repository_arn],
        ),
    ]
    if gateway_arn:
        statements.append(
            iam.PolicyStatement(
                sid="MayInvokeGateway",
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeGateway"],
                resources=[gateway_arn],
            )
        )
    statements.append(
        iam.PolicyStatement(
            sid="MayNotEscalate",
            # The explicit deny is why the boundary exists at all. Without it the
            # provisioner's own `iam:CreateRole` grant is enough to mint an administrator
            # role -- the boundary condition on that grant only checks that *some* boundary
            # is attached, so this list is what makes attaching one mean something. And the
            # first two AgentCore entries are the self-dispatch fence: a container that can
            # call the AgentCore control plane can invoke its own runtime, which turns one
            # compromised task into an unbounded fan-out that bills like a bug.
            #
            # Control-plane and memory actions are listed literally (no `bedrock-agentcore:*`
            # wildcard) so an explicit Deny does not shadow unrelated Allows.
            #
            # `ecr:*` is deliberately absent: an explicit Deny beats the two Allow statements
            # above it, so including it makes the image-pull grant a dead letter -- the
            # runtime cannot pull the agent image. Nothing widens: a boundary is a whitelist, so `ecr:PutImage`,
            # `ecr:DeleteRepository`, `bedrock-agentcore:ListAgentRuntimes` and every other
            # unlisted action are denied by not being Allowed above. `s3:*` **stays**: an ECR
            # layer download is a presigned S3 URL, which needs no `s3:` permission on the
            # caller.
            effect=iam.Effect.DENY,
            actions=[
                "iam:*",
                "sts:AssumeRole",
                "organizations:*",
                "bedrock-agentcore:InvokeAgentRuntime",
                "bedrock-agentcore:StopRuntimeSession",
                "bedrock-agentcore:CreateAgentRuntime",
                "bedrock-agentcore:UpdateAgentRuntime",
                "bedrock-agentcore:DeleteAgentRuntime",
                "bedrock-agentcore:CreateMemory",
                "bedrock-agentcore:UpdateMemory",
                "bedrock-agentcore:DeleteMemory",
                "bedrock-agentcore:ListMemories",
                "bedrock-agentcore:DeleteEvent",
                "bedrock-agentcore:DeleteMemoryRecord",
                "bedrock-agentcore:BatchDeleteMemoryRecords",
                # The Identity control plane, denied by name for exactly the reason the seven
                # memory actions above are. `MayUseGitHubCredential` has to allow
                # `GetResourceOauth2Token` on `*`, so a container is one widening away from
                # reconfiguring the credential provider every repository in the deployment
                # shares -- which would let it point the provider at an attacker's OAuth app,
                # or delete it and take the whole fleet's access with it. `SetTokenVaultCMK`
                # is here for the same reason: re-keying the vault is a denial of service on
                # every stored authorisation. Still no wildcard: `Get*` would shadow
                # `GetResourceOauth2Token` and `GetWorkloadAccessTokenForUserId` and break
                # every task.
                "bedrock-agentcore:CreateOauth2CredentialProvider",
                "bedrock-agentcore:UpdateOauth2CredentialProvider",
                "bedrock-agentcore:DeleteOauth2CredentialProvider",
                "bedrock-agentcore:ListOauth2CredentialProviders",
                "bedrock-agentcore:CreateApiKeyCredentialProvider",
                "bedrock-agentcore:UpdateApiKeyCredentialProvider",
                "bedrock-agentcore:DeleteApiKeyCredentialProvider",
                "bedrock-agentcore:CreateWorkloadIdentity",
                "bedrock-agentcore:UpdateWorkloadIdentity",
                "bedrock-agentcore:DeleteWorkloadIdentity",
                "bedrock-agentcore:ListWorkloadIdentities",
                "bedrock-agentcore:CreateTokenVault",
                "bedrock-agentcore:SetTokenVaultCMK",
                # Secrets Manager by **literal** action, not `secretsmanager:*`. A wildcard Deny
                # beats every Allow, so it would also kill `MayRefreshGitHubCredential`'s
                # `GetSecretValue` and put the eight-hour failure back with nothing in either
                # document naming the cause -- the same trap the memory actions record. These
                # are the actions a careless widening would add: writing, rotating or deleting
                # the client secret every repository's credential depends on, and enumerating
                # secrets the container was not given.
                "secretsmanager:CreateSecret",
                "secretsmanager:PutSecretValue",
                "secretsmanager:UpdateSecret",
                "secretsmanager:DeleteSecret",
                "secretsmanager:RotateSecret",
                "secretsmanager:RestoreSecret",
                "secretsmanager:PutResourcePolicy",
                "secretsmanager:ListSecrets",
                "secretsmanager:BatchGetSecretValue",
                "s3:*",
                "sqs:*",
            ],
            resources=["*"],
        ),
    )
    return statements


class AgentStack(cdk.Stack):
    """The agent runtime's prerequisites: the ARM64 image and the permissions boundary."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app_name: str,
        git_author_name: str = "",
        git_author_email: str = "",
        gateway_url: str = "",
        gateway_arn: str = "",
        tasks_table_name: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -- the image ------------------------------------------------------------
        #
        # `agent/Dockerfile`: ARM64 platform (invariant 2) and no Dockerfile `ENV WORK_ROOT`
        # (runtime env vars override only). `tests/test_repo_contracts.py::test_the_agent_image_*`
        # holds the Dockerfile contract; CI cannot synthesize this stack without Docker.
        self.image = ecr_assets.DockerImageAsset(
            self,
            "AgentImage",
            directory=str(Path(__file__).resolve().parents[2] / "agent"),
            # Invariant 2, and silent when wrong: CloudFormation accepts an x86 image
            # happily and AgentCore then fails to start it with no signal anywhere -- no
            # exec-format error in the runtime's logs, because there are no logs. The only
            # evidence is a runtime stuck out of `READY`.
            #
            # The built image is ~1.3 GB against AgentCore's 2 GB cap. Weigh new tooling in
            # the Dockerfile against that headroom: the limit is enforced at
            # `CreateAgentRuntime`, so overshooting it fails the provisioning of every
            # configuration at once rather than one build.
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        # PUBLIC network mode: no VPC, NAT, or interface endpoints in this fleet.

        # -- the permissions boundary --------------------------------------------
        #
        # The one physical name this repository sets on purpose. Everything else is left to
        # CloudFormation so an accidental replacement is its problem, but
        # `provisioner.EXECUTION_ROLE_BOUNDARY_ARN` is compared as a *string* inside the
        # `iam:PermissionsBoundary` condition on the provisioner's `iam:CreateRole` grant,
        # and a generated name makes that condition unreadable in the console at exactly
        # the moment someone is trying to work out why a create was denied.
        #
        # It carries `self.region` because an IAM managed policy name is **account-global**
        # while every other stack in this app is per-region: without the suffix a second
        # deployment in a second region dies at `CreateManagedPolicy` with
        # `EntityAlreadyExists`, and it dies AFTER the ~1.3 GB image build and push, which is
        # the most expensive step of the whole deploy. Nothing compares the literal --
        # `api_stack` takes `role_boundary.managed_policy_arn` by reference and the
        # provisioner reads the ARN out of its environment -- so the suffix costs nothing but
        # the readability above, and `rca-execution-boundary-us-west-2` still names the region
        # whose roles it bounds.
        self.role_boundary = iam.ManagedPolicy(
            self,
            "ExecutionRoleBoundary",
            managed_policy_name=f"{app_name}-execution-boundary-{self.region}",
            description=(
                f"Ceiling on every {app_name} per-configuration AgentCore execution role "
                "(invariant 17)"
            ),
            statements=execution_role_boundary_statements(
                account=self.account,
                region=self.region,
                # The asset's own repository, as a token that resolves to a real ARN.
                # `self.image.image_uri` cannot be used: at synth time it is an unresolved
                # token with no repository name in it to parse. This is the same repository
                # the provisioner derives from `AGENT_IMAGE_URI` at run time, so grant and
                # ceiling name one repository from one source.
                asset_repository_arn=self.image.repository.repository_arn,
                gateway_arn=gateway_arn,
                app_name=app_name,
            ),
        )

        self.execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name=f"{app_name}-execution",
            path="/rca/",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            permissions_boundary=self.role_boundary,
        )
        self.execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:Query",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetResourceApiKey",
                ],
                resources=["*"],
            )
        )
        if gateway_arn:
            self.execution_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock-agentcore:InvokeGateway"],
                    resources=[gateway_arn],
                )
            )

        kiro_secret_name = f"{app_name}/kiro-api-key"
        runtime_env = {
            "AGENT_GIT_AUTHOR_NAME": git_author_name,
            "AGENT_GIT_AUTHOR_EMAIL": git_author_email,
            "WORK_ROOT": "/mnt/workspace/work",
            "TASKS_TABLE": tasks_table_name,
            "KIRO_API_KEY_SECRET": kiro_secret_name,
        }
        if gateway_url:
            runtime_env["BEDROCK_AGENTCORE_GATEWAY_URL"] = gateway_url

        self.runtime = agentcore.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name=f"{app_name}_fleet",
            role_arn=self.execution_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=self.image.image_uri
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            platform_version="V2",
            protocol_configuration="HTTP",
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                max_lifetime=28800
            ),
            environment_variables=runtime_env,
        )
        self.execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{kiro_secret_name}*",
                    # AgentCore Identity reads the GitHub PAT secret when GetResourceApiKey runs.
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity!*",
                ],
            )
        )
        self.runtime.node.add_dependency(self.execution_role)
        self.agent_runtime_arn: str = self.runtime.attr_agent_runtime_arn

        cdk.CfnOutput(self, "AgentRuntimeArn", value=self.agent_runtime_arn)
        cdk.CfnOutput(self, "ExecutionRoleArn", value=self.execution_role.role_arn)
