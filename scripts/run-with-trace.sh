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

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date +%s)"
CWD="$PWD"
ARGS_JSON="$(python3 - <<'PY' "$@"
import json,sys
print(json.dumps(sys.argv[1:]))
PY
)"

set +e
OPENCLAW_RUN_WITH_TRACE=1 "${RUN_CMD[@]}" "$@"
EXIT_CODE=$?
set -e
END_EPOCH="$(date +%s)"
END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DURATION="$((END_EPOCH - START_EPOCH))"

python3 - <<'PY' "$LOG_FILE" "$RUN_ID" "$START_TS" "$END_TS" "$DURATION" "$EXIT_CODE" "$TAG" "$SCRIPT" "$CWD" "$ARGS_JSON"
import json
import pathlib
import sys

log_file = pathlib.Path(sys.argv[1])
run = {
    "run_id": sys.argv[2],
    "started_at": sys.argv[3],
    "ended_at": sys.argv[4],
    "duration_seconds": int(sys.argv[5]),
    "exit_code": int(sys.argv[6]),
    "tag": sys.argv[7],
    "script": sys.argv[8],
    "cwd": sys.argv[9],
    "args": json.loads(sys.argv[10]),
}
with log_file.open("a", encoding="utf-8") as f:
    f.write(json.dumps(run, separators=(",", ":")) + "\n")
PY

exit "$EXIT_CODE"
