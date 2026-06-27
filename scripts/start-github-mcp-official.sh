#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

CRED_FILE="/home/aaron/.openclaw/credentials/github_credentials.json"

usage() {
  cat <<'EOF'
Usage: start-github-mcp-official.sh --agent <jerry|main|resi|dwight|druck>

Starts official GitHub MCP server (stdio) with deterministic bot token mapping.
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
  jerry|main|dwight|druck|resi)
    profile="rsl-bot"
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

runtime="${GITHUB_MCP_RUNTIME:-direct}"
case "$runtime" in
  direct)
    if ! command -v npx >/dev/null 2>&1; then
      echo "[start-github-mcp-official] npx is required for direct runtime" >&2
      exit 1
    fi
    # Direct stdio runtime (no docker.sock needed).
    exec env GITHUB_PERSONAL_ACCESS_TOKEN="$token" npx -y @modelcontextprotocol/server-github
    ;;
  docker)
    exec docker run --rm -i -e GITHUB_PERSONAL_ACCESS_TOKEN="$token" ghcr.io/github/github-mcp-server:latest stdio --toolsets=default
    ;;
  *)
    echo "[start-github-mcp-official] unsupported GITHUB_MCP_RUNTIME '$runtime' (use direct|docker)" >&2
    exit 2
    ;;
esac
