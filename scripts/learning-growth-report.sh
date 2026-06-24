#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-growth-report.sh — weekly, ZERO-CODEX world-model growth report.
# Runs the deterministic worldmodel_growth.py (snapshots metrics + computes the
# delta vs last week), then posts the compact summary to Telegram. No LLM/Codex.
# Paired host crontab entry: `34 17 * * 5` (Fri 17:34 ET, after close + learning).

OC="$HOME/.openclaw"
PY="/usr/bin/python3"
SCRIPT="$OC/workspaces/trading-intel/scripts/worldmodel_growth.py"
LOG="$OC/logs/learning-growth-report.log"
TG_ACCOUNT="druck"; TG_TARGET="6043080629"
OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >>"$LOG"; }
tg()  { "$OPENCLAW_BIN" message send --channel telegram --account "$TG_ACCOUNT" -t "$TG_TARGET" -m "$1" >/dev/null 2>&1 || log "WARN: telegram send failed"; }

mkdir -p "$(dirname "$LOG")"
log "===== growth report start ====="
out="$("$PY" "$SCRIPT" 2>&1)"; rc=$?
printf '%s\n' "$out" >>"$LOG"

if [[ $rc -ne 0 ]]; then
  tg "⚠️ Weekly world-model growth report FAILED (rc=$rc). Log: $LOG"
  log "===== growth report end rc=$rc ====="
  exit 1
fi

# Extract the compact block between the markers and send it (notify — marquee weekly report).
summary="$(printf '%s\n' "$out" | sed -n '/===TG_START===/,/===TG_END===/p' | sed '1d;$d')"
[[ -n "$summary" ]] && tg "$summary"
log "===== growth report end rc=0 ====="
exit 0
