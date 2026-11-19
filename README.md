# Sample remote coding agent

Fleet AgentCore runtime that runs [Kiro CLI](https://kiro.dev) against a Git repository,
with Git credentials from AgentCore Identity and provider APIs through AgentCore Gateway MCP.

## Quick start

```bash
make setup
make deploy EMAIL=you@example.com   # first deploy ~15–25 minutes (ARM64 image)
make task REPO=https://github.com/org/repo BRANCH=feat/x PROMPT='Update the greeting'
make task-get ID=tsk_...
```

The API URL is written to `~/.rca/endpoint.json` after deploy. Override with `RCA_API_URL`.

## Verification

```bash
make test
make lint
```

See [AGENTS.md](AGENTS.md) for invariants and layout. Operators: [docs/operations.md](docs/operations.md).

## Task outcomes

A task ends **SUCCEEDED** when Kiro reports success, **FAILED** otherwise. The agent's final
message is stored in DynamoDB as `result` (no required pull request). Poll with
`scripts/rca_cli.py get` or `make task-get ID=...`.