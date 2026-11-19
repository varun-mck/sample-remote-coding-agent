# AIPDLC remote coding agent on Amazon Bedrock AgentCore

Turn a scoped coding task into a pull request using **Kiro CLI** inside AgentCore. Each task
runs in an isolated AgentCore session (PUBLIC network mode), uses AgentCore Identity for Git,
and ends with a pull request for human review.

## Quick start

```bash
make setup
make preflight
make deploy EMAIL=you@example.com
```

Submit tasks with the `sample-rca` plugin (`submit_task`) or `POST /tasks` on the IAM-
authorised API. Pass a `repositoryUrl` on GitHub, Bitbucket Cloud, or another configured
host.

## Architecture

See [docs/architecture-aipdlc.md](docs/architecture-aipdlc.md) for a simple component/flow diagram.

**AgentCore Runtime V2** in **PUBLIC** network mode. Supported regions include `us-east-1`, `us-east-2`,
`us-west-1`, `us-west-2`, `eu-west-1`, `eu-west-2`, `eu-central-1`, `ap-southeast-1`,
`ap-southeast-2`, `ap-northeast-1`, `ap-south-1`, `ca-central-1`; the full set is
`agent_stack.AGENTCORE_VPC_ZONE_IDS`.

- **Control plane:** a Lambda function URL (`AWS_IAM` auth, fronting a `router` Lambda;
  API Gateway is denied by SCP), Lambda (`tasks`, `dispatcher`), SQS, DynamoDB tasks table,
  Bedrock prompt guardrail.
- **Runtime:** one static AgentCore runtime (PUBLIC, ARM64) running Kiro CLI and harness MCP.
  Agents, skills, and steering come from each target repo's `.kiro/`.
- **Git:** AgentCore Gateway MCP targets per provider; short-lived credentials from AgentCore
  Identity (`scripts/providers.sh`).

## Costs

Idle infrastructure is roughly **$15–25/month** in us-east-1 (queues, table, Lambda,
AgentCore). Optional Bedrock budget (`-c bedrockBudgetEnabled=true`) caps Guardrail screening
only; model spend is through Kiro. See [docs/operations.md](docs/operations.md).

## Verification

```bash
make test
make lint
```