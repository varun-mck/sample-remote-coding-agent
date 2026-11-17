# Operations reference

Reference for deploying, running and removing a fleet. `README.md` has the walkthrough.

## Deploying

```bash
make setup                         # create .venv and install all four packages
make preflight                     # prerequisite checks only; read-only, safe at any time
make deploy EMAIL=you@example.com  # the whole deploy, first time included
make deploy EMAIL=you@example.com STACK_PREFIX=Foo  # a second, separate fleet
```

**`make preflight`** checks: a Docker daemon (the ~1.3 GB ARM64 agent image is built on this
machine), `uv` on `PATH`, Node `^20.19 || >=22.12`, AWS credentials and the region they resolve
to, CDK bootstrap in that account and region, that Bedrock AgentCore answers there, and that
every inference profile the deploy forwards is `ACTIVE` there. Each failure prints a
`preflight: FAILED --` line and a `fix:` line.

**`make deploy`** runs `preflight`, then deploys five stacks: data, gateway, runtime, API, and
an account stack in `us-east-1` with the monthly Bedrock budget (notifications go to `EMAIL`).
It takes about 15–25 minutes, mostly building and pushing the image. It prints the API URL and
the region, writes `~/.rca/endpoint.json` for the MCP server, and offers to run
`scripts/providers.sh` when credentials are not yet configured. Both targets are idempotent, so
a re-run is the health check.

**`STACK_PREFIX`** renames all five stacks, so `STACK_PREFIX=Foo` is a separate fleet in the
same account. The plugin follows it through its `stack_name` setting (`RCA_STACK_NAME`).

**Model profiles** default to `global.`. `-c planModelId=`, `-c codeModelId=` and
`-c haikuModelId=` pin others; see `docs/security.md` for residency.

## Provider credentials

Create AgentCore Identity credential providers out of band with `make providers` (or
`scripts/providers.sh`). Gateway outbound auth and the runtime's git push path share the same
provider per host. Secrets never enter CloudFormation templates.

## Claude Code plugin

The plugin is `sample-rca`. Its tools are `mcp__plugin_sample-rca_remote-coding-agent__*`.

The MCP server finds the deployment in this order: `RCA_API_URL` (the plugin's `api_url`
setting) **outranks** `~/.rca/endpoint.json` and stack discovery; `RCA_REGION` (the plugin's
`aws_region` setting) pins the region; `RCA_STACK_NAME` (the plugin's `stack_name` setting)
names the CloudFormation stack to read when nothing else is set. Leave the settings empty for a
single fleet. Every tool's
answer ends with an `endpoint used:` line naming the URL, the region and the source.

`/sample-rca:remote` dispatches and follows tasks. In a checkout of this repository the
un-namespaced `/remote` command does the same when present.

## Tasks

- Submit with `submit_task` or `POST /tasks` with `repositoryUrl` and `prompt`.
- Optional `baseBranch`, `verifyCommand`, and `setupCommand` override repository detection.
- Cancel with `POST /tasks/{taskId}/cancel` when a run is still active.
- Tasks are refused when the repository host is not configured or not on the allowlist.

## Why the Makefile targets are named that way

The agent uses only entry points a repository declares: a Makefile `setup:` or `bootstrap:`
target to prepare and a `test:` target to verify (or `package.json` `scripts.setup` and
`scripts.test`). It guesses nothing else. `setup` is idempotent because it runs before
verification on every run.

## Tearing a fleet down

AgentCore Gateway and Identity credential providers are created by API calls as well as by
stacks, so read the table below before destroying.

1. **Remove provider secrets** you created for this fleet (Secrets Manager), if any.
2. **Destroy the five stacks by name**, with the same pinned CDK CLI `make deploy` uses:

   ```bash
   cd infra && PATH="$PWD/../.venv/bin:$PATH" npx --yes aws-cdk@2.1138.0 destroy \
     -c account=<account> -c region=<region> \
     SampleCodingAgentAccountEuWest1 SampleCodingAgentGateway SampleCodingAgentApi \
     SampleCodingAgentRuntime SampleCodingAgentData
   ```

   The `PATH` prefix makes `cdk.json`'s `python3 app.py` find the CDK libraries, and the
   context values are required because `infra/app.py` reads the account and region from
   context. The account stack is suffixed with the consuming region (`eu-west-1` →
   `…AccountEuWest1`); `STACK_PREFIX=Foo` renames all five. Run `… ls` with the same
   arguments first to see the names. Naming the five stacks is the safe choice; use `destroy
   --all` **only** when this account and region hold exactly one fleet and no leftover
   unsuffixed account stack.

### What survives a destroy

| Resource | Why it is kept | Delete it with |
|---|---|---|
| The Tasks table | `RemovalPolicy.RETAIN`, point-in-time recovery on | `aws dynamodb delete-table` after listing resources |
| ECR images (~1.3 GB each) | they live in the CDK bootstrap repository | `aws ecr batch-delete-image` |
| AgentCore runtime log groups | created by the service | `aws logs delete-log-group` under `/aws/bedrock-agentcore/runtimes/` |

The DNS query log group `/aws/route53/rca-agent-dns` is deleted with the stack; export it
first if you need it.

## Budget cap

Bedrock model spend is capped at **$50/month** (override with `-c monthlyBudgetUsd=`). Email
alerts fire at 80% of actual and 100% of forecast spend. At 100% of actual spend AWS Budgets
attaches a deny policy to the runtime execution role and the dispatcher role. Lift it with
`make budget-reset` after raising the limit or at the start of a month.

## Expected cost

Idle infrastructure is roughly **$83–85/month** in us-east-1 with interface endpoints in one
AZ (see `-c endpointAzCount=`). The $50 cap applies to Bedrock usage only, not the whole
account.