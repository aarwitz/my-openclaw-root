#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# Deterministic market-wide discovery lane. This runs frequently during the
# session, independently of LLM availability, and writes an atomic research
# brief. It never creates hypotheses, intents, orders, or mechanism approval.

OC="${HOME}/.openclaw"
PY="/usr/bin/python3"
INTAKE="${OC}/workspaces/trading-intel/scripts/market_event_intake.py"
LOG="${OC}/logs/market-event-intake.log"
BRIEF="${OC}/state/market-event-brief.json"
TMP="${BRIEF}.tmp"

mkdir -p "$(dirname "${LOG}")" "$(dirname "${BRIEF}")"
exec 9>"${OC}/state/market-event-intake.lock"
if ! flock -n 9; then
  printf '%s skip: previous intake still running\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" >>"${LOG}"
  exit 0
fi

printf '%s start\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" >>"${LOG}"
"${PY}" "${INTAKE}" collect >>"${LOG}" 2>&1
collect_rc=$?
if "${PY}" "${INTAKE}" brief --hours 168 --limit 60 >"${TMP}" 2>>"${LOG}"; then
  mv "${TMP}" "${BRIEF}"
else
  brief_rc=$?
  rm -f "${TMP}"
  printf '%s failed: brief rc=%s collect_rc=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "${brief_rc}" "${collect_rc}" >>"${LOG}"
  exit "${brief_rc}"
fi
printf '%s end collect_rc=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "${collect_rc}" >>"${LOG}"
exit "${collect_rc}"
