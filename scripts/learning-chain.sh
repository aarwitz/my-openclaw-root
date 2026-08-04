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
#   2. forward-shadow              — immutable no-authority candidate observations
#   3. forward-shadow-report       — deterministic, human-gated forward assessment
#   4. grade_outcomes              — resolve matured predictions -> mechanism_observations
#   4. prediction-challenger       — grade shadow calibration variants (no trading authority)
#   5. calibrate                   — fold outcomes into Beta posteriors + draft rule_proposals
#   6. compute_attribution         — closed-position P&L attribution vs SPY -> benchmarks
#   7. selection-funnel            — counterfactual returns for every research candidate
#   8. extract_patterns            — recurring mechanism themes from postmortems
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

# This chain writes both canonical SQLite stores for an extended period. It
# shares the same lock as trader/guard/signal passes so post-close learning
# cannot race a delayed money-path run.
exec 9>"$OC/state/trading-money-path.lock"
if ! flock -w 600 9; then
  log "===== learning chain end (failed: money-path-lock-timeout) ====="
  tg notify "⚠️ Learning chain FAILED: money-path lock unavailable after 10 minutes. Log: $LOG"
  exit 1
fi

log "===== learning chain start (pid $$) ====="
# --top-n 600 (not the 150 default): signal_scan scans the top-600 liquid names,
# so the post-close refresh must cover the full scanned pool or 450 of them trade
# on stale feature tails (2026-07-02 dataset audit). Post-close has no deadline;
# the tight 08:52 pre-open refresh stays at 150 (daily bars only change at close).
# D54: the internal ledger IS the brokerage — back it up before anything else.
step "ledger-backup"       bash "$HOME/.openclaw/scripts/backup-ledger.sh"
# D110/D111: one release gate owns syntax, docs, every discoverable unit,
# money-path conservation, and no-write trading-day/failure scenario replay.
step "autotrade-preflight" bash "$HOME/.openclaw/scripts/run-with-trace.sh" --tag test "$HOME/.openclaw/scripts/autotrade-preflight.py"
step "sync-symbol-aliases" "$PY" "$TI/sync_symbol_aliases.py"
step "refresh-live"        "$PY" "$TI/feature_store.py" refresh-live --top-n 600
step "forward-shadow"      "$PY" "$TI/forward_shadow.py"
step "forward-shadow-report" "$PY" "$TI/forward_shadow_report.py"
# LLM feature factory (P3): type today's news into point-in-time features
# (llm_news_dir / material_ct / neg_mat_ct). Cached per batch — only new
# articles cost a model call. Best-effort: never blocks the learning chain.
step "llm-features"        "$PY" "$TI/llm_features.py" daily --top-n 64
# "Lazy Prices" filing deltas — new 10-K/Qs land any day; the MinHash signature
# cache makes the daily walk nearly free after the initial backfill.
step "edgar-deltas"        "$PY" "$TI/edgar_deltas.py" daily --top-n 150
# Economic-link momentum (peers' relative return propagates with a lag) + KG peer edges
step "peer-features"       "$PY" "$TI/peer_features.py" daily --top-n 300
# Cross-name narratives must emerge from price structure and then be graded by
# prices. Both jobs are offline/deterministic; neither has trading authority.
step "industry-rs"         "$PY" "$TI/industry_rs.py"
step "score-themes"        "$PY" "$TI/score_themes.py"
step "theme-context"       "$PY" "$TI/theme_context.py" --write
# ADVISORY nightly ML rank (P2 prep): builds the live out-of-sample track record
# in features.sqlite::ml_scores. Research may use it for discovery and the
# separate internal shadow model book rebalances from it monthly; desk intents,
# sizing, and Risk do not consume it (see docs/06_ALPHA_ENGINE_ROADMAP.md P2).
step "ml-score-live"       "$PY" "$TI/ml_ranker.py" --score-live --top-n 600
# Internal paper engine: apply audited corporate actions, mark owned books,
# rebalance the experimental model book, and verify ledger arithmetic.
step "sim-integrity"       "$PY" "$HOME/.openclaw/workspaces/executor/scripts/sim_broker.py" nightly
# Market x-ray (D65): decompose what the tape did (breadth/dispersion/corr/
# factor spreads/vol), flag |z|>=2 phenomena the desk never engaged = BLIND
# SPOTS — reviewed weekly by the Sunday audit. Self-auditing beats waiting
# for the operator to point at the market.
step "market-xray"         "$PY" "$HOME/.openclaw/workspaces/trading-intel/scripts/market_xray.py" snapshot
# D61: fundamental forecasting loop — grade FCF/EPS forecasts against newly
# reported actuals daily; refresh forecasts for book names (cheap, cached).
step "fund-grade"          "$PY" "$TI/fundamental_forecast.py" grade
step "fund-forecast"       "$PY" "$TI/fundamental_forecast.py" forecast --book
step "grade_outcomes"      "$PY" "$AR/grade_outcomes.py"
step "prediction-challenger" "$PY" "$DEV/prediction_challenger.py" grade
step "calibrate"           "$PY" "$AR/calibrate.py"
# Close the challenged->resolve loop: deep second-order LLM resolution of stale challenged
# theses (HOLD/CLOSE/FLIP). Bounded per run; flips flow through quant->critic->risk gated.
step "resolve_challenged"  "$PY" "$TI/resolve_challenged.py" --max "${RESOLVE_MAX:-16}"
# Outcome-grade challenges + resolver decisions against the market (no organ ungraded).
step "grade_resolutions"   "$PY" "$TI/grade_resolutions.py"
step "grade_valuations"    "$PY" "$TI/grade_valuations.py"
# Same-day second-order decomposition of the biggest single-name moves (front of the
# research funnel — attacks research:big_story_direction). Stances land gated in 'scored'.
# HELD: v1 decomposition prompt scored 0/7 direction-correct OOS (backtest_decomposition
# 2026-07-23, fade-bias) — live authoring stays off until a prompt version BEATS both
# naive baselines on the corpus. Re-enable by raising DECOMP_MAX once validated.
step "decompose_events"    "$PY" "$TI/decompose_events.py" --max "${DECOMP_MAX:-0}"
step "exam-report"         "$PY" "$TI/exam_report.py" --since "$(date -u -d '-1 day' +%Y-%m-%d)"
step "mark_positions"      "$PY" "$DEV/mark_positions.py"
step "compute_attribution" "$PY" "$DEV/compute_attribution.py"
step "selection-funnel"    "$PY" "$DEV/selection_funnel_attribution.py" backfill
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
  # D57: an alert that fails to send is a silent failure of the alerting itself
  # (Jun 29-Jul 1: three consecutive send failures, zero escalation). Page via
  # the direct Bot API path, which works even with the gateway down.
  if grep -q "WARN: telegram send failed" <(tail -5 "$LOG"); then
    bash "$HOME/.openclaw/scripts/page-operator.sh" "chain-alert-dead" "learning chain FAILED (${FAILED}) AND its telegram alert failed to send" || true
  fi
  exit 1
fi
EXAM=$("$PY" "$TI/exam_report.py" --since "$(date -u -d '-1 day' +%Y-%m-%d)" 2>/dev/null | grep '^SUMMARY:' | head -1)
if [[ "$EXAM" == *graded* ]]; then
  tg notify "🎓 ${EXAM#SUMMARY: } — full card in learning-chain.log"
else
  tg silent "🧪 Daily learning chain ok — predictions resolved $RES, observations $OBS, rule_proposals $RP, attribution $ATTR, patterns $PAT"
fi
exit 0
