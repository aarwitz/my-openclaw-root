#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
# trader-learn-deterministic.sh — closes the world-model learning loop (run daily, post-close).
#
#   1. grade_outcomes : grade matured predictions from realized market-relative returns ->
#                       set hypotheses.resolved_state  (the keystone that was missing)
#   2. calibrate      : resolve -> mechanism_observations -> recompute Beta posteriors (on top of
#                       the calibrated priors) -> draft promote/deprecate rule_proposals
#   3. extract_patterns: mine postmortems for recurring themes
#
# Emits one consolidated JSON. Never aborts on a single step's failure. The LIVE trading pass
# (trader-pass-deterministic.sh) is NOT touched by this script.
set -u
OPENCLAW="${OPENCLAW:-$HOME/.openclaw}"
PY="${PYTHON:-python3}"
AR="$OPENCLAW/workspaces/archivist/scripts"

run_step() {
  local name="$1"; shift
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ $rc -eq 0 ]; then
    echo "  \"$name\": \"ok\","
  else
    echo "  \"$name\": \"rc=$rc\","
    printf '%s\n' "$out" | tail -4 | sed 's/^/    # /'
  fi
  return 0
}

echo "{ \"stage\": \"trader-learn\", \"started\": \"$(date -u +%FT%TZ)\","
run_step grade_outcomes   "$PY" "$AR/grade_outcomes.py"
run_step calibrate        "$PY" "$AR/calibrate.py"
run_step extract_patterns "$PY" "$AR/extract_patterns.py"
echo "  \"done\": \"$(date -u +%FT%TZ)\" }"
