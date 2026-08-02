#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-rediscovery.sh — deterministic, ZERO-CODEX weekly re-discovery of the
# world-model research set over the FULL universe, then a READ-ONLY live-
# integration eligibility check. Development/reused holdouts may generate
# research candidates but never enter the live mechanism ledger automatically.
# Replaces the gateway agentTurn job. No LLM/Codex. Paired host crontab entry:
# `18 2 * * 0` (Sun 02:18 ET).

OC="$HOME/.openclaw"
TI="$OC/workspaces/trading-intel/scripts"
PY="/usr/bin/python3"                 # pinned: has pandas/numpy/sklearn for cron
LOG="$OC/logs/learning-rediscovery.log"
FEAT="$OC/state/features.sqlite"
TG_ACCOUNT="druck"; TG_TARGET="6043080629"
OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >>"$LOG"; }
tg()  { local mode="$1"; shift
  local a=(message send --channel telegram --account "$TG_ACCOUNT" -t "$TG_TARGET" -m "$*")
  [[ "$mode" == "silent" ]] && a+=(--silent)
  "$OPENCLAW_BIN" "${a[@]}" >/dev/null 2>&1 || log "WARN: telegram send failed"; }
run() { local label="$1"; shift
  log "-> $label: $*"
  if "$@" >>"$LOG" 2>&1; then log "   ok: $label"; return 0
  else local rc=$?; log "   FAIL($rc): $label"; FAILED="$label"; return "$rc"; fi; }

mkdir -p "$(dirname "$LOG")"
log "===== rediscovery start (pid $$) ====="

FAILED=""
run "backtest ALL" "$PY" "$TI/mechanism_backtest.py" --universe ALL --test-start 2020-06-18 \
 && run "promote"      "$PY" "$TI/promote_mechanisms.py" \
 && run "correlation"  "$PY" "$TI/mechanism_correlation.py" \
 && run "regime"       "$PY" "$TI/mechanism_regime.py" \
 && run "eligibility"  "$PY" "$TI/integrate_calibrated.py" --check-only
rc=$?

surv="$("$PY" - "$FEAT" <<'PYEOF'
import sqlite3,sys
try: print(sqlite3.connect(sys.argv[1]).execute("select count(*) from calibrated_mechanisms").fetchone()[0])
except Exception: print("?")
PYEOF
)"
log "===== rediscovery end rc=$rc calibrated=$surv ====="

if [[ $rc -ne 0 ]]; then
  tg notify "⚠️ Weekly mechanism rediscovery FAILED at: ${FAILED:-unknown} (rc=$rc). Log: $LOG"
  exit 1
fi
tg silent "🔬 Weekly rediscovery done — $surv development survivor(s); live eligibility checked, live mechanism ledger unchanged."
exit 0
