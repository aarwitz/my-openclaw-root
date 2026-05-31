#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# credential-preflight.sh
# Fast-fail health checks for auth-backed dependencies Jerry relies on.
# Catches: auth drift, gateway misconfiguration, node unreachability, SSH key issues.

NODE_NAME="ios-build-node"
MAC_USER="${EWAG_MAC_USER:-taylorolsen-vogt}"
SSH_HOST="${EWAG_MAC_SSH_HOST:-${MAC_USER}@100.125.133.123}"
IOS_AGENT_BIN="${EWAG_IOS_AGENT_BIN:-/Users/${MAC_USER}/ios-agent/ios-agent}"
PREFLIGHT_CACHE_DIR="${HOME}/.openclaw/tmp/preflight-cache"
GOOGLE_CHECK_TTL_SEC="${GOOGLE_CHECK_TTL_SEC:-14400}"
GH_ROUTER_BIN="/home/aaron/.openclaw/scripts/gh-account-router.sh"
GOG_ROUTER_BIN="/home/aaron/.openclaw/scripts/gog-account-router.sh"
CF_ROUTER_BIN="/home/aaron/.openclaw/scripts/cloudflare-account-router.sh"
GH_MCP_VALIDATE_BIN="/home/aaron/.openclaw/scripts/validate-github-mcp-official.sh"
GOOGLE_MCP_VALIDATE_BIN="/home/aaron/.openclaw/scripts/validate-google-workspace-mcp-readonly.sh"
OPENCLAW_BIN="${OPENCLAW_BIN:-}"

failures=0
GOOGLE_AUTH_OK=0

mkdir -p "$PREFLIGHT_CACHE_DIR"

if [[ -z "$OPENCLAW_BIN" ]]; then
  OPENCLAW_BIN="$(/home/aaron/.openclaw/scripts/resolve-openclaw-bin.sh)"
fi

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

check_output() {
  local label="$1"
  local expected="$2"
  shift 2
  local output
  output=$("$@" 2>/dev/null || true)
  if echo "$output" | grep -qE "$expected"; then
    echo "PASS  $label"
  else
    echo "FAIL  $label (expected: $expected, got: ${output:0:80})"
    failures=$((failures + 1))
  fi
}

check_cached() {
  local label="$1"
  local ttl_sec="$2"
  shift 2

  local key
  key="$(echo "$label" | sed 's/[^A-Za-z0-9._-]/_/g')"
  local cache_file="${PREFLIGHT_CACHE_DIR}/${key}.status"
  local now ts status age
  now="$(date +%s)"

  if [[ -f "$cache_file" ]]; then
    read -r ts status < "$cache_file" || true
    if [[ -n "${ts:-}" && -n "${status:-}" && "$status" == "0" ]]; then
      age=$((now - ts))
      if [[ "$age" -lt "$ttl_sec" ]]; then
        echo "PASS  $label (cached ${age}s)"
        return 0
      fi
    fi
  fi

  if "$@" >/dev/null 2>&1; then
    echo "PASS  $label"
    echo "$now 0" > "$cache_file"
  else
    echo "FAIL  $label"
    echo "$now 1" > "$cache_file"
    failures=$((failures + 1))
  fi
}

check_google_service() {
  local label="$1"
  shift
  if [[ "$GOOGLE_AUTH_OK" -eq 1 ]]; then
    check_cached "$label" "$GOOGLE_CHECK_TTL_SEC" "$@"
  else
    check "$label" "$@"
  fi
}

# --- OpenClaw core ---
check "gateway health" "$OPENCLAW_BIN" health
check "model auth" "$OPENCLAW_BIN" models status --check

# --- Gateway bind verification (prevents loopback/tailnet drift) ---
check_output "gateway bind (listening on 0.0.0.0)" "(0\.0\.0\.0|\[::\]|:::?)?\s*:18789" ss -ltnp

# --- Node connectivity ---
echo "      checking ios-build-node..."
check "node reachable" ssh -o BatchMode=yes -o ConnectTimeout=10 "${MAC_USER}@100.125.133.123" \
  "$IOS_AGENT_BIN branch"

# --- SSH to Mac (BatchMode — no password prompts) ---
check "mac ssh (BatchMode)" ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_HOST" 'echo OK'

# --- GitHub ---
check "github auth" gh auth status
check "github account router present" test -x "$GH_ROUTER_BIN"
check "github mcp validator present" test -x "$GH_MCP_VALIDATE_BIN"
check_output "github account (jerry/main)" "^aaronclawrsl-bot$" \
  "$GH_ROUTER_BIN" --agent main api user --jq '.login'
check_output "github account (dwight)" "^aaronclawrsl-bot$" \
  "$GH_ROUTER_BIN" --agent dwight api user --jq '.login'
check_output "github account (druck)" "^aaronclawrsl-bot$" \
  "$GH_ROUTER_BIN" --agent druck api user --jq '.login'
check_output "github account (resi)" "^EWAG-dev$" \
  "$GH_ROUTER_BIN" --agent resi api user --jq '.login'
check "github mcp quick validation" "$GH_MCP_VALIDATE_BIN" --quick

# --- Google router presence ---
check "google account router present" test -x "$GOG_ROUTER_BIN"
check "cloudflare account router present" test -x "$CF_ROUTER_BIN"

# --- Google ---
if gog auth list --check --no-input >/dev/null 2>&1; then
  GOOGLE_AUTH_OK=1
  echo "PASS  google auth"
else
  echo "FAIL  google auth"
  failures=$((failures + 1))
fi

check_output "google account (jerry/main)" "^aaronclawrsl@gmail\\.com$" \
  "$GOG_ROUTER_BIN" --agent main --print-account
check "google workspace mcp validator present" test -x "$GOOGLE_MCP_VALIDATE_BIN"
check "google workspace mcp readonly validation" "$GOOGLE_MCP_VALIDATE_BIN"
check "cloudflare default token verify" "$CF_ROUTER_BIN" --mode default --verify
check_output "cloudflare worker token path" "^/home/aaron/\\.openclaw/credentials/cloudflare/account-token\\.bak$" \
  "$CF_ROUTER_BIN" --mode worker-mutate --print-token-path
check_google_service "drive probe" gog drive search 'owner:me' --max 1 --no-input
check_google_service "gmail probe" gog gmail search 'newer_than:7d' --max 1 --no-input
check_google_service "calendar probe" gog calendar calendars --max 1 --no-input

if [[ $failures -gt 0 ]]; then
  echo ""
  echo "credential-preflight: $failures check(s) failed"
  echo "Recover what can be recovered automatically first, then escalate only the remaining auth drift."
  exit 1
fi

echo ""
echo "credential-preflight: all checks passed"
