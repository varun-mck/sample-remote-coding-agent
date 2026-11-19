# Kiro interactive session in a container image

Use this when **org policy blocks `KIRO_API_KEY`** but headless `kiro-cli` must run inside Linux (local Docker or a runtime image). You **copy the host interactive login** into the container; you do not bake secrets into the image.

## What to mount

| Host | In-container (Linux CLI layout) | Purpose |
|------|----------------------------------|---------|
| macOS: `~/Library/Application Support/kiro-cli/` | `/root/.local/share/kiro-cli/` | Session DB `data.sqlite3` (`auth_kv` table) |
| Linux: `~/.local/share/kiro-cli/` | same relative path under the container user’s home | Same session DB |
| `~/.kiro/` | `~/.kiro/` (or set `KIRO_HOME`) | Settings, sessions, global agent config |

The “token” is **not** a single env var; it lives in **`data.sqlite3`**. Refresh may lag until you run `kiro-cli` once on the host after login.

## Host prep

```bash
kiro-cli login
kiro-cli whoami
```

On macOS, confirm the DB exists:

```bash
ls -la "$HOME/Library/Application Support/kiro-cli/data.sqlite3"
```

If missing but `whoami` works on the host, run `kiro-cli logout` then `kiro-cli login` again.

## Run (bind mounts, not baked into image)

**macOS → Linux container (example):**

```bash
docker run --rm -it \
  -v "$HOME/Library/Application Support/kiro-cli:/root/.local/share/kiro-cli:ro" \
  -v "$HOME/.kiro:/root/.kiro:ro" \
  <image-with-kiro-cli> \
  kiro-cli whoami
```

**Linux host:**

```bash
docker run --rm -it \
  -v "$HOME/.local/share/kiro-cli:/root/.local/share/kiro-cli:ro" \
  -v "$HOME/.kiro:/root/.kiro:ro" \
  <image-with-kiro-cli> \
  kiro-cli whoami
```

Headless chat smoke test:

```bash
kiro-cli chat --v3 --no-interactive --output-format stream-json "Reply with exactly: pong"
```

Match **CPU arch** with the image (e.g. ARM64 AgentCore → ARM64 `kiro-cli` binary).

## Image requirements

- Install **Kiro CLI** (3.4+ recommended) on the image; do **not** `COPY` `data.sqlite3` into image layers.
- Mount credentials at **runtime** only.
- Container user home must match mount targets (adjust `/root/...` if you run as non-root).

## Security

- Treat mounted auth like a **long-lived API key** — any process in the container can use it.
- Do **not** push images or volumes containing `data.sqlite3` to a shared registry or multi-tenant fleet without explicit approval.
- Prefer **read-only** mounts (`:ro`) when the task does not need to write Kiro config.

## What this does not solve

- **Kiro Cloud** (`--cloud`) headless still expects **`KIRO_API_KEY`** per docs; this pattern is for **local/self-hosted** headless runs.
- **Production AgentCore**: fleet-wide session injection, token rotation, and org SSO on a personal laptop session are separate governance problems.
- Session can **expire**; remount after re-login on the host.

## Related

- Local harness POC: [`../README.md`](../README.md), [`../run_task.py`](../run_task.py).