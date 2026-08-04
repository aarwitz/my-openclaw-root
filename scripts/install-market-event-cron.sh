#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Idempotently install only this component's host-cron block. Existing host
# jobs are preserved byte-for-byte outside the markers.

BEGIN="# BEGIN AUTOTRADE MARKET EVENT INTAKE"
END="# END AUTOTRADE MARKET EVENT INTAKE"
TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT

crontab -l 2>/dev/null | awk -v begin="${BEGIN}" -v end="${END}" '
  $0 == begin {skip=1; next}
  $0 == end {skip=0; next}
  !skip {print}
' >"${TMP}"

printf '%s\n' \
  "${BEGIN}" \
  "# Market-wide event discovery: 15m premarket/session/early after-hours, hourly otherwise." \
  "*/15 6-20 * * 1-5 /home/aaron/.openclaw/scripts/run-with-trace.sh --tag cron /home/aaron/.openclaw/scripts/market-event-intake.sh >> /home/aaron/.openclaw/logs/market-event-intake-cron.log 2>&1" \
  "7 0-5,21-23 * * 1-5 /home/aaron/.openclaw/scripts/run-with-trace.sh --tag cron /home/aaron/.openclaw/scripts/market-event-intake.sh >> /home/aaron/.openclaw/logs/market-event-intake-cron.log 2>&1" \
  "7 * * * 0,6 /home/aaron/.openclaw/scripts/run-with-trace.sh --tag cron /home/aaron/.openclaw/scripts/market-event-intake.sh >> /home/aaron/.openclaw/logs/market-event-intake-cron.log 2>&1" \
  "${END}" >>"${TMP}"

crontab "${TMP}"
echo "installed market-event intake cron block"
