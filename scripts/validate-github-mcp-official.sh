#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

EXPORT_BIN="/home/aaron/.openclaw/scripts/export-github-mcp-env.sh"

if [[ ! -x "$EXPORT_BIN" ]]; then
  echo "[validate-github-mcp-official] missing executable export script: $EXPORT_BIN" >&2
  exit 1
fi

# shellcheck disable=SC1090
source <("$EXPORT_BIN")

quick=0
if [[ "${1:-}" == "--quick" ]]; then
  quick=1
fi

check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $label"
  else
    echo "FAIL  $label"
    return 1
  fi
}

failures=0

check "docker available" command -v docker || failures=$((failures + 1))
check "github mcp image available" docker image inspect ghcr.io/github/github-mcp-server:latest || failures=$((failures + 1))
check "github mcp list-scopes" docker run --rm ghcr.io/github/github-mcp-server:latest list-scopes --toolsets=default || failures=$((failures + 1))

if [[ $quick -eq 0 ]]; then
  validate_login() {
    local label="$1"
    local token="$2"
    local expected_login="$3"

    local login
    login="$(curl -fsS -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" https://api.github.com/user | jq -r '.login // empty' || true)"

    if [[ "$login" == "$expected_login" ]]; then
      echo "PASS  $label -> $login"
    else
      echo "FAIL  $label expected=$expected_login got=${login:-<empty>}"
      failures=$((failures + 1))
    fi
  }

  validate_login "github PAT main" "$GITHUB_PAT_MAIN" "aaronclawrsl-bot"
  validate_login "github PAT dwight" "$GITHUB_PAT_DWIGHT" "aaronclawrsl-bot"
  validate_login "github PAT druck" "$GITHUB_PAT_DRUCK" "aaronclawrsl-bot"
  validate_login "github PAT resi" "$GITHUB_PAT_RESI" "EWAG-dev"
fi

if [[ $failures -gt 0 ]]; then
  echo "github-mcp validation failed: $failures check(s)"
  exit 1
fi

echo "github-mcp validation passed"
