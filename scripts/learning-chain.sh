#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-chain.sh — deterministic, ZERO-CODEX daily post-close learning loop.
# Owns the MATH of world-model learning so it runs reliably regardless of LLM /
# Codex availability (the daily-learning AGENT keeps only its research/narrative/
# rule-proposal role). Best-effort: every stage runs; the chain never aborts on a
# single failure, and Telegram-alerts if any stage failed.
# Stages (dependency order):
#   1. feature_store refresh-live  — fresh EOD prices into the point-in-time store
#   2. grade_outcomes              — resolve matured predictions -> mechanism_observations
#   3. calibrate                   — fold outcomes into Beta posteriors + draft rule_proposals
#   4. compute_attribution         — closed-position P&L attribution vs SPY -> benchmarks
#   5. extract_patterns            — recurring mechanism themes from postmortems
# Paired host crontab entry: `12 16 * * 1-5` (weekdays 16:12 ET, post-close, before daily-learning).

OC="$HOME/.openclaw"
PY="/usr/bin/python3"
TI="$OC/workspaces/trading-intel/scripts"
AR="$OC/workspaces/archivist/scripts"
DEV="$OC/workspaces/developer/scripts"
DB="$OC/state/trading-intel.sqlite"
LOG="$OC/logs/learning-chain.log"
TG_ACCOUNT="druck"; TG_TARGET="6043080629"
OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >>"$LOG"; }
tg()  { local mode="$1"; shift
  local a=(message send --channel telegram --account "$TG_ACCOUNT" -t "$TG_TARGET" -m "$*")
  [[ "$mode" == "silent" ]] && a+=(--silent)
  "$OPENCLAW_BIN" "${a[@]}" >/dev/null 2>&1 || log "WARN: telegram send failed"; }
FAILED=""
step() { local label="$1"; shift
  log "-> $label: $*"
  if "$@" >>"$LOG" 2>&1; then log "   ok: $label"
  else local rc=$?; log "   FAIL($rc): $label"; FAILED="${FAILED:+$FAILED, }$label"; fi; }

mkdir -p "$(dirname "$LOG")"
log "===== learning chain start (pid $$) ====="
# --top-n 600 (not the 150 default): signal_scan scans the top-600 liquid names,
# so the post-close refresh must cover the full scanned pool or 450 of them trade
# on stale feature tails (2026-07-02 dataset audit). Post-close has no deadline;
# the tight 08:52 pre-open refresh stays at 150 (daily bars only change at close).
step "refresh-live"        "$PY" "$TI/feature_store.py" refresh-live --top-n 600
# LLM feature factory (P3): type today's news into point-in-time features
# (llm_news_dir / material_ct / neg_mat_ct). Cached per batch — only new
# articles cost a model call. Best-effort: never blocks the learning chain.
step "llm-features"        "$PY" "$TI/llm_features.py" daily --top-n 64
# "Lazy Prices" filing deltas — new 10-K/Qs land any day; the MinHash signature
# cache makes the daily walk nearly free after the initial backfill.
step "edgar-deltas"        "$PY" "$TI/edgar_deltas.py" daily --top-n 150
step "grade_outcomes"      "$PY" "$AR/grade_outcomes.py"
step "calibrate"           "$PY" "$AR/calibrate.py"
step "compute_attribution" "$PY" "$DEV/compute_attribution.py"
step "extract_patterns"    "$PY" "$AR/extract_patterns.py"
log "===== learning chain end (failed: ${FAILED:-none}) ====="

# headline learning counts for the summary
res="$("$PY" - "$DB" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
def n(q):
    try: return c.execute(q).fetchone()[0]
    except Exception: return "?"
print("|".join(str(x) for x in [
    n("SELECT COUNT(*) FROM predictions WHERE resolved_at IS NOT NULL"),
    n("SELECT COUNT(*) FROM mechanism_observations"),
    n("SELECT COUNT(*) FROM rule_proposals"),
    n("SELECT COUNT(*) FROM attribution"),
    n("SELECT COUNT(*) FROM patterns"),
]))
PYEOF
)"
IFS='|' read -r RES OBS RP ATTR PAT <<<"$res"

if [[ -n "$FAILED" ]]; then
  tg notify "⚠️ Daily learning chain — FAILED stage(s): ${FAILED}. (resolved=$RES obs=$OBS proposals=$RP). Log: $LOG"
  exit 1
fi
tg silent "🧪 Daily learning chain ok — predictions resolved $RES, observations $OBS, rule_proposals $RP, attribution $ATTR, patterns $PAT"
exit 0
