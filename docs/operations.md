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

Git hosts use **AgentCore Identity API key credential providers** (a stored PAT or app
password), not a GitHub App or OAuth console wizard. The runtime reads them through
`GetWorkloadAccessToken` + `GetResourceApiKey`. Gateway PR tools use the same provider per host.
Secrets never enter CloudFormation templates.

Default provider **names** (prefix `rca` = `APP_NAME` in `infra/app.py`):

| Host | Identity provider name | Git HTTPS user |
|------|------------------------|----------------|
| `github.com` | `rca_github` | `x-access-token` |
| `bitbucket.org` | `rca_bitbucket_cloud` | `x-token-auth` |

Workload identity name: **`rca_git_wl`** (override with runtime env `IDENTITY_WORKLOAD_NAME`).

### 1. Workload identity (once per account/region)

```bash
export AWS_REGION=us-east-1   # same region as the runtime

aws bedrock-agentcore-control create-workload-identity --name rca_git_wl
```

### 2. GitHub — API key provider `rca_github`

1. In GitHub, create a **fine-grained PAT** (or classic PAT) with **Contents** and **Pull
   requests** read/write on the repositories the agent will use.
2. Register the token with Identity (replace `ghp_...` — or pipe from a file instead of echo):

```bash
aws bedrock-agentcore-control create-api-key-credential-provider \
  --name rca_github \
  --api-key-secret-source MANAGED \
  --api-key "$(printf '%s' 'ghp_YOUR_TOKEN')"
```

Alternatively, store the PAT in Secrets Manager first and pass `--api-key-secret-source EXTERNAL`
with `--api-key-secret-config` (see
[API key credential providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-providers.html)).

### 3. Bitbucket Cloud — API key provider `rca_bitbucket_cloud`

1. In Bitbucket → **Personal settings** → **App passwords**, create a password with
   **Repositories: Read/Write** (and pull requests if you open PRs).
2. Register it:

```bash
aws bedrock-agentcore-control create-api-key-credential-provider \
  --name rca_bitbucket_cloud \
  --api-key-secret-source MANAGED \
  --api-key "$(printf '%s' 'YOUR_APP_PASSWORD')"
```

### 4. Verify

```bash
WAT=$(aws bedrock-agentcore get-workload-access-token \
  --workload-name rca_git_wl --query workloadAccessToken --output text)

aws bedrock-agentcore get-resource-api-key \
  --workload-identity-token "$WAT" \
  --resource-credential-provider-name rca_github \
  --scopes repository --query apiKey --output text
```

Repeat with `rca_bitbucket_cloud` when using Bitbucket.

### 5. Gateway (for create-PR tools)

In the console, open **AgentCore → Gateway →** `rca-gateway`, edit the **github** and
**bitbucket-cloud** targets, and set **outbound auth** to the matching **API key** credential
provider ARN (`rca_github` / `rca_bitbucket_cloud`). Git clone/push inside the runtime does not
need this step; opening PRs through Gateway does.

**Network:** cloning still needs reachable `github.com` / `bitbucket.org` from the VPC (NAT). The
account NACL guardrail may block that until platform grants an exception.

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

Idle infrastructure is roughly **$98–100/month** in us-east-1 with interface endpoints in one
AZ (see `-c endpointAzCount=`). That includes S3 and DynamoDB *interface* endpoints (~$7.30
each) alongside their free gateway endpoints, because accounts whose NACL guardrail strips
public inbound rules drop every reply a gateway endpoint carries. The VPC always spans two AZs because AgentCore expects the
runtime in at least two subnets; subnets are free, and the single NAT gateway and the
endpoints stay in the first AZ, so the second AZ adds only inter-AZ data transfer
($0.01/GB each way) for sessions placed there. `-c endpointAzCount=2` puts every
interface endpoint in both AZs, about $58/month more. The $50 cap applies to Bedrock usage
only, not the whole account.