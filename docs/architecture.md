# Architecture

```mermaid
flowchart LR
  Operator["scripts/rca_cli.py (SigV4)"] --> Api["Function URL /tasks"]
  Api --> Queue[SQS] --> Dispatcher --> Runtime["AgentCore runtime (server.py)"]
  Runtime --> Harness["harness.run_task"]
  Harness -->|"clone/checkout via askpass"| Git[GitHost]
  Harness --> Kiro["kiro-cli rca agent"]
  Kiro -->|shell| Workspace
  Kiro -->|"@agentcore-gateway"| Bridge["agentcore_gateway_bridge"] --> Gateway["AgentCore Gateway"]
  Harness -->|"status + result"| Ddb[DynamoDB]
```

Components:

| Piece | Role |
|---|---|
| Task API | IAM-signed Lambda function URL: create, list, get, cancel tasks |
| Dispatcher | SQS → AgentCore `InvokeAgentRuntime` with task payload |
| Runtime | `coding_agent.server` off-thread harness |
| Kiro `rca` agent | `read` / `write` / `shell` + Gateway MCP |
| Gateway | OpenAPI targets (GitHub, Jira, Confluence, …) |
| Identity | Workload `rca_git_wl` + per-host credential providers |

Debug artefacts per task (under the runtime work dir): `kiro-stream.jsonl`,
`gateway-bridge.log`; CloudWatch log group `/aws/bedrock-agentcore/runtimes/...`.