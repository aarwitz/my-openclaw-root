#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

CRED_FILE="/home/aaron/.openclaw/credentials/github_credentials.json"
ACTION="${1:-get}"

# Git calls helper with get/store/erase; we only need deterministic get routing.
if [[ "$ACTION" != "get" ]]; then
  exit 0
fi

protocol=""
host=""
path=""

while IFS='=' read -r key value; do
  [[ -z "${key}" ]] && break
  case "$key" in
    protocol) protocol="$value" ;;
    host) host="$value" ;;
    path) path="$value" ;;
  esac
done

if [[ "$protocol" != "https" || "$host" != "github.com" ]]; then
  exit 0
fi

profile="rsl-bot"
if [[ "$path" == EWAG-dev/* ]]; then
  profile="ewag"
fi

username="$(jq -r --arg p "$profile" '.profiles[$p].username // empty' "$CRED_FILE")"
token="$(jq -r --arg p "$profile" '.profiles[$p].token // empty' "$CRED_FILE")"

if [[ -z "$username" || -z "$token" || "$username" == "null" || "$token" == "null" ]]; then
  exit 0
fi

printf 'username=%s\n' "$username"
printf 'password=%s\n' "$token"
