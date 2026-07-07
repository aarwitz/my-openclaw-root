#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
# trader-pass-deterministic.sh
#
# Deterministic prefix for every trader cron pass. Runs:
#   1. classify_regime (writes new regime row)
#   1b. value_universe (DCF/comps fair value + margin of safety + realized vol -> valuations)
#   2. score_hypotheses (writes quant_score on raw hypotheses)
#   3. critic_baseline (deterministic critic challenges; raises bar on rich names)
#   4. predict (world-model probabilistic call: p_correct + name-aware return band)
#   5. author_intents (fractional-Kelly sizing from the prediction)
#   6. gate_evaluator on every proposed/critic_review intent -> risk_review
#   7. risk_gate (Risk agent caps size; risk_review -> approved|blocked)
#   8. execute_intent for every approved intent (LIVE — paper account only)
#   8b. sync_fills (broker truth per order id → fills, intents, positions)
#   9. reconcile (alpaca vs DB)
#  10. benchmark_scoreboard (portfolio vs SPY per horizon -> benchmarks rows)
#  11. snapshot writer (refreshes lidisolutions.ai data.json)
#  12. audit_pipeline_health + audit_app_snapshot (Bessent watchdogs)
#
# Outputs ONE consolidated JSON to stdout summarizing each step. The agent
# turn that calls this script should parse this JSON and compose the
# narrative (Telegram + retail_insights) from it.
#
# Exit codes:
#   0 ok (possibly with yellow health)
#   1 red health (do not surface optimistic narrative to retail)
#   2 hard failure (script itself crashed)
#
# Usage:
#   trader-pass-deterministic.sh [--skip-execute] [--skip-snapshot]

set -u

OPENCLAW="${OPENCLAW:-$HOME/.openclaw}"
LIDI_REPO="${TRADER_INTEL_REPO:-${LIDI:-$HOME/repos/lidi-solutions}}"
APP_DATA_JSON="$LIDI_REPO/public/solutions/trader_intel/app/data.json"
DIST_DATA_JSON="$LIDI_REPO/dist/solutions/trader_intel/app/data.json"
SKIP_EXECUTE=0
SKIP_SNAPSHOT=0
PUBLISH=0
for arg in "$@"; do
  case "$arg" in
    --skip-execute) SKIP_EXECUTE=1 ;;
    --skip-snapshot) SKIP_SNAPSHOT=1 ;;
    --publish) PUBLISH=1 ;;
  esac
done

cd "$OPENCLAW" || { echo '{"ok":false,"error":"cd failed"}'; exit 2; }

run_step() {
  local name="$1" cmd_timeout="$2"; shift 2
  local out
  out=$(timeout "$cmd_timeout" "$@" 2>&1)
  local rc=$?
  # quote payload for JSON; collapse to single line
  local one
  one=$(printf '%s' "$out" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read()))')
  printf ',\n  "%s": {"rc": %d, "out": %s}' "$name" "$rc" "$one"
}

printf '{\n  "started_at": "%s"' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Market-calendar gate: cron only knows Mon-Fri; the exchange calendar knows
# holidays (2026-07-03: five passes ran on the Jul-4-observed holiday and one
# queued an order into a 3-day weekend gap). On a non-trading day the pass
# still refreshes data/scoreboard/snapshot but skips authoring and execution.
# Fail-open on calendar errors (a dead calendar API must not halt the desk on
# a real trading day — the executor has its own fail-closed clock gate).
TRADING_DAY=$(timeout 20 python3 -c "
import sys
sys.path.insert(0, 'workspaces/trading-intel/scripts')
from datetime import datetime
from zoneinfo import ZoneInfo
try:
    from connectors.alpaca import is_trading_day
    today = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    print('1' if is_trading_day(today) else '0')
except Exception:
    print('1')" 2>/dev/null)
[[ "$TRADING_DAY" == "0" ]] || TRADING_DAY=1
printf ',\n  "market_today": {"trading_day": %s}' "$([[ "$TRADING_DAY" == "1" ]] && echo true || echo false)"

run_step "classify_regime" 90 python3 workspaces/quant/scripts/classify_regime.py
run_step "value_universe" 180 python3 workspaces/trading-intel/scripts/valuation.py universe
run_step "score_hypotheses" 60 python3 workspaces/quant/scripts/score_hypotheses.py
run_step "critic_baseline" 30 python3 workspaces/critic/scripts/critic_baseline.py
run_step "predict" 90 python3 workspaces/quant/scripts/predict.py --states scored,challenged,ready
if [[ "$TRADING_DAY" == "1" ]]; then
  # D53: enforce declared stop rules BEFORE authoring new ideas — cut rule-
  # breaching losers first, then deploy freed capital. (2026-07-07: ORCL sat
  # at -22.6% against a stated -8% stop while the desk kept opening names.)
  run_step "enforce_stops" 90 python3 workspaces/trader/scripts/enforce_stops.py
  run_step "author_intents" 60 python3 workspaces/trader/scripts/author_intents.py
  run_step "gate_evaluator" 60 python3 workspaces/trading-intel/scripts/gate_evaluator.py --all-proposed
  run_step "risk_gate" 90 python3 workspaces/risk/scripts/gate_risk_intents.py --all-pending
else
  printf ',\n  "author_intents": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "gate_evaluator": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "risk_gate": {"rc": 0, "skipped": "non-trading day"}'
fi
if [[ "$SKIP_EXECUTE" -eq 0 && "$TRADING_DAY" == "1" ]]; then
  run_step "execute_intent" 60 python3 workspaces/executor/scripts/execute_intent.py
else
  printf ',\n  "execute_intent": {"rc": 0, "skipped": true}'
fi
# sync_fills BEFORE reconcile: pulls broker truth per order id (fill price,
# filled_at) and books positions against the real hypothesis lineage. Without
# it, non-instant fills rot as pending_new until reconcile marks them
# closed_unknown (price lost) and re-creates the position as a POS-SYNC
# placeholder with a fabricated hypothesis (2026-07-06 finding).
run_step "sync_fills" 60 python3 workspaces/executor/scripts/sync_fills.py
run_step "reconcile" 30 python3 workspaces/executor/scripts/reconcile.py
# D52: mark the internal desk book each pass so book_equity (the equity curve
# the app serves) stays fresh intraday — daily-keyed, so this upserts today.
run_step "sim_mark" 60 python3 workspaces/executor/scripts/sim_broker.py mark --book desk
run_step "portfolio_risk" 120 python3 workspaces/trading-intel/scripts/risk_model.py snapshot
run_step "scoreboard" 60 python3 workspaces/trading-intel/scripts/benchmark_scoreboard.py --backfill
# Macro layer: keep the forward calendar populated and detect realized surprises
# (both idempotent + cheap; pull-actuals writes a market_event on a big surprise).
run_step "macro_seed" 30 python3 workspaces/trading-intel/scripts/macro_calendar.py seed --months 3
run_step "macro_actuals" 45 python3 workspaces/trading-intel/scripts/macro_calendar.py pull-actuals
if [[ "$SKIP_SNAPSHOT" -eq 0 ]]; then
  if [[ ! -d "$LIDI_REPO/public/solutions/trader_intel/app" ]]; then
    printf ',\n  "snapshot": {"rc": 2, "out": "FATAL: lidi-solutions repo not mounted at %s. Add bind mount in docker-compose.openclaw.yml and safe-restart."}' "$LIDI_REPO"
    printf ',\n  "finished_at": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    exit 2
  fi
  run_step "snapshot" 60 python3 workspaces/developer/scripts/snapshot_builder.py --out "$APP_DATA_JSON"
  # Overlay the richer JS snapshot (per-agent headline/lastOutput) — it reads
  # the same canonical DB but produces the shape the UI's paintAgents()
  # consumes. If node or the script is missing we keep the Python output as
  # a fallback.
  if command -v node >/dev/null 2>&1 && [[ -f "$LIDI_REPO/scripts/snapshot-trader-intel.mjs" ]]; then
    run_step "snapshot_overlay" 60 bash -c "cd '$LIDI_REPO' && node scripts/snapshot-trader-intel.mjs"
  else
    printf ',\n  "snapshot_overlay": {"rc": 0, "skipped": true}'
  fi
  printf ',\n  "snapshot_path": %s' "$(printf '%s' "$APP_DATA_JSON" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read()))')"
  # Mirror to dist/ so wrangler-served build sees the new payload without a vite rebuild.
  mkdir -p "$(dirname "$DIST_DATA_JSON")" 2>/dev/null || true
  cp -f "$APP_DATA_JSON" "$DIST_DATA_JSON" 2>/dev/null || true
fi
run_step "pipeline_health" 30 python3 workspaces/developer/scripts/audit_pipeline_health.py
run_step "app_snapshot" 20 python3 workspaces/developer/scripts/audit_app_snapshot.py --path "$APP_DATA_JSON"

if [[ "$PUBLISH" -eq 1 && "$SKIP_SNAPSHOT" -eq 0 ]]; then
  # Data-only publish (KV put via /api/trader-data, seconds) — since 2026-07-02 the
  # app reads data from KV, so passes no longer need a full vite build + Pages
  # deploy. Code changes still ship via publish-trader-intel.sh (manual / on merge).
  run_step "publish" 120 bash "$OPENCLAW/scripts/push-trader-data.sh"
fi

printf ',\n  "finished_at": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
