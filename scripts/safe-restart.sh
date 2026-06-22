#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# safe-restart.sh
# Safely restarts the OpenClaw gateway without corrupting OAuth refresh tokens.
#
# What it does:
#   1. Backs up auth-profiles.json (preserves current refresh tokens)
#   2. Records enabled job-IDs to a manifest, then disables cron jobs (prevents
#      concurrent token consumption during restart). Restore re-applies the
#      manifest onto the current job definitions — robust to job-set drift and
#      never trusts a stale full-file snapshot.
#   3. Stops gateway gracefully (openclaw gateway stop — emits shutdown event, waits for drain)
#   4. Waits for process to fully exit
#   5. Starts gateway
#   6. Validates token health
#   7. If tokens are broken, restores from backup and does one retry
#   8. Re-enables cron jobs
#
# Usage: safe-restart.sh [--force] [--skip-cron]
#
# PREFER hot reload (openclaw config set) for config changes — no restart needed.
# Only use this script when a restart is truly necessary (e.g., binary update).

SCRIPTS_DIR="${HOME}/.openclaw/scripts"
CRON_JOBS="${HOME}/.openclaw/cron/jobs.json"
# Restore is driven by a manifest of enabled job-IDs (re-applied onto the CURRENT
# job definitions), not by swapping whole jobs.json files. This survives job-set
# drift and never trusts a stale full-file snapshot. The full snapshot is kept
# only as a debug/safety artifact.
CRON_ENABLED_MANIFEST="${CRON_JOBS}.pre-restart-enabled-ids"
CRON_SAFETY_BAK="${CRON_JOBS}.pre-restart-bak"
SAFE_RESTART_LOG="${HOME}/.openclaw/logs/safe-restart.log"
OPENCLAW_BIN="${OPENCLAW_BIN:-}"
MAX_WAIT=30  # seconds to wait for gateway to stop
RUN_ID="$(date -u +"%Y%m%dT%H%M%SZ")-$$"
REASON="manual"
ROOT="${HOME}/.openclaw"
COMPOSE_FILE="${ROOT}/docker-compose.openclaw.yml"
DOCKER_SERVICE="openclaw-gateway"
DOCKER_CONTAINER="openclaw-gateway"

force=false
skip_cron=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force=true; shift ;;
    --skip-cron) skip_cron=true; shift ;;
    --reason)
      REASON="${2:-manual}"
      shift 2
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$(dirname "$SAFE_RESTART_LOG")"

log() {
  local msg="[safe-restart] $*"
  echo "$msg"
  printf '%s run=%s reason=%s level=INFO %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$RUN_ID" "$REASON" "$*" >> "$SAFE_RESTART_LOG"
}
warn() {
  local msg="[safe-restart] ⚠️  $*"
  echo "$msg" >&2
  printf '%s run=%s reason=%s level=WARN %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$RUN_ID" "$REASON" "$*" >> "$SAFE_RESTART_LOG"
}
fail() {
  local msg="[safe-restart] 🚨 $*"
  echo "$msg" >&2
  printf '%s run=%s reason=%s level=FAIL %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$RUN_ID" "$REASON" "$*" >> "$SAFE_RESTART_LOG"
  exit 1
}

is_docker_runtime() {
  [[ -f "${COMPOSE_FILE}" ]] && docker ps -a --format '{{.Names}}' | grep -Fxq "${DOCKER_CONTAINER}"
}

# Count enabled jobs in a jobs.json file (0 on missing/parse error).
cron_enabled_count() {
  jq '[.jobs[]? | select(.enabled == true)] | length' "$1" 2>/dev/null || echo 0
}

gateway_healthy() {
  if [[ "${runtime_mode}" == "docker" ]]; then
    local status
    local health
    status="$(docker inspect --format '{{.State.Status}}' "${DOCKER_CONTAINER}" 2>/dev/null || true)"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${DOCKER_CONTAINER}" 2>/dev/null || true)"
    [[ "${status}" == "running" ]] && [[ "${health}" == "healthy" || "${health}" == "none" ]]
  else
    "${OPENCLAW_BIN}" health &>/dev/null
  fi
}

restart_gateway() {
  if [[ "${runtime_mode}" == "docker" ]]; then
    docker compose -f "${COMPOSE_FILE}" stop "${DOCKER_SERVICE}" || true
    docker compose -f "${COMPOSE_FILE}" up -d "${DOCKER_SERVICE}"
  else
    "${OPENCLAW_BIN}" gateway stop 2>&1 || true

    # Wait for the process to actually exit
    local waited=0
    while "${OPENCLAW_BIN}" health &>/dev/null 2>&1 && [[ ${waited} -lt ${MAX_WAIT} ]]; do
      sleep 1
      waited=$((waited + 1))
    done

    if "${OPENCLAW_BIN}" health &>/dev/null 2>&1; then
      warn "Gateway still responding after ${MAX_WAIT}s. Forcing stop..."
      systemctl --user stop openclaw-gateway 2>/dev/null || true
      sleep 2
    fi

    # Non-fatal: under `set -e` a failed start would abort the script mid-flight,
    # skipping token validation and the cron-jobs restore (incident 2026-06-11/12).
    "${OPENCLAW_BIN}" gateway start 2>&1 || warn "gateway start returned non-zero; continuing to validation/restore steps."
  fi
}

wait_for_gateway_healthy() {
  local waited=0
  while [[ ${waited} -lt ${MAX_WAIT} ]]; do
    if gateway_healthy; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

check_models_auth() {
  set +e
  MODELS_CHECK_OUT="$(${OPENCLAW_BIN} models status --check 2>&1)"
  MODELS_CHECK_RC=$?
  set -e
  if [[ ${MODELS_CHECK_RC} -eq 0 ]]; then
    return 0
  fi

  # In some builds, --check returns non-zero for "expiring" while runtime auth is still usable.
  if printf '%s\n' "${MODELS_CHECK_OUT}" | rg -q 'status=usable' \
    && ! printf '%s\n' "${MODELS_CHECK_OUT}" | rg -q 'status=(missing|invalid|unusable)'; then
    warn "models status --check returned ${MODELS_CHECK_RC}, but runtime auth is still usable; treating as pass"
    return 0
  fi
  return 1
}

# --- Pre-flight ---
log "Starting safe gateway restart..."
log "Run context: force=${force} skip_cron=${skip_cron}"

if [[ -z "$OPENCLAW_BIN" ]]; then
  OPENCLAW_BIN="$("$SCRIPTS_DIR/resolve-openclaw-bin.sh")" || fail "openclaw CLI not found via resolver"
fi
log "Resolved OpenClaw CLI: ${OPENCLAW_BIN}"

runtime_mode="systemd"
if is_docker_runtime; then
  runtime_mode="docker"
fi
log "Runtime mode: ${runtime_mode}"

# Check if gateway is actually running
if ! gateway_healthy; then
  if [[ "$force" == "true" ]]; then
    warn "Gateway not healthy (may be down). Proceeding with --force."
  else
    if [[ "${runtime_mode}" == "docker" ]]; then
      warn "Gateway not healthy. Use --force to restart anyway, or run: docker compose -f ${COMPOSE_FILE} up -d ${DOCKER_SERVICE}"
    else
      warn "Gateway not healthy. Use --force to restart anyway, or just run: openclaw gateway start"
    fi
    exit 1
  fi
fi

# --- Step 1: Backup tokens ---
log "Step 1/6: Backing up auth tokens..."
bash "$SCRIPTS_DIR/token-backup.sh" --label "pre-restart"

# --- Step 2: Disable cron jobs (prevent concurrent token use) ---
# Restore re-applies the manifest's enabled job-IDs onto the CURRENT job
# definitions. Idempotent: after a successful restore the manifest is removed,
# so the EXIT trap's later call is a no-op. If a previous run died mid-restart,
# this still does the right thing without trusting a stale full-file snapshot.
restore_cron_jobs() {
  [[ "$skip_cron" == "true" ]] && return 0
  [[ -f "$CRON_ENABLED_MANIFEST" && -f "$CRON_JOBS" ]] || return 0
  local ids_json
  ids_json="$(jq -R -s 'split("\n") | map(select(length > 0))' "$CRON_ENABLED_MANIFEST" 2>/dev/null || echo '[]')"
  if jq --argjson ids "$ids_json" \
        '.jobs |= map(.id as $i | .enabled = (($ids | index($i)) != null))' \
        "$CRON_JOBS" > "$CRON_JOBS.tmp" 2>/dev/null && [[ -s "$CRON_JOBS.tmp" ]]; then
    mv "$CRON_JOBS.tmp" "$CRON_JOBS"
    log "  Cron jobs restored ($(cron_enabled_count "$CRON_JOBS") enabled, re-applied from manifest)."
    rm -f "$CRON_ENABLED_MANIFEST" "$CRON_SAFETY_BAK"
  else
    rm -f "$CRON_JOBS.tmp"
    warn "Cron restore FAILED; kept manifest ($CRON_ENABLED_MANIFEST) and snapshot ($CRON_SAFETY_BAK) for manual recovery."
  fi
}
if [[ "$skip_cron" == "false" && -f "$CRON_JOBS" ]]; then
  log "Step 2/6: Capturing cron enabled-state and disabling jobs..."
  cur_enabled="$(cron_enabled_count "$CRON_JOBS")"
  refresh_manifest=true
  # A leftover manifest means a previous run died before restoring. Decide which
  # enabled-set is authoritative by CONTENT, not mere existence: if the current
  # file still has >= as many enabled jobs, it is the live truth — refresh from
  # it. Only keep the prior manifest when current has fewer enabled (i.e. the
  # interrupted run already disabled the live file). Incident 2026-06-11/12 (and
  # 2026-06-20): trusting a stale snapshot left the desk disabled for days.
  if [[ -f "$CRON_ENABLED_MANIFEST" ]]; then
    prior_enabled="$(grep -c . "$CRON_ENABLED_MANIFEST" 2>/dev/null || echo 0)"
    if (( cur_enabled < prior_enabled )); then
      refresh_manifest=false
      warn "Prior-run manifest found ($prior_enabled ids); current jobs.json has fewer enabled ($cur_enabled) — keeping prior manifest (current was likely left disabled by the interrupted run)."
    else
      warn "Prior-run manifest found ($prior_enabled ids); current jobs.json enabled=${cur_enabled} >= prior — refreshing manifest from current live state."
    fi
  fi
  if [[ "$refresh_manifest" == "true" ]]; then
    jq -r '.jobs[]? | select(.enabled == true) | .id' "$CRON_JOBS" > "$CRON_ENABLED_MANIFEST"
    cp "$CRON_JOBS" "$CRON_SAFETY_BAK"
  fi
  # Restore even if a later step fails — keeps a crashed restart from leaving
  # every job disabled.
  trap restore_cron_jobs EXIT
  if (( cur_enabled > 0 )); then
    jq '.jobs |= map(.enabled = false)' "$CRON_JOBS" > "$CRON_JOBS.tmp" && mv "$CRON_JOBS.tmp" "$CRON_JOBS"
    log "  Disabled ${cur_enabled} cron job(s); will restore $(grep -c . "$CRON_ENABLED_MANIFEST" 2>/dev/null || echo 0) on completion."
  else
    log "  No active cron jobs to disable; will restore $(grep -c . "$CRON_ENABLED_MANIFEST" 2>/dev/null || echo 0) on completion."
  fi
else
  log "Step 2/6: Skipping cron management."
fi

# --- Step 3+4: Restart runtime ---
log "Step 3/6: Restarting gateway runtime..."
restart_gateway
if wait_for_gateway_healthy; then
  log "  Gateway runtime healthy after restart."
else
  warn "Gateway did not reach healthy state within ${MAX_WAIT}s."
fi

# --- Step 5: Validate tokens ---
log "Step 5/6: Validating token health..."
if check_models_auth; then
  log "  Model auth check passed; tokens are healthy."
  token_ok=true
else
  warn "Model auth check FAILED after restart."
  warn "models status output: $(printf '%s' "${MODELS_CHECK_OUT}" | head -n 1)"
  token_ok=false

  # Try restoring from backup
  log "  Attempting token restore from pre-restart backup..."
  bash "$SCRIPTS_DIR/token-restore.sh"

  # Give the gateway a moment to pick up the restored file
  sleep 2

  if check_models_auth; then
    log "  Token restore successful; auth is working."
    token_ok=true
  else
    warn "Token restore did not fix auth."
    warn "Manual fix needed: openclaw models auth login --provider openai-codex"
    token_ok=false
  fi
fi

# --- Step 6: Restore cron jobs ---
if [[ "$skip_cron" == "false" ]]; then
  log "Step 6/6: Restoring cron jobs..."
  restore_cron_jobs
else
  log "Step 6/6: Skipping cron restore."
fi

# --- Summary ---
echo ""
if [[ "$token_ok" == "true" ]]; then
  log "Gateway restart complete. All tokens healthy."
else
  log "Gateway restart complete, but OpenAI tokens need manual re-auth:"
  log "   openclaw models auth login --provider openai-codex"
fi
