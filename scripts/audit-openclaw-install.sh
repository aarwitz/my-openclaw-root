#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

NEW_BIN="/home/aaron/.nvm/versions/node/v22.22.1/bin/openclaw"
OLD_BIN="/usr/bin/openclaw"
OLD_LIB="/usr/lib/node_modules/openclaw"
UNIT="openclaw-gateway.service"

echo "[audit] Binary resolution"
which -a openclaw || true

echo "[audit] Versions"
"$NEW_BIN" --version || true
if [[ -x "$OLD_BIN" ]]; then
  "$OLD_BIN" --version || true
else
  echo "old binary not present: $OLD_BIN"
fi

echo "[audit] Service ExecStart"
SYSTEMD_PAGER=cat systemctl --user show -P ExecStart "$UNIT" || true

echo "[audit] Service status summary"
"$NEW_BIN" gateway status | sed -n '1,80p' || true

echo "[audit] Dual-install check"
if [[ -e "$OLD_LIB" ]]; then
  echo "WARN old system install still present at $OLD_LIB"
else
  echo "OK old system install not found"
fi
