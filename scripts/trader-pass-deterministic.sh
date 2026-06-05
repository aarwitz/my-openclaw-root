#!/usr/bin/env bash
# trader-pass-deterministic.sh
#
# Deterministic prefix for every trader cron pass. Runs:
#   1. classify_regime (writes new regime row)
#   2. score_hypotheses (writes quant_score on raw hypotheses)
#   3. gate_evaluator on every proposed/critic_review intent
#   4. execute_intent for every approved intent (LIVE — paper account only)
#   5. reconcile (alpaca vs DB)
#   6. snapshot writer (refreshes lidisolutions.ai data.json)
#   7. audit_pipeline_health + audit_app_snapshot (Bessent watchdogs)
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

run_step "classify_regime" 90 python3 workspaces/quant/scripts/classify_regime.py
run_step "score_hypotheses" 60 python3 workspaces/quant/scripts/score_hypotheses.py
run_step "critic_baseline" 30 python3 workspaces/critic/scripts/critic_baseline.py
run_step "author_intents" 60 python3 workspaces/trader/scripts/author_intents.py
run_step "gate_evaluator" 60 python3 workspaces/trading-intel/scripts/gate_evaluator.py --all-proposed
if [[ "$SKIP_EXECUTE" -eq 0 ]]; then
  run_step "execute_intent" 60 python3 workspaces/executor/scripts/execute_intent.py
else
  printf ',\n  "execute_intent": {"rc": 0, "skipped": true}'
fi
run_step "reconcile" 30 python3 workspaces/executor/scripts/reconcile.py
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
  run_step "publish" 240 bash "$OPENCLAW/scripts/publish-trader-intel.sh"
fi

printf ',\n  "finished_at": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
