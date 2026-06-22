#!/usr/bin/env bash
# Enforce wrapper-only execution for operational scripts.
if [[ "${OPENCLAW_RUN_WITH_TRACE:-}" != "1" ]]; then
  RUNNER="/home/aaron/.openclaw/scripts/run-with-trace.sh"
  CALLER="${BASH_SOURCE[1]:-}"
  if [[ "${OPENCLAW_REQUIRE_WRAPPER_NO_AUTORUN:-0}" != "1" ]] && [[ -n "$CALLER" ]] && [[ -x "$RUNNER" ]]; then
    exec "$RUNNER" --tag auto "$CALLER" "$@"
  fi
  echo "This script must be run via ~/.openclaw/scripts/run-with-trace.sh" >&2
  exit 126
fi
