---
name: dispatch-task
description: Dispatch a task to the remote coding agent fleet and follow it to a result. Use when the user asks to run, submit, dispatch, monitor or cancel a remote coding agent (RCA) task.
---

# Dispatch a remote coding agent task

The `rca-fleet` MCP server is the fleet's **task gateway**. Kiro signs in with Cognito when
the connection starts; you do not pass AWS credentials or a `submittedBy` field.

## Step 1: Confirm the inputs

- `repositoryUrl`: the full clone URL, such as `https://github.com/owner/repo`. A bare
  `owner/repo` is refused by the API.
- `branch`: a branch that **already exists on the remote**. The agent checks it out; it
  does not create it.
- `prompt`: what the agent should do. Include "create a PR" or "push" only if the user
  asked for it.
- `model` and `effort` are optional. Pass them only when the user names a model or an
  effort level; otherwise the fleet runs **claude-haiku-5.5**.

Ask the user for anything missing rather than guessing a repository or branch.

## Step 2: Submit

Call `rca-tasks___submit_task`. Keep the returned `taskId` and repeat it to the user.

## Step 3: Follow it

Call `rca-tasks___get_task` with the `taskId`. If the user asked you to monitor, poll about
every 30 seconds until the status is `SUCCEEDED`, `FAILED` or `CANCELLED`. A typical run
takes one to five minutes.

`QUEUED`, `DISPATCHED` and `RUNNING` are in flight. `steps` lists the harness steps reached
so far, ending in `record-result` once the outcome is stored.

## Step 4: Report

- Quote the `result` text: it is the agent's own summary and the only place a PR link,
  compare link or push outcome appears.
- `SUCCEEDED` means the agent's run finished, not that a PR was opened. Read `result`
  before saying a PR exists.
- On `FAILED`, report `error` as well as `result`.

## Rules

- Never ask for, accept or forward a GitHub token or any other credential. Repository
  credentials live in AgentCore Identity.
- Use `rca-tasks___cancel_task` only when the user asks to stop a task.
- If the tools fail with a sign-in error, ask the user to reconnect the `rca-fleet` server in
  Kiro (Cognito hosted UI). `make task` in the repository is a fallback only for engineers
  with an AWS CLI profile.