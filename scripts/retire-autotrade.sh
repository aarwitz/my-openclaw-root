#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Idempotently remove AutoTrade-owned host jobs while preserving generic host
# maintenance (SDK updater, script-policy lint, Jerry host ops, gateway
# availability, and log rotation). Gateway jobs are disabled separately through
# the gateway API so the live scheduler and jobs.json agree.

ROOT="/home/aaron/.openclaw"
ARCHIVE="${ROOT}/archives/autotrade-retired-20260805"
MARKER="${ROOT}/state/AUTOTRADE_RETIRED"
CURRENT="$(mktemp)"
FILTERED="$(mktemp)"
trap 'rm -f "${CURRENT}" "${FILTERED}"' EXIT

mkdir -p "${ARCHIVE}" "$(dirname "${MARKER}")"
crontab -l >"${CURRENT}" 2>/dev/null || :

awk '
  /^# BEGIN AUTOTRADE / {in_autotrade_block=1; next}
  /^# END AUTOTRADE / {in_autotrade_block=0; next}
  in_autotrade_block {next}
  /learning-kg-rebuild\.sh/ {next}
  /learning-rediscovery\.sh/ {next}
  /learning-growth-report\.sh/ {next}
  /learning-chain\.sh/ {next}
  /push-trader-data\.sh/ {next}
  /sweep-and-page\.sh/ {next}
  /social_collect\.py/ {next}
  /guard-pass\.sh/ {next}
  /pr-gate-sweeper\.py/ {next}
  /^# Deterministic zero-Codex learning/ {next}
  /^# Data-only trader-intel publish/ {next}
  /^# D57: deterministic health escalation/ {next}
  /^# D60: ApeWisdom social-mention collector/ {next}
  /^# Intraday deterministic protection passes/ {next}
  {print}
' "${CURRENT}" >"${FILTERED}"

# Collapse runs of blank lines but otherwise preserve unrelated entries.
awk 'NF {blank=0; print; next} !blank {blank=1; print}' "${FILTERED}" >"${FILTERED}.clean"
mv "${FILTERED}.clean" "${FILTERED}"
crontab "${FILTERED}"
cp "${FILTERED}" "${ARCHIVE}/host-crontab.after.txt"

if rg -n 'learning-(kg-rebuild|rediscovery|growth-report|chain)|push-trader-data|sweep-and-page|social_collect|guard-pass|pr-gate-sweeper|dwight-pq-rail|market-event-intake|learning-signals' "${FILTERED}"; then
  echo "AutoTrade cron residue remains" >&2
  exit 1
fi

printf '%s\n' \
  'AutoTrade v1 retired intentionally.' \
  'retired_at=2026-08-05' \
  'authority=none' \
  'reactivation=explicit-operator-decision-required' >"${MARKER}"

echo "AutoTrade host jobs retired; generic host maintenance preserved"
