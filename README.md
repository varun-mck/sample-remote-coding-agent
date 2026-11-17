# AIPDLC remote coding agent on Amazon Bedrock AgentCore

Turn a scoped coding task into a pull request from Claude Code. Each task runs in an
isolated AgentCore session in your AWS account, calls Bedrock through the execution role,
uses private VPC networking, and ends with a pull request for human review.

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

**AgentCore Runtime V2 only** in VPC mode. Supported regions include `us-east-1`, `us-east-2`,
`us-west-1`, `us-west-2`, `eu-west-1`, `eu-west-2`, `eu-central-1`, `ap-southeast-1`,
`ap-southeast-2`, `ap-northeast-1`, `ap-south-1`, `ca-central-1`; the full set is
`agent_stack.AGENTCORE_VPC_ZONE_IDS`.

- **Control plane:** API Gateway, Lambda (`tasks`, `dispatcher`), SQS, DynamoDB tasks table,
  Bedrock prompt guardrail.
- **Runtime:** one static AgentCore runtime (VPC, ARM64) running the harness and baked
  AIPDLC bundle (`agent/aipdlc/`).
- **Git:** AgentCore Gateway MCP targets per provider; short-lived credentials from AgentCore
  Identity (`scripts/providers.sh`).

## Costs

Idle infrastructure is roughly **$83–85/month** in us-east-1 (one-AZ interface endpoints,
NAT, queues, table). Bedrock usage is capped at **$50/month** via AWS Budgets and an
automatic spend deny policy (`make budget-reset` lifts it). See [docs/operations.md](docs/operations.md).

## Verification

```bash
make test
make lint
```