# Operations

## Submit a task

```bash
export AWS_REGION=us-east-1   # where the API is deployed
make task REPO=https://github.com/owner/repo BRANCH=feat/x PROMPT='Your task'
# optional model and reasoning effort for the remote kiro-cli run
make task REPO=... BRANCH=... PROMPT='...' MODEL=claude-sonnet-4.6 EFFORT=high
# or
python scripts/rca_cli.py submit --repo ... --branch ... --prompt '...' --model ... --effort high
```

`model` is passed to `kiro-cli chat --model` (ids from `kiro-cli chat --list-models`); if you
omit it, the fleet uses **claude-haiku-5.5**. Set runtime env `KIRO_DEFAULT_MODEL` to change
that default. An id the fleet's Kiro account cannot use logs a warning and the run continues
on Kiro's account default. `effort` is one of `low`, `medium`, `high`, `xhigh`, `max`. Optional.

Requires IAM permission to invoke the API function URL (`lambda:InvokeFunctionUrl`).

## Submit from Kiro (power)

`kiro-rca-power/` is a Kiro power that connects to the **task gateway** (`SampleCodingAgentTaskGateway`)
over HTTPS. Kiro signs in through the Cognito hosted UI (email and password); there is no local
MCP server and no AWS CLI profile.

1. `make deploy EMAIL=you@example.com`
2. Cognito console → user pool `rca-tasks` → **Create user**: username = work email, temporary
   password; mark email verified if asked.
3. `make power` (renders `mcp.json` from `~/.rca/stack-outputs.json` and installs the power)
4. In Kiro: connect **rca-fleet**, complete hosted UI login, set a permanent password on first login.

Tools are named `rca-tasks___submit_task`, `rca-tasks___get_task`, `rca-tasks___list_tasks`,
`rca-tasks___cancel_task`. See [kiro-rca-power/README.md](../kiro-rca-power/README.md).

## Poll status

```bash
make task-get ID=tsk_1234567890_abcdef
python scripts/rca_cli.py list --status RUNNING
```

Fields of interest:

- `status`: `QUEUED` → `DISPATCHED` → `RUNNING` → `SUCCEEDED` | `FAILED`
- `steps`: harness step names appended during the run
- `result`: agent's final message on success
- `error`: failure reason
- `model`, `effort`: what was requested, when given
- `durationSeconds`, `kiroCredits`

## Logs

Runtime: CloudWatch `/aws/bedrock-agentcore/runtimes/<runtime-name>-DEFAULT`.

Per-task debug files (on the runtime volume, not in git):

- `.../work/<taskId>/.agent/kiro-stream.jsonl` — raw Kiro stream-json
- `.../work/<taskId>/.agent/gateway-bridge.log` — Gateway MCP bridge (tool names and arg keys only)

Set `LOG_LEVEL=DEBUG` on the runtime for more harness logging.

## AgentCore Gateway (GitHub PR tools)

The CDK stack deploys a minimal GitHub OpenAPI target and attaches the `rca_github` API-key
provider to it as `Authorization: Bearer <PAT>`; the gateway role may read that key. The
provider must exist before the gateway stack deploys. Git clone uses Identity; REST PR creation
uses Gateway. If `github___create_pull_request` returns an internal error, check that the
`github` target shows the credential provider in the console and read `detail=` lines in the
per-task gateway bridge log tail in the runtime log stream.

## GitHub credential

Workload identity: `rca_git_wl`. API key provider: `rca_github` (PAT in Secrets Manager
under `bedrock-agentcore-identity!default/apikey/...`).

Verify token fetch (no `--scopes` on CLI):

```bash
export AWS_REGION=us-east-1
WL_TOKEN=$(aws bedrock-agentcore get-workload-access-token --workload-name rca_git_wl --query workloadAccessToken --output text)
aws bedrock-agentcore get-resource-api-key \
  --workload-identity-token "$WL_TOKEN" \
  --resource-credential-provider-name rca_github
```

## Redeploy after harness changes

```bash
make deploy EMAIL=you@example.com
```

Redeploy updates the ARM64 image and execution role (Gateway invoke, secrets, DynamoDB).