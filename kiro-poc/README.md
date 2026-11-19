# Kiro CLI local POC

Headless **Kiro CLI** on your laptop (interactive `kiro-cli login`, no API key) against a throwaway copy of a tiny repo. It checks whether Kiro can cover what this project's Claude Agent SDK harness does:

- MCP tools: `run_verification`, `publish_branch`, fake `create_pull_request`
- PreToolUse hook: block `git push` / `git remote`, rewrite PR `head` / `base` / `draft`
- PostToolUse hook: capture PR URL
- Repo `.kiro/steering` is loaded as-is (no isolation)

Inspired by [Claude Code on AgentCore](https://github.com/aws-samples/anthropic-on-aws/tree/main/claude-code-on-agentcore); this POC does **not** deploy to AWS.

## Prerequisites

1. **Kiro CLI 3.4.0+** (for PreToolUse `updatedInput` on PR args):

   ```bash
   brew upgrade --cask kiro-cli   # or: kiro-cli update
   kiro-cli --version
   ```

2. **Sign in** (org SSO / Builder ID — not `KIRO_API_KEY`):

   ```bash
   kiro-cli login
   kiro-cli whoami
   ```

3. **Repo venv** (for MCP servers and hooks):

   ```bash
   cd .. && make setup
   ```

## Run

```bash
./kiro-poc/scripts/preflight.sh
./kiro-poc/scripts/run_scenarios.sh
```

Single task:

```bash
.venv/bin/python kiro-poc/run_task.py \
  --prompt "Change greeting to hello devs, verify, publish, open PR."
```

Keep the temp workspace:

```bash
.venv/bin/python kiro-poc/run_task.py --keep --prompt "..."
```

If `--agent kiro-poc` is not supported on your CLI build, preflight writes `kiro-poc/.preflight.env` and scenarios use `--fallback-config` (standalone `.kiro/hooks` + `settings/mcp.json`).

## Layout

| Path | Role |
|------|------|
| [`run_task.py`](run_task.py) | Copy fixture, git branch, agent profile, run `kiro-cli`, parse `stream-json` |
| [`mcp/harness_server.py`](mcp/harness_server.py) | Harness MCP |
| [`mcp/gateway_server.py`](mcp/gateway_server.py) | Fake gateway MCP |
| [`hooks/policy_hook.py`](hooks/policy_hook.py) | Policy command hook |
| [`fixtures/sample-repo/`](fixtures/sample-repo/) | Passing tests + `.kiro/steering` |
| [`fixtures/sample-repo-broken-tests/`](fixtures/sample-repo-broken-tests/) | Failing tests for draft-PR scenario |
| [`scripts/preflight.sh`](scripts/preflight.sh) | Version, login, headless probe |
| [`scripts/run_scenarios.sh`](scripts/run_scenarios.sh) | Five automated checks |

## Scenarios

1. **Happy path** — edit, verify, publish, PR; `draft=false`, steering adds `CHANGELOG.md`
2. **Push blocked** — `git push` denied by hook
3. **Draft when unverified** — broken tests → `draft=true`
4. **Repo `.kiro/`** — covered by scenario 1 (`CHANGELOG.md`)
5. **Merge denied** — `merge_pull_request` blocked

Each scenario uses Kiro credits from your subscription.

## Notes

- No real `git push` or GitHub calls.
- State is shared via `<workspace>/.agent/state.json`.
- This tree is outside `make lint` / `make test` for the main repo.