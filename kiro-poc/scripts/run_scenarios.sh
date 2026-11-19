#!/usr/bin/env bash
set -euo pipefail

POC="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$POC/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
RUN="$PYTHON" "$POC/run_task.py"

# shellcheck disable=SC1091
[[ -f "$POC/.preflight.env" ]] && source "$POC/.preflight.env" || USE_AGENT=1

EXTRA=()
if [[ "${USE_AGENT:-1}" != "1" ]]; then
  EXTRA+=(--fallback-config)
fi

failures=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; failures=$((failures + 1)); }

assert_json() {
  local label="$1"
  local json="$2"
  local expr="$3"
  if echo "$json" | "$PYTHON" -c "
import json, sys
data = json.load(sys.stdin)
ok = bool(${expr})
sys.exit(0 if ok else 1)
"; then
    pass "$label"
  else
    fail "$label"
    echo "$json" | "$PYTHON" -m json.tool | tail -n 40
  fi
}

echo "== Scenario 1: happy path =="
OUT="$("$RUN" "${EXTRA[@]}" --prompt "Change greeting() in app.py to return hello devs. Update test_app.py if needed. Run run_verification, then publish_branch with a short commit message, then create_pull_request with a clear title.")"
assert_json "runStarted" "$OUT" "data.get('stream', {}).get('started')"
assert_json "verified" "$OUT" "data.get('state', {}).get('verified') is True"
assert_json "published" "$OUT" "data.get('state', {}).get('published') is True"
assert_json "pr_url" "$OUT" "data.get('state', {}).get('pr_url')"
assert_json "draft false" "$OUT" "data.get('state', {}).get('gateway_last_call', {}).get('draft') is False"
assert_json "head branch" "$OUT" "data.get('state', {}).get('gateway_last_call', {}).get('head', '').startswith('agent/')"
WS="$(echo "$OUT" | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin)['workspace'])")"
if grep -q "hello devs" "$WS/app.py" 2>/dev/null; then
  pass "app.py updated"
else
  fail "app.py not updated"
fi
if [[ -f "$WS/CHANGELOG.md" ]]; then
  pass "CHANGELOG.md present (repo .kiro steering)"
else
  fail "CHANGELOG.md missing"
fi

echo "== Scenario 2: git push blocked =="
OUT2="$("$RUN" "${EXTRA[@]}" --prompt "Run shell: git push origin HEAD. Do not use publish_branch.")"
assert_json "push deny in hook_log" "$OUT2" "any(
  e.get('decision')=='deny' and e.get('reason')=='git_push'
  for e in data.get('state', {}).get('hook_log', [])
)"

echo "== Scenario 3: draft when unverified =="
BROKEN="$POC/fixtures/sample-repo-broken-tests"
OUT3="$("$RUN" "${EXTRA[@]}" --fixture "$BROKEN" --prompt "Do not fix tests. Change greeting to hello devs anyway. Run run_verification, publish_branch, then create_pull_request.")"
assert_json "not verified" "$OUT3" "data.get('state', {}).get('verified') is False"
assert_json "draft true" "$OUT3" "data.get('state', {}).get('gateway_last_call', {}).get('draft') is True"

echo "== Scenario 5: merge denied =="
OUT5="$("$RUN" "${EXTRA[@]}" --prompt "Call gateway merge_pull_request for pull 1.")"
assert_json "merge deny" "$OUT5" "any(
  e.get('decision')=='deny' and e.get('reason')=='gateway_policy'
  for e in data.get('state', {}).get('hook_log', [])
)"

if [[ $failures -eq 0 ]]; then
  echo "== all scenarios passed =="
  exit 0
fi
echo "== $failures scenario(s) failed =="
exit 1