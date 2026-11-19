# AGENTS.md

Rules for changing this repository. Operator reference: `README.md`, `docs/operations.md`,
`docs/security.md`.

## Verification

```bash
make setup
make test
make lint
```

## Layout

| Path | What it is |
|---|---|
| `agent/` | AgentCore runtime image and Kiro harness |
| `api/` | Task API Lambdas |
| `infra/` | CDK: Data, Runtime, Gateway, Api, Account stacks |
| `scripts/rca_cli.py` | Operator CLI (SigV4 to the task API) |
| `kiro-rca-power/` | Kiro power: MCP server (SigV4 to the task API) and dispatch skill |

## Core invariants

1. Never run the harness on the invocation thread (`server.py` uses `add_async_task`).
2. ARM64 everywhere.
3. Git credentials never enter the environment or command line; use `credentials.fetch_token`
   and `git_ops._askpass`.
4. Git clone uses AgentCore Identity via `git_ops._askpass`; provider REST (PR, Jira,
   Confluence, …) uses AgentCore Gateway MCP only.
5. Per-task `KIRO_HOME` for the harness `rca` agent only; the target repo's `.kiro/` is trusted as cloned.
6. AgentCore Gateway MCP (stdio IAM bridge) for provider tools; verify via shell when configured.
7. One runtime per fleet; one session per task.
8. Task outcome is free-form: `SUCCEEDED` or `FAILED` from the Kiro run, with the agent's final
   message stored as `result` (no mandatory PR).
9. AgentCore runtime uses `PUBLIC` network mode (no VPC in this fleet).
10. Every dependency has an upper bound at the next major version in project requirements files.