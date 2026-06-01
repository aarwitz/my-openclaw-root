#!/usr/bin/env bash
# Shared queue lock for EWAG iOS mac-node operations.
# Guarantees one conflicting operation at a time across build/test/capture/sim.

set -euo pipefail

EWAG_NODE_QUEUE_STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw/state}/ewag-node-queue"
EWAG_NODE_QUEUE_LOCK_FILE="${EWAG_NODE_QUEUE_STATE_DIR}/ios-build-node.lock"
EWAG_NODE_QUEUE_ACTIVE_FILE="${EWAG_NODE_QUEUE_STATE_DIR}/active.json"
EWAG_NODE_QUEUE_LOG_FILE="${HOME}/.openclaw/logs/ewag-node-queue.log"
EWAG_NODE_QUEUE_TIMEOUT_SEC="${EWAG_NODE_QUEUE_TIMEOUT_SEC:-3600}"

__ewag_lock_fd="213"
__ewag_lock_acquired="0"

_ewag_node_queue_now_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

_ewag_node_queue_log() {
  mkdir -p "$(dirname "$EWAG_NODE_QUEUE_LOG_FILE")"
  printf '%s [ewag-node-queue] %s\n' "$(_ewag_node_queue_now_utc)" "$*" | tee -a "$EWAG_NODE_QUEUE_LOG_FILE" >/dev/null
}

_ewag_node_queue_write_active() {
  local operation="$1"
  local script_path="$2"
  cat > "$EWAG_NODE_QUEUE_ACTIVE_FILE" <<EOF
{
  "pid": $$,
  "operation": "${operation}",
  "script": "${script_path}",
  "started_at": "$(_ewag_node_queue_now_utc)"
}
EOF
}

_ewag_node_queue_pid_alive() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

_ewag_node_queue_cleanup_stale_active() {
  if [[ ! -f "$EWAG_NODE_QUEUE_ACTIVE_FILE" ]]; then
    return 0
  fi

  local active_pid
  active_pid="$(jq -r '.pid // empty' "$EWAG_NODE_QUEUE_ACTIVE_FILE" 2>/dev/null || true)"
  if [[ -z "$active_pid" ]]; then
    rm -f "$EWAG_NODE_QUEUE_ACTIVE_FILE"
    _ewag_node_queue_log "removed malformed active state file"
    return 0
  fi

  if ! _ewag_node_queue_pid_alive "$active_pid"; then
    rm -f "$EWAG_NODE_QUEUE_ACTIVE_FILE"
    _ewag_node_queue_log "removed stale active state for dead pid=${active_pid}"
  fi
}

release_ewag_node_lock() {
  if [[ "$__ewag_lock_acquired" != "1" ]]; then
    return 0
  fi

  if [[ -f "$EWAG_NODE_QUEUE_ACTIVE_FILE" ]]; then
    local active_pid
    active_pid="$(jq -r '.pid // empty' "$EWAG_NODE_QUEUE_ACTIVE_FILE" 2>/dev/null || true)"
    if [[ "$active_pid" == "$$" ]]; then
      rm -f "$EWAG_NODE_QUEUE_ACTIVE_FILE"
    fi
  fi

  flock -u "$__ewag_lock_fd" || true
  __ewag_lock_acquired="0"
  _ewag_node_queue_log "released lock pid=$$"
}

acquire_ewag_node_lock() {
  local operation="${1:-unspecified}"
  local script_path="${2:-${BASH_SOURCE[1]:-unknown}}"

  if [[ "$__ewag_lock_acquired" == "1" ]]; then
    return 0
  fi

  if [[ ! "$EWAG_NODE_QUEUE_TIMEOUT_SEC" =~ ^[0-9]+$ ]]; then
    echo "Invalid EWAG_NODE_QUEUE_TIMEOUT_SEC: $EWAG_NODE_QUEUE_TIMEOUT_SEC" >&2
    exit 2
  fi

  mkdir -p "$EWAG_NODE_QUEUE_STATE_DIR" "$(dirname "$EWAG_NODE_QUEUE_LOG_FILE")"
  _ewag_node_queue_cleanup_stale_active

  # shellcheck disable=SC3045
  exec {__ewag_lock_fd}>"$EWAG_NODE_QUEUE_LOCK_FILE"

  if flock -n "$__ewag_lock_fd"; then
    _ewag_node_queue_log "acquired immediately pid=$$ op=$operation script=$script_path"
  else
    _ewag_node_queue_log "queued pid=$$ op=$operation script=$script_path timeout=${EWAG_NODE_QUEUE_TIMEOUT_SEC}s"
    echo "EWAG node is busy. Queued and waiting for lock (timeout ${EWAG_NODE_QUEUE_TIMEOUT_SEC}s)..."
    if ! flock -w "$EWAG_NODE_QUEUE_TIMEOUT_SEC" "$__ewag_lock_fd"; then
      _ewag_node_queue_log "timeout waiting pid=$$ op=$operation"
      echo "Timed out waiting for EWAG node lock after ${EWAG_NODE_QUEUE_TIMEOUT_SEC}s." >&2
      exit 75
    fi
    _ewag_node_queue_log "acquired after wait pid=$$ op=$operation script=$script_path"
  fi

  __ewag_lock_acquired="1"
  _ewag_node_queue_write_active "$operation" "$script_path"
  trap release_ewag_node_lock EXIT
}
