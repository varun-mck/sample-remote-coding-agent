# Operations

## Submit a task

```bash
export AWS_REGION=us-east-1   # where the API is deployed
make task REPO=https://github.com/owner/repo BRANCH=feat/x PROMPT='Your task'
# or
python scripts/rca_cli.py submit --repo ... --branch ... --prompt '...'
```

Requires IAM permission to invoke the API function URL (`lambda:InvokeFunctionUrl`).

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
- `durationSeconds`, `kiroCredits`

## Logs

Runtime: CloudWatch `/aws/bedrock-agentcore/runtimes/<runtime-name>-DEFAULT`.

Per-task debug files (on the runtime volume, not in git):

- `.../work/<taskId>/.agent/kiro-stream.jsonl` — raw Kiro stream-json
- `.../work/<taskId>/.agent/gateway-bridge.log` — Gateway MCP bridge (tool names and arg keys only)

Set `LOG_LEVEL=DEBUG` on the runtime for more harness logging.

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