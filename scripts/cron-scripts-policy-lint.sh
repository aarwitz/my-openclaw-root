#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Weekly policy lint runner for automation.
# - Runs scripts-policy-lint.sh (must stay wrapper-guarded)
# - Emits a compact JSON line into ~/.openclaw/logs/script-audit.jsonl

ROOT="$HOME/.openclaw"
LOG_DIR="$ROOT/logs"
AUDIT_LOG="$LOG_DIR/script-audit.jsonl"
LINT_SCRIPT="$ROOT/scripts/scripts-policy-lint.sh"
POLICY_FILE="$ROOT/scripts/policy.json"

mkdir -p "$LOG_DIR"

if ! command -v jq >/dev/null 2>&1; then
  echo "cron-scripts-policy-lint: jq is required" >&2
  exit 127
fi

START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
HOSTNAME_VAL="$(hostname 2>/dev/null || echo unknown)"

set +e
OUTPUT="$($LINT_SCRIPT 2>&1)"
EXIT_CODE=$?
set -e

END_EPOCH="$(date +%s)"
END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DURATION="$((END_EPOCH - START_EPOCH))"

SUMMARY_LINE="$(printf '%s\n' "$OUTPUT" | tail -n 1)"
CHECKED="$(printf '%s\n' "$OUTPUT" | sed -n 's/.*checked=\([0-9]\+\).*/\1/p' | tail -n 1)"
VIOLATIONS="$(printf '%s\n' "$OUTPUT" | sed -n 's/.*violations=\([0-9]\+\).*/\1/p' | tail -n 1)"

if [[ -z "$CHECKED" ]]; then CHECKED=0; fi
if [[ -z "$VIOLATIONS" ]]; then VIOLATIONS=0; fi

jq -cn \
  --arg ts "$END_TS" \
  --arg started_at "$START_TS" \
  --arg host "$HOSTNAME_VAL" \
  --arg script "$LINT_SCRIPT" \
  --arg policy "$POLICY_FILE" \
  --arg output "$OUTPUT" \
  --arg summary "$SUMMARY_LINE" \
  --argjson checked "$CHECKED" \
  --argjson violations "$VIOLATIONS" \
  --argjson exit_code "$EXIT_CODE" \
  --argjson duration_seconds "$DURATION" \
  '{
    ts: $ts,
    started_at: $started_at,
    host: $host,
    event: "scripts_policy_lint",
    script: $script,
    policy_file: $policy,
    checked: $checked,
    violations: $violations,
    exit_code: $exit_code,
    duration_seconds: $duration_seconds,
    summary: $summary,
    output: $output
  }' >> "$AUDIT_LOG"

printf '%s\n' "$OUTPUT"
exit "$EXIT_CODE"
