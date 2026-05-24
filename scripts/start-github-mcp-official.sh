#!/usr/bin/env bash
set -euo pipefail

CRED_FILE="/home/aaron/.openclaw/credentials/github_credentials.json"

usage() {
  cat <<'EOF'
Usage: start-github-mcp-official.sh --agent <main|resi|dwight|druck>

Starts official GitHub MCP server (stdio) with deterministic per-agent token mapping.
EOF
}

agent=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      agent="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$agent" ]]; then
  usage
  exit 2
fi

if [[ ! -f "$CRED_FILE" ]]; then
  echo "[start-github-mcp-official] missing credentials file: $CRED_FILE" >&2
  exit 1
fi

case "$agent" in
  main|dwight|druck)
    profile="rsl-bot"
    ;;
  resi)
    profile="ewag"
    ;;
  *)
    echo "[start-github-mcp-official] unknown agent '$agent'" >&2
    exit 2
    ;;
esac

token="$(jq -r --arg p "$profile" '.profiles[$p].token // empty' "$CRED_FILE")"
if [[ -z "$token" ]]; then
  echo "[start-github-mcp-official] missing token for profile '$profile'" >&2
  exit 1
fi

exec docker run --rm -i -e GITHUB_PERSONAL_ACCESS_TOKEN="$token" ghcr.io/github/github-mcp-server:latest stdio --toolsets=default
