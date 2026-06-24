#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-rediscovery.sh — deterministic, ZERO-CODEX weekly re-discovery of the
# world-model mechanism set over the FULL universe, then INCREMENTAL integration
# that PRESERVES the live learning ledger (observed_hits/misses, predictions,
# observations, attribution). Replaces the gateway agentTurn job. No LLM/Codex.
# NEVER passes --reset. Paired host crontab entry: `18 2 * * 0` (Sun 02:18 ET).

OC="$HOME/.openclaw"
TI="$OC/workspaces/trading-intel/scripts"
PY="/usr/bin/python3"                 # pinned: has pandas/numpy/sklearn for cron
LOG="$OC/logs/learning-rediscovery.log"
DB="$OC/state/trading-intel.sqlite"
FEAT="$OC/state/features.sqlite"
BAK="$OC/backups/trading-intel-PRE-REDISCOVERY-$(date +%Y%m%d).sqlite"
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

mkdir -p "$(dirname "$LOG")" "$OC/backups"
log "===== rediscovery start (pid $$) ====="

FAILED=""
run "backup db"     cp "$DB" "$BAK" \
 && run "backtest ALL" "$PY" "$TI/mechanism_backtest.py" --universe ALL --test-start 2020-06-18 \
 && run "promote"      "$PY" "$TI/promote_mechanisms.py" \
 && run "correlation"  "$PY" "$TI/mechanism_correlation.py" \
 && run "regime"       "$PY" "$TI/mechanism_regime.py" \
 && run "integrate"    "$PY" "$TI/integrate_calibrated.py"
rc=$?

surv="$("$PY" - "$FEAT" <<'PYEOF'
import sqlite3,sys
try: print(sqlite3.connect(sys.argv[1]).execute("select count(*) from calibrated_mechanisms").fetchone()[0])
except Exception: print("?")
PYEOF
)"
log "===== rediscovery end rc=$rc calibrated=$surv ====="

if [[ $rc -ne 0 ]]; then
  tg notify "⚠️ Weekly mechanism rediscovery FAILED at: ${FAILED:-unknown} (rc=$rc). DB backup kept: $BAK. Log: $LOG"
  exit 1
fi
tg silent "🔬 Weekly rediscovery done — $surv calibrated mechanisms (live ledger preserved). Backup: $(basename "$BAK")"
exit 0
