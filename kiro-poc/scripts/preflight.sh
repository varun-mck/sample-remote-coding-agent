#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POC="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"

echo "== Kiro POC preflight =="

if ! command -v kiro-cli >/dev/null; then
  echo "FAIL: kiro-cli not on PATH"
  exit 1
fi

VER="$(kiro-cli --version 2>/dev/null | awk '{print $NF}')"
echo "kiro-cli version: ${VER}"

need_min="3.4.0"
if ! python3 - <<'PY' "$VER" "$need_min"
import sys

def parts(v: str) -> tuple[int, ...]:
    out: list[int] = []
    for bit in v.split("."):
        digits = "".join(c for c in bit if c.isdigit())
        out.append(int(digits or 0))
    return tuple(out)

have, need = parts(sys.argv[1]), parts(sys.argv[2])
sys.exit(0 if have >= need else 1)
PY
then
  echo "WARN: version ${VER} is below ${need_min}; PreToolUse updatedInput needs 3.4.0+"
  echo "      Run: brew upgrade --cask kiro-cli  OR  kiro-cli update"
fi

WHOAMI="$(kiro-cli whoami 2>&1 || true)"
echo "whoami: ${WHOAMI}"
if echo "$WHOAMI" | grep -qi "not logged in"; then
  echo "FAIL: run kiro-cli login first"
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "FAIL: repo .venv not found at $PYTHON (run make setup)"
  exit 1
fi

"$PYTHON" -c "import mcp" || { echo "FAIL: mcp package missing in .venv"; exit 1; }
echo "OK: .venv has mcp"

echo "Probing headless stream-json (short prompt)..."
set +e
PROBE="$(
  cd "$POC/fixtures/sample-repo" && \
  kiro-cli chat --v3 --no-interactive --output-format stream-json "Reply with exactly: pong" 2>/dev/null | head -n 3
)"
set -e
if echo "$PROBE" | grep -q runStarted; then
  echo "OK: headless stream-json works with current login"
else
  echo "FAIL: headless probe did not emit runStarted"
  echo "$PROBE"
  exit 1
fi

set +e
kiro-cli chat --v3 --help 2>&1 | grep -q -- '--agent'
AGENT_FLAG=$?
set -e
if [[ $AGENT_FLAG -eq 0 ]]; then
  echo "OK: --agent flag is documented on kiro-cli chat"
  echo "USE_AGENT=1" > "$POC/.preflight.env"
else
  echo "NOTE: --agent may be V2-only; scenarios will use --fallback-config"
  echo "USE_AGENT=0" > "$POC/.preflight.env"
fi

echo "== preflight passed =="