#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

exec python3 /home/aaron/.openclaw/workspaces/dwight/scripts/poll_priority_queue.py "$@"
