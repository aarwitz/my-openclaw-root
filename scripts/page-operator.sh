#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# page-operator.sh — deterministic operator paging via the Telegram Bot API.
#
# Sends directly to api.telegram.org with curl, so it works when the OpenClaw
# gateway is DOWN (the whole point: gateway-down incidents must page the human).
# Used by openclaw-availability-watchdog.sh; safe to call from any host cron.
#
# Usage:
#   page-operator.sh <alert-key> <message...>
#
# <alert-key> is a stable slug for the alert class (e.g. "gateway-crashloop").
# Each key is rate-limited to one page per PAGE_COOLDOWN_SECONDS (default 1800)
# via a state file, so a 5-minute cron loop doesn't spam. Pass PAGE_FORCE=1 to
# bypass the cooldown (e.g. for recovery notices).

TOKEN_FILE="${PAGE_TOKEN_FILE:-/home/aaron/.openclaw/credentials/telegram-bot-token}"
CHAT_ID="${PAGE_CHAT_ID:-6043080629}"
STATE_DIR="${PAGE_STATE_DIR:-/home/aaron/.openclaw/state/pager}"
COOLDOWN="${PAGE_COOLDOWN_SECONDS:-1800}"
LOG_FILE="/home/aaron/.openclaw/logs/pager.log"

log() { printf '%s [pager] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >> "$LOG_FILE"; }

if [[ $# -lt 2 ]]; then
  echo "usage: page-operator.sh <alert-key> <message...>" >&2
  exit 2
fi

KEY="$1"; shift
MSG="$*"

if [[ ! "$KEY" =~ ^[a-z0-9-]+$ ]]; then
  echo "alert-key must be a kebab-case slug, got: $KEY" >&2
  exit 2
fi

if [[ ! -f "$TOKEN_FILE" ]]; then
  log "FATAL: bot token file missing at $TOKEN_FILE (alert=$KEY dropped: $MSG)"
  exit 1
fi

mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/$KEY.last"
now="$(date +%s)"
last=0
[[ -f "$STATE_FILE" ]] && last="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"

if [[ "${PAGE_FORCE:-0}" != "1" ]] && (( now - last < COOLDOWN )); then
  log "suppressed (cooldown $((now - last))s < ${COOLDOWN}s) alert=$KEY: $MSG"
  exit 0
fi

TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
TEXT="🚨 [$KEY] $MSG
host: $(hostname) · $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

RESP_FILE="$(mktemp)"
http_code="$(curl -sS -m 15 -o "$RESP_FILE" -w '%{http_code}' \
  --data-urlencode "chat_id=$CHAT_ID" \
  --data-urlencode "text=$TEXT" \
  "https://api.telegram.org/bot${TOKEN}/sendMessage" || echo "000")"

if [[ "$http_code" == "200" ]]; then
  printf '%s' "$now" > "$STATE_FILE"
  log "sent alert=$KEY: $MSG"
  rm -f "$RESP_FILE"
  exit 0
fi

log "SEND FAILED (http=$http_code) alert=$KEY: $MSG :: $(head -c 300 "$RESP_FILE" 2>/dev/null || true)"
rm -f "$RESP_FILE"
exit 1
