#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

CRED_FILE="/home/aaron/.openclaw/credentials/github_credentials.json"

if [[ ! -f "$CRED_FILE" ]]; then
  echo "[export-github-mcp-env] missing credentials file: $CRED_FILE" >&2
  exit 1
fi

get_token() {
  local profile="$1"
  jq -r --arg p "$profile" '.profiles[$p].token // empty' "$CRED_FILE"
}

RSL_BOT_TOKEN="$(get_token rsl-bot)"
EWAG_TOKEN="$(get_token ewag)"

if [[ -z "$RSL_BOT_TOKEN" || -z "$EWAG_TOKEN" ]]; then
  echo "[export-github-mcp-env] missing required tokens in profiles {rsl-bot, ewag}" >&2
  exit 1
fi

cat <<EOF
export GITHUB_PAT_MAIN='$RSL_BOT_TOKEN'
export GITHUB_PAT_DWIGHT='$RSL_BOT_TOKEN'
export GITHUB_PAT_DRUCK='$RSL_BOT_TOKEN'
export GITHUB_PAT_RESI='$EWAG_TOKEN'
EOF
