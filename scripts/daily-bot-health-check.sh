#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -u

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
CFG="/home/aaron/.openclaw/openclaw.json"
SCHWAB_TOKEN="/home/aaron/.openclaw/credentials/schwab-dev-token.json"
GITHUB_PREFLIGHT="/home/aaron/.openclaw/scripts/github-ssh-preflight.sh"
REPORT_FILE="/tmp/openclaw-daily-health-report.txt"
OPENCLAW_BIN="${OPENCLAW_BIN:-}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

PASS_LINES=()
WARN_LINES=()
FAIL_LINES=()
MANUAL_LINES=()

add_pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  PASS_LINES+=("$1")
}

add_warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  WARN_LINES+=("$1")
}

add_fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAIL_LINES+=("$1")
}

add_manual() {
  MANUAL_LINES+=("$1")
}

if [[ -z "$OPENCLAW_BIN" ]]; then
  OPENCLAW_BIN="$(/home/aaron/.openclaw/scripts/resolve-openclaw-bin.sh)"
fi

classify_error() {
  local s
  s="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
  if echo "$s" | rg -q "rate limit|quota|429"; then
    echo "rate_limit"
  elif echo "$s" | rg -q "auth|401|403|unauthorized|forbidden|token|credential|permission denied|publickey"; then
    echo "auth"
  elif echo "$s" | rg -q "timeout|timed out|network|dns|econn|connection|unreachable|reset"; then
    echo "network"
  else
    echo "other"
  fi
}

safe_jq() {
  jq -r "$1" "$2" 2>/dev/null || true
}

probe_json_ok() {
  local body="$1"
  echo "$body" | jq -e "$2" >/dev/null 2>&1
}

fail_with_reason() {
  local label="$1"
  local detail="$2"
  local manual="$3"
  local reason
  reason="$(classify_error "$detail")"
  add_fail "${label}: failed (${reason})"
  add_manual "${manual}"
}

# 1) Gateway and Telegram channel health
GW_STATUS="$("$OPENCLAW_BIN" gateway status 2>&1 || true)"
if echo "$GW_STATUS" | rg -q "RPC probe: ok"; then
  add_pass "gateway: rpc probe ok"
else
  add_fail "gateway: rpc probe failed"
  add_manual "Gateway RPC probe is not healthy. Run: openclaw gateway status and ~/.openclaw/scripts/safe-restart.sh if needed."
fi

CHANNEL_STATUS="$("$OPENCLAW_BIN" channels status --probe 2>&1 || true)"
for acct in default druck dwight resi; do
  if echo "$CHANNEL_STATUS" | rg -q -- "- Telegram ${acct}: .* works"; then
    add_pass "telegram/${acct}: works"
  else
    add_fail "telegram/${acct}: unhealthy"
    add_manual "Telegram account '${acct}' is unhealthy. Run: openclaw channels status --probe and fix token/group reachability."
  fi
done

# 2) Per-bot model handshake
for agent in main resi druck dwight; do
  AGENT_OUT="$("$OPENCLAW_BIN" agent --agent "$agent" --message "Reply exactly: HEALTH_OK" --json 2>&1 || true)"
  AGENT_TEXT="$(echo "$AGENT_OUT" | jq -r '.result.payloads[0].text // empty' 2>/dev/null || true)"
  if echo "$AGENT_TEXT" | rg -q "HEALTH_OK"; then
    add_pass "agent/${agent}: model handshake ok"
  else
    REASON="$(classify_error "$AGENT_OUT")"
    add_fail "agent/${agent}: model handshake failed (${REASON})"
    add_manual "Agent '${agent}' failed model handshake (${REASON}). Check /model status and auth profiles for that agent."
  fi
done

# 3) Skill readiness for configured skills
DEFAULT_SKILLS=( $(jq -r '.agents.defaults.skills[]' "$CFG" 2>/dev/null) )
if [[ ${#DEFAULT_SKILLS[@]} -eq 0 ]]; then
  add_fail "skills/defaults: empty"
  add_manual "agents.defaults.skills is empty."
fi

ALL_SKILLS="$(jq -r '[.agents.defaults.skills[]] + [.agents.list[] | (.skills // [])[]] | unique[]' "$CFG" 2>/dev/null || true)"
if [[ -n "$ALL_SKILLS" ]]; then
  while IFS= read -r skill; do
    [[ -z "$skill" ]] && continue
    SK_OUT="$("$OPENCLAW_BIN" skills info "$skill" 2>&1 || true)"
    if echo "$SK_OUT" | rg -q "✓ Ready"; then
      add_pass "skill/${skill}: ready"
    else
      add_warn "skill/${skill}: not ready"
    fi
  done <<< "$ALL_SKILLS"
fi

# 4) Druck source checks (APIs + auth)
FINNHUB_KEY="$(safe_jq '."api key" // empty' /home/aaron/.openclaw/credentials/finnhub-api.json)"
if [[ -n "$FINNHUB_KEY" ]]; then
  BODY="$(curl -sS --max-time 12 "https://finnhub.io/api/v1/quote?symbol=AAPL&token=${FINNHUB_KEY}" 2>&1 || true)"
  if probe_json_ok "$BODY" '.c != null'; then
    add_pass "druck/finnhub: quote probe ok"
  else
    add_fail "druck/finnhub: probe failed"
    add_manual "Finnhub probe failed. Verify key in credentials/finnhub-api.json and provider binding."
  fi
else
  add_fail "druck/finnhub: api key missing"
  add_manual "Finnhub api key missing in credentials/finnhub-api.json"
fi

MASSIVE_KEY="$(safe_jq '."api key" // empty' /home/aaron/.openclaw/credentials/massive-api.json)"
if [[ -n "$MASSIVE_KEY" ]]; then
  BODY="$(curl -sS --max-time 12 "https://api.massive.com/v2/aggs/ticker/AAPL/prev?adjusted=true&apiKey=${MASSIVE_KEY}" 2>&1 || true)"
  if probe_json_ok "$BODY" '.status != null'; then
    add_pass "druck/massive: prev close probe ok"
  else
    add_fail "druck/massive: probe failed"
    add_manual "Massive probe failed. Verify key in credentials/massive-api.json."
  fi
else
  add_fail "druck/massive: api key missing"
  add_manual "Massive api key missing in credentials/massive-api.json"
fi

NEWSAPI_KEY="$(safe_jq '."API key" // ."api key" // empty' /home/aaron/.openclaw/credentials/news-api-ai.json)"
if [[ -n "$NEWSAPI_KEY" ]]; then
  BODY="$(curl -sS --max-time 12 "https://newsapi.org/v2/top-headlines?country=us&pageSize=1&apiKey=${NEWSAPI_KEY}" 2>&1 || true)"
  if probe_json_ok "$BODY" '.status == "ok"'; then
    add_pass "druck/newsapi: headlines probe ok"
  else
    fail_with_reason "druck/newsapi" "$BODY" "NewsAPI probe failed. Key is likely invalid/expired. Regenerate key and update credentials/news-api-ai.json."
  fi
else
  add_fail "druck/newsapi: api key missing"
  add_manual "NewsAPI key missing in credentials/news-api-ai.json"
fi

FMP_KEY="$(safe_jq '."api key" // empty' /home/aaron/.openclaw/credentials/financial-modeling-prep-api.json)"
if [[ -n "$FMP_KEY" ]]; then
  BODY="$(curl -sS --max-time 12 "https://financialmodelingprep.com/api/v3/profile/AAPL?apikey=${FMP_KEY}" 2>&1 || true)"
  if probe_json_ok "$BODY" 'type=="array" and length>=1'; then
    add_pass "druck/fmp: profile probe ok"
  elif echo "$BODY" | rg -qi "legacy endpoint|no longer supported"; then
    add_fail "druck/fmp: failed (legacy_endpoint)"
    add_manual "FMP key is on legacy endpoint path. Migrate probe/skill usage to current FMP endpoints and/or refresh plan/key per latest FMP docs."
  else
    fail_with_reason "druck/fmp" "$BODY" "FMP probe failed. Verify key in credentials/financial-modeling-prep-api.json"
  fi
else
  add_fail "druck/fmp: api key missing"
  add_manual "FMP api key missing in credentials/financial-modeling-prep-api.json"
fi

ALPACA_KEY="$(safe_jq '."api key" // empty' /home/aaron/.openclaw/credentials/alpaca-api.json)"
ALPACA_SECRET="$(safe_jq '."secret" // empty' /home/aaron/.openclaw/credentials/alpaca-api.json)"
ALPACA_EP="$(safe_jq '."endpoint" // "https://paper-api.alpaca.markets"' /home/aaron/.openclaw/credentials/alpaca-api.json)"
if [[ -n "$ALPACA_KEY" && -n "$ALPACA_SECRET" ]]; then
  BODY="$(curl -sS --max-time 12 -H "APCA-API-KEY-ID: ${ALPACA_KEY}" -H "APCA-API-SECRET-KEY: ${ALPACA_SECRET}" "${ALPACA_EP}/clock" 2>&1 || true)"
  if probe_json_ok "$BODY" '.timestamp != null'; then
    add_pass "druck/alpaca: clock probe ok"
  else
    fail_with_reason "druck/alpaca" "$BODY" "Alpaca probe failed. Verify endpoint/key/secret in credentials/alpaca-api.json"
  fi
else
  add_fail "druck/alpaca: key/secret missing"
  add_manual "Alpaca key/secret missing in credentials/alpaca-api.json"
fi

if [[ -f "$SCHWAB_TOKEN" ]]; then
  HAS_REFRESH="$(safe_jq '(.refresh_token // .refresh // "") | length' "$SCHWAB_TOKEN")"
  if [[ "${HAS_REFRESH:-0}" -gt 10 ]]; then
    add_pass "druck/schwab: token file has refresh token"
  else
    add_fail "druck/schwab: refresh token missing"
    add_manual "Schwab token appears invalid. Re-auth may be required: openclaw models auth login --provider schwab"
  fi
else
  add_fail "druck/schwab: token file missing"
  add_manual "Missing Schwab token file at credentials/schwab-dev-token.json"
fi

# 5) Taylor's Mac node health (JSON for deterministic parsing)
NODES_JSON="$("$OPENCLAW_BIN" nodes status --json 2>/dev/null || true)"
NODE_CONNECTED="$(echo "$NODES_JSON" | jq -r '.nodes[]? | select(.displayName=="ios-build-node") | .connected' 2>/dev/null | head -1)"
if [[ "$NODE_CONNECTED" == "true" ]]; then
  add_pass "node/ios-build-node: connected"
elif [[ "$NODE_CONNECTED" == "false" ]]; then
  add_fail "node/ios-build-node: disconnected"
  add_manual "Taylor's Mac (ios-build-node) is disconnected. Ensure node host app is running and network/Tailscale are up."
else
  add_fail "node/ios-build-node: not found"
  add_manual "ios-build-node not found in paired nodes. Re-pair node if needed."
fi

# 6) GitHub SSH deterministic path
if [[ -x "$GITHUB_PREFLIGHT" ]]; then
  GH_OUT="$($GITHUB_PREFLIGHT 2>&1 || true)"
  if echo "$GH_OUT" | rg -q "OK"; then
    add_pass "github/ssh: preflight ok"
  else
    add_fail "github/ssh: preflight failed"
    add_manual "GitHub SSH preflight failed. Run ~/.openclaw/scripts/github-ssh-preflight.sh and fix auth."
  fi
else
  add_fail "github/ssh: preflight script missing"
  add_manual "Missing executable github-ssh-preflight.sh"
fi

# Compose report
{
  echo "Dwight Daily Bot Health Report"
  echo "Timestamp: ${TS}"
  echo "Summary: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT}"
  echo

  echo "Passes:"
  if [[ ${#PASS_LINES[@]} -eq 0 ]]; then
    echo "- none"
  else
    for l in "${PASS_LINES[@]}"; do
      echo "- ${l}"
    done
  fi

  echo
  echo "Warnings:"
  if [[ ${#WARN_LINES[@]} -eq 0 ]]; then
    echo "- none"
  else
    for l in "${WARN_LINES[@]}"; do
      echo "- ${l}"
    done
  fi

  echo
  echo "Failures:"
  if [[ ${#FAIL_LINES[@]} -eq 0 ]]; then
    echo "- none"
  else
    for l in "${FAIL_LINES[@]}"; do
      echo "- ${l}"
    done
  fi

  echo
  echo "Manual Intervention Needed From Aaron:"
  if [[ ${#MANUAL_LINES[@]} -eq 0 ]]; then
    echo "- none"
  else
    # de-duplicate while preserving order
    declare -A seen=()
    for l in "${MANUAL_LINES[@]}"; do
      if [[ -z "${seen[$l]:-}" ]]; then
        seen[$l]=1
        echo "- ${l}"
      fi
    done
  fi

  echo
  echo "Next Steps:"
  if [[ $FAIL_COUNT -eq 0 ]]; then
    echo "- Continue normal operations."
    echo "- Keep this check on a 24h cadence."
  else
    echo "- Resolve manual items above in priority order."
    echo "- Re-run: /home/aaron/.openclaw/scripts/daily-bot-health-check.sh"
    echo "- After fixes, validate Telegram and node health again."
  fi
} | tee "$REPORT_FILE"

exit 0
