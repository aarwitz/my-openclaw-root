#!/usr/bin/env bash
set -euo pipefail

ROOT="${HOME}/.openclaw"
COMPOSE_FILE="${ROOT}/docker-compose.openclaw.yml"
SCRIPTS_DIR="${ROOT}/scripts"
OPENCLAW_BIN="${OPENCLAW_BIN:-$(command -v openclaw || true)}"

if [[ -z "${OPENCLAW_BIN}" ]]; then
  echo "ERROR: openclaw CLI not found" >&2
  exit 1
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "ERROR: missing compose file ${COMPOSE_FILE}" >&2
  exit 1
fi

echo "[cutover] backing up tokens"
bash "${SCRIPTS_DIR}/token-backup.sh" --label pre-docker-cutover

echo "[cutover] building docker image"
docker compose -f "${COMPOSE_FILE}" build openclaw-gateway

echo "[cutover] stopping gateway gracefully"
"${OPENCLAW_BIN}" gateway stop || true
sleep 2

echo "[cutover] stopping systemd unit to avoid port conflict"
systemctl --user stop openclaw-gateway.service || true
sleep 2

echo "[cutover] starting dockerized gateway"
docker compose -f "${COMPOSE_FILE}" up -d openclaw-gateway

echo "[cutover] waiting for health"
for _ in $(seq 1 25); do
  if docker compose -f "${COMPOSE_FILE}" ps --status running | grep -q openclaw-gateway; then
    if "${OPENCLAW_BIN}" gateway status >/dev/null 2>&1; then
      echo "[cutover] gateway status check passed"
      exit 0
    fi
  fi
  sleep 3
done

echo "[cutover] gateway failed health check; collecting logs"
docker compose -f "${COMPOSE_FILE}" logs --tail=120 openclaw-gateway || true
echo "[cutover] rollback: stopping docker gateway and restoring host service"
docker compose -f "${COMPOSE_FILE}" down || true
"${OPENCLAW_BIN}" gateway start || true
exit 1
