# Security and data boundary

**Deploy this into a dedicated AWS account.** The agent runs code it did not write: a
repository's own `setupCommand` and verification command. The account boundary is the outermost
fence.

## Only register repositories you trust

This system clones a repository, points a model with unrestricted `Bash` at it, and runs the
repository's own setup and test commands. Everything below bounds blast radius; nothing removes
it.

## Git access: AgentCore Identity and Gateway

No long-lived token is stored in this repository. Each Git host you configure gets one AgentCore
Identity credential provider (`make providers`). The runtime fetches short-lived credentials at
push time through `credentials.fetch_token`; Gateway uses the same provider for create-PR API
calls. Configure provider secrets out of band only.

Install OAuth apps or tokens with the narrowest scope that can open a pull request on the
repositories you allow (`REPOSITORY_ALLOWLIST` when set).

Turn on branch protection on the default branch. The agent pushes only `agent/<taskId>` and opens
a pull request; protection limits what a compromised run can merge without a person.

## The data boundary

Inference runs on Bedrock in your account. Bedrock does not retain prompts or completions for
model training. Nothing in this sample exports vendor telemetry (`CLAUDE_CODE_ENABLE_TELEMETRY=0`).

### How it is enforced

- **`WebFetch` and `WebSearch` are not in the toolset** (`options.py`).
- **`setting_sources=[]` and `strict_mcp_config=True`:** only harness MCP and configured Gateway
  stdio servers are available.
- **`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`:** Claude Code's container-scoped auto-memory stays off.
  There is no AgentCore Memory in this fleet runtime; task history lives in DynamoDB only.
- **Git credentials never enter the environment** (invariant 3).
- **Publishing is harness-owned:** `publish_branch` and Gateway create-PR tools, with hooks that
  force draft when `run_verification` did not pass.

`tests/test_env_contract.py` and `tests/test_options.py` hold the runtime surface.

## Prompt guardrail

A Bedrock guardrail screens every task prompt in `runs.screen_prompt` before queueing. A blocked
prompt returns HTTP **422** with `"PromptBlockedByGuardrail"`. An `ApplyGuardrail` outage returns
**503** with `"GuardrailUnavailable"`.

The stack enables input-only policies at `LOW` for `PROMPT_ATTACK`, `MISCONDUCT`, and
`AWS_ACCESS_KEY`, plus secret-shaped regexes (`infra/stacks/prompt_guardrail.py`). Refusals name
policy types, never matched text. `-c guardrailEnabled=false` turns screening off.

## What remains exposed

- **Execution-role credentials inside the container.** Repository commands use a minimal
  environment, but code can still reach the instance metadata path. The boundary and VPC egress
  limit what those credentials can do.
- **Prompt injection through repository content.** Malicious setup or test scripts still run by
  design. Use a dedicated account, allowlisted repositories, and branch protection.
- **Gateway tool surface.** Hooks deny merge, approve, delete, and direct `git push`; only
  allowlisted provider tools are rewritten for draft and branch pinning. Misconfigured Gateway
  targets widen API access.

## Static analysis suppressions

Dependency suppressions for `pip-audit` and similar tools live beside the lockfiles they guard.
They document accepted risk, not silent ignores.