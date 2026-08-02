#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
# trader-pass-deterministic.sh
#
# Deterministic prefix for every trader cron pass. Runs:
#   1. classify_regime (writes new regime row)
#   1b. value_universe (DCF/comps fair value + margin of safety + realized vol -> valuations)
#   1c. signals_to_hypotheses (stored world-model fires -> raw hypotheses)
#   2. score_hypotheses (writes quant_score on raw hypotheses)
#   3. critic_baseline (deterministic triage only; never promotes)
#   4. predict ready hypotheses after substantive Critic review
#   4b. ml_evidence_track (advisory ranker trust ledger; no trading control)
#   5. enforce_horizons (exit theses past wm horizon + grace — D55)
#   5b. enforce_stops (D53 stop-rule enforcement)
#   6. author_intents (adaptive deployment governor + probabilistic sizing)
#   7. gate_evaluator on every proposed/critic_review intent -> risk_review
#   8. risk_gate (Risk agent caps size; risk_review -> approved|blocked)
#   9. execute_intent for every approved intent (LIVE — paper account only)
#   9b. sync_fills (broker truth per order id → fills, intents, positions)
#  10. reconcile (ledger vs DB)
#  11. benchmark_scoreboard (portfolio vs SPY per horizon -> benchmarks rows)
#  12. capital_efficiency_audit (rank dollar bottlenecks -> capital_efficiency_snapshots)
#  13. snapshot writer (refreshes lidisolutions.ai data.json)
#  14. audit_pipeline_health + audit_app_snapshot (Bessent watchdogs)
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
#   trader-pass-deterministic.sh --scenario
#
# `--scenario` is a no-write orchestration replay used by preflight.  Every
# stage is simulated, while the real branching, dependency circuit, JSON
# assembly, and exit semantics run unchanged.  It is explicit-only so an
# environment variable can never accidentally disable a live paper pass.

set -uo pipefail

OPENCLAW="${OPENCLAW:-$HOME/.openclaw}"
LIDI_REPO="${TRADER_INTEL_REPO:-${LIDI:-$HOME/repos/lidi-solutions}}"
SNAPSHOT_DIR="${TRADER_INTEL_RUNTIME_DIR:-$OPENCLAW/state/trader-intel-snapshot}"
APP_DATA_JSON="$SNAPSHOT_DIR/data.json"
SKIP_EXECUTE=0
SKIP_SNAPSHOT=0
PUBLISH=0
PIPELINE_RC=0
PIPELINE_FAILURES=()
EXECUTION_BLOCKERS=()
SCENARIO=0
for arg in "$@"; do
  case "$arg" in
    --skip-execute) SKIP_EXECUTE=1 ;;
    --skip-snapshot) SKIP_SNAPSHOT=1 ;;
    --publish) PUBLISH=1 ;;
    --scenario) SCENARIO=1 ;;
  esac
done

if [[ "$SCENARIO" -eq 1 && "$PUBLISH" -eq 1 ]]; then
  echo '{"ok":false,"error":"--scenario cannot publish"}'
  exit 2
fi

cd "$OPENCLAW" || { echo '{"ok":false,"error":"cd failed"}'; exit 2; }

# Architecture invariant before any write or order path. This aborts the pass if
# a legacy provider credential, connector, backend switch, prompt, or active-code
# reference is reintroduced.
INTERNAL_PAPER_REPORT="$(mktemp /tmp/autotrade-internal-paper-check.XXXXXX.json)" || {
  echo '{"ok":false,"error":"cannot allocate architecture-check report"}'
  exit 2
}
cleanup_internal_paper_report() { rm -f "$INTERNAL_PAPER_REPORT"; }
trap cleanup_internal_paper_report EXIT
if ! python3 "$OPENCLAW/scripts/check-internal-paper-only.py" >"$INTERNAL_PAPER_REPORT"; then
  python3 -c 'import json,sys; print(json.dumps({"ok": False, "error": "internal-paper-only architecture violation", "report": json.load(open(sys.argv[1]))}))' "$INTERNAL_PAPER_REPORT"
  exit 2
fi

# One writer pipeline at a time. Eight scheduled roles can otherwise overlap
# their full/second passes and contend on the trading + feature databases.
# Scenario replay never writes, so it must not contend with or suppress a live
# money-path pass.
if [[ "$SCENARIO" -eq 0 ]]; then
  exec 9>"$OPENCLAW/state/trading-money-path.lock"
  if ! flock -n 9; then
    printf '{"started_at":"%s","skipped":true,"reason":"another money-path pipeline holds the lock","finished_at":"%s"}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    exit 0
  fi
  export TRADING_MONEY_LOCK_HELD=1
fi

run_step() {
  local name="$1" cmd_timeout="$2"; shift 2
  local out rc
  if [[ "$SCENARIO" -eq 1 ]]; then
    if [[ ",${AUTOTRADE_SCENARIO_FAIL_STEPS:-}," == *",${name},"* ]]; then
      out="scenario-injected failure: ${name}"
      rc=42
    else
      out="{\"scenario_step\":\"${name}\",\"ok\":true}"
      rc=0
    fi
  else
    out=$(timeout "$cmd_timeout" "$@" 2>&1)
    rc=$?
  fi
  if (( rc != 0 )); then
    PIPELINE_RC=1
    PIPELINE_FAILURES+=("$name:$rc")
    case "$name" in
      classify_regime|value_universe|hypothesis_hygiene|score_hypotheses|critic_baseline|predict|enforce_horizons|enforce_falsifiers|enforce_stops|author_intents|gate_evaluator|risk_gate|sim_integrity_pre|reconcile_preflight)
        EXECUTION_BLOCKERS+=("$name:$rc") ;;
    esac
  fi
  # quote payload for JSON; collapse to single line
  local one
  one=$(printf '%s' "$out" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read()))')
  printf ',\n  "%s": {"rc": %d, "out": %s}' "$name" "$rc" "$one"
}

printf '{\n  "started_at": "%s"' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ "$SCENARIO" -eq 1 ]]; then
  printf ',\n  "scenario": {"enabled": true, "injected_failures": %s}' \
    "$(printf '%s' "${AUTOTRADE_SCENARIO_FAIL_STEPS:-}" | python3 -c 'import json,sys; print(json.dumps([x for x in sys.stdin.read().split(",") if x]))')"
fi

# Market-calendar gate: cron only knows Mon-Fri; the exchange calendar knows
# holidays (2026-07-03: five passes ran on the Jul-4-observed holiday and one
# queued an order into a 3-day weekend gap). On a non-trading day the pass
# still refreshes data/scoreboard/snapshot but skips authoring and execution.
# Fail-open on calendar errors (a dead calendar API must not halt the desk on
  # a real trading day — the executor has its own fail-closed clock gate).
if [[ "$SCENARIO" -eq 1 ]]; then
  TRADING_DAY="${AUTOTRADE_SCENARIO_TRADING_DAY:-1}"
else
  TRADING_DAY=$(timeout 20 python3 -c "
import sys
sys.path.insert(0, 'workspaces/trading-intel/scripts')
from datetime import datetime
from zoneinfo import ZoneInfo
try:
    from connectors.marketdata import is_trading_day
    today = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    print('1' if is_trading_day(today) else '0')
except Exception:
    print('1')" 2>/dev/null)
fi
[[ "$TRADING_DAY" == "0" ]] || TRADING_DAY=1
printf ',\n  "market_today": {"trading_day": %s}' "$([[ "$TRADING_DAY" == "1" ]] && echo true || echo false)"

run_step "classify_regime" 90 python3 workspaces/quant/scripts/classify_regime.py
run_step "value_universe" 180 python3 workspaces/trading-intel/scripts/valuation.py universe
run_step "sync_symbol_aliases" 20 python3 workspaces/trading-intel/scripts/sync_symbol_aliases.py
# Keep the research board honest before origination: retire terminal "active"
# rows to dormant, reopen any thesis whose position is still live, and collapse
# same-ticker live duplicates without deleting evidence or forecast history.
run_step "hypothesis_hygiene" 30 python3 workspaces/trading-intel/scripts/hypothesis_hygiene.py --repair
# Deterministic world-model ORIGINATION. D68: keep the signal lane inside the
# normal pass so idle cash caused by no qualified ideas is attacked by more
# candidates flowing through the unchanged score -> critic -> trader -> risk
# gates. The script dedupes unresolved names and now persists mechanism evidence
# rows, so downstream evidence/provenance gates remain intact.
run_step "signals_to_hypotheses" 180 python3 workspaces/trading-intel/scripts/signals_to_hypotheses.py --max-new 8 --scan-top-n 600
# D59: valuation-first ORIGINATION — value was compute-only (critic brake +
# predict bands); nothing authored ideas from undervaluation. Max 3/day,
# value-trap screened (requires trend or analyst inflection).
run_step "value_scan" 120 python3 workspaces/trading-intel/scripts/value_scan.py
run_step "score_hypotheses" 60 python3 workspaces/quant/scripts/score_hypotheses.py
run_step "critic_baseline" 30 python3 workspaces/critic/scripts/critic_baseline.py
run_step "predict" 90 python3 workspaces/quant/scripts/predict.py --states ready
run_step "prediction_challenger_record" 30 python3 workspaces/developer/scripts/prediction_challenger.py record
run_step "ml_evidence_track" 30 python3 workspaces/trading-intel/scripts/track_ml_evidence.py
if [[ "$TRADING_DAY" == "1" ]]; then
  # D53/D113: enforce declared stops and quarantine legacy shorts BEFORE
  # authoring new ideas — cut invalid/rule-breaching risk first. (2026-07-07: ORCL sat
  # at -22.6% against a stated -8% stop while the desk kept opening names.)
  run_step "enforce_horizons" 90 python3 workspaces/trader/scripts/enforce_horizons.py
  # D57: falsifier tripwire — exits positions whose thesis tripwire has fired.
  run_step "enforce_falsifiers" 60 python3 workspaces/trader/scripts/enforce_falsifiers.py
  run_step "enforce_stops" 90 python3 workspaces/trader/scripts/enforce_stops.py
  run_step "author_intents" 60 python3 workspaces/trader/scripts/author_intents.py
  run_step "gate_evaluator" 60 python3 workspaces/trading-intel/scripts/gate_evaluator.py --all-proposed
  run_step "risk_gate" 90 python3 workspaces/risk/scripts/gate_risk_intents.py --all-pending
  # Verify the owned ledger and canonical-vs-simulator position/order lineage
  # BEFORE any submission. Post-fill reconciliation still runs below. A broken
  # preflight, or any critical upstream decision-stage failure, disarms the
  # executor for this pass while allowing diagnostics/snapshots to finish.
  run_step "sim_integrity_pre" 30 python3 workspaces/executor/scripts/sim_broker.py integrity --book desk
  run_step "reconcile_preflight" 30 python3 workspaces/executor/scripts/reconcile.py --dry-run
else
  printf ',\n  "enforce_horizons": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "enforce_falsifiers": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "enforce_stops": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "author_intents": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "gate_evaluator": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "risk_gate": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "sim_integrity_pre": {"rc": 0, "skipped": "non-trading day"}'
  printf ',\n  "reconcile_preflight": {"rc": 0, "skipped": "non-trading day"}'
fi
if [[ "$SKIP_EXECUTE" -eq 0 && "$TRADING_DAY" == "1" && "${#EXECUTION_BLOCKERS[@]}" -eq 0 ]]; then
  run_step "execute_intent" 60 python3 workspaces/executor/scripts/execute_intent.py
else
  printf ',\n  "execute_intent": {"rc": 0, "skipped": true, "reason": %s, "blockers": %s}' \
    "$(if [[ "${#EXECUTION_BLOCKERS[@]}" -gt 0 ]]; then printf '%s' 'critical pre-execution dependency failed'; elif [[ "$TRADING_DAY" != "1" ]]; then printf '%s' 'non-trading day'; else printf '%s' 'requested'; fi | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
    "$(printf '%s\n' "${EXECUTION_BLOCKERS[@]}" | python3 -c 'import json,sys; print(json.dumps([x.strip() for x in sys.stdin if x.strip()]))')"
fi
# sync_fills BEFORE reconcile: pulls broker truth per order id (fill price,
# filled_at) and books positions against the real hypothesis lineage. Without
# it, non-instant fills rot as pending_new until reconcile marks them
# closed_unknown (price lost) and re-creates the position as a POS-SYNC
# placeholder with a fabricated hypothesis (2026-07-06 finding).
run_step "sync_fills" 60 python3 workspaces/executor/scripts/sync_fills.py
# --repair since D53: exits close in the sim ledger instantly but the strategy
# positions row lags; the guarded repairs (proceeds test refuses mass-vanish
# glitches) close them safely. Without this, every stop exit strands a row.
run_step "reconcile" 30 python3 workspaces/executor/scripts/reconcile.py --repair
# D52: mark the internal desk book each pass so book_equity (the equity curve
# the app serves) stays fresh intraday — daily-keyed, so this upserts today.
run_step "sim_mark" 60 python3 workspaces/executor/scripts/sim_broker.py mark --book desk
run_step "compute_attribution" 60 python3 workspaces/developer/scripts/compute_attribution.py
run_step "portfolio_risk" 120 python3 workspaces/trading-intel/scripts/risk_model.py snapshot
run_step "scoreboard" 60 python3 workspaces/trading-intel/scripts/benchmark_scoreboard.py --backfill
# --- learning loop (D56) --------------------------------------------------
# These stages existed but were NEVER wired into the pass: 174 predictions had
# never been graded, 5/6 resolved hypotheses had no postmortem, and 0 patterns
# were ever extracted. Order matters:
#   grade_outcomes    — grade matured predictions from realized prices (vs SPY)
#   calibrate         — fold graded outcomes into mechanism Beta posteriors
#                       (--no-propose: per-pass data accumulation is autonomous;
#                       gated rule_proposals stay on the daily learning pass)
#   write_postmortems — every resolved hypothesis gets a structured postmortem
#   extract_patterns  — promote recurring postmortem themes to patterns
#   exit_quality      — measure post-exit rebounds so "sold too early" is a
#                       tracked number per exit lane, not an anecdote
run_step "grade_outcomes" 120 python3 workspaces/archivist/scripts/grade_outcomes.py
run_step "calibrate" 90 python3 workspaces/archivist/scripts/calibrate.py --no-propose
run_step "write_postmortems" 30 python3 workspaces/archivist/scripts/write_postmortems.py
run_step "extract_patterns" 30 python3 workspaces/archivist/scripts/extract_patterns.py
run_step "exit_quality" 90 python3 workspaces/trading-intel/scripts/exit_quality_audit.py
# ---------------------------------------------------------------------------
# Macro layer: keep the forward calendar populated and detect realized surprises
# (both idempotent + cheap; pull-actuals writes a market_event on a big surprise).
run_step "macro_seed" 30 python3 workspaces/trading-intel/scripts/macro_calendar.py seed --months 3
run_step "macro_actuals" 45 python3 workspaces/trading-intel/scripts/macro_calendar.py pull-actuals
# Basket rotation / seesaw observable (D64): corr + spread z per axis, so
# intra-theme decoupling is a measured regime the agents reason over — not a
# surprise the world model mislearns as "growth mechanisms missed".
run_step "rotation" 120 python3 workspaces/trading-intel/scripts/rotation_monitor.py snapshot
run_step "capital_efficiency" 45 python3 workspaces/trading-intel/scripts/capital_efficiency_audit.py
if [[ "$SKIP_SNAPSHOT" -eq 0 ]]; then
  if [[ "$SCENARIO" -eq 0 ]]; then
    mkdir -p "$SNAPSHOT_DIR"
  fi
  run_step "snapshot" 60 python3 workspaces/developer/scripts/snapshot_builder.py --out "$APP_DATA_JSON"
  # Runtime jobs write only ignored state. Cron must never mutate the tracked
  # website checkout: that recreated a dirty repo every ten minutes, blocked
  # launches, and let stale generated files hitchhike in code commits.
  if command -v node >/dev/null 2>&1 && [[ -f "$LIDI_REPO/scripts/snapshot-trader-intel.mjs" ]]; then
    run_step "snapshot_overlay" 60 env \
      TRADER_INTEL_OUT_DIR="$SNAPSHOT_DIR" \
      TRADER_INTEL_SKIP_DIST=1 \
      node "$LIDI_REPO/scripts/snapshot-trader-intel.mjs"
  else
    PIPELINE_RC=1
    PIPELINE_FAILURES+=("snapshot_overlay:missing")
    printf ',\n  "snapshot_overlay": {"rc": 2, "out": "node or snapshot overlay missing"}'
  fi
  printf ',\n  "snapshot_path": %s' "$(printf '%s' "$APP_DATA_JSON" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read()))')"
fi
run_step "pipeline_health" 30 python3 workspaces/developer/scripts/audit_pipeline_health.py
run_step "app_snapshot" 20 python3 workspaces/developer/scripts/audit_app_snapshot.py --path "$APP_DATA_JSON"

if [[ "$PUBLISH" -eq 1 && "$SKIP_SNAPSHOT" -eq 0 ]]; then
  # Data-only publish (KV put via /api/trader-data, seconds) — since 2026-07-02 the
  # app reads data from KV, so passes no longer need a full vite build + Pages
  # deploy. Site code changes ship via deploy-lidi-solutions.sh (manual / on merge).
  run_step "publish" 120 bash "$OPENCLAW/scripts/push-trader-data.sh"
fi

printf ',\n  "pipeline_result": {"rc": %d, "failures": %s}' \
  "$PIPELINE_RC" "$(printf '%s\n' "${PIPELINE_FAILURES[@]}" | python3 -c 'import json,sys; print(json.dumps([x.strip() for x in sys.stdin if x.strip()]))')"
printf ',\n  "finished_at": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit "$PIPELINE_RC"
