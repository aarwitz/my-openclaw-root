#!/usr/bin/env bash
# Enforce wrapper-only execution for operational scripts.
if [[ "${OPENCLAW_RUN_WITH_TRACE:-}" != "1" ]]; then
  echo "This script must be run via ~/.openclaw/scripts/run-with-trace.sh" >&2
  exit 126
fi
