#!/usr/bin/env bash
# Host wrapper for the Dwight lane bridge — turns TM-issue assignments into
# real lane execution (Developer code work or trading-agent thought passes).
#
# Required policy: source require-wrapper.sh and run via run-with-trace.sh.
set -euo pipefail

source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/dwight-lane-bridge.py" "$@"
