#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

if [[ -n "${OPENCLAW_BIN:-}" && -x "${OPENCLAW_BIN}" ]]; then
  printf '%s\n' "${OPENCLAW_BIN}"
  exit 0
fi

if command -v openclaw >/dev/null 2>&1; then
  command -v openclaw
  exit 0
fi

for candidate in \
  "$HOME/.nvm/versions/node/v22.22.1/bin/openclaw" \
  "$HOME/.local/bin/openclaw" \
  "/usr/local/bin/openclaw" \
  "/usr/bin/openclaw"
do
  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    exit 0
  fi
done

echo "OpenClaw CLI not found. Set OPENCLAW_BIN or install openclaw on PATH." >&2
exit 1
