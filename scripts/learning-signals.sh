#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-signals.sh — deterministic, ZERO-CODEX pre-open signal activation.
# Refreshes live prices, fires the calibrated mechanisms on current features, and
# writes the top-conviction signals as RAW hypotheses for the day's trading passes
# (score -> critic -> predict -> author_intents -> Risk gate -> executor). Replaces
# the `world-model-signals` gateway agent, which only launched these two scripts.
# Extra args pass through to signals_to_hypotheses (e.g. --dry-run for testing).
# Paired host crontab: `52 8 * * 1-5` (08:52 ET — after reconcile 08:45, before
# pre-market 09:00, so the raw hypotheses exist before the consuming passes run).

OC="$HOME/.openclaw"
PY="/usr/bin/python3"
TI="$OC/workspaces/trading-intel/scripts"
LOG="$OC/logs/learning-signals.log"
TG_ACCOUNT="druck"; TG_TARGET="6043080629"
OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >>"$LOG"; }
tg()  { local mode="$1"; shift
  local a=(message send --channel telegram --account "$TG_ACCOUNT" -t "$TG_TARGET" -m "$*")
  [[ "$mode" == "silent" ]] && a+=(--silent)
  "$OPENCLAW_BIN" "${a[@]}" >/dev/null 2>&1 || log "WARN: telegram send failed"; }

mkdir -p "$(dirname "$LOG")"

# This job writes the 48M-row feature store and canonical hypotheses. Never
# overlap it with the full trading pipeline; prior overlap produced DB locks and
# a malformed-image read during signal activation.
exec 9>"$OC/state/trading-money-path.lock"
if ! flock -w 300 9; then
  log "===== signals end (failed: money-path-lock-timeout) ====="
  tg notify "⚠️ Pre-open signals FAILED: money-path lock unavailable after 5 minutes. Log: $LOG"
  exit 1
fi

log "===== signals start (pid $$) args=$* ====="
FAILED=""

# 0) Market-wide event intake. This is intentionally broader than ticker
# news: forced flows, fund distress, ownership transfers, and market-structure
# shocks often become knowable before the affected ticker set is obvious.
# The frequent host-cron owns normal collection; this is a pre-open fallback.
if "$OC/scripts/market-event-intake.sh" >>"$LOG" 2>&1; then log "   ok: market-event-intake"
else log "   WARN: market-event-intake failed (advisory, health sweep will page)"; fi

# 1) fresh live prices into the point-in-time store
if "$PY" "$TI/feature_store.py" refresh-live >>"$LOG" 2>&1; then log "   ok: refresh-live"
else log "   FAIL: refresh-live"; FAILED="refresh-live"; fi

# 1b) X attention-spike features (x_mention_vol_z) for the most-liquid names.
# Was orphaned (last manual run 2026-06-23) — the consensus/crowding ingredient.
# Best-effort: an X API hiccup must not block the signal chain.
if "$PY" "$TI/x_features.py" --top-n 64 >>"$LOG" 2>&1; then log "   ok: x-features"
else log "   WARN: x-features failed (non-blocking)"; fi

# 1c) catalyst brief — the quant×news×social×model reasoning brief the research
# agents read (state/catalyst_brief.json). Was fully orphaned (no cron ran it;
# the UI showed "insights as of 6d ago"). Includes the nightly GBM top-10 as a
# discovery channel + MODEL_TOP/BOTTOM_DECILE flags. Best-effort: news-API
# hiccups must not block the signal chain.
if timeout 300 "$PY" "$TI/catalyst_scan.py" --days 7 >>"$LOG" 2>&1; then log "   ok: catalyst-brief"
else log "   WARN: catalyst-brief failed (non-blocking)"; fi

# 2) fire calibrated mechanisms -> top-conviction RAW hypotheses (extra args pass through).
# --scan-top-n 600: scan the top-600 liquid names (not just 200) so the 4 picks are
# selected from the full high-conviction pool; downstream caps/gates are unchanged.
log "-> signals_to_hypotheses --max-new 4 --scan-top-n 600 $*"
sig="$("$PY" "$TI/signals_to_hypotheses.py" --max-new 4 --scan-top-n 600 "$@" 2>&1)"; rc=$?
printf '%s\n' "$sig" >>"$LOG"
[[ $rc -ne 0 ]] && FAILED="${FAILED:+$FAILED, }signals_to_hypotheses"

head="$(printf '%s\n' "$sig" | grep -iE "wrote|hypothes|none|candidate" | head -1)"
log "===== signals end (failed: ${FAILED:-none}) ====="

if [[ -n "$FAILED" ]]; then
  tg notify "⚠️ Pre-open signals FAILED: ${FAILED}. ${head}. Log: $LOG"
  exit 1
fi
tg silent "📡 Pre-open signals — ${head:-done}"
exit 0
