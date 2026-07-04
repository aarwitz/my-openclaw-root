#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# dwight-assign-coding-task.sh — the DEFAULT_LAUNCHER dwight-launch-from-issue.py
# expects. Thin pass-through to launch-coding-task.sh (the real coding engine:
# codex-subagent lane, strict JSON result contract). This file was referenced by
# the dispatch path but missing from disk (found 2026-07-03 org audit) — every
# auto-dispatch without an explicit --launcher override was silently broken.
#
# Flags are identical to launch-coding-task.sh; we forward verbatim.

LAUNCHER="$HOME/.openclaw/scripts/launch-coding-task.sh"
if [[ ! -f "$LAUNCHER" ]]; then
  echo "FATAL: launch-coding-task.sh missing at $LAUNCHER" >&2
  exit 2
fi
exec bash "$LAUNCHER" "$@"
