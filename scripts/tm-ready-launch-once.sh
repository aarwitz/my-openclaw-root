#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER="${SCRIPT_DIR}/tm-ready-watcher.sh"

issue_id=""
max_launches="${MAX_LAUNCHES_PER_RUN:-1}"

usage() {
  cat <<'EOF'
Usage:
  tm-ready-launch-once.sh --issue-id <id> [--help]

Purpose:
  Run one controlled execute-mode watcher pass against a single opted-in issue.

Requirements:
  - issue must satisfy watcher readiness contract
  - issue description must contain AUTO_LAUNCH_READY

Behavior:
  - enables execute mode explicitly for this invocation only
  - restricts watcher to one issue id
  - restricts launch count to 1
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-id)
      issue_id="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$issue_id" || ! "$issue_id" =~ ^[0-9]+$ ]]; then
  echo "Valid --issue-id is required." >&2
  exit 2
fi

if [[ ! -x "$WATCHER" ]]; then
  echo "Watcher missing or not executable: $WATCHER" >&2
  exit 2
fi

TM_READY_WATCHER_ALLOW_EXECUTE=true \
MAX_LAUNCHES_PER_RUN=1 \
"$WATCHER" --execute --issue-id "$issue_id"
