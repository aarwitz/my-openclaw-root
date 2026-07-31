#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# Close any forecasts that became gradeable after the post-close data refresh,
# then fold the new observations into calibration before health is evaluated.
#
# Daily-bar vendors can expose the final bar after the 16:12 ET learning chain.
# The 06:25 ET health sweep is the first deterministic consumer of that newly
# complete window, so it must repair the expected availability race before it
# diagnoses a broken learning loop. Any repair failure remains page-worthy in
# sweep-and-page.sh.

OPENCLAW_DIR="${OPENCLAW:-/home/aaron/.openclaw}"
DB_PATH="$OPENCLAW_DIR/state/trading-intel.sqlite"
LOG_PATH="$OPENCLAW_DIR/logs/learning-loop-closure.log"
GRADER="$OPENCLAW_DIR/workspaces/archivist/scripts/grade_outcomes.py"
CALIBRATOR="$OPENCLAW_DIR/workspaces/archivist/scripts/calibrate.py"

if [[ ! -f "$DB_PATH" ]]; then
  echo "learning-loop closure refused: database missing at $DB_PATH" >&2
  exit 66
fi

# This writes the prediction, observation, mechanism, and audit ledgers. Share
# the exact lock used by trader, guard, signal, publisher, and learning jobs.
exec 9>"$OPENCLAW_DIR/state/trading-money-path.lock"
if ! flock -w 90 9; then
  echo "learning-loop closure failed: money-path lock unavailable after 90 seconds" >&2
  exit 75
fi
export TRADING_MONEY_LOCK_HELD=1

mkdir -p "$(dirname "$LOG_PATH")"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s close-matured-predictions start\n' "$STARTED_AT" >>"$LOG_PATH"

timeout 120 python3 "$GRADER" --db "$DB_PATH" >>"$LOG_PATH" 2>&1
RC=$?
if [[ $RC -ne 0 ]]; then
  printf '%s grade_outcomes failed rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RC" >>"$LOG_PATH"
  echo "learning-loop closure failed: grade_outcomes rc=$RC (see $LOG_PATH)" >&2
  exit "$RC"
fi

# Calibration is autonomous evidence accumulation. Structural rule changes
# remain gated and are intentionally not drafted from this health preflight.
timeout 90 python3 "$CALIBRATOR" --no-propose >>"$LOG_PATH" 2>&1
RC=$?
if [[ $RC -ne 0 ]]; then
  printf '%s calibrate failed rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RC" >>"$LOG_PATH"
  echo "learning-loop closure failed: calibrate rc=$RC (see $LOG_PATH)" >&2
  exit "$RC"
fi

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s close-matured-predictions ok\n' "$FINISHED_AT" >>"$LOG_PATH"
printf '{"ok":true,"started_at":"%s","finished_at":"%s"}\n' \
  "$STARTED_AT" "$FINISHED_AT"
