#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Readiness preflight for the coding lane architecture.
# ACP is disabled by policy; Codex SDK runtime is required.

pass_count=0
warn_count=0
fail_count=0
SCRIPTS_DIR="${HOME}/.openclaw/scripts"
OPENCLAW_BIN="${OPENCLAW_BIN:-}"

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
  "$OPENCLAW_BIN" config get "$key" 2>/dev/null | tr -d '[:space:]' | tr -d '"'
}

config_get_json() {
  local key="$1"
  "$OPENCLAW_BIN" config get "$key" --json 2>/dev/null || true
}

check_codex_sdk_readiness() {
  local codex_plugin_enabled=""
  codex_plugin_enabled="$(config_get "plugins.entries.codex.enabled" || true)"

  if [[ "$codex_plugin_enabled" == "true" ]]; then
    pass "Codex SDK provider plugin enabled (plugins.entries.codex.enabled=true)"
  else
    fail "Codex SDK provider plugin is not enabled (plugins.entries.codex.enabled=true required)"
  fi
}

echo "== Coding Lane Preflight =="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ -z "$OPENCLAW_BIN" ]]; then
  OPENCLAW_BIN="$("$SCRIPTS_DIR/resolve-openclaw-bin.sh")"
fi

if "$OPENCLAW_BIN" config validate >/dev/null 2>&1; then
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
if [[ "$acp_enabled" == "false" && ( "$acpx_enabled" == "false" || -z "$acpx_enabled" ) ]]; then
  pass "ACP bridge disabled by policy (acp.enabled=false, plugins.entries.acpx.enabled=false)"
else
  fail "ACP bridge policy mismatch (expected disabled): acp.enabled=$acp_enabled, acpx.enabled=$acpx_enabled"
fi

check_codex_sdk_readiness

echo
echo "Summary: pass=$pass_count warn=$warn_count fail=$fail_count"

if (( fail_count > 0 )); then
  exit 2
fi

exit 0
