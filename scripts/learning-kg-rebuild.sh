#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# learning-kg-rebuild.sh — deterministic, ZERO-CODEX daily rebuild of the
# knowledge graph + evidence-association layer + offline consolidation. Replaces the gateway
# agentTurn cron job (which spun an LLM agent just to launch these scripts).
# No LLM, no gateway agent, no Codex. Pings Telegram on failure. Routine
# rebuild success is log-only: record-count growth is coverage, not learning
# quality, and must not be narrated as causal progress.
# Paired host crontab entry: `12 6 * * *`.

OC="$HOME/.openclaw"
KG="$OC/workspaces/trading-intel/scripts"
PY="/usr/bin/python3"                 # has pandas/numpy/sklearn; pinned for cron's minimal env
LOG="$OC/logs/learning-kg-rebuild.log"
FEAT="$OC/state/features.sqlite"
TG_ACCOUNT="druck"; TG_TARGET="6043080629"
OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >>"$LOG"; }
tg()  { # tg <silent|notify> <message...>
  local mode="$1"; shift
  local a=(message send --channel telegram --account "$TG_ACCOUNT" -t "$TG_TARGET" -m "$*")
  [[ "$mode" == "silent" ]] && a+=(--silent)
  "$OPENCLAW_BIN" "${a[@]}" >/dev/null 2>&1 || log "WARN: telegram send failed"
}
counts() {
  "$PY" - "$FEAT" <<'PYEOF'
import sqlite3,sys
q=sqlite3.connect(sys.argv[1]).cursor()
def n(t):
    try: return q.execute("select count(*) from "+t).fetchone()[0]
    except Exception: return -1
print(f"nodes={n('kg_nodes')} edges={n('kg_edges')} causal={n('causal_edges')} entities={n('entities')}")
PYEOF
}
run() { # run <label> <cmd...> — logs and records the first failure
  local label="$1"; shift
  log "-> $label: $*"
  if "$@" >>"$LOG" 2>&1; then log "   ok: $label"; return 0
  else local rc=$?; log "   FAIL($rc): $label"; FAILED="$label"; return "$rc"; fi
}

mkdir -p "$(dirname "$LOG")"
log "===== KG rebuild start (pid $$) ====="
before="$(counts)"; log "before: $before"

FAILED=""
run "kg build"         "$PY" "$KG/knowledge_graph.py" build --top-n 1400 \
 && run "kg news"      "$PY" "$KG/knowledge_graph.py" news  --top-n 1400 \
 && run "evidence build" "$PY" "$KG/causal_graph.py" build --rebuild \
 && run "dream"        "$PY" "$KG/dream.py"
rc=$?

after="$(counts)"; log "after: $after"
log "===== KG rebuild end rc=$rc ====="

if [[ $rc -ne 0 ]]; then
  tg notify "⚠️ Daily KG rebuild FAILED at: ${FAILED:-unknown} (rc=$rc). before[$before] after[$after]. Log: $LOG"
  exit 1
fi
# Retain a local consolidated baseline for diagnostics. Do not Telegram routine
# count changes: more links do not imply better prediction or identified cause.
AFTER_STATE="$HOME/.openclaw/state/kg-last-after.txt"
echo "$after" > "$AFTER_STATE"
log "routine success is silent; graph counts are coverage diagnostics only"
exit 0
