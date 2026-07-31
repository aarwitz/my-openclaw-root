#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# sweep-and-page.sh — deterministic repair + escalation for the health sweep.
# crit -> page the operator via the direct Bot API (page-operator.sh works with
# the gateway down); warn -> silent telegram. No LLM in this path.

OC="$HOME/.openclaw"

# Overnight vendor bars can make forecasts mature after the post-close learning
# chain. Close that expected availability gap before diagnosing the loop. A
# grader/calibrator/lock failure is appended to the health report as its own
# CRIT so a partially repaired loop can never look green.
PREFLIGHT_OUT=$(bash "$OC/scripts/close-matured-predictions.sh" 2>&1)
PREFLIGHT_RC=$?
OUT=$(python3 "$OC/scripts/system-health-sweep.py" 2>/dev/null)
RC=$?
if [[ $PREFLIGHT_RC -ne 0 ]]; then
  PREFLIGHT_DETAIL="${PREFLIGHT_OUT:0:360}"
  OUT=$(printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
detail = f"pre-sweep prediction closure failed (rc={sys.argv[1]}): {sys.argv[2]}"
row = {"check": "learning_loop_closure", "severity": "crit", "detail": detail}
d.setdefault("findings", []).append(row)
d.setdefault("escalate", []).append(row)
d["overall"] = "crit"
counts = {s: sum(1 for f in d["findings"] if f.get("severity") == s)
          for s in ("ok", "warn", "crit")}
d["counts"] = counts
d["summary"] = f"{counts['"'"'ok'"'"']} ok / {counts['"'"'warn'"'"']} warn / {counts['"'"'crit'"'"']} crit"
print(json.dumps(d))
' "$PREFLIGHT_RC" "$PREFLIGHT_DETAIL")
  RC=2
fi
BAD=$(echo "$OUT" | python3 -c "
import json, re, sys
d = json.load(sys.stdin)
rows = []
for f in d.get('escalate', [])[:6]:
    detail = re.sub(r'\\s+', ' ', str(f.get('detail') or '')).strip()
    if len(detail) > 240:
        detail = detail[:237].rsplit(' ', 1)[0] + '…'
    rows.append(f\"• {f.get('check')}: {detail}\")
print('\\n'.join(rows))" 2>/dev/null)
FINGERPRINT=$(echo "$OUT" | python3 -c "
import hashlib, json, sys
d=json.load(sys.stdin)
stable=[(f.get('check'),f.get('severity'),f.get('detail')) for f in d.get('escalate',[])]
print(hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest())" 2>/dev/null)
ALERT_STATE="$OC/state/health-sweep-last-alert.json"
LAST_FP="$(jq -r '.fingerprint // empty' "$ALERT_STATE" 2>/dev/null || true)"
LAST_TS="$(jq -r '.sent_at_epoch // 0' "$ALERT_STATE" 2>/dev/null || echo 0)"
NOW_TS="$(date +%s)"

# Identical findings are one incident, not a fresh page every six hours.
# Re-notify unchanged incidents after 24h so they cannot disappear forever.
if [[ -n "$FINGERPRINT" && "$FINGERPRINT" == "$LAST_FP" ]] \
   && [[ $((NOW_TS - LAST_TS)) -lt 86400 ]]; then
  exit 0
fi

SENT=0
if [[ $RC -ge 2 ]]; then
  if bash "$OC/scripts/page-operator.sh" "sweep-crit" "🚨 HEALTH SWEEP CRIT
$BAD"; then
    SENT=1
  fi
elif [[ $RC -eq 1 ]]; then
  OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"
  if "$OPENCLAW_BIN" message send --channel telegram --account druck -t 6043080629 \
    -m "⚠️ HEALTH SWEEP WARN
$BAD" --silent >/dev/null 2>&1; then
    SENT=1
  fi
else
  exit 0
fi

if [[ "$SENT" -ne 1 ]]; then
  echo "health alert delivery failed; alert fingerprint not acknowledged" >&2
  exit 1
fi

python3 -c "
import json,sys
json.dump({'fingerprint':sys.argv[1],'sent_at_epoch':int(sys.argv[2])},open(sys.argv[3],'w'))
" "$FINGERPRINT" "$NOW_TS" "$ALERT_STATE"
exit 0
