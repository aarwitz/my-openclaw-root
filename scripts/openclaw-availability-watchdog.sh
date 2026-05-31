#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

OPENCLAW_BIN="/home/aaron/.nvm/versions/node/v22.22.1/bin/openclaw"
SAFE_RESTART="/home/aaron/.openclaw/scripts/safe-restart.sh"
RUN_WITH_TRACE="/home/aaron/.openclaw/scripts/run-with-trace.sh"
LOG_FILE="/home/aaron/.openclaw/logs/availability-watchdog.log"
LOCK_FILE="/tmp/openclaw-availability-watchdog.lock"
STATE_FILE="/tmp/openclaw-availability-watchdog.state"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-1800}"
COMPOSE_FILE="/home/aaron/.openclaw/docker-compose.openclaw.yml"
DOCKER_CONTAINER="openclaw-gateway"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s [watchdog] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" | tee -a "$LOG_FILE"
}

if [[ ! -x "$OPENCLAW_BIN" ]]; then
  log "openclaw binary missing at $OPENCLAW_BIN"
  exit 1
fi

if [[ ! -x "$SAFE_RESTART" ]]; then
  log "safe-restart script missing at $SAFE_RESTART"
  exit 1
fi

if [[ ! -x "$RUN_WITH_TRACE" ]]; then
  log "run-with-trace wrapper missing at $RUN_WITH_TRACE"
  exit 1
fi

is_docker_runtime=false
if [[ -f "$COMPOSE_FILE" ]] && docker ps -a --format '{{.Names}}' | grep -Fxq "$DOCKER_CONTAINER"; then
  is_docker_runtime=true
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another watchdog run is active; skipping"
  exit 0
fi

gateway_ok=false
telegram_ok=false

if [[ "$is_docker_runtime" == true ]]; then
  status="$(docker inspect --format '{{.State.Status}}' "$DOCKER_CONTAINER" 2>/dev/null || true)"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$DOCKER_CONTAINER" 2>/dev/null || true)"
  if [[ "$status" == "running" ]] && [[ "$health" == "healthy" || "$health" == "none" ]]; then
    gateway_ok=true
  fi

  LOG_TAIL="$(docker logs --tail=2000 "$DOCKER_CONTAINER" 2>&1 || true)"
  lane_count=0
  for lane in druck dwight jerry resi; do
    if printf '%s\n' "$LOG_TAIL" | rg -q "\[telegram\] \[${lane}\] starting provider|ingress-spool-${lane}"; then
      lane_count=$((lane_count + 1))
    fi
  done
  if [[ "$lane_count" -ge 4 ]]; then
    telegram_ok=true
  fi
else
  GW_OUT="$($OPENCLAW_BIN gateway status 2>&1 || true)"
  if printf '%s\n' "$GW_OUT" | rg -q "Runtime: running" && printf '%s\n' "$GW_OUT" | rg -q "Connectivity probe: ok"; then
    gateway_ok=true
  fi

  CH_OUT="$($OPENCLAW_BIN channels status --probe 2>&1 || true)"
  WORKS_COUNT="$(printf '%s\n' "$CH_OUT" | rg -c "^- Telegram .*: .* works" || true)"
  if [[ "${WORKS_COUNT}" -ge 4 ]]; then
    telegram_ok=true
  fi
fi

if [[ "$gateway_ok" == true && "$telegram_ok" == true ]]; then
  log "healthy: gateway and telegram probes are OK"
  exit 0
fi

now_epoch="$(date +%s)"
last_epoch=0
if [[ -f "$STATE_FILE" ]]; then
  last_epoch="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
fi

since_last=$(( now_epoch - last_epoch ))
if (( since_last < COOLDOWN_SECONDS )); then
  log "unhealthy but in cooldown (${since_last}s < ${COOLDOWN_SECONDS}s); skipping restart"
  exit 0
fi

log "unhealthy detected (gateway_ok=${gateway_ok}, telegram_ok=${telegram_ok}, mode=$([[ "$is_docker_runtime" == true ]] && echo docker || echo systemd)); running safe restart"
printf '%s' "$now_epoch" > "$STATE_FILE"

if "$RUN_WITH_TRACE" --tag watchdog "$SAFE_RESTART" --force --reason watchdog >>"$LOG_FILE" 2>&1; then
  log "safe restart completed"
else
  log "safe restart failed; manual intervention required"
  exit 1
fi

POST_OUT="$($OPENCLAW_BIN gateway status 2>&1 || true)"
if printf '%s\n' "$POST_OUT" | rg -q "Runtime: running"; then
  log "post-restart gateway runtime is running"
else
  log "post-restart gateway runtime still unhealthy"
  exit 1
fi

exit 0
