#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# openclaw-availability-watchdog.sh — 5-minute host-cron guard for the gateway.
#
# Detects AND PAGES (via page-operator.sh, direct Bot API — works when the
# gateway is down) the failure modes from the 2026-07-02 incident:
#   1. rogue listener on 18789 that is not docker-proxy (e.g. a resurrected
#      host systemd gateway) — auto-stops the known systemd unit, pages
#   2. container crash-loop / unhealthy — safe-restarts (cooldown), pages
#   3. host CLI cannot reach the gateway, or the live cron scheduler is empty
#      while cron/jobs.json has enabled jobs (bind regression / lost scheduler)
#   4. app snapshot (data.json) stale during market hours — pages only
#
# Restart trigger policy is unchanged: the ONLY condition that justifies a
# gateway restart is the gateway itself being down. Everything else pages.

OPENCLAW_BIN="/home/aaron/.nvm/versions/node/v22.22.1/bin/openclaw"
SAFE_RESTART="/home/aaron/.openclaw/scripts/safe-restart.sh"
RUN_WITH_TRACE="/home/aaron/.openclaw/scripts/run-with-trace.sh"
PAGER="/home/aaron/.openclaw/scripts/page-operator.sh"
LOG_FILE="/home/aaron/.openclaw/logs/availability-watchdog.log"
LOCK_FILE="/tmp/openclaw-availability-watchdog.lock"
STATE_FILE="/tmp/openclaw-availability-watchdog.state"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-1800}"
COMPOSE_FILE="/home/aaron/.openclaw/docker-compose.openclaw.yml"
DOCKER_CONTAINER="openclaw-gateway"
JOBS_JSON="/home/aaron/.openclaw/cron/jobs.json"
SNAPSHOT_JSON="/home/aaron/repos/lidi-solutions/public/solutions/trader_intel/app/data.json"
ROGUE_UNIT="openclaw-gateway.service"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s [watchdog] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" | tee -a "$LOG_FILE"
}

page() { # page <alert-key> <message...>
  local key="$1"; shift
  "$RUN_WITH_TRACE" --tag watchdog "$PAGER" "$key" "$@" >>"$LOG_FILE" 2>&1 \
    || log "WARN: paging failed for alert=$key"
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

# ---------------------------------------------------------------------------
# CHECK 1 — rogue gateway process. The canonical gateway lives in docker; any
# non-docker-proxy process holding 127.0.0.1:18789 is an impostor that will
# steal the port from the container and share the single-use OAuth store.
# (Incident 2026-07-02: legacy systemd user unit resurrected a host gateway.)
# ---------------------------------------------------------------------------
if [[ "$is_docker_runtime" == true ]]; then
  rogue_pid="$(ss -tlnp 2>/dev/null | awk '/127\.0\.0\.1:18789/ && /users:/ {
      match($0, /pid=[0-9]+/); print substr($0, RSTART+4, RLENGTH-4); exit }')"
  if [[ -n "${rogue_pid:-}" ]]; then
    rogue_cmd="$(ps -o comm= -p "$rogue_pid" 2>/dev/null || echo unknown)"
    if [[ "$rogue_cmd" != "docker-proxy" ]]; then
      log "CRITICAL: rogue process on 18789 (pid=$rogue_pid cmd=$rogue_cmd); stopping $ROGUE_UNIT"
      systemctl --user stop "$ROGUE_UNIT" 2>/dev/null || true
      systemctl --user disable "$ROGUE_UNIT" 2>/dev/null || true
      # If the listener survives (not the known unit), kill it by pid — the
      # canonical container cannot bind while it lives.
      if kill -0 "$rogue_pid" 2>/dev/null && [[ "$(ps -o comm= -p "$rogue_pid" 2>/dev/null)" == "$rogue_cmd" ]]; then
        kill "$rogue_pid" 2>/dev/null || true
      fi
      page gateway-rogue-listener "Rogue gateway on 18789 (pid=$rogue_pid cmd=$rogue_cmd). Stopped/disabled $ROGUE_UNIT and killed the listener. Container gateway may need a restart if it lost its port binding — watchdog will handle it next cycle."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# CHECK 2 — container liveness (the original check, now with paging).
# ---------------------------------------------------------------------------
gateway_ok=false
telegram_ok=false

if [[ "$is_docker_runtime" == true ]]; then
  status="$(docker inspect --format '{{.State.Status}}' "$DOCKER_CONTAINER" 2>/dev/null || true)"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$DOCKER_CONTAINER" 2>/dev/null || true)"
  if [[ "$status" == "running" ]] && [[ "$health" == "healthy" || "$health" == "none" ]]; then
    gateway_ok=true
  fi

  # Telegram liveness: probe live channel status inside the container. Never
  # forces a restart on its own (single-use OAuth tokens make restarts fragile).
  CH_OUT="$(docker exec "$DOCKER_CONTAINER" openclaw channels status --probe 2>&1 || true)"
  WORKS_COUNT="$(printf '%s\n' "$CH_OUT" | rg -c "works" || true)"
  if [[ "${WORKS_COUNT:-0}" -ge 1 ]]; then
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

# ---------------------------------------------------------------------------
# CHECK 3 — host-reachable gateway + armed cron scheduler. A "healthy"
# container is not enough: bind regressions (gateway.bind != lan) or a lost
# scheduler leave the desk dead while docker reports healthy. Runs only when
# the container looks healthy (otherwise check 2 handles it).
# ---------------------------------------------------------------------------
if [[ "$gateway_ok" == true && "$is_docker_runtime" == true ]]; then
  enabled_jobs="$(python3 -c "
import json
try:
    jobs = json.load(open('$JOBS_JSON')).get('jobs', [])
    print(sum(1 for j in jobs if j.get('enabled')))
except Exception:
    print(-1)
" 2>/dev/null || echo -1)"

  cron_out="$(timeout 60 "$OPENCLAW_BIN" cron list 2>&1 || true)"
  if printf '%s\n' "$cron_out" | rg -q "GatewayTransportError|Could not start the CLI|gateway timeout|gateway closed"; then
    log "CRITICAL: container healthy but host CLI cannot reach the gateway (bind regression?)"
    page gateway-unreachable-from-host "Gateway container is healthy but the host CLI cannot connect to ws://127.0.0.1:18789. Likely gateway.bind regressed to loopback in openclaw.json (must be \"lan\"), or the docker port proxy lost its endpoint. Host cron scripts and Tailscale access are dead until fixed."
  elif [[ "$enabled_jobs" -gt 0 ]] && printf '%s\n' "$cron_out" | rg -q "No cron jobs"; then
    log "CRITICAL: live cron scheduler is EMPTY but jobs.json has $enabled_jobs enabled jobs"
    page cron-scheduler-empty "Gateway is up but the live cron scheduler has NO jobs while cron/jobs.json has $enabled_jobs enabled. Trading passes are NOT running. Recover: while read id; do openclaw cron enable \"\$id\"; done < ~/.openclaw/cron/jobs.json.pre-restart-enabled-ids"
  fi
fi

# ---------------------------------------------------------------------------
# CHECK 4 — stale app snapshot during market hours (weekday 10:00–16:30 ET,
# snapshot older than 3h means passes are silently failing to publish).
# ---------------------------------------------------------------------------
if [[ "$gateway_ok" == true && -f "$SNAPSHOT_JSON" ]]; then
  et_dow="$(TZ=America/New_York date +%u)"   # 1..7
  et_hm="$(TZ=America/New_York date +%H%M)"
  if [[ "$et_dow" -le 5 && "$et_hm" -ge 1000 && "$et_hm" -le 1630 ]]; then
    snap_age=$(( $(date +%s) - $(stat -c %Y "$SNAPSHOT_JSON") ))
    if (( snap_age > 10800 )); then
      log "WARN: app snapshot is $((snap_age/60))m old during market hours"
      page app-snapshot-stale "AutoTrade app snapshot (data.json) is $((snap_age/60)) minutes old during market hours — trading passes are running but not publishing, or not running at all."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Restart + paging decision (gateway-down is the only restart trigger).
# ---------------------------------------------------------------------------
if [[ "$gateway_ok" == true ]]; then
  if [[ "$telegram_ok" == true ]]; then
    log "healthy: gateway and telegram probes are OK"
  else
    log "WARN: gateway OK but telegram probe degraded (telegram_ok=false); NOT restarting (gateway-down is the only restart trigger). Investigate telegram lanes."
  fi
  exit 0
fi

# Gateway is down: page FIRST (before the restart attempt, so the operator
# hears about it even if the restart hangs), then safe-restart under cooldown.
restart_count="$(docker inspect --format '{{.RestartCount}}' "$DOCKER_CONTAINER" 2>/dev/null || echo '?')"
page gateway-down "Gateway is DOWN (container status=${status:-n/a} health=${health:-n/a} restarts=$restart_count). Watchdog is attempting a safe restart. If this repeats, the container is crash-looping — check 'docker logs openclaw-gateway' and the recovery playbook (rogue unit? lost network endpoints?)."

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
  page gateway-restart-failed "safe-restart.sh FAILED after gateway-down. Manual intervention required — the desk is dead until someone looks."
  exit 1
fi

if [[ "$is_docker_runtime" == true ]]; then
  post_status="$(docker inspect --format '{{.State.Status}}' "$DOCKER_CONTAINER" 2>/dev/null || true)"
  if [[ "$post_status" == "running" ]]; then
    log "post-restart gateway container is running"
    PAGE_FORCE=1 "$RUN_WITH_TRACE" --tag watchdog "$PAGER" gateway-recovered \
      "Gateway restarted by watchdog and container is running again." >>"$LOG_FILE" 2>&1 || true
  else
    log "post-restart gateway container still unhealthy (status=$post_status)"
    page gateway-restart-failed "Gateway restarted by watchdog but container is still unhealthy (status=$post_status). Likely crash-looping — manual intervention required."
    exit 1
  fi
else
  POST_OUT="$($OPENCLAW_BIN gateway status 2>&1 || true)"
  if printf '%s\n' "$POST_OUT" | rg -q "Runtime: running"; then
    log "post-restart gateway runtime is running"
  else
    log "post-restart gateway runtime still unhealthy"
    page gateway-restart-failed "Gateway restarted by watchdog but runtime still unhealthy. Manual intervention required."
    exit 1
  fi
fi

exit 0
