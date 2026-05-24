#!/usr/bin/env bash
set -euo pipefail

# Readiness preflight for the coding lane architecture.

pass_count=0
warn_count=0
fail_count=0

pass() {
  echo "PASS: $1"
  pass_count=$((pass_count + 1))
}

warn() {
  echo "WARN: $1"
  warn_count=$((warn_count + 1))
}

fail() {
  echo "FAIL: $1"
  fail_count=$((fail_count + 1))
}

config_get() {
  local key="$1"
  openclaw config get "$key" 2>/dev/null | tr -d '[:space:]' | tr -d '"'
}

config_get_json() {
  local key="$1"
  openclaw config get "$key" --json 2>/dev/null || true
}

check_binary() {
  local name="$1"
  local bin="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    pass "$name harness binary available: $bin"
  else
    warn "$name harness binary missing: $bin"
  fi
}

echo "== Coding Lane Preflight =="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if openclaw config validate >/dev/null 2>&1; then
  pass "openclaw.json validates"
else
  fail "openclaw.json validation failed"
fi

runtime_id="$(config_get "models.providers.openai.agentRuntime.id" || true)"
if [[ "$runtime_id" == "codex" ]]; then
  pass "fail-closed runtime is set: models.providers.openai.agentRuntime.id=codex"
else
  fail "fail-closed runtime missing; expected models.providers.openai.agentRuntime.id=codex"
fi

acp_enabled="$(config_get "acp.enabled" || true)"
acpx_enabled="$(config_get "plugins.entries.acpx.enabled" || true)"
if [[ "$acp_enabled" == "true" && "$acpx_enabled" == "true" ]]; then
  pass "ACP bridge enabled (acp.enabled=true, plugins.entries.acpx.enabled=true)"
else
  fail "ACP bridge not fully enabled (acp.enabled=$acp_enabled, acpx.enabled=$acpx_enabled)"
fi

perm_mode="$(config_get "plugins.entries.acpx.config.permissionMode" || true)"
non_interactive="$(config_get "plugins.entries.acpx.config.nonInteractivePermissions" || true)"
if [[ "$perm_mode" == "approve-all" && "$non_interactive" == "deny" ]]; then
  pass "ACP non-interactive permission policy is hardened"
else
  fail "ACP permission policy mismatch (permissionMode=$perm_mode, nonInteractivePermissions=$non_interactive)"
fi

max_sessions="$(config_get "acp.maxConcurrentSessions" || true)"
if [[ -n "$max_sessions" && "$max_sessions" =~ ^[0-9]+$ ]]; then
  if (( max_sessions >= 2 && max_sessions <= 4 )); then
    pass "ACP concurrency cap is in recommended range (2-4): $max_sessions"
  else
    warn "ACP concurrency cap outside recommended range (2-4): $max_sessions"
  fi
else
  warn "ACP concurrency cap not set"
fi

allowed_agents_json="$(config_get_json "acp.allowedAgents")"
if [[ -n "$allowed_agents_json" ]] && echo "$allowed_agents_json" | grep -Eq '"(cursor|copilot|claude)"'; then
  pass "ACP allowed agents include at least one preferred external harness"
else
  warn "ACP allowed agents do not include cursor/copilot/claude"
fi

check_binary "Cursor" "cursor-agent"
check_binary "Copilot" "copilot"
check_binary "Claude" "claude"
check_binary "Codex" "codex"

echo
echo "Summary: pass=$pass_count warn=$warn_count fail=$fail_count"

if (( fail_count > 0 )); then
  exit 2
fi

if (( warn_count > 0 )); then
  exit 1
fi

exit 0
