#!/usr/bin/env bash
set -euo pipefail

# run-with-trace.sh
# Executes a script and records a usage trail to ~/.openclaw/logs/script-runs.jsonl.

usage() {
  cat <<'EOF'
Usage:
  run-with-trace.sh [--tag <name>] -- <script_path> [args...]
  run-with-trace.sh [--tag <name>] <script_path> [args...]

Examples:
  ~/.openclaw/scripts/run-with-trace.sh ~/.openclaw/scripts/safe-restart.sh --reason maintenance
  ~/.openclaw/scripts/run-with-trace.sh --tag cron ~/.openclaw/scripts/tm-ready-watcher.sh
EOF
}

TAG="manual"
if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="${2:-manual}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

SCRIPT="$1"
shift

if [[ "$SCRIPT" != /* ]]; then
  SCRIPT="$PWD/$SCRIPT"
fi

if command -v realpath >/dev/null 2>&1; then
  SCRIPT="$(realpath "$SCRIPT")"
fi

if [[ ! -f "$SCRIPT" ]]; then
  echo "script not found: $SCRIPT" >&2
  exit 2
fi

RUN_CMD=()
case "$SCRIPT" in
  *.sh)
    RUN_CMD=(/usr/bin/env bash "$SCRIPT")
    ;;
  *.py)
    RUN_CMD=(/usr/bin/env python3 "$SCRIPT")
    ;;
  *)
    if [[ -x "$SCRIPT" ]]; then
      RUN_CMD=("$SCRIPT")
    else
      echo "unsupported script type (or not executable): $SCRIPT" >&2
      exit 2
    fi
    ;;
esac

LOG_FILE="$HOME/.openclaw/logs/script-runs.jsonl"
mkdir -p "$(dirname "$LOG_FILE")"

if ! command -v jq >/dev/null 2>&1; then
  echo "run-with-trace requires jq" >&2
  exit 127
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
CWD="$PWD"
ARGS_JSON="$(printf '%s\n' "$@" | jq -R . | jq -s .)"

set +e
OPENCLAW_RUN_WITH_TRACE=1 "${RUN_CMD[@]}" "$@"
EXIT_CODE=$?
set -e
END_EPOCH="$(date +%s)"
END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DURATION="$((END_EPOCH - START_EPOCH))"

jq -cn \
  --arg run_id "$RUN_ID" \
  --arg started_at "$START_TS" \
  --arg ended_at "$END_TS" \
  --argjson duration_seconds "$DURATION" \
  --argjson exit_code "$EXIT_CODE" \
  --arg tag "$TAG" \
  --arg script "$SCRIPT" \
  --arg cwd "$CWD" \
  --argjson args "$ARGS_JSON" \
  '{
    run_id: $run_id,
    started_at: $started_at,
    ended_at: $ended_at,
    duration_seconds: $duration_seconds,
    exit_code: $exit_code,
    tag: $tag,
    script: $script,
    cwd: $cwd,
    args: $args
  }' >> "$LOG_FILE"

exit "$EXIT_CODE"
