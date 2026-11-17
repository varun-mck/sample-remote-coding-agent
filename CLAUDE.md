# CLAUDE.md

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
| `agent/` | AgentCore runtime image, harness, AIPDLC bundle |
| `api/` | Task API Lambdas |
| `infra/` | CDK: Data, Runtime, Gateway, Api, Account stacks |
| `mcp/` | Claude Code MCP server |
| `plugins/sample-rca/` | Plugin pin and remote skill |

## Core invariants

1. Never run the harness on the invocation thread (`server.py` uses `add_async_task`).
2. ARM64 everywhere.
3. Git credentials never enter the environment or command line; use `credentials.fetch_token`
   and `git_ops._askpass`.
4. `publish_branch` and Gateway create-PR tools are the only publishers; hooks force draft
   when `run_verification` did not pass.
5. `setting_sources=[]`, `strict_mcp_config=True`, no `WebFetch`/`WebSearch`.
6. Gateway MCP is allowed for Git providers; harness MCP is in-process only.
7. One runtime per fleet; one session per task.
8. Every dependency has an upper bound at the next major version (invariant 29).
9. Interface endpoints stay private; default placement is one AZ (`-c endpointAzCount=`).

## Changing `mcp/`

Land as two commits: the change, then update `plugins/sample-rca/.mcp.json` pin.