#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

CRED_FILE="/home/aaron/.openclaw/credentials/github_credentials.json"

usage() {
  cat <<'EOF'
Usage: gh-account-router.sh [--agent <main|resi|dwight|druck>] <gh args...>

Routes gh CLI auth deterministically by agent:
  - all supported agents -> rsl-bot profile

You can also set OPENCLAW_AGENT_ID, OPENCLAW_AGENT, or AGENT_ID.
EOF
}

agent="${OPENCLAW_AGENT_ID:-${OPENCLAW_AGENT:-${AGENT_ID:-main}}}"
if [[ "${1:-}" == "--agent" ]]; then
  [[ $# -ge 3 ]] || {
    usage
    exit 2
  }
  agent="$2"
  shift 2
fi

[[ $# -gt 0 ]] || {
  usage
  exit 2
}

profile="rsl-bot"

username="$(jq -r --arg p "$profile" '.profiles[$p].username // empty' "$CRED_FILE")"
token="$(jq -r --arg p "$profile" '.profiles[$p].token // empty' "$CRED_FILE")"

if [[ -z "$username" || -z "$token" || "$username" == "null" || "$token" == "null" ]]; then
  echo "[gh-account-router] missing username/token for profile '$profile'" >&2
  exit 1
fi

GH_TOKEN="$token" GITHUB_TOKEN="$token" gh "$@"
