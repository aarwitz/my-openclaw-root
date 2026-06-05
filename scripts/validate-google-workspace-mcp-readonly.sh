#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

failures=0

check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $label"
  else
    echo "FAIL  $label"
    failures=$((failures + 1))
  fi
}

check "node available" command -v node
check "npx available" command -v npx
check "google mcp credentials bridge" /home/aaron/.openclaw/scripts/ensure-google-workspace-mcp-credentials.sh

status_output="$(npx -y google-workspace-mcp status 2>&1 || true)"
if echo "$status_output" | grep -qE "No issues found|0 issue\(s\) found|Server is ready to use"; then
  echo "PASS  google workspace mcp status"
else
  echo "FAIL  google workspace mcp status"
  echo "$status_output" | sed -n '1,20p'
  failures=$((failures + 1))
fi

if [[ $failures -gt 0 ]]; then
  echo "google-workspace-mcp validation failed: $failures check(s)"
  exit 1
fi

echo "google-workspace-mcp validation passed"
