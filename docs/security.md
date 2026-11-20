# Security

## Credentials

- Git tokens flow only through `GIT_ASKPASS` (`git_ops._askpass`); they are not logged or
  placed in environment variables for the Kiro process beyond the askpass script lifetime.
- Kiro API key: Secrets Manager `rca/kiro-api-key` (or `KIRO_API_KEY` for local dev only).
- Task API (engineers): IAM SigV4 on the Lambda function URL; no API keys in requests.
- Task gateway (MCP clients): Cognito access tokens verified by AgentCore Gateway (`CUSTOM_JWT`).
  A request interceptor stamps `submittedBy` from the token; clients cannot supply it. The MCP
  adapter forwards only calls from this deployment's gateway id to the route Lambdas.

## Agent capabilities

The fleet `rca` agent may:

- Read/write files in the cloned repo
- Run shell commands in the repo (including verify commands you configure)
- Call AgentCore Gateway MCP tools (Git host, Jira, Confluence, …)

It must not merge or approve pull requests (prompt policy). Outcomes are not gated on opening a PR.

## Network

Runtime network mode is **PUBLIC** (no VPC). Egress reaches Git hosts, Gateway, AWS APIs, and
Kiro endpoints configured in the image.

## Data

Task rows live in DynamoDB (`Tasks` table). No AgentCore Memory in this fleet.