#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# sweep-and-page.sh — deterministic escalation for the health sweep (D57).
# crit -> page the operator via the direct Bot API (page-operator.sh works with
# the gateway down); warn -> silent telegram. No LLM in this path.

OC="$HOME/.openclaw"
OUT=$(python3 "$OC/scripts/system-health-sweep.py" 2>/dev/null)
RC=$?
BAD=$(echo "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('; '.join(f\"{f['check']}: {f['detail'][:90]}\" for f in d.get('escalate', [])[:4]))" 2>/dev/null)

if [[ $RC -ge 2 ]]; then
  bash "$OC/scripts/page-operator.sh" "sweep-crit" "🚨 HEALTH SWEEP CRIT — $BAD" || true
elif [[ $RC -eq 1 ]]; then
  OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"
  "$OPENCLAW_BIN" message send --channel telegram --account druck -t 6043080629 \
    -m "⚠️ health sweep warn — $BAD" --silent >/dev/null 2>&1 || true
fi
exit 0
