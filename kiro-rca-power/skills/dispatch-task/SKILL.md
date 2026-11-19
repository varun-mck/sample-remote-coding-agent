---
name: dispatch-task
description: Dispatch a task to the remote coding agent fleet and follow it to a result. Use when the user asks to run, submit, dispatch, monitor or cancel a remote coding agent (RCA) task.
---

# Dispatch a remote coding agent task

The `rca-fleet` MCP tools talk to the fleet's task API. They sign every request with the
operator's own AWS credentials, so there is nothing to log in to from here.

## Step 1: Confirm the inputs

- `repositoryUrl`: the full clone URL, such as `https://github.com/owner/repo`. A bare
  `owner/repo` is refused by the API.
- `branch`: a branch that **already exists on the remote**. The agent checks it out; it
  does not create it.
- `prompt`: what the agent should do. Include "create a PR" or "push" only if the user
  asked for it.

Ask the user for anything missing rather than guessing a repository or branch.

## Step 2: Submit

Call `submit_task`. Keep the returned `taskId` and repeat it to the user.

## Step 3: Follow it

Call `get_task` with the `taskId`. If the user asked you to monitor, poll about every 30
seconds until the status is `SUCCEEDED`, `FAILED` or `CANCELLED`. A typical run takes one to
five minutes.

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
  credentials live in AgentCore Identity; AWS credentials come from the operator's profile.
- Use `cancel_task` only when the user asks to stop a task.
- If the tools fail with a configuration or credential error, relay the message: it names
  the fix (`make deploy`, `~/.rca/power.json`, or refreshing AWS credentials). `make task`
  in the repository is a fallback only when the MCP server itself will not start.
