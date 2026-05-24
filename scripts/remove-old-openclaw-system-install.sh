#!/usr/bin/env bash
set -euo pipefail

# Removes old root-owned /usr OpenClaw install after migration to nvm-managed OpenClaw.
# Requires sudo privileges.

NEW_BIN="/home/aaron/.nvm/versions/node/v22.22.1/bin/openclaw"
OLD_BIN="/usr/bin/openclaw"
OLD_BIN2="/bin/openclaw"
OLD_LIB="/usr/lib/node_modules/openclaw"
UNIT="openclaw-gateway.service"

TARGET_UID="${SUDO_UID:-$(id -u)}"
TARGET_USER="${SUDO_USER:-$(id -un)}"

run_user_systemctl_show_execstart() {
  if [[ -n "${SUDO_USER:-}" ]]; then
    sudo -u "$TARGET_USER" env \
      XDG_RUNTIME_DIR="/run/user/$TARGET_UID" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$TARGET_UID/bus" \
      SYSTEMD_PAGER=cat \
      systemctl --user show -P ExecStart "$UNIT"
  else
    SYSTEMD_PAGER=cat systemctl --user show -P ExecStart "$UNIT"
  fi
}

run_as_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [[ ! -x "$NEW_BIN" ]]; then
  echo "New OpenClaw binary missing: $NEW_BIN" >&2
  exit 1
fi

echo "[check] New binary: $($NEW_BIN --version)"
echo "[check] Effective service ExecStart:"
EXECSTART="$(run_user_systemctl_show_execstart 2>/dev/null || true)"
echo "$EXECSTART"

if [[ -z "$EXECSTART" ]]; then
  echo "Could not read user service ExecStart for $UNIT. Abort." >&2
  echo "Tip: Run as your normal user or with sudo from an interactive login shell." >&2
  exit 1
fi

if ! grep -q "/home/aaron/.nvm/versions/node/v22.22.1/lib/node_modules/openclaw/dist/index.js" <<<"$EXECSTART"; then
  echo "Service is not pinned to the new nvm OpenClaw path. Abort." >&2
  exit 1
fi

echo "[check] Current gateway status:"
"$NEW_BIN" gateway status | sed -n '1,40p' || true

echo "[action] Removing old root-owned OpenClaw install from /usr..."
run_as_root rm -f "$OLD_BIN" "$OLD_BIN2"
run_as_root rm -rf "$OLD_LIB"

echo "[verify] Binary resolution after removal:"
which -a openclaw || true

echo "[verify] New binary still works:"
"$NEW_BIN" --version
"$NEW_BIN" gateway status | sed -n '1,60p' || true

echo "Done. Old /usr OpenClaw install removed."
