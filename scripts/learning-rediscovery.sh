#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-rediscovery.sh — deterministic, ZERO-CODEX weekly integrity check of
# the canonical immutable historical replay. It deliberately does not rerun the
# reused single split, rewrite discovered/calibrated tables, or execute the old
# promote -> correlation -> regime chain. Candidate discovery is an offline,
# fingerprinted walk-forward run; live promotion remains a separate human gate.
# Paired host crontab entry: `18 2 * * 0` (Sun 02:18 ET).

OC="$HOME/.openclaw"
TI="$OC/workspaces/trading-intel/scripts"
PY="/usr/bin/python3"                 # pinned: has pandas/numpy/sklearn for cron
LOG="$OC/logs/learning-rediscovery.log"
REPORT="$OC/state/historical-validation/purged_walkforward_v2.json"
SNAPSHOT="$OC/state/research-snapshots/purged_walkforward_v2"
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
run "frozen historical evidence gate" "$PY" "$TI/historical_report_check.py" \
  --report "$REPORT" --snapshot-dir "$SNAPSHOT"
rc=$?

surv="$("$PY" - "$REPORT" <<'PYEOF'
import json,sys
try: print(len(json.load(open(sys.argv[1]))["stable_development_candidates"]))
except Exception: print("?")
PYEOF
)"
log "===== frozen evidence check end rc=$rc stable_development_candidates=$surv ====="

if [[ $rc -ne 0 ]]; then
  tg notify "⚠️ Weekly frozen historical evidence check FAILED at: ${FAILED:-unknown} (rc=$rc). No research or live tables were changed. Log: $LOG"
  exit 1
fi
tg silent "🔬 Weekly frozen replay verified — $surv historical development candidate(s); promotion authority remains none and live state was untouched."
exit 0
