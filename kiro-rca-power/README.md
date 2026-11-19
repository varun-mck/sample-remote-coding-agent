# rca-fleet Kiro power

A [Kiro power](https://kiro.dev/docs/powers/create/) that lets Kiro dispatch and track tasks on
the remote coding agent fleet. It bundles:

- a stdio MCP server with four tools: `submit_task`, `get_task`, `list_tasks` and `cancel_task`;
- a `dispatch-task` skill that tells the agent how to use them.

The server calls the task API's Lambda Function URL (`AWS_IAM` auth) and signs each request
with SigV4 in-process, using your normal AWS credential chain. It never runs the `aws` CLI,
`make` or `scripts/rca_cli.py`.

## Requirements

- [uv](https://docs.astral.sh/uv/) (`uvx` on `PATH`, or in `/opt/homebrew/bin`, `/usr/local/bin`,
  `~/.local/bin` or `~/.cargo/bin`). It builds and runs the server; Python is fetched if needed.
- A deployed fleet (`make deploy` in this repository).
- AWS credentials allowed `lambda:InvokeFunctionUrl` on the API's router function.

## Install

Kiro CLI (v3):

```bash
kiro-cli powers install /absolute/path/to/kiro-rca-power
```

Kiro IDE: **Powers** panel, **Add Custom Power**, **Import power from a folder**, then pick
this directory.

The first tool call takes a few seconds while `uvx` builds the server; later starts reuse the
cached build.

## Configuration

The power reads files rather than environment variables. Kiro does not expand `${VAR}` from
your shell inside a power's `mcp.json`, and an IDE launched from the Dock does not inherit your
shell environment at all.

Settings are read in this order; the first value found wins.

1. `~/.rca/power.json` (optional, written by you):

   ```json
   { "apiUrl": "https://<id>.lambda-url.us-east-1.on.aws/", "region": "us-east-1", "profile": "my-sso-profile" }
   ```

   Every key is optional. Use `profile` when the credentials you need are not your default
   profile. Use `apiUrl` to point at a different deployment than the last `make deploy`.
2. `~/.rca/endpoint.json`, which `make deploy` writes. Nothing to do if you deployed from this
   machine.
3. `RCA_API_URL`, `RCA_REGION` and `RCA_AWS_PROFILE`, if Kiro passes them through to the server.

The signing region is `region` from `power.json` or `RCA_REGION` if set. Otherwise it is read
from the URL (`<id>.lambda-url.<region>.on.aws`), then `endpoint.json`, then your profile's
default. `AWS_REGION` is never used as a pin, because it often names a model region rather than
the deployment's.

Credentials come from boto3's default chain for the chosen profile: SSO, `aws login`, static
keys, environment, or an instance role. Refresh them the way you normally do; the server picks
up new credentials on the next call.

## Using it

Mention the remote coding agent in a Kiro conversation, for example:

> Dispatch a remote coding agent task to https://github.com/varun-mck/rca-test-repo on branch
> feat/update-greeting: update the greeting to Hello Devs and create a PR. Monitor it.

Kiro activates the power, calls `submit_task`, then polls `get_task`.

Name a model or an effort level ("use claude-sonnet-4.6 at high effort") and `submit_task`
passes `model` and `effort` through to the remote `kiro-cli`. Leave them out to use Kiro's
defaults.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `uvx not found` in the MCP logs | uv is not installed, or not in one of the locations above. |
| `no API URL configured` | No `power.json` or `endpoint.json`. Run `make deploy` or write `~/.rca/power.json`. |
| `403` from the API | The credentials are not allowed `lambda:InvokeFunctionUrl`, the wrong profile is in use, or the URL belongs to an old deployment. |
| `404 NotFound` for a task you just submitted | The API filters tasks by the caller's IAM `team` tag; submit and read with the same identity. |
| Credential or SSO token errors | Refresh credentials (`aws sso login --profile ...` or `aws login`). |

Server logs go to stderr (Kiro's MCP log output) and to `rca-power.log` in the power's data
directory. Request bodies are never logged.
