#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Deterministic operator-event delivery rail. Telegram capture writes locally;
# this narrow poller is the only network hop and creates/reconciles hosted Task
# Manager work without invoking an LLM. A lock prevents overlapping polls.

ROOT="/home/aaron/.openclaw"
PY="/usr/bin/python3"
POLLER="${ROOT}/workspaces/dwight/scripts/poll_priority_queue.py"
LOG="${ROOT}/logs/dwight-pq-rail.log"

mkdir -p "${ROOT}/state" "${ROOT}/logs"
exec 9>"${ROOT}/state/dwight-pq-rail.lock"
if ! flock -n 9; then
  printf '%s skip: previous queue poll still running\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" >>"${LOG}"
  exit 0
fi

printf '%s start\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" >>"${LOG}"
set +e
"${PY}" "${POLLER}" --sprint 5 >>"${LOG}" 2>&1
rc=$?
set -e
printf '%s end rc=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "${rc}" >>"${LOG}"
exit "${rc}"
