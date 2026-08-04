#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Idempotently install only the deterministic operator-event delivery block.
# Existing host jobs remain byte-for-byte unchanged outside these markers.

BEGIN="# BEGIN AUTOTRADE OPERATOR EVENT RAIL"
END="# END AUTOTRADE OPERATOR EVENT RAIL"
TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

crontab -l 2>/dev/null | awk -v begin="${BEGIN}" -v end="${END}" '
  $0 == begin {skip=1; next}
  $0 == end {skip=0; next}
  !skip {print}
' >"${TMP}"

printf '%s\n' \
  "${BEGIN}" \
  "# Durable bot failures reach hosted Task Manager within five minutes; no LLM." \
  "*/5 * * * * /home/aaron/.openclaw/scripts/run-with-trace.sh --tag cron /home/aaron/.openclaw/scripts/dwight-pq-rail.sh >> /home/aaron/.openclaw/logs/dwight-pq-rail-cron.log 2>&1" \
  "${END}" >>"${TMP}"

crontab "${TMP}"
echo "installed operator-event delivery cron block"
